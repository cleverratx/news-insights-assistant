from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION: Final = "1.0"

Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Significance(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BankProduct(StrEnum):
    SETTLEMENT = "settlement"
    GUARANTEE = "guarantee"
    LOAN = "loan"
    LEASING = "leasing"
    DEPOSIT = "deposit"
    PAYROLL = "payroll"
    FACTORING = "factoring"


def require_unique(ids: Iterable[str], what: str) -> None:
    repeated = sorted(value for value, count in Counter(ids).items() if count > 1)
    if repeated:
        raise ValueError(f"Повторяются идентификаторы {what}: {', '.join(repeated)}")
