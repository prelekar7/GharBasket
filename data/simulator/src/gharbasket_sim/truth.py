"""Ground truth as Parquet (handbook 05 §5.11), one folder per run: data/sim_truth/seed-<n>/.

- households (one row per household): key, household_id, society_id, cohort, size,
  adult_equivalents, diet, jain, diabetic, cuisines, fasts, weekly_budget_paise, accept_bias,
  skip_week
- daily_use (household x product): household, product, unit, weekly_qty (the latent rate)
- taste (household x recipe): household, recipe, taste (a logit)
- weeks (household x week): household, week, monday, ordered, plan_status, relaxed, planner,
  fasting_days
- meals (household x day x slot): household, week, date, slot, planned, action, final
  (action: accept, edit, tiffin; own or none when there was no plan)
- consumption (household x week x product): household, week, product, unit, qty

`manifest.json` records the run's arguments and row counts.
"""

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def write(out: Path, tables: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        pq.write_table(pa.Table.from_pylist(rows), out / f"{name}.parquet")
    counts = {name: len(rows) for name, rows in tables.items()}
    text = json.dumps({**manifest, "rows": counts}, indent=2, sort_keys=True, default=str)
    (out / "manifest.json").write_text(text + "\n")


def read(out: Path, name: str) -> list[dict[str, Any]]:
    return pq.read_table(out / f"{name}.parquet").to_pylist()
