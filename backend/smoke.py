import argparse
import json
from datetime import datetime, timezone
from statistics import mean
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.app.schemas import RecommendationResponse

EXPECTED = {
    "halls": ("found", 6, ["HK-64395", "HK-58236", "HK-90011"]),
    "halls_next_day": ("found", 2, ["HK-64395", "HK-90011"]),
    "florist": ("found", 1, ["HK-39372"]),
    "decorator": ("no_category_in_city", 0, []),
    "busy_halls": ("all_filtered", 0, []),
    "host_budget": ("all_filtered", 0, []),
    "busy_hosts": ("all_filtered", 0, []),
    "bands": ("found", 2, ["HK-23752", "HK-83709"]),
}


def run(base_url: str, require_facts: bool = False) -> dict:
    times = []

    def request(path, body=None):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        start = perf_counter()
        with urlopen(Request(base_url.rstrip("/") + path, data=payload,
                             headers={"Content-Type": "application/json"}), timeout=10) as response:
            value = json.load(response)
        times.append((perf_counter() - start) * 1000)
        return value

    started = perf_counter()
    health, meta = request("/api/health"), request("/api/meta")
    assert health["status"] == "ok" and health["catalog_count"] == 66
    assert meta["versions"] == health["versions"]
    assert meta["versions"]["dataset"] == "6a724b6b7dfb5973343e68ba18dadb60fc807d87e3d78f03ee86fb26cb089f7d"
    if require_facts:
        assert meta["explanation_mode"] == "approved_facts", "Full acceptance requires the approved AI registry"
    assert {d["id"] for d in meta["demo_queries"]} == set(EXPECTED)
    scenarios = []
    for demo in meta["demo_queries"]:
        result = request("/api/recommendations", demo["query"])
        parsed = RecommendationResponse.model_validate(result)
        status, count, ids = EXPECTED[demo["id"]]
        assert parsed.status == status and parsed.counts.eligible_count == count, demo["id"]
        assert [c.id for c in parsed.cards] == ids, demo["id"]
        assert result["versions"] == meta["versions"]
        assert request("/api/recommendations", demo["query"]) == result
        for suggestion in result["suggestions"]:
            applied = request("/api/recommendations", suggestion["query"])
            assert applied["versions"] == result["versions"]
            assert applied["counts"]["eligible_count"] == suggestion["eligible_count"]
            assert applied["counts"]["shown_count"] == suggestion["shown_count"]
        if demo["id"] == "decorator":
            assert result["city_alternatives"] == [{"city": "Алматы", "catalog_count": 3}]
        if demo["id"] == "busy_halls":
            assert [(s["value"], s["eligible_count"]) for s in result["suggestions"]] == [("2026-12-18", 1), ("2026-12-20", 1)]
        if demo["id"] == "host_budget":
            assert any(s["field"] == "budget_kzt" and s["value"] == 650000 for s in result["suggestions"])
        if demo["id"] == "busy_hosts":
            assert [(s["value"], s["eligible_count"]) for s in result["suggestions"]] == [("2026-12-11", 1), ("2026-12-13", 2)]
        if demo["id"] == "bands" and require_facts:
            quotes = [next(e.quote for e in card.evidence if e.kind == "description") for card in parsed.cards]
            assert "два вокалиста" in quotes[0] and "струнный квартет" in quotes[1]
        scenarios.append({"id": demo["id"], "status": status, "eligible_count": count,
                          "shown_ids": ids, "verified_suggestions": len(result["suggestions"])})
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "explanation_mode": meta["explanation_mode"], "versions": meta["versions"],
        "require_facts": require_facts, "http_requests": len(times), "scenarios": scenarios,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "request_ms": {"min": round(min(times), 2), "mean": round(mean(times), 2), "max": round(max(times), 2)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--require-facts", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.base_url, args.require_facts), ensure_ascii=False, indent=2))
        return 0
    except (AssertionError, ValueError, URLError, HTTPError, TimeoutError, KeyError, StopIteration) as exc:
        print(f"Smoke check failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
