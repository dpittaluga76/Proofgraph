# Project Task Tracker

This backlog is derived from `design.md`, which is the source of truth. Tasks are ordered by dependency and implementation phase. When a task is completed, move its full entry to **Done**.

## Current Goal

Continue **Phase 5: Demo hardening, evaluation, and delivery** with public deployment.

Current phase entry criteria:

- Phase 4 patch review, transactional application, dependency-aware invalidation, and always-parallel regeneration are complete.
- The local PostgreSQL, backend, frontend, and browser verification gates pass.
- PG-026 delivers the isolated, resettable, quota-protected anonymous demo locally.
- PG-027 proves the comparative evaluation satisfies the pre-registered V2 acceptance rule.
- PG-028 aggregates production telemetry and verifies the public-demo security boundaries.

Current implementation task: **PG-029**. PG-028 is complete after its correlated diagnostic drill, audit inspection, security regression suite, and full repository gates passed. DQ-007 is also resolved: the final product and submission name is **ProofGraph**.

## Pending

### Phase 5 — Demo hardening, evaluation, and delivery

#### PG-029 — Deploy the public web, worker, and PostgreSQL runtime

**Depends on:** PG-026, PG-028

**Outcome:** Provide a stable public demo using only the three approved runtime components.

**Done when:**

- HTTPS and HTTP/2 are enabled, SSE proxy buffering is disabled, and idle timeouts permit heartbeat delivery.
- Web and worker processes use restart policies and bounded database connection settings.
- Environment-based OpenAI and provider credentials are server-managed.
- The seeded demo, reset, hybrid profile, and replay fallback work in the deployed environment.
- Secure session cookies, CSRF, per-session/global PostgreSQL quotas, concurrent-run limits, and the anonymous profile allowlist remain effective across multiple web/worker processes.
- A production smoke test completes the canonical workflow and verifies incremental SSE delivery.

#### PG-030 — Complete README, setup, testing, and hackathon compliance

**Depends on:** PG-027, PG-029, DQ-007

**Outcome:** Make the repository understandable, runnable, testable, and submission-ready.

**Done when:**

- Product-name decision **DQ-007** is applied consistently to the repository, UI, and submission copy.
- README explains the product, architecture, setup, ASGI/SSE verification, tests, execution profiles, source-ingestion limits, public-demo quotas/profile restrictions, replay fallback, judge path, and known limitations.
- README explains how Codex and GPT-5.6 contributed and records the Session ID returned by running `/feedback` near the end in the Codex Project task where the majority of core functionality was built; an unrelated planning/review task ID does not satisfy this criterion.
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
- Final submission artifacts and links, including the primary implementation Project task's `/feedback` Session ID, are checked against the official rules.

## Done

### PG-028 — Aggregate production observability and verify security hardening

**Completed:** July 20, 2026

**Depends on:** PG-025, PG-026

**Outcome:** Proved the component-owned instrumentation is complete and usable, and verified that trust boundaries enforced by ingestion, extraction, synthesis, and rendering remain effective in the public demo.

**Completion:** A common structured-telemetry boundary now stamps every record with a UTC timestamp and component, normalizes identifiers, and recursively redacts credentials, authorization values, cookies, lease tokens, and sensitive URL query values. Run, queue, stage, provider, research, cache, patch, graph-operation, source-ingestion, cancellation/attempt terminalization, regeneration, and demo lifecycle emitters retain their applicable correlation, lease, attempt, operation-key, version, duration, response, token, count, cache, and error dimensions. `observability_report` aggregates these records into operational views, reports missing required fields, optionally joins them to durable PostgreSQL audit state, and fails a strict four-scenario diagnostic drill when any correlation is absent.

The drill uses real component emissions and durable state for a successful run, retryable provider timeout, fenced lease loss, and immutable patch conflict. Audit inspection covers frozen prompt/strategy/provider-model/fixture versions, packed context, source and claim checkpoints, candidates, critiques, patch candidates and decisions, and direct user edits. The security gate combines endpoint-wide anonymous ownership and CSRF checks, server-side secret non-disclosure, URL/redirect/private-network defenses, derived-only source retention, isolated untrusted model inputs, adversarial IP-policy validation, inert command-like user text, and React text escaping.

**Done when:**

- Structured logs emitted by their owning run, worker, provider, cache, and patch components include all identifiers, lease data, versions, durations, token usage, counts, and errors listed in design section 25.1.
- Existing component metrics are aggregated into queue, stage, failure/retry, lease, provider, patch, and evidence-quality views; this task does not defer first-time instrumentation from earlier phases.
- Aggregation includes attempt/cancellation terminalization, source-ingestion reclaim, stale and pending-patch regeneration, demo session creation/expiry/cleanup, reset/profile rejection, quota rejection, circuit-breaker, and replay-switch telemetry emitted by their owning tasks.
- An end-to-end diagnostic drill traces one successful run, one retryable provider failure, one lease loss, and one patch conflict through correlated logs, metrics, events, and audit records.
- Audit records preserve prompts/strategies/models, context, sources, claims, candidates, critiques, accepted/rejected operations, and user edits.
- End-to-end security tests verify endpoint-wide anonymous resource authorization, sanitized rendering, server-side secrets, URL/redirect defenses, user-text isolation, prompt-injection resistance, and intellectual-property output restrictions.
- No third-party or user-supplied content can alter system instructions or trigger command execution.

**Verification:** All 284 PostgreSQL-backed backend tests and 30 frontend unit/component tests pass. Ruff lint and formatting, Django checks, migration-drift detection, and the production frontend build pass. The focused observability and security regressions pass, including required-field validation on the real diagnostic telemetry and persisted audit coverage for every reasoning and user-decision category.

### DQ-007 — Select the final product name

**Completed:** July 20, 2026

**Depends on:** None

**Outcome:** Selected **ProofGraph** as the final product and submission name.

**Done when:** Naming, repository/UI migration scope, and submission usage are recorded for PG-030.

