# ProofGraph V2 evaluation runbook

This runbook operates the `comparative_acceptance_v2` benchmark through
`scripts/run-evaluation-v2.ps1`. `design.md` sections 23.5–23.6 are authoritative. PG-027 completed
after the fresh post-registration V2 generation and judge run passed.

## Current status

As of July 20, 2026, the fresh V2 workflow is complete. It produced 80 Terra outputs with no
partials, two 80-rating Sol/Luna judge artifacts, and a schema-v3
`comparative_acceptance_v2` PASS. The frozen V1 result remains unchanged.

The earlier V1-artifact V2 diagnostic remains implementation evidence only. This runbook creates a
separate fresh run under `evaluation/runs/eval-terra-v2`; it never writes to `eval-terra-v1`.

| Required dimension | Full-pipeline mean | Mean full − generic | 95% bootstrap CI | Result |
| --- | ---: | ---: | --- | --- |
| Evidence relevance | `5.000` | `+2.950` | `[2.675, 3.225]` | Pass |
| Specificity | `5.000` | `+0.825` | `[0.675, 0.950]` | Pass |
| Testability | `5.000` | `+1.450` | `[1.250, 1.650]` | Pass |
| Builder fit | `5.000` | `+0.450` | `[0.250, 0.650]` | Pass |

## Frozen defaults

| Setting | V2 default |
| --- | --- |
| Run directory | `evaluation/runs/eval-terra-v2` |
| Scenario set | `evaluation/scenarios.v1.json` |
| Generation model | `gpt-5.6-terra` |
| Vera judge model | `gpt-5.6-sol` |
| Marco judge model | `gpt-5.6-luna` |
| Generation seed | `28001` |
| Packet seed | `28002` |
| Judge seed | `28003` |
| Workers | `6` |
| Acceptance rule | `comparative_acceptance_v2` |
| Result schema | `3` |

The script writes these semantic settings, except worker count, to the ignored
`run-config-v2.json` manifest before execution. Worker count is execution-only and may be reduced
when resuming after rate limits. Changing any frozen setting requires a new run directory.

## 1. Inspect the complete workflow without running it

Run this first from the repository root:

```powershell
.\scripts\run-evaluation-v2.ps1 -DryRun
```

Dry run prints all four management commands and creates no directory, manifest, benchmark artifact,
or provider call. Confirm that it shows:

- The V2 run directory, never `eval-terra-v1`.
- Terra for generation, Sol for Vera, and Luna for Marco.
- Seeds `28001`, `28002`, and `28003`.
- `--acceptance-rule v2` on analysis.
- `--confirm-cost` only on generation and judging.

## 2. Run the fresh V2 workflow — completed

The full workflow makes approximately 200 generation calls and 40 judge calls. Only run it after
reviewing the dry-run output and intentionally accepting that cost boundary:

```powershell
.\scripts\run-evaluation-v2.ps1 -ConfirmCost
```

The script executes these stages in order:

1. `generate` — create or resume the 80-output private generation artifact.
2. `prepare` — deterministically create the blind packet and private variant map offline.
3. `judge` — create or resume 40 blinded judge checkpoints and materialize both rating artifacts.
4. `analyze` — apply explicit V2 scoring offline and write schema-v3 JSON and Markdown reports.

Paid commands still enforce their own `--confirm-cost` and credential checks. The script does not
weaken those management-command boundaries.

The official `eval-terra-v2` artifact is complete. Do not rerun it merely to reproduce the verdict;
preserve it unchanged. Use a new run directory for any later protocol or model change.

## 3. Resume one stage

Use the same run directory, models, and seeds. Successful provider checkpoints are reused.

```powershell
# Resume generation after a provider interruption.
.\scripts\run-evaluation-v2.ps1 -Stage generate -ConfirmCost

# Recreate the deterministic blind packet after generation is complete.
.\scripts\run-evaluation-v2.ps1 -Stage prepare

# Resume automated judging after a provider interruption.
.\scripts\run-evaluation-v2.ps1 -Stage judge -ConfirmCost

# Regenerate only the deterministic V2 reports.
.\scripts\run-evaluation-v2.ps1 -Stage analyze
```

After repeated `429` responses, reduce concurrency without changing run identity:

```powershell
.\scripts\run-evaluation-v2.ps1 -Stage generate -Workers 3 -ConfirmCost
.\scripts\run-evaluation-v2.ps1 -Stage judge -Workers 3 -ConfirmCost
```

Do not run two processes against the same generation or judge artifact simultaneously.

## 4. Inspect the result

The completed run writes:

```text
evaluation/runs/eval-terra-v2/
├── run-config-v2.json
├── private-generation.json
├── rating/
│   ├── blind-packet.json
│   ├── private-variant-map.json
│   ├── private-judge-run.json
│   ├── rating-judge-a.json
│   └── rating-judge-b.json
├── result.json
└── result.md
```

Review the human-readable report:

```powershell
Get-Content evaluation/runs/eval-terra-v2/result.md
```

Check the authoritative machine fields:

```powershell
$Result = Get-Content evaluation/runs/eval-terra-v2/result.json -Raw | ConvertFrom-Json
$Result.schema_version
$Result.acceptance_rule_version
$Result.acceptance_passed
$Result.dimensions | ConvertTo-Json -Depth 8
```

Expected protocol values are schema `3` and `comparative_acceptance_v2`. A `FAIL` verdict means the
run completed successfully but one or more required quality rules did not pass. Preserve that run;
do not change its scores, thresholds, or artifacts.

The official run reports schema `3`, `comparative_acceptance_v2`, and `acceptance_passed: true`.

## V2 acceptance rules

| Required dimension | Rule |
| --- | --- |
| Evidence relevance | Mean full-minus-generic lift at least `+0.500`; CI lower bound greater than `0` |
| Specificity | Mean full-minus-generic lift at least `+0.500`; CI lower bound greater than `0` |
| Testability | Mean full-minus-generic lift at least `+0.500`; CI lower bound greater than `0` |
| Builder fit | Full-pipeline mean at least `4.500`; paired CI lower bound at least `0` |

Novelty, feasibility, and economic leverage remain reported but non-gating.

## Safety and recovery

- The runner refuses any run directory outside ignored `evaluation/runs/`.
- The frozen `evaluation/runs/eval-terra-v1` directory is explicitly protected.
- A missing manifest beside existing artifacts is rejected rather than adopted.
- A manifest/model/seed/scenario mismatch is rejected; choose a new directory for changed settings.
- `generate`, `judge`, and `all` refuse execution without `-ConfirmCost`.
- `prepare` and `analyze` are offline and validate their required input artifacts.
- Provider failures retain completed checkpoints; rerun the same stage with identical frozen settings.

## References

- `scripts/run-evaluation-v2.ps1` — guarded orchestration entry point.
- `design.md`, sections 23.5–23.6 — authoritative V2 protocol.
- `TASKS.md`, PG-027 — task status and definition of done.
- `evaluation/README.md` — artifact privacy boundaries and underlying commands.
- `demo-steps.md` — V1 history and the diagnostic-only V2 reanalysis.
