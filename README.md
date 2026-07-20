# ProofGraph

An evidence-native canvas for discovering defensible software opportunities.

**Phase 5 is in progress: the isolated demo, comparative evaluation, and production observability/security hardening are complete through PG-028.** Each anonymous visitor receives a private canonical seed, signed session, one-click reset, server-enforced profile allowlist, and PostgreSQL-backed cost controls. The internal benchmark freezes 20 synthetic scenarios, four generation variants, resumable concurrent generation, deterministic blinding, two automated model-judge personas, arithmetic-mean scoring, disagreement telemetry, and paired bootstrap reporting. The frozen V1 failure remains unchanged. A fresh post-registration V2 run completed 80 Terra outputs and two 80-rating Sol/Luna judge artifacts, then passed all four required schema-v3 acceptance gates. Structured telemetry now feeds aggregate operational views and a correlated four-scenario diagnostic drill; deployment is the active task. The implementation follows [`design.md`](design.md): a Django ASGI web process, a separate Django management-command worker, PostgreSQL as the only stateful service, and an isolated React/Vite browser client.

The PostgreSQL schema persists canvases, typed nodes and edges, append-only graph operations, and operation-linked staleness causes. Localized canvas operations provide optimistic semantic, position, and edge versions; idempotent retries; audited constraint anchoring; explicit dependency conflicts; and incremental revision replay. Database constraints and triggers enforce the frozen graph taxonomy, same-canvas references, branch-scoped constraint anchors, actor-scoped idempotency keys, and exact stale/cause consistency.

The browser workspace can create or reopen canvases, add and edit fixed graph types, connect nodes, configure global or branch-scoped constraints, persist drag movement, resolve deletion dependencies through visible audited operations, and save a deterministic auto-layout. Phase 4 adds one replayable generation stream per canvas, provisional run-owned evidence overlays, pending patch handoff, visible rejected evidence, stale node/branch regeneration, audited assumption replacement, and canonical retained-branch comparison. In Phase 5 public mode, the welcome screen is replaced by automatic anonymous bootstrap into the security-questionnaire seed, the toolbar exposes reset instead of arbitrary canvas switching, and the UI distinguishes previously retrieved evidence, live GPT-5.6 reasoning, and deterministic replay. Django's health endpoint issues the CSRF cookie used by browser mutations.

The generation domain persists idempotent, version-checked runs; immutable stage checkpoints; fenced worker leases; candidate patches; and canvas-scoped progress events. Phase 3 adds deterministic operation-specific context packing, strict structured planning/extraction/synthesis/critique/patch schemas, bounded research adapters, evidence clustering, and immutable replay fixtures. Phase 4 adds explicit target-local regeneration plans and add-only patches whose fresh successors retain the stale branch through audited old-to-new lineage. Product requests may select `live_v1`, `demo_hybrid_v1`, or `replay_v1`; anonymous sessions are restricted to hybrid and replay, while the Phase 2 `phase2_test_v1` adapter remains injectable only in tests and is unreachable through the product resolver.

## Implementation status

| Phase | Status | Delivered |
|---|---|---|
| Phase 1 — Graph foundation | Complete | PostgreSQL graph schema, localized audited operations, semantic/position versions, canvas UI, deterministic layout, and optimistic conflict recovery |
| Phase 2 — Durable jobs | Complete | PostgreSQL queue, fenced worker leases, immutable checkpoints, retry/cancellation, candidate patches, and replayable canvas SSE |
| Phase 3 — Intelligence pipeline | Complete | Explicit-neighborhood context, structured generation stages, bounded research, evidence clustering, production profiles, and immutable fixtures |
| Phase 4 — Patch review | Complete and locally verified through PG-025 | Dependency-closed review, transactional apply, evidence rejection, durable staleness, explicit always-parallel regeneration, progress UX, and retained-branch comparison |
| Phase 5 — Demo hardening and delivery | In progress; complete through PG-028, PG-029 active | Seeded anonymous demo delivered; frozen V1 failure preserved; official fresh V2 benchmark passed; correlated observability and security drill complete; deployment, compliance, and final acceptance remain |

