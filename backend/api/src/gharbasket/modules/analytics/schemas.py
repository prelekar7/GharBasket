"""Request and response models of M12 Console & Analytics."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MetricOut(BaseModel):
    metric: str
    value: float
    split: Literal["train", "holdout", "backtest", "live"]
    data_source: Literal["synthetic", "pilot", "live", "mixed"]
    version: str
    computed_at: datetime


class ModelHealthOut(BaseModel):
    model_name: str
    metrics: list[MetricOut]
    drift: bool = Field(description="The newest live measurement is worse than the backtest")


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
