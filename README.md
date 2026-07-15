# Proofgraph

An evidence-native canvas for discovering defensible software opportunities.

The **Phase 3 intelligence pipeline**, through **PG-020**, is implemented and verified; the next implementation phase begins with **PG-021**. The implementation follows [`design.md`](design.md): a Django ASGI web process, a separate Django management-command worker, PostgreSQL as the only stateful service, and an isolated React/Vite browser client.

The PostgreSQL schema persists canvases, typed nodes and edges, append-only graph operations, and operation-linked staleness causes. Localized canvas operations provide optimistic semantic, position, and edge versions; idempotent retries; audited constraint anchoring; explicit dependency conflicts; and incremental revision replay. Database constraints and triggers enforce the frozen graph taxonomy, same-canvas references, branch-scoped constraint anchors, actor-scoped idempotency keys, and exact stale/cause consistency.

The browser workspace can create or reopen canvases, add and edit fixed graph types, connect nodes, configure global or branch-scoped constraints, persist drag movement, resolve deletion dependencies through visible audited operations, and save a deterministic auto-layout. Django's health endpoint issues the CSRF cookie used by browser mutations.

The generation domain persists idempotent, version-checked runs; immutable stage checkpoints; fenced worker leases; candidate patches; and canvas-scoped progress events. Phase 3 adds deterministic operation-specific context packing, strict structured planning/extraction/synthesis/critique/patch schemas, bounded research adapters, evidence clustering, and immutable replay fixtures. Product requests may select `live_v1`, `demo_hybrid_v1`, or `replay_v1`; the Phase 2 `phase2_test_v1` adapter remains injectable only in tests and is unreachable through the product resolver.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python environments and locking
- Python 3.11 or newer (uv can install it)
- Node.js 20.19 or newer
- Docker with Compose for the local PostgreSQL service

The checked-in dependency ranges use Django 5.2 LTS and React 19. PostgreSQL is the only service in `compose.yaml`; Redis, Celery, Neo4j, and WebSockets are intentionally absent.

## Local setup on PowerShell

```powershell
Copy-Item .env.example .env
uv sync --all-groups
docker compose up -d db
uv run python manage.py migrate
uv run python manage.py check_database

Set-Location web
npm ci
npx playwright install chromium
Set-Location ..
```

`check_database` must report the configured PostgreSQL database and user. The default Compose port is `55432` to avoid colliding with a system PostgreSQL installation; override it with `POSTGRES_PORT` and update `DATABASE_URL` together.

`replay_v1` works without external credentials. Set `OPENAI_API_KEY` to enable `live_v1` and `demo_hybrid_v1`. A GitHub token and Stack Exchange key are optional but increase their public API allowances; never expose any provider credential to the browser client.

## Run the three local components

Open separate terminals from the repository root.

Web API:

```powershell
uv run uvicorn proofgraph.asgi:application --reload --host 127.0.0.1 --port 8000
```

Generation worker:

```powershell
uv run python manage.py run_generation_worker
```

Browser client:

```powershell
Set-Location web
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to Django. The page reports readiness only when Django can execute a PostgreSQL query. You can inspect the same result directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

For a non-blocking worker startup check:

```powershell
uv run python manage.py run_generation_worker --once
```

`--once` processes at most one eligible run. Normal workers claim PostgreSQL rows with `FOR UPDATE SKIP LOCKED`, keep a 60-second fenced lease alive every 12 seconds on a dedicated database connection, physically delete expired research and source-content cache rows at startup and every 60 seconds, and recycle after 50 jobs or four hours.

## Phase 2 generation APIs and SSE verification

Generation remains operation-specific. Create a run with `POST /api/canvases/{canvas_id}/generation-runs`, inspect it with `GET /api/generation-runs/{run_id}`, and use the `/cancel` or `/retry` subresource for explicit lifecycle controls. Run creation accepts only `operation`, `selected_node_ids`, `expected_node_versions`, optional `instruction`, `execution_profile_id`, `idempotency_key`, and `regeneration_scope` only for `regenerate_stale`.

The run endpoint locks the canvas before selected nodes and freezes one semantic context snapshot. An exact idempotency-key replay returns the existing run with `202`; conflicting reuse returns `409`. The browser progress UI is intentionally deferred, but the persisted SSE API is complete.

Start the ASGI server exactly as follows:

```powershell
uv run uvicorn proofgraph.asgi:application --host 127.0.0.1 --port 8000 --http h11
```

Then verify incremental delivery for a real canvas, preserving the connection with `curl -N`:

```powershell
curl.exe -N -H "Accept: text/event-stream" "http://127.0.0.1:8000/api/canvases/<canvas-id>/events?after=0"
```

Each committed event includes an SSE `id`, event name, run ID, canvas and run sequences, sanitized payload, and timestamp. Reconnect with the last received sequence in `after` or `Last-Event-ID`. The server replays in batches of 100, polls PostgreSQL once per second when caught up, and sends `: keepalive` every 15 seconds. It does not hold a transaction or database connection during the wait.

If an HTTP proxy is added, disable response buffering and caching for this route (for nginx: `proxy_buffering off; proxy_cache off; gzip off;`) and keep the idle timeout above 15 seconds. The response already sends `text/event-stream`, `Cache-Control: no-cache, no-transform`, and `X-Accel-Buffering: no` without `Content-Length`.

## Phase 3 intelligence profiles

All profiles use the same run, checkpoint, event, and candidate-patch orchestrator:

| Profile | Provider composition | Availability |
|---|---|---|
| `live_v1` | Live GPT-5.6 structured stages plus live bounded research | Requires `OPENAI_API_KEY` |
| `demo_hybrid_v1` | Fixture planning/research/extraction plus live synthesis/critique/patch construction | Requires `OPENAI_API_KEY` |
| `replay_v1` | Immutable fixtures for every provider-backed stage | Always available |

The canonical replay bundle is `fixtures/security-questionnaires/v1`. Matching is exact across operation, stage, regeneration phase, target kinds, pipeline/provider identity, fixture version, and a normalized semantic-input hash. A mismatch fails recoverably with `fixture_input_mismatch`; it never falls through to a live provider. Fixture outputs pass through the same Pydantic schemas and retained-content checks as live outputs.

Live structured stages use the OpenAI Responses structured-output path with GPT-5.6. Research is bounded to the configured query/result limits across OpenAI hosted web search, GitHub public search, Stack Exchange search, and explicit user sources. Research events remain provisional until a candidate evidence patch is accepted, and synthesis accepts only explicitly selected applied, current claim nodes with their source provenance.

## Retained source content

Proofgraph follows the DQ-003 derived-evidence-only policy. It never persists complete retrieved pages, HTML, or user-supplied source documents. Durable records may contain citation metadata, content hashes, derived claims, and sanitized excerpts of at most 500 Unicode characters. Accepted graph evidence and run/stage/event audit records remain until canvas deletion; deleting a canvas permanently cascades through its graph, runs, stages, events, patches, decisions, reservations, and caches. Future query caches have a 24-hour physical-expiry requirement, retrieved-content rows keep `retained_content` null, and test fixture bundles must be synthetic or explicitly redistributable.

## Checks

Backend formatting and static analysis:

```powershell
uv run ruff format --check .
uv run ruff check .
```

Backend tests, including real PostgreSQL and worker startup:

```powershell
uv run pytest
```

Browser formatting, lint, type checking, tests, and production build:

```powershell
Set-Location web
npm run check
```

## Phase 1 verification

Run the complete graph-foundation gate from a clean terminal:

```powershell
docker compose up -d db
uv run python manage.py migrate
uv run pytest
uv run ruff format --check .
uv run ruff check .