`TASKS.md` is the implementation queue. **DQ-006 is resolved**: the benchmark remains an internal command-line workflow with no product-UI scope. **DQ-007 is resolved**: the final product and submission name is **ProofGraph**, while compatibility-sensitive technical identifiers remain lowercase `proofgraph`. **PG-027 is complete** after the fresh V2 benchmark passed its pre-registered schema-v3 rule. “Complete” above means implemented and verified in the local PostgreSQL-backed repository; it does not mean publicly deployed.

### Current boundaries

- The isolated anonymous demo is implemented and verified locally, but no public environment has been deployed yet.
- The comparative evaluation harness is complete. The V1 result failed only builder-fit relative lift and remains unchanged; the distinct fresh V2 run passed all required dimensions. Private generation, mapping, judge, and detailed result artifacts remain ignored under `evaluation/runs/`; only aggregate results are published. Public deployment, hackathon packaging, and final demo acceptance remain tracked by PG-029 through PG-031.
- `replay_v1` is a strict canonical-fixture profile, not a general offline model. Inputs that do not match a committed semantic fixture fail explicitly with `fixture_input_mismatch`.
- `live_v1` and the live stages of `demo_hybrid_v1` require a server-side `OPENAI_API_KEY`. Browser code never receives provider credentials.

## Repository map

| Path | Responsibility |
|---|---|
| `proofgraph/graph/` | Canvas, node, edge, operation, staleness, lifecycle, and graph telemetry domain |
| `proofgraph/demo/` | Anonymous session authorization, canonical seed, reset, quotas, cleanup, and demo telemetry |
| `proofgraph/evaluation/` | Internal structured generation, deterministic blinding, automated model judging, mean scoring, disagreement telemetry, and paired bootstrap analysis |
| `proofgraph/generation/` | Run APIs, queue, context packing, providers, research, fixtures, SSE, patch review/application, and generation telemetry |
| `proofgraph/runtime/` | Health check, telemetry aggregation/audit drill, and generation-worker management commands |
| `fixtures/security-questionnaires/v1/` | Immutable canonical replay assets and semantic-input commitments |
| `evaluation/` | Versioned synthetic benchmark scenarios and the private-artifact workflow guide |
| `demo-steps.md` | Phase 5 operator checklist for automated blind judging, offline analysis, and acceptance |
| `web/` | Isolated React/Vite workspace, component tests, and Playwright journeys |
| `tests/` | PostgreSQL-backed unit, integration, replay, concurrency, lifecycle, and phase-flow tests |
| `design.md` | Architecture and product source of truth |
| `TASKS.md` | Dependency-ordered implementation tracker; completed work moves to Done |

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

`replay_v1` works without external credentials. Set `OPENAI_API_KEY` to enable `live_v1` and `demo_hybrid_v1`. A GitHub token and Stack Exchange key are optional but increase their public API allowances; never expose any provider credential to the browser client. The checked-in example enables `DEMO_PUBLIC_MODE=true`; set it to `false` when you need the unrestricted local operator welcome/create/open workflow.

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

Open `http://127.0.0.1:5173`. With `DEMO_PUBLIC_MODE=true`, Vite proxies `/api` to Django and the browser opens directly into a private seeded demo. With public mode off, use `http://127.0.0.1:5173/?demo=1` to verify the same demo flow locally without changing server configuration. The page reports readiness only when Django can execute a PostgreSQL query. You can inspect the same result directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

For a non-blocking worker startup check:

```powershell
uv run python manage.py run_generation_worker --once
```

`--once` processes at most one eligible run. Normal workers claim PostgreSQL rows with `FOR UPDATE SKIP LOCKED`, keep a 60-second fenced lease alive every 12 seconds on a dedicated database connection, physically delete expired research/source-content cache rows and bounded batches of expired demo sessions at startup and every 60 seconds, and recycle after 50 jobs or four hours.