**Resolution:** `design.md` section 32.1 records the exact public capitalization and migration scope. Product UI, documentation, demo/video copy, and submission materials use **ProofGraph**. The public repository may use the lowercase `proofgraph` slug, and compatibility-sensitive technical identifiers remain lowercase `proofgraph`; PG-030 applies and audits the user-visible branding without an internal package, database, cookie, local-storage, or telemetry migration.

### PG-027 — Build the comparative evaluation harness

**Completed:** July 20, 2026

**Depends on:** PG-019, PG-020, DQ-006

**Outcome:** Measured whether orchestration materially beats generic brainstorming and obtained a passing fresh post-registration V2 result.

**Completion:** The internal deterministic harness contains 20 versioned synthetic scenarios, four frozen GPT-5.6-family structured-output paths, an explicit model allowlist, resumable six-worker generation, deterministic per-scenario blinding, two independent automated model judges, arithmetic-mean scoring, disagreement telemetry, and 10,000-resample paired bootstrap reporting. The frozen V1 result remains an authoritative failure because builder-fit lift was `+0.350`, below its `+0.500` relative threshold. The pre-registered V2 correction retained the relative gates for evidence relevance, specificity, and testability while changing builder fit to an absolute `4.500` full-pipeline floor plus a non-negative paired confidence-interval lower bound.

The official fresh V2 run used Terra generation with seed `28001`, packet seed `28002`, Vera Crosscheck on Sol and Marco Launch on Luna with judge seed `28003`, and the distinct ignored `eval-terra-v2` directory. It completed 80 normalized outputs with no partials and materialized 80 ratings per judge. The schema-v3 `comparative_acceptance_v2` result passed every required dimension:

| Required dimension | Full-pipeline mean | Mean full − generic | 95% bootstrap CI | Result |
| --- | ---: | ---: | --- | --- |
| Evidence relevance | `5.000` | `+2.950` | `[2.675, 3.225]` | Pass |
| Specificity | `5.000` | `+0.825` | `[0.675, 0.950]` | Pass |
| Testability | `5.000` | `+1.450` | `[1.250, 1.650]` | Pass |
| Builder fit | `5.000` | `+0.450` | `[0.250, 0.650]` | Pass |

**Done when:**

- The harness contains at least twenty builder scenarios and compares generic, strategy-only, strategy-plus-evidence, and full-pipeline variants.
- Variant labels/order are randomized; Vera Crosscheck and Marco Launch independently score every opaque output on the fixed five-point rubric for specificity, evidence relevance, novelty, feasibility, economic leverage, testability, and builder fit.
- Effective scores are the arithmetic mean of both model judges; absolute two-point disagreements, both original scores, and both rationales are retained and reported without adjudication.
- Results report each dimension separately with reproducible model/prompt/strategy versions.
- Under explicit `comparative_acceptance_v2`, evidence relevance, specificity, and testability each show at least `+0.5` mean points over generic with a 95% paired-bootstrap confidence-interval lower bound above zero; builder fit has a full-pipeline mean of at least `4.5` and a paired full-minus-generic confidence-interval lower bound of at least zero.
- The authoritative V1 failure remains unchanged, and the passing V2 result uses fresh post-registration generation and judge artifacts rather than reclassifying V1.
- Evaluation placement decision **DQ-006** determines whether the harness remains internal or ships in the UI.

**Verification:** The V2 result reports schema `3`, `comparative_acceptance_v2`, and overall PASS. All required artifacts exist after the pre-registration commit; generation has 80 outputs and zero partials; each judge artifact has 80 ratings; all four required dimensions pass; and only one of 560 score comparisons differs by at least two points. The complete evaluation artifacts remain private under ignored `evaluation/runs/`, while only this aggregate summary is checked in. All 17 focused evaluation tests, Ruff checks, PowerShell parsing, runner dry-run, V1-directory protection, and manifestless-artifact rejection pass without additional provider calls.

### DQ-006 — Keep the evaluation harness internal

**Completed:** July 15, 2026

**Depends on:** None

**Outcome:** The benchmark is a command-line, Git-reviewable workflow for maintainers, two automated blinded model judges, and submission reviewers. It adds no product UI and exposes no variant identities, judge-run metadata, evaluation credentials, or raw provider metadata to anonymous demo visitors. Versioned synthetic scenarios and summarized results may live in the repository; private generation, variant-map, and judge-checkpoint artifacts remain separate from blind inputs and published summaries.

**Done when:** `design.md` section 23.6 and decision DQ-006 record the audience, data exposure, zero product-UI scope, four frozen variants, deterministic artifact split, automated-judge mean and disagreement semantics, cost-bearing generation and judging boundaries, and unchanged numerical acceptance criteria for PG-027.

### PG-026 — Deliver the seeded judge-facing demo experience

**Completed:** July 15, 2026

**Depends on:** PG-025, DQ-008

**Outcome:** Made the canonical workflow locally reliable without an account or private browser setup. Public mode bootstraps each visitor into an isolated canonical seed, defaults to hybrid reasoning, exposes deterministic replay explicitly, and restores the seed with one reset action.

**Done when:**

- Demo opportunity decision **DQ-008** is resolved and represented by a seeded canonical canvas and immutable fixture bundle.
- `demo_hybrid_v1` is the primary judge-facing profile and `replay_v1` is an explicit emergency fallback.
- A one-click reset restores the known starting state.
- Migrations create PostgreSQL-backed `demo_session` and global quota-window state, enforce unique active-canvas ownership, link demo runs to their session independently of reset, and add active-run/session-expiry indexes from design section 12.14; the browser receives a signed HttpOnly SameSite cookie and mutating requests retain Django CSRF protection.
- Every anonymous visitor receives an isolated clone of the seeded canvas, and reset replaces only that session's active clone under concurrent requests.
- Sessions expire 24 hours after creation; reset neither extends expiry nor resets quota, cookie expiry matches server expiry, bootstrap GET creates a new isolated session after expiry, and expired API requests return `demo_session_expired` without performing work.
- A bounded `SKIP LOCKED` cleanup path cancels nonterminal work, respects lease fencing, deletes expired canvas data through DQ-003, and removes the session only after its runs are terminal or fenced.
- Every canvas, graph-operation, run status/cancel/retry, SSE, patch get/apply/reject/regenerate, source, and ingestion endpoint resolves the signed session and returns non-enumerating `404` for cross-session or retired-canvas resources.
- Anonymous requests may select only `demo_hybrid_v1` or `replay_v1`; `live_v1` and unregistered profiles are rejected server-side.
- Hybrid usage is limited to twelve runs per session per one-hour window, at most two concurrent runs, and 120 global runs per one-hour window; counters update atomically before queueing.
- Quota exhaustion returns `429` and offers an explicit replay switch without silently changing the stored execution profile.
- Cached evidence is labeled as previously retrieved and visually distinguished from live GPT-5.6 reasoning.
- The flow requires no account; all provider credentials remain server-managed.
- This component emits demo-session creation/expiry/cleanup, reset, profile rejection, per-session/global quota rejection, circuit-breaker, and replay-switch telemetry with session/run/canvas/profile identifiers.
- Tests cover session forgery, cross-session read and mutation denial for every resource family, expired-cookie bootstrap versus API behavior, concurrent cleanup/lease fencing, CSRF, concurrent quota races, profile allowlisting, global circuit breaking, unique canvas ownership, and reset without expiry or quota evasion.

