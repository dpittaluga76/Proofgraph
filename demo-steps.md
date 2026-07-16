# Proofgraph Phase 5 execution steps

This file tracks the operational work required to complete Phase 5. `design.md` is the source of
truth and `TASKS.md` is the task tracker. Never mark a task complete here unless its definition of
done is also satisfied in `TASKS.md`.

## PG-027 — Automated blinded model evaluation

**Current status (July 15, 2026):** The Terra generation is complete at 200 successful stages and
80 normalized outputs. The blind packet and private variant map are prepared. The two-judge runner,
schema-v2 analysis, tests, and documentation are implemented. The paid automated judge run has 12 of
40 valid checkpoints: 7 from Vera and 5 from Marco. A strict-schema bug and an unsafe model-echoed ID
were corrected without discarding those checkpoints; rerunning the frozen command requests only the
remaining 28 calls. The numerical acceptance result remains pending, so PG-027 remains Pending.

**Frozen run:** `evaluation/runs/eval-terra-v1`

**Automated judges:**

- **Vera Crosscheck — Evidence Auditor:** `gpt-5.6-sol`, persona `vera_crosscheck_v1`.
- **Marco Launch — Bootstrap Operator:** `gpt-5.6-luna`, persona `marco_launch_v1`.

Both judges pursue the same goal: identify opportunities that a constrained builder can responsibly
test, reach buyers for, and monetize without overstating evidence. They use the same rubric and
weights. Every effective score is their arithmetic mean. Absolute differences of at least two points
are reported, not adjudicated.

### Completion tracker

- [x] Complete the private Terra generation artifact: 80 outputs from 200 provider stages.
- [x] Prepare the 80-output blind packet and separate private variant map.
- [x] Implement and test the two-persona, resumable, concurrent judge runner.
- [x] Implement schema-v2 arithmetic-mean analysis and disagreement reporting.
- [x] Update `design.md`, `TASKS.md`, `README.md`, and evaluation operator documentation.
- [ ] Explicitly authorize and complete the 40 paid judge calls.
- [ ] Generate `result.json` and `result.md`.
- [ ] Confirm all four required dimensions pass both numerical thresholds.
- [ ] Copy the non-sensitive result summary into `README.md` and move PG-027 to Done.

### 1. Set the frozen paths and models

Run all commands from `C:\Users\Usuario\Proofgraph` in PowerShell:

```powershell
$Run = "evaluation/runs/eval-terra-v1"
$JudgeA = "gpt-5.6-sol"
$JudgeB = "gpt-5.6-luna"
```

Do not edit or replace the completed Terra generation artifact. Do not reuse
`evaluation/runs/eval-v1/private-generation.json`; that is a legacy bare-`gpt-5.6` artifact.

### 2. Configure credentials and run the offline preflight

Put `OPENAI_API_KEY` in the ignored `.env` file. Never commit it or place it in browser code.

```powershell
uv run python manage.py check
uv run pytest -q tests/test_evaluation.py
```

Both commands must pass before authorizing judge calls.

### 3. Verify the prepared inputs

These checks are offline:

```powershell
$Generation = Get-Content "$Run/private-generation.json" -Raw | ConvertFrom-Json
$Packet = Get-Content "$Run/rating/blind-packet.json" -Raw | ConvertFrom-Json
$Generation.model
@($Generation.outputs).Count
@($Packet.scenarios).Count
@($Packet.scenarios.outputs).Count
```

Expected values are `gpt-5.6-terra`, `80`, `20`, and `80`. Keep the following files private:

| Artifact | Active PG-027 purpose |
| --- | --- |
| `private-generation.json` | Variant labels, provider response IDs, and generation token usage |
| `blind-packet.json` | The only input given to both model judges |
| `private-variant-map.json` | Reverses opaque IDs after judging is complete |
| `private-judge-run.json` | Resumable judge checkpoints, configuration, response IDs, and usage |
| `rating-judge-a.json` | Vera's validated original scores, rationales, and provenance |
| `rating-judge-b.json` | Marco's validated original scores, rationales, and provenance |

The existing `rating-rater-a.json`, `rating-rater-b.json`, and `adjudications.json` files are legacy
compatibility artifacts. Leave them untouched; they are not inputs to the automated workflow.

### 4. Explicitly run or resume the paid judges

This is the only remaining paid step. It makes exactly 40 calls: 20 scenarios for Vera and 20 for
Marco. Each call scores all four opaque outputs, so the run produces 80 ratings and 560 individual
dimension scores per judge.

