"""Means with 95 % confidence intervals across seeds (Student's t, small n)."""

import math
from dataclasses import dataclass

# Two-sided 95 % critical values of Student's t by degrees of freedom (n - 1).
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


@dataclass(frozen=True)
class Estimate:
    mean: float
    ci: float  # half-width of the 95 % interval; 0 with one seed
    n: int

    def as_dict(self) -> dict[str, float | int]:
        return {"mean": round(self.mean, 4), "ci95": round(self.ci, 4), "n": self.n}

    def __str__(self) -> str:
        return f"{self.mean:.3f} ± {self.ci:.3f}" if self.n > 1 else f"{self.mean:.3f}"


def estimate(values: list[float]) -> Estimate:
    n = len(values)
    if n == 0:
        return Estimate(math.nan, math.nan, 0)
    mean = sum(values) / n
    if n == 1:
        return Estimate(mean, 0.0, 1)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    t = T95.get(n - 1, 1.96)
    return Estimate(mean, t * sd / math.sqrt(n), n)
