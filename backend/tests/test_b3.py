import hashlib
import json
from dataclasses import replace
from datetime import date
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from backend.ai import prepare
from backend.ai.prepare import ExtractedProfile, ReviewReport, accept_review
from backend.app.catalog import Catalog, Contractor
from backend.app.core.config import Settings
from backend.app.facts import FactsError, Registry, description_hash, load_facts, validate_registry
from backend.app.main import create_app
from backend.app.recommendations import recommend
from backend.app.schemas import RecommendationQuery
from backend.app.selection import evaluate


@pytest.fixture
def catalog():
    rows = [
        ("P1", 100, "Играем на свадьбах. Два вокалиста."),
        ("P2", 200, "Проводим корпоративы. Струнный квартет."),
        ("P3", 200, "Проводим корпоративы. Духовая группа."),
    ]
    return Catalog(tuple(Contractor(
        id=id_, anon_name=id_, city="Алматы", categories=("Лайв-бэнд",),
        event_formats=("корпоратив", "свадьба"), languages=("русский",),
        busy_dates=(), price_from_kzt=price, max_hours=None, description=description,
        synthetic=False, price_imputed=False, city_imputed=False,
    ) for id_, price, description in rows), "a" * 64)


@pytest.fixture
def registry_dict(catalog):
    return {
        "schema_version": 1, "dataset_sha256": catalog.sha256,
        "generator": {"provider": "test", "model": "test-model", "method": "test_fixture"},
        "profiles": [
            {"contractor_id": p.id, "description_sha256": description_hash(p.description),
             "review_status": "approved", "facts": [
                 {"id": p.id + "-fact", "quote": p.description.split(". ")[0] + ".",
                  "event_formats": ["свадьба"] if p.id == "P1" else ["корпоратив"]}
             ]} for p in catalog.profiles
        ],
    }


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def query(**changes):
    return RecommendationQuery.model_validate(dict(
        city="Алматы", category="Лайв-бэнд", date="2026-11-13",
        event_format="корпоратив", budget_kzt=1000,
    ) | changes)


def test_valid_registry_ranks_by_requested_format(catalog, registry_dict, tmp_path):
    path = tmp_path / "facts.json"
    save(path, registry_dict)
    snapshot = load_facts(path, catalog)
    assert snapshot.mode == "approved_facts" and snapshot.warning is None
    assert snapshot.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    enriched = replace(catalog, facts=snapshot)
    result = recommend(query(), enriched)
    assert [c.id for c in result.cards] == ["P2", "P3", "P1"]
    assert [c.rank.format_evidence for c in result.cards] == [True, True, False]
    assert [c.rank.tied_on_policy for c in result.cards] == [True, True, False]
    assert result.versions.facts == snapshot.sha256
    assert result.explanation_mode == "approved_facts"
    for card in result.cards:
        evidence = next(e for e in card.evidence if e.kind == "description")
        assert evidence.fact_id and evidence.quote in card.explanation
        assert evidence.source_field == "description"
    wedding = recommend(query(event_format="свадьба"), enriched)
    assert wedding.cards[0].id == "P1" and wedding.cards[0].rank.format_evidence
    assert not wedding.cards[1].rank.format_evidence