**Verification:** Migrations apply with no model drift; Django checks, Ruff formatting/lint, and the production frontend build pass. All 260 PostgreSQL-backed backend tests and 29 frontend unit/component tests pass. Three live Playwright journeys pass, including the anonymous seed/bootstrap/reset path, explicit hybrid-to-replay selection, retired-canvas denial, durable Phase 4 invalidation, and the Phase 1 graph journey. Representative query plans use the demo active-run and expiry indexes without sequential scans. The live browser gate found and fixed duplicate session creation under React Strict Mode by coalescing concurrent bootstrap requests.

### PG-025 — Verify patch review and regeneration end to end

**Completed:** July 15, 2026

**Depends on:** PG-022, PG-023, PG-024

**Outcome:** Prove the graph provides provenance, branching, invalidation, and review behavior that a chat transcript cannot.

**Done when:**

- End-to-end tests cover critique-to-preview-to-transactional-apply.
- Opportunity preview and applied-node inspection show every required quality dimension separately plus distribution and defensibility rationale.
- Editing or deleting an input visibly marks every dependent descendant stale.
- A user can compare parallel branches and replace an assumption through audited graph operations.
- Explicit composite branch regeneration produces one patch, resumes from completed batch checkpoints after failure, leaves unrelated branches unchanged, and preserves all old-branch stale causes while creating fresh linked successors.
- Evidence rejection remains auditable, excludes rejected evidence from later context, marks dependent descendants stale, and preserves independently supported claims.
- Patch conflict recovery, per-operation decision audit, partial acceptance, cancellation, and reload preserve authoritative state.
- The canonical journey satisfies MVP acceptance criteria 1–14 locally.

**Verification:** PostgreSQL migrations, migration-drift detection, and database readiness pass. Ruff formatting and lint pass; all 248 backend tests pass; the frontend formatting, lint, source/e2e typecheck, 26 unit/component tests, and production build pass; and both live PostgreSQL-backed Playwright journeys pass. The Phase 4 browser journey verifies durable visible invalidation across a four-level dependency chain and reload, while backend replay, checkpoint-resume, cancellation, conflict, evidence-rejection, transactional-apply, lineage, and SSE tests cover MVP criteria 1–14. The gate also found and fixed PostgreSQL JSON-key filtering that had excluded manually created descendants without a `review_status` key from invalidation.

### Phase 4 dependency-aware regeneration and progress UX — PG-023 and PG-024

**Completed:** July 15, 2026

**Depends on:** PG-009, PG-020, PG-021, PG-022, DQ-002, DQ-004, DQ-005

**Outcome:** Added durable transitive invalidation, explicit node/branch regeneration, always-parallel successor lineage, cloned branch constraints, rejected-evidence and assumption-replacement audit actions, resilient canvas-wide progress replay, provisional evidence overlays, pending patch handoff, and retained-branch comparison.

**Done when:**

- Every semantic node/edge mutation uses the explicit dependency-direction table, preserves operation-linked causes, increments only the first fresh-to-stale transition, and never starts generation automatically.
- Evidence rejection recalculates support atomically, preserves independently supported claims, excludes rejected material from later context, and invalidates all dependent descendants including direct source-to-opportunity dependencies.
- Node and branch regeneration freeze target-local worksets, resume checkpointed batches, emit exactly one successor per production root, and produce one add-only candidate patch.
- Applying regeneration follows DQ-004: old stale nodes and active causes remain untouched; successors carry canonical parallel metadata and old-to-new lineage; anchored branch constraints are cloned; partial review keeps each lineage group atomic.
- Unsupported, manually authored, source, placeholder, fresh, or otherwise ineligible regeneration selections return `422`.
- One replayable SSE stream per canvas reconstructs run progress, shows clearly provisional run-owned evidence, opens only current pending previews, and clears loading on completion, failure, cancellation, lease loss, or retry.
- Rejected evidence stays visible and accessible; audited rejection, assumption replacement, regeneration, and canonical retained-branch comparison expose prerequisite-aware disabled states.

**Verification:** Initial focused verification covered backend compilation, strict pipeline/provider contracts, fixture integrity, and the complete frontend gate. The later unrestricted PostgreSQL, Ruff, full-suite, and Playwright phase-exit results are recorded under completed PG-025.

### Phase 4 design decisions — DQ-002 and DQ-004

**Completed:** July 15, 2026

**Outcome:** Selected accessible muted-visible rejected evidence and always-parallel stale regeneration. `design.md` now fixes presentation, provenance, undo/audit, branch comparison, constraint cloning, patch dependency, and old-cause preservation semantics for PG-023 and PG-024.

### Phase 4 patch review and transactional apply — PG-021 and PG-022

**Completed:** July 15, 2026

**Outcome:** Added an immutable graph-patch review surface and PostgreSQL-transactional application workflow. Users can inspect exact candidates, provenance, assumptions, risks, contradictions, dependencies, and all six opportunity-quality dimensions; accept all or a dependency-closed subset; apply only nonconflicting operations; reject all; or request one idempotently linked regeneration run. Accepted candidates materialize through deterministic patch-local identities and operation keys with append-only graph-operation audit records and an explicit decision for every reviewed operation.

