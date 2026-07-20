# PG-029 local production acceptance

Date: July 20, 2026

This record covers the local production-container gate only. It does not close PG-029; the named
Cloudflare hostname, public HTTP/2/SSE checks, hybrid check, controlled reboot, and separate-network
verification are still pending.

## Verified

- `compose.public.yaml` resolves successfully with a non-production smoke environment.
- The production image builds from the locked Python and Node dependency files.
- The React/Vite production client is served at `/` by Django ASGI through WhiteNoise and Uvicorn.
- `web` and `worker` run from the same production image in separate containers.
- `db` is PostgreSQL 17 with a persistent named volume, health check, and 40-connection ceiling.
- `db` publishes no host port; `worker` publishes no port; `web` publishes only
  `127.0.0.1:8000`.
- All three containers use `restart: unless-stopped`; `web` and `db` report healthy.
- Database migrations complete before Uvicorn starts, and the worker waits for the healthy web
  container.
- The runtime image contains the built client and excludes `.env`, `.env.public`, and private
  evaluation output.
- Forwarded HTTPS responses include HSTS, CSP, permissions, clickjacking, content-type, referrer,
  and cross-origin-opener headers. API responses are `no-store`; the SSE-specific cache policy
  remains more specific.
- A fresh production-browser session opened the isolated canonical canvas at revision 0 with one
  goal and three pinned constraints.
- Deterministic replay completed without an OpenAI credential, returned six candidate graph
  operations, and left the canvas at revision 0 until explicit patch application.
- Applying the six selected operations produced three strategy nodes and three provenance edges and
  advanced the canvas revision from 0 to 6.
- One-click reset returned the same anonymous session to a new canonical revision-0 canvas with four
  nodes and zero edges.

## Local commands

```powershell
$env:PROOFGRAPH_ENV_FILE = ".env.public.test"
docker compose -f compose.public.yaml config --quiet
docker compose -f compose.public.yaml up --detach --build
docker compose -f compose.public.yaml ps
```

The ignored `.env.public.test` contains only throwaway local smoke values. It is not a production
credential source and is excluded from the Docker build context.

## Still required before PG-029 closes

- Rotate/delete the previously exposed Cloudflare credential.
- Choose the exact hostname on an active Cloudflare domain and create a new named tunnel.
- Create the ignored production `.env.public` with fresh database/Django secrets and the exact
  allowed host/trusted origin. Add the OpenAI key only if existing credits cover the minimal run.
- Install the new tunnel connector as a Windows service and add the `/api/*` cache-bypass rule.
- Verify public HTTPS/HTTP2, incremental SSE, `Last-Event-ID` resume, isolated anonymous sessions,
  retired-resource 404s, durable patch replay, quotas/profile allowlisting, CSRF, and secure cookies.
- Restart the worker during replay and verify fenced recovery without duplicate mutation.
- Reboot Windows and verify Docker, the database volume, all containers, and the connector recover.
- Verify the hostname from a phone on cellular data.
