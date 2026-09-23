import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import ai_answers, llm, text_input
from backend.app.catalog import load_catalog
from backend.app.core.config import PROJECT_ROOT, ModelConfig, Settings
from backend.app.demo_queries import demo_queries
from backend.app.main import create_app
from backend.app.recommendations import recommend
from backend.app.schemas import RecommendationQuery


@pytest.fixture
def catalog():
    return load_catalog(PROJECT_ROOT / "backend/data/catalog.csv")


@pytest.fixture
def config():
    return ModelConfig("openai", "test-secret", "gpt-6-luna")


@pytest.fixture(autouse=True)
def clean_ai_environment(monkeypatch):
    for prefix in ("", "PARSE_", "ANSWER_"):
        for provider in ("OPENAI", "ANTHROPIC"):
            for suffix in ("API_KEY", "MODEL"):
                monkeypatch.delenv(f"{prefix}{provider}_{suffix}", raising=False)
    for stage in ("PARSE", "ANSWER"):
        monkeypatch.delenv(f"{stage}_ENABLED", raising=False)


def test_provider_priority_and_independent_stages(monkeypatch):
    assert Settings.from_env().parse_model is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    assert Settings.from_env().parse_model.provider == "anthropic"
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    assert Settings.from_env().answer_model.provider == "openai"
    monkeypatch.setenv("PARSE_OPENAI_API_KEY", "parse-only-secret")
    monkeypatch.setenv("PARSE_OPENAI_MODEL", "custom-model")
    settings = Settings.from_env()
    assert settings.parse_model.api_key == "parse-only-secret"
    assert settings.parse_model.model == "custom-model"
    assert settings.answer_model.api_key == "openai-secret"
    assert "secret" not in repr(settings)
    monkeypatch.setenv("ANSWER_OPENAI_API_KEY", "   ")
    monkeypatch.setenv("ANSWER_OPENAI_MODEL", "   ")
    assert Settings.from_env().answer_model.api_key == "openai-secret"
    assert Settings.from_env().answer_model.model == "gpt-6-luna"
    monkeypatch.setenv("PARSE_ENABLED", "false")
    assert Settings.from_env().parse_model is None
    assert Settings.from_env().answer_model is not None
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("PARSE_ENABLED", "true")
    monkeypatch.setenv("ANSWER_ANTHROPIC_API_KEY", "answer-only-secret")
    assert Settings.from_env().parse_model.provider == "openai"
    assert Settings.from_env().answer_model.provider == "anthropic"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_http_protocol(monkeypatch, provider):
    seen = []
    def handle(request):
        seen.append(request)
        output = {"ok": True}
        if provider == "openai":
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}]})
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(output)}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    model = "gpt-6-luna" if provider == "openai" else "claude-sonnet-5"
    result = asyncio.run(llm.generate_json(ModelConfig(provider, "test-secret", model), "rules", {"text": "hello"}, {"type": "object", "properties": {"ok": {"type": "boolean"}}}, "check"))
    assert result == {"ok": True}
    body = json.loads(seen[0].content)
    assert "api_key" not in body and body["model"] == model
    if provider == "openai":
        assert str(seen[0].url) == "https://api.openai.com/v1/responses"
        assert seen[0].headers["authorization"] == "Bearer test-secret"
        assert body["store"] is False and body["text"]["format"]["strict"]
    else:
        assert str(seen[0].url) == "https://api.anthropic.com/v1/messages"
        assert seen[0].headers["x-api-key"] == "test-secret"
        assert seen[0].headers["anthropic-version"] == "2023-06-01"
        assert body["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize("failure", ["http", "timeout", "refusal", "truncated", "json"])
def test_provider_failures_are_safe(monkeypatch, config, failure):
    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test-secret", request=request)
        if failure == "http":
            return httpx.Response(401, json={"error": "test-secret"})
        if failure == "truncated":
            return httpx.Response(200, json={"status": "incomplete"})
        content = [{"type": "refusal"}] if failure == "refusal" else [{"type": "output_text", "text": "not JSON"}]
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": content}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(llm.ProviderError) as exc:
        asyncio.run(llm.generate_json(config, "rules", {}, {}, "test"))
    assert "test-secret" not in str(exc.value)


def extraction(query):
    return {"query": query, "source_spans": {key: str(value) if value is not None else None for key, value in query.items()}, "unresolved_fields": [], "warnings": []}


def test_text_complete_and_partial_queries(monkeypatch, catalog, config):
    query = demo_queries(catalog)[0].query.model_dump(mode="json")
    raw = extraction(query)
    async def generate(*args): return raw
    monkeypatch.setattr(text_input, "generate_json", generate)
    result = asyncio.run(text_input.parse_text(" ".join(str(v) for v in query.values()), catalog, config))
    assert result.ready and result.query.model_dump() == query
    raw["query"]["budget_kzt"] = None
    raw["source_spans"]["budget_kzt"] = None
    result = asyncio.run(text_input.parse_text(" ".join(str(v) for v in query.values()), catalog, config))
    assert not result.ready and result.missing_fields == ["budget_kzt"]
    assert result.query.budget_kzt is None


def test_text_unverified_unknown_out_of_window_and_ambiguous(monkeypatch, catalog, config):
    query = demo_queries(catalog)[0].query.model_dump(mode="json")
    query.update(city="Unknown city", date="2027-01-01")
    raw = extraction(query)
    raw["source_spans"]["budget_kzt"] = "invented source"
    raw["unresolved_fields"] = ["category"]
    async def generate(*args): return raw
    monkeypatch.setattr(text_input, "generate_json", generate)
    result = asyncio.run(text_input.parse_text(" ".join(str(v) for v in query.values()), catalog, config))
    assert not result.ready
    assert set(result.missing_fields) == {"city", "date", "budget_kzt", "category"}
    assert result.warnings


def test_live_answer_preserves_selection_and_uses_only_exact_sources(monkeypatch, catalog, config):
    query = next(d.query for d in demo_queries(catalog) if d.id == "bands")
    baseline = recommend(query, catalog)
    quotes = ["два вокалиста", "струнный квартет"]
    async def generate(*args):
        return {"cards": [{"id": c.id, "quote": quote, "focus": "price"} for c, quote in zip(baseline.cards, quotes)]}
    monkeypatch.setattr(ai_answers, "generate_json", generate)
    enhanced = asyncio.run(ai_answers.enhance_answer(baseline, catalog, config))
    assert enhanced.explanation_mode == "live_quotes" and enhanced.answer_generation.status == "generated"
    for field in ("query", "counts", "diagnostics", "suggestions", "versions", "summary"):
        assert getattr(enhanced, field) == getattr(baseline, field)
    assert [c.id for c in enhanced.cards] == [c.id for c in baseline.cards]
    for card, quote in zip(enhanced.cards, quotes):
        assert quote in card.explanation
        assert card.evidence[-1].quote == quote
        assert not card.rank.format_evidence


@pytest.mark.parametrize("failure", ["invented_quote", "changed_id", "missing_card", "provider"])
def test_answer_falls_back_atomically(monkeypatch, catalog, config, failure):
    baseline = recommend(demo_queries(catalog)[0].query, catalog)
    async def generate(*args):
        if failure == "provider": raise llm.ProviderError("unavailable")
        plans = [{"id": c.id, "quote": None, "focus": "price"} for c in baseline.cards]
        if failure == "invented_quote": plans[0]["quote"] = "Guaranteed booking and discount 99999"
        if failure == "changed_id": plans[0]["id"] = "FAKE"
        if failure == "missing_card": plans.pop()
        return {"cards": plans}
    monkeypatch.setattr(ai_answers, "generate_json", generate)
    result = asyncio.run(ai_answers.enhance_answer(baseline, catalog, config))
    assert result.cards == baseline.cards
    assert result.answer_generation.status == "fallback"
    assert result.warnings[-1].startswith("AI-объяснения сейчас недоступны")


def test_disabled_and_empty_results_do_not_call_model(monkeypatch, catalog, config):
    async def forbidden(*args): raise AssertionError("Unexpected model call")
    monkeypatch.setattr(ai_answers, "generate_json", forbidden)
    baseline = recommend(demo_queries(catalog)[0].query, catalog)
    assert asyncio.run(ai_answers.enhance_answer(baseline, catalog, None)) == baseline
    empty = recommend(next(d.query for d in demo_queries(catalog) if d.id == "decorator"), catalog)
    assert asyncio.run(ai_answers.enhance_answer(empty, catalog, config)).answer_generation.status == "not_needed"


def test_actual_same_price_hosts_have_distinct_explanations(catalog):
    query = RecommendationQuery(city="Алматы", category="Ведущий", event_format="свадьба", date="2026-10-18", budget_kzt=1000000, language="казахский")
    cards = {c.id: c for c in recommend(query, catalog).cards}
    assert cards["HK-27222"].explanation != cards["HK-77838"].explanation
    assert "10 ч" in cards["HK-27222"].explanation and "8 ч" in cards["HK-77838"].explanation


def test_routes_capabilities_disabled_parsing_and_static_allowlist(tmp_path, config):
    settings = Settings(PROJECT_ROOT / "backend/data/catalog.csv", tmp_path / "facts.json", PROJECT_ROOT / "frontend")
    with TestClient(create_app(settings)) as client:
        assert not client.get("/api/meta").json()["ai"]["text_input"]["enabled"]
        assert client.post("/api/parse-request", json={"text": "hello"}).json()["error"]["code"] == "text_input_disabled"
        for text in ("", " ", "x" * 2001):
            assert client.post("/api/parse-request", json={"text": text}).status_code == 422
        for path in ("/", "/styles.css", "/app.js", "/lib/contracts.mjs"):
            assert client.get(path).status_code == 200
        for path in ("/tests/fixtures.mjs", "/.tools/node_modules/playwright/package.json", "/HANDOFF.md"):
            assert client.get(path).status_code == 404
    with TestClient(create_app(replace(settings, parse_model=config))) as client:
        meta = client.get("/api/meta")
        assert meta.json()["ai"]["text_input"]["enabled"]
        assert "test-secret" not in meta.text


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_full_api_text_to_selection_with_mock_provider_wire_protocol(monkeypatch, catalog, tmp_path, provider):
    query = next(d.query for d in demo_queries(catalog) if d.id == "bands").model_dump(mode="json")
    requests = []
    def handle(request):
        body = json.loads(request.content)
        data = json.loads(body["input"] if provider == "openai" else body["messages"][0]["content"])
        requests.append(data)
        if "text" in data:
            output = extraction(query)
        else:
            output = {"cards": [{"id": c["id"], "quote": "два вокалиста" if c["id"] == "HK-23752" else "струнный квартет", "focus": "price"} for c in data["cards"]]}
        content = json.dumps(output)
        if provider == "openai":
            response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]}
        else:
            response = {"stop_reason": "end_turn", "content": [{"type": "text", "text": content}]}
        return httpx.Response(200, json=response)
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    config = ModelConfig(provider, "test-secret", "gpt-6-luna" if provider == "openai" else "claude-sonnet-5")
    settings = Settings(PROJECT_ROOT / "backend/data/catalog.csv", tmp_path / "facts.json", PROJECT_ROOT / "frontend", config, config)
    with TestClient(create_app(settings)) as client:
        parsed = client.post("/api/parse-request", json={"text": " ".join(str(v) for v in query.values())})
        assert parsed.status_code == 200 and parsed.json()["ready"]
        result = client.post("/api/recommendations", json=parsed.json()["query"])
        assert result.status_code == 200
        assert result.json()["explanation_mode"] == "live_quotes"
        assert result.json()["answer_generation"]["provider"] == provider
        assert [c["id"] for c in result.json()["cards"]] == ["HK-23752", "HK-83709"]
        assert "test-secret" not in result.text
    assert len(requests) == 2
