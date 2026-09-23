from collections import Counter

from .catalog import Catalog, Contractor
from .facts import Fact
from .schemas import (
    DATE_MAX, DATE_MIN, Card, Counts, DataFlags, Evidence, Rank,
    RecommendationQuery, RecommendationResponse, Versions,
)
from .selection import evaluate, summarize


def make_card(query: RecommendationQuery, profile: Contractor, tied: bool, indistinguishable: bool,
              facts: tuple[Fact, ...] = (), format_evidence: bool = False) -> Card:
    headroom = query.budget_kzt - profile.price_from_kzt
    price_mark = " (оценочная)" if profile.price_imputed else ""
    price_text = f"{profile.price_from_kzt:,}".replace(",", " ")
    headroom_text = f"{headroom:,}".replace(",", " ")
    card = Card(
        id=profile.id, anon_name=profile.anon_name, matched_category=query.category,
        city=profile.city, categories=sorted(profile.categories), languages=sorted(profile.languages),
        price_from_kzt=profile.price_from_kzt, budget_headroom_kzt=headroom, max_hours=profile.max_hours,
        explanation=(f"Цена от {price_text} ₸{price_mark}; запас бюджета — {headroom_text} ₸, "
                     "итоговая стоимость неизвестна. "
                     f"Принимает формат «{query.event_format}»; по календарю не отмечен занятым {query.date.isoformat()}."),
        evidence=[
            Evidence(id="price", kind="field", source_field="price_from_kzt",
                     text=f"Начальная цена: {profile.price_from_kzt} ₸{price_mark}; итоговая стоимость неизвестна."),
            Evidence(id="headroom", kind="derived", source_field="budget_kzt-price_from_kzt",
                     text=f"Бюджет {query.budget_kzt} ₸ − начальная цена {profile.price_from_kzt} ₸ = {headroom} ₸."),
            Evidence(id="format", kind="field", source_field="event_formats",
                     text=f"Формат «{query.event_format}» указан в event_formats."),
            Evidence(id="calendar", kind="field", source_field="busy_dates",
                     text=f"Дата {query.date.isoformat()} отсутствует в busy_dates; известное окно {DATE_MIN.isoformat()}–{DATE_MAX.isoformat()}."),
        ],
        data_flags=DataFlags(synthetic=profile.synthetic, price_imputed=profile.price_imputed,
                             city_imputed=profile.city_imputed),
        rank=Rank(format_evidence=format_evidence, starting_price_kzt=profile.price_from_kzt, tied_on_policy=tied),
        comparison_note="По доступным сведениям варианты не удаётся содержательно различить" if indistinguishable else None,
    )
    if facts:
        fact = min(facts, key=lambda f: (query.event_format not in f.event_formats, f.id))
        card.evidence.append(Evidence(id=f"description-{fact.id}", kind="description", source_field="description",
                                      text="Цитата из описания в каталоге; независимая проверка сведений не проводилась.",
                                      quote=fact.quote, fact_id=fact.id))
        card.explanation = (f"Цена от {price_text} ₸{price_mark}; запас бюджета — {headroom_text} ₸, "
                            f"итоговая стоимость неизвестна. В описании: «{fact.quote}»")
    return card


def recommend(query: RecommendationQuery, catalog: Catalog) -> RecommendationResponse:
    result = evaluate(query, catalog)
    policy = Counter((catalog.format_evidence(p.id, query.event_format), p.price_from_kzt) for p in result.eligible)
    def comparison_key(profile: Contractor):
        return profile.price_from_kzt, tuple(sorted(profile.languages)), profile.max_hours

    signatures = Counter(comparison_key(p) for p in result.shown)
    cards = []
    for p in result.shown:
        format_evidence = catalog.format_evidence(p.id, query.event_format)
        cards.append(make_card(query, p, policy[(format_evidence, p.price_from_kzt)] > 1,
                               signatures[comparison_key(p)] > 1,
                               catalog.facts.for_profile(p.id) if catalog.facts is not None else (), format_evidence))
    warnings = []
    if catalog.facts is not None and catalog.facts.warning:
        warnings.append(catalog.facts.warning)
    elif catalog.facts is None:
        warnings.append("AI-реестр отсутствует; используются только структурированные поля.")
    if len(result.eligible) < 3:
        warnings.append("Автоматические предложения изменить запрос и альтернативные города пока недоступны.")
    return RecommendationResponse(
        status=result.status, query=query, summary=summarize(result), cards=cards,
        counts=Counts(catalog_count=result.catalog_count, eligible_count=len(result.eligible), shown_count=len(cards)),
        diagnostics=result.diagnostics, suggestions=[], city_alternatives=[],
        versions=Versions(dataset=catalog.sha256, facts=catalog.facts_sha256),
        explanation_mode=catalog.explanation_mode, warnings=warnings,
    )
