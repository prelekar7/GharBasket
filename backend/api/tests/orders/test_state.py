"""The order state machine (handbook 06 §6.2), without a database."""

import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gharbasket.modules.orders import state
from gharbasket.platform.errors import ProblemError

ALL = sorted(state.TRANSITIONS)
ACTORS = ("customer", "system", "hub")


def test_the_happy_path_is_allowed_and_final_states_have_no_exit() -> None:
    path = ["approved", "locked", "picking", "packed", "dispatched", "at_society", "delivered"]
    for a, b in itertools.pairwise(path):
        actor = "system" if b == "locked" else "hub"
        state.check(a, b, actor)
    assert {"cancelled", "delivered", "refunded"} == state.FINAL
    assert "approved" not in state.PAID and "locked" in state.PAID
    assert set(state.EVENTS) == set(ALL) - {"approved"}
    state.check("payment_failed", "locked", "customer")  # paid during the grace (M4 §3.4)


@pytest.mark.parametrize(
    ("a", "b", "actor"),
    [
        ("approved", "picking", "hub"),  # not paid yet
        ("locked", "cancelled", "customer"),  # money taken: refunds go another way
        ("delivered", "approved", "system"),
        ("approved", "locked", "customer"),  # only the cut-off locks
        ("picking", "dispatched", "hub"),
    ],
)
def test_illegal_moves_are_409(a: str, b: str, actor: str) -> None:
    with pytest.raises(ProblemError) as caught:
        state.check(a, b, actor)
    assert caught.value.code == "orders.illegal_transition"
    assert caught.value.extra is not None
    assert caught.value.extra["from"] == a and caught.value.extra["to"] == b


@settings(max_examples=300)
@given(moves=st.lists(st.tuples(st.sampled_from(ALL), st.sampled_from(ACTORS)), max_size=12))
def test_any_sequence_of_moves_keeps_one_status_and_never_leaves_a_final_state(
    moves: list[tuple[str, str]],
) -> None:
    status = "approved"
    for to, actor in moves:
        try:
            state.check(status, to, actor)
        except ProblemError:
            continue  # refused: the order stays where it was
        assert status not in state.FINAL
        assert to in state.TRANSITIONS[status]
        status = to
    assert status in state.TRANSITIONS
