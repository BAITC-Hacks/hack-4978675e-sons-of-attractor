import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    api_key: str = field(repr=False)
    model: str


def model_config(stage: str) -> ModelConfig | None:
    """Stage credentials override shared credentials; OpenAI has first priority."""
    if os.getenv(f"{stage}_ENABLED", "true").strip().lower() in {"false", "0", "no"}:
        return None
    for provider, default in (("OPENAI", "gpt-6-luna"), ("ANTHROPIC", "claude-sonnet-5")):
        key = (os.getenv(f"{stage}_{provider}_API_KEY", "").strip()
               or os.getenv(f"{provider}_API_KEY", "").strip())
        if key:
            model = (os.getenv(f"{stage}_{provider}_MODEL", "").strip()
                     or os.getenv(f"{provider}_MODEL", "").strip() or default)
            return ModelConfig(provider.lower(), key, model)
    return None


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    dataset_path: Path
    facts_path: Path
    frontend_path: Path
    parse_model: ModelConfig | None = None
    answer_model: ModelConfig | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            resolve_path(os.getenv("DATASET_PATH", "backend/data/catalog.csv")),
            resolve_path(os.getenv("FACTS_PATH", "backend/data/facts.json")),
            PROJECT_ROOT / "frontend",
            model_config("PARSE"),
            model_config("ANSWER"),
        )
