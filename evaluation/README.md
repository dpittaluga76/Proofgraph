# Comparative evaluation

PG-027 is an internal, reproducible command-line benchmark. It compares four frozen generation
variants over the 20 synthetic scenarios in `scenarios.v1.json`. No evaluation controls, provider
metadata, variant identities, or rater identities are exposed in the product UI.

## Artifact boundaries

- `private-generation.json` contains variant labels, provider response IDs, and token usage. Keep it
  private.
- `blind-packet.json` contains only scenarios, evidence, opaque output IDs, normalized opportunities,
  and the fixed rubric. Give this file to each rater.
- `private-variant-map.json` reverses the blinding. Never give it to either rater.
- `rating-rater-a.json` and `rating-rater-b.json` are independent templates. Each rater supplies a
  distinct `rater_id`, scores every dimension from 1 through 5, and may add notes.
- `adjudications.json` begins empty. Every rater disagreement of at least two points must receive one
  final score and rationale while the two original ratings remain unchanged.
- `result.json` is authoritative. It retains provenance, all original scores, adjudications,
  effective scores, paired scenario differences, and deterministic confidence intervals.

The ignored `evaluation/runs/` directory is the recommended location for all generated, private,
and human-rating artifacts. Only the synthetic scenario set and this workflow documentation are
checked in.

## 1. Explicit cost-bearing generation

Set the server-side `OPENAI_API_KEY`, choose a new run directory, and acknowledge the cost. With 20
scenarios, the frozen paths make 200 Responses API calls and produce 80 normalized outputs. The
command writes after every completed scenario/variant, so rerunning the same command resumes without
repeating completed calls.

```powershell
uv run python manage.py generate_evaluation_variants `
  --output evaluation/runs/eval-v1/private-generation.json `
  --seed 27001 `
  --model gpt-5.6 `
  --confirm-cost
```

Do not change the model, seed, prompt suite, strategy catalog, or scenario set while resuming an
existing artifact. The command rejects a configuration mismatch. API storage is disabled for every
benchmark request.

## 2. Blind packet preparation

This step is deterministic and offline:

```powershell
uv run python manage.py prepare_evaluation_packet `
  --generation evaluation/runs/eval-v1/private-generation.json `
  --output-dir evaluation/runs/eval-v1/rating `
  --seed 27002
```

Distribute separate copies of `blind-packet.json` and the appropriate rating template to two raters.
Keep the run directory's private generation artifact and variant map away from them until both rating
artifacts are final.

## 3. Adjudication and analysis

Compare completed rating files without revealing the variant map. For every score difference of two
or more, add exactly one entry to `adjudications.json` using the opaque output ID, dimension,
adjudicator ID, resolved 1–5 score, and rationale. Analysis rejects missing or extra adjudications,
blank or duplicate rater IDs, incomplete dimensions, invalid scores, and mismatched artifacts.

```powershell
uv run python manage.py analyze_evaluation `
  --packet evaluation/runs/eval-v1/rating/blind-packet.json `
  --private-map evaluation/runs/eval-v1/rating/private-variant-map.json `
  --generation evaluation/runs/eval-v1/private-generation.json `
  --rater-a evaluation/runs/eval-v1/rating/rating-rater-a.json `
  --rater-b evaluation/runs/eval-v1/rating/rating-rater-b.json `
  --adjudications evaluation/runs/eval-v1/rating/adjudications.json `
  --output-json evaluation/runs/eval-v1/result.json `
  --output-markdown evaluation/runs/eval-v1/result.md
```

The full pipeline passes PG-027 only if evidence relevance, specificity, testability, and builder fit
each improve by at least 0.5 mean points over generic generation and each paired scenario-bootstrap
95% confidence interval has a lower bound above zero. All seven dimensions are still reported.
