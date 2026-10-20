from collections.abc import Mapping
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.data.loader import COMPANY_FIELDS


class CompanyRow(BaseModel):
    """Строка таблицы companies, как её записал загрузчик A2-01: значения уже очищены."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    company_id: str
    org_type: str | None = None
    ownership: str | None = None
    age_band: str | None = None
    lifecycle_stage: str | None = None
    okved_code: str | None = None
    okved_name: str | None = None
    has_key_owner_mark: bool | None = None
    employees_fns: str | None = None
    employees_spark: str | None = None
    client_type: str | None = None
    has_settlement: bool | None = None
    has_guarantee: bool | None = None
    has_loan: bool | None = None
    has_leasing: bool | None = None
    has_deposit: bool | None = None
    has_payroll: bool | None = None
    has_customs_records: bool | None = None
    customs_countries: list[str] = Field(default_factory=list)
    has_factoring: bool | None = None
    revenue_rub: Decimal | None = None
    group_id: str | None = None
    city: str | None = None
    region: str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "CompanyRow":
        return cls.model_validate(dict(record))


def source_field(column: str) -> str:
    """Поле выгрузки, из которого взят столбец: на него ссылается признак профиля."""
    return COMPANY_FIELDS[column]
