# Evaluation reports

`just eval` (the harness in `backend/training/src/gharbasket_training/eval`) writes one HTML and one
JSON report here per run, named `<date>-<commit>`, following the pre-registered protocol in
[`docs/research/eval-protocol.md`](../research/eval-protocol.md). The same numbers go to
`intel.model_metrics` through `POST /v1/internal/intel/model-metrics` with `--push`.

What a report holds (v0, October 2026):
- **RQ2 — meal planner A/B:** `cpsat` (v1) and `cpsat_v2` (the liking term from each household's observed acceptance, M10 §3.2 v2) against the `rules` fallback and two baselines the simulator
  provides, `greedy` (the cheapest allowed dish per slot) and `random` (a random valid plan), over the
  protocol's five seeds: acceptance, edits per box, distinct perishables, variety, preference, relaxed
  and infeasible weeks, cost and hard-rule violations (`planner.check()`, must be 0); each arm against
  each baseline as the per-seed paired difference (mean ± 95 % t-CI, acceptance first); the M-C
  hypotheses with their intervals and a verdict on the primary metric, acceptance.
- **RQ1 — pantry baseline:** predicting each daily-use item's consumption a week ahead, as quantity MAE
  over the weekly quantity (Σ |error| / Σ actual per household item, zero weeks kept): the family-size
  rule table (static) and a running mean of the weeks observed so far (the cold-start curve, k observed
  weeks → the error on week k + 1). M-A's targets are shown on the running mean for reference only;
  the twin (M10 F13) gets a verdict when it is scored the same way.
- **Provenance:** the simulator version and source digest, and the sha256 of cohorts.json,
  shocks.json and every file in data/seeds/catalog, in the report and in each metric row's `details`
  (with the per-seed values).

- **RQ4 — fair kitchen assignment:** per seed one simulated week (7 evenings of tiffin orders, each
  kitchen's earnings carried forward from 0) under greedy nearest kitchen and the CP-SAT model at
  λ ∈ {0, 100, 200, 300, 500, 700, 1000, 2000} paise per portion of imbalance: the Gini of the week's
  earnings (eligible kitchens; every kitchen beside it), the cost a portion in ₹ (distance + quality
  penalty) and the rider's part, km a portion, and each λ's per-seed cost against greedy. Seeds are
  pairs: a seed where any point did not solve is dropped for all and listed. The M-G target (Gini ≤
  0.25 at ≤ +5 % cost) holds at a λ > 0 when both 95 % CI upper bounds are within it; no verdict
  below 5 paired seeds.

`just eval-assistant` (M13) is separate: it scores Ghar Saathi on `data/eval/assistant_utterances.jsonl` per
candidate model (intent accuracy, slot F1, no write on injection fixtures, cost a turn with cache reads
and writes priced apart) and writes
`assistant-<date>.json` here; it needs `ANTHROPIC_API_KEY` and costs real tokens.

Synthetic data only (ADR-0008). Numbers are mean ± 95 % CI across seeds. `just eval --smoke` is the CI
check (one seed, one week, no CP-SAT planner arm, the λ sweep on that seed's small week, under a
minute); the committed reports come from full runs.
