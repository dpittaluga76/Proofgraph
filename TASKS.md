# Project Task Tracker

This backlog is derived from `design.md`, which is the source of truth. Tasks are ordered by dependency and implementation phase. When a task is completed, move its full entry to **Done**.

## Current Goal

Deliver **Phase 1: Graph foundation** so a user can create, edit, lay out, save, and reload a typed opportunity canvas.

Current phase exit criteria:

- Canvas, node, and edge state is persisted in PostgreSQL.
- Localized graph operations are idempotent, versioned, validated, and recorded.
- The browser renders and edits the graph, including node movement and auto-layout.
- Save/reload and optimistic-conflict behavior are covered by tests.

Next task: **PG-001**.

## Pending

### Decision gates

Decision tasks are listed before their dependents. Each completed decision must be recorded in `design.md` or a linked architecture decision and moved to **Done**.

#### DQ-002 — Choose how rejected evidence remains visible for auditability

**Depends on:** None

**Outcome:** Select muted-visible or hidden-by-default presentation without changing the fixed rejection, exclusion, and invalidation semantics.

**Done when:** The decision, rationale, accessibility behavior, and canvas acceptance criteria are recorded for PG-024.

#### DQ-003 — Define retained-source-content lifecycle

**Depends on:** None

**Outcome:** Define what source content may be retained, for how long, and how deletion propagates across every persistence surface.

**Done when:** The policy covers graph nodes, stage outputs, persisted event payloads, normalized caches, fixture bundles, canvas deletion, user-visible disclosure, and verification requirements for PG-006, PG-009, PG-015, PG-016, and PG-019.

#### DQ-004 — Choose replace-versus-parallel stale-branch regeneration

**Depends on:** None

**Outcome:** Define whether regenerated stale work replaces the old branch or creates a parallel branch.

**Done when:** The decision records provenance, undo/audit, branch comparison, and patch behavior required by PG-023 and PG-024.

#### DQ-005 — Decide explicit-neighborhood versus semantic-similarity context

**Depends on:** None

**Outcome:** Select the MVP context-neighborhood strategy without introducing an unrecorded default.

**Done when:** The design records the algorithm, deterministic ranking implications, cost, fallback behavior, and tests required by PG-013 and PG-023.

#### DQ-006 — Choose internal-only or product-UI evaluation harness

**Depends on:** None

**Outcome:** Decide where benchmark results run and appear.

**Done when:** The design records the audience, data exposure, UI scope if any, and acceptance criteria for PG-027.

#### DQ-007 — Select the final product name

**Depends on:** None

**Outcome:** Select the submission and product name.

**Done when:** Naming, repository/UI migration scope, and submission usage are recorded for PG-030.

#### DQ-008 — Select the canonical demo opportunity

**Depends on:** None

**Outcome:** Select the scenario used by fixtures, the seeded canvas, evaluation examples, and the judge demonstration.

**Done when:** The scenario inputs, expected evidence, opportunity shape, reset state, and fixture-bundle identifier are recorded for PG-019 and PG-026.

### Phase 1 — Graph foundation

#### PG-001 — Bootstrap the application runtime

**Depends on:** None

**Outcome:** Establish the smallest runnable Django, PostgreSQL, browser-client, and test foundation without introducing infrastructure excluded by the design.

**Done when:**

- The Django ASGI web process starts locally and connects to PostgreSQL through environment-based configuration.
- A separate worker entry point exists, even if generation work is not implemented yet.
- The browser client has a documented, reversible build/development setup.
- Setup includes baseline formatting, static analysis, and test commands.
- No Redis, Celery, Neo4j, or WebSocket dependency is introduced.

#### PG-002 — Implement the relational graph schema

**Depends on:** PG-001

**Outcome:** Persist canvases, typed nodes, typed edges, and append-only graph operations using the frozen domain taxonomy.

**Done when:**

- Migrations create `canvas`, `node`, `edge`, and `graph_operation` records aligned with design sections 9 and 12.
- Nodes and edges belong to one canvas; nodes carry independent semantic and position versions, while edges carry an entity version.
- Node semantic content is separated from position and UI-only state.
- Node and edge kinds are validated against the frozen MVP taxonomy.
- Referential integrity prevents cross-canvas edges and dangling endpoints.
- Graph operations store an actor-scoped operation key and request fingerprint with a uniqueness constraint per canvas.

