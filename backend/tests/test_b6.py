import csv
from dataclasses import replace
from random import Random

from backend.app.catalog import load_catalog
from backend.app.core.config import PROJECT_ROOT
from backend.app.demo_queries import demo_queries
from backend.app.facts import FactsSnapshot, Generator, ProfileFacts, Registry, description_hash, validate_registry
from backend.app.recommendations import recommend


def test_reordered_csv_with_compatible_registry_preserves_business_response(tmp_path):
    source = PROJECT_ROOT / "backend/data/catalog.csv"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows, fields = list(reader), reader.fieldnames
    Random(7).shuffle(rows)
    reordered = tmp_path / "catalog.csv"
    with reordered.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    original, changed = load_catalog(source), load_catalog(reordered)
    assert original.sha256 != changed.sha256
    registry = Registry(
        schema_version=1, dataset_sha256=original.sha256,
        generator=Generator(provider="test", model="test-fixture", method="test_fixture"),
        profiles=tuple(ProfileFacts(contractor_id=p.id, description_sha256=description_hash(p.description),
                                     review_status="no_usable_facts", facts=()) for p in original.profiles),
    )
    compatible = registry.model_copy(update={"dataset_sha256": changed.sha256})
    validate_registry(registry, original)
    validate_registry(compatible, changed)
    original = replace(original, facts=FactsSnapshot(registry, "a" * 64, None))
    changed = replace(changed, facts=FactsSnapshot(compatible, "b" * 64, None))
    for demo in demo_queries(original):
        before = recommend(demo.query, original).model_dump(exclude={"versions"})
        after = recommend(demo.query, changed).model_dump(exclude={"versions"})
        assert before == after

