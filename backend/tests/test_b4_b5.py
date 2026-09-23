from dataclasses import replace
from datetime import date, timedelta

import pytest

from backend.app.catalog import Catalog, Contractor, load_catalog
from backend.app.core.config import PROJECT_ROOT
from backend.app.explanations import select_facts
from backend.app.facts import Fact, FactsSnapshot, Generator, ProfileFacts, Registry, description_hash, validate_registry
from backend.app.recommendations import recommend
from backend.app.schemas import DATE_MAX, DATE_MIN, RecommendationQuery
from backend.app.selection import evaluate


def query(**changes):
    return RecommendationQuery.model_validate(dict(
        city="Алматы", category="Банкетный зал", date="2026-11-13", event_format="корпоратив",
        budget_kzt=7_000_000, language="русский", duration_hours=6,
    ) | changes)


def profile(id="P1", **changes):
    return Contractor.model_validate(dict(
        id=id, anon_name="Тест", city="Алматы", categories=("Банкетный зал",),
        event_formats=("корпоратив",), languages=("русский",), busy_dates=(),
        price_from_kzt=100, max_hours=6, description="Общий состав. Струнный квартет. Духовая группа. Корпоративы.",
        synthetic=False, price_imputed=False, city_imputed=False,
    ) | changes)


def with_facts(catalog, mapping):
    registry = Registry(
        schema_version=1, dataset_sha256=catalog.sha256,
        generator=Generator(provider="test", model="test-fixture", method="test_fixture"),
        profiles=tuple(ProfileFacts(contractor_id=p.id, description_sha256=description_hash(p.description),
                                     review_status="approved" if mapping.get(p.id) else "no_usable_facts",
                                     facts=tuple(mapping.get(p.id, ()))) for p in catalog.profiles),
    )
    validate_registry(registry, catalog)
    return replace(catalog, facts=FactsSnapshot(registry, "b" * 64, None))


def test_distinct_fact_preferred_over_shared_quote():
    catalog = Catalog((profile(), profile("P2")), "a" * 64)
    catalog = with_facts(catalog, {
        "P1": [Fact(id="a1", quote="Общий состав.", event_formats=()), Fact(id="z1", quote="Струнный квартет.", event_formats=())],
        "P2": [Fact(id="a2", quote="Общий состав.", event_formats=()), Fact(id="z2", quote="Духовая группа.", event_formats=())],
    })
    result = recommend(query(), catalog)
    assert "Струнный квартет." in result.cards[0].explanation
    assert "Духовая группа." in result.cards[1].explanation
    assert all(c.comparison_note is None for c in result.cards)
    assert result.cards[0].evidence[-1].fact_id == "z1"


def test_direct_format_fact_has_priority_then_id():
    catalog = Catalog((profile(), profile("P2")), "a" * 64)
    catalog = with_facts(catalog, {
        "P1": [Fact(id="a", quote="Струнный квартет.", event_formats=()), Fact(id="z", quote="Корпоративы.", event_formats=("корпоратив",))],
        "P2": [Fact(id="b", quote="Корпоративы.", event_formats=("корпоратив",)), Fact(id="c", quote="Духовая группа.", event_formats=())],
    })
    selected = select_facts(query(), catalog, catalog.profiles)
    assert selected["P1"].id == "z" and selected["P2"].id == "b"
    assert all(card.comparison_note for card in recommend(query(), catalog).cards)
    single = with_facts(Catalog((profile(),), "a" * 64), {
        "P1": [Fact(id="z", quote="Струнный квартет.", event_formats=()), Fact(id="a", quote="Духовая группа.", event_formats=())],
    })
    assert select_facts(query(), single, single.profiles)["P1"].id == "a"


def test_actual_band_descriptions_remain_distinct_without_names():
    source = load_catalog(PROJECT_ROOT / "backend/data/catalog.csv")
    bands = tuple(p for p in source.profiles if p.id in {"HK-23752", "HK-83709"})
    catalog = Catalog(bands, source.sha256)
    catalog = with_facts(catalog, {
        "HK-23752": [Fact(id="test-band-1", quote="🎤 два вокалиста 🎤 вокалистка 🥁 барабанщик 🎸 бас-гитарист 🎸 соло-гитарист 🎺 труба 🎷 саксофон 🎵 тромбон", event_formats=())],
        "HK-83709": [Fact(id="test-band-2", quote="Большой музыкальный состав: 4 вокалиста, струнный квартет, духовой брасс, перкуссионист, клавишник, барабанщик, гитарист и бас-гитарист.", event_formats=())],
    })
    cards = recommend(query(category="Лайв-бэнд", budget_kzt=1_200_000, language="казахский"), catalog).cards
    assert [c.id for c in cards] == ["HK-23752", "HK-83709"]
    assert "два вокалиста" in cards[0].explanation and "саксофон" in cards[0].explanation
    assert "4 вокалиста" in cards[1].explanation and "струнный квартет" in cards[1].explanation
    assert all(c.comparison_note is None for c in cards)
    assert all(not c.rank.format_evidence for c in cards)
    assert all("Thunder" not in c.explanation and "Eva" not in c.explanation for c in cards)


