# Spike S-4: weekly meal plan with CP-SAT

**Question:** can CP-SAT plan a week of meals from about 20 recipes, with Jain and fasting rules, within 5 s?
**Answer: yes (passed).** An optimal plan takes 10 to 50 ms for the seed households, and about 0.07 s with 126 recipes. The real risk is not the solver but a **menu too small for strict households**. A fixed fallback order handles that.

**Owner:** Prathamesh Relekar · **Date:** 4 Oct 2026 · **Code:** `spikes/s4-meal-plan/plan.py` (output in `results.txt`)
**Run:** `uv run --with ortools==9.15.6755 python spikes/s4-meal-plan/plan.py` from the repository root.

## Setup
- **Machine:** Apple M2, 8 cores. OR-Tools 9.15 (CP-SAT), Python 3.13, 8 solver workers unless stated otherwise.
- **Data:** the real seed catalog, i.e. 21 recipes (22 minus chapati, which is a side, not a meal), with ingredients, the 68 products and the seed hub's prices. The four seed households (Priya, Sunita, Ramesh, Meena) plus "Priya + Tuesday fast". The week is Mon 12 Oct 2026, which falls in Navratri in the (unverified) seed calendar.
- **Model** (handbook 08, M-C): one choice per (day, slot), for breakfast, lunch and dinner; tiffin is allowed only on the household's no-cook days.
  - **Hard rules:**
    - diet: no egg dishes for veg households;
    - Jain households only Jain-OK dishes;
    - diabetic members: lunch and dinner diabetic-OK;
    - on a fasting day: no onion or garlic if the fast says so, and at least one fasting-OK dish;
    - cook time per meal within the weekday or weekend limit;
    - no-cook days: tiffin for lunch and dinner, and a no-cook breakfast;
    - no repeats within 2 days (breakfast) or 3 days (lunch and dinner, which share their window);
    - the week's food cost within the Budget Lock.
  - **Soft goals:** 3 × cuisine preference + 5 × variety − 4 × the number of different perishables bought.
- **Checks:** every plan is re-verified by an independent checker, and compared with 20 random valid plans and a greedy plan.

## Results
| Household | Rules that bind | Result | Time | Score (CP-SAT / random / greedy) | Perishables (CP-SAT / random) |
|---|---|---|---|---|---|
| Priya (4, veg) | Sunday no-cook, budget | Optimal, all rules hold | 0.05 s (first run) | **98** / 68.5 / 67 | **7** / 10.2 |
| Sunita (2, egg) | none special | Optimal, all rules hold | 0.01 s | **97** / 57.5 / 75 | **5** / 11.3 |
| Priya + Tuesday fast | fasting day | Optimal, all rules hold | 0.01 s | **98** / 70.8 / 67 | **7** / 10.3 |
| Ramesh (2, diabetic) | diabetic, 20-min weekday meals | **Infeasible** | < 0.01 s | n/a | n/a |
| Meena (3, Jain) | Jain plus Navratri every day | **Infeasible** | < 0.01 s | n/a | n/a |

- **Quality:** CP-SAT beats both baselines by 30 to 70 % on the score, and buys **30 to 55 % fewer different perishables**, which is the zero-waste USP. The greedy plan is cheaper but repetitive (variety 8–9 against 15).
- **Scale:** Priya with 21 / 63 / 126 recipes takes 0.011 / 0.028 / 0.074 s (median of 5, all optimal). The pilot target of at least 120 recipes is far inside 5 s.
- **One worker** (a small server CPU): the optimum score (98) is found within 0.25 s, but CP-SAT cannot *prove* it optimal within 5 s. In production, use a 1 to 2 s limit and accept FEASIBLE.

## Why two households were infeasible, and the fix
The diagnosis switches off one rule at a time and re-solves.
- **Ramesh:** the diabetic rule plus 20-minute weekday meals leaves 2 breakfasts and 3 lunch/dinner dishes. That is too few for no repeats within 3 days. It is plannable if either cook time or the repeat rule is relaxed.
- **Meena:** Jain plus a fasting dish on each of the 7 Navratri days leaves only fruit bowl and kakdi koshimbir as Jain fasting dishes, which cannot cover 7 days without repeats. It is plannable if the repeat rule is relaxed.

**Design adopted for the planner (M10):** relax *comfort* rules in a fixed order (repeats, then cook time, then budget), **never** the safety or religious rules (diet, Jain, diabetic, onion/garlic on fasts, a fasting dish). A relaxed repeat rule still keeps a dish from appearing twice on the same day. Both households then get a valid plan in about 5 ms, and the plan says what was relaxed (a reason code for the Week screen). The plans are monotonous, though: the real fix is content.

## What this asks of others
- **Content** (Rhythm Thakur, Manish Raje, from the catalog seed):
  - at least 6 quick (≤ 20 min) diabetic-friendly lunch/dinner dishes and 3 breakfasts;
  - at least 5 Jain fasting dishes (for example rajgira, bhagar/varai, singhada, sama rice);
  - the verified festival calendar (Ekadashi and others).
- **M1 spec:** confirm that `weekday_cook_minutes` means *per meal*, which this spike assumes. Per day, most households would be infeasible.
- **Sides:** chapati and rice go with a sabzi or a dal; the planner should pair a side with a main instead of treating each as a meal.

## Not covered yet (planned in M10)
- nutrition targets per member (needs IFCT 2017 data on products);
- the price-spike penalty (needs price history);
- preferences learned from feedback (M-B) instead of cuisine match;
- quantities per member (M-A);
- kits on busy days.

## Decision
The spike passed, so **no ADR change is needed**. CP-SAT stays the meal planner (ADR-0007). Next: `backend/ml` exposes `/plan/week` with this model plus the fallback order. The API calls it with a timeout and a rule-based fallback, because ML never blocks a customer.