#### PG-003 — Implement canvas and localized graph-operation APIs

**Depends on:** PG-002

**Outcome:** Support canvas CRUD and localized graph mutations rather than whole-graph replacement.

**Done when:**

- Canvas endpoints implement the API surface in design section 27.1.
- `POST /api/canvases/{canvas_id}/operations` supports `ADD_NODE`, `UPDATE_NODE`, `DELETE_NODE`, `ADD_EDGE`, `UPDATE_EDGE`, `DELETE_EDGE`, `PATCH_NODE_METADATA`, and `MOVE_NODE`.
- Every direct mutation includes a client-generated operation key; exact retry returns the original result and conflicting key reuse returns `409`.
- `GET /api/canvases/{canvas_id}/operations?after={revision}` returns every later operation in deterministic revision-and-operation order for incremental synchronization.
- Semantic node mutations validate `expected_version`; `MOVE_NODE` validates `expected_position_version`; edge updates/deletes validate the edge version.
- Semantic mutations increment only the semantic version and invalidate token metadata; `MOVE_NODE` increments only the position version, so layout changes cannot invalidate generation or graph-patch preconditions.
- Each successful mutation appends a `graph_operation` and increments the canvas revision in the same short transaction.
- API tests cover validation, rollback, cross-canvas isolation, incremental operation replay, and optimistic conflicts.

#### PG-004 — Build the editable graph canvas

**Depends on:** PG-003

**Outcome:** Let a user create and manipulate the typed graph through a browser canvas.

**Done when:**

- A user can create/open a canvas and add goal and constraint nodes.
- Typed nodes and edges render distinctly without supporting arbitrary ontology editing.
- A user can select nodes, edit semantic content, add/remove edges, delete entities, and move nodes.
- Node movement is persisted through idempotent localized `MOVE_NODE` operations using position-version preconditions.
- An auto-layout action produces readable deterministic placement without changing semantic graph state.

#### PG-005 — Complete Phase 1 persistence and graph-foundation verification

**Depends on:** PG-003, PG-004

**Outcome:** Prove the graph foundation survives real editing and reload flows.

**Done when:**

- Save/reload preserves graph content, metadata, positions, entity versions, and canvas revision.
- Concurrent stale edits produce a recoverable UI conflict instead of overwriting newer state.
- Tests cover node/edge CRUD, operation idempotency and conflicting-key reuse, semantic/position version isolation, operation ordering, revision increments, auto-layout persistence, and reload.
- The canonical goal-plus-builder-constraints setup works end to end.
- Phase 1 setup and verification commands are documented.

### Phase 2 — Durable jobs and progress

#### PG-006 — Add generation persistence and run APIs

**Depends on:** PG-005, DQ-003

**Outcome:** Persist generation state and expose an idempotent, version-checked entry point for operation-specific runs.

**Done when:**

- Migrations implement `generation_run`, `generation_stage`, `canvas_event_cursor`, `generation_event`, `graph_patch`, and `graph_patch_operation_decision` from design section 12.
- The durable layer defines stable `RunContextFactory`, `ExecutionProfileResolver`, and `StageOutputValidator` ports plus a generic validated stage-result envelope.
- Deterministic test-only adapters exercise the Phase 2 system; they are unavailable to product requests and cannot silently substitute for an approved profile.
- `POST /api/canvases/{canvas_id}/generation-runs` resolves the injected ports, validates the operation and selected-node kinds, locks and verifies expected semantic versions, captures their immutable outputs, creates the queued run in one short transaction, and immediately returns `202`, `run_id`, and `events_url`.
- The supported operations are `generate_strategies`, `research_evidence`, `synthesize_opportunities`, and `regenerate_stale`, each with the stage plan defined in design section 14.1.
- Idempotency keys are unique per canvas: an identical request returns the existing run and a conflicting reuse returns `409`.
- `GET /api/generation-runs/{run_id}` returns status, current stage, attempts, structured terminal error, and ready patch ID when present.
- Run context snapshots, manifests, semantic hashes, selected node versions, retry fields, request fingerprints, and immutable execution configuration are stored.
- Unique constraints protect stage keys, canvas event sequences, run event sequences, and per-operation patch decisions.
- The cursor migration backfills every existing canvas, and the canvas-creation path atomically creates a cursor row for every future canvas.
- Run creation emits the shared structured-log context required for later run, stage, worker, and provider instrumentation.
- Model tests cover allowed state transitions and terminal states.

