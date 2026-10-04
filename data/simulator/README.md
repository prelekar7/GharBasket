# data/simulator: synthetic households and ground truth (M10, ADR-0008)

```
just up && just migrate && just seed     # the hub, societies, catalog and prices
just sim                                 # seed 7: households + 8 weeks of behaviour (~1 min)
just sim --seed 11 --weeks 4 --planner rules
```

**Deterministic:** the same `--seed` and the same catalog give the same households, plans, choices and consumption. The CP-SAT planner runs in its deterministic mode for this.

## What one run does
1. **Households.** It samples about 10 % of the flats in the seed societies that have a hub (`--adoption`) from the cohorts in [`cohorts.json`](cohorts.json): size, diet, Jain, cuisines, fasts, a diabetic elder, cook time, no-cook Sunday and the Budget Lock.
   - Each one is onboarded through M1's services, exactly as the app does with `POST /v1/households`, with `data_source='synthetic'`.
   - Phones are +91 98001 00001 onwards (reserved for the simulator).
   - Running it again keeps existing households.
2. **Weeks.** Each week:
   - the planner (`cpsat` or the `rules` fallback) plans 21 meals, with festival fasts from the catalog calendar;
   - the household accepts each dish with probability sigmoid(accept bias + taste), or swaps it for its favourite allowed dish under the same rules;
   - sometimes it skips the week's box but still eats.
3. **Ground truth** goes to `data/sim_truth/seed-<n>/` (git-ignored) as Parquet:
   - the latent daily-use rates and tastes;
   - the plans and actions;
   - weekly consumption per product.
   - The columns are listed in [`truth.py`](src/gharbasket_sim/truth.py).

## Not yet (v0)
- **Through M3 and M4:** the weeks reach the database through those modules (boxes, edits, skips, orders) once they exist; until then they live only in the truth files.
- **Later:** price shocks, churn, partners and donations (handbook 05 §5.11, items 5–6).
- **Review:** cohort numbers are assumptions until Rhythm Thakur and Manish Raje check them against the UX interviews.
