"""The simulated day: a fresh database, the seed and two simulated weeks, then one delivery day
driven through every job on main at the IST time its spec gives, with customers acting through the
real endpoints in between, and the invariants checked at the end.

The steps run in file order and build on each other (`Day` carries what they learnt). Times are the
specs' (M3 §6, M4 §6, M6 §6, M11 §6, M0 §6, M1 §6) for delivery day T, a Tuesday.
"""

import asyncio
import math
import subprocess
import sys
from asyncio import Runner
from collections import Counter
from collections.abc import Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import invariants
import pytest
import time_machine
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from harness import API_DIR, REPO, SUMMARY, App, Config, ist, key
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import SCHEMAS, create_engine
from gharbasket.platform.jobs import JOBS
from gharbasket.tools.seed import SEED_HUB_ID

# Every job on main is run in the day, or listed here with why not (later waves remove them).
NOT_IN_THE_DAY = {
    "build_data_export": "needs object storage (SeaweedFS, `just up-storage`); no export is "
    "requested in the day",
    "sweep_uploads": "needs object storage (SeaweedFS, `just up-storage`)",
}
# Open product bugs this suite found, by the invariant that shows them, with the bug as the reason;
# each is xfail(strict=True), so fixing one turns the suite red until it is removed. (#280's two,
# tools that lost outbox consumers and `just job` knowing only the platform's jobs, are fixed.)
BUGS: dict[str, str] = {}
VIKRAM = "+919800001008"  # the seed hub's manager
PRIYA = "+919800001001"  # a seed customer, subscribing during the day
ROLES = (
    "editor",
    "skipper",
    "unskipper",
    "grace_payer",
    "grace_skipper",
    "defaulter",
    "auto",
    "late",
)
PAYING = ("editor", "unskipper", "grace_payer", "grace_skipper", "defaulter", "auto")


@dataclass
class Home:
    phone: str
    user_id: UUID
    household_id: UUID
    auth: dict[str, str] = field(default_factory=dict[str, str])
    cycle_id: str | None = None
    order_id: str | None = None
    total_paise: int = 0


@dataclass
class Day:
    """What the steps learn, for the steps after them."""

    t: date
    homes: dict[str, Home] = field(default_factory=dict[str, Home])
    watermark: str | None = None  # the last outbox id before the day: what the day adds is after
    cycles: int = 0


@pytest.fixture(scope="module")
def day(config: Config) -> Day:
    return Day(config.delivery_day)


@contextmanager
def at(when: datetime) -> Iterator[None]:
    """The process clock reads `when` (frozen) for the step: the API, the jobs and the tokens."""
    with time_machine.travel(when, tick=False):
        yield


def step(loop: Runner, when: datetime, coro: Coroutine[Any, Any, Any]) -> Any:
    with at(when):
        return loop.run(coro)


@asynccontextmanager
async def database(config: Config):
    engine = create_engine(config.database_url, pooled=False)
    try:
        async with AsyncSession(engine) as session:
            yield session
    finally:
        await engine.dispose()


async def rows(config: Config, sql: str, **params: Any) -> list[Any]:
    async with database(config) as session:
        return list((await session.execute(text(sql), params)).all())


async def scalar(config: Config, sql: str, **params: Any) -> Any:
    async with database(config) as session:
        return (await session.execute(text(sql), params)).scalar()


