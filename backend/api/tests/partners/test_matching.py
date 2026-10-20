"""Who cooks what (M8 §3.4): eligibility, the preferred cook first and M8's greedy fallback."""

import uuid
from typing import Any

import pytest

from gharbasket.modules.partners import matching

A, B, C = (uuid.UUID(int=n) for n in (1, 2, 3))
SUNRISE = (19.0395, 73.0675)  # Sunrise Towers
NEAR = (19.0430, 73.0720)  # Shree Ganesh CHS, about 0.6 km away
FAR = (19.2000, 73.2000)  # about 25 km away


def cook(id: uuid.UUID, at: tuple[float, float], left: int = 10, **kw: Any) -> matching.Cook:
    return matching.Cook(
        id, at[0], at[1], left, kw.get("skills", frozenset({"veg"})), kw.get("rating", 4.5), 0
    )


def order(id: uuid.UUID, portions: int, **kw: Any) -> matching.Order:
    return matching.Order(
        id, *SUNRISE, portions, kw.get("skills", frozenset({"veg"})), kw.get("preferred")
    )


def test_the_preferred_cook_first_then_the_nearest_eligible_kitchen() -> None:
    near, nearer, far = uuid.UUID(int=10), uuid.UUID(int=11), uuid.UUID(int=12)
    cooks = [cook(near, NEAR, left=3), cook(nearer, SUNRISE, left=4), cook(far, FAR)]
    orders = [order(A, 2, preferred=near), order(B, 3), order(C, 2, preferred=far)]

    taken, rest = matching.preferred_first(orders, cooks)
    # A goes to its preferred kitchen; C's is out of reach and loses the preference.
    (match,) = taken
    assert (match.order_id, match.partner_id, match.portions) == (A, near, 2)
    assert match.km == pytest.approx(0.6, abs=0.1)
    assert [o.id for o in rest] == [B, C] and cooks[0].left == 1
    assert cooks[0].earnings == 2 * matching.PAISE_PER_PORTION

    more, missed = matching.greedy(rest, cooks)
    assert [(m.order_id, m.partner_id) for m in more] == [(B, nearer)]
    assert missed == {C: "no_room"}  # the nearest has 1 left, the far one is out of reach


def test_each_rule_and_the_tie_breaks() -> None:
    assert matching.why_not(cook(A, SUNRISE, rating=3.7), order(B, 1)) == "below_rating_floor"
    assert matching.why_not(cook(A, SUNRISE), order(B, 1, skills={"jain"})) == "no_skills"
    assert matching.why_not(cook(A, FAR), order(B, 1)) == "out_of_reach"
    assert matching.why_not(cook(A, SUNRISE, left=1), order(B, 2)) == "no_room"
    assert matching.why_not(cook(A, NEAR), order(B, 1)) is None
    assert matching.greedy([order(A, 1)], []) == ([], {A: "no_room"})
    # Equal distance: the kitchen that earned less wins, then the lower id.
    twins = [cook(B, SUNRISE), cook(A, SUNRISE)]
    twins[1].earnings = 100
    assert matching.greedy([order(C, 1)], twins)[0][0].partner_id == B
    assert (
        matching.greedy([order(C, 1)], [cook(B, SUNRISE), cook(A, SUNRISE)])[0][0].partner_id == A
    )
    # Largest first, then by id.
    assert [o.id for o in matching.by_size([order(B, 1), order(C, 2), order(A, 1)])] == [C, A, B]


def test_the_runs_summary_for_the_fairness_dashboard() -> None:
    assert matching.gini([0, 0]) == 0.0 and matching.gini([0, 10]) == 0.5
    cooks = [cook(A, SUNRISE), cook(B, NEAR)]
    matches = [matching.take(cooks[0], order(C, 2))]
    assert matching.summary(matches, cooks, {A, B}) == {
        "portions": 2,
        "kitchens_used": 1,
        "km_per_portion": 0.0,
        "gini": 0.5,  # every kitchen of the request, as M10 counts it
        "gini_eligible": 0.5,
    }
    # Only the kitchens that could take an order count in `gini_eligible`.
    assert matching.summary(matches, cooks, {A})["gini_eligible"] == 0.0
    assert matching.summary([], cooks, set())["km_per_portion"] == 0.0
