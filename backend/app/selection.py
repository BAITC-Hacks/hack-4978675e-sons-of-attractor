from dataclasses import dataclass
from typing import Literal

from .catalog import Catalog, Contractor
from .schemas import Diagnostics, Exclusion, Reason, ReasonCounts, RecommendationQuery

REASON_ORDER: tuple[Reason, ...] = ("busy", "format", "budget", "language", "duration")
REASON_LABELS = {
    "busy": "заняты по календарю",
    "format": "не принимают выбранный формат",
    "budget": "начальная цена выше бюджета",
    "language": "нет выбранного языка",
    "duration": "недостаточная длительность присутствия",
}


@dataclass(frozen=True)
class Evaluation:
    status: Literal["found", "no_category_in_city", "all_filtered"]
    catalog_count: int
    eligible: tuple[Contractor, ...]
    diagnostics: Diagnostics

    @property
    def shown(self) -> tuple[Contractor, ...]:
        return self.eligible[:3]


def evaluate(query: RecommendationQuery, catalog: Catalog) -> Evaluation:
    candidates = sorted(
        (p for p in catalog.profiles if p.city == query.city and query.category in p.categories),
        key=lambda p: p.id,
    )
    eligible = []
    exclusions = []
    reason_counts = dict.fromkeys(REASON_ORDER, 0)
    otherwise_busy = 0
    for profile in candidates:
        violations = {
            "busy": query.date in profile.busy_dates,
            "format": query.event_format not in profile.event_formats,
            "budget": profile.price_from_kzt > query.budget_kzt,
            "language": query.language is not None and query.language not in profile.languages,
            "duration": (query.duration_hours is not None and profile.max_hours is not None
                         and profile.max_hours < query.duration_hours),
        }
        reasons = [reason for reason in REASON_ORDER if violations[reason]]
        if reasons:
            exclusions.append(Exclusion(id=profile.id, reasons=reasons))
            for reason in reasons:
                reason_counts[reason] += 1
            otherwise_busy += reasons == ["busy"]
        else:
            eligible.append(profile)

    eligible.sort(key=lambda p: (-int(catalog.format_evidence(p.id, query.event_format)), p.price_from_kzt, p.id))
    status = "found" if eligible else "all_filtered" if candidates else "no_category_in_city"
    return Evaluation(
        status=status,
        catalog_count=len(candidates),
        eligible=tuple(eligible),
        diagnostics=Diagnostics(
            reason_counts=ReasonCounts(**reason_counts),
            otherwise_eligible_but_busy=otherwise_busy,
            exclusions=exclusions,
        ),
    )


def summarize(result: Evaluation) -> str:
    if result.status == "no_category_in_city":
        return "В выбранном городе нет профилей выбранной категории."
    count = len(result.eligible)
    if count >= 3:
        return f"Показаны 3 из {count} подходящих."
    parts = [f"Показаны {count} из {count} подходящих." if count else "Подходящих вариантов нет."]
    if result.catalog_count < 3:
        parts.append(f"В каталоге выбранного города и категории всего профилей: {result.catalog_count}.")
    if result.diagnostics.exclusions:
        parts.append(f"Не прошли условия: {len(result.diagnostics.exclusions)} из {result.catalog_count}.")
        reasons = result.diagnostics.reason_counts.model_dump()
        parts.append("Причины: " + "; ".join(
            f"{REASON_LABELS[reason]} — {reasons[reason]}" for reason in REASON_ORDER if reasons[reason]
        ) + ". Один профиль может иметь несколько причин отказа.")
    return " ".join(parts)