**Verification:** Focused API tests cover review serialization, rejection without graph mutation, regeneration revalidation/profile conflicts/lineage/idempotency/terminal telemetry, full and partial apply, strict rollback, nonconflicting dependency skips, node-deletion prerequisites, deterministic identity replay, and a concurrent dependency writer. UI tests cover separate quality-dimension rendering, visible distribution/defensibility rationale, dependency-safe selection, and nonconflicting apply with canvas refresh. The complete backend suite passes (221 tests); migration drift, Django, and PostgreSQL readiness checks pass; Ruff formatting and lint pass; the frontend formatting/lint/typecheck/unit/build gate passes (16 unit tests); and the live PostgreSQL-backed Playwright journey passes.

### Phase 3 final handoff remediation — PG-016 through PG-020

**Completed:** July 15, 2026

**Outcome:** Closed the remaining Phase 3 trust-boundary gaps before Phase 4: deterministic prohibited-intent handling without guardrail false positives, subject-bound first-party authority classification, database-enforced immutability for candidate patch inputs, legacy regeneration-contract backfill, and a durable reviewed Git baseline.

**Verification:** Adversarial policy tests cover behavioral reconstruction, confidential/private data, third-party terms, negated guardrails, monitoring, and descriptive compliance risks. Authority tests cover brandless vendor surfaces, generic analyst commentary, misleading subdomains, public publisher brands, GitHub discussions, and public-suffix identity. Migration `generation.0006_protect_graph_patch_contract` backfills compatible regeneration declarations, safely rejects incompatible pending patches, and installs PostgreSQL JSON-shape constraints plus an immutable-candidate trigger while preserving Phase 4 lifecycle updates. The complete backend suite passes (207 tests); Ruff formatting and lint, migration drift, and database readiness checks pass; the frontend formatting/lint/typecheck/unit/build gate passes (14 unit tests); the live PostgreSQL-backed Playwright journey passes; and the reviewed repository scope excludes credentials and generated output.

### Phase 3 second-review remediation — PG-013, PG-016 through PG-020

**Completed:** July 15, 2026

**Outcome:** Closed the ordered Phase 3 review findings: reusable source-content caching, exact research/strategy and patch-lineage binding, clause-aware intellectual-property safeguards, opportunity-family replay fidelity, persisted mode-neutral regeneration targets and permitted stale resolutions, audit-complete extraction, immutable fixture identity, PostgreSQL-time ingestion leases, publisher-aware source-authority and independence classification, versioned mechanism tags, and cache-invalidation telemetry.

**Verification:** Adversarial policy tests cover prohibited paraphrases, explicit guardrails, and descriptive risks. Publisher/subdomain, public-suffix, authority, mirror-normalization, and durable regeneration-contract tests pass. Migration `generation.0005_graphpatch_regeneration_contract` applies to PostgreSQL with no model drift. The complete backend suite passes (197 tests), including replay, concurrency, cache/index-plan, and persistence coverage; Ruff formatting and lint pass; the frontend formatting/lint/typecheck/unit/build gate passes (14 unit tests); the live PostgreSQL-backed Playwright journey passes; and `git diff --check` reports no whitespace errors.

### Phase 3 review hardening — PG-013, PG-016 through PG-020

**Completed:** July 15, 2026

**Outcome:** Closed all ten architecture-review findings without changing the Phase 3 boundary. Candidate patches are now exact materializations of validated checkpoints; graph lineage uses the canonical dependency directions; branch synthesis can consume prior extraction batches; the research budget is enforced across the complete run; support and independent-source counts derive from authoritative claim-source relations; progress is durably emitted while a stage is still running; expired caches are physically deleted by the worker; replay identity is strictly versioned; and intellectual-property policy enforcement catches prohibited semantic copying rather than only exact phrases.

**Verification:** Added focused regressions for patch cardinality and checkpoint binding, lineage direction, composite-branch data flow, global research budgets, signal spoofing, independent-source ranking, strict fixture identity, incremental durable events, worker cache cleanup, and paraphrased proprietary-copying requests. The complete backend suite (176 tests), frontend formatting/lint/typecheck/unit/build gate, PostgreSQL checks, migration drift check, full Ruff gate, Playwright journey, and `git diff --check` pass.

### Phase 3 — Intelligence pipeline

#### PG-020 — Integrate and verify the intelligence pipeline

**Completed:** July 14, 2026

**Depends on:** PG-013, PG-018, PG-019

**Outcome:** Integrated strict production context, profile resolution, and contextual stage validation through the Phase 2 durable ports. Live, hybrid, and replay profiles now share one orchestrator/event contract; complete replay covers generation, research, synthesis, every stale production-unit kind, and composite stale branches without live provider access. Review hardening binds synthesis evidence to selected claim-source relations, normalizes opportunity-family worksets, enforces operation-local patch lineage/metadata, validates the complete outbound request budget, and correlates provider telemetry with its run and lease.

**Phase boundary:** Phase 3 produces immutable candidate evidence patches but does not apply them. Patch review and authoritative application are delivered by PG-022 and PG-023 in Phase 4; Phase 3 synthesis accepts only evidence that is already authoritative in the frozen graph (for example, seeded fixtures or a patch applied by that later workflow).

**Done when:**