#### PG-007 — Implement queue claiming and fenced worker leases

**Depends on:** PG-006

**Outcome:** Claim work safely through PostgreSQL `SKIP LOCKED` with stale-worker fencing.

**Done when:**

- The worker claims one eligible run using `SELECT ... FOR UPDATE SKIP LOCKED` in a short transaction.
- Every claim assigns `worker_id`, a fresh lease token, and an incremented lease epoch.
- Every worker checkpoint/finalization write verifies run ID, running status, lease token, and lease epoch.
- A lease-keeper renews heartbeats on its own database connection every 10–15 seconds.
- The default lease is 60 seconds, heartbeat and expiry use PostgreSQL time, and renewal extends only the current token/epoch lease.
- Queue-depth, claim-attempt, heartbeat-failure, and lease-loss telemetry is emitted with run/canvas/worker identifiers.
- Concurrent-worker tests cover delayed-but-valid heartbeat, expiry/reclaim, late fenced renewal, and prove a stale worker cannot checkpoint or finalize after reassignment.

#### PG-008 — Implement stage checkpoints, retry, and resume

**Depends on:** PG-007

**Outcome:** Resume failed runs from the first incomplete stage without repeating valid completed work.

**Done when:**

- Stage input hashes include semantic input, stage version, provider identity, profile, and fixture version supplied through the injected durable-layer ports.
- The worker executes only the stages configured for the run operation; deterministic clustering is checkpointed with its versioned application-stage identity.
- Completed stage outputs are immutable, pass the generic Phase 2 envelope and the injected stage-specific validator, and are reusable only on an exact key match.
- The worker executes external calls outside database transactions using the two-transaction stage protocol.
- Retry provides at-least-once execution while graph state remains isolated behind unapplied patches.
- Integration tests prove completed stages are reused and invalidated stages are rerun.
- Stage duration, attempt, failure/retry, model-response-ID, and token-usage hooks emit structured telemetry even when a test adapter supplies the values.

#### PG-009 — Implement persisted canvas-scoped SSE progress

**Depends on:** PG-006

**Outcome:** Stream and replay generation progress through one SSE connection per canvas.

**Done when:**

- `GET /api/canvases/{canvas_id}/events?after={canvas_sequence}` replays committed events in ascending canvas-sequence order and then waits for new ones.
- Appending an event allocates its monotonic canvas sequence through `canvas_event_cursor` and inserts the event in the same short transaction.
- All design section 17 event types include both canvas and run sequences.
- Research-source and extracted-evidence events carry `provisional: true`, and progress handling never treats them as authoritative graph mutations.
- Persisted payloads follow DQ-003 retention/redaction rules, including replay and canvas deletion.
- Heartbeat comments keep idle streams alive, and disconnect/reconnect does not lose progress.
- Compression/buffering middleware does not materialize `text/event-stream` responses.
- Integration tests interleave at least two concurrent runs on one canvas and prove replay skips or duplicates no committed event.
- The setup guide records the ASGI command, proxy settings if used, and an incremental `curl -N` verification.

#### PG-010 — Add cancellation, terminal failures, and bounded worker recycling

**Depends on:** PG-007, PG-008, PG-009

**Outcome:** Make long-running work recoverable and prevent poison jobs or worker growth from degrading the system.

**Done when:**

- `POST /api/generation-runs/{run_id}/cancel` records a request checked before each stage, after external calls, and before finalization.
- `POST /api/generation-runs/{run_id}/retry` requeues only a failed run with a retryable error, no active lease, and remaining attempts; it preserves immutable inputs/checkpoints, emits `run.retry_requested`, and returns `409` for cancelled, non-retryable, lease-owned, or exhausted runs.
- Runs fail terminally after the configured maximum attempts with a structured error and `run.failed` event.
- Failures preserve completed checkpoints and never leave permanent loading state.
- Workers exit gracefully after 50 jobs or 14,400 seconds and release connections, lease threads, queries, caches, and large payloads between jobs.
- Tests cover cancellation, explicit safe retry, exhausted retry rejection, poison jobs, lease loss, worker crash recovery, and clean recycling.

#### PG-011 — Verify the durable-job architecture end to end

**Depends on:** PG-008, PG-009, PG-010

**Outcome:** Demonstrate reliable job execution under concurrency and interruption before adding live intelligence.

**Done when:**