Set-Location web
npm run check
npm run test:e2e
Set-Location ..
```

The backend suite uses PostgreSQL and verifies the canonical goal-plus-builder-constraints flow through localized operations, semantic and position version isolation, edge versions, deterministic revision replay, index-backed graph access, explicit dependency resolution, canvas-locked read snapshots, and a full canvas reload. The browser unit and component suite verifies canvas creation/opening, node and edge editing, pinned global and branch constraints, recoverable optimistic conflicts, drag persistence, dependency-guided deletion, CSRF headers, same-envelope network retries, and deterministic auto-layout operations. Playwright then drives the live Vite/Django/PostgreSQL stack through the canonical eight-node layout, low-node drag persistence and reload, and a real stale edit produced by a second API client.

## Phase 2 verification

Run the durable-job gate after Phase 1:

```powershell
docker compose up -d db
uv run python manage.py migrate
uv run python manage.py makemigrations --check --dry-run
uv run pytest
uv run ruff format --check .
uv run ruff check .
git diff --check
```

The backend suite verifies cursor creation, same-canvas composite constraints, lifecycle deletion, graph-write and durable-payload retention validation, exact operation inputs and versions, stable idempotent SSE baselines, unavailable production profiles, the indexed combined claim/reclaim query, lease reclaim and stale-worker fencing, poison jobs, immutable checkpoints, branch-phase checkpoint resume and cancellation, safe retry, fenced patch-ready crash recovery, interleaved SSE replay, reconnect cursors, keepalives, incremental delivery through a live Uvicorn connection, and a full API-to-worker-to-patch flow using only the synthetic test adapter. Existing frontend and Playwright gates remain part of the complete repository gate even though Phase 2 adds no browser progress UI.

## Phase 3 verification

Run the intelligence-pipeline gate after Phase 2:

```powershell
docker compose up -d db
uv run python manage.py migrate
uv run python manage.py makemigrations --check --dry-run
uv run pytest
uv run ruff format --check .
uv run ruff check .

Set-Location web
npm run check
npm run test:e2e
Set-Location ..

git diff --check
```

The backend suite verifies strict schemas and OpenAI-compatible JSON schemas; deterministic graph neighborhoods and budgets; bounded source ingestion, caching, extraction, and clustering; exact evidence-selection gates; three-candidate synthesis and one critique pass; dependency-closed candidate patches with provenance; production profile fencing; timeout, malformed-output, rate-limit, and no-result behavior; strict fixture mismatch handling; and full replay for generation, research, synthesis, every target-localized stale-node plan, and a composite stale branch without live provider access.

## Reversible browser setup

The browser source toolchain is contained in `web/`. Its generated `node_modules`, `dist`, coverage, and Playwright result directories are ignored, and Django does not depend on generated browser assets during development. `npm ci` recreates the exact locked dependency tree. Playwright stores Chromium in its normal per-user browser cache; run `npx playwright uninstall chromium` from `web/` to remove that binary as well.

## Environment

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Required PostgreSQL connection URL. SQLite and other engines are rejected. |
| `DATABASE_CONN_MAX_AGE` | Django persistent-connection lifetime in seconds. |
| `DJANGO_DEBUG` | Enables local debug behavior when true. |
| `DJANGO_SECRET_KEY` | Required outside debug mode. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated Django host allowlist. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated browser origins allowed to submit CSRF-protected mutations through the local Vite proxy. |
| `OPENAI_API_KEY` | Enables the live and hybrid Phase 3 execution profiles. Leave blank for replay-only use. |
| `GITHUB_TOKEN` | Optional GitHub public-search token for higher API allowances. |
| `STACK_EXCHANGE_KEY` | Optional Stack Exchange application key for higher API allowances. |

## Stop local services

```powershell
docker compose down
```

`docker compose down -v` also deletes the local PostgreSQL volume and all of its data.
