"""Load every module's jobs and outbox consumers, in any process.

Jobs (`@platform.jobs.job`) and consumers (`platform.outbox.subscribe`) register as a side effect
of importing the module's `jobs.py`. The API imports them through its routers; a tool that runs
jobs or relays the outbox (`just job`, `just seed`, `just sim --write-db`) imports no routers, so
it calls `load_modules()` first. Without it the relay refuses to run (`outbox.relay`), because an
event whose consumers were never imported would be marked sent and lost.

`jobs.py` is the only place a module registers jobs or consumers (tests/test_registry.py checks).
"""

import importlib
from importlib.util import find_spec

from gharbasket.modules import MODULES
from gharbasket.platform import jobs as _platform_jobs  # noqa: F401 (the platform's own jobs)
from gharbasket.platform import outbox


def jobs_modules() -> list[str]:
    """The `jobs.py` of every business module that has one, in M-number order."""
    return [
        f"gharbasket.modules.{name}.jobs"
        for name in MODULES
        if find_spec(f"gharbasket.modules.{name}.jobs") is not None
    ]


def load_modules() -> None:
    """Import every module's `jobs.py` (idempotent), then let the outbox relay run."""
    for name in jobs_modules():
        importlib.import_module(name)
    outbox.mark_consumers_loaded()
