from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import tldextract

_PUBLIC_SUFFIX_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())
_OFFICIAL_HOST_LABELS = frozenset(
    {
        "api",
        "developer",
        "developers",
        "docs",
        "documentation",
        "legal",
        "pricing",
        "security",
        "support",
        "terms",
        "trust",
    }
)
_OFFICIAL_PATH_SEGMENTS = _OFFICIAL_HOST_LABELS
_PUBLIC_AUTHORITY_SUFFIX_LABELS = frozenset({"ac", "edu", "gov"})
_EDITORIAL_SIGNALS = re.compile(
    r"\b(?:analysis|comparison|independent|news|opinion|review|roundup|versus|vs)\b"
)
_GENERIC_OFFICIAL_TITLE = re.compile(
    r"^(?:api(?: reference)?|developer documentation|documentation|docs|getting started|"
    r"legal|plans(?: and billing)?|pricing|privacy policy|security|support|terms(?: of service)?|"
    r"trust center)$"
)


@dataclass(frozen=True)
class SourceAuthorityDecision:
    domain: str
    publisher: str
    authoritative: bool
    hierarchy_rank: int


def _normalized_hostname(value: str) -> str:
    parsed = urlsplit(value if "://" in value else f"//{value}")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("source identity requires a hostname")
    return hostname.encode("idna").decode("ascii").casefold().rstrip(".")


@lru_cache(maxsize=2_048)
def registrable_domain(value: str) -> str:
    """Return a network-free public-suffix-aware publisher identity."""

    hostname = _normalized_hostname(value)
    extracted = _PUBLIC_SUFFIX_EXTRACTOR(hostname)
    if extracted.suffix and extracted.domain:
        return f"{extracted.domain}.{extracted.suffix}"
    labels = [label for label in hostname.split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else hostname


def publisher_independence_key(url: str) -> str:
    return f"publisher:{registrable_domain(url)}"


def _brand_identity(hostname: str, publisher: str) -> str:
    publisher_labels = publisher.split(".")
    brand = publisher_labels[0] if publisher_labels else hostname.split(".")[0]
    return re.sub(r"[^a-z0-9]", "", brand.casefold())


def classify_source_authority(
    url: str,
    *,
    hierarchy_rank: int,
    title: str | None = None,
    allow_first_party: bool = True,
) -> SourceAuthorityDecision:
    hostname = _normalized_hostname(url)
    publisher = registrable_domain(hostname)
    if not allow_first_party:
        return SourceAuthorityDecision(
            domain=publisher,
            publisher=publisher,
            authoritative=False,
            hierarchy_rank=hierarchy_rank,
        )

    extracted = _PUBLIC_SUFFIX_EXTRACTOR(hostname)
    suffix_labels = set(extracted.suffix.casefold().split(".")) if extracted.suffix else set()
    public_authority = bool(suffix_labels & _PUBLIC_AUTHORITY_SUFFIX_LABELS)

    publisher_labels = publisher.split(".")
    hostname_labels = hostname.split(".")
    subdomain_labels = hostname_labels[: -len(publisher_labels)] if publisher_labels else []
    official_host = bool(set(subdomain_labels) & _OFFICIAL_HOST_LABELS)

    parsed = urlsplit(url)
    path_segments = {segment.casefold() for segment in parsed.path.split("/") if segment}
    official_path = bool(path_segments & _OFFICIAL_PATH_SEGMENTS)
    normalized_title_words = re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()
    normalized_title = normalized_title_words.replace(" ", "")
    brand = _brand_identity(hostname, publisher)
    title_proves_publisher = bool(brand and brand in normalized_title)
    generic_official_title = bool(_GENERIC_OFFICIAL_TITLE.fullmatch(normalized_title_words))
    normalized_path = re.sub(r"[^a-z0-9]+", " ", parsed.path.casefold()).strip()
    editorial_surface = bool(
        _EDITORIAL_SIGNALS.search(normalized_title_words)
        or _EDITORIAL_SIGNALS.search(normalized_path)
    )
    first_party_surface = (
        (official_host or official_path)
        and (title_proves_publisher or generic_official_title)
        and not editorial_surface
    )

    authoritative = public_authority or first_party_surface
    return SourceAuthorityDecision(
        domain=publisher,
        publisher=publisher,
        authoritative=authoritative,
        hierarchy_rank=1 if authoritative else hierarchy_rank,
    )


__all__ = [
    "SourceAuthorityDecision",
    "classify_source_authority",
    "publisher_independence_key",
    "registrable_domain",
]
