"""M-F: the routing arm of the harness (eval/routing.py): the seeded mornings, both production
arms scored by `routing.check()`, the verdict rule, the report section and the CLI flags."""

from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

import pytest
from gharbasket_ml import routing
from gharbasket_training.eval import __main__ as cli
from gharbasket_training.eval import report
from gharbasket_training.eval import routing as routing_eval
from gharbasket_training.eval.routing import ARMS, Arm, RoutingRun


def test_a_morning_is_deterministic_per_seed_and_size() -> None:
    a, b = routing_eval.morning(7, 50), routing_eval.morning(7, 50)
    assert a.model_dump() == b.model_dump()
    assert routing_eval.morning(11, 50).model_dump() != a.model_dump()
    assert len(a.stops) == 50 and len({s.id for s in a.stops}) == 50
    # The five seed societies come first, at their boundary's centre (S-3's points).
    assert [s.id.split("/")[0] for s in a.stops[:5]] == [
        slug for slug, _, _ in routing_eval.seed_societies()
    ]
    assert (round(a.stops[0].lat, 4), round(a.stops[0].lng, 4)) == (19.0395, 73.0675)
    lat0, lat1, lng0, lng1 = routing_eval.AREA
    assert all(lat0 <= s.lat <= lat1 and lng0 <= s.lng <= lng1 for s in a.stops[5:])
    for s in a.stops:
        assert 4 <= s.orders <= 20 and s.crates == -(-s.orders // 3)
        assert 30 * s.orders <= s.kg_tenths <= 60 * s.orders
        assert s.empties in (0, s.crates) and s.donation_kg_tenths == 0
        assert (s.window_start_s, s.window_end_s) in routing_eval.WINDOWS
    assert {(s.window_start_s, s.window_end_s) for s in a.stops} == set(routing_eval.WINDOWS)
    # As M7 sends it: loaded by 06:30, back by 11:30, 20 s for both waves, no food bank.
    assert (a.depart_s, a.return_by_s, a.time_limit_s) == (23_400, 41_400, 20.0)
    assert a.food_bank is None
    assert a.vans.count == -(-sum(s.crates for s in a.stops) * 3 // (24 * 2))
    # The reusable generator also builds S-3's donations and food bank.
    bank = routing_eval.morning(7, 50, food_bank=True)
    assert bank.food_bank is not None and sum(1 for s in bank.stops if s.donation_kg_tenths) == 8
    with pytest.raises(ValueError, match="size"):
        routing_eval.morning(7, 201)
    # Hub, windows and fleet slack are keywords (M-H reuses the generator).
    seed_hub = (19.0330, 73.0297)
    other = routing_eval.morning(
        7, 50, hub=seed_hub, windows=((7 * 3600, 9 * 3600),), fleet_slack=2.0
    )
    assert (other.hub.lat, other.hub.lng) == seed_hub
    assert {(s.window_start_s, s.window_end_s) for s in other.stops} == {(25_200, 32_400)}
    assert other.vans.count == -(-sum(s.crates for s in other.stops) * 2 // 24)
    assert other.return_by_s == 9 * 3600 + 90 * 60


def test_the_harness_uses_the_production_numbers() -> None:
    from gharbasket_training.eval import assignment_sweep

    from gharbasket.modules.logistics import solver

    assert routing_eval.TIME_LIMIT_S == solver.TIME_LIMIT_S  # two waves of 10 s
    assert routing_eval.RETURN_MARGIN_S == solver.RETURN_MARGIN_S
    assert routing_eval.BAGS_PER_CRATE == solver.BAGS_PER_CRATE
    assert routing_eval.HUB == assignment_sweep.HUB  # S-3's hub, shared with the λ sweep


@pytest.fixture(scope="module")
def small() -> RoutingRun:
    """Seed 7, 20 societies, 1 s for both waves (0.5 s the shorter budget): quick, and the same
    code path."""
    return routing_eval.run_routing(7, 20, time_limit_s=1.0, short_limit_s=0.5)


def test_both_arms_give_plans_the_checker_passes(small: RoutingRun) -> None:
    plan, nn, short = small.arm(ARMS[0]), small.arm(ARMS[1]), small.arm(routing_eval.SHORT_ARM)
    assert plan.status == "solved" and nn.status == "baseline" and short.status == "solved"
    assert small.short_limit_s == 0.5 and 0.5 <= short.seconds < plan.seconds
    for arm in (plan, nn, short):
        assert arm.feasible, arm.problems
        assert arm.stops == 20 and arm.orders == small.orders  # every society served
        assert 0 < arm.vans <= small.vans_available and arm.km > 0 and arm.cost_paise > 0
        assert 0 <= arm.on_time <= 1 and arm.waves == 2
    assert plan.seconds >= 0.9  # guided local search spends its budget
    # The checker's own replay of the baseline gives the same figures.
    inst = routing_eval.instance(routing_eval.morning(7, 20))
    checked = routing.check(inst, routing.nearest_neighbour(inst))
    assert (checked.cost_paise, checked.vans) == (nn.cost_paise, nn.vans)


def _run(
    seed: int,
    *,
    plan_cost: int,
    on_time: float,
    size: int = 50,
    limit: float = 20.0,
    short_cost: int | None = None,
    **plan: Any,
) -> RoutingRun:
    base = Arm(ARMS[1], "baseline", 600, 50, 10, 300.0, 600_000, 0.9, 10.0, 0.01, 2)
    mine = replace(base, arm=ARMS[0], status="solved", cost_paise=plan_cost, on_time=on_time)
    mine = replace(mine, **{"seconds": limit + 0.02, **plan})
    arms = [mine, base]
    if short_cost is not None:
        arms.append(replace(mine, arm=routing_eval.SHORT_ARM, cost_paise=short_cost, seconds=4.01))
    short = 4.0 if short_cost is not None else None
    return RoutingRun(seed, size, 600, 200, 2700.0, 14, limit, arms, short)


SEEDS = (7, 11, 13, 17, 19)


def test_the_verdict_needs_both_bounds_five_seeds_and_clean_solver_plans() -> None:
    # ₹/order 20-24 % below nearest neighbour, always on time: holds.
    good = [_run(s, plan_cost=456_000 + 6_000 * i, on_time=1.0) for i, s in enumerate(SEEDS)]
    v = routing_eval.verdict(good)[50]
    assert v["holds"] is True and v["headline"] and v["n"] == 5
    assert v["cost_vs_nearest_upper"] <= -0.10 and v["on_time_lower"] == 1.0
    assert v["max_seconds_per_wave"] == pytest.approx(10.01)
    assert v["short_budget"] is None  # no shorter budget in these runs
    # Four seeds: no verdict.
    assert routing_eval.verdict(good[:4])[50]["holds"] is None
    # The mean saving is 12 %, but its interval reaches above -0.10: the upper bound decides.
    costs = (450_000, 600_000, 528_000, 480_000, 582_000)  # -25 %, 0, -12 %, -20 %, -3 %
    wide = [_run(s, plan_cost=c, on_time=1.0) for s, c in zip(SEEDS, costs, strict=True)]
    w = routing_eval.verdict(wide)[50]
    assert w["cost_vs_nearest"]["mean"] == pytest.approx(-0.12) and w["holds"] is False
    assert w["cost_vs_nearest_upper"] > -0.10
    # On time 0.96 on average but its lower bound under 0.95.
    shares = (1.0, 0.9, 1.0, 0.94, 0.96)
    late = [_run(s, plan_cost=450_000, on_time=t) for s, t in zip(SEEDS, shares, strict=True)]
    assert routing_eval.verdict(late)[50]["holds"] is False
    # One plan from the fallback, or with a checker problem: no.
    fell = [*good[:4], _run(19, plan_cost=450_000, on_time=1.0, status="fallback")]
    assert routing_eval.verdict(fell)[50]["holds"] is False
    messy = [*good[:4], _run(19, plan_cost=450_000, on_time=1.0, problems=["x: 31 min late"])]
    assert routing_eval.verdict(messy)[50]["holds"] is False
    # Other sizes are reported with their own verdict, the headline is 50.
    big = [_run(s, plan_cost=450_000, on_time=1.0, size=100) for s in SEEDS]
    both = routing_eval.verdict(good + big)
    assert not both[100]["headline"] and both[100]["holds"] is True


def test_the_guardrail_judges_the_setting_and_reports_the_overhead() -> None:
    # The solver spends its budget, so the wall time cannot judge it: the limit a wave does.
    ok = [_run(s, plan_cost=450_000, on_time=1.0, seconds=20.3) for s in SEEDS]
    v = routing_eval.verdict(ok)[50]
    assert v["wave_limit_s"] == 10.0 and v["guardrail_holds"] is True
    assert v["max_overhead_s"] == pytest.approx(0.15)  # 20.3 s over two waves of 10 s
    long = [_run(s, plan_cost=450_000, on_time=1.0, limit=21.0) for s in SEEDS]
    assert routing_eval.verdict(long)[50]["guardrail_holds"] is False  # 10.5 s a wave
    # The shorter budget is reported beside it, with the same target rule, as evidence.
    short = [_run(s, plan_cost=450_000, on_time=1.0, short_cost=480_000) for s in SEEDS]
    v = routing_eval.verdict(short)[50]
    s = v["short_budget"]
    assert s["seconds_a_wave"] == 2.0 and s["holds"] is True
    assert s["cost_vs_nearest"]["mean"] == pytest.approx(-0.2) and v["holds"] is True
    weak = [_run(s, plan_cost=450_000, on_time=1.0, short_cost=570_000) for s in SEEDS]
    assert routing_eval.verdict(weak)[50]["short_budget"]["holds"] is False
    assert routing_eval.verdict(weak)[50]["holds"] is True  # the target is on the full budget
    summary = routing_eval.summarise(short)[50]
    assert routing_eval.SHORT_ARM in summary["arms"]
    assert summary["paired"]["cost_vs_nearest_short"].mean == pytest.approx(-0.2)


def test_the_settings_record_the_machine() -> None:
    where = routing_eval.settings()["platform"]
    assert set(where) == {"machine", "processor", "cpu_count", "python", "system"}
    assert where["cpu_count"] and where["python"].count(".") == 2


def test_cost_per_order_counts_the_served_orders() -> None:
    arm = Arm(ARMS[1], "baseline", 300, 25, 5, 150.0, 300_000, 1.0, 0.0, 0.0, 2, ["not served: x"])
    assert arm.cost_paise_per_order == 1000 and arm.km_per_order == 0.5 and not arm.feasible


def test_the_report_section_rows_and_html(small: RoutingRun, tmp_path: Path) -> None:
    built = report.build([], [], args={}, routing_runs=[small])
    sec = built["routing"]
    assert set(sec["sizes"]) == {"20"}
    assert set(sec["sizes"]["20"]["arms"]) == {*ARMS, routing_eval.SHORT_ARM}
    assert sec["verdict"]["20"]["holds"] is None  # one seed
    assert sec["verdict"]["20"]["short_budget"]["holds"] is None
    assert sec["settings"]["short_limit_s"] == 0.5
    assert sec["settings"]["time_limit_s"] == 1.0 and sec["settings"]["deterministic"] is False
    assert built["seeds"] == [7] and built["runs"]["routing"][0]["size"] == 20
    rows = [r for r in report.metrics_rows(built) if r["model_name"].startswith("routing_")]
    names = {(r["model_name"], r["metric"]) for r in rows}
    assert ("routing_cvrptw_waves", "cost_paise_per_order_stops_20") in names
    assert ("routing_nearest_neighbour", "on_time_stops_20") in names
    assert ("routing_cvrptw_waves", "cost_vs_nearest_stops_20") in names
    assert ("routing_cvrptw_waves", "cost_vs_nearest_short_stops_20") in names
    assert ("routing_cvrptw_waves_short", "on_time_stops_20") in names
    assert all(r["split"] == "holdout" and len(r["metric"]) <= 40 for r in rows)
    cost = next(r for r in rows if r["metric"] == "cost_paise_per_order_stops_20")
    assert cost["details"]["per_seed"] == {"7": small.arm(ARMS[0]).cost_paise_per_order}
    page = report.to_html(built)
    routing_part = page.split("M-F — van routing")[1]
    assert "M-F hypothesis" in routing_part and "reference only" in routing_part  # one seed
    assert "0.25 s a wave" in routing_part and "CPUs" in routing_part
    assert "M-F" not in report.to_html(report.build([], [], args={}))
    assert report.build([], [], args={})["routing"] == {}


def test_the_standalone_run_writes_json_and_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "routing"
    assert (
        routing_eval.main(
            [
                "--seeds",
                "7",
                "--sizes",
                "12",
                "--time-limit",
                "0.5",
                "--out",
                str(out),
                "--short-limit",
                "0",
            ]
        )
        == 0
    )
    assert list(out.glob("routing-*.json")) and list(out.glob("routing-*.html"))
    assert "routing  12 societies" in capsys.readouterr().out
    bad_args = (["--sizes", "0"], ["--sizes", "201"], ["--time-limit", "0.1"])
    for bad in (*bad_args, ["--short-limit", "0.1"]):
        with pytest.raises(SystemExit):
            routing_eval.main(["--out", str(out), *bad])


class _Stop(Exception):
    pass


def _stop(built: dict[str, Any], out: Path) -> NoReturn:
    raise _Stop(built)


def test_the_cli_runs_routing_unless_told_not_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    got: list[tuple[int, int, float, float | None]] = []

    def fake(seed: int, size: int, *, time_limit_s: float, short_limit_s: float | None) -> str:
        got.append((seed, size, time_limit_s, short_limit_s))
        return f"run {seed} {size}"

    monkeypatch.setattr(routing_eval, "run_routing", fake)
    monkeypatch.setattr(cli.planner_ab, "run_planner", lambda *a, **k: None)
    monkeypatch.setattr(cli.pantry_baseline, "run_pantry", lambda *a, **k: None)
    monkeypatch.setattr(cli.report, "build", lambda *a, **k: k)
    monkeypatch.setattr(cli.report, "write", _stop)
    flags = [
        "--seeds",
        "7",
        "11",
        "--no-learned",
        "--no-forecast",
        "--no-assignment",
        "--no-ranker",
    ]
    flags += ["--out", str(tmp_path)]
    with pytest.raises(_Stop) as stop:
        cli.main(flags)
    assert got == [(7, 50, 20.0, 4.0), (11, 50, 20.0, 4.0)]  # the defaults: the headline
    assert stop.value.args[0]["routing_runs"][0] == "run 7 50"
    assert stop.value.args[0]["args"]["routing_sizes"] == [50]

    got.clear()
    with pytest.raises(_Stop):
        cli.main([*flags, "--routing-sizes", "30", "30", "--routing-time-limit", "4"])
    assert got == [(7, 30, 4.0, 4.0), (11, 30, 4.0, 4.0)]

    got.clear()
    with pytest.raises(_Stop):
        cli.main([*flags, "--routing-sizes", "200", "50", "100", "--routing-short-limit", "0"])
    assert [(n, short) for _, n, _, short in got[::2]] == [(50, 0.0), (100, 0.0), (200, 0.0)]

    got.clear()
    with pytest.raises(_Stop) as stop:
        cli.main([*flags, "--no-routing"])
    assert got == [] and stop.value.args[0]["routing_runs"] == []
    assert stop.value.args[0]["args"]["routing_sizes"] is None

    for bad in (
        ["--routing-sizes", "0"],
        ["--routing-time-limit", "30"],
        ["--routing-short-limit", "0.2"],
    ):
        with pytest.raises(SystemExit):
            cli.main([*flags, *bad])
