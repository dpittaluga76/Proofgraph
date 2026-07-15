from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from proofgraph.generation.source_identity import publisher_independence_key

CONNECT_TIMEOUT_SECONDS: Final = 3.0
TOTAL_TIMEOUT_SECONDS: Final = 15.0
MAX_REDIRECTS: Final = 5
MAX_DECOMPRESSED_BYTES: Final = 2 * 1024 * 1024
MAX_USER_TEXT_BYTES: Final = 100 * 1024
MAX_EXCERPT_CHARS: Final = 500
ALLOWED_CONTENT_TYPES: Final = frozenset({"text/html", "text/plain"})


class SourceRetrievalError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


@dataclass(frozen=True)
class TransientSourceDocument:
    kind: str
    normalized_url: str | None
    title: str
    retrieved_at_iso: str
    content_hash: str
    independence_key: str
    sanitized_excerpt: str
    untrusted_content: str
    content_type: str
    redirect_count: int = 0
    cache_hit: bool = False


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
        if normalized == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript", "template"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        if normalized == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)


def _plain_text(value: str) -> str:
    normalized = "".join(
        char if char.isprintable() else " " if char.isspace() else "" for char in value
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _safe_excerpt(value: str) -> str:
    return _plain_text(value)[:MAX_EXCERPT_CHARS]


def _hash_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def normalize_https_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRetrievalError("invalid_source_url", "url must be a non-empty HTTPS URL.")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as error:
        raise SourceRetrievalError("invalid_source_url", "The source URL is malformed.") from error
    if parsed.scheme.casefold() != "https":
        raise SourceRetrievalError("invalid_source_url", "Only HTTPS source URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise SourceRetrievalError(
            "invalid_source_url",
            "Source URLs may not contain credentials.",
        )
    if parsed.hostname is None:
        raise SourceRetrievalError("invalid_source_url", "The source URL requires a hostname.")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise SourceRetrievalError(
            "invalid_source_url", "The source hostname is invalid."
        ) from error
    if port is not None and not 1 <= port <= 65_535:
        raise SourceRetrievalError("invalid_source_url", "The source URL port is invalid.")
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None and port != 443:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit(SplitResult("https", netloc, path, parsed.query, ""))


def _validated_addresses(
    hostname: str,
    port: int,
    resolver: Callable[..., list[tuple[object, ...]]],
) -> tuple[str, ...]:
    try:
        answers = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise SourceRetrievalError(
            "source_dns_failed",
            "The source hostname could not be resolved.",
            retryable=True,
        ) from error
    addresses = sorted({str(answer[4][0]) for answer in answers})
    if not addresses:
        raise SourceRetrievalError(
            "source_dns_failed",
            "The source hostname returned no addresses.",
            retryable=True,
        )
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise SourceRetrievalError(
                "unsafe_source_destination",
                "The source URL resolves to a non-public network address.",
                details={"hostname": hostname},
            )
    return tuple(addresses)


class _ResolvedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        hostname: str,
        port: int,
        address: str,
        *,
        timeout: float,
    ) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._resolved_address = address

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._resolved_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _read_bounded(
    response: http.client.HTTPResponse,
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> bytes:
    encoding = (response.getheader("Content-Encoding") or "identity").casefold().strip()
    decompressor: zlib.Decompress | None
    if encoding in {"", "identity"}:
        decompressor = None
    elif encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj()
    else:
        raise SourceRetrievalError(
            "unsupported_content_encoding",
            "The source response uses an unsupported content encoding.",
        )

    output = bytearray()
    while True:
        if monotonic() >= deadline:
            raise SourceRetrievalError(
                "source_timeout",
                "Source retrieval exceeded the total timeout.",
                retryable=True,
            )
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        if decompressor is None:
            decoded = chunk
        else:
            decoded = decompressor.decompress(chunk, MAX_DECOMPRESSED_BYTES - len(output) + 1)
            if decompressor.unconsumed_tail:
                raise SourceRetrievalError(
                    "source_too_large",
                    "The decompressed source exceeds 2 MiB.",
                )
        output.extend(decoded)
        if len(output) > MAX_DECOMPRESSED_BYTES:
            raise SourceRetrievalError(
                "source_too_large",
                "The decompressed source exceeds 2 MiB.",
            )
    if decompressor is not None:
        output.extend(decompressor.flush(MAX_DECOMPRESSED_BYTES - len(output) + 1))
    if len(output) > MAX_DECOMPRESSED_BYTES:
        raise SourceRetrievalError(
            "source_too_large",
            "The decompressed source exceeds 2 MiB.",
        )
    return bytes(output)


class SecureSourceRetriever:
    def __init__(
        self,
        *,
        resolver: Callable[..., list[tuple[object, ...]]] = socket.getaddrinfo,
        connection_factory: Callable[..., http.client.HTTPSConnection] = _ResolvedHTTPSConnection,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.resolver = resolver
        self.connection_factory = connection_factory
        self.monotonic = monotonic

    def retrieve_url(self, value: str, *, retrieved_at_iso: str) -> TransientSourceDocument:
        current_url = normalize_https_url(value)
        deadline = self.monotonic() + TOTAL_TIMEOUT_SECONDS
        redirect_count = 0
        while True:
            parsed = urlsplit(current_url)
            assert parsed.hostname is not None
            port = parsed.port or 443
            addresses = _validated_addresses(parsed.hostname, port, self.resolver)
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise SourceRetrievalError(
                    "source_timeout",
                    "Source retrieval exceeded the total timeout.",
                    retryable=True,
                )
            connection = self.connection_factory(
                parsed.hostname,
                port,
                addresses[0],
                timeout=min(CONNECT_TIMEOUT_SECONDS, remaining),
            )
            try:
                target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Accept": "text/html, text/plain;q=0.9",
                        "Accept-Encoding": "gzip, deflate",
                        "User-Agent": "ProofgraphSourceIngestion/1.0",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if location is None:
                        raise SourceRetrievalError(
                            "invalid_source_redirect",
                            "The source redirect omitted its destination.",
                        )
                    if redirect_count >= MAX_REDIRECTS:
                        raise SourceRetrievalError(
                            "source_redirect_limit",
                            "The source exceeded five redirects.",
                        )
                    redirect_count += 1
                    current_url = normalize_https_url(urljoin(current_url, location))
                    continue
                if not 200 <= response.status < 300:
                    raise SourceRetrievalError(
                        "source_http_error",
                        "The source server returned an unsuccessful status.",
                        retryable=response.status >= 500 or response.status == 429,
                        details={"status": response.status},
                    )
                content_type = (
                    (response.getheader("Content-Type") or "").split(";", 1)[0].strip().casefold()
                )
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise SourceRetrievalError(
                        "unsupported_source_content_type",
                        "Only HTML and plain-text sources are accepted.",
                        details={"content_type": content_type or None},
                    )
                payload = _read_bounded(
                    response,
                    deadline=deadline,
                    monotonic=self.monotonic,
                )
            except (OSError, http.client.HTTPException) as error:
                raise SourceRetrievalError(
                    "source_unavailable",
                    "The source could not be retrieved.",
                    retryable=True,
                ) from error
            finally:
                connection.close()

            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SourceRetrievalError(
                    "invalid_source_encoding",
                    "The source must decode as UTF-8.",
                ) from error
            title = parsed.hostname
            if content_type == "text/html":
                parser = _HTMLTextParser()
                parser.feed(text)
                text = _plain_text(" ".join(parser.text_parts))
                parsed_title = _safe_excerpt(" ".join(parser.title_parts))
                if parsed_title:
                    title = parsed_title
            else:
                text = _plain_text(text)
            return TransientSourceDocument(
                kind="user_url",
                normalized_url=current_url,
                title=_safe_excerpt(title),
                retrieved_at_iso=retrieved_at_iso,
                content_hash=_hash_bytes(payload),
                independence_key=publisher_independence_key(current_url),
                sanitized_excerpt=_safe_excerpt(text),
                untrusted_content=text,
                content_type=content_type,
                redirect_count=redirect_count,
            )

    def receive_text(
        self,
        value: str,
        *,
        title: str | None,
        retrieved_at_iso: str,
    ) -> TransientSourceDocument:
        try:
            payload = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise SourceRetrievalError(
                "invalid_source_text",
                "User source text must be valid UTF-8.",
            ) from error
        if len(payload) > MAX_USER_TEXT_BYTES:
            raise SourceRetrievalError(
                "source_text_too_large",
                "User source text exceeds 100 KiB UTF-8.",
            )
        normalized_text = _plain_text(value)
        if not normalized_text:
            raise SourceRetrievalError(
                "invalid_source_text",
                "User source text must not be empty.",
            )
        safe_title = _safe_excerpt(title or "User-supplied text")
        if not safe_title:
            safe_title = "User-supplied text"
        return TransientSourceDocument(
            kind="user_text",
            normalized_url=None,
            title=safe_title,
            retrieved_at_iso=retrieved_at_iso,
            content_hash=_hash_bytes(payload),
            independence_key=f"user_text:{hashlib.sha256(payload).hexdigest()[:24]}",
            sanitized_excerpt=_safe_excerpt(normalized_text),
            untrusted_content=normalized_text,
            content_type="text/plain",
        )


__all__ = [
    "SecureSourceRetriever",
    "SourceRetrievalError",
    "TransientSourceDocument",
    "normalize_https_url",
]
