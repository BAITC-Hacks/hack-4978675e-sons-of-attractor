import csv
import hashlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.catalog import CatalogError, Contractor, load_catalog
from backend.app.core.config import PROJECT_ROOT, Settings
from backend.app.main import create_app
from backend.app.schemas import RecommendationQuery


def record(**changes):
    return {
        "id": "TEST-1", "anon_name": "Тестовый профиль", "city": "Алматы",
        "categories": " Ведущий | Ведущий церемонии | Ведущий ",
        "event_formats": "корпоратив|свадьба", "languages": "русский| казахский",
        "busy_dates": "2026-11-13|2026-11-13", "price_from_kzt": "600000",
        "max_hours": "", "description": ' Состав, музыка: "тест"\nВторая строка. ',
        "synthetic": "False", "price_imputed": "True", "city_imputed": "False",
        **changes,
    }


def write_catalog(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(Contractor.model_fields))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "catalog.csv"
    write_catalog(path, [record(), record(id="TEST-2", city="Астана", max_hours="6.5")])
    return Settings(path, tmp_path / "facts.json", tmp_path / "frontend")


def query(**changes):
    return {
        "city": "Алматы", "date": "2026-11-13", "event_format": "корпоратив",
        "category": "Ведущий", "budget_kzt": 600000, **changes,
    }


def test_csv_roundtrip(settings):
    catalog = load_catalog(settings.dataset_path)
    profile = catalog.profiles[0]
    assert len(catalog.profiles) == 2
    assert profile.categories == ("Ведущий", "Ведущий церемонии")
    assert profile.synthetic is False and profile.city_imputed is False
    assert profile.price_imputed is True
    assert profile.max_hours is None
    assert catalog.profiles[1].max_hours == 6.5
    assert len(profile.busy_dates) == 1
    assert profile.description == record()["description"]
    assert catalog.sha256 == hashlib.sha256(settings.dataset_path.read_bytes()).hexdigest()


def test_supplied_dataset():
    catalog = load_catalog(PROJECT_ROOT / "backend/data/catalog.csv")
    assert catalog.sha256 == "6a724b6b7dfb5973343e68ba18dadb60fc807d87e3d78f03ee86fb26cb089f7d"
    assert len(catalog.profiles) == 66
    assert sum(p.synthetic for p in catalog.profiles) == 13
    assert sum(p.price_imputed for p in catalog.profiles) == 18
    assert sum(p.city_imputed for p in catalog.profiles) == 8
    assert sum(p.max_hours is None for p in catalog.profiles) == 9
    assert "Ведущий церемонии" in catalog.dictionaries()["categories"]


@pytest.mark.parametrize("change", [
    {"id": ""}, {"city": " "}, {"categories": " | "}, {"synthetic": "0"},
    {"price_from_kzt": "0"}, {"price_from_kzt": "12.5"}, {"price_from_kzt": "-1"},
    {"max_hours": "0"}, {"max_hours": "nan"}, {"max_hours": "inf"},
    {"busy_dates": "2026-02-30"}, {"busy_dates": "2027-01-01"},
])
def test_invalid_row_rejects_entire_catalog(settings, change):
    write_catalog(settings.dataset_path, [record(id="VALID"), record(**change)])
    with pytest.raises(CatalogError):
        load_catalog(settings.dataset_path)
    with TestClient(create_app(settings)) as client:
        for route in ("/api/health", "/api/meta"):
            response = client.get(route)
            assert response.status_code == 503
            assert response.json()["error"]["fields"] == {}
            assert str(settings.dataset_path) not in response.text


def test_duplicate_and_empty_catalog(settings):
    for rows in ([record(), record()], []):
        write_catalog(settings.dataset_path, rows)
        with pytest.raises(CatalogError):
            load_catalog(settings.dataset_path)


@pytest.mark.parametrize("raw", [
    b"id,id\n1,1\n", b"not,a,catalog\n", b"\xff\xfe",
])
def test_invalid_file(settings, raw):
    settings.dataset_path.write_bytes(raw)
    with pytest.raises(CatalogError):
        load_catalog(settings.dataset_path)


