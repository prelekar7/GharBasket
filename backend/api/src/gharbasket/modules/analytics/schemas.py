"""Request and response models of M12 Console & Analytics."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

DataSource = Literal["synthetic", "pilot", "live", "mixed"]  # "mixed": old rows; never written now


class MetricOut(BaseModel):
    metric: str
    value: float
    split: Literal["train", "holdout", "backtest", "live"]
    data_source: DataSource
    version: str
    computed_at: datetime
    hub_id: str | None = Field(default=None, description="Set when the row is about one hub")


class HubDriftOut(BaseModel):
    hub_id: str
    data_source: DataSource
    drift: bool = Field(
        description="A live WAPE of this hub and source in the window was more "
        "than 10 points worse than its own backtest"
    )
    drifted_days: int
    scored_days: int
    for_date: str | None = Field(description="The newest scored delivery day")
    wape: float | None = Field(description="Its realised WAPE")
    backtest_wape: float | None = Field(
        description="The same hub and data source's backtest WAPE it was compared with"
    )


class ModelHealthOut(BaseModel):
    model_name: str
    metrics: list[MetricOut] = Field(
        description="The newest value per (metric, split, data source, hub)"
    )
    drift: bool = Field(description="Any hub drifted in the window (see `hubs`)")
    hubs: list[HubDriftOut] = Field(
        default_factory=list, description="monitor_drift per hub and data source over the window"
    )


class PredictionCountOut(BaseModel):
    kind: str
    source: Literal["ml", "fallback"]
    count: int


class AiHealthOut(BaseModel):
    generated_at: datetime
    window_days: int
    models: list[ModelHealthOut]
    predictions: list[PredictionCountOut] = Field(
        description="How often each kind of prediction came from the ML service or the fallback"
    )


# --- the today board (spec §3.3) ----------------------------------------------------------------


class SocietyBoxesOut(BaseModel):
    society_id: uuid.UUID
    name: str
    boxes: int


class BoardBoxesOut(BaseModel):
    total: int = Field(description="Boxes of the day, not cancelled")
    locked: int = Field(description="Boxes the cut-off locked (and any status after locked)")
    cutoff_at: datetime
    by_society: list[SocietyBoxesOut]


class BoardPackingOut(BaseModel):
    bags_total: int
    bags_packed: int
    finished_at: datetime | None = Field(description="When the last bag was packed, once all are")


class BoardShortageOut(BaseModel):
    product_id: uuid.UUID
    name: str = Field(description="In the caller's language (`Accept-Language`)")
    bags: int = Field(description="Bags with at least one step of it short")


class BoardRoutesOut(BaseModel):
    planned: int
    started: int
    completed: int
    stops_total: int
    stops_done: int = Field(description="Handed over or skipped")
    late_stops: int


class BoardDeliveriesOut(BaseModel):
    at_society: int
    delivered: int
    held: int
    undelivered: int = Field(description="Failed attempts, whatever happened to them since")


class BoardWasteOut(BaseModel):
    kg: float = Field(description="Wasted that day (cascade to the bin)")
    previous_kg: float | None
    previous_date: date | None = Field(description="The newest earlier day with sales or waste")


StepKey = Literal[
    "cutoff", "procurement", "receiving", "packing", "dispatch", "delivery", "surplus"
]


class BoardStepOut(BaseModel):
    key: StepKey
    due_at: str | None = Field(description="IST HH:MM; the cut-off's is the day before")
    state: Literal["done", "current", "issue", "todo"]
    note: str | None


AttentionKind = Literal[
    "issue_open",
    "payment_failed",
    "shortage",
    "plan_needs_review",
    "delivery_held",
    "donations_to_verify",
    "surplus_open",
    "society_live",
]


class BoardAttentionOut(BaseModel):
    kind: AttentionKind
    count: int
    oldest_at: datetime | None
    due_at: datetime | None
    link: str = Field(description="A console route, never a URL with a token")


class TodayOut(BaseModel):
    hub_id: uuid.UUID
    date: date
    as_of: datetime
    boxes: BoardBoxesOut
    packing: BoardPackingOut
    shortages: list[BoardShortageOut]
    routes: BoardRoutesOut
    deliveries: BoardDeliveriesOut
    waste: BoardWasteOut
    steps: list[BoardStepOut]
    attention: list[BoardAttentionOut] = Field(
        description="One row per kind with a count above 0; kinds without a source are never sent"
    )


# --- KPIs and societies (spec §3.3) -------------------------------------------------------------

KpiSource = Literal["real", "synthetic"]
Grain = Literal["day", "week"]


class KpisOut(BaseModel):
    hub_id: uuid.UUID | None = Field(description="Null: every hub (admin only)")
    data_source: KpiSource
    grain: Grain
    as_of: datetime | None = Field(description="Finish time of the last run that refreshed it")
    stale: bool
    periods: list[date] = Field(description="The first day of each period (ISO weeks start Monday)")
    series: dict[str, list[int | None]] = Field(
        description=(
            "One value per period for every KPI key; null when undefined. "
            "`partner_earnings_paise` is null under 5 paid partners (never an individual's pay)"
        )
    )
    totals: dict[str, int | None] = Field(
        description="Over the whole range; rates recomputed from their parts, not averaged"
    )


class SocietyRowOut(BaseModel):
    society_id: uuid.UUID
    name: str
    status: Literal["prospect", "unlocking", "live", "paused"]
    joined: int = Field(description="Households whose address is in the society")
    threshold: int = Field(description="Households that unlock free delivery")
    active_households: int = Field(description="Delivered an order in the last 4 weeks")
    paused_households: int
    approved_on_time_bp: int | None = Field(
        description="Cycles approved before the cut-off, of the cycles generated, last 4 weeks"
    )
    average_box_paise: int | None = Field(
        description="Net of refunds and credits, over delivered boxes of the last 4 weeks"
    )


class SocietiesOut(BaseModel):
    hub_id: uuid.UUID | None
    as_of: datetime | None
    items: list[SocietyRowOut]


# --- cohorts and partner earnings (spec §3.5) ----------------------------------------------------

PartnerKind = Literal["kitchen", "captain", "rider", "packer"]


class CohortOut(BaseModel):
    week: date = Field(description="The Monday of the households' signup ISO week")
    households: int
    small: bool = Field(description="Under 5 households: too few to read a rate from")
    retention_bp: list[int | None] = Field(
        description="Entry i: the share of the cohort with a delivered order in the ISO week "
        "i + 1 after the signup week; null when that week has not ended yet"
    )


class CohortsOut(BaseModel):
    data_source: KpiSource
    hub_id: uuid.UUID | None = Field(description="Null: every hub")
    weeks: int
    as_of: datetime | None = Field(description="Finish time of the last nightly run or rebuild")
    stale: bool
    cohorts: list[CohortOut] = Field(description="Oldest first; a week without signups is left out")


class PartnerEarningsWeekOut(BaseModel):
    week: date = Field(description="The Monday of the ISO week")
    partners: int = Field(description="Partners paid something that week")
    total_paise: int | None = Field(
        description="Null under 5 paid partners: one partner's total is their earnings"
    )
    p10_paise: int | None = Field(description="Null under 5 paid partners")
    median_paise: int | None = Field(description="Null under 5 paid partners")
    p90_paise: int | None = Field(description="Null under 5 paid partners")
    gini_bp: int | None = Field(description="Null under 5 paid partners")


class PartnerEarningsOut(BaseModel):
    hub_id: uuid.UUID | None = Field(description="Null: every hub (admin only)")
    kind: PartnerKind
    data_source: KpiSource
    as_of: datetime | None = Field(description="Finish time of the last nightly run or rebuild")
    stale: bool
    weeks: list[PartnerEarningsWeekOut] = Field(
        description="Every ISO week of the range; a distribution only, never one partner"
    )
