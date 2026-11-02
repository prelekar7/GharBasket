# data/simulator: synthetic households and ground truth (M10, ADR-0008)

```
just up && just migrate && just seed     # the hub, societies, catalog and prices
just sim                                 # seed 7: households + 8 weeks of behaviour (~1 min)
just sim --seed 11 --weeks 4 --planner rules
just sim --weeks 4 --write-db                # v1b: the weeks go through M3, M4 and M5
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
3. **The box and the pantry (v1a).** Each week the household's box is drafted the way M3 drafts it (the planned dishes' ingredients plus the family-size rule table, in steps at this week's prices), then the household edits it: ingredients of dishes it rejected go and its own dishes' come in (`taste`), items its pantry still holds go or shrink (`stock`), items whose price spiked are swapped for a cheaper one or cut (`price`, by the cohort's `price_sensitivity`), sometimes a liked fruit is added. The approved box is delivered into a pantry; the week's cooking draws it down; what runs short is bought elsewhere, perishables left over are waste, and on some items (`feedback_rate`) the household taps *ran out*, *wasted* or *just right*.
4. **Prices** follow [`shocks.json`](shocks.json): episodes such as tomato ×1.8 for three weeks (`--no-shocks` for flat prices).
5. **Ground truth** goes to `data/sim_truth/seed-<n>/` (git-ignored) as Parquet:
   - the latent daily-use rates and tastes;
   - the plans and actions;
   - weekly consumption per product;
   - boxes, box lines, edits, pantry rows with the taps, and prices (v1a).
   - The columns are listed in [`truth.py`](src/gharbasket_sim/truth.py).

6. **Through the modules (`--write-db`, v1b).** Each household subscribes (wallet); a week at a time, M3 generates the week's cycle at T-3 18:00 IST once the week before has been replayed, so its carry-over counts what was delivered (the CP-SAT planner runs in process behind the gateway for `cpsat`, the rules fallback for `rules`), and the household's behaviour runs against *that* plan and *those* lines: its box edits become `PATCH` line ops (with `edit_feedback`), it approves (an M4 order) or skips, its wallet is topped up to cover the box (one week in twenty is left short), the cut-off (the cycle's `cutoff_at`) debits and locks or fails and cancels, the outbox is relayed so M3's consumers mirror the order on the cycle, the order goes packed → dispatched → at society → delivered, and the pantry taps become `pantry_feedback`. The modules' clocks are set to the simulated instant for each step. Before the cut-off the week's add-ons go on the approved box as add-on lines (M4 `set_addons`, priced from the hub's list), so the top-up and the debit cover them: the forecast's uncommitted demand ([ADR-0031](../../docs/adr/0031-uncommitted-demand-is-add-on-lines-and-tiffins.md)). They come from [`uncommitted.py`](src/gharbasket_sim/uncommitted.py), the one derivation the eval harness's RQ3 also calls: a pantry shortfall becomes an add-on with probability 0.5 × the price multiplier⁻¹ × (1 + 0.1 per festival day), only on a box the household ordered, never for a product already on it, within M4's limits (20 lines, 20 steps); a no-cook slot becomes a tiffin with probability 0.7, which is not placed yet (no tiffin menus, M2 §3.4). `--no-uncommitted` leaves the add-ons out. `outcomes.parquet` records the cycle, order, statuses, ops, refusals and add-on lines and steps per household-week. Prices through the modules are the hub's list prices (the shock episodes shape behaviour, not the price list).

## Staging demo data
The simulator writes only to the local Docker database. Staging gets its demo data through `scripts/staging-demo.sh` (`just staging-demo`, handbook 12 §12.3), which runs the seed and `--write-db --pause-after` against Neon. Its guards:
- a shared database is refused unless the run has both `--allow-remote` and `GB_ALLOW_REMOTE_SEED=<the database host>`, typed out by the owner;
- a Neon restore-point branch (`before-demo-<UTC time>`) is made before anything is written;
- the weeks are the last full weeks before this IST week, so every simulated delivery is in the past;
- a week the simulator already replayed is never simulated twice (`--add-weeks` adds only the weeks after the latest one);
- a run that would take an `unlocking` society to its threshold is refused (simulated households count towards the unlock, M9 §3.6);
- on a shared database a +91 98001 phone that belongs to a real account stops the run instead of being taken over;
- the simulator shows each household only its own draft (`send_box_ready_notices(cycle_ids=…)`), never another household's;
- `--pause-after` pauses the simulated subscriptions at the end, so staging's weekly job leaves them alone; the next `--write-db` run resumes them;
- the ground truth goes to `data/sim_truth/staging-<UTC time>/`, not over a local `seed-<n>` run.

The helpers it calls are in [`staging.py`](src/gharbasket_sim/staging.py): `host`, `window`, `estimate`, `unlock` and `counts`. Simulated people get no push or WhatsApp on staging: M11's `send_deliveries` skips synthetic accounts outside dev and test, and their inbox is kept. The data trains the ranker, the pantry refit and, with about 11 weeks of history, the forecast (`--weeks 12` for a run meant to train it, handbook 12).

## Not yet
- **Later:** real Vashi price episodes replayed from the mandi history (M10 F19), churn, partners and donations (handbook 05 §5.11, item 6).
- **Review:** cohort numbers, `price_sensitivity`, `feedback_rate` and the shock episodes are assumptions until Rhythm Thakur and Manish Raje check them against the UX interviews and the 2023–25 APMC prices.