def run_tool(config: Config, *args: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-m", *args],
        cwd=REPO,
        env=config.sub_env(),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert done.returncode == 0, f"{args[0]} failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    return done.stdout.splitlines()


# --- 1. the schema ----------------------------------------------------------------------------


def test_migrations_upgrade_downgrade_and_upgrade_again_with_one_head_and_no_drift(
    config: Config, loop: Runner, fresh_db: str
) -> None:
    alembic = AlembicConfig(
        toml_file=API_DIR / "pyproject.toml", attributes={"database_url": fresh_db}
    )
    heads = ScriptDirectory.from_config(alembic).get_heads()
    assert len(heads) == 1, f"migration heads: {heads}"

    command.upgrade(alembic, "head")
    command.downgrade(alembic, "base")
    left = loop.run(
        scalar(
            config,
            "SELECT string_agg(table_schema || '.' || table_name, ', ') FROM information_schema"
            ".tables WHERE table_schema = ANY(:schemas) AND table_name <> 'alembic_version'",
            schemas=list(SCHEMAS),
        )
    )
    assert left is None, f"tables left after downgrade to base: {left}"
    command.upgrade(alembic, "head")
    command.check(alembic)  # raises when the models and the migrated schema differ
    assert loop.run(scalar(config, "SELECT version_num FROM platform.alembic_version")) == heads[0]


# --- 2. the world -----------------------------------------------------------------------------


def test_seed_then_seed_again_changes_nothing(config: Config) -> None:
    first = run_tool(config, "gharbasket.tools.seed")
    again = run_tool(config, "gharbasket.tools.seed")
    assert any(line.endswith("synthetic households") for line in first)
    assert not [line for line in again if line.endswith(" new")], again
    assert any(line.endswith("(0 new)") for line in again), again


def test_the_simulator_writes_two_weeks_through_the_modules(
    config: Config, day: Day, loop: Runner, tmp_path_factory: pytest.TempPathFactory
) -> None:
    out = tmp_path_factory.mktemp("sim_truth")
    lines = run_tool(
        config,
        "gharbasket_sim",
        "--seed",
        str(config.sim_seed),
        "--weeks",
        str(config.sim_weeks),
        "--start",
        config.first_monday.isoformat(),
        "--planner",
        config.planner,
        "--adoption",
        str(config.sim_adoption),
        "--write-db",
        "--out",
        str(out),
    )
    SUMMARY.extend(lines)
    through = next(line for line in lines if line.startswith("through the modules"))
    assert "locked" in through and "refused: none" in through, through
    assert (out / "outcomes.parquet").exists()
    day.watermark = str(
        loop.run(scalar(config, "SELECT id FROM platform.outbox ORDER BY id DESC LIMIT 1"))
    )


# --- 3a. the delivery day ---------------------------------------------------------------------


async def _homes(config: Config) -> list[Home]:
    """The simulated households with an active subscription, by phone."""
    async with database(config) as session:
        rows = await session.execute(
            text(
                """
                SELECT u.phone_e164, u.id AS user_id, h.id AS household_id
                FROM identity.users u
                JOIN identity.households h ON h.owner_user_id = u.id
                JOIN subscriptions.subscriptions s ON s.household_id = h.id
                WHERE u.phone_e164 LIKE '+9198001%' AND s.status = 'active'
                ORDER BY u.phone_e164
                """
            )
        )
        return [Home(r.phone_e164, r.user_id, r.household_id) for r in rows]


def test_customers_sign_in_and_one_turns_auto_approve_on(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Friday T-4: the households the day follows sign in on their phones; one asks for
    auto-approve; Priya (a seed customer) subscribes."""
    homes = loop.run(_homes(config))
    assert len(homes) >= len(ROLES) + 2, f"only {len(homes)} simulated households subscribed"
    day.homes = dict(zip(ROLES, homes, strict=False))

    async def go() -> None:
        for home in day.homes.values():
            home.auth = await app.sign_in(home.phone)
        auto = await app.http.patch(
            "/v1/subscriptions/current", json={"auto_approve": True}, headers=day.homes["auto"].auth
        )
        assert auto.status_code == 200 and auto.json()["auto_approve"] is True, auto.text
        priya = await app.sign_in(PRIYA)
        subscribed = await app.http.post(
            "/v1/subscriptions",
            json={"plan": "autopilot", "payment_mode": "wallet"},
            headers={**priya, **key()},
        )
        assert subscribed.status_code == 201, subscribed.text
        await app.sweep(ist(day.t - timedelta(days=4), 10, 0))

    step(loop, ist(day.t - timedelta(days=4), 10, 0), go())


def test_generate_weekly_plans_drafts_every_subscribed_household(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Saturday T-3 18:00: M3 drafts the boxes for Tuesday."""
    when = ist(day.t - timedelta(days=3), 18, 0)

    async def go() -> dict[str, Any]:
        summary = await app.job("generate_weekly_plans", when)
        again = await app.job("generate_weekly_plans", when + timedelta(minutes=2))
        await app.sweep(when)
        return {"first": summary, "again": again}

    ran = step(loop, when, go())
    subscribed = loop.run(
        scalar(config, "SELECT count(*) FROM subscriptions.subscriptions WHERE status = 'active'")
    )
    assert ran["first"]["delivery_date"] == day.t.isoformat()
    assert ran["first"]["created"] == subscribed, ran
    assert ran["again"]["created"] == 0 and ran["again"]["existing"] == subscribed, ran
    day.cycles = subscribed


def test_box_ready_notices_open_the_boxes_and_push_them(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Saturday T-3 18:30: the drafts are shown and every household is told (M11 → FCM)."""
    when = ist(day.t - timedelta(days=3), 18, 30)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        notices = await app.job("send_box_ready_notices", when)
        await app.sweep(when)
        sent = await app.job("send_deliveries", when + timedelta(minutes=1))
        return notices, sent

    notices, sent = step(loop, when, go())
    # The day's own box-ready messages: the simulated weeks before it have theirs too.
    messages = loop.run(
        scalar(
            config,
            "SELECT count(*) FROM notifications.messages WHERE kind = 'subscriptions.box_ready' "
            "AND event_id > CAST(:watermark AS uuid)",
            watermark=day.watermark,
        )
    )
    assert notices["delivery_date"] == day.t.isoformat()
    assert messages == day.cycles, (notices, messages)
    # Only the signed-in phones have a push token; everyone else's push is skipped (inbox only).
    assert sent["sent"] >= len(day.homes), sent


def _top_up_amount(needed: int, balance: int) -> int:
    """What a household tops up to cover `needed`: whole rupees, at least the ₹100 minimum."""
    short = max(needed - balance, 0)
    return max(10_000, math.ceil(short / 100) * 100)


async def _top_up(app: App, home: Home, amount: int) -> None:
    """A top-up as the app makes one: a (dev) Razorpay order, then its payment captured."""
    started = await app.http.post(
        "/v1/wallet/topups", json={"amount_paise": amount}, headers={**home.auth, **key()}
    )
    assert started.status_code == 201, started.text
    paid = await app.http.post(
        "/v1/dev/razorpay/pay",
        json={"razorpay_order_id": started.json()["razorpay_order_id"]},
        headers=home.auth,
    )
    assert paid.status_code == 200, paid.text


async def _order(app: App, home: Home) -> dict[str, Any]:
    answer = await app.http.get(f"/v1/orders/{home.order_id}", headers=home.auth)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def wake(app: App, day: Day) -> None:
    """The phones open the app again: fresh access tokens (they last 15 minutes)."""
    for home in day.homes.values():
        home.auth = await app.refresh(home.phone)


async def _balance(app: App, home: Home) -> int:
    return (await app.http.get("/v1/wallet", headers=home.auth)).json()["balance_paise"]


def test_customers_edit_approve_skip_and_unskip_their_boxes(
    app: App, day: Day, loop: Runner
) -> None:
    """Sunday T-2 10:00: through the customer endpoints, with If-Match and Idempotency-Key."""
    when = ist(day.t - timedelta(days=2), 10, 0)
    homes = day.homes

    async def current(home: Home) -> dict[str, Any]:
        answer = await app.http.get("/v1/cycles/current", headers=home.auth)
        assert answer.status_code == 200, answer.text
        box = answer.json()
        assert box["delivery_date"] == day.t.isoformat() and box["status"] == "awaiting_approval"
        home.cycle_id, home.total_paise = box["id"], box["total_paise"]
        return box

    async def approve(home: Home, version: int) -> dict[str, Any]:
        idem = key()
        answer = await app.http.post(
            f"/v1/cycles/{home.cycle_id}/approve",
            headers={**home.auth, **idem, "If-Match": f'"{version}"'},
        )
        assert answer.status_code == 200, answer.text
        replay = await app.http.post(
            f"/v1/cycles/{home.cycle_id}/approve",
            headers={**home.auth, **idem, "If-Match": f'"{version}"'},
        )
        assert replay.json() == answer.json() and replay.headers.get("idempotent-replayed")
        box = answer.json()
        home.order_id, home.total_paise = box["order_id"], box["total_paise"]
        assert box["status"] == "approved" and home.order_id
        return box

    async def go() -> None:
        await wake(app, day)
        for home in homes.values():
            await current(home)
        # The editor: one more of the first line, the second line gone; then approves.
        editor = homes["editor"]
        box = await current(editor)
        first, second = box["lines"][0], box["lines"][1]
        edited = await app.http.patch(
            f"/v1/cycles/{editor.cycle_id}/lines",
            json={
                "ops": [
                    {"op": "qty", "product_id": first["product_id"], "qty": first["qty"] + 1},
                    {"op": "remove", "product_id": second["product_id"]},
                ]
            },
            headers={**editor.auth, "If-Match": f'"{box["version"]}"'},
        )
        assert edited.status_code == 200, edited.text
        stale = await app.http.patch(
            f"/v1/cycles/{editor.cycle_id}/lines",
            json={"ops": [{"op": "remove", "product_id": first["product_id"]}]},
            headers={**editor.auth, "If-Match": f'"{box["version"]}"'},
        )
        assert stale.json()["code"] == "subscriptions.version_conflict"
        approved = await approve(editor, edited.json()["version"])
        # An edit to the approved box hands the new lines to its order (M3 §3.1).
        again = await app.http.patch(
            f"/v1/cycles/{editor.cycle_id}/lines",
            json={
                "ops": [{"op": "qty", "product_id": first["product_id"], "qty": first["qty"] + 2}]
            },
            headers={**editor.auth, "If-Match": f'"{approved["version"]}"'},
        )
        assert again.status_code == 200 and again.json()["status"] == "approved", again.text
        editor.total_paise = again.json()["total_paise"]
        order = await _order(app, editor)
        assert (order["id"], order["total_paise"]) == (editor.order_id, editor.total_paise), order

        skipped = await app.http.post(
            f"/v1/cycles/{homes['skipper'].cycle_id}/skip",
            headers={**homes["skipper"].auth, **key()},
        )
        assert skipped.status_code == 200 and skipped.json()["status"] == "skipped", skipped.text

        back = homes["unskipper"]
        gone = await app.http.post(
            f"/v1/cycles/{back.cycle_id}/skip", headers={**back.auth, **key()}
        )
        unskipped = await app.http.post(
            f"/v1/cycles/{back.cycle_id}/unskip",
            headers={**back.auth, **key(), "If-Match": f'"{gone.json()["version"]}"'},
        )
        assert unskipped.status_code == 200, unskipped.text
        assert unskipped.json()["status"] == "awaiting_approval"
        await approve(back, unskipped.json()["version"])

        for role in ("grace_payer", "grace_skipper", "defaulter"):
            await approve(homes[role], (await current(homes[role]))["version"])

        # Those who will be paid for top up now; the others keep what the weeks before left.
        for role in ("editor", "unskipper", "auto"):
            home = homes[role]
            await _top_up(app, home, _top_up_amount(home.total_paise, await _balance(app, home)))
        for role in ("grace_payer", "grace_skipper", "defaulter"):
            assert await _balance(app, homes[role]) < homes[role].total_paise
        await app.sweep(when)

    step(loop, when, go())


def test_the_small_hours_before_the_cut_off_run_the_weekly_and_nightly_jobs(
    app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 00:30 to 03:33 IST, the nightly and Monday jobs of M5, M9, M10, M8, M1: each runs
    clean on the world the day has built (the Kindness lots, referrals, thresholds, vendor scores,
    deletions and drift it has to find nothing, or little, to do for)."""
    monday = day.t - timedelta(days=1)
    summaries: dict[str, Any] = {}

    async def go() -> None:
        for name, hour, minute in (
            ("expire_kindness_points", 0, 30),
            ("recompute_unlock_thresholds", 2, 10),
            ("expire_referrals", 3, 10),
            ("compute_vendor_scores", 3, 33),
            ("purge_deleted_users", 3, 33),
            ("monitor_drift", 3, 33),
        ):
            summaries[name] = await app.job(name, ist(monday, hour, minute))
        await app.sweep(ist(monday, 3, 40))

    step(loop, ist(monday, 0, 0), go())
    # Nothing in the world is old enough to expire, owed, deleted or scored: each finds no work.
    assert summaries["expire_kindness_points"]["points"] == 0, summaries
    assert summaries["expire_referrals"]["voided"] == 0, summaries
    assert summaries["recompute_unlock_thresholds"]["went_live"] == 0, summaries
    assert summaries["compute_vendor_scores"]["vendors"] == 0, summaries  # no purchase orders yet
    purge = summaries["purge_deleted_users"]
    assert (purge["purged"], purge["blocked"], purge["overdue"], purge["failed"]) == (0, {}, [], {})
    assert summaries["monitor_drift"]["drifted"] == 0, summaries


def test_monday_morning_pays_the_partners_last_week_once(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 10:00 IST (M5 §3.5): `weekly_payouts` pays what partners were owed up to Sunday
    through the simulated provider and holds those who cannot be paid yet (the seed's captains
    have no KYC application); a second run pays nothing more. `payouts_match_the_ledger` checks
    the money at the end of the day."""
    monday = day.t - timedelta(days=1)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        first = await app.job("weekly_payouts", ist(monday, 10, 0))
        again = await app.job("weekly_payouts", ist(monday, 10, 5))
        await app.sweep(ist(monday, 10, 10))
        return first, again

    first, again = step(loop, ist(monday, 10, 0), go())
    assert (first["failed"], first["send_failed"], first["refused"]) == (0, 0, 0), first
    assert first["processed"] == first["pending"] + first["retried"], first
    assert (again["pending"], again["held"], again["processed"], again["failed"]) == (0, 0, 0, 0)
    assert again["existing"] == first["pending"] + first["held"], again
    entries = loop.run(
        scalar(config, "SELECT count(*) FROM billing.journal_entries WHERE kind = 'payout'")
    )
    assert entries == first["pending"] + first["retried"], (entries, first)


def test_reminders_go_to_unapproved_boxes_that_will_not_auto_approve(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 19:00: one reminder per box still awaiting approval, except auto-approve."""
    when = ist(day.t - timedelta(days=1), 19, 0)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        reminded = await app.job("remind_unapproved", when)
        again = await app.job("remind_unapproved", when + timedelta(minutes=5))
        await app.sweep(when)
        await app.job("send_deliveries", when + timedelta(minutes=1))
        return reminded, again

    reminded, again = step(loop, when, go())
    waiting = loop.run(
        scalar(
            config,
            "SELECT count(*) FROM subscriptions.weekly_cycles WHERE delivery_date = :t "
            "AND status = 'awaiting_approval'",
            t=day.t,
        )
    )
    assert reminded["reminded"] == waiting - 1, (reminded, waiting)  # the auto household is not
    assert again["reminded"] == 0, again


def test_auto_approve_approves_the_household_that_asked(app: App, day: Day, loop: Runner) -> None:
    """Monday T-1 20:55, five minutes before the cut-off."""
    when = ist(day.t - timedelta(days=1), 20, 55)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        await wake(app, day)
        summary = await app.job("apply_auto_approve", when)
        await app.sweep(when)
        box = await app.http.get("/v1/cycles/current", headers=day.homes["auto"].auth)
        return summary, box.json()

    summary, box = step(loop, when, go())
    assert summary["approved"] == 1, summary
    assert box["status"] == "approved" and box["order_id"], box
    day.homes["auto"].order_id = box["order_id"]


def test_cut_off_locks_the_paid_and_fails_the_short_then_refuses_late_edits(
    app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 21:00: debit and lock, or payment_failed with a 2 h grace. Two triggers at
    once (a cron retry racing the first) and a late third one change nothing more; the API
    refuses edits after the cut-off."""
    when = ist(day.t - timedelta(days=1), 21, 0)
    homes = day.homes

    async def go() -> dict[str, Any]:
        await wake(app, day)
        both = await asyncio.gather(
            app.trigger("cutoff_lock", when), app.trigger("cutoff_lock", when)
        )
        runs = [run["summary"] for run in both if run["status"] == "ok"]
        assert all(run["status"] in ("ok", "skipped") for run in both), both
        first = {k: sum(run[k] for run in runs) for k in ("locked", "payment_failed")}
        again = await app.job("cutoff_lock", when + timedelta(minutes=4))
        await app.sweep(when)
        await app.job("send_deliveries", when + timedelta(minutes=1))
        statuses = {role: (await _order(app, homes[role]))["status"] for role in PAYING}
        late = homes["late"]
        box = (await app.http.get("/v1/cycles/current", headers=late.auth)).json()
        refused = await app.http.patch(
            f"/v1/cycles/{late.cycle_id}/lines",
            json={"ops": [{"op": "remove", "product_id": box["lines"][0]["product_id"]}]},
            headers={**late.auth, "If-Match": f'"{box["version"]}"'},
        )
        approve = await app.http.post(
            f"/v1/cycles/{late.cycle_id}/approve",
            headers={**late.auth, **key(), "If-Match": f'"{box["version"]}"'},
        )
        mirrored = {
            role: (
                await app.http.get(f"/v1/cycles/{homes[role].cycle_id}", headers=homes[role].auth)
            ).json()["status"]
            for role in ROLES
        }
        return {
            "first": first,
            "again": again,
            "statuses": statuses,
            "refused": (refused.status_code, refused.json().get("code")),
            "approve": (approve.status_code, approve.json().get("code")),
            "cycles": mirrored,
        }

    seen = step(loop, when + timedelta(minutes=5), go())
    assert (seen["first"]["locked"], seen["first"]["payment_failed"]) == (3, 3), seen["first"]
    assert seen["again"]["locked"] == 0 and seen["again"]["skipped_already_locked"] == 3, seen
    assert seen["statuses"] == {
        "editor": "locked",
        "unskipper": "locked",
        "grace_payer": "payment_failed",
        "grace_skipper": "payment_failed",
        "defaulter": "payment_failed",
        "auto": "locked",
    }, seen["statuses"]
    assert seen["refused"] == (409, "subscriptions.cutoff_passed"), seen["refused"]
    assert seen["approve"] == (409, "subscriptions.cutoff_passed"), seen["approve"]
    assert seen["cycles"] == {
        "editor": "locked",
        "skipper": "skipped",
        "unskipper": "locked",
        "grace_payer": "payment_failed",
        "grace_skipper": "payment_failed",
        "defaulter": "payment_failed",
        "auto": "locked",
        "late": "awaiting_approval",
    }, seen["cycles"]


def test_after_the_cut_off_procurement_is_planned_and_money_jobs_run_clean(
    app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 21:10 to 21:20: the hub's procurement plan from the locked orders, and the
    every-few-minutes and hourly money jobs (no Razorpay here: nothing to send or compare)."""
    monday = day.t - timedelta(days=1)
    out: dict[str, Any] = {}

    async def go() -> dict[str, Any]:
        out["build"] = await app.job("build_procurement_plan", ist(monday, 21, 10))
        out["refunds"] = await app.job("send_refunds", ist(monday, 21, 10))
        out["reconcile"] = await app.job("reconcile_payments", ist(monday, 21, 17))
        out["proposals"] = await app.job("expire_proposals", ist(monday, 21, 20))
        await app.sweep(ist(monday, 21, 20))
        plan = await app.http.get(
            f"/v1/hubs/{SEED_HUB_ID}/procurement-plans",
            params={"date": day.t.isoformat()},
            headers=await app.sign_in(VIKRAM),
        )
        assert plan.status_code == 200, plan.text
        return plan.json()

    plan = step(loop, ist(monday, 21, 10), go())
    assert (out["build"]["hubs"], out["build"]["built"]) == (1, 1), out
    assert plan["lines"], plan  # the locked boxes need buying
    assert out["refunds"]["sent"] == out["refunds"]["found"] == 0, out  # no refund to send
    # Razorpay is not configured here, so the reconciliation has nothing to compare and says so.
    assert out["reconcile"] == {"status": "skipped", "checked": 0, "healed": 0, "opened": 0}, out
    assert out["proposals"]["expired"] == 0, out


def test_in_the_grace_period_a_top_up_pays_a_short_one_does_not_and_a_skip_cancels(
    app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 21:15 to 21:30: a top-up's event re-runs the debit (M4's consumer); one that
    still falls short leaves the box unpaid; a payment_failed week may still be skipped."""
    homes = day.homes
    payer, short, skipper = homes["grace_payer"], homes["defaulter"], homes["grace_skipper"]

    async def statuses(home: Home) -> tuple[str, str]:
        box = await app.http.get(f"/v1/cycles/{home.cycle_id}", headers=home.auth)
        return (await _order(app, home))["status"], box.json()["status"]

    async def early() -> dict[str, tuple[str, str]]:
        await wake(app, day)
        await _top_up(app, short, 10_000)  # ₹100: less than its shortfall
        skipped = await app.http.post(
            f"/v1/cycles/{skipper.cycle_id}/skip", headers={**skipper.auth, **key()}
        )
        assert skipped.status_code == 200, skipped.text
        await app.sweep(ist(day.t - timedelta(days=1), 21, 15))
        return {"short": await statuses(short), "skipper": await statuses(skipper)}

    async def late() -> tuple[str, str]:
        balance = await _balance(app, payer)
        await _top_up(app, payer, _top_up_amount(payer.total_paise, balance))
        await app.sweep(ist(day.t - timedelta(days=1), 21, 30))
        return await statuses(payer)

    seen = step(loop, ist(day.t - timedelta(days=1), 21, 15), early())
    assert seen == {
        "short": ("payment_failed", "payment_failed"),
        "skipper": ("cancelled", "skipped"),
    }, seen
    assert step(loop, ist(day.t - timedelta(days=1), 21, 30), late()) == ("locked", "locked")


def test_the_evening_runs_the_tiffin_round_and_the_pickup_sweep(
    app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 21:30, 22:00 and 22:30: the tiffin assignment round and its offer timeout (the
    day books no tiffin, so there is nothing to assign or expire) and the day's missed donation
    pickups."""
    monday = day.t - timedelta(days=1)
    out: dict[str, Any] = {}

    async def go() -> None:
        out["assign"] = await app.job("assign_kitchens", ist(monday, 21, 30))
        out["timeout"] = await app.job("assignment_acceptance_timeout", ist(monday, 22, 0))
        out["pickups"] = await app.job("sweep_missed_pickups", ist(monday, 22, 30))
        await app.sweep(ist(monday, 22, 30))

    step(loop, ist(monday, 21, 30), go())
    assert out["assign"]["runs"] == out["assign"]["offers"] == out["assign"]["unassigned"] == 0, out
    assert out["timeout"]["expired"] == 0, out  # no tiffin offers in the day, so none to expire
    assert out["pickups"]["rolled"] == out["pickups"]["cancelled"] == 0, out


def test_grace_expiry_cancels_what_is_still_unpaid(app: App, day: Day, loop: Runner) -> None:
    """Monday T-1 23:00."""
    when = ist(day.t - timedelta(days=1), 23, 0)
    defaulter = day.homes["defaulter"]

    async def go() -> tuple[dict[str, Any], str, str, dict[str, Any]]:
        await wake(app, day)
        summary = await app.job("payment_grace_expiry", when)
        await app.sweep(when)
        sent = await app.job("send_deliveries", when + timedelta(minutes=1))
        box = await app.http.get(f"/v1/cycles/{defaulter.cycle_id}", headers=defaulter.auth)
        return summary, (await _order(app, defaulter))["status"], box.json()["status"], sent

    summary, order, cycle, _ = step(loop, when, go())
    assert summary["cancelled"] == 1, summary
    assert (order, cycle) == ("cancelled", "cancelled")


def test_after_the_grace_the_plan_is_refreshed_and_the_routes_are_solved(
    app: App, day: Day, loop: Runner
) -> None:
    """Monday T-1 23:05 and 23:15: tomorrow's procurement plan is rebuilt after the grace expired,
    then each hub's box routes are solved (no ML service: M7's fallback solver)."""
    monday = day.t - timedelta(days=1)
    out: dict[str, Any] = {}

    async def go() -> list[dict[str, Any]]:
        vikram = await app.sign_in(VIKRAM)
        # The hub has no vehicle until its manager registers one: a solve needs a cargo van.
        van = await app.http.post(
            f"/v1/hubs/{SEED_HUB_ID}/vehicles",
            json={"label": "EV-1", "kind": "cargo_3w"},
            headers={**vikram, **key()},
        )
        assert van.status_code == 201, van.text
        out["refresh"] = await app.job("refresh_procurement_plan", ist(monday, 23, 5))
        out["routes"] = await app.job("solve_routes", ist(monday, 23, 15))
        await app.sweep(ist(monday, 23, 20))
        plans = await app.http.get(
            f"/v1/hubs/{SEED_HUB_ID}/route-plans",
            params={"date": day.t.isoformat(), "kind": "box"},
            headers=vikram,
        )
        assert plans.status_code == 200, plans.text
        return plans.json()["items"]

    plans = step(loop, ist(monday, 23, 5), go())
    assert (out["refresh"]["hubs"], out["refresh"]["rebuilt"]) == (1, 1), out
    assert out["routes"]["failed"] == 0 and out["routes"]["plans"] == 1, out
    # No ML service in the day (E2E_ML=1 has one): M7's fallback solved and published the plan.
    [plan] = plans
    assert plan["status"] == "published" and plan["summary"]["problems"] == [], plan
    sources = ("ml", "ml_fallback") if app.config.ml_url else ("fallback_nearest",)
    assert plan["source"] in sources, plan
    paid = {day.homes[r].order_id for r in ("editor", "unskipper", "auto", "grace_payer")}
    assert plan["summary"]["orders"] == len(paid), plan


def test_the_night_refunds_no_carried_items_when_none_waited(
    app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 02:40: `expire_carry_overs` (M4 §3.8) refunds carried items still waiting 14
    days after a second miss; the day has no box missed twice, so nothing is due."""
    when = ist(day.t, 2, 40)
    summary = step(loop, when, app.job("expire_carry_overs", when))
    assert summary == {
        "date": day.t.isoformat(),
        "refunded": 0,
        "refunded_paise": 0,
        "wallet_limit": 0,
        "no_owner": 0,
    }, summary


def test_the_night_housekeeping_jobs_run_clean(app: App, day: Day, loop: Runner) -> None:
    """Tuesday T 02:40 to 04:33 IST: the nightly analytics refresh (M12 §6), then the daily
    logistics, platform, identity and notification jobs."""
    out: dict[str, Any] = {}

    async def go() -> None:
        out["analytics"] = await app.job("refresh_analytics_nightly", ist(day.t, 2, 40))
        out["pings"] = await app.job("purge_gps_pings", ist(day.t, 3, 43))
        for name, hour, minute in (
            ("cleanup_idempotency_keys", 4, 3),
            ("expire_pending_grants", 4, 18),
            ("expire_data_exports", 4, 23),
            ("purge_notifications", 4, 33),
        ):
            await app.job(name, ist(day.t, hour, minute))

    step(loop, ist(day.t, 4, 0), go())
    assert out["pings"] == {"deleted": 0, "partitions_dropped": 0}, out  # no rider sent a ping
    # The simulated weeks delivered boxes within its 35 days, and their households signed up.
    assert out["analytics"]["read"]["orders"] > 0, out["analytics"]
    assert out["analytics"]["read"]["households"] > 0, out["analytics"]


def test_packing_tasks_are_built_for_every_paid_box(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 05:00: one packing task per locked order of the day, once."""
    when = ist(day.t, 5, 0)

    async def go() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        built = await app.job("build_packing_tasks", when)
        again = await app.job("build_packing_tasks", when + timedelta(minutes=3))
        vikram = await app.sign_in(VIKRAM)
        tasks = await app.http.get(
            f"/v1/hubs/{SEED_HUB_ID}/packing-tasks",
            params={"date": day.t.isoformat(), "limit": 200},
            headers=vikram,
        )
        assert tasks.status_code == 200, tasks.text
        await app.sweep(when)
        return built, again, tasks.json()["items"]

    built, again, tasks = step(loop, when, go())
    paid = {day.homes[r].order_id for r in ("editor", "unskipper", "auto", "grace_payer")}
    assert built["created"] == len(paid) and again["created"] == 0, (built, again)
    assert {t["order_id"] for t in tasks} == paid, tasks


def test_the_morning_sends_what_quiet_hours_held(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 07:01: messages held overnight go out; nothing is left queued that is due."""
    when = ist(day.t, 7, 1)

    async def go() -> dict[str, Any]:
        await app.sweep(when)
        return await app.job("send_deliveries", when)

    step(loop, when, go())
    due = loop.run(
        scalar(
            config,
            "SELECT count(*) FROM notifications.deliveries WHERE status IN ('queued', 'held') "
            "AND next_attempt_at <= :now",
            now=when,
        )
    )
    assert due == 0
    pushes = Counter((p["token"], p["data"]["message_id"]) for p in app.outside.pushes)
    assert not [k for k, n in pushes.items() if n > 1], "a message was pushed twice to a device"
    devices = set(app.devices.values())
    assert {p["token"] for p in app.outside.pushes} <= devices


async def logistics_untouched(config: Config, day: Day) -> None:
    """The published route is as the 23:15 solve left it, and no bag has been tried at a door."""
    states = await rows(
        config, "SELECT status FROM logistics.routes WHERE delivery_date = :d", d=day.t
    )
    assert states and {status for (status,) in states} == {"published"}, states
    assert await scalar(config, "SELECT count(*) FROM logistics.deliveries") == 0
    assert await scalar(config, "SELECT count(*) FROM logistics.gps_pings") == 0


def test_the_morning_moves_the_riders_pings_and_finds_none(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 07:02: `persist_gps_pings` (every minute) with nothing in the stream. No route has
    started, so the published route stays as it is."""
    when = ist(day.t, 7, 2)
    summary = step(loop, when, app.job("persist_gps_pings", when))
    assert summary == {"read": 0, "inserted": 0}, summary
    loop.run(logistics_untouched(config, day))


def test_lunch_tiffins_nobody_took_are_refunded_at_0930(app: App, day: Day, loop: Runner) -> None:
    """Tuesday T 09:30: the lunch slot's manual assignment closes and unassigned tiffins are
    refunded (the day books no tiffin, so there is nothing to refund)."""
    when = ist(day.t, 9, 30)
    summary = step(loop, when, app.job("refund_unassigned_tiffins", when))
    assert summary["refunded"] == summary["refused"] == 0, summary


def test_the_0935_tiffin_solve_publishes_an_empty_lunch_plan(
    app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 09:35: `solve_tiffin_routes` (M7 §3.7). The day books no tiffin, so each active
    hub gets a solved lunch plan with no routes, published at once; a second run solves nothing."""
    when = ist(day.t, 9, 35)
    summary = step(loop, when, app.job("solve_tiffin_routes", when))
    assert summary["plans"] >= 1 and summary["published"] == summary["plans"], summary
    assert summary["failed"] == 0 and summary["delivery_date"] == day.t.isoformat(), summary
    again = step(loop, when, app.job("solve_tiffin_routes", when))
    assert again["plans"] == 0 and again["skipped"] == summary["plans"], again


def test_the_1230_run_closes_the_days_buying_and_has_no_surplus_to_offer(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 12:30: `surplus_cascade` (M6 §3.5). Nobody packs in the simulated day and no
    goods were received, so nothing is drawn down or offered; the day's plan closes, and a second
    run changes nothing."""
    when = ist(day.t, 12, 30)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        first = await app.job("surplus_cascade", when)
        again = await app.job("surplus_cascade", when + timedelta(minutes=2))
        await app.sweep(when)
        return first, again

    first, again = step(loop, when, go())
    empty = {"drawn_tasks": 0, "closed_purchase_orders": 0, "offers": 0, "binned": 0}
    assert first == again == {"date": day.t.isoformat(), "hubs": 1, **empty}, (first, again)
    status = loop.run(
        scalar(
            config,
            "SELECT status FROM hub_ops.procurement_plans WHERE hub_id = :hub AND "
            "delivery_date = :d",
            hub=SEED_HUB_ID,
            d=day.t,
        )
    )
    assert status == "closed"
    assert loop.run(scalar(config, "SELECT count(*) FROM hub_ops.surplus_offers")) == 0


def test_lunch_tiffins_are_prompted_for_a_rating_at_1430(app: App, day: Day, loop: Runner) -> None:
    """Tuesday T 14:30: `prompt_tiffin_ratings` asks households to rate the day's delivered lunch
    tiffins (the day books no tiffin, so nothing is delivered and nobody is asked)."""
    when = ist(day.t, 14, 30)
    summary = step(loop, when, app.job("prompt_tiffin_ratings", when))
    assert summary == {"delivery_date": day.t.isoformat(), "prompted": 0}, summary


def test_the_logistics_evening_closes_routes_and_expires_holds_with_nothing_to_do(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 16:00, 20:30 and 20:45: `close_routes`, `hold_expiry` and `schedule_redeliveries`.
    The day's published route never started (the simulated day has no rider), so each runs clean,
    touches nothing and writes no delivery."""
    date = day.t.isoformat()
    closed = step(loop, ist(day.t, 16, 0), app.job("close_routes", ist(day.t, 16, 0)))
    assert closed == {"date": date, "closed": 0, "returned_to_hub": 0, "tiffins_to_triage": 0}, (
        closed
    )
    expired = step(loop, ist(day.t, 20, 30), app.job("hold_expiry", ist(day.t, 20, 30)))
    assert expired == {
        "date": date,
        "not_home": 0,
        "not_confirmed": 0,
        "failed": 0,
        "left_behind": 0,
    }, expired
    when = ist(day.t, 20, 45)
    again = step(loop, when, app.job("schedule_redeliveries", when))
    assert again == {
        "date": date,
        "redelivery": 0,
        "refunded": 0,
        "triage": 0,
        "failed": 0,
    }, again
    step(loop, when, app.sweep(when))
    loop.run(logistics_untouched(config, day))


def test_the_analytics_refresh_and_a_rebuild_agree(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Tuesday T 20:50, a 15-minute `refresh_analytics` (M12 §3.2), then `rebuild_analytics` by
    hand: facts are a cache of the source modules, so the rebuild through the facades gives the
    same daily numbers for the refresh's window (M12 §9)."""
    when = ist(day.t, 20, 50)
    refreshed = step(loop, when, app.job("refresh_analytics", when))
    assert refreshed["read"]["orders"] > 0, refreshed
    window = (day.t - timedelta(days=3), day.t + timedelta(days=1))
    sql = (
        "SELECT * FROM analytics.mv_daily_hub_kpis WHERE date BETWEEN :a AND :b"
        " ORDER BY hub_id, date, data_source"
    )
    before = loop.run(rows(config, sql, a=window[0], b=window[1]))
    later = when + timedelta(minutes=5)
    rebuilt = step(loop, later, app.job("rebuild_analytics", later))
    assert rebuilt["read"]["orders"] >= refreshed["read"]["orders"], rebuilt
    assert before and loop.run(rows(config, sql, a=window[0], b=window[1])) == before


def test_every_event_of_the_day_that_m11_maps_became_a_message(
    config: Config, app: App, day: Day, loop: Runner
) -> None:
    """Within the day (the app's own relay and `outbox_sweep`), no mapped event is lost."""

    async def check() -> list[str]:
        async with database(config) as session:
            return await invariants.routed_events_have_their_message(session, after=day.watermark)

    assert loop.run(check()) == []


def test_every_job_on_main_is_in_the_day(app: App) -> None:
    ran = {run.name for run in app.jobs}
    assert set(JOBS) == ran | set(NOT_IN_THE_DAY), (
        f"not run and not excused: {set(JOBS) - ran - set(NOT_IN_THE_DAY)}"
    )


# --- 4. invariants ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=BUGS[name]))
        if name in BUGS
        else name
        for name in invariants.ALL
    ],
)
def test_invariant(name: str, config: Config, loop: Runner, app: App) -> None:
    async def check() -> list[str] | None:
        async with database(config) as session:
            return await invariants.ALL[name](session)

    violations = loop.run(check())
    if violations is None:
        pytest.skip(f"{name}: its tables are not built yet")
    assert violations == [], "\n".join(violations[:50])


# --- the local tools (M0 §6: `just job`) ------------------------------------------------------


def test_just_job_runs_a_module_job(config: Config, day: Day, app: App) -> None:
    """`just job remind_unapproved --scheduled-for …` (M0 §6: the local trigger)."""
    at = ist(day.t - timedelta(days=1), 19, 0).isoformat()
    lines = run_tool(config, "gharbasket.tools.run_job", "remind_unapproved", "--scheduled-for", at)
    assert '"status": "ok"' in lines[-1]


def test_just_job_outbox_sweep_hands_events_to_their_consumers(
    config: Config, day: Day, loop: Runner, app: App
) -> None:
    """A box-ready event the after-commit relay missed (a restart), swept with
    `just job outbox_sweep`, still becomes Priya's message."""

    async def pending() -> str:
        async with database(config) as session:
            new = await session.execute(
                text(
                    """
                    INSERT INTO platform.outbox (event_type, payload)
                    SELECT event_type, payload FROM platform.outbox o
                    WHERE event_type = 'subscriptions.box_ready'
                      AND payload->>'household_id' = (
                        SELECT h.id::text FROM identity.households h
                        JOIN identity.users u ON u.id = h.owner_user_id
                        WHERE u.phone_e164 = :phone)
                    ORDER BY id DESC LIMIT 1
                    RETURNING id
                    """
                ),
                {"phone": PRIYA},
            )
            event_id = str(new.scalar_one())
            await session.commit()
            return event_id

    event_id = loop.run(pending())
    run_tool(config, "gharbasket.tools.run_job", "outbox_sweep")
    sent = loop.run(
        scalar(config, "SELECT sent_at IS NOT NULL FROM platform.outbox WHERE id = :i", i=event_id)
    )
    messages = loop.run(
        scalar(
            config, "SELECT count(*) FROM notifications.messages WHERE event_id = :i", i=event_id
        )
    )
    assert sent, "the sweep did not take the event"
    assert messages == 1, f"event {event_id} was marked sent without its message"
