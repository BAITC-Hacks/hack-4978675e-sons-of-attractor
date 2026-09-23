from dataclasses import replace
from datetime import date
from random import Random

import pytest
from fastapi.testclient import TestClient

from backend.app.catalog import Catalog, Contractor, load_catalog
from backend.app.core.config import PROJECT_ROOT, Settings
from backend.app.main import create_app
from backend.app.recommendations import recommend
from backend.app.schemas import RecommendationQuery, RecommendationResponse
from backend.app.selection import evaluate, summarize


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(PROJECT_ROOT / "backend/data/catalog.csv")


@pytest.fixture(scope="module")
def client():
    settings = Settings(PROJECT_ROOT / "backend/data/catalog.csv", PROJECT_ROOT / "backend/data/facts.json",
                        PROJECT_ROOT / "frontend")
    with TestClient(create_app(settings)) as client:
        yield client


def query(**changes):
    return RecommendationQuery.model_validate(dict(
        city="Алматы", category="Банкетный зал", date="2026-11-13",
        event_format="корпоратив", budget_kzt=7_000_000, language="русский", duration_hours=6,
    ) | changes)


def profile(id="P-1", **changes):
    return Contractor.model_validate(dict(
        id=id, anon_name="Тест", city="Алматы", categories=("Банкетный зал",),
        event_formats=("корпоратив",), languages=("русский",), busy_dates=(),
        price_from_kzt=100, max_hours=6, description="Работаем по Казахстану, корпоративы.",
        synthetic=False, city_imputed=False, price_imputed=False,
    ) | changes)


CASES = [
    ({}, "found", 7, 6, ["HK-64395", "HK-58236", "HK-90011"]),
    ({"date": "2026-11-14"}, "found", 7, 2, ["HK-64395", "HK-90011"]),
    ({"category": "Флорист", "budget_kzt": 500_000}, "found", 2, 1, ["HK-39372"]),
    ({"city": "Астана", "category": "Декоратор", "budget_kzt": 3_000_000}, "no_category_in_city", 0, 0, []),
    ({"date": "2026-12-19"}, "all_filtered", 7, 0, []),
    ({"category": "Ведущий", "date": "2026-11-14", "budget_kzt": 600_000}, "all_filtered", None, 0, []),
    ({"category": "Ведущий", "date": "2026-12-12", "budget_kzt": 1_500_000}, "all_filtered", None, 0, []),
    ({"category": "Лайв-бэнд", "budget_kzt": 1_200_000, "language": "казахский"}, "found", None, 2, ["HK-23752", "HK-83709"]),
]