## Production observability and security verification

Generation, demo, graph, provider, cache, source-ingestion, worker, and patch components emit one JSON object per telemetry record. Every record carries an intrinsic UTC timestamp and component name; applicable records carry correlated run, canvas, demo-session, patch, ingestion, graph-operation, worker, lease-epoch, attempt, operation-key, profile/version, duration, response, token, count, cache, and error fields. The emitter redacts credentials, authorization values, cookies, lease tokens, and sensitive URL query values before serialization.

`observability_report` consumes a dedicated JSONL telemetry export and produces queue/depth, stage-duration/reuse, failure/retry/cancellation, lease/reclaim, provider latency/token/error, patch/conflict/regeneration, evidence quality, cache, source-ingestion, and demo-lifecycle views:

```powershell
uv run python manage.py observability_report `
  --input .\telemetry.jsonl `
  --output .\observability-report.json
```

The strict PG-028 drill expects the database referenced by the telemetry to remain available. It finds and correlates a successful run, retryable provider failure, lease loss, and patch conflict across logs, aggregate metrics, durable generation events, stage checkpoints, patch decisions, and graph-operation audits. Use `--include-audit-payloads` only in an access-controlled operator environment because it includes retained semantic contexts and derived excerpts:

```powershell
uv run python manage.py observability_report `
  --input .\diagnostic-telemetry.jsonl `
  --output .\diagnostic-report.json `
  --require-drill `
  --include-audit-payloads
```

The report fails the strict drill if a scenario cannot be correlated or if structured records omit required identifiers. Audit snapshots preserve frozen prompt, strategy, provider/model and fixture versions; packed context; bounded sources and claims; candidate and critique checkpoints; accepted/rejected patch operations; and direct user edits. Raw third-party source documents remain excluded by DQ-003.

The security regression gate combines endpoint-wide anonymous authorization, CSRF and quota tests; URL/redirect/private-network and retention defenses; structured user-input isolation; prompt-injection and intellectual-property policy tests; server-only credential checks; inert command-like user text; and React text escaping:

```powershell
uv run pytest `
  tests/test_demo_sessions.py `
  tests/test_secure_sources.py `
  tests/test_source_ingestion.py `
  tests/test_structured_providers.py `
  tests/test_generation_pipeline_schemas.py `
  tests/test_security_hardening.py

Set-Location web
npm test -- --run src/App.test.tsx
Set-Location ..
```

## Phase 5 anonymous demo

`GET /api/demo/bootstrap` validates the signed HttpOnly SameSite cookie or creates a new 24-hour `demo_session` and isolated clone of the DQ-008 security-questionnaire canvas. The cookie expires with the server session. An invalid or expired cookie on bootstrap rotates to a new session; an expired cookie on any resource API returns `demo_session_expired` without doing work. React development-mode duplicate effects share one in-flight bootstrap request, preventing accidental double-session creation.

`POST /api/demo/reset` is the one-click restore path. It keeps the original session expiry and quota window, fences and terminalizes nonterminal work, swaps in a new canonical clone under the session lock, and deletes the retired canvas through the same authoritative lifecycle as direct deletion. Retired or cross-session canvas, operation, run, SSE, patch, source, and ingestion identifiers return the same non-enumerating `404`. Mutations retain Django CSRF validation.

Anonymous generation defaults to `demo_hybrid_v1`, which uses canonical fixture planning/research/extraction and live GPT-5.6 synthesis/critique/patch construction. `replay_v1` remains a visible, user-selected full-fixture fallback; the server never silently changes the stored profile. Anonymous `live_v1`, test, and unknown profiles are rejected. Hybrid cost controls allow 12 runs per session per one-hour window, at most two active hybrid runs per session, and 120 global hybrid runs per clock-hour window. Counters and run creation commit atomically; a `429` includes `replay_v1` as the explicit fallback.

