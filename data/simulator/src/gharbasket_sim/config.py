"""The cohort file (data/simulator/cohorts.json), validated."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gharbasket.modules.identity.schemas import AgeBand, Cuisine, Diet, Fast

COHORTS_FILE = Path(__file__).resolve().parents[2] / "cohorts.json"


class Cohort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    weight: float = Field(gt=0)
    locale: str
    adults: tuple[int, int]  # 18-59, inclusive range
    children: tuple[int, int]  # under 5 or 5-17
    seniors: tuple[int, int]  # 60+
    diet: dict[Diet, float]
    cuisines: list[Cuisine] = Field(min_length=1)  # the first is the household's own
    second_cuisine: float  # chance the household also likes the second
    jain: float
    fasts: dict[Fast, float]  # chance the household observes each fast
    no_onion_garlic_on_fasts: float
    diabetic: float  # chance one elder member has diabetes (with health consent)
    weekday_cook_minutes: list[int]  # choices
    weekend_cook_minutes: list[int]
    no_cook_sunday: float
    budget_on: float  # chance the monthly Budget Lock is on
    budget_paise: tuple[int, int]  # monthly, rounded to ₹500
    accept_bias: float  # logit added to every dish's taste when the plan arrives
    skip_week: float  # chance a week's box is skipped (they still eat)
    price_sensitivity: float = 0.5  # chance of swapping or dropping an item whose price spiked
    feedback_rate: float = 0.4  # chance the "ran out / wasted" tap is given for an item

    @model_validator(mode="after")
    def _someone_lives_there(self) -> "Cohort":
        if self.adults[1] + self.seniors[1] == 0:
            raise ValueError(f"{self.name}: a household needs an adult or a senior")
        return self


class Shock(BaseModel):
    """A price episode: a product costs `multiplier` times its list price for some weeks."""

    model_config = ConfigDict(extra="forbid")

    product: str
    start_week: int = Field(ge=0)  # 0 = the run's first week
    weeks: int = Field(ge=1)
    multiplier: float = Field(gt=0)
    note: str = ""


class Shocks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    about: str
    episodes: list[Shock]


SHOCKS_FILE = COHORTS_FILE.with_name("shocks.json")


def load_shocks(path: Path = SHOCKS_FILE) -> Shocks:
    return Shocks.model_validate_json(path.read_text())


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    about: str
    adoption: float = Field(gt=0, le=1)  # share of a society's flats that sign up
    adult_equivalent: dict[AgeBand, float]
    daily_use_per_adult_week: dict[str, float]  # product slug → base units a week
    fruit_per_adult_week_g: float
    cohorts: list[Cohort] = Field(min_length=1)


def load(path: Path = COHORTS_FILE) -> Config:
    return Config.model_validate_json(path.read_text())