```powershell
uv run python manage.py judge_evaluation_packet `
  --packet "$Run/rating/blind-packet.json" `
  --output-dir "$Run/rating" `
  --seed 27003 `
  --judge-a-model $JudgeA `
  --judge-b-model $JudgeB `
  --workers 6 `
  --confirm-cost
```

The command uses medium reasoning, a fixed 3,000-output-token ceiling, structured responses,
`store=False`, and six workers by default. It independently and deterministically shuffles the four
opaque outputs for each judge and scenario. Neither judge receives the private variant map,
generation metadata, peer scores, or variant labels. Candidate content is explicitly treated as
untrusted data.

Each successful scenario response is atomically checkpointed in `private-judge-run.json`. If the
process is interrupted or the provider returns an error, rerun the identical command. Completed
judge/scenario calls are skipped. After repeated `429` responses, rerun with fewer workers; a value
from 1 through 8 is allowed. Worker count is execution-only. Changing a model, packet, seed, persona,
prompt, reasoning level, or token budget requires preserving the old artifact and using a new output
directory.

### 5. Verify the completed judge artifacts

```powershell
$JudgeRun = Get-Content "$Run/rating/private-judge-run.json" -Raw | ConvertFrom-Json
$A = Get-Content "$Run/rating/rating-judge-a.json" -Raw | ConvertFrom-Json
$B = Get-Content "$Run/rating/rating-judge-b.json" -Raw | ConvertFrom-Json
@($JudgeRun.results).Count
@($A.ratings).Count
@($B.ratings).Count
$A.provenance.model
$B.provenance.model
```

Expected values are `40`, `80`, `80`, `gpt-5.6-sol`, and `gpt-5.6-luna`. Do not proceed if any
count or model differs.

### 6. Analyze and unblind offline

```powershell
uv run python manage.py analyze_evaluation `
  --packet "$Run/rating/blind-packet.json" `
  --private-map "$Run/rating/private-variant-map.json" `
  --generation "$Run/private-generation.json" `
  --judge-a "$Run/rating/rating-judge-a.json" `
  --judge-b "$Run/rating/rating-judge-b.json" `
  --output-json "$Run/result.json" `
  --output-markdown "$Run/result.md"
```

`--rater-a` and `--rater-b` remain deprecated aliases. The automated workflow does not accept
`--adjudications`. Analysis rejects mismatched runs, packet hashes, persona versions, incomplete
coverage, duplicate IDs, invalid scores, and mismatched private maps.

Review the result:

```powershell
Get-Content "$Run/result.md"
$Result = Get-Content "$Run/result.json" -Raw | ConvertFrom-Json
$Result.acceptance_passed
$Result.disagreements | ConvertTo-Json -Depth 8
$Result.dimensions | ConvertTo-Json -Depth 8
```

### 7. Apply the frozen acceptance rule

`acceptance_passed` is true only when every required dimension independently meets both conditions:

| Required dimension | Mean full pipeline − generic | 95% bootstrap CI lower bound |
| --- | ---: | ---: |
| Evidence relevance | At least `+0.5` | Greater than `0` |
| Specificity | At least `+0.5` | Greater than `0` |
| Testability | At least `+0.5` | Greater than `0` |
| Builder fit | At least `+0.5` | Greater than `0` |

Novelty, feasibility, and economic leverage are reported but are not gating dimensions. The report
uses 10,000 deterministic paired scenario-bootstrap resamples. Judge disagreements never replace the
original scores; even differences of two or more are averaged and reported.

If the result passes, preserve all artifacts in controlled storage, copy only the non-sensitive
summary into `README.md`, record every version, and move PG-027 to Done in `TASKS.md`. If it fails,
keep PG-027 Pending, preserve the complete failed run unchanged, diagnose scenario differences, and
version any later benchmark or pipeline change before starting a new run.

### Cost boundary

The completed generation was estimated at approximately `$4.04`. The planned Sol/Luna judge run was
estimated at approximately `$2–$3.50`, keeping the projected combined usage below the stated `$11`
budget. These are planning estimates only; actual token usage and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
are authoritative. The implementation and test commands make no paid judge calls.

## References

- `design.md`, sections 23.4–23.6 and DQ-006: authoritative protocol.
- `TASKS.md`, PG-027: implementation status and definition of done.
- `evaluation/README.md`: artifact boundaries and command reference.
- `evaluation/scenarios.v1.json`: versioned synthetic benchmark set.
