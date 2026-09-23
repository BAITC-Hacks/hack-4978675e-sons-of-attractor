import re
from datetime import date as Date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATE_MIN = Date(2026, 9, 23)
DATE_MAX = Date(2026, 12, 31)
MAX_SAFE_INTEGER = 9_007_199_254_740_991
Money = Annotated[int, Field(strict=True, gt=0, le=MAX_SAFE_INTEGER)]
Count = Annotated[int, Field(strict=True, ge=0)]
Hours = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
ExplanationMode = Literal["approved_facts", "structured_only", "live_quotes"]
Reason = Literal["busy", "format", "budget", "language", "duration"]


def parse_calendar_date(value: object) -> Date:
    if type(value) is Date:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        result = Date.fromisoformat(value)
    else:
        raise ValueError("Ожидается дата в формате YYYY-MM-DD")
    if not DATE_MIN <= result <= DATE_MAX:
        raise ValueError("Нет данных о занятости за эту дату")
    return result


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RecommendationQuery(DTO):
    city: str = Field(min_length=1, strict=True)
    date: Date
    event_format: str = Field(min_length=1, strict=True)
    category: str = Field(min_length=1, strict=True)
    budget_kzt: Money
    duration_hours: Hours | None = None
    language: str | None = Field(default=None, min_length=1, strict=True)

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, value: object) -> Date:
        return parse_calendar_date(value)


class Versions(DTO):
    dataset: str
    facts: str | None = None
    algorithm: str = "selection-v1"


class DemoQuery(DTO):
    id: str
    label: str
    query: RecommendationQuery


class AIFeature(DTO):
    enabled: bool = False
    provider: Literal["openai", "anthropic"] | None = None
    model: str | None = None


class AICapabilities(DTO):
    text_input: AIFeature = Field(default_factory=AIFeature)
    answers: AIFeature = Field(default_factory=AIFeature)


class AnswerGeneration(DTO):
    status: Literal["disabled", "generated", "fallback", "not_needed"] = "disabled"
    provider: Literal["openai", "anthropic"] | None = None
    model: str | None = None


class MetaResponse(DTO):
    cities: list[str]
    categories: list[str]
    event_formats: list[str]
    languages: list[str]
    date_min: Date = DATE_MIN
    date_max: Date = DATE_MAX
    versions: Versions
    explanation_mode: ExplanationMode
    demo_queries: list[DemoQuery] = Field(default_factory=list)
    ai: AICapabilities = Field(default_factory=AICapabilities)


class HealthResponse(DTO):
    status: Literal["ok"] = "ok"
    catalog_count: Count
    explanation_mode: ExplanationMode
    versions: Versions


class ErrorDetail(DTO):
    code: str
    message: str
    fields: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(DTO):
    error: ErrorDetail


class Evidence(DTO):
    id: str
    kind: Literal["field", "derived", "description"]
    source_field: str
    text: str
    quote: str | None = None
    fact_id: str | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.kind == "description" and (not self.quote or not self.fact_id or self.source_field != "description"):
            raise ValueError("Description evidence requires a quote, fact_id and description source")
        if self.kind == "derived" and self.source_field != "budget_kzt-price_from_kzt":
            raise ValueError("Unknown derived evidence source")
        return self


class DataFlags(DTO):
    synthetic: bool
    price_imputed: bool
    city_imputed: bool


class Rank(DTO):
    format_evidence: bool
    starting_price_kzt: Money
    tied_on_policy: bool


class Card(DTO):
    id: str
    anon_name: str
    matched_category: str
    city: str
    categories: list[str]
    languages: list[str]
    price_from_kzt: Money
    budget_headroom_kzt: Count
    max_hours: Hours | None
    explanation: str
    evidence: list[Evidence]
    data_flags: DataFlags
    rank: Rank
    comparison_note: str | None = None

    @model_validator(mode="after")
    def unique_evidence(self):
        if len({e.id for e in self.evidence}) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique within a card")
        return self


class Counts(DTO):
    catalog_count: Count
    eligible_count: Count
    shown_count: Annotated[int, Field(strict=True, ge=0, le=3)]


class ReasonCounts(DTO):
    busy: Count = 0
    format: Count = 0
    budget: Count = 0
    language: Count = 0
    duration: Count = 0


class Exclusion(DTO):
    id: str
    reasons: list[Reason] = Field(min_length=1)


class Diagnostics(DTO):
    reason_counts: ReasonCounts
    otherwise_eligible_but_busy: Count
    exclusions: list[Exclusion]


class DateSuggestion(DTO):
    field: Literal["date"]
    value: Date
    query: RecommendationQuery
    eligible_count: Count
    shown_count: Annotated[int, Field(strict=True, ge=0, le=3)]
    message: str


class BudgetSuggestion(DTO):
    field: Literal["budget_kzt"]
    value: Money
    query: RecommendationQuery
    eligible_count: Count
    shown_count: Annotated[int, Field(strict=True, ge=0, le=3)]
    message: str


class CityAlternative(DTO):
    city: str
    catalog_count: Count


class RecommendationResponse(DTO):
    status: Literal["found", "no_category_in_city", "all_filtered"]
    query: RecommendationQuery
    summary: str
    cards: list[Card] = Field(max_length=3)
    counts: Counts
    diagnostics: Diagnostics
    suggestions: list[Annotated[DateSuggestion | BudgetSuggestion, Field(discriminator="field")]] = Field(max_length=3)
    city_alternatives: list[CityAlternative]
    versions: Versions
    explanation_mode: ExplanationMode
    warnings: list[str]
    answer_generation: AnswerGeneration = Field(default_factory=AnswerGeneration)

    @model_validator(mode="after")
    def validate_result(self):
        if self.counts.shown_count != len(self.cards) or self.counts.shown_count != min(3, self.counts.eligible_count):
            raise ValueError("Inconsistent result counts")
        if len({card.id for card in self.cards}) != len(self.cards):
            raise ValueError("Duplicate cards")
        if self.counts.eligible_count > self.counts.catalog_count:
            raise ValueError("Eligible count exceeds the category catalog")
        if sum(s.field == "date" for s in self.suggestions) > 2 or sum(s.field == "budget_kzt" for s in self.suggestions) > 1:
            raise ValueError("Too many suggestions for one field")
        for suggestion in self.suggestions:
            different = [field for field in type(self.query).model_fields
                         if getattr(self.query, field) != getattr(suggestion.query, field)]
            if different != [suggestion.field] or getattr(suggestion.query, suggestion.field) != suggestion.value:
                raise ValueError("Suggestion must change only its declared field")
            if suggestion.shown_count != min(3, suggestion.eligible_count) or suggestion.shown_count <= self.counts.shown_count:
                raise ValueError("Suggestion must increase the shown count")
        return self
