"""Request and response models of M12 Console & Analytics."""

from datetime import datetime
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
