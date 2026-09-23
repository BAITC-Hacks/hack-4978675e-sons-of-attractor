import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    dataset_path: Path
    facts_path: Path
    frontend_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            resolve_path(os.getenv("DATASET_PATH", "backend/data/catalog.csv")),
            resolve_path(os.getenv("FACTS_PATH", "backend/data/facts.json")),
            PROJECT_ROOT / "frontend",
        )
