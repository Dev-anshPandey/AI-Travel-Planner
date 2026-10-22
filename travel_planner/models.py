from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=2000)]
Money = Annotated[float, Field(ge=0, le=10_000_000, allow_inf_nan=False)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TravelRequest(Model):
    destination: Annotated[str, Field(min_length=1, max_length=200)]
    start_date: date
    end_date: date
    budget_min: Money
    budget_max: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"
    interests: Annotated[list[Text], Field(min_length=1, max_length=20)]
    travelers: int = Field(ge=1, le=20)
    preferences: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_trip(self):
        if not 0 <= (self.end_date - self.start_date).days < 14:
            raise ValueError("Trip must span 1 to 14 inclusive calendar days")
        if self.budget_min > self.budget_max or self.budget_max <= 0:
            raise ValueError("Budget must satisfy 0 <= minimum <= maximum and maximum > 0")
        return self

    @property
    def dates(self):
        return [self.start_date + timedelta(days=i)
                for i in range((self.end_date - self.start_date).days + 1)]


class Source(Model):
    title: Text
    url: Annotated[str, Field(pattern=r"^https?://", max_length=2000)]
    excerpt: str = Field(max_length=6000)


class Research(Model):
    summary: Text
    attractions: list[Text] = Field(min_length=1, max_length=15)
    local_tips: list[Text] = Field(min_length=1, max_length=10)
    safety: list[Text] = Field(min_length=1, max_length=10)
    weather_and_season: Text
    uncertainties: list[Text] = Field(max_length=15)


class Activity(Model):
    time: Literal["morning", "afternoon", "evening"]
    name: Text
    description: Text
    estimated_cost: Money
    source_url: Annotated[str, Field(pattern=r"^https?://", max_length=2000)]


class Day(Model):
    date: date
    activities: list[Activity] = Field(min_length=1, max_length=3)
    meals_cost: Money
    transport_cost: Money
    lodging_cost: Money

    @model_validator(mode="after")
    def distinct_slots(self):
        if len({a.time for a in self.activities}) != len(self.activities):
            raise ValueError("Activity time slots must be unique within a day")
        return self


class Itinerary(Model):
    title: Text
    accommodation: Text
    days: list[Day] = Field(min_length=1, max_length=14)
    packing_list: list[Text] = Field(min_length=1, max_length=30)
    notes: list[Text] = Field(min_length=1, max_length=20)

    @property
    def total(self):
        return round(sum(d.meals_cost + d.transport_cost + d.lodging_cost
                         + sum(a.estimated_cost for a in d.activities) for d in self.days), 2)

    def check_trip(self, request: TravelRequest, sources: list[dict]):
        if [d.date for d in self.days] != request.dates:
            raise ValueError("Plan must cover every trip date exactly once, in order")
        if self.total > request.budget_max:
            raise ValueError("Estimated group cost exceeds maximum budget")
        urls = {s["url"] for s in sources}
        if any(a.source_url not in urls for d in self.days for a in d.activities):
            raise ValueError("Activity citations must reference retrieved research sources")
        return self


class DayEdit(Model):
    day: Day


class PlanChanges(Model):
    days: list[Day] = Field(default_factory=list, max_length=14)
    accommodation: Text | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.days and self.accommodation is None:
            raise ValueError("Provide days or accommodation to modify")
        if len({d.date for d in self.days}) != len(self.days):
            raise ValueError("Duplicate edited dates")
        return self


class Review(Model):
    action: Literal["approve", "reject", "modify"]
    revision: int = Field(ge=1)
    feedback: str = Field(default="", max_length=2000)
    rerun_research: bool = False
    changes: PlanChanges | None = None

    @model_validator(mode="after")
    def valid_action(self):
        if self.action == "reject" and not self.feedback:
            raise ValueError("Rejection requires feedback")
        if (self.action == "modify") != (self.changes is not None):
            raise ValueError("Only modify requires changes")
        if self.action != "reject" and (self.feedback or self.rerun_research):
            raise ValueError("Feedback and rerun_research are only for rejection")
        return self


def apply_changes(draft: dict, changes: PlanChanges, request: TravelRequest, sources: list[dict]):
    plan = Itinerary.model_validate(draft)
    edits = {d.date: d for d in changes.days}
    if not set(edits) <= set(request.dates):
        raise ValueError("Edited date is outside the trip")
    plan.days = [edits.get(day.date, day) for day in plan.days]
    if changes.accommodation is not None:
        plan.accommodation = changes.accommodation
    return plan.check_trip(request, sources).model_dump(mode="json")
