# Evaluation reports

`just eval` (the harness in `backend/training/src/gharbasket_training/eval`) writes one HTML and one
JSON report here per run, named `<date>-<commit>`, following the pre-registered protocol in
[`docs/research/eval-protocol.md`](../research/eval-protocol.md). The same numbers go to
`intel.model_metrics` through `POST /v1/internal/intel/model-metrics` with `--push`.

What a report holds (v0, October 2026):
- **RQ2 — meal planner A/B:** `cpsat` against the `rules` fallback and two baselines the simulator
  provides, `greedy` (the cheapest allowed dish per slot) and `random` (a random valid plan), over the
  protocol's five seeds: acceptance, edits per box, distinct perishables, variety, preference, relaxed
  and infeasible weeks, cost and hard-rule violations (must be 0); the M-C hypotheses with a yes/no.
- **RQ1 — pantry baseline:** predicting each daily-use item's consumption a week ahead: the family-size
  rule table (static) and a running mean of the weeks observed so far (the cold-start curve, k observed
  weeks → the error on week k + 1); the twin (M10 F13) is scored the same way when it exists.

Synthetic data only (ADR-0008). Numbers are mean ± 95 % CI across seeds. `just eval --smoke` is the CI
check (one seed, one week, no CP-SAT, under a minute); the committed reports come from full runs.
