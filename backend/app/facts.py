import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import Catalog

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1)]


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(FactModel):
    id: Text
    quote: Text
    event_formats: tuple[Text, ...]


class ProfileFacts(FactModel):
    contractor_id: Text
    description_sha256: Hash
    review_status: Literal["pending", "approved", "no_usable_facts"]
    facts: tuple[Fact, ...] = Field(max_length=2)

    @model_validator(mode="after")
    def check_status(self):
        if self.review_status == "approved" and not self.facts:
            raise ValueError("Approved profile must contain facts")
        if self.review_status == "no_usable_facts" and self.facts:
            raise ValueError("No usable facts requires an empty list")
        return self


class Generator(FactModel):
    provider: Text
    model: Text
    method: Text
    requested_model: str | None = None
    prompt_sha256: Hash | None = None


class ReviewMetadata(FactModel):
    reviewer: Text
    method: Literal["executor_review", "human_review"]
    pending_sha256: Hash


class Registry(FactModel):
    schema_version: Literal[1]
    dataset_sha256: Hash
    generator: Generator
    profiles: tuple[ProfileFacts, ...]
    review: ReviewMetadata | None = None


class FactsError(ValueError):
    pass


@dataclass(frozen=True)
class FactsSnapshot:
    registry: Registry | None
    sha256: str | None
    warning: str | None

    @property
    def mode(self) -> Literal["approved_facts", "structured_only"]:
        return "approved_facts" if self.registry is not None else "structured_only"

    def for_profile(self, contractor_id: str) -> tuple[Fact, ...]:
        if self.registry is None:
            return ()
        return next(p.facts for p in self.registry.profiles if p.contractor_id == contractor_id)

    def has_format(self, contractor_id: str, event_format: str) -> bool:
        return any(event_format in f.event_formats for f in self.for_profile(contractor_id))


def description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def validate_registry(registry: Registry, catalog: Catalog, *, allow_pending: bool = False) -> None:
    if registry.dataset_sha256 != catalog.sha256:
        raise FactsError("Dataset hash mismatch")
    profiles = {p.id: p for p in catalog.profiles}
    ids = [p.contractor_id for p in registry.profiles]
    if len(ids) != len(set(ids)) or set(ids) != set(profiles):
        raise FactsError("Registry must cover every catalog ID exactly once")
    fact_ids = set()
    for entry in registry.profiles:
        profile = profiles[entry.contractor_id]
        if entry.description_sha256 != description_hash(profile.description):
            raise FactsError(f"Description hash mismatch: {profile.id}")
        if not allow_pending and entry.review_status == "pending":
            raise FactsError(f"Unreviewed profile: {profile.id}")
        quotes = set()
        for fact in entry.facts:
            if fact.id in fact_ids or fact.quote in quotes:
                raise FactsError(f"Duplicate fact: {profile.id}")
            fact_ids.add(fact.id)
            quotes.add(fact.quote)
            if not fact.quote.strip() or fact.quote not in profile.description:
                raise FactsError(f"Quote is not an exact description substring: {profile.id}")
            if len(fact.event_formats) != len(set(fact.event_formats)):
                raise FactsError(f"Duplicate format: {profile.id}")
            if not set(fact.event_formats).issubset(profile.event_formats):
                raise FactsError(f"Format outside structured catalog fields: {profile.id}")


def load_facts(path: Path, catalog: Catalog) -> FactsSnapshot:
    try:
        raw = path.read_bytes()
        registry = Registry.model_validate_json(raw)
        validate_registry(registry, catalog)
        return FactsSnapshot(registry, hashlib.sha256(raw).hexdigest(), None)
    except FileNotFoundError:
        return FactsSnapshot(None, None, "AI-реестр отсутствует; используются только структурированные поля.")
    except (OSError, ValueError):
        return FactsSnapshot(None, None, "AI-реестр повреждён, несовместим или не полностью проверен; он целиком отключён.")
