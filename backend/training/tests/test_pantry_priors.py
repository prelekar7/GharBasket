"""The pantry twin v1: NumPyro recovers cohort priors from simulated boxes, the learned priors
beat the rule table leave-last-week-out, the registry document, `just promote` and the job's
read of locked boxes."""

import json
import math
import random
from pathlib import Path

import pytest
from gharbasket_training import pantry_priors
from gharbasket_training.jobs import promote
from gharbasket_training.jobs import refit_pantry as job
from gharbasket_training.lake import LocalLake
from sqlalchemy.ext.asyncio import AsyncSession

STEP = 500.0  # ml of milk in one step


def synthetic(seed: int = 7, weeks: int = 6) -> list[pantry_priors.Observation]:
    """Three cohorts of four households each; the truth is 2, 4 and 6 steps of milk a week."""
    rng = random.Random(seed)
    truth = {"small": 2.0, "medium": 4.0, "large": 6.0}
    out = []
    for cohort, mean in truth.items():
        for h in range(4):
            rate = mean * rng.uniform(0.8, 1.2)
            for w in range(weeks):
                count = sum(1 for _ in range(60) if rng.random() < rate / 60)  # ~Poisson
                out.append(
                    pantry_priors.Observation(
                        f"{cohort}-{h}", cohort, "milk", w, count * STEP, None, STEP
                    )
                )
    return out


def test_cohorts_and_tap_corrected_counts() -> None:
    assert [pantry_priors.cohort_of(x) for x in (1.0, 1.99, 2.0, 3.49, 3.5, 6)] == [
        "small",
        "small",
        "medium",
        "medium",
        "large",
        "large",
    ]
    ran_out = pantry_priors.Observation("h", "small", "milk", 0, 1000, "ran_out", STEP)
    assert ran_out.count == pytest.approx(2.6)


def test_numpyro_recovers_the_cohort_means_and_beats_the_rule_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pantry_priors, "WARMUP", 150)
    monkeypatch.setattr(pantry_priors, "SAMPLES", 150)
    observed = synthetic()

    fitted = pantry_priors.fit(observed, "test")

    by_key = fitted.by_key()
    assert fitted.skipped == {} and set(by_key) == {(c, "milk") for c in pantry_priors.COHORTS}
    means = {c: by_key[(c, "milk")].prior_mean / STEP for c in pantry_priors.COHORTS}
    assert means["small"] < means["medium"] < means["large"]
    assert means["small"] == pytest.approx(2.0, rel=0.35)
    assert means["large"] == pytest.approx(6.0, rel=0.35)
    assert all(0.5 <= p.prior_weeks <= 52 for p in fitted.priors)
    assert all(p.households == 4 and p.weeks == 24 for p in fitted.priors)

    learned = pantry_priors.holdout_mae(observed, by_key)
    table = pantry_priors.holdout_mae(observed, None)
    assert learned["households_weeks"] == table["households_weeks"] == 12
    assert learned["mae_pct"] < 0.5 and table["mae_pct"] >= 0  # the table knows no cohort truth

    document = pantry_priors.as_document(
        fitted, {"rule_table": table, "learned_priors": learned}, "synthetic"
    )
    assert document["model"] == "pantry_priors" and len(document["items"]) == 3
    assert document["items"][0]["rule_mean"] is not None  # milk is in the rule table


def test_too_few_households_keep_the_rule_table() -> None:
    few = [o for o in synthetic() if o.household in ("small-0", "small-1")]
    fitted = pantry_priors.fit(few, "test")
    assert fitted.priors == [] and fitted.skipped == {"milk": "2 households"}
    empty = pantry_priors.holdout_mae([], None)
    assert math.isnan(empty["mae_pct"]) and empty["households_weeks"] == 0


def test_promote_moves_the_pointer_and_refuses_unknown_versions(tmp_path: Path) -> None:
    lake = LocalLake(tmp_path)
    document = {"version": "2026-10-05", "items": [], "metrics": {}}
    job.publish(lake, document)
    pointer = json.loads((tmp_path / "models/pantry_priors/latest.json").read_text())
    assert pointer["version"] == "2026-10-05"
    lake.write_bytes("models/pantry_priors/2026-09-28/priors.json", b"{}")

    rolled = promote.promote(lake, "pantry_priors", "2026-09-28")

    assert rolled["version"] == "2026-09-28" and rolled["previous"] == "2026-10-05"
    assert json.loads(lake.read_bytes("models/pantry_priors/latest.json") or b"")["version"] == (
        "2026-09-28"
    )
    with pytest.raises(SystemExit):
        promote.promote(lake, "pantry_priors", "2030-01-01")
    with pytest.raises(SystemExit):
        promote.promote(lake, "planner", "1")
    assert promote.main(["pantry_priors", "2026-10-05", "--out", str(tmp_path)]) == 0


@pytest.mark.anyio
@pytest.mark.services
async def test_the_job_reads_locked_boxes_with_cohorts_and_taps(db_session: AsyncSession) -> None:
    from datetime import date as _date

    from gharbasket_sim.run import simulate_world

    from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session,
        seed=7,
        weeks=2,
        start=_date(2026, 10, 5),
        planner="rules",
        adoption=0.1,
        write_db=True,
    )
    from sqlalchemy import func, select

    from gharbasket.modules.subscriptions.models import WeeklyCycle

    statuses = dict(
        (
            await db_session.execute(
                select(WeeklyCycle.status, func.count()).group_by(WeeklyCycle.status)
            )
        ).all()
    )
    observed, data_source = await job.observations(db_session)
    assert observed, statuses
    assert data_source == "synthetic"
    assert {o.cohort for o in observed} <= set(pantry_priors.COHORTS)
    assert all(o.qty > 0 and o.step > 0 for o in observed)
    assert len({o.product for o in observed}) >= 2
