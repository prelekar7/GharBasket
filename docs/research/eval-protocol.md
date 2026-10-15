# Evaluation protocol (pre-registered)

**Owner:** Prathamesh Relekar · **Reviewers:** Rhythm Thakur, Manish Raje · **Registered:** 4 Oct 2026, before any learned model was trained.
**Rule:** anything below changes only by a dated entry in the change log at the end, never silently. Results are reported against the protocol as it stood when the run started.

## 1. Research questions
| | Question | Algorithm | Depth (ADR-0007) |
|---|---|---|---|
| RQ1 | Does a hierarchical Bayesian pantry twin cut quantity error and waste against family-size rules, and how many weeks does it need to beat them (cold start)? | M-A | deepest |
| RQ2 | Does constraint-based weekly planning with ingredient reuse raise plan acceptance and cut distinct perishables against greedy and random valid plans, under Indian dietary rules (Jain, fasting, diabetic)? | M-C | deepest |
| RQ3 | Does forecasting only the uncommitted part of demand with newsvendor buffers cut waste and stock-outs against "buy last week"? | M-E | standard metrics |
| RQ4 | What does fairness cost in home-kitchen assignment: the Gini of weekly earnings against ₹ per portion along a λ sweep? | M-G | standard metrics |

## 2. Data
- **Synthetic:** the household simulator (`data/simulator`, ADR-0008), `data_source = synthetic`. Every experiment runs on **at least 5 seeds** (7, 11, 13, 17, 19); results are mean ± 95 % CI across seeds. The simulator version, cohorts file hash and catalog seed hash are recorded with every run.
- **Pilot:** consented households (`data_source = pilot`), reported **separately** from synthetic, never pooled in a headline number.
- **External:** Agmarknet (data.gov.in) daily mandi prices from 4 Oct 2026, the India Data Portal 2019–2025 history, IFCT 2017, ICMR-NIN RDA 2020, Open-Meteo, the panchang calendar; each with its fetch date in the run record.
- **Realism check:** one table compares simulated per-capita consumption of staples, vegetables, fruit and milk with NSSO HCES 2022–23 (Maharashtra urban); the gap is reported, not hidden.

## 3. Splits and leakage
- Time-based splits only. Training ends at a cut-off; the holdout is the following weeks. Random splits are never used on time-ordered data.
- Features are computed as of the cut-off (`intel.feature_snapshots`); a leakage test asserts no feature uses later rows.
- Protocols: M-A weekly rolling evaluation; M-B leave-last-basket-out; M-C simulator A/B over all household-weeks; M-E rolling-origin backtest (origins every week, 2-week horizon); M-F fixed-seed benchmark set; M-G λ sweep on simulated weeks.

## 4. Metrics, baselines and targets
Targets are **hypotheses** set before training. A model that misses its target is still reported.