- A deterministic test-double job progresses from queued to completed and emits replayable stage events without depending on the Phase 3 fixture-bundle system.
- The Phase 2 test profile is proven unreachable from product requests.
- Reclaim after lease expiry cannot produce duplicate finalization.
- SSE reconnect resumes after the supplied canvas sequence, including when concurrent runs interleave events.
- A failed job resumes at its first incomplete stage.
- No test or trace shows an external call, retry sleep, or streaming loop inside a database transaction.

### Phase 3 — Intelligence pipeline

#### PG-012 — Define strict pipeline domain schemas and strategy templates

**Depends on:** PG-011

**Outcome:** Establish validated contracts for planning, research, sources, claims, opportunities, critiques, and graph patches.

**Done when:**

- The initial 14 opportunity strategies and their required signals/failure conditions are versioned data.
- Strict schemas distinguish operation plan, research questions, query plan, required evidence types, source, observed/derived/inferred/contradicting claim, evidence cluster, opportunity, assumption, risk, validation experiment, and graph patch, and supply production `StageOutputValidator` implementations for those outputs.
- Claim schemas require normalized, sorted `topic_keys` and `mechanism_tags` plus a stable `independence_key`; invalid or noncanonical values are rejected.
- Opportunity output requires buyer, problem, spend/workaround, mechanism, business model, why now, evidence, contradiction, assumptions, risks, experiment, and builder fit.
- Quality dimensions remain separate and the supported/speculative threshold is enforced.
- Invalid or incomplete structured model output fails explicitly.

#### PG-013 — Build deterministic operation-specific context selection

**Depends on:** PG-012, DQ-005

**Outcome:** Construct token-budgeted context from the relevant graph neighborhood, not the full canvas.

**Done when:**

- Context includes mandatory selected nodes, global pinned constraints, operation, user instruction, IDs, and versions.
- A production `RunContextFactory` implements the durable Phase 2 port without changing its stored run envelope.
- Provenance, evidence, descendants, and optional neighbors follow the frozen tier budgets and deterministic ranking rules.
- Traversal follows the design's per-edge dependency direction table, uses a visited set, and is cycle-safe.
- A bounded contradiction reserve is applied before supporting evidence is packed.
- Canonical semantic token counts are precomputed and invalidated when semantic content changes.
- Every run persists included and excluded entity IDs in a context manifest.
- Unit tests prove deterministic packing, stable tie-breaking, correct forward/inverse edge traversal, and termination on cycles.

#### PG-014 — Implement provider ports and execution profiles

**Depends on:** PG-012

**Outcome:** Keep orchestration independent of live, hybrid-demo, or replay provider implementations.

**Done when:**

- Typed ports exist for planning, research, extraction, synthesis, critique, and patch construction.
- Deterministic clustering is a versioned application-layer stage shared by all profiles rather than a provider port.
- A production `ExecutionProfileResolver` implements the durable Phase 2 port and resolves only registered product profiles.
- `live_v1` uses live planning, research, extraction, synthesis, critique, and patch construction; `demo_hybrid_v1` uses fixtures for planning/research/extraction and live synthesis/critique/patch construction; `replay_v1` uses fixtures for all six provider-backed stages.
- Product requests reject unregistered and test-only profiles.
- Domain orchestration contains no `is_demo_mode` branching.
- Each run stores immutable profile, fixture, pipeline, prompt, and strategy versions.
- Provider identity participates in stage input hashing.

#### PG-015 — Implement secure research adapters

**Depends on:** PG-014, DQ-003

**Outcome:** Gather bounded evidence from OpenAI hosted web search, GitHub, Stack Exchange, and user-supplied inputs.

**Done when:**

- Research respects the default budget of five queries and ten retained sources.
- Adapter telemetry records provider/source identity, latency, rate-limit outcomes, retained counts, and OpenAI token usage where available.
- Source results retain URL, title, retrieval time, content hash, source kind, and authority metadata.
- GitHub and Stack Exchange rate limits produce structured retryable failures.
- User-supplied URLs allow HTTPS only, reject private/loopback/link-local/metadata destinations, re-check redirects, and enforce time/size limits.
- `POST /api/canvases/{canvas_id}/sources` accepts either a protected HTTPS URL or bounded user-supplied text, validates it as untrusted data, and creates an audited user-authored source node for the canvas.
- Any URL retrieval occurs outside a database transaction; only the validated normalized result is persisted in a short transaction with its graph operation.
- `GET /api/sources/{source_id}` returns source metadata and permitted retained content without exposing credentials or unsanitized excerpts.
- Retrieved pages and user-supplied text remain isolated from system instructions, cannot trigger command execution, and pass adversarial prompt-injection tests.
- Source retention policy decision **DQ-003** is resolved and implemented.

