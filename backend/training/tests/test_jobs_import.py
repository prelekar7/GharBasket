"""Every job starts the way Actions starts it: `python -m gharbasket_training.jobs.<job>` in a fresh
interpreter. An import cycle among the API's modules only shows up there, never inside a test
process that has already imported them in another order."""

import subprocess
import sys
from pathlib import Path

import pytest

JOBS = sorted(
    path.stem
    for path in (Path(__file__).parents[1] / "src" / "gharbasket_training" / "jobs").glob("*.py")
    if path.stem != "__init__"
)


def test_the_jobs_are_found() -> None:
    assert {"collect_mandi", "export_snapshots", "forecast", "refit_pantry", "train_ranker"} <= set(
        JOBS
    )


@pytest.mark.parametrize("job", JOBS)
def test_a_job_imports_in_a_fresh_interpreter(job: str) -> None:
    done = subprocess.run(
        [sys.executable, "-c", f"import gharbasket_training.jobs.{job}"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
