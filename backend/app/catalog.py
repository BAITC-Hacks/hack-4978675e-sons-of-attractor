import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .schemas import Hours, Money, parse_calendar_date

if TYPE_CHECKING:
    from .facts import FactsSnapshot


class CatalogError(ValueError):
    pass


class Contractor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    anon_name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    categories: tuple[str, ...] = Field(min_length=1)
    event_formats: tuple[str, ...] = Field(min_length=1)
    languages: tuple[str, ...] = Field(min_length=1)
    busy_dates: tuple[date, ...]
    price_from_kzt: Money
    max_hours: Hours | None
    description: str
    synthetic: bool
    price_imputed: bool
    city_imputed: bool


@dataclass(frozen=True)
class Catalog:
    profiles: tuple[Contractor, ...]
    sha256: str
    facts: "FactsSnapshot | None" = None

    @property
    def explanation_mode(self):
        return self.facts.mode if self.facts is not None else "structured_only"

    @property
    def facts_sha256(self) -> str | None:
        return self.facts.sha256 if self.facts is not None else None

    def format_evidence(self, contractor_id: str, event_format: str) -> bool:
        return self.facts is not None and self.facts.has_format(contractor_id, event_format)

    def dictionaries(self) -> dict[str, list[str]]:
        return {
            "cities": sorted({p.city for p in self.profiles}),
            **{
                field: sorted({item for p in self.profiles for item in getattr(p, field)})
                for field in ("categories", "event_formats", "languages")
            },
        }


def split_list(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split("|") if part.strip()))


def parse_bool(value: str) -> bool:
    if value.strip() not in ("True", "False"):
        raise ValueError("Ожидается True или False")
    return value.strip() == "True"


def load_catalog(path: Path) -> Catalog:
    try:
        raw = path.read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames
        required = set(Contractor.model_fields)
        if not headers or len(headers) != len(set(headers)) or not required.issubset(headers):
            raise CatalogError("Отсутствуют обязательные столбцы или заголовки повторяются")
        profiles: list[Contractor] = []
        ids: set[str] = set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise CatalogError(f"Строка {reader.line_num}: неверное число столбцов")
            try:
                values: dict = {key: row[key].strip() for key in required}
                values["description"] = row["description"]
                for field in ("categories", "event_formats", "languages"):
                    values[field] = split_list(row[field])
                values["busy_dates"] = tuple(parse_calendar_date(d) for d in split_list(row["busy_dates"]))
                for field in ("synthetic", "price_imputed", "city_imputed"):
                    values[field] = parse_bool(row[field])
                if not re.fullmatch(r"[0-9]+", values["price_from_kzt"]):
                    raise ValueError("Цена должна быть целым числом")
                values["price_from_kzt"] = int(values["price_from_kzt"])
                values["max_hours"] = float(values["max_hours"]) if values["max_hours"] else None
                profile = Contractor.model_validate(values)
                if profile.id in ids:
                    raise ValueError("Повторяющийся ID")
            except ValueError as exc:
                raise CatalogError(f"Строка {reader.line_num}: некорректные данные") from exc
            ids.add(profile.id)
            profiles.append(profile)
        if not profiles:
            raise CatalogError("Каталог пуст")
        return Catalog(tuple(sorted(profiles, key=lambda p: p.id)), hashlib.sha256(raw).hexdigest())
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CatalogError("Не удалось прочитать CSV каталога") from exc