#### PG-016 — Implement claim extraction, evidence clustering, and source caching

**Depends on:** PG-015

**Outcome:** Convert researched sources into deduplicated, source-backed claims and deterministic clusters with reusable PostgreSQL-backed caching.

**Done when:**

- One source can back multiple claims and one claim can reference multiple independent sources.
- Claims record classification, evidence type, strength, limitations, and source IDs.
- Claims record canonical `topic_keys`, `mechanism_tags`, and `independence_key` values used by deterministic retention and clustering.
- Duplicate, irrelevant, unsupported, and contradicting evidence are handled explicitly.
- At most twelve claims are retained using the source hierarchy, source authority, independent-source count, strength, recency, and stable-ID tie-breaking.
- A deterministic `clustering` checkpoint groups claims by the exact tuple of evidence type, sorted topic keys, sorted mechanism tags, and contradiction target without losing source provenance or independence boundaries.
- The PostgreSQL cache reads and writes exact keys covering normalized query, source URL/content hash, strategy version, prompt version, and context hash; repeat-hit and changed-key tests prove reuse and invalidation behavior.
- Cached results retain original retrieval metadata and a cache-hit marker so they cannot be presented as newly retrieved.
- Telemetry records extraction/retention counts, cluster counts, independent-source counts, cache hits/misses, and invalidation reasons.
- Each schema-valid extraction batch emits progressive provisional evidence; no source or claim becomes authoritative or selectable until its research patch is accepted.
- Rendered excerpts are sanitized before they reach the browser.
- Rejected sources and claims follow design section 8.2.1 and are excluded from context, quality thresholds, synthesis, and critique.
- Tests prove clustering is stable under input reordering and that syndicated copies sharing an `independence_key` do not count as independent support.

#### PG-017 — Implement opportunity synthesis and critique

**Depends on:** PG-013, PG-014, PG-016

**Outcome:** Produce three specific, evidence-aware opportunities and challenge them before presentation.

**Done when:**

- Synthesis generates exactly three structured candidates from the selected strategy, constraints, and retained evidence.
- Critique evaluates novelty, feasibility, buyer/budget, recurrence, distribution, operational burden, differentiation, builder fit, and falsifying evidence.
- Each candidate exposes separate quality dimensions rather than one synthetic score.
- `supported` is assigned only when every design section 7.3 threshold is met; otherwise the candidate is `speculative`.
- Every candidate includes material contradicting evidence or an explicit evidence gap.
- Synthesis and critique enforce the intellectual-property boundaries in design section 21.3 and reject recommendations to copy protected code/assets, impersonate trademarks, reuse private datasets, or violate third-party terms.
- The default budget permits one critique pass; retry reuses checkpoints rather than silently adding extra critique calls.

#### PG-018 — Construct candidate graph patches without direct graph writes

**Depends on:** PG-017

**Outcome:** Convert validated pipeline output into typed, reviewable graph operations.

**Done when:**

- Patch construction emits only supported localized operation types and records `base_canvas_revision`.
- Semantic update/delete operations include `expected_version`; `MOVE_NODE` includes `expected_position_version`; edge updates/deletes include the edge `expected_version`.
- Every newly proposed entity has a patch-local `client_generated_id`; IDs are unique within the patch and references may use only known server IDs or those local IDs.
- The patch records an operation dependency graph, and schema validation rejects cycles, unresolved references, and subsets whose required prerequisites are absent.
- New nodes and edges preserve traceable provenance from sources and claims through opportunities, risks, assumptions, and experiments.
- Pipeline completion stores a candidate `GraphPatch`; it never mutates authoritative graph state.
- Patch schema validation rejects missing endpoints, invalid kinds, and malformed metadata.
- The default budget permits one patch-construction pass; retry reuses a matching completed checkpoint.

#### PG-019 — Create strict versioned fixture bundles

**Depends on:** PG-014, PG-016, PG-017, PG-018, DQ-003, DQ-008

**Outcome:** Support deterministic hybrid and replay flows through immutable scenario bundles.