- A user can generate three materially different strategies from a goal and builder constraints.
- A `research_evidence` run produces the selected strategy, research questions, query plan, required evidence types, progressively inspectable provisional evidence, deterministic clusters, and a reviewable evidence patch.
- `synthesize_opportunities` requires an explicitly selected applied strategy and applied, non-stale, non-rejected claims; selected IDs/expected versions are captured and their accepted source provenance is included automatically. Applying the preceding evidence patch is explicitly a Phase 4 responsibility.
- Operation tests cover every allowed and rejected node-kind/cardinality/review-state combination, global and anchored branch constraints, claim-only synthesis selection with automatic source provenance, every target-localized stale-node plan, and composite branch worksets containing strategy, evidence, and opportunity-family targets.
- The synthesis run produces exactly three structured opportunities and never consumes provisional, rejected, unselected, or stale evidence.
- Every opportunity traces to supporting and contradicting claims.
- Live, hybrid-demo, and replay profiles use the same orchestrator and event contract.
- The production composition root registers `RunContextFactory`, `ExecutionProfileResolver`, and `StageOutputValidator`; only then are `live_v1`, `demo_hybrid_v1`, and `replay_v1` enabled for product requests, while the deterministic Phase 2 test profile remains unreachable.
- Tests cover every operation stage plan, the evidence-selection gate, timeouts, invalid structured output, rate limits, no-results, and unsupported-opportunity paths with user-readable errors.

#### PG-019 — Create strict versioned fixture bundles

**Completed:** July 14, 2026

**Depends on:** PG-014, PG-016, PG-017, PG-018, DQ-003, DQ-008

**Outcome:** Added the immutable `security_questionnaires_v1` bundle with exact normalized semantic-input matching, shared production validation, sanitized progress events, strict mismatch failure, hybrid fixture stages, and full provider-free replay across all operation and regeneration plans.

**Done when:**

- A canonical fixture directory contains a manifest, sources, claims, planning outputs, synthesis outputs, critique outputs, patch-construction outputs, and progress-event payloads.
- Fixture payloads pass through the same validation as live provider outputs.
- Matching includes scenario, stage, pipeline version, provider identity, semantic input hash, and fixture version.
- A mismatch fails with a recoverable fixture-input error and never silently falls back to live APIs.
- Fixture providers emit the same persisted domain events as live providers.
- Manifest cases cover planning, research, extraction, synthesis, critique, and patch construction for every node plan and composite branch-phase combination; full replay reaches one `patch.ready` without any live provider call.
- Fixture content and deletion behavior implement the retained-content policy selected in **DQ-003**.

#### PG-018 — Construct candidate graph patches without direct graph writes

**Completed:** July 14, 2026

**Depends on:** PG-017

**Outcome:** Added strict dependency-aware candidate-patch construction with versioned preconditions, local-ID resolution, delete prerequisites, regeneration workset fencing, provenance validation, and separate patch-ready/final completion transactions without authoritative graph mutation.

**Done when:**

- Patch construction emits only supported localized operation types and records `base_canvas_revision`.
- Semantic update/delete operations include `expected_version`; `MOVE_NODE` includes `expected_position_version`; edge updates/deletes include the edge `expected_version`.
- Every newly proposed entity has a patch-local `client_generated_id`; IDs are unique within the patch and references may use only known server IDs or those local IDs.
- The patch records an operation dependency graph, and schema validation rejects cycles, unresolved references, and subsets whose required prerequisites are absent.
- A candidate `DELETE_NODE` includes accepted prerequisite operations for every incident edge and branch-root constraint reference known at construction time; otherwise validation rejects the patch.
- Regeneration patches declare their frozen target production units and the exact stale node IDs they may resolve; schema validation rejects targets outside the originating run workset.
- New nodes and edges preserve traceable provenance from sources and claims through opportunities, risks, assumptions, and experiments.
- Pipeline completion stores a candidate `GraphPatch`; it never mutates authoritative graph state.
- Patch schema validation rejects missing endpoints, invalid kinds, and malformed metadata.
- The default budget permits one patch-construction pass; retry reuses a matching completed checkpoint.

#### PG-017 — Implement opportunity synthesis and critique

**Completed:** July 14, 2026

**Depends on:** PG-013, PG-014, PG-016

**Outcome:** Added exact three-candidate synthesis with separate decision dimensions, structured support thresholds, contradiction/gap requirements, one checkpointed critique pass per candidate, and intellectual-property/third-party-terms enforcement.

**Done when:**

- Synthesis generates exactly three structured candidates from the selected strategy, constraints, and retained evidence.
- Every candidate carries explicit distribution channel/rationale, defensibility rationale, technical feasibility, and operational burden fields.
- Critique evaluates novelty, feasibility, buyer/budget, recurrence, distribution, operational burden, differentiation, builder fit, and falsifying evidence.
- Each candidate exposes evidence strength, novelty, builder fit, technical feasibility, distribution clarity, and operational burden separately rather than one synthetic score.
- `supported` is assigned only when every design section 7.3 threshold is met; otherwise the candidate is `speculative`.
- Every candidate includes material contradicting evidence or an explicit evidence gap.
- Synthesis and critique enforce the intellectual-property boundaries in design section 21.3 and reject recommendations to copy protected code/assets, impersonate trademarks, reuse private datasets, or violate third-party terms.
- The default budget permits one critique pass; retry reuses checkpoints rather than silently adding extra critique calls.

#### PG-016 — Implement claim extraction, evidence clustering, and source caching

**Completed:** July 14, 2026

**Depends on:** PG-015

**Outcome:** Implemented strict source-backed extraction, deterministic twelve-claim retention and exact evidence clustering, progressive provisional evidence events, and split canvas-scoped query/content caches with freshness, expiry, and retained-content enforcement.

**Done when:**

- One source can back multiple claims and one claim can reference multiple independent sources.
- Claims record classification, evidence type, strength, limitations, and source IDs.
- Claims record canonical `topic_keys`, `mechanism_tags`, `contradiction_target_key`, and source IDs; sources record canonical `independence_key` values used by deterministic retention and support counting.
- Duplicate, irrelevant, unsupported, and contradicting evidence are handled explicitly.
- At most twelve claims are retained using the source hierarchy, source authority, independent-source count, strength, recency, and stable-ID tie-breaking.
- A deterministic `clustering` checkpoint groups claims by the exact tuple of evidence type, sorted topic keys, sorted mechanism tags, and normalized contradiction target without losing source provenance; independent support counts distinct accepted source `independence_key` values.
- Migrations create separate canvas-scoped `research_query_cache` and `source_content_cache` tables, their exact-key/freshness indexes, and DQ-003 deletion/redaction behavior from design section 12.11.
- Query-cache lookup uses only known pre-request fields; source-content lookup uses normalized URL plus freshness before retrieval and persists content hash as immutable identity afterward. Repeat-hit, expiry, changed-hash, changed-key, and deletion tests prove reuse and invalidation behavior.
- Cached results retain original retrieval metadata and a cache-hit marker so they cannot be presented as newly retrieved.
- Telemetry records extraction/retention counts, cluster counts, independent-source counts, cache hits/misses, and invalidation reasons.
- Each schema-valid extraction batch emits progressive provisional evidence; no source or claim becomes authoritative or selectable until its research patch is accepted.
- Rendered excerpts are sanitized before they reach the browser.
- Rejected sources and claims follow design section 8.2.1 and are excluded from context, quality thresholds, synthesis, and critique.
- Tests prove clustering is stable under input reordering, contradiction targets split clusters correctly, multiple independent source keys count separately, and syndicated copies sharing a source `independence_key` count only once.