Expired-session cleanup claims at most 100 sessions per pass with `FOR UPDATE SKIP LOCKED`. Queued or expired-lease work is fenced and terminalized; a live worker receives cancellation and retains ownership until its lease is safely fenced. Only then are the canvas and session deleted. Demo telemetry covers creation, expiry, cleanup, reset, rejected profiles, session/concurrent/global quota rejection, the global circuit breaker, and replay selection.

### Local judge path

1. Start PostgreSQL, Django, the worker, and Vite with `DEMO_PUBLIC_MODE=true`, then open `http://127.0.0.1:5173`—no account or canvas ID is required.
2. Confirm the seeded goal and three pinned constraints, open generation controls, and leave the instruction blank so the canonical fixture identity remains exact.
3. Keep the default **Hybrid live reasoning** profile when `OPENAI_API_KEY` is configured, or explicitly choose **Deterministic replay** for the no-provider path.
4. Select the goal and all three constraints, start generation, follow the durable progress stream, review the candidate patch, and apply the desired dependency-closed operations.
5. Use **Reset demo** at any point to return only this visitor's session to a fresh seed without extending expiry or restoring hybrid quota.

## Phase 5 comparative evaluation

PG-027 is a completed internal benchmark; it adds no application route or browser control. The checked-in
`evaluation/scenarios.v1.json` contains 20 synthetic builder scenarios with explicit constraints,
advantages, preferences, and evidence limitations. The operator must select one of
`gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna`; all four variants use that same selected model,
medium reasoning effort, and output budget, and normalize to three opportunities:

- `generic`: direct opportunity generation from the builder scenario.
- `strategy_only`: strategy-catalog planning followed by opportunity generation.
- `strategy_plus_evidence`: planning, complete analysis of the versioned evidence packet, and
  opportunity generation.
- `full_pipeline`: the evidence path followed by one critique-and-revision pass.

`generate_evaluation_variants` uses six bounded concurrent workers by default, is resumable,
serializes deterministic checkpoint writes, records private response IDs and token usage, disables
API storage, and refuses bare `gpt-5.6`, unlisted models, or execution without both
`OPENAI_API_KEY` and `--confirm-cost`; the completed Terra run made 200 provider stages and produced
80 outputs. `prepare_evaluation_packet` randomized each scenario's output order behind opaque IDs and
wrote the existing blind packet and separately held private map. Legacy empty human templates remain
for artifact compatibility but are inactive.

`judge_evaluation_packet` runs Vera Crosscheck on Sol and Marco Launch on Luna with one structured
call per scenario, independently shuffled opaque output order, medium reasoning, a fixed 3,000-token
output ceiling, `store=False`, six-worker concurrency, atomic checkpoints, and strict resumability.
It makes exactly 40 paid calls and materializes two 80-rating artifacts only after completion. The
judges never receive the private map, generation metadata, peer scores, or variant labels.
`analyze_evaluation` always takes the arithmetic mean of both judge scores, reports every absolute
two-point disagreement without adjudication, retains both original scores and rationales, and emits a
schema-v2 V1 result or an explicitly selected schema-v3 V2 result with all seven dimensions and
deterministic 10,000-resample paired scenario-bootstrap intervals.

The full workflow, artifact privacy boundaries, and commands are documented in
[`evaluation/README.md`](evaluation/README.md). Terra generation, the 40-call judge run, rating
materialization, and analysis are complete. The frozen v1 required-dimension result is:

| Dimension | Mean full − generic | 95% bootstrap CI | Result |
| --- | ---: | --- | --- |
| Evidence relevance | `+2.925` | `[2.725, 3.100]` | Pass |
| Specificity | `+0.950` | `[0.800, 1.100]` | Pass |
| Testability | `+1.650` | `[1.475, 1.825]` | Pass |
| Builder fit | `+0.350` | `[0.175, 0.550]` | **Fail** |

Both judges scored full-pipeline builder fit at `5.0`, while generic already scored `4.5` and `4.8`,
leaving insufficient headroom for the frozen `+0.500` relative-lift rule. Only 2 of 560 score
comparisons had disagreements of at least two points, and neither concerned builder fit. The report
therefore remains an authoritative FAIL rather than being adjusted after observation.

