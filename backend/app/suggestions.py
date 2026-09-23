from collections import Counter
from datetime import timedelta

from .catalog import Catalog
from .schemas import DATE_MAX, DATE_MIN, BudgetSuggestion, CityAlternative, DateSuggestion, RecommendationQuery
from .selection import Evaluation, evaluate


def suggestions(query: RecommendationQuery, catalog: Catalog, result: Evaluation) -> list[DateSuggestion | BudgetSuggestion]:
    if not result.catalog_count or len(result.eligible) >= 3:
        return []
    current_count = len(result.shown)
    dates = []
    day = DATE_MIN
    while day <= DATE_MAX:
        if day != query.date:
            changed = query.model_copy(update={"date": day})
            evaluated = evaluate(changed, catalog)
            if len(evaluated.shown) > current_count:
                dates.append(DateSuggestion(
                    field="date", value=day, query=changed,
                    eligible_count=len(evaluated.eligible), shown_count=len(evaluated.shown),
                    message=(f"Если изменить только дату на {day.isoformat()}, "
                             f"подойдут {len(evaluated.eligible)}, будут показаны {len(evaluated.shown)}."),
                ))
        day += timedelta(days=1)
    dates.sort(key=lambda s: (abs((s.value - query.date).days), s.value))
    answer: list[DateSuggestion | BudgetSuggestion] = list(dates[:2])
    budget_only = {entry.id for entry in result.diagnostics.exclusions if entry.reasons == ["budget"]}
    thresholds = sorted({p.price_from_kzt for p in catalog.profiles if p.id in budget_only})
    for threshold in thresholds:
        changed = query.model_copy(update={"budget_kzt": threshold})
        evaluated = evaluate(changed, catalog)
        if len(evaluated.shown) > current_count:
            increase = threshold - query.budget_kzt
            threshold_text = f"{threshold:,}".replace(",", " ")
            increase_text = f"{increase:,}".replace(",", " ")
            answer.append(BudgetSuggestion(
                field="budget_kzt", value=threshold, query=changed,
                eligible_count=len(evaluated.eligible), shown_count=len(evaluated.shown),
                message=(f"Если увеличить только бюджет до {threshold_text} ₸ (+{increase_text} ₸), "
                         f"подойдут {len(evaluated.eligible)}, будут показаны {len(evaluated.shown)}; "
                         "это порог по начальной цене, итоговая стоимость неизвестна."),
            ))
            break
    return answer


def city_alternatives(query: RecommendationQuery, catalog: Catalog) -> list[CityAlternative]:
    counts = Counter(p.city for p in catalog.profiles if query.category in p.categories and p.city != query.city)
    return [CityAlternative(city=city, catalog_count=count) for city, count in sorted(counts.items())]