#### PG-015 — Implement secure research adapters

**Completed:** July 14, 2026

**Outcome:** Added bounded OpenAI hosted-search, GitHub, Stack Exchange, HTTPS URL, and user-text adapters with structured rate-limit/telemetry behavior. User URL ingestion now uses a same-canvas fenced reservation, performs all retrieval outside transactions, pins validated public DNS addresses across redirects, enforces all design limits, stores only citations/hashes/sanitized excerpts, exposes idempotent source APIs, and passes concurrency, reclaim, SSRF, decompression, retention, lifecycle, and index-plan tests.

#### PG-014 — Implement provider ports and execution profiles

**Completed:** July 14, 2026

**Outcome:** Added distinct typed ports for all six provider-backed stages, one shared deterministic-clustering port, immutable registered profile configurations, and a profile-driven executor with no demo-mode branch. The approved live, hybrid, and replay compositions route exactly as designed; product resolution rejects unknown and Phase 2 test profiles, and frozen provider identity remains part of checkpoint hashing.

#### PG-013 — Build deterministic operation-specific context selection

**Completed:** July 14, 2026

**Outcome:** Replaced full-canvas snapshots with cycle-safe, operation-specific dependency traversal and deterministic tier packing. Context construction now reserves contradictory evidence first, honors pinned branch anchors, caches canonical semantic token upper bounds, excludes layout/UI state, records included and excluded nodes/edges, and rejects mandatory or fully serialized overflow with `422 context_too_large`.

#### PG-012 — Define strict pipeline domain schemas and strategy templates

**Completed:** July 14, 2026

**Outcome:** Added the versioned 14-strategy catalog and strict, production stage validators for planning, research, extraction, clustering, synthesis, critique, and dependency-aware graph-patch output. Canonical claim/source relations, separate opportunity dimensions, structured support thresholds, IP checks, and malformed-output rejection are covered by focused tests.

#### DQ-008 — Select the canonical demo opportunity

**Completed:** July 14, 2026

**Outcome:** Selected the security-questionnaire workflow and recorded its inputs, evidence expectations, opportunity shape, reset state, and immutable `security_questionnaires_v1` fixture identifier in `design.md` section 32.1.

#### DQ-005 — Decide explicit-neighborhood versus semantic-similarity context

**Completed:** July 14, 2026

**Outcome:** Selected deterministic explicit graph neighborhoods for the MVP, with no embedding fallback, and recorded ranking, cost, fallback, and test implications in `design.md` section 32.1.

### Phase 2 — Durable jobs and progress

#### PG-011 — Verify the durable-job architecture end to end

**Completed:** July 14, 2026

**Depends on:** PG-008, PG-009, PG-010

**Outcome:** Demonstrate reliable job execution under concurrency and interruption before adding live intelligence.

**Done when:**

- A deterministic test-double job progresses from queued to completed and emits replayable stage events without depending on the Phase 3 fixture-bundle system.
- The Phase 2 test profile is proven unreachable from product requests.
- Reclaim after lease expiry cannot produce duplicate finalization.
- SSE reconnect resumes after the supplied canvas sequence, including when concurrent runs interleave events.
- A failed job resumes at its first incomplete stage.
- No test or trace shows an external call, retry sleep, or streaming loop inside a database transaction.
- The Phase 2 review hardening closes DQ-003 at direct source-node and durable-payload boundaries, including source aliases and 500-character limits before graph-operation audit persistence.
- Composite branch regeneration executes phase-local stable target batches, resumes matching checkpoints after failure, honors cancellation without a partial patch, and emits exactly one deterministic patch unit per frozen target.
- Cancellation is rechecked atomically while entering `patch_ready`, explicit retries emit `run.resumed`, and expired `patch_ready` recovery acquires a fresh fenced lease epoch before completion.
- Idempotent run replay preserves its creation-time SSE baseline after progress has been emitted, the actual combined claim query has representative index-plan coverage, and a live Uvicorn integration test proves incremental SSE delivery.

#### PG-010 — Add cancellation, terminal failures, and bounded worker recycling

**Completed:** July 14, 2026

**Depends on:** PG-007, PG-008, PG-009

**Outcome:** Make long-running work recoverable and prevent poison jobs or worker growth from degrading the system.

**Done when:**

- `POST /api/generation-runs/{run_id}/cancel` locks the run: queued runs become cancelled immediately, running runs receive a cancellation request finalized only by the fenced worker, duplicate cancelled requests return the existing result, and completed/non-cancellable states return `409`.
- `POST /api/generation-runs/{run_id}/retry` requeues only a failed run with a retryable error, no active lease, and remaining attempts; it preserves immutable inputs/checkpoints, emits `run.retry_requested`, and returns `409` for cancelled, non-retryable, lease-owned, or exhausted runs.
- Runs fail terminally after the configured maximum attempts with a structured error and `run.failed` event.
- Failures preserve completed checkpoints and never leave permanent loading state.
- Workers exit gracefully after 50 jobs or 14,400 seconds and release connections, lease threads, queries, caches, and large payloads between jobs.
- Attempt, cancellation, exhausted-job, retry, and recycling telemetry is emitted by this component.
- Tests cover concurrent cancel-versus-claim/finalize, at-most-one `run.cancelled` event, explicit safe retry, exhausted retry rejection, poison jobs, lease loss, worker crash recovery, and clean recycling.

