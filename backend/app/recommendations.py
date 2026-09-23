from .catalog import Catalog
from .explanations import build_cards
from .schemas import Counts, RecommendationQuery, RecommendationResponse, Versions
from .selection import evaluate, summarize
from .suggestions import city_alternatives, suggestions


def recommend(query: RecommendationQuery, catalog: Catalog) -> RecommendationResponse:
    result = evaluate(query, catalog)
    cards = build_cards(query, catalog, result)
    alternatives = city_alternatives(query, catalog) if result.status == "no_category_in_city" else []
    changes = suggestions(query, catalog, result)
    summary = summarize(result)
    if alternatives:
        summary += " В других городах есть профили этой категории; их доступность и соответствие остальным условиям не проверены."
    elif result.catalog_count and len(result.eligible) < 3 and not changes:
        summary += (" Изменение только даты или только бюджета не добавляет вариантов в известных данных; "
                    "пересмотрите остальные условия вручную.")
    warnings = []
    if catalog.facts is not None and catalog.facts.warning:
        warnings.append(catalog.facts.warning)
    elif catalog.facts is None:
        warnings.append("AI-реестр отсутствует; используются только структурированные поля.")
    return RecommendationResponse(
        status=result.status, query=query, summary=summary, cards=cards,
        counts=Counts(catalog_count=result.catalog_count, eligible_count=len(result.eligible), shown_count=len(cards)),
        diagnostics=result.diagnostics, suggestions=changes, city_alternatives=alternatives,
        versions=Versions(dataset=catalog.sha256, facts=catalog.facts_sha256),
        explanation_mode=catalog.explanation_mode, warnings=warnings,
    )
