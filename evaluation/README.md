# Comparative evaluation

PG-027 is an internal, reproducible command-line benchmark. It compares four frozen generation
variants over the 20 synthetic scenarios in `scenarios.v1.json`. Generation explicitly selects one
of `gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna`; all variants in the run use that same model.
Bare `gpt-5.6` and every other model ID are rejected. The completed run used Terra and produced 80
normalized outputs. Its blind packet, 40-call automated judge run, two 80-rating artifacts, and
schema-v2 report are complete. The frozen v1 result is FAIL because builder-fit mean lift was
`+0.350`, below the required `+0.500`; the result must remain unchanged. The V2 acceptance rule is
pre-registered and implemented locally, but no fresh V2 generation or judge run has been authorized
or completed.

The two independent automated judges are:

- **Vera Crosscheck — Evidence Auditor** (`vera_crosscheck_v1`), normally `gpt-5.6-sol`.
- **Marco Launch — Bootstrap Operator** (`marco_launch_v1`), normally `gpt-5.6-luna`.

Both judges share the same mission, rubric, dimension weights, and blind inputs. Every effective
score is their arithmetic mean. Absolute score differences of at least two points are reported and
retained without adjudication.

## Artifact boundaries

- `private-generation.json` contains variant labels, provider response IDs, and token usage. Keep it
  private.
- `blind-packet.json` contains scenarios, evidence, opaque output IDs, normalized opportunities, and
  the fixed rubric. This is the only evaluation artifact passed to the model judges.
- `private-variant-map.json` reverses the blinding. Never pass it to either judge.
- `private-judge-run.json` contains the packet hash, frozen judge configurations, work order,
  response IDs, token usage, and successful scenario checkpoints. Keep it private.
- `rating-judge-a.json` and `rating-judge-b.json` are materialized only after all 40 checkpoints
  validate. They contain the original scores, rationales, and model/persona provenance.
- `rating-rater-a.json`, `rating-rater-b.json`, and `adjudications.json` are legacy compatibility
  artifacts. Packet preparation leaves them available, but they are not part of active PG-027.
- `result.json` is the authoritative V1 schema-v2 result. It retains judge provenance, original scores
  and rationales, arithmetic means, disagreements, paired differences, and confidence intervals.
- An official V2 `result.json` will use schema v3 and record `comparative_acceptance_v2`, each
  full-pipeline dimension mean, and the exact acceptance criteria. It must live under a distinct V2
  run directory and use fresh post-registration generation and judge artifacts.

The ignored `evaluation/runs/` directory is the recommended location for generated and private
artifacts. Only the synthetic scenario set and workflow documentation are checked in.

For a fresh official V2 workflow, use `scripts/run-evaluation-v2.ps1` and follow
`demo-steps-v2.md`. The runner preserves the command boundaries below, adds a frozen local manifest,
and refuses paid stages without explicit cost confirmation.

## 1. Generation — complete for `eval-terra-v1`

The generation command makes 200 Responses API calls for 20 scenarios and four frozen paths. It
atomically checkpoints each successful stage and supports 1–8 workers, with six as the default.

```powershell
uv run python manage.py generate_evaluation_variants `
  --output evaluation/runs/eval-terra-v1/private-generation.json `
  --seed 27001 `
  --model gpt-5.6-terra `
  --workers 6 `
  --confirm-cost
```

Do not rerun this command for the existing completed artifact. A different model, seed, prompt,
strategy catalog, or scenario set requires a new output path. API storage is disabled. If a provider
failure interrupts a new run, rerun the identical command; successful stage checkpoints are reused.

## 2. Blind packet preparation — complete for `eval-terra-v1`

This deterministic offline command produced the existing 80-output blind packet:

```powershell
uv run python manage.py prepare_evaluation_packet `
  --generation evaluation/runs/eval-terra-v1/private-generation.json `
  --output-dir evaluation/runs/eval-terra-v1/rating `
  --seed 27002
```

Automated judges consume only `blind-packet.json`. They never receive the generation artifact,
private map, peer scores, variant labels, or provider metadata.

## 3. Run or resume the two automated judges — complete for `eval-terra-v1`

This command makes exactly 40 calls: one scenario-level call per judge for each of 20 scenarios.
Each response independently scores all four opaque outputs on all seven rubric dimensions. The two
judges receive independently and deterministically shuffled output orders. Calls use medium
reasoning, a 3,000-output-token ceiling, structured output, API storage disabled, and six concurrent
workers by default.