#### PG-009 — Implement persisted canvas-scoped SSE progress

**Completed:** July 14, 2026

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

#### PG-008 — Implement stage checkpoints, retry, and resume

**Completed:** July 14, 2026

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

#### PG-007 — Implement queue claiming and fenced worker leases

**Completed:** July 14, 2026

**Depends on:** PG-006

**Outcome:** Claim work safely through PostgreSQL `SKIP LOCKED` with stale-worker fencing.

**Done when:**

- The worker claims one eligible run using `SELECT ... FOR UPDATE SKIP LOCKED` in a short transaction.
- Claim and expired-lease reclaim queries use the partial indexes from design section 12.14, with representative `EXPLAIN` assertions at production-like cardinality.
- Every claim atomically increments `attempt` and `lease_epoch` and assigns `worker_id` plus a fresh lease token.
- Every worker checkpoint/finalization write verifies run ID, running status, lease token, and lease epoch.
- A lease-keeper renews heartbeats on its own database connection every 10–15 seconds.
- The default lease is 60 seconds, heartbeat and expiry use PostgreSQL time, and renewal extends only the current token/epoch lease.
- Queue-depth, claim-attempt, heartbeat-failure, and lease-loss telemetry is emitted with run/canvas/worker identifiers.
- Expired or queued rows at `max_attempts` are terminalized with a poison-job error and one `run.failed` event instead of remaining claimable or stuck in `running`.
- Concurrent-worker tests cover first-claim/reclaim attempt increments, exhausted-crash terminalization, delayed-but-valid heartbeat, expiry/reclaim, late fenced renewal, and prove a stale worker cannot checkpoint or finalize after reassignment.

#### PG-006 — Add generation persistence and run APIs

**Completed:** July 14, 2026

**Depends on:** PG-005, DQ-003

**Outcome:** Persist generation state and expose an idempotent, version-checked entry point for operation-specific runs.

**Done when:**

- Migrations implement `generation_run`, `generation_stage`, `canvas_event_cursor`, `generation_event`, `graph_patch`, and `graph_patch_operation_decision` from design section 12.
- Composite database constraints prove that every event and patch belongs to the same canvas as its run and that every patch decision can link only a graph operation from that patch's canvas; cross-canvas fixtures fail at the database boundary.
- The durable layer defines stable `RunContextFactory`, `ExecutionProfileResolver`, and `StageOutputValidator` ports plus a generic validated stage-result envelope.
- Deterministic test-only adapters exercise the Phase 2 system; they are unavailable to product requests and cannot silently substitute for an approved profile.
- `POST /api/canvases/{canvas_id}/generation-runs` resolves session ownership and injected ports, locks the canvas before selected entities, validates the exact operation-specific kind/cardinality/review-state contract from design section 14.1, and captures one immutable semantic before-or-after state before creating the queued run and returning `202`, `run_id`, and `events_url`.
- The supported operations are `generate_strategies`, `research_evidence`, `synthesize_opportunities`, and `regenerate_stale`; node scope resolves one target-localized production plan, while branch scope freezes the cycle-safe composite strategy/evidence/opportunity workset and unsupported combinations return `422`.
- Idempotency keys are unique per canvas: an identical request returns the existing run and a conflicting reuse returns `409`.
- `GET /api/generation-runs/{run_id}` returns status, current stage, attempts, structured terminal error, and ready patch ID when present.
- Run context snapshots, manifests, semantic hashes, selected node versions, branch target IDs/versions/distances/anchors, retry fields, request fingerprints, and immutable execution configuration are stored.
- Unique constraints protect stage keys, canvas event sequences, run event sequences, and per-operation patch decisions.
- Generation-run migrations include the queue claim/reclaim indexes required by design section 12.14; the later demo-session migration adds its active-session index when that foreign key exists.
- The cursor migration backfills every existing canvas, and the canvas-creation path atomically creates a cursor row for every future canvas.
- The DQ-003 deletion path is extended and integration-tested across runs, stages, cursor/events, patches/decisions, and every later canvas-scoped persistence table.
- Run creation emits the shared structured-log context and applicable run/canvas/session/operation identifiers required for later run, stage, worker, and provider instrumentation.
- Tests cover allowed state transitions and terminal states, exact before-or-after context capture during a concurrent graph mutation, branch workset cycle/convergence deduplication, and rejection of mixed-revision snapshots.

#### DQ-003 — Define retained-source-content lifecycle

**Completed:** July 14, 2026

**Depends on:** None

**Outcome:** Define what source content may be retained, for how long, and how deletion propagates across every persistence surface.

**Done when:** The policy covers graph nodes, stage outputs, persisted event payloads, source-ingestion reservations, both normalized cache tables, fixture bundles, canvas deletion, user-visible disclosure, and verification requirements for PG-006, PG-009, PG-015, PG-016, and PG-019.

#### PG-005 — Complete Phase 1 persistence and graph-foundation verification

**Completed:** July 14, 2026

**Depends on:** PG-003, PG-004

**Outcome:** Prove the graph foundation survives real editing and reload flows.

**Done when:**

- Save/reload preserves graph content, metadata, positions, entity versions, and canvas revision.
- Concurrent stale edits produce a recoverable UI conflict instead of overwriting newer state.
- Tests cover node/edge CRUD, incident-edge and branch-anchor node-delete rejection/retry, operation idempotency and conflicting-key reuse, metadata field ownership, semantic/position version and timestamp isolation, global/branch constraint anchoring and pinning, operation ordering, revision increments, index-backed graph access, auto-layout persistence, and reload.
- The canonical goal-plus-builder-constraints setup works end to end.
- A live browser/API/PostgreSQL test covers the canonical layout bounds, drag/reload persistence, and a stale edit from a concurrent API client.
- Phase 1 setup and verification commands are documented.

#### PG-004 — Build the editable graph canvas

**Completed:** July 14, 2026

**Depends on:** PG-003

**Outcome:** Let a user create and manipulate the typed graph through a browser canvas.

**Done when:**