def test_missing_data_returns_503(tmp_path):
    settings = Settings(tmp_path / "missing.csv", tmp_path / "facts.json", tmp_path / "frontend")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/meta").status_code == 503


def test_meta_health_and_single_startup_load(settings):
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["catalog_count"] == 2
        assert health.json()["explanation_mode"] == "structured_only"
        assert health.json()["versions"]["facts"] is None
        meta = client.get("/api/meta").json()
        assert meta["cities"] == ["Алматы", "Астана"]
        assert meta["categories"] == ["Ведущий", "Ведущий церемонии"]
        assert meta["date_min"] == "2026-09-23"
        assert meta["date_max"] == "2026-12-31"
        assert meta["versions"] == health.json()["versions"]
        # Changing the file after startup does not change the loaded snapshot.
        settings.dataset_path.write_text("broken", encoding="utf-8")
        assert client.get("/api/meta").json() == meta
        assert client.get("/openapi.json").status_code == 200


def test_order_independent_dictionaries(settings):
    before = load_catalog(settings.dataset_path)
    write_catalog(settings.dataset_path, [record(id="TEST-2", city="Астана", max_hours="6.5"), record()])
    after = load_catalog(settings.dataset_path)
    assert before.dictionaries() == after.dictionaries()
    assert before.profiles == after.profiles


@pytest.mark.parametrize("change", [
    {"budget_kzt": True}, {"budget_kzt": "600000"}, {"budget_kzt": 1.5},
    {"budget_kzt": 0}, {"budget_kzt": 9007199254740992},
    {"duration_hours": True}, {"duration_hours": "6"}, {"duration_hours": 0},
    {"date": "2027-01-01"}, {"date": "2026-11-13T00:00:00"}, {"date": 1794528000},
    {"city": "Несуществующий"}, {"category": "Музыкант"},
    {"event_format": "неизвестный"}, {"language": "неизвестный"},
    {"language": ""}, {"extra": "secret"},
])
def test_query_validation_envelope(settings, change):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/recommendations", json=query(**change))
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert next(iter(change)) in error["fields"]


def test_optional_normalization_and_finite_hours():
    normalized = RecommendationQuery.model_validate(query()).model_dump(mode="json")
    assert normalized["language"] is None and normalized["duration_hours"] is None
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            RecommendationQuery.model_validate(query(duration_hours=value))
    for date in ("2026-09-23", "2026-12-31"):
        assert RecommendationQuery.model_validate(query(date=date, duration_hours=6))


def test_static_and_api_namespace(settings):
    settings.frontend_path.mkdir()
    (settings.frontend_path / "index.html").write_text("<h1>Test frontend</h1>", encoding="utf-8")
    # Even a frontend file under /api must never shadow the reserved namespace.
    (settings.frontend_path / "api").mkdir()
    (settings.frontend_path / "api" / "fake.html").write_text("wrong", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        assert "Test frontend" in client.get("/").text
        for path in ("/api", "/api/unknown", "/api/fake.html", "/api/recommendations"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"
        assert client.get("/api/health").status_code == 200


def test_internal_error_is_neutral(settings):
    app = create_app(settings)

    @app.get("/_fail")
    def fail():
        raise RuntimeError("private key or local path")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_fail")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "private" not in response.text


def test_settings_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATASET_PATH", raising=False)
    monkeypatch.delenv("FACTS_PATH", raising=False)
    assert Settings.from_env().dataset_path == PROJECT_ROOT / "backend/data/catalog.csv"
    monkeypatch.setenv("DATASET_PATH", "custom/catalog.csv")
    assert Settings.from_env().dataset_path == PROJECT_ROOT / "custom/catalog.csv"
    monkeypatch.setenv("DATASET_PATH", str(tmp_path / "absolute.csv"))
    assert Settings.from_env().dataset_path == tmp_path / "absolute.csv"
