"""Request and response models of the unit-economics, P&L and assumptions endpoints (M12 §3.4)."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from gharbasket.modules.analytics.schemas import KpiSource

Source = Literal["measured", "assumed", "override"]


class AssumptionsOut(BaseModel):
    """The seven editable inputs of a box's economics; money in paise, rates in basis points."""

    aov_paise: int = Field(description="Average box value, add-ons included, delivery fee not")
    margin_bp: int = Field(description="Margin on produce, after vendor cost")
    delivery_paise: int = Field(description="Delivery cost per box")
    packing_paise: int = Field(description="Packing cost per box")
    captain_paise: int = Field(description="Society Captain pay per box")
    payment_fee_bp: int = Field(description="Share of the box value paid to the payment provider")
    cac_paise: int = Field(description="Customer acquisition cost spread per box")


class AssumptionsIn(AssumptionsOut):
    """The PUT body: every key, whole numbers (a range error is 422 `analytics.assumptions_invalid`
    with `errors[]`)."""

    model_config = ConfigDict(extra="forbid")

    aov_paise: StrictInt
    margin_bp: StrictInt
    delivery_paise: StrictInt
    packing_paise: StrictInt
    captain_paise: StrictInt
    payment_fee_bp: StrictInt
    cac_paise: StrictInt


class ChangeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    from_: int | None = Field(alias="from", description="Null: the key had no value before")
    to: int


class ChangedByOut(BaseModel):
    name: str | None = Field(description="The editor's name; null when they have none on record")


class HistoryOut(BaseModel):
    at: datetime
    by: ChangedByOut
    changes: list[ChangeOut]


class HubAssumptionsOut(BaseModel):
    hub_id: uuid.UUID
    assumptions: AssumptionsOut
    version: int = Field(description="Also the ETag; send it back as If-Match")
    history: list[HistoryOut] = Field(description="The newest 20 saves")


class MeasuredOut(BaseModel):
    """What the pilot (or the simulator) measured; null: not measurable yet. The payment fee is
    never measured in v1 and the CAC never, so neither is here."""

    aov_paise: int | None
    margin_bp: int | None
    delivery_paise: int | None
    packing_paise: int | None
    captain_paise: int | None


class SourcesOut(BaseModel):
    aov_paise: Source
    margin_bp: Source
    delivery_paise: Source
    packing_paise: Source
    captain_paise: Source
    payment_fee_bp: Source
    cac_paise: Source
    delivery_fee_paise: Source = Field(description="Measured, or an override")


class PerBoxOut(BaseModel):
    margin_paise: int
    delivery_fee_paise: int = Field(description="Revenue: the pre-live fee")
    delivery_paise: int
    packing_paise: int
    captain_paise: int
    fee_paise: int = Field(description="The payment fee, a cost")
    cac_paise: int
    contribution_paise: int = Field(description="May be negative")


class UnitWeekOut(BaseModel):
    week: date = Field(description="The week's Monday")
    boxes: int
    contribution_paise: int | None = Field(
        description="Per box, from that week's own inputs; null in a week without boxes"
    )


class UnitEconomicsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    hub_id: uuid.UUID
    data_source: KpiSource
    from_: date = Field(alias="from", description="The Monday of the first week")
    to: date = Field(description="The Sunday of the last week")
    as_of: datetime | None = Field(description="Finish time of the last run that refreshed it")
    boxes: int = Field(description="Delivered boxes")
    assumptions: AssumptionsOut = Field(description="The hub's saved values")
    measured: MeasuredOut
    used: AssumptionsOut = Field(
        description="Override, else measured (prefer=measured), else saved"
    )
    sources: SourcesOut
    delivery_fee_paise: int = Field(
        description="Per box: Σ fees of delivered boxes ÷ boxes, 0 with no pre-live boxes; "
        "or the override"
    )
    per_box: PerBoxOut
    weeks: list[UnitWeekOut]


class BasisTimes(BaseModel):
    kind: Literal["times"] = "times"
    qty: int
    unit: Literal["box", "tiffin"]
    rate_paise: int


class BasisShare(BaseModel):
    kind: Literal["share"] = "share"
    bp: int
    of: Literal["box_revenue"] = "box_revenue"


LineKey = Literal[
    "box_revenue",
    "delivery_fee_revenue",
    "tiffin_revenue",
    "produce",
    "kitchen",
    "delivery",
    "packing",
    "captain",
    "fees",
    "acquisition",
]


class PnlLineOut(BaseModel):
    key: LineKey
    amount_paise: int = Field(description="Costs are negative; exact paise")
    source: Literal["measured", "assumed"]
    basis: BasisTimes | BasisShare


class PartnersPaidOut(BaseModel):
    captains: int
    kitchens: int
    riders: int
    packers: int


class PnlWeekOut(BaseModel):
    week: date = Field(description="The week's Monday")
    contribution_paise: int


class PnlOut(BaseModel):
    hub_id: uuid.UUID
    period: str = Field(examples=["2027-02"])
    as_of: datetime | None
    boxes: int
    tiffins: int
    revenue_paise: int
    contribution_paise: int
    previous_contribution_paise: int | None = Field(
        description="The month before's; null when it has no data"
    )
    per_box_paise: int | None = Field(description="Contribution ÷ boxes; null without boxes")
    partners_paise: PartnersPaidOut
    lines: list[PnlLineOut]
    weeks: list[PnlWeekOut]
