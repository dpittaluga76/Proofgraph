# ProofGraph — Devpost submission copy

This file is the source for the Devpost draft. Replace every `PENDING` value only after its public
acceptance evidence exists.

## Submission fields

| Field | Value |
|---|---|
| Product | ProofGraph |
| Submitter | Individual |
| Country | Uruguay |
| Category | Work & Productivity |
| Repository | https://github.com/dpittaluga76/Proofgraph |
| Public demo | PENDING_NAMED_TUNNEL_URL |
| Public video | PENDING_YOUTUBE_URL |
| `/feedback` Session ID | PENDING_PRIMARY_BUILD_TASK_FEEDBACK_SESSION_ID |

No judge account or credential is required.

## Tagline

An evidence-native opportunity canvas that keeps ideas connected to claims, risks, provenance, and
falsifiable experiments.

## Description

### Inspiration

Product discovery usually happens across chat, documents, spreadsheets, and whiteboards. The final
idea survives, but the evidence, contradictions, assumptions, and reasons behind it are easily lost.
That makes opportunity selection hard to audit and even harder to revise when an upstream fact
changes.

### What it does

ProofGraph is an evidence-native opportunity canvas. A builder starts with a goal and constraints,
generates three distinct strategies, researches evidence, reviews source-backed claims, and creates
structured opportunities with assumptions, risks, and cheap validation experiments. Every output is
a typed graph node connected to its provenance.

Generation never mutates the authoritative canvas in the background. It streams durable progress
over server-sent events and returns a reviewable graph patch. Patch application is transactional and
idempotent. When a user edits an upstream node, affected descendants become visibly stale; explicit
regeneration creates parallel successor branches so the original reasoning remains auditable.

The public demo requires no account. Each anonymous visitor receives an isolated clone of the
canonical security-questionnaire scenario, a signed session, one-click reset, profile allowlisting,
and PostgreSQL-backed session, concurrency, and global hybrid quotas. The primary demo profile uses
deterministic planning/evidence with live GPT-5.6 synthesis and critique. A deterministic replay
profile exercises the same pipeline and patch UI without a provider call.

### How we built it

The application follows a three-component architecture:

- Django ASGI serves the API and built React client through Uvicorn and WhiteNoise.
- A separate Django worker claims durable PostgreSQL jobs with fenced leases.
- PostgreSQL stores graph state, immutable stage checkpoints, replayable events, patches, quotas,
  sessions, audit operations, and the worker queue.

React and TypeScript render the graph and patch review experience. SSE supports resumable progress
from the last event sequence. Typed Pydantic contracts validate every provider stage. Public source
ingestion blocks private-network targets and retains only derived, bounded excerpts—not full fetched
documents. Production traffic enters through a Cloudflare named Tunnel; the origin is loopback-only
and PostgreSQL has no public port.

### Codex and GPT-5.6

Codex was the primary engineering environment used to translate the architecture into migrations,
domain services, security boundaries, deterministic fixtures, tests, production containers, and
acceptance workflows. The final `/feedback` Session ID from the primary build task is listed in the
submission fields once generated.

GPT-5.6 powers live synthesis, critique, and patch construction in the hybrid demo. It also supported
the internal blinded comparative benchmark. The official post-registration V2 run covered 20
scenarios, produced 80 complete outputs with zero partials, received 80 ratings from each of two
independent judge personas, and passed all four required schema-3 dimensions. Only the sanitized
aggregate result is public.

### Challenges

The difficult work was not drawing nodes. It was preserving authority and auditability across long
model calls: no external request inside a database transaction, no duplicate mutation after worker
loss, no silent profile fallback, no stale reasoning presented as current, and no cross-session data
access in an anonymous public demo. Deterministic replay also had to match semantic graph state after
real patches removed fixture-only identifiers and advanced canvas revisions.

### Accomplishments

- Reviewable, dependency-closed graph patches with transactional application.
- Replayable SSE, checkpoint resume, cancellation, retry, and fenced worker recovery.
- Source-backed claims, contradictions, lineage, visible staleness, and parallel regeneration.
- Anonymous isolation, reset, CSRF, secure cookies, profile restrictions, and durable cost controls.
- A fresh, pre-registered V2 benchmark that passed all required dimensions.
- A zero-subscription public architecture using Docker and a stable Cloudflare named Tunnel.

### What we learned

AI product quality depends as much on state and evidence contracts as on prompts. Durable checkpoints
make recovery testable; explicit patches keep the user authoritative; provenance turns model output
into inspectable reasoning; and a frozen benchmark prevents a weak result from being redefined after
it is observed.

### What is next

Next steps are broader real-user validation, additional redistributable scenario bundles, stronger
operator monitoring, and evaluation of semantic retrieval only if explicit graph neighborhoods prove
insufficient. Replacement regeneration, full-document retention, autonomous legal/security review,
and paid multi-region hosting remain intentionally outside this MVP.

## Judge testing instructions

1. Open `PENDING_NAMED_TUNNEL_URL` in a fresh anonymous browser. No login or credentials are needed.
2. Confirm the revision-0 security-questionnaire canvas contains one goal and three pinned builder
   constraints.
3. Open generation controls. For the guaranteed no-cost path, choose **Deterministic replay**, select
   the goal and all three constraints, leave the instruction blank, and start generation.
4. Watch multiple progress events arrive. At completion, inspect the patch and apply it. Confirm the
   canvas revision advances only after application.
5. Continue the canonical journey: select the generated strategy, research evidence, accept the
   evidence patch, then select accepted claims and synthesize opportunities.
6. Inspect an opportunity’s supporting claims, provenance, assumptions, risks, and experiment.
7. Edit an upstream node to see dependent descendants become stale, then regenerate a selected stale
   branch and compare the parallel successor lineage.
8. Use **Reset demo**. Confirm a new revision-0 canvas appears and the retired canvas cannot be
   reopened.
9. Open a second anonymous browser context to verify it receives a different canvas and cannot access
   the first context’s resources.
10. When the UI reports that hybrid is available, the **Hybrid · cached evidence + live reasoning**
    profile demonstrates live GPT-5.6 synthesis using the same reviewable-patch workflow. Replay is
    always the explicit fallback; the server never silently changes profiles.

Availability depends on the self-hosted PC and its internet connection. If the demo is temporarily
unreachable, use the public video and follow the README’s clean local setup with deterministic replay.
