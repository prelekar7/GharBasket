"""The quantity rules of a procurement line (M6 §3.3): buffer, short-life cap, forecast basis,
operations units and rounding, without a database."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from gharbasket.modules.hub_ops import procurement


def test_the_buffer_the_short_life_cap_and_rounding() -> None:
    q = procurement.quantities
    ten, zero = Decimal(10), Decimal(0)
    assert q(ten, None, 0, None, zero, "kg") == ("buffer", Decimal(0), Decimal("10.0"))
    assert q(ten, None, 10, None, zero, "kg") == ("buffer", Decimal(1), Decimal(11))
    assert q(ten, None, 50, None, zero, "kg") == ("buffer", Decimal(5), Decimal(15))
    # 48 h or less: at most 5 % of committed whatever the basis; 49 h is not capped.
    assert q(ten, None, 50, 48, zero, "bunch") == ("buffer", Decimal("0.5"), Decimal(11))
    assert q(ten, None, 50, 49, zero, "bunch") == ("buffer", Decimal(5), Decimal(15))
    assert q(ten, Decimal(3), 10, 48, zero, "l") == ("forecast", Decimal("0.5"), Decimal("10.5"))
    assert q(ten, Decimal(3), 10, None, zero, "kg") == ("forecast", Decimal(3), Decimal(13))
    assert q(Decimal("1.234"), None, 10, None, zero, "kg")[2] == Decimal("1.4")
    assert q(ten, None, 10, None, Decimal(12), "piece")[2] == 0  # never negative
    assert procurement.to_ops(2.5, 500, "g") == Decimal("1.25")
    assert procurement.to_ops(3, 500, "ml") == Decimal("1.5")
    assert procurement.to_ops(1.5, 4, "piece") == Decimal(6)
    assert procurement.amount(Decimal("0.1"), 3205) == 321  # half up
    assert procurement.amount(Decimal(1), None) is None


@given(
    committed=st.decimals(0, 5000, places=3),
    on_hand=st.decimals(0, 5000, places=3),
    pct=st.integers(0, 50),
    unit=st.sampled_from(["kg", "piece"]),
)
def test_suggested_stays_between_committed_and_one_rounding_unit_over_the_buffer(
    committed: Decimal, on_hand: Decimal, pct: int, unit: str
) -> None:
    _, _, suggested = procurement.quantities(committed, None, pct, None, on_hand, unit)
    grain = Decimal("0.1") if unit == "kg" else Decimal(1)
    assert suggested >= committed - on_hand
    assert suggested <= max(Decimal(0), committed * (1 + Decimal(pct) / 100) - on_hand) + grain
