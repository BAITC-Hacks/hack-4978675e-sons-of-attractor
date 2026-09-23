from collections import Counter

from .catalog import Catalog, Contractor
from .facts import Fact
from .schemas import DATE_MAX, DATE_MIN, Card, DataFlags, Evidence, Rank, RecommendationQuery
from .selection import Evaluation


def quote_key(quote: str) -> str:
    return " ".join(quote.casefold().split())


def select_facts(query: RecommendationQuery, catalog: Catalog, shown: tuple[Contractor, ...]) -> dict[str, Fact | None]:
    by_profile = {p.id: catalog.facts.for_profile(p.id) if catalog.facts is not None else () for p in shown}
    frequency = Counter(key for facts in by_profile.values() for key in {quote_key(f.quote) for f in facts})
    return {
        id_: min(facts, key=lambda f: (query.event_format not in f.event_formats,
                                     frequency[quote_key(f.quote)] > 1, f.id)) if facts else None
        for id_, facts in by_profile.items()
    }


def field_signature(profile: Contractor):
    return profile.price_from_kzt, tuple(sorted(profile.languages)), profile.max_hours


def distinguish_cards(cards: list[Card]) -> list[Card]:
    """Resolve actual text collisions, including a third card masking a pair."""
    groups: dict[str, list[Card]] = {}
    for card in cards:
        groups.setdefault(quote_key(card.explanation), []).append(card)
    for group in groups.values():
        if len(group) < 2:
            continue
        hours_differ = len({c.max_hours for c in group}) > 1
        languages_differ = len({tuple(c.languages) for c in group}) > 1
        for card in group:
            details = []
            if hours_differ:
                details.append(f"присутствие — до {card.max_hours:g} ч" if card.max_hours is not None
                               else "ограничение присутствия по часам неприменимо")
            if languages_differ:
                details.append("языки: " + ", ".join(card.languages))
            if details:
                first, dot, rest = card.explanation.partition(".")
                card.explanation = first + "; " + "; ".join(details) + dot + rest
        repeats = Counter(quote_key(c.explanation) for c in group)
        for card in group:
            card.comparison_note = ("По доступным сведениям варианты не удаётся содержательно различить"
                                    if repeats[quote_key(card.explanation)] > 1 else None)
    return cards


def build_cards(query: RecommendationQuery, catalog: Catalog, result: Evaluation) -> list[Card]:
    selected = select_facts(query, catalog, result.shown)
    signatures = Counter((*field_signature(p), quote_key(selected[p.id].quote) if selected[p.id] else None)
                         for p in result.shown)
    policies = Counter((catalog.format_evidence(p.id, query.event_format), p.price_from_kzt) for p in result.eligible)
    cards = []
    for profile in result.shown:
        fact = selected[profile.id]
        headroom = query.budget_kzt - profile.price_from_kzt
        price_text = f"{profile.price_from_kzt:,}".replace(",", " ")
        headroom_text = f"{headroom:,}".replace(",", " ")
        price_mark = " (оценочная)" if profile.price_imputed else ""
        explanation = (f"Цена от {price_text} ₸{price_mark}, запас бюджета — {headroom_text} ₸; "
                       "итоговая стоимость неизвестна.")
        evidence = [
            Evidence(id="price", kind="field", source_field="price_from_kzt",
                     text=f"Начальная цена: {profile.price_from_kzt} ₸; итоговая стоимость неизвестна."),
            Evidence(id="headroom", kind="derived", source_field="budget_kzt-price_from_kzt",
                     text=f"Бюджет {query.budget_kzt} ₸ − начальная цена {profile.price_from_kzt} ₸ = {headroom} ₸."),
            Evidence(id="calendar", kind="field", source_field="busy_dates",
                     text=(f"По календарю не отмечен занятым {query.date.isoformat()}: эта дата отсутствует в busy_dates; "
                           f"известное окно {DATE_MIN.isoformat()}–{DATE_MAX.isoformat()}.")),
            Evidence(id="format", kind="field", source_field="event_formats",
                     text=f"Формат «{query.event_format}» указан в event_formats."),
            Evidence(id="languages", kind="field", source_field="languages",
                     text="Языки по каталогу: " + ", ".join(sorted(profile.languages)) + "."),
            Evidence(id="hours", kind="field", source_field="max_hours",
                     text=(f"Максимальное присутствие: {profile.max_hours:g} ч." if profile.max_hours is not None
                           else "max_hours не задан: ограничение присутствия по часам неприменимо по схеме каталога.")),
        ]
        if profile.price_imputed:
            evidence.append(Evidence(id="estimated-price", kind="field", source_field="price_imputed",
                                     text="Цена проставлена при подготовке датасета и является оценочной."))
        if fact:
            explanation += f" В описании: «{fact.quote}»"
            evidence.append(Evidence(id=f"description-{fact.id}", kind="description", source_field="description",
                                     text="Цитата из описания в каталоге; независимая проверка сведений не проводилась.",
                                     quote=fact.quote, fact_id=fact.id))
        else:
            peers = [p for p in result.shown if p.id != profile.id]
            if peers and any(tuple(sorted(p.languages)) != tuple(sorted(profile.languages)) for p in peers):
                explanation += " Языки по каталогу: " + ", ".join(sorted(profile.languages)) + "."
            elif peers and any(p.max_hours != profile.max_hours for p in peers):
                explanation += (f" Максимальное присутствие — {profile.max_hours:g} ч." if profile.max_hours is not None
                                else " Ограничение присутствия по часам неприменимо по схеме каталога.")
        signature = (*field_signature(profile), quote_key(fact.quote) if fact else None)
        format_evidence = catalog.format_evidence(profile.id, query.event_format)
        cards.append(Card(
            id=profile.id, anon_name=profile.anon_name, matched_category=query.category, city=profile.city,
            categories=sorted(profile.categories), languages=sorted(profile.languages), price_from_kzt=profile.price_from_kzt,
            budget_headroom_kzt=headroom, max_hours=profile.max_hours, explanation=explanation, evidence=evidence,
            data_flags=DataFlags(synthetic=profile.synthetic, price_imputed=profile.price_imputed, city_imputed=profile.city_imputed),
            rank=Rank(format_evidence=format_evidence, starting_price_kzt=profile.price_from_kzt,
                      tied_on_policy=policies[(format_evidence, profile.price_from_kzt)] > 1),
            comparison_note=("По доступным сведениям варианты не удаётся содержательно различить"
                             if signatures[signature] > 1 else None),
        ))
    return distinguish_cards(cards)