@pytest.mark.parametrize("changes,status,c_count,a_count,ids", CASES)
def test_spec_scenarios_through_api(client, catalog, changes, status, c_count, a_count, ids):
    request = query(**changes)
    response = client.post("/api/recommendations", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    RecommendationResponse.model_validate(body)
    assert body["status"] == status
    if c_count is not None:
        assert body["counts"]["catalog_count"] == c_count
    assert body["counts"]["eligible_count"] == a_count
    assert body["counts"]["shown_count"] == min(3, a_count) == len(body["cards"])
    assert [card["id"] for card in body["cards"]] == ids
    assert len(ids) == len(set(ids))
    assert body["explanation_mode"] == "structured_only"
    assert body["versions"] == client.get("/api/meta").json()["versions"]
    assert body["suggestions"] == [] and body["city_alternatives"] == []
    by_id = {p.id: p for p in catalog.profiles}
    for card in body["cards"]:
        p = by_id[card["id"]]
        assert p.city == request.city and request.category in p.categories
        assert request.date not in p.busy_dates
        assert request.event_format in p.event_formats
        assert request.language in p.languages
        assert p.price_from_kzt <= request.budget_kzt
        assert p.max_hours is None or p.max_hours >= request.duration_hours
        assert card["budget_headroom_kzt"] == request.budget_kzt - p.price_from_kzt
        assert card["rank"]["format_evidence"] is False
        assert len({e["id"] for e in card["evidence"]}) == len(card["evidence"])
    # Same query and snapshot must reproduce the entire response, including text.
    assert client.post("/api/recommendations", json=request.model_dump(mode="json")).json() == body


def test_rejections_overlap_without_inflating_excluded_people():
    candidates = Catalog((
        profile("ALL", busy_dates=(date(2026, 11, 13),), event_formats=("свадьба",),
                price_from_kzt=200, languages=("казахский",), max_hours=5),
        profile("BUSY", busy_dates=(date(2026, 11, 13),)),
        profile("OK"),
    ), "test")
    result = evaluate(query(budget_kzt=100), candidates)
    assert result.diagnostics.exclusions[0].reasons == ["busy", "format", "budget", "language", "duration"]
    assert result.diagnostics.reason_counts.model_dump() == dict(busy=2, format=1, budget=1, language=1, duration=1)
    assert result.diagnostics.otherwise_eligible_but_busy == 1
    assert [p.id for p in result.eligible] == ["OK"]
    assert "Не прошли условия: 2 из 3" in summarize(result)
    assert "несколько причин" in summarize(result)


def test_exact_category_city_and_structured_format():
    candidates = Catalog((
        profile("CEREMONY", categories=("Ведущий церемонии",)),
        profile("MULTI", categories=("Ведущий церемонии", "Ведущий")),
        profile("CITY", city="Астана", categories=("Ведущий",)),
        profile("FORMAT", categories=("Ведущий",), event_formats=("свадьба",)),
    ), "test")
    result = evaluate(query(category="Ведущий"), candidates)
    assert result.catalog_count == 2
    assert [p.id for p in result.eligible] == ["MULTI"]
    assert result.diagnostics.exclusions[0].id == "FORMAT"
    assert result.diagnostics.exclusions[0].reasons == ["format"]


def test_boundaries_optional_filters_and_null_hours():
    candidates = Catalog((profile("BOUNDARY"), profile("NULL", max_hours=None),
                          profile("SHORT", max_hours=5, languages=("казахский",))), "test")
    result = evaluate(query(budget_kzt=100), candidates)
    assert [p.id for p in result.eligible] == ["BOUNDARY", "NULL"]
    assert result.diagnostics.exclusions[0].reasons == ["language", "duration"]
    assert len(evaluate(query(budget_kzt=100, language=None, duration_hours=None), candidates).eligible) == 3
    assert len(evaluate(query(budget_kzt=99), candidates).eligible) == 0


@pytest.mark.parametrize("category", ["Флорист", "Банкетный зал"])
def test_calendar_applies_even_without_max_hours(category):
    candidates = Catalog((profile(categories=(category,), max_hours=None,
                                   busy_dates=(date(2026, 11, 13),)),), "test")
    result = evaluate(query(category=category, duration_hours=None), candidates)
    assert result.status == "all_filtered" and not result.shown
    assert result.diagnostics.otherwise_eligible_but_busy == 1


def test_order_tie_counts_include_candidates_outside_top_three():
    candidates = Catalog((profile("P-2", price_from_kzt=200), profile("P-3"),
                          profile("P-4", price_from_kzt=200), profile("P-10")), "test")
    body = recommend(query(), candidates)
    assert [p.id for p in body.cards] == ["P-10", "P-3", "P-2"]
    assert body.counts.eligible_count == 4
    assert body.summary == "Показаны 3 из 4 подходящих."
    assert body.diagnostics.exclusions == []
    assert all(p.rank.tied_on_policy for p in body.cards)
    assert body.cards[0].comparison_note is not None
    assert all(not p.rank.format_evidence for p in body.cards)


@pytest.mark.parametrize("changes,_,__,___,____", CASES)
def test_full_response_stable_when_catalog_is_permuted(catalog, changes, _, __, ___, ____):
    shuffled = list(catalog.profiles)
    Random(42).shuffle(shuffled)
    assert recommend(query(**changes), catalog) == recommend(query(**changes), replace(catalog, profiles=tuple(shuffled)))


def test_flags_do_not_change_eligibility_or_order(catalog):
    flipped = tuple(p.model_copy(update={"synthetic": not p.synthetic, "price_imputed": not p.price_imputed,
                                        "city_imputed": not p.city_imputed}) for p in catalog.profiles)
    before = evaluate(query(), catalog)
    after = evaluate(query(), replace(catalog, profiles=flipped))
    assert [p.id for p in before.eligible] == [p.id for p in after.eligible]
    assert before.diagnostics == after.diagnostics


def test_summary_distinguishes_small_catalog_and_rejections():
    small = Catalog((profile(),), "test")
    assert "всего профилей: 1" in summarize(evaluate(query(), small))
    assert "Не прошли условия" not in summarize(evaluate(query(), small))
    mixed = Catalog((profile(), profile("BUSY", busy_dates=(date(2026, 11, 13),))), "test")
    summary = summarize(evaluate(query(), mixed))
    assert "всего профилей: 2" in summary and "Не прошли условия: 1 из 2" in summary


def test_date_change_excludes_old_card_for_busy_reason(client):
    response = client.post("/api/recommendations", json=query(date="2026-11-14").model_dump(mode="json")).json()
    diagnostics = response["diagnostics"]
    assert diagnostics["reason_counts"]["busy"] == 5
    assert next(e for e in diagnostics["exclusions"] if e["id"] == "HK-58236")["reasons"] == ["busy"]


def test_demo_queries_are_valid_requests(client):
    demos = client.get("/api/meta").json()["demo_queries"]
    assert len(demos) == 8
    assert len({d["id"] for d in demos}) == 8
    for demo in demos:
        assert set(demo) == {"id", "label", "query"}
        assert client.post("/api/recommendations", json=demo["query"]).status_code == 200


def test_missing_data_search_returns_503(tmp_path):
    settings = Settings(tmp_path / "missing.csv", tmp_path / "facts.json", tmp_path / "frontend")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/recommendations", json=query().model_dump(mode="json"))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "data_unavailable"


def test_normalized_query_and_malformed_json(client):
    request = query().model_dump(mode="json", exclude={"duration_hours", "language"})
    body = client.post("/api/recommendations", json=request).json()
    assert body["query"]["duration_hours"] is None and body["query"]["language"] is None
    response = client.post("/api/recommendations", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