**Done when:**

- A canonical fixture directory contains a manifest, sources, claims, planning outputs, synthesis outputs, critique outputs, patch-construction outputs, and progress-event payloads.
- Fixture payloads pass through the same validation as live provider outputs.
- Matching includes scenario, stage, pipeline version, provider identity, semantic input hash, and fixture version.
- A mismatch fails with a recoverable fixture-input error and never silently falls back to live APIs.
- Fixture providers emit the same persisted domain events as live providers.
- Manifest cases cover planning, research, extraction, synthesis, critique, and patch construction for every operation plan; full replay reaches `patch.ready` without any live provider call.
- Fixture content and deletion behavior implement the retained-content policy selected in **DQ-003**.

#### PG-020 — Integrate and verify the intelligence pipeline

**Depends on:** PG-013, PG-018, PG-019

**Outcome:** Complete the canonical strategy, research, evidence-selection, and synthesis workflow through separate operation-specific runs in every approved profile.

**Done when:**

- A user can generate three materially different strategies from a goal and builder constraints.
- A `research_evidence` run produces the selected strategy, research questions, query plan, required evidence types, progressively inspectable provisional evidence, deterministic clusters, and a reviewable evidence patch.
- The user must accept the evidence patch and explicitly select applied, non-rejected evidence nodes before starting `synthesize_opportunities`; selected IDs and expected versions are captured by the new run.
- The synthesis run produces exactly three structured opportunities and never consumes provisional, rejected, unselected, or stale evidence.
- Every opportunity traces to supporting and contradicting claims.
- Live, hybrid-demo, and replay profiles use the same orchestrator and event contract.
- The production composition root registers `RunContextFactory`, `ExecutionProfileResolver`, and `StageOutputValidator`; only then are `live_v1`, `demo_hybrid_v1`, and `replay_v1` enabled for product requests, while the deterministic Phase 2 test profile remains unreachable.
- Tests cover every operation stage plan, the evidence-selection gate, timeouts, invalid structured output, rate limits, no-results, and unsupported-opportunity paths with user-readable errors.

### Phase 4 — Patch review and dependency-aware regeneration

#### PG-021 — Build graph-patch preview and selective review

**Depends on:** PG-018, PG-020

**Outcome:** Let users inspect and choose generated graph operations before any authoritative mutation.

**Done when:**

- Patch previews distinguish additions, updates, deletes, provenance, assumptions, risks, and contradictions.
- `GET /api/graph-patches/{patch_id}` returns the immutable candidate operations and any existing per-operation decisions.
- A user can accept all, accept selected operations, apply nonconflicting operations only, reject all, or request regeneration.
- Dependencies between selected operations are shown in review; selection includes all required prerequisites or is blocked with an actionable dependency error.
- `POST /api/graph-patches/{patch_id}/reject` records every candidate operation as rejected without mutating graph state and is idempotent on retry.
- Rejected patches and per-operation decisions remain auditable without appearing as accepted graph state.

#### PG-022 — Apply accepted patch operations transactionally

**Depends on:** PG-021

**Outcome:** Apply user-approved operations atomically with deterministic locking and entity-version checks.

**Done when:**

- Apply locks the patch, canvas, and touched entities in deterministic ID order under PostgreSQL `READ COMMITTED`.
- `POST /api/graph-patches/{patch_id}/apply` accepts the requested operation subset and optional nonconflicting-only mode.
- Expected versions, endpoint existence, dependency closure, and patch-local identity references are validated before writes.
- The server allocates deterministic-on-retry UUID mappings for accepted `client_generated_id` values, persists the `client_id_map`, resolves dependent references in topological order, and returns the map.
- Each applied operation uses a deterministic patch-derived operation key so an exact retry returns the original outcome and a conflicting reuse is rejected.
- `base_canvas_revision` remains audit context rather than a global apply precondition; only touched-entity versions and dependency validation cause conflicts, and a conflicted prerequisite skips its dependents.
- Accepted operations, linked graph-operation records, accepted/rejected/skipped-conflict decisions, canvas revision, timestamps, and final patch status commit together.
- Conflicts return enough information to review, apply only nonconflicting operations, or regenerate from current state.
- Patch telemetry records conflict outcomes, accepted/rejected/skipped counts, apply duration, and accepted-operation ratio.
- Tests cover full apply, partial apply, rejection, rollback, idempotency, and concurrent conflict.

