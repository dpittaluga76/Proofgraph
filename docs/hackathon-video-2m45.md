# ProofGraph hackathon video — 2:45

This is the recording plan for the public OpenAI Build Week submission. The final video must be
exactly 2:45, use a user-recorded English voiceover, contain no music, and show only behavior that
was captured from the real public ProofGraph demo.

## Final sequence

| Time | Clip | Footage | Narration |
|---|---|---|---|
| 0:00–0:12 | `01-problem.mp4` | Title card, then the full ProofGraph canvas. | “Teams make product decisions in documents and chat, where evidence, assumptions, and decisions quickly drift apart. ProofGraph is an evidence-native opportunity canvas: every idea stays connected to why it should be believed and how it can be tested.” |
| 0:12–0:28 | `02-fresh-canvas.mp4` | Open a completely fresh anonymous browser. Show the seeded revision-0 canvas and the goal and constraints. | “A new anonymous visitor gets an isolated, seeded canvas—no account and no shared mutable demo. This example asks how a small SaaS team can reduce repeated security-questionnaire work, with a six-week build horizon and approved evidence only.” |
| 0:28–0:48 | `03-start-hybrid.mp4` | Select the goal and relevant constraints, choose **Hybrid · cached evidence + live reasoning**, and start generation. Keep the profile explanation visible briefly. | “I select the goal and constraints and start the judge-facing hybrid workflow. Planning, sources, and claims are deterministic, while GPT-5.6 performs live synthesis, critique, and patch construction. Credentials stay on the server, with profile allowlisting and PostgreSQL-backed quotas.” |
| 0:48–1:10 | `04-sse-patch-apply.mp4` | Show at least two progress events arriving, the terminal state, the patch review, and **Apply patch**. Show the revision changing only after apply. | “Progress streams incrementally over server-sent events. The run produces a reviewable graph patch instead of mutating the canvas in the background. I can inspect every proposed node and edge; application is transactional, and the authoritative revision advances only when I approve it.” |
| 1:10–1:34 | `05-evidence-provenance.mp4` | Open a source and two claims. Show cached-evidence labeling, excerpts or limitations, and provenance edges. | “The result is source-backed, not a loose answer. Claims retain evidence type, strength, limitations, review status, and provenance. Cached fixture evidence is labeled honestly, while live GPT reasoning is visually distinct. The graph makes it possible to trace a recommendation back to the evidence supporting—or contradicting—it.” |
| 1:34–1:56 | `06-opportunity-experiment.mp4` | Inspect the leading opportunity, then its assumptions, risks, and experiment. | “ProofGraph turns those claims into ranked opportunities without hiding uncertainty. Here the approved-answer workspace stays connected to its assumptions and adoption risks. A cheap concierge experiment makes the proposal falsifiable: interview the buyer, test repeated work, and stop if recurrence or budget is absent.” |
| 1:56–2:18 | `07-stale-regeneration.mp4` | Edit an upstream node, show downstream staleness, select stale branches, and start regeneration. Capture simultaneous/parallel branch progress. | “When upstream thinking changes, ProofGraph marks affected descendants stale instead of pretending the old answer is still valid. Regeneration is dependency-aware and always parallel across independent stale branches. Each result still returns as a patch, so failure or retry cannot silently duplicate graph mutation.” |
| 2:18–2:30 | `08-replay-reset-isolation.mp4` | Briefly show deterministic replay reaching patch review; reset; then a second anonymous context with a different canvas ID. | “The full replay profile follows the same pipeline without a provider call. One-click reset creates a new revision-0 canvas, retires the old resources, and a separate anonymous browser receives a different, inaccessible canvas.” |
| 2:30–2:38 | `09-v2-pass.mp4` | Full-screen sanitized `proofgraph-v2-pass.png`; animate only a simple scale or dissolve. | “The frozen V2 benchmark passed all four required dimensions: twenty scenarios, eighty complete outputs, zero partials, and two independent judges with eighty ratings each.” |
| 2:38–2:45 | `10-close.mp4` | Return to the public demo, with small public-demo and repository links. | “Codex helped build and verify the system; GPT-5.6 powers its live reasoning. Open ProofGraph and test the evidence trail yourself.” |

## Capture checklist

- Record at 1920×1080 with browser zoom at 100%, the pointer visible, and notifications disabled.
- Use a fresh anonymous browser for `02-fresh-canvas.mp4` and a separate fresh context for the
  isolation proof in `08-replay-reset-isolation.mp4`.
- Before recording, verify the public hostname, worker, database, and SSE stream are healthy.
- Capture real public-demo behavior. Do not stage or simulate a success response.
- Keep the run ID visible only when useful; it is not an authoritative graph node.
- Let at least two SSE progress events visibly arrive before terminal completion.
- Show the canvas revision immediately before and after patch application.
- Capture the hybrid segment only after a minimal live GPT-5.6 run succeeds with existing credits.
  If hybrid is blocked for lack of credit, replace that footage and narration with an explicit
  “hybrid verification blocked—no charge authorized” disclosure; do not imply a live run occurred.
- Capture deterministic replay, reset, and session isolation in real time.
- If a wait is shortened in editing, place a visible `live wait trimmed` caption over the cut for
  its full duration. Do not speed-ramp or jump-cut a wait without that caption.
- Record each clip with two seconds of handle at the start and end. Trim to the exact table ranges
  only in the final edit.

## Privacy and truthfulness rules

- Never open, record, screenshot, package, or expose `evaluation/runs/`.
- Use only the sanitized V2 graphic generated from the aggregate README figures.
- Do not show `.env`, `.env.public`, terminal environment output, Cloudflare connector tokens,
  API keys, cookies, request headers, provider response IDs, database credentials, or Devpost
  account details.
- Do not include private filesystem paths, internal artifact names, raw judge prompts, private
  maps, rating files, or model-provider payloads.
- Review every frame at full size before upload. Scan the exported file metadata and remove any
  title, author, comment, or path fields that are not intentionally public.
- Use only the user’s recorded English narration. Do not use copyrighted music; this submission
  uses no music at all.
- Do not say a public, hybrid, reboot, recovery, or isolation check passed unless the corresponding
  acceptance evidence exists.

## Voiceover and edit workflow

1. Record the ten screen clips with the filenames above into an ignored local video-work folder.
2. Record one clean English narration track in a quiet room. Leave short natural pauses between
   table rows so each section can be aligned without changing speech speed.
3. Remove mistakes and long silences, then normalize dialogue conservatively. Do not add music.
4. Add only truthful captions, including `live wait trimmed` wherever a real wait was shortened.
5. Use simple cuts or short dissolves. Avoid decorative transitions that obscure the UI.
6. Export exactly 165 seconds at 1920×1080, 30 fps, H.264 video, AAC dialogue audio, and a broadly
   compatible `yuv420p` pixel format.
7. Watch the complete exported file with headphones, then repeat the privacy review frame by frame.
8. Upload publicly as **ProofGraph — Evidence-native opportunity canvas | OpenAI Build Week**, mark
   it **not made for kids**, include the public demo and repository links, and verify the URL from a
   clean anonymous browser.

## Final export gate

- Duration is between 2:40 and 2:50, with the target export exactly 2:45.
- Resolution is 1920×1080; video is H.264; audio is AAC; no music is present.
- Every table segment is represented and the ending call to action finishes before 2:45.
- Hybrid footage is either a verified live run or explicitly disclosed as blocked.
- Any trimmed live wait has a visible caption.
- The V2 card is the sanitized public asset, not a benchmark screenshot.
- Public demo and repository URLs are correct, and no secret or private artifact is visible.