@pytest.mark.parametrize("damage", [
    "dataset", "description", "missing", "duplicate_profile", "unknown_id", "pending",
    "quote", "duplicate_fact", "unknown_format", "duplicate_format", "empty_approved", "nonempty_unusable",
    "schema_version", "too_many_facts",
])
def test_invalid_registry_falls_back_as_a_whole(catalog, registry_dict, tmp_path, damage):
    entries = registry_dict["profiles"]
    if damage == "dataset":
        registry_dict["dataset_sha256"] = "b" * 64
    elif damage == "description":
        entries[0]["description_sha256"] = "b" * 64
    elif damage == "missing":
        entries.pop()
    elif damage == "duplicate_profile":
        entries.append(entries[0])
    elif damage == "unknown_id":
        entries[0]["contractor_id"] = "UNKNOWN"
    elif damage == "pending":
        entries[0]["review_status"] = "pending"
    elif damage == "quote":
        entries[0]["facts"][0]["quote"] = "Несуществующая цитата"
    elif damage == "duplicate_fact":
        entries[1]["facts"][0]["id"] = entries[0]["facts"][0]["id"]
    elif damage == "unknown_format":
        entries[0]["facts"][0]["event_formats"] = ["юбилей"]
    elif damage == "duplicate_format":
        entries[0]["facts"][0]["event_formats"] = ["свадьба", "свадьба"]
    elif damage == "empty_approved":
        entries[0]["facts"] = []
    elif damage == "nonempty_unusable":
        entries[0]["review_status"] = "no_usable_facts"
    elif damage == "schema_version":
        registry_dict["schema_version"] = 2
    elif damage == "too_many_facts":
        entries[0]["facts"] *= 3
    path = tmp_path / "facts.json"
    save(path, registry_dict)
    snapshot = load_facts(path, catalog)
    assert snapshot.mode == "structured_only"
    assert snapshot.registry is None and snapshot.sha256 is None and snapshot.warning
    result = recommend(query(), replace(catalog, facts=snapshot))
    assert [c.id for c in result.cards] == ["P1", "P2", "P3"]
    assert all(not c.rank.format_evidence for c in result.cards)
    assert all(e.kind != "description" for c in result.cards for e in c.evidence)
    assert snapshot.warning in result.warnings


def test_missing_malformed_and_empty_facts(catalog, tmp_path, registry_dict):
    path = tmp_path / "facts.json"
    assert load_facts(path, catalog).mode == "structured_only"
    path.write_bytes(b"not json")
    assert load_facts(path, catalog).mode == "structured_only"
    for entry in registry_dict["profiles"]:
        entry["facts"] = []
        entry["review_status"] = "no_usable_facts"
    save(path, registry_dict)
    assert load_facts(path, catalog).mode == "approved_facts"


def test_minimal_generator_metadata_from_spec(catalog, tmp_path, registry_dict):
    registry_dict["generator"].pop("method")
    path = tmp_path / "facts.json"
    save(path, registry_dict)
    assert load_facts(path, catalog).mode == "approved_facts"


def pending_and_report(registry_dict):
    for p in registry_dict["profiles"]:
        p["review_status"] = "pending"
    raw = json.dumps(registry_dict).encode()
    report = {
        "pending_sha256": hashlib.sha256(raw).hexdigest(), "reviewer": "test executor",
        "method": "executor_review", "profiles": [
            {"contractor_id": p["contractor_id"], "accepted_fact_ids": [f["id"] for f in p["facts"]],
             "note": "Test fixture decision"} for p in registry_dict["profiles"]
        ],
    }
    return raw, report


def test_separate_review_can_reject_facts(catalog, registry_dict):
    raw, report = pending_and_report(registry_dict)
    report["profiles"][0]["accepted_fact_ids"] = []
    accepted = accept_review(raw, ReviewReport.model_validate(report), catalog)
    assert accepted.profiles[0].review_status == "no_usable_facts"
    assert accepted.profiles[1].review_status == "approved"
    assert accepted.review.method == "executor_review"
    validate_registry(accepted, catalog)


@pytest.mark.parametrize("damage", ["hash", "missing_empty", "duplicate", "invented_fact", "duplicate_fact", "method"])
def test_review_requires_complete_bound_decisions(catalog, registry_dict, damage):
    registry_dict["profiles"][0]["facts"] = []
    raw, report = pending_and_report(registry_dict)
    if damage == "hash":
        report["pending_sha256"] = "b" * 64
    elif damage == "missing_empty":
        report["profiles"].pop(0)
    elif damage == "duplicate":
        report["profiles"].append(report["profiles"][0])
    elif damage == "invented_fact":
        report["profiles"][0]["accepted_fact_ids"] = ["invented"]
    elif damage == "duplicate_fact":
        report["profiles"][1]["accepted_fact_ids"] *= 2
    elif damage == "method":
        report["method"] = "independently_verified"
    with pytest.raises(ValueError):
        accept_review(raw, ReviewReport.model_validate(report), catalog)