#### PG-023 — Implement transitive staleness and explicit regeneration

**Depends on:** PG-022, DQ-004, DQ-005

**Outcome:** Mark dependent descendants stale after premise changes and regenerate only when the user explicitly requests it.

**Done when:**

- Editing/removing an upstream premise propagates stale state transitively using the design's explicit per-edge dependency direction table.
- Rejecting previously accepted evidence propagates staleness to every accepted descendant that depended on it while leaving independently supported claims eligible.
- Propagation is a cycle-safe breadth-first traversal with a visited set; converging paths do not duplicate work and the origin is not re-marked through a cycle.
- Staleness never triggers automatic recursive generation.
- A user can regenerate one selected stale node or branch with a fresh context snapshot.
- Regenerated output is delivered as another candidate patch.
- Backend operations support audited rejection of previously accepted evidence with semantic-version preconditions, assumption replacement, retained branch lineage, and branch comparison according to **DQ-004**.
- Regeneration context selection implements the explicit-neighborhood or semantic-similarity policy selected in **DQ-005**.
- Tests cover every edge kind and direction, pre-delete and changed-edge relationships, cycles, converging paths, independent support, evidence rejection, assumption replacement, and parallel/replacement branch lineage.

#### PG-024 — Implement generation placeholders and resilient progress UX

**Depends on:** PG-009, PG-020, PG-021, PG-023, DQ-002, DQ-004

**Outcome:** Represent work in progress without presenting placeholders as real ideas or leaving indefinite loading state.

**Done when:**

- A placeholder is explicitly ephemeral and tied to one run, or is rendered as a non-persisted overlay.
- Schema-valid extraction batches appear progressively as provisional evidence and are clearly distinguished from accepted graph nodes.
- Rejected evidence follows the muted-or-hidden presentation selected in **DQ-002** without changing its fixed exclusion and invalidation semantics.
- The canvas exposes audited actions to reject previously accepted evidence, replace an assumption, and compare retained branches; disabled states explain when lineage or dependency prerequisites are unavailable.
- Completion removes/replaces the placeholder with patch preview.
- Failure, cancellation, lease loss, and retry clear loading state while preserving canvas edits.
- Reconnecting to SSE reconstructs the correct current progress state.

#### PG-025 — Verify patch review and regeneration end to end

**Depends on:** PG-022, PG-023, PG-024

**Outcome:** Prove the graph provides provenance, branching, invalidation, and review behavior that a chat transcript cannot.

**Done when:**

- End-to-end tests cover critique-to-preview-to-transactional-apply.
- Editing or deleting an input visibly marks every dependent descendant stale.
- A user can compare parallel branches and replace an assumption through audited graph operations.
- Explicit branch regeneration leaves unrelated graph branches unchanged.
- Evidence rejection remains auditable, excludes rejected evidence from later context, marks dependent descendants stale, and preserves independently supported claims.
- Patch conflict recovery, per-operation decision audit, partial acceptance, cancellation, and reload preserve authoritative state.
- The canonical journey satisfies MVP acceptance criteria 1–14 locally.

### Phase 5 — Demo hardening, evaluation, and delivery

#### PG-026 — Deliver the seeded judge-facing demo experience

**Depends on:** PG-025, DQ-008

**Outcome:** Make the canonical workflow reliable without private setup.

**Done when:**

- Demo opportunity decision **DQ-008** is resolved and represented by a seeded canonical canvas and immutable fixture bundle.
- `demo_hybrid_v1` is the primary judge-facing profile and `replay_v1` is an explicit emergency fallback.
- A one-click reset restores the known starting state.
- Cached evidence is labeled as previously retrieved and visually distinguished from live GPT-5.6 reasoning.
- The flow requires no account or uses trivial judge credentials with server-managed secrets.

#### PG-027 — Build the comparative evaluation harness

**Depends on:** PG-019, PG-020, DQ-006

**Outcome:** Measure whether orchestration materially beats generic brainstorming.

**Done when:**

- The harness contains 15–25 builder scenarios and compares generic, strategy-only, strategy-plus-evidence, and full-pipeline variants.
- Blind scoring covers specificity, evidence relevance, novelty, feasibility, economic leverage, testability, and builder fit.
- Results report each dimension separately with reproducible model/prompt/strategy versions.
- The full pipeline materially outperforms the generic baseline on evidence relevance, specificity, testability, and builder fit.
- Evaluation placement decision **DQ-006** determines whether the harness remains internal or ships in the UI.

