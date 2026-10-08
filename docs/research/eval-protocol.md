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
