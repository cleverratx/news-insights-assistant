"""Демо-легенда (ADR 0003): условные названия поверх обезличенных SYNTHETIC_U.

Легенда задаёт название, описание и бренды демо-компании, а её крупнейшим контрагентам —
название, роль (покупатель, поставщик, перевозчик) и отрасль словами, если контрагента нет
в выгрузке юрлиц. Признаки, чувствительность и отбор считаются по выгрузке: если легенда
и данные расходятся, правы данные. Условное название в поиск новостей не идёт, из легенды
в поиск попадает только отрасль контрагента, которого нет в выгрузке.

Нет файла, пустой или битый файл, пустое имя — сервис работает на синтетических
идентификаторах, и лента от этого не меняется.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.contracts.profile import (
    CompanyProfile,
    CounterpartyRole,
    IndustryResolution,
    Legend,
    PartnerLegend,
)
from app.core.config import get_settings
from app.profile.okved import resolve

ROLE_WORDS = {
    "покупатель": CounterpartyRole.BUYER,
    "поставщик": CounterpartyRole.SUPPLIER,
    "перевозчик": CounterpartyRole.CARRIER,
}

logger = logging.getLogger(__name__)


class LegendFileError(Exception):
    pass


def blank_is_none(value: object) -> object:
    return value.strip() or None if isinstance(value, str) else value


class PartnerEntry(BaseModel):
    """Контрагент демо-компании. Лишние поля заготовки (industry_hint) допустимы."""

    model_config = ConfigDict(extra="ignore")

    synthetic_u: str
    name: str = ""
    role: CounterpartyRole | None = None
    industry: str | None = None
    okved_code: str | None = None

    @field_validator("role", mode="before")
    @classmethod
    def role_in_words(cls, value: object) -> object:
        value = blank_is_none(value)
        if isinstance(value, str):
            return ROLE_WORDS.get(value.lower(), value)
        return value

    @field_validator("industry", "okved_code", mode="before")
    @classmethod
    def empty(cls, value: object) -> object:
        return blank_is_none(value)


class CompanyEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    synthetic_u: str
    name: str = ""
    description: str | None = None
    brands: list[str] = Field(default_factory=list)
    partners: list[PartnerEntry] = Field(default_factory=list)

    @field_validator("description", mode="before")
    @classmethod
    def empty(cls, value: object) -> object:
        return blank_is_none(value)


class LegendFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    companies: list[CompanyEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class Partner:
    name: str
    industry: str | None = None
    segment: str | None = None


@dataclass
class Legends:
    """Легенда в виде справочников: название по SYNTHETIC_U и роль контрагента у клиента."""

    companies: dict[str, Legend] = field(default_factory=dict)
    partners: dict[str, Partner] = field(default_factory=dict)
    roles: dict[tuple[str, str], CounterpartyRole] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.companies or self.partners)

    @classmethod
    def from_entries(cls, entries: list[CompanyEntry]) -> "Legends":
        legends = cls()
        for entry in entries:
            if entry.name.strip():
                legends.companies[entry.synthetic_u] = Legend(
                    name=entry.name.strip(), description=entry.description, brands=entry.brands
                )
            for partner in entry.partners:
                if not partner.name.strip():
                    continue
                if partner.role is not None:
                    legends.roles[(entry.synthetic_u, partner.synthetic_u)] = partner.role
                info = resolve(partner.okved_code, partner.industry)
                known = info.resolution is not IndustryResolution.UNKNOWN
                segment = info.segment.id if known and info.segment.id != "other" else None
                legends.partners.setdefault(
                    partner.synthetic_u,
                    Partner(
                        name=partner.name.strip(),
                        industry=partner.industry or (info.class_name if known else None),
                        segment=segment,
                    ),
                )
        return legends

    def name(self, company_id: str) -> str | None:
        company = self.companies.get(company_id)
        if company is not None:
            return company.name
        partner = self.partners.get(company_id)
        return partner.name if partner else None

    def partner(
        self, client_id: str, company_id: str, segment_in_data: str | None
    ) -> PartnerLegend | None:
        """Легенда связанной компании. Отрасль из легенды — только если её нет в выгрузке."""
        name = self.name(company_id)
        if name is None:
            return None
        info = self.partners.get(company_id)
        from_legend = segment_in_data is None and info is not None
        return PartnerLegend(
            name=name,
            role=self.roles.get((client_id, company_id)),
            industry=info.industry if from_legend and info else None,
            segment=info.segment if from_legend and info else None,
        )


def parse_legends(text: str) -> Legends:
    try:
        raw = yaml.safe_load(text) or {}
        return Legends.from_entries(LegendFile.model_validate(raw).companies)
    except (yaml.YAMLError, ValidationError) as error:
        raise LegendFileError(f"Файл легенды не разобран: {error}") from error


@lru_cache(maxsize=4)
def read_legends(path: str, modified: float) -> Legends:
    """Кэш по времени изменения: поправленный файл подхватывается без перезапуска."""
    try:
        return parse_legends(Path(path).read_text(encoding="utf-8"))
    except (OSError, LegendFileError) as error:
        logger.warning("Легенда не загружена, работаем на идентификаторах: %s", error)
        return Legends()


def load_legends(path: Path) -> Legends:
    try:
        modified = path.stat().st_mtime
    except OSError:
        return Legends()
    return read_legends(str(path), modified)


def get_legends() -> Legends:
    """Легенда по настройкам: LEGEND_FILE относительно DATA_DIR, пустое значение — без легенды."""
    settings = get_settings()
    if not settings.legend_file:
        return Legends()
    return load_legends(Path(settings.data_dir) / settings.legend_file)


def apply_legend(profile: CompanyProfile, legends: Legends) -> CompanyProfile:
    """Профиль с названиями из легенды. Признаки и чувствительность не меняются."""
    if not legends:
        return profile
    client = profile.company_id
    counterparties = [
        item.model_copy(update={"legend": legends.partner(client, item.company_id, item.segment)})
        for item in profile.counterparties
    ]
    related = [
        item.model_copy(update={"legend": legends.partner(client, item.company_id, item.segment)})
        for item in profile.related_companies
    ]
    group = profile.group
    if group is not None:
        members = [
            item.model_copy(
                update={"legend": legends.partner(client, item.company_id, item.segment)}
            )
            for item in group.members
        ]
        group = group.model_copy(update={"members": members})
    return profile.model_copy(
        update={
            "legend": legends.companies.get(client),
            "counterparties": counterparties,
            "related_companies": related,
            "group": group,
        }
    )