#### PG-028 — Aggregate production observability and verify security hardening

**Depends on:** PG-025

**Outcome:** Prove the component-owned instrumentation is complete and usable, and verify that trust boundaries already enforced by ingestion, extraction, synthesis, and rendering remain effective in the public demo.

**Done when:**

- Structured logs emitted by their owning run, worker, provider, cache, and patch components include all identifiers, lease data, versions, durations, token usage, counts, and errors listed in design section 25.1.
- Existing component metrics are aggregated into queue, stage, failure/retry, lease, provider, patch, and evidence-quality views; this task does not defer first-time instrumentation from earlier phases.
- An end-to-end diagnostic drill traces one successful run, one retryable provider failure, one lease loss, and one patch conflict through correlated logs, metrics, events, and audit records.
- Audit records preserve prompts/strategies/models, context, sources, claims, candidates, critiques, accepted/rejected operations, and user edits.
- End-to-end security tests verify sanitized rendering, server-side secrets, URL/redirect defenses, user-text isolation, prompt-injection resistance, and intellectual-property output restrictions.
- No third-party or user-supplied content can alter system instructions or trigger command execution.

#### PG-029 — Deploy the public web, worker, and PostgreSQL runtime

**Depends on:** PG-026, PG-028

**Outcome:** Provide a stable public demo using only the three approved runtime components.

**Done when:**

- HTTPS and HTTP/2 are enabled, SSE proxy buffering is disabled, and idle timeouts permit heartbeat delivery.
- Web and worker processes use restart policies and bounded database connection settings.
- Environment-based OpenAI and provider credentials are server-managed.
- The seeded demo, reset, hybrid profile, and replay fallback work in the deployed environment.
- A production smoke test completes the canonical workflow and verifies incremental SSE delivery.

#### PG-030 — Complete README, setup, testing, and hackathon compliance

**Depends on:** PG-027, PG-029, DQ-007

**Outcome:** Make the repository understandable, runnable, testable, and submission-ready.

**Done when:**

- Product-name decision **DQ-007** is applied consistently to the repository, UI, and submission copy.
- README explains the product, architecture, setup, ASGI/SSE verification, tests, execution profiles, judge path, and known limitations.
- README explains how Codex and GPT-5.6 contributed and records the required `/feedback` Codex Session ID.
- Repository URL, Work & Productivity positioning, public testing path, and under-three-minute YouTube requirements are covered.
- Instructions are verified from a clean environment.

#### PG-031 — Run final acceptance and prepare the three-minute demo

**Depends on:** PG-027, PG-030

**Outcome:** Prove every MVP acceptance criterion and communicate the product clearly within the judging limit.

**Done when:**

- All unit, integration, end-to-end, security, concurrency, resume, and deployment smoke checks pass.
- A first-time user completes all 16 MVP acceptance criteria on the public instance.
- The video script explains the problem, graph-native differentiation, evidence/provenance, Codex usage, GPT-5.6 usage, and judge test path in under three minutes.
- Replay fallback and failure-recovery procedures are rehearsed.
- Final submission artifacts and links are checked against the official rules.

## In Progress

None.

## Done

#### DQ-001 — Choose progressive or post-extraction evidence display

**Completed:** July 14, 2026

**Resolution:** Display each schema-valid extraction batch progressively as provisional evidence. It becomes authoritative and selectable only after the user accepts the evidence patch. Recorded in `design.md` sections 14 and 32.

## Notes

- Follow the phase order unless `design.md` or an explicit user instruction changes it.
- Preserve the frozen architecture: Django, PostgreSQL-only state, separate worker, PostgreSQL queue, canvas-sequenced SSE, fenced leases, short transactions, idempotent direct graph operations, independent semantic/position versions, operation-specific runs with explicit safe retry, explicit evidence selection, candidate graph patches with dependency-closed local-ID mapping and per-operation decisions, deterministic context packing and evidence clustering, direction-aware cycle-safe cascade invalidation, full-stage replay fixtures, resumable checkpoints, bounded workers, and typed execution profiles.
- Do not add MVP non-goals such as multi-user collaboration, mobile-first editing, arbitrary ontologies, billing, Redis, Celery, Neo4j, or WebSockets.
- Open questions are decision checkpoints, not permission to diverge from frozen architectural decisions.