The pre-registered V2 rule retains the V1 `+0.500` mean-lift and positive confidence-interval
requirements for evidence relevance, specificity, and testability. Builder fit instead requires a
full-pipeline mean of at least `4.500 / 5` and a non-negative paired confidence-interval lower bound.
The official fresh post-registration V2 result is schema 3, identifies
`comparative_acceptance_v2`, and passes:

| Required dimension | Full-pipeline mean | Mean full − generic | 95% bootstrap CI | Result |
| --- | ---: | ---: | --- | --- |
| Evidence relevance | `5.000` | `+2.950` | `[2.675, 3.225]` | Pass |
| Specificity | `5.000` | `+0.825` | `[0.675, 0.950]` | Pass |
| Testability | `5.000` | `+1.450` | `[1.250, 1.650]` | Pass |
| Builder fit | `5.000` | `+0.450` | `[0.250, 0.650]` | Pass |

Terra produced 80 complete outputs with no partials; Vera Crosscheck on Sol and Marco Launch on Luna
each produced 80 ratings. Only one of 560 score comparisons differed by at least two points. The
earlier V1-artifact V2 reanalysis remains diagnostic only and does not replace either official result.

The completed V2 workflow is automated by [`scripts/run-evaluation-v2.ps1`](scripts/run-evaluation-v2.ps1)
and documented in [`demo-steps-v2.md`](demo-steps-v2.md). Its paid stages require explicit cost
confirmation, its dry-run mode creates no artifacts or provider calls, and the completed private run
remains ignored under `evaluation/runs/eval-terra-v2/`.

## Current end-to-end workflow

1. Create or open a canvas, then add one goal and the relevant global or branch-scoped builder constraints.
2. Open generation controls, choose an execution profile, select the goal and constraints, and start `generate_strategies`.
3. Keep the worker running while the browser reconstructs progress from the canvas SSE cursor. When the run reaches a pending patch, inspect its operations and apply all or a dependency-closed subset.
4. Select one accepted strategy and run `research_evidence`. Provisional source-backed claims remain visibly separate from authoritative graph state until the evidence patch is reviewed and applied.
5. Select the accepted strategy plus accepted, current claims and run `synthesize_opportunities`. The resulting patch contains three opportunities plus critique-derived assumptions, risks, contradictions, validation experiments, provenance, and separate quality dimensions.
6. Apply the selected patch operations transactionally. The applied opportunity inspector preserves distribution and defensibility rationale alongside evidence strength, novelty, builder fit, feasibility, distribution clarity, and operational burden.
7. Edit or remove an upstream premise, disconnect a dependency, reject accepted evidence, or replace an assumption. The audited mutation marks every reachable dependent stale according to the fixed edge-direction table.
8. Explicitly regenerate one stale production unit or a composite branch. Applying the regeneration patch keeps the old stale branch and causes intact, creates fresh successors and `evolves_into` lineage, clones applicable branch constraints, and enables retained-branch comparison.

Patch rejection, generation failure, cancellation, retry, lease loss, and SSE reconnect never promote provisional UI state into the authoritative graph. Only transactional graph operations and an accepted pending patch do that.

## Phase 2 generation APIs and SSE verification

Generation remains operation-specific. Create a run with `POST /api/canvases/{canvas_id}/generation-runs`, inspect it with `GET /api/generation-runs/{run_id}`, and use the `/cancel` or `/retry` subresource for explicit lifecycle controls. Run creation accepts only `operation`, `selected_node_ids`, `expected_node_versions`, optional `instruction`, `execution_profile_id`, `idempotency_key`, and `regeneration_scope` only for `regenerate_stale`.

The run endpoint locks the canvas before selected nodes and freezes one semantic context snapshot. An exact idempotency-key replay returns the existing run with `202`; conflicting reuse returns `409`. The browser consumes the persisted SSE API through one cursor-replayed stream per canvas and reconstructs active, failed, cancelled, retried, and patch-ready runs after reconnect.

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