- A user can create/open a canvas and add goal and constraint nodes.
- Typed nodes and edges render distinctly without supporting arbitrary ontology editing.
- A user can select nodes, edit semantic content, add/remove edges, delete entities, and move nodes.
- Deleting a node with incident edges or anchored branch constraints presents the conflict, guides the user through explicit dependency resolution, and retries without hiding any removal from history.
- A user can mark a constraint as pinned global or branch-scoped, choose a valid branch root, reanchor it, and see that semantic state survive reload.
- Node movement is persisted through idempotent localized `MOVE_NODE` operations using position-version preconditions.
- An auto-layout action produces readable deterministic placement without changing semantic graph state.

#### PG-003 — Implement canvas and localized graph-operation APIs

**Completed:** July 14, 2026

**Depends on:** PG-002

**Outcome:** Support canvas CRUD and localized graph mutations rather than whole-graph replacement.

**Done when:**

- Canvas endpoints implement the API surface in design section 27.1.
- `POST /api/canvases/{canvas_id}/operations` supports `ADD_NODE`, `UPDATE_NODE`, `DELETE_NODE`, `ADD_EDGE`, `UPDATE_EDGE`, `DELETE_EDGE`, `PATCH_NODE_METADATA`, and `MOVE_NODE`.
- Every direct mutation includes a client-generated operation key; exact retry returns the original result and conflicting key reuse returns `409`.
- `GET /api/canvases/{canvas_id}/operations?after={revision}` returns every later operation in deterministic revision-and-operation order for incremental synchronization.
- Semantic node mutations validate `expected_version`; `MOVE_NODE` validates `expected_position_version`; edge updates/deletes validate the edge version.
- Semantic mutations increment only the semantic version, update `semantic_updated_at`, and invalidate token metadata; `MOVE_NODE` increments only the position version and updates only position/general timestamps, so layout changes cannot invalidate generation or graph-patch preconditions.
- `UPDATE_NODE` and `PATCH_NODE_METADATA` enforce the same per-kind writable-field allowlists; attempts to smuggle or directly write server-owned review, support, stale, source-identity, provenance, or lineage fields are rejected without creating an operation.
- `DELETE_NODE` returns `409` with incident edge IDs/versions and referencing branch-constraint IDs/versions until the client resolves those dependencies through audited operations; it never silently cascades.
- Pinning/unpinning, changing a constraint's context scope, or reanchoring its branch root is an audited semantic mutation with an expected-version precondition and kind/same-canvas validation.
- Each successful mutation locks the canvas before entity rows, appends a `graph_operation`, and increments the canvas revision in the same short transaction.
- Canvas deletion removes every Phase 1 canvas-scoped record; later persistence migrations must extend the same lifecycle deletion test.
- API tests cover validation, metadata ownership, wrong-kind fields, rollback, cross-canvas branch anchors, incremental operation replay, canvas-first locking, and optimistic conflicts.

#### PG-002 — Implement the relational graph schema

**Completed:** July 14, 2026

**Depends on:** PG-001

**Outcome:** Persist canvases, typed nodes, typed edges, and append-only graph operations using the frozen domain taxonomy.

**Done when:**

- Migrations create `canvas`, `node`, `edge`, `graph_operation`, and `node_staleness_cause` records aligned with design sections 9 and 12.
- Nodes and edges belong to one canvas; nodes carry independent semantic and position versions/timestamps, while edges carry an entity version.
- Node semantic content is separated from position and UI-only state.
- Constraint metadata validates `context_scope: global | branch` and `pinned: boolean`; branch scope requires one relational same-canvas `branch_root_node_id` of kind strategy, claim, or opportunity, while global scope requires a null root.
- Nodes persist queryable stale state and an append-only, operation-linked cause ledger with database-enforced same-canvas references and valid active/cleared records.
- Node and edge kinds are validated against the frozen MVP taxonomy.
- Referential integrity prevents cross-canvas edges, branch anchors, staleness causes, and dangling endpoints.
- Graph operations store an actor-scoped operation key and request fingerprint with a uniqueness constraint per canvas.
- Migrations add the required node, edge, graph-operation, active-staleness, and branch-anchor access-path indexes from design section 12.14; representative query-plan checks avoid full scans.

#### PG-001 — Bootstrap the application runtime

**Completed:** July 14, 2026

**Depends on:** None

**Outcome:** Establish the smallest runnable Django, PostgreSQL, browser-client, and test foundation without introducing infrastructure excluded by the design.

**Done when:**

- The Django ASGI web process starts locally and connects to PostgreSQL through environment-based configuration.
- A separate worker entry point exists, even if generation work is not implemented yet.
- The browser client has a documented, reversible build/development setup.
- Setup includes baseline formatting, static analysis, and test commands.
- No Redis, Celery, Neo4j, or WebSocket dependency is introduced.

#### DQ-001 — Choose progressive or post-extraction evidence display

**Completed:** July 14, 2026

**Resolution:** Display each schema-valid extraction batch progressively as provisional evidence. It becomes authoritative and selectable only after the user accepts the evidence patch. Recorded in `design.md` sections 14 and 32.

## Notes

- Follow the phase order unless `design.md` or an explicit user instruction changes it.
- Preserve the frozen architecture: Django, PostgreSQL-only state, separate worker, PostgreSQL queue, canvas-sequenced SSE, fenced leases, short transactions, idempotent direct graph/source-ingestion operations, explicit incident-edge deletion, independent semantic/position versions and recency, exact operation input contracts, operation-specific runs with explicit safe retry, source-level evidence independence, split query/content caches, explicit evidence selection, candidate graph patches with dependency-closed local-ID mapping, regeneration lineage, and per-operation decisions, deterministic context packing and evidence clustering, direction-aware cycle-safe cascade invalidation, full-stage replay fixtures, resumable checkpoints, bounded workers, typed execution profiles, and isolated quota-protected public demo sessions.
- Do not add MVP non-goals such as multi-user collaboration, mobile-first editing, arbitrary ontologies, billing, Redis, Celery, Neo4j, or WebSockets.
- Open questions are decision checkpoints, not permission to diverge from frozen architectural decisions.