```powershell
uv run python manage.py judge_evaluation_packet `
  --packet evaluation/runs/eval-terra-v1/rating/blind-packet.json `
  --output-dir evaluation/runs/eval-terra-v1/rating `
  --seed 27003 `
  --judge-a-model gpt-5.6-sol `
  --judge-b-model gpt-5.6-luna `
  --workers 6 `
  --confirm-cost
```

Rerun the identical command after interruption or a provider failure. Completed judge/scenario
checkpoints are reused. Worker count is execution-only and may be reduced after repeated `429`
responses. A packet, seed, model, persona, prompt, reasoning, or token-budget mismatch is rejected;
preserve the existing artifact and use a new output directory for a changed configuration. Run only
one process against a judge artifact at a time.

## 4. Analyze and report — offline

Analysis validates both judge artifacts, always uses the arithmetic mean of their scores, and reports
large disagreements without changing them. `--rater-a` and `--rater-b` remain deprecated aliases for
the two judge arguments. The automated path has no `--adjudications` option.

```powershell
uv run python manage.py analyze_evaluation `
  --packet evaluation/runs/eval-terra-v1/rating/blind-packet.json `
  --private-map evaluation/runs/eval-terra-v1/rating/private-variant-map.json `
  --generation evaluation/runs/eval-terra-v1/private-generation.json `
  --judge-a evaluation/runs/eval-terra-v1/rating/rating-judge-a.json `
  --judge-b evaluation/runs/eval-terra-v1/rating/rating-judge-b.json `
  --acceptance-rule v1 `
  --output-json evaluation/runs/eval-terra-v1/result.json `
  --output-markdown evaluation/runs/eval-terra-v1/result.md
```

`--acceptance-rule` defaults to `v1`, preserving the frozen schema-v2 behavior. V1 requires evidence
relevance, specificity, testability, and builder fit each to improve by at least `+0.5` mean points
over generic generation and each paired scenario-bootstrap 95% confidence interval to have a lower
bound above zero. All seven dimensions and disagreement counts and rates are reported. Bootstrap
analysis remains deterministic at 10,000 resamples.

## Frozen v1 result

| Required dimension | Mean full − generic | 95% bootstrap CI | Result |
| --- | ---: | --- | --- |
| Evidence relevance | `+2.925` | `[2.725, 3.100]` | Pass |
| Specificity | `+0.950` | `[0.800, 1.100]` | Pass |
| Testability | `+1.650` | `[1.475, 1.825]` | Pass |
| Builder fit | `+0.350` | `[0.175, 0.550]` | **Fail** |

The full pipeline received builder-fit `5.0` from both judges, while the generic baseline received
`4.5` from Vera and `4.8` from Marco. This ceiling, rather than judge disagreement, caused the mean
threshold failure: only 2 of 560 comparisons had differences of at least two points, and neither was
builder fit. Preserve `result.json` and `result.md` as the authoritative v1 failure. Any benchmark-v2
change must be pre-registered before another paid run; do not adjust v1 scores or thresholds.

## Pre-registered V2 local rule

V2 keeps the V1 rule for evidence relevance, specificity, and testability. Builder fit instead
passes only when both conditions hold:

- Full-pipeline builder-fit mean is at least `4.500 / 5`.
- The paired full-minus-generic 95% bootstrap confidence-interval lower bound is at least `0`.

This treats builder fit as an absolute quality guardrail plus a no-regression check. V2 analysis is
explicit and produces schema v3:

```powershell
uv run python manage.py analyze_evaluation `
  --packet evaluation/runs/eval-terra-v1/rating/blind-packet.json `
  --private-map evaluation/runs/eval-terra-v1/rating/private-variant-map.json `
  --generation evaluation/runs/eval-terra-v1/private-generation.json `
  --judge-a evaluation/runs/eval-terra-v1/rating/rating-judge-a.json `
  --judge-b evaluation/runs/eval-terra-v1/rating/rating-judge-b.json `
  --acceptance-rule v2 `
  --output-json evaluation/runs/eval-terra-v1/diagnostic-v2.json `
  --output-markdown evaluation/runs/eval-terra-v1/diagnostic-v2.md
```

That command is a local implementation check using V1 ratings. Its output must remain labeled
diagnostic and cannot replace the authoritative V1 result or count as the official V2 benchmark.
A later official V2 run requires a distinct directory plus fresh generation and judge seeds and
artifacts created after this pre-registration.

The checked local diagnostic produced schema v3 and passed the V2 implementation rules. Builder fit
had a `5.000` full-pipeline mean, `+0.350` lift, and `[0.175, 0.550]` interval. The ignored outputs are
`evaluation/runs/eval-terra-v1/diagnostic-v2.json` and `diagnostic-v2.md`; they remain diagnostic V1
reanalysis rather than an official V2 result.