## Phase 4 patch review and explicit regeneration

Editing or deleting a semantic node or dependency edge traverses the explicit edge-direction table and writes operation-linked staleness causes. The first fresh-to-stale transition increments the semantic version once; additional causes remain append-only without another version bump. Rejecting accepted evidence is one audited transaction that recalculates source eligibility, preserves independently supported claims, rejects unsupported claims, excludes rejected material from future generation context, and invalidates every dependent descendant.

Regeneration is always explicit and always parallel. Node scope regenerates one normalized strategy, claim/provenance, or opportunity-family production unit. Branch scope freezes a deduplicated, checkpointed strategy/evidence/opportunity workset and emits one final candidate patch. Applying that patch never edits or clears the old stale branch: each production root receives one fresh successor with `regenerated_from_node_id`, the frozen `regeneration_scope`, and `lineage_mode: parallel`; an audited `evolves_into` edge links old to new; and every applicable branch-scoped constraint is cloned onto its successor. Partial review treats each successor, lineage edge, and constraint-clone set as one atomic dependency group.

The patch preview and applied opportunity inspector show evidence strength, novelty, builder fit, technical feasibility, distribution clarity, and operational burden separately, together with distribution and defensibility rationale. Failed, cancelled, lease-lost, or retried runs clear ephemeral loading state without mutating the authoritative graph; only a pending patch can replace the overlay with review UI.

The same canvas-operation endpoint also owns the Phase 4 audited actions `REJECT_EVIDENCE` and `REPLACE_ASSUMPTION`. Rejected source and claim nodes remain readable, focusable, visibly badged, and inspectable after reload, but they are excluded from future context and generation selection. Assumption replacement records the previous value and invalidates its owning opportunity family without silently regenerating it.

## Current API surface

All mutating browser requests retain Django CSRF protection. In public mode, every resource endpoint below resolves the anonymous session before resource lookup; direct canvas creation/deletion and profiles outside hybrid/replay are disabled. The local API currently exposes:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Verify application/PostgreSQL readiness, report public-demo mode, and issue the CSRF cookie |
| `GET` | `/api/demo/bootstrap` | Resume a valid anonymous demo or create a new isolated seeded session |
| `POST` | `/api/demo/reset` | Fence active work and replace only the current session's canvas with a fresh seed |
| `POST` | `/api/canvases` | Create a canvas |
| `GET`, `PATCH`, `DELETE` | `/api/canvases/{canvas_id}` | Read, rename, or delete a canvas and its owned durable data |
| `GET`, `POST` | `/api/canvases/{canvas_id}/operations` | Replay later revisions or apply one idempotent localized graph operation |
| `POST` | `/api/canvases/{canvas_id}/generation-runs` | Create an operation-specific generation or regeneration run |
| `GET` | `/api/generation-runs/{run_id}` | Inspect durable run state and its linked patch |
| `POST` | `/api/generation-runs/{run_id}/cancel` or `/retry` | Cancel or explicitly retry a run |
| `GET` | `/api/canvases/{canvas_id}/events?after={sequence}` | Replay and stream canvas-scoped SSE progress |
| `GET` | `/api/graph-patches/{patch_id}` | Inspect a candidate patch, review metadata, dependencies, and decisions |
| `POST` | `/api/graph-patches/{patch_id}/apply` | Apply all operations, a dependency-closed selection, or only nonconflicting operations |
| `POST` | `/api/graph-patches/{patch_id}/reject` | Reject the complete pending patch without graph mutation |
| `POST` | `/api/graph-patches/{patch_id}/regenerate` | Request one idempotently linked revised patch run with an instruction |
| `POST` | `/api/canvases/{canvas_id}/sources` | Start bounded URL or user-text source ingestion |
| `GET` | `/api/source-ingestions/{ingestion_id}` or `/api/sources/{source_id}` | Inspect ingestion state or retained derived source metadata |

