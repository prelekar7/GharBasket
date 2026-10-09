"""The order state machine per kind (handbook 06 §6.2, §6.3; ADR-0026 §2, §3), no database."""

import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gharbasket.modules.orders import state
from gharbasket.modules.orders.models import ACTORS, ORDER_KINDS, STATUSES
from gharbasket.platform.errors import ProblemError

ALL = sorted(STATUSES)
KINDS = sorted(ORDER_KINDS)
BOX_PATH = ["approved", "locked", "picking", "packed", "dispatched", "at_society", "delivered"]
TIFFIN_PATH = ["approved", "locked", "picking", "packed", "dispatched", "delivered"]

# ADR-0026 §2: (from, to) -> who may make the move, for a box or an add-on and for a tiffin.
BOX_MOVES = {
    ("approved", "locked"): {"system"},
    ("approved", "payment_failed"): {"system"},
    ("approved", "cancelled"): {"customer", "system"},
    ("payment_failed", "locked"): {"system"},
    ("payment_failed", "cancelled"): {"customer", "system"},
    ("locked", "picking"): {"packer", "hub"},
    ("picking", "packed"): {"packer", "hub"},
    ("locked", "packed"): {"hub"},
    ("packed", "dispatched"): {"rider", "hub"},
    ("redelivery", "dispatched"): {"rider", "hub"},
    ("dispatched", "at_society"): {"captain", "rider", "hub"},
    ("at_society", "delivered"): {"captain", "rider", "hub"},
    ("at_society", "undelivered"): {"captain", "rider", "system", "hub"},
    ("undelivered", "redelivery"): {"customer", "hub"},
    ("undelivered", "refunded"): {"customer", "system"},
}
TIFFIN_MOVES = {
    ("approved", "locked"): {"system"},
    ("approved", "payment_failed"): {"system"},
    ("approved", "cancelled"): {"customer", "system"},
    ("payment_failed", "locked"): {"system"},
    ("payment_failed", "cancelled"): {"customer", "system"},
    ("locked", "picking"): {"kitchen", "hub"},
    ("picking", "packed"): {"kitchen", "hub"},
    ("packed", "dispatched"): {"rider", "hub"},
    ("dispatched", "delivered"): {"rider", "captain", "hub"},
    ("dispatched", "undelivered"): {"rider", "captain", "hub"},
    ("undelivered", "refunded"): {"system"},
}
EXPECTED = {"box": BOX_MOVES, "addon": BOX_MOVES, "tiffin": TIFFIN_MOVES}


def allowed(kind: str, a: str, b: str, actor: str) -> bool:
    try:
        state.check(kind, a, b, actor)
    except ProblemError as refused:
        assert refused.code == "orders.illegal_transition"
        assert refused.extra is not None
        assert (refused.extra["kind"], refused.extra["from"], refused.extra["to"]) == (kind, a, b)
        return False
    return True


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_actor_and_move_matches_the_adr_table(kind: str) -> None:
    for a, b, actor in itertools.product(ALL, ALL, ACTORS):
        assert allowed(kind, a, b, actor) == (actor in EXPECTED[kind].get((a, b), set())), (
            kind,
            a,
            b,
            actor,
        )


def test_the_happy_paths_and_the_shared_sets() -> None:
    box_actors = ["system", "packer", "packer", "rider", "captain", "captain"]
    for (a, b), actor in zip(itertools.pairwise(BOX_PATH), box_actors, strict=True):
        state.check("box", a, b, actor)
        state.check("addon", a, b, actor)
    tiffin_actors = ["system", "kitchen", "kitchen", "rider", "rider"]
    for (a, b), actor in zip(itertools.pairwise(TIFFIN_PATH), tiffin_actors, strict=True):
        state.check("tiffin", a, b, actor)
    assert {"cancelled", "delivered", "refunded"} == state.FINAL
    assert "approved" not in state.PAID and "locked" in state.PAID
    assert set(state.EVENTS) == set(ALL) - {"approved"}
    assert set(state.TRANSITIONS) == set(ORDER_KINDS)
    # Tiffins never wait at the society or come back for a second try.
    assert {"at_society", "redelivery"}.isdisjoint(state.TRANSITIONS["tiffin"])
    for kind in ORDER_KINDS:  # paid during the grace (M4 §3.4)
        state.check(kind, "payment_failed", "locked", "customer")


@pytest.mark.parametrize(
    ("kind", "a", "b", "actor"),
    [
        ("box", "approved", "picking", "hub"),  # not paid yet
        ("box", "locked", "cancelled", "customer"),  # money taken: refunds go another way
        ("box", "delivered", "approved", "system"),
        ("box", "approved", "locked", "customer"),  # only the cut-off locks
        ("box", "picking", "dispatched", "hub"),
        ("box", "dispatched", "delivered", "captain"),  # boxes go through the society
        ("box", "locked", "picking", "kitchen"),  # kitchens cook tiffins, not boxes
        ("box", "locked", "packed", "packer"),  # the fast path is the hub's override
        ("tiffin", "dispatched", "at_society", "rider"),
        ("tiffin", "undelivered", "redelivery", "customer"),  # a lunch is never redelivered
        ("tiffin", "undelivered", "refunded", "customer"),  # refunded at once by the system
        ("tiffin", "locked", "picking", "packer"),
        ("tiffin", "locked", "packed", "hub"),
        ("unknown", "approved", "locked", "system"),
    ],
)
def test_illegal_moves_are_409(kind: str, a: str, b: str, actor: str) -> None:
    with pytest.raises(ProblemError) as caught:
        state.check(kind, a, b, actor)
    assert caught.value.status == 409 and caught.value.code == "orders.illegal_transition"
    assert caught.value.extra is not None
    assert (caught.value.extra["kind"], caught.value.extra["from"], caught.value.extra["to"]) == (
        kind,
        a,
        b,
    )


def test_a_wrong_actor_is_named_in_the_problem() -> None:
    with pytest.raises(ProblemError) as caught:
        state.check("addon", "dispatched", "at_society", "packer")
    assert caught.value.extra is not None and caught.value.extra["actor"] == "packer"
    assert "captain, hub, rider" in str(caught.value)


@settings(max_examples=300)
@given(
    kind=st.sampled_from(KINDS),
    moves=st.lists(st.tuples(st.sampled_from(ALL), st.sampled_from(ACTORS)), max_size=12),
)
def test_any_sequence_of_moves_keeps_one_status_and_never_leaves_a_final_state(
    kind: str, moves: list[tuple[str, str]]
) -> None:
    status = "approved"
    for to, actor in moves:
        try:
            state.check(kind, status, to, actor)
        except ProblemError:
            continue  # refused: the order stays where it was
        assert status not in state.FINAL
        assert to in state.TRANSITIONS[kind][status]
        assert actor in EXPECTED[kind][(status, to)]
        status = to
    assert status in state.TRANSITIONS[kind]
    if kind == "tiffin":
        assert status not in {"at_society", "redelivery"}