def test_field_differences_and_honest_null_hours():
    catalog = Catalog((profile(max_hours=None, price_imputed=True), profile("P2", max_hours=8)), "a" * 64)
    cards = recommend(query(), catalog).cards
    assert "оценочная" in cards[0].explanation
    assert "неприменимо" in cards[0].explanation
    assert "8 ч" in cards[1].explanation
    assert all(c.comparison_note is None for c in cards)
    assert any(e.source_field == "price_imputed" for e in cards[0].evidence)
    for card in cards:
        calendar = next(e for e in card.evidence if e.source_field == "busy_dates")
        assert "2026-09-23" in calendar.text and "2026-12-31" in calendar.text
        assert "гарантированно" not in card.explanation and "безлимит" not in card.explanation


def test_identical_fields_need_comparison_note():
    cards = recommend(query(), Catalog((profile(), profile("P2")), "a" * 64)).cards
    assert all(c.comparison_note for c in cards)
    assert all(e.kind != "description" for c in cards for e in c.evidence)


@pytest.mark.parametrize("changes,expected_dates,expected_budget", [
    ({"date": "2026-12-19"}, [("2026-12-18", 1), ("2026-12-20", 1)], None),
    ({"category": "Ведущий", "date": "2026-11-14", "budget_kzt": 600_000}, None, 650_000),
    ({"category": "Ведущий", "date": "2026-12-12", "budget_kzt": 1_500_000}, [("2026-12-11", 1), ("2026-12-13", 2)], None),
])
def test_spec_suggestion_scenarios(changes, expected_dates, expected_budget):
    catalog = load_catalog(PROJECT_ROOT / "backend/data/catalog.csv")
    request = query(**changes)
    result = recommend(request, catalog)
    dates = [s for s in result.suggestions if s.field == "date"]
    budgets = [s for s in result.suggestions if s.field == "budget_kzt"]
    if expected_dates is not None:
        assert [(s.value.isoformat(), s.eligible_count) for s in dates] == expected_dates
    assert [s.value for s in budgets] == ([] if expected_budget is None else [expected_budget])
    if budgets:
        assert "+50 000" in budgets[0].message
    for suggestion in result.suggestions:
        changed = [key for key in type(request).model_fields if getattr(request, key) != getattr(suggestion.query, key)]
        assert changed == [suggestion.field]
        applied = evaluate(suggestion.query, catalog)
        assert len(applied.eligible) == suggestion.eligible_count
        assert len(applied.shown) == suggestion.shown_count > len(result.cards)


def test_two_independent_obstacles_do_not_get_false_advice():
    catalog = Catalog((profile(event_formats=("свадьба",), price_from_kzt=200),), "a" * 64)
    result = recommend(query(budget_kzt=100), catalog)
    assert result.status == "all_filtered" and result.suggestions == []
    assert "Изменение только даты или только бюджета не добавляет" in result.summary


def test_partial_result_no_advice_does_not_claim_nobody_found():
    result = recommend(query(), Catalog((profile(),), "a" * 64))
    assert len(result.cards) == 1 and not result.suggestions
    assert "Подходящих вариантов нет" not in result.summary
    assert "пересмотрите остальные условия" in result.summary


def test_minimum_budget_threshold_and_three_card_cap():
    catalog = Catalog((profile("P1", price_from_kzt=200), profile("P2", price_from_kzt=300),
                       profile("P3", price_from_kzt=300), profile("P4", price_from_kzt=400)), "a" * 64)
    result = recommend(query(budget_kzt=100), catalog)
    budget = next(s for s in result.suggestions if s.field == "budget_kzt")
    assert budget.value == 200 and budget.shown_count == 1
    assert recommend(query(budget_kzt=300), catalog).suggestions == []


@pytest.mark.parametrize("day,next_day", [(DATE_MIN, DATE_MIN + timedelta(days=1)), (DATE_MAX, DATE_MAX - timedelta(days=1))])
def test_date_suggestions_stay_inside_window(day, next_day):
    catalog = Catalog((profile(busy_dates=(day,)),), "a" * 64)
    result = recommend(query(date=day), catalog)
    assert result.suggestions[0].value == next_day
    assert all(DATE_MIN <= s.value <= DATE_MAX and s.value != day for s in result.suggestions)


def test_nearest_dates_searches_entire_window():
    target = DATE_MAX
    busy = tuple(DATE_MIN + timedelta(days=i) for i in range((DATE_MAX - DATE_MIN).days))
    result = recommend(query(date=DATE_MIN), Catalog((profile(busy_dates=busy),), "a" * 64))
    assert len(result.suggestions) == 1 and result.suggestions[0].value == target


def test_city_alternatives_only_count_category_not_availability():
    catalog = Catalog((profile(city="Астана", busy_dates=(date(2026, 11, 13),)),
                       profile("P2", city="Астана", price_from_kzt=999),
                       profile("P3", city="Актау")), "a" * 64)
    result = recommend(query(budget_kzt=100), catalog)
    assert result.status == "no_category_in_city" and not result.suggestions
    assert [(a.city, a.catalog_count) for a in result.city_alternatives] == [("Актау", 1), ("Астана", 2)]
    assert "не проверены" in result.summary
