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
  hypotheses with their intervals and a verdict on the primary metric, acceptance. `cpsat_v2` is
  paired with `cpsat` instead, over all weeks and over weeks ≥ 2 (before them it plans exactly as
  v1), with its own verdict: paired acceptance CI lower bound ≥ 0, hard rules kept, perishables
  ≤ v1 + 1, none below 5 seeds (protocol §7, 8 Oct).
- **RQ1 — pantry baseline:** predicting each daily-use item's consumption a week ahead, as quantity MAE
  over the weekly quantity (Σ |error| / Σ actual per household item, zero weeks kept): the family-size
  rule table (static) and a running mean of the weeks observed so far (the cold-start curve, k observed
  weeks → the error on week k + 1). M-A's targets are shown on the running mean for reference only;
  the twin (M10 F13) gets a verdict when it is scored the same way. On the twin's scope (milk and
  curd, their daily-use share) a reference arm with no target sits next to it: the twin from cohort
  priors learned (v1, NumPyro) on another seed's households (`--learned-from`, by default the next
  of the five seeds; `--no-learned` skips it; protocol §7, 8 Oct).
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

Also in a report: **RQ3 — forecast v0 vs v1** (the M-E backtest on simulated demand: the statistical v0, the
LightGBM v1 and the per-seed champion; WAPE, MASE, pinball, coverage, waste, stock-out and newsvendor cost,
v1 − v0 paired by seed) and, when a basket-ranker run's `explanations.json` is passed with
`--ranker-explanations`, **ranker explanations** (global mean |SHAP| per feature and sampled decisions).

## Published reports

### 2026-10-28-77bf7d87 (MS-C)

`just eval --ranker-explanations <explanations.json>` at commit `77bf7d87` (main after #397), full run, the
protocol's seeds 7, 11, 13, 17, 19, 5 weeks from 2026-10-05, default adoption, shocks on, forecast 20 weeks
from 2026-09-07, pantry learned-priors reference arm on the next seed's households (19 → 7).
The ranker explanations come from `train_ranker` on a fresh local database (migrated, `just seed`,
`just sim --seed 7 --weeks 8 --write-db`; one seed, 87 holdout baskets, 4,698 candidates), not from the five
seeds. The run took roughly 4 CPU-hours on a laptop. The assistant eval (M13) was not run.

| Question | Result (mean ± 95 % CI over 5 seeds) | Target | Verdict |
|---|---|---|---|
| RQ2 acceptance, CP-SAT v1 | 0.796 ± 0.015 (v2 0.812, rules 0.827, greedy 0.829, random 0.707) | ≥ 0.70 and above greedy and random | **No**: above random (+0.089 ± 0.009), but 0.033 ± 0.009 *below* greedy |
| RQ2 other M-C hypotheses | distinct perishables 3.7 (greedy 6.2, random 11.2); 40 % fewer than greedy; objective gain over greedy 74 %; hard-rule violations 0 | ≥ 25 % fewer; ≥ 30 % gain; 0 | hold |
| RQ2 planner v2 vs v1 | +0.027 ± 0.010 acceptance from week 2 (+0.016 ± 0.006 over all weeks); perishables +0.06 | CI lower bound ≥ 0, perishables ≤ +1 | holds |
| RQ1 pantry twin v0 (milk, curd), MAE after 4 weeks | 0.227 (rule table 0.194, running mean 0.089; 80 % interval covers 98.5 %); twin from learned priors 0.300 (reference) | ≤ 0.15 | **No**: keep `PANTRY_TWIN_ENABLED` off |
| RQ1 pantry, all daily-use items | rule table 0.294; running mean 0.127 → 0.087 over weeks 1–4 | reference only | no verdict |
| RQ4 fair assignment | Gini: greedy 0.420, λ=0 0.435, λ=500 0.396, λ=700 0.262 ± 0.189 (cost +2.5 % ± 8.9 %), λ=1000 0.201 ± 0.205 (+6.5 % ± 8.3 %), λ=2000 0.029 (+23.5 % ± 11.4 %) | Gini ≤ 0.25 at ≤ +5 % cost | **No**: no λ has both upper bounds inside the target |
| RQ3 forecast v1 − v0 | WAPE +0.015 ± 0.008 (v0 0.188, v1 0.203); newsvendor cost −0.021 ± 0.052 (v0 0.146, v1 0.125); stock-out 0.453 → 0.376; q_τ coverage 0.42 / 0.44; waste 0.021 / 0.025 | M-E: WAPE ≤ 25 %, stock-out ≤ 3 %, waste ≤ 5 %, WAPE ≥ 10 points better than seasonal naive | WAPE and waste are under their ceilings for both, stock-out is far over (38–45 %); the report has no seasonal-naive or buy-last-week arm yet, so no M-E verdict; v1 is not more accurate than v0 on WAPE, and its cost and stock-out gains are within noise |
| Ranker, top features (mean \|SHAP\| share) | `drafted` 32.8 %, `popularity` 30.5 %, `price_paise` 25.9 %, `weeks_since` 5.4 %, `frequency` 4.9 %, `removed_last` 0.5 % | none | descriptive |

Limits: all data is synthetic (ADR-0008) and the simulator's households accept dishes and use milk by rules
the planner and the rule table partly share, so the planner and pantry numbers say little about real
families. The forecast's v0 and champion are picked on the windows they are scored on (optimistic, as the
forecast section says); v1 is fixed, so the comparison favours v0. The assignment sweep's CIs at λ ≥ 700 are
wide (seeds differ a lot), so "no verdict above 5 % cost" is also "not enough power". The ranker
explanations come from one seed's boxes and say what the model leans on in the simulator, in which
`drafted` is the planner's own draft and `popularity` and `price_paise` are simulator-built; the sampled
decisions list product ids of a scratch database, which no longer exists.

Synthetic data only (ADR-0008). Numbers are mean ± 95 % CI across seeds. `just eval --smoke` is the CI
check (one seed, one week, no CP-SAT planner arm, the λ sweep on that seed's small week, under a
minute); the committed reports come from full runs.