| Algorithm | Primary metric | Guardrails | Baselines | Target (hypothesis) |
|---|---|---|---|---|
| M-A pantry twin | quantity MAE as % of weekly quantity, after 4 weeks | 80 % interval coverage in 75–85 %; "ran out" + "wasted" rate | family-size rule table (M3's rule box) | MAE ≤ 15 % (rule table ≈ 30 %); cold start < 4 weeks |
| M-B basket ranker | NDCG@10 (leave-last-basket-out) | Recall@10; edits per box; calibration | repeat last week; top frequency; TIFU-KNN | NDCG@10 ≥ repeat-last-week + 0.05; Recall@10 ≥ 0.6 |
| M-C meal planner | plan acceptance in the simulator | 100 % hard constraints; distinct perishables; variety index; nutrition adherence; solve time ≤ 5 s | random valid plan; greedy | acceptance ≥ 70 %; score ≥ +30 % vs greedy; ≥ 25 % fewer distinct perishables |
| M-E forecast + newsvendor | WAPE on uncommitted demand (2-week horizon) | MASE; pinball loss (q50, qτ); stock-out %; waste % | seasonal naive; buy last week | WAPE ≤ 25 % and ≥ 10 points better than seasonal naive; stock-out ≤ 3 %; waste ≤ 5 % |
| M-F routing | ₹ per order | km per order; on-time %; solve time ≤ 10 s per wave | one trip per order; nearest neighbour | on-time ≥ 95 %; ₹/order ≥ 10 % below nearest neighbour |
| M-G kitchen assignment | Gini of weekly partner earnings at a chosen λ | ₹ per portion; rating floor kept | greedy nearest kitchen | Gini ≤ 0.25 at ≤ +5 % cost against greedy |
| M13 assistant | intent accuracy on the 200-utterance set | slot-filling F1; cost per turn; zero unauthorised actions on injection fixtures | — (three Claude models compared, ADR-0013) | accuracy ≥ 90 %; F1 ≥ 0.85; ≤ $0.01 per turn |
| Monitoring | WAPE drift against the last eval report | acceptance, waste | — | alert when WAPE worsens by > 10 points |

## 5. Ablations (cheap, because the solvers are fast)
- M-C: drop one objective term at a time (ingredient reuse, nutrition closeness, price-spike penalty, variety) over all household-weeks.
- M-A: cohort prior on/off; online update on/off.
- M-E: with and without price, festival and weather features; conformal vs model intervals.
- M-G: λ ∈ {0, 100, 200, 300, 500, 700, 1000, 2000} paise per portion of imbalance (M10 §3.5) for the Pareto figure.

## 6. Reporting
- `just eval` writes an HTML report (marimo) to `docs/eval/<date>-<commit>.html` and rows to `intel.model_metrics` with `split`, `data_source`, seed, simulator and model versions.
- Figures promised: M-A cold-start curve; M-C acceptance and perishables bars with CIs; M-E rolling WAPE; M-G Pareto curve; M-B SHAP top features; M13 model comparison; the HCES realism table; a replay of real Agmarknet price spikes through the planner.
- Synthetic and pilot numbers sit in separate tables in every figure and in the paper.

## 7. Change log
| Date | Change | Why |
|---|---|---|
| 2026-10-04 | Protocol registered. | Before any model training. |
| 2026-10-08 | How the v0 harness computes what §4 registers (no target changed). M-A: quantity MAE as a share of weekly quantity = Σ \|error\| / Σ actual per household item over the scored weeks, zero weeks kept, then the mean over items; on the cold-start curve the one scored week is scaled by the item's mean weekly quantity. Cold start = the fewest observed weeks after which an estimator's MAE is below the rule table's. M-C: each arm (CP-SAT, the rules fallback) against each baseline (greedy, random) as the per-seed paired difference, mean ± 95 % t-CI, acceptance first; "raises acceptance against greedy and random" holds when both paired intervals lie above 0, and the M-C verdict is on acceptance (≥ 70 % and above both baselines, hard rules kept). Hard rules are counted with the planner's own `check()` (allowed dishes, no-repeat windows, a fasting dish on each fasting day, the weekly budget) under the relaxation each plan records; the greedy and random baselines keep the same rules, relaxing comfort rules in CP-SAT's order. | The first report (2026-10-04, commit 90a70a2) scored a MAPE with zero-actual weeks dropped, compared the planners to the baselines without paired intervals, and checked hard rules per dish only, so baseline plans that missed a fasting dish or the budget counted as valid; it is replaced by `docs/eval/2026-10-07-23823ad` (run at the fixed commit, dated in UTC). That run: the running mean reaches 0.086 after 4 weeks and beats the rule table (0.29) after 1 week in every seed. |
| 2026-10-08 | M-A: the running-mean baseline's MAE after 4 weeks and its cold start are shown for reference only, with no verdict, until a model arm (the twin) is scored. The ≤ 15 % target stands but is to be revisited: the simulator draws daily use with lognormal noise σ = 0.1 around a fixed weekly rate, which makes ≤ 15 % easy for a naive running mean. | A target a naive baseline meets says little about the twin; the target itself is not changed after seeing results. |
| 2026-10-08 | §6: v0 reports are self-contained HTML tables (and JSON) with the simulator version and the sha256 of the cohorts file, shocks file, simulator source and seed catalog; the marimo report and the promised figures come later. | The harness's first version; figures follow once the model arms exist. |
| 2026-10-08 | M-E backtest details fixed before any M-E result: at most the latest 8 weekly origins (at least 3, else no backtest); WAPE pooled over both steps of the 2-week horizon; the champion is chosen among the models and both baselines; q_τ by split conformal on per-series scaled residuals with nested windows (each window calibrated only on targets known at its origin), its coverage reported next to pinball and MASE (scaled by the in-sample week-on-week change); weekly series need 7 weeks (StatsForecast's AutoETS minimum). | §3 named the origins and horizon but not their number, the pooling or the interval evaluation; a v0 draft (5 Oct, never run on data) had silently used a 1-week horizon with 3 origins. |
| 2026-10-08 | M-A v0 is scored on the pantry twin's scope: the perishable daily-use items (milk, curd) and their daily-use share (actual daily use, without the recipes' need), with the same MAE (Σ \|error\| / Σ actual per household item, zero weeks kept) next to the rule table and a running mean on that scope, and the 80 % interval's coverage over every predicted week. The ≤ 15 % MAE target after 4 weeks gets a verdict on the twin (none when a run is shorter than 5 weeks); the cold start stays on the running-mean baseline for reference. | The twin v0 drafts only milk and curd (staples carry over and stay on the rule table until v1 models stock), so it is scored on what it predicts; with a model arm scored, the MAE target has a verdict. |