Graph patch apply accepts optional `selected_operation_ids` and `apply_nonconflicting_only`. Dependency closure is validated server-side; client selection cannot orphan a prerequisite, lineage edge, or cloned branch constraint. Patch rejection accepts no fields. Patch regeneration requires a nonblank `instruction` and `idempotency_key`.

## Retained source content

ProofGraph follows the DQ-003 derived-evidence-only policy. It never persists complete retrieved pages, HTML, or user-supplied source documents. Durable records may contain citation metadata, content hashes, derived claims, and sanitized excerpts of at most 500 Unicode characters. Accepted graph evidence and run/stage/event audit records remain until canvas deletion; deleting a canvas permanently cascades through its graph, runs, stages, events, patches, decisions, reservations, and caches. Future query caches have a 24-hour physical-expiry requirement, retrieved-content rows keep `retained_content` null, and test fixture bundles must be synthetic or explicitly redistributable.

## Checks

Latest verified Phase 5 repository gate, including the deterministic PG-027 harness, on July 15, 2026:

- PostgreSQL migrations, migration-drift detection, and database readiness passed.
- Ruff formatting and lint passed.
- All **270 backend tests** passed.
- Frontend formatting, lint, application/e2e type checking, all **29 unit/component tests**, and the production build passed.
- All **3 live PostgreSQL-backed Playwright journeys** passed: the anonymous demo seed/reset/retired-canvas path, durable Phase 4 invalidation, and the Phase 1 graph journey.
- Representative plans use the partial demo active-run index and ordered session-expiry index without sequential scans.

PG-027 closure was verified on July 20, 2026: all 17 focused evaluation tests passed; the official
V2 artifacts contain 80 complete outputs, zero partials, and 80 ratings per judge; and the
schema-v3 report passed all required dimensions. This closure verification made no additional
provider calls.

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

## Phase 4 verification

Run the patch-review and regeneration gate after Phase 3:

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

The Phase 4 coverage adds every dependency direction, cycles and converging paths, multiple active causes, pre-delete and changed-edge invalidation, evidence rejection with independent support, manual/ineligible regeneration rejection, target-local and composite checkpoint resume/cancellation, DQ-004 add-only lineage and constraint clones, atomic partial lineage selection, critique-to-preview-to-transactional-apply, patch conflict recovery, persistent quality inspection, replay-safe SSE progress, and retained-branch actions. PG-025 completes the phase exit with 248 passing backend tests, 26 passing frontend unit/component tests, a production build, and two live PostgreSQL-backed Playwright journeys, including durable visible invalidation across reload.

## Phase 5 demo verification

Run the anonymous-demo gate after Phase 4:

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

The gate currently contains 275 backend tests, 29 frontend component tests, and three Playwright journeys. Phase 5 coverage verifies signed-cookie forgery rejection; exact seed isolation; unique active-canvas ownership; expired bootstrap rotation versus API denial; reset without expiry/quota evasion; reset and cleanup lease fencing; CSRF; cross-session and retired-resource denial; anonymous profile allowlisting; hybrid quotas and circuit breaker; demo query plans; cached-evidence labels; live-versus-replay explanation; bootstrap request coalescing; and the one-click judge journey. PG-027 coverage additionally validates all 20 synthetic scenarios, the frozen 1/2/3/4-stage variants, API-storage disabling, bounded concurrent generation and judging with serialized deterministic checkpoints, stage-level and judge-call resume, sanitized provider-error recovery, strict structured-output schema compatibility, legacy-artifact compatibility, deterministic blinding, private-map separation, independently shuffled model-judge prompts, complete seven-dimension scoring, arithmetic means for large disagreements, disagreement telemetry, paired bootstrap thresholds, and the offline management-command artifact workflow. Tests make no paid provider calls.

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
| `DEMO_PUBLIC_MODE` | When true, require anonymous demo sessions and bootstrap the browser directly into its isolated canonical canvas. |

## Stop local services

```powershell
docker compose down
```

`docker compose down -v` also deletes the local PostgreSQL volume and all of its data.