def test_extraction_stays_pending_and_records_actual_model(catalog, monkeypatch):
    calls = []

    def provider(profile, key, model, prompt, schema):
        calls.append(profile.id)
        assert key == "test-key" and model == "requested-model"
        assert "description" in prompt and schema["additionalProperties"] is False
        return ExtractedProfile(facts=()), "actual-response-model"

    monkeypatch.setattr(prepare, "request_profile", provider)
    registry = prepare.extract(catalog, "test-key", "requested-model")
    assert calls == [p.id for p in catalog.profiles]
    assert registry.generator.model == "actual-response-model"
    assert registry.generator.requested_model == "requested-model"
    assert registry.generator.prompt_sha256
    assert all(p.review_status == "pending" for p in registry.profiles)
    with pytest.raises(FactsError):
        validate_registry(registry, catalog)


def test_provider_request_and_response_contract(catalog, monkeypatch):
    def transport(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_header("Authorization") == "Bearer test-key"
        assert payload["store"] is False and payload["text"]["format"]["strict"] is True
        assert json.loads(payload["input"][1]["content"])["description"] == catalog.profiles[0].description
        return BytesIO(json.dumps({"status": "completed", "model": "actual-model", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"facts": []}'}]}
        ]}).encode())

    monkeypatch.setattr(prepare, "urlopen", transport)
    value, model = prepare.request_profile(catalog.profiles[0], "test-key", "model", "prompt", {})
    assert not value.facts and model == "actual-model"


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "model": "test"},
    {"status": "completed", "model": "test", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
])
def test_incomplete_and_refusal_fail(catalog, monkeypatch, response):
    monkeypatch.setattr(prepare, "urlopen", lambda *args, **kwargs: BytesIO(json.dumps(response).encode()))
    with pytest.raises(RuntimeError):
        prepare.request_profile(catalog.profiles[0], "secret", "model", "prompt", {})


@pytest.mark.parametrize("exception", [URLError("secret"), HTTPError("url", 401, "secret", {}, None)])
def test_provider_errors_do_not_expose_credentials(catalog, monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(prepare, "urlopen", fail)
    with pytest.raises(RuntimeError) as captured:
        prepare.request_profile(catalog.profiles[0], "secret", "model", "prompt", {})
    assert "secret" not in str(captured.value)


def test_no_key_does_not_create_fake_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    target = tmp_path / "pending.json"
    monkeypatch.setattr("sys.argv", ["prepare", "extract", "--pending", str(target)])
    assert prepare.main() == 1
    assert not target.exists()


def test_registry_never_relaxes_eligibility(catalog, registry_dict, tmp_path):
    path = tmp_path / "facts.json"
    save(path, registry_dict)
    snapshot = load_facts(path, catalog)
    profiles = tuple(p.model_copy(update={"busy_dates": (date(2026, 11, 13),)}) for p in catalog.profiles)
    assert not evaluate(query(), replace(catalog, profiles=profiles, facts=snapshot)).eligible
    assert [p.id for p in evaluate(query(budget_kzt=100), replace(catalog, facts=snapshot)).eligible] == ["P1"]


def test_api_startup_offline_and_corrupt_registry_fallback(catalog, registry_dict, tmp_path, monkeypatch):
    import backend.app.main as main_module

    monkeypatch.setattr(main_module, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(prepare, "urlopen", lambda *args, **kwargs: pytest.fail("Search must not call the provider"))
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "facts.json"
    settings = Settings(tmp_path / "catalog.csv", path, tmp_path / "frontend")
    save(path, registry_dict)
    for expected_mode in ("approved_facts", "structured_only"):
        with TestClient(create_app(settings)) as client:
            health = client.get("/api/health")
            assert health.status_code == 200
            meta = client.get("/api/meta").json()
            response = client.post("/api/recommendations", json=query().model_dump(mode="json")).json()
            assert health.json()["explanation_mode"] == meta["explanation_mode"] == response["explanation_mode"] == expected_mode
            assert health.json()["versions"] == meta["versions"] == response["versions"]
            if expected_mode == "structured_only":
                assert response["versions"]["facts"] is None and response["warnings"]
            path.write_text("broken", encoding="utf-8")
            assert client.get("/api/meta").json() == meta
