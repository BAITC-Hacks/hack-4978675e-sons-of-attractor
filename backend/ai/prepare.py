import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import Field

from backend.app.catalog import Catalog, load_catalog
from backend.app.core.config import Settings, resolve_path
from backend.app.facts import (
    Fact, FactModel, Generator, Hash, ProfileFacts, Registry, ReviewMetadata, Text,
    description_hash, validate_registry,
)

AI_DIR = Path(__file__).resolve().parent


class ExtractedFact(FactModel):
    quote: Text
    event_formats: tuple[Text, ...]


class ExtractedProfile(FactModel):
    facts: tuple[ExtractedFact, ...] = Field(max_length=2)


class Decision(FactModel):
    contractor_id: Text
    accepted_fact_ids: tuple[Text, ...]
    note: Text


class ReviewReport(FactModel):
    pending_sha256: Hash
    reviewer: Text
    method: str
    profiles: tuple[Decision, ...]


def request_profile(profile, key: str, model: str, prompt: str, schema: dict) -> tuple[ExtractedProfile, str]:
    payload = {
        "model": model,
        "store": False,
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "contractor_id": profile.id, "description": profile.description,
                "allowed_event_formats": profile.event_formats,
            }, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": "contractor_facts", "strict": True, "schema": schema}},
        "max_output_tokens": 4096,
    }
    request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Provider returned HTTP {exc.code} for {profile.id}") from None
    except (URLError, TimeoutError):
        raise RuntimeError(f"Provider connection failed for {profile.id}") from None
    if result.get("status") != "completed" or not result.get("model"):
        raise RuntimeError(f"Incomplete provider response for {profile.id}")
    blocks = [block for item in result.get("output", []) if item.get("type") == "message"
              for block in item.get("content", [])]
    if any(block.get("type") == "refusal" for block in blocks):
        raise RuntimeError(f"Provider refused extraction for {profile.id}")
    text = "".join(block["text"] for block in blocks if block.get("type") == "output_text")
    return ExtractedProfile.model_validate_json(text), result["model"]


def extract(catalog: Catalog, key: str, model: str) -> Registry:
    prompt = (AI_DIR / "prompt.txt").read_text(encoding="utf-8")
    schema = json.loads((AI_DIR / "output.schema.json").read_text(encoding="utf-8"))
    profiles = []
    actual_model = None
    for profile in catalog.profiles:
        extracted, response_model = request_profile(profile, key, model, prompt, schema)
        if actual_model is not None and response_model != actual_model:
            raise RuntimeError("Provider model changed during extraction; registry was not saved")
        actual_model = response_model
        facts = []
        for item in extracted.facts:
            formats = tuple(sorted(set(item.event_formats)))
            fingerprint = json.dumps([profile.id, item.quote, formats], ensure_ascii=False)
            facts.append(Fact(id=f"{profile.id}-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}",
                              quote=item.quote, event_formats=formats))
        profiles.append(ProfileFacts(contractor_id=profile.id, description_sha256=description_hash(profile.description),
                                     review_status="pending", facts=tuple(sorted(facts, key=lambda f: f.id))))
        print(f"Extracted {len(profiles)}/{len(catalog.profiles)}: {profile.id}")
    registry = Registry(schema_version=1, dataset_sha256=catalog.sha256,
                        generator=Generator(provider="openai", model=actual_model, requested_model=model,
                                            method="responses_api", prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest()),
                        profiles=tuple(profiles))
    validate_registry(registry, catalog, allow_pending=True)
    return registry


def accept_review(pending: bytes, report: ReviewReport, catalog: Catalog) -> Registry:
    registry = Registry.model_validate_json(pending)
    validate_registry(registry, catalog, allow_pending=True)
    if any(p.review_status != "pending" for p in registry.profiles):
        raise ValueError("Input must contain only pending profiles")
    if hashlib.sha256(pending).hexdigest() != report.pending_sha256:
        raise ValueError("Review refers to a different pending registry")
    decisions = {p.contractor_id: p for p in report.profiles}
    if len(decisions) != len(report.profiles) or set(decisions) != {p.contractor_id for p in registry.profiles}:
        raise ValueError("Every profile, including empty ones, requires exactly one review decision")
    profiles = []
    for profile in registry.profiles:
        accepted = decisions[profile.contractor_id].accepted_fact_ids
        if len(accepted) != len(set(accepted)) or not set(accepted).issubset(f.id for f in profile.facts):
            raise ValueError(f"Invalid accepted fact IDs: {profile.contractor_id}")
        facts = tuple(f for f in profile.facts if f.id in accepted)
        profiles.append(profile.model_copy(update={"facts": facts, "review_status": "approved" if facts else "no_usable_facts"}))
    result = registry.model_copy(update={
        "profiles": tuple(profiles),
        "review": ReviewMetadata(reviewer=report.reviewer, method=report.method, pending_sha256=report.pending_sha256),
    })
    validate_registry(result, catalog)
    return result


def write_new(path: Path, registry: Registry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(registry.model_dump_json(indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("extract", "accept", "validate"))
    parser.add_argument("--dataset", type=resolve_path, default=Settings.from_env().dataset_path)
    parser.add_argument("--pending", type=resolve_path, default=resolve_path("backend/data/facts.pending.json"))
    parser.add_argument("--facts", type=resolve_path, default=Settings.from_env().facts_path)
    parser.add_argument("--review", type=resolve_path)
    args = parser.parse_args()
    try:
        if args.command == "extract":
            key, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL")
            if not key or not model:
                raise ValueError("Set OPENAI_API_KEY and OPENAI_MODEL in the process environment")
            if args.pending.exists():
                raise ValueError("Pending output already exists; use a new --pending path")
            registry = extract(load_catalog(args.dataset), key, model)
            write_new(args.pending, registry)
            print("Pending registry saved; separate content review is required")
        elif args.command == "accept":
            if args.review is None:
                raise ValueError("--review is required")
            if args.facts.exists():
                raise ValueError("Facts output already exists; use a new --facts path")
            registry = accept_review(args.pending.read_bytes(), ReviewReport.model_validate_json(args.review.read_bytes()),
                                     load_catalog(args.dataset))
            write_new(args.facts, registry)
            print("Reviewed registry saved")
        else:
            registry = Registry.model_validate_json(args.facts.read_bytes())
            validate_registry(registry, load_catalog(args.dataset))
            print(f"Valid registry: {len(registry.profiles)} profiles")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Preparation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
