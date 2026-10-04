# S-4: weekly meal plan with CP-SAT

Throwaway spike code (excluded from lint). The findings are in [docs/spikes/S-4-meal-plan.md](../../docs/spikes/S-4-meal-plan.md).

```
uv run --with ortools==9.15.6755 python spikes/s4-meal-plan/plan.py
```

It reads the catalog seed (`data/seeds/catalog/*.json`) and the seed households (`gharbasket.tools.seed.HOUSEHOLDS`), so no database is needed. `results.txt` is the output of the run in the note.
