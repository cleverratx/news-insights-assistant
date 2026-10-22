from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Id
from app.contracts.insight import CheckIssue


class CriticReport(Contract):
    passed: bool
    semantic_checked: bool
    issues: list[CheckIssue] = Field(default_factory=list)
    suggested_tools: list[Id] = Field(default_factory=list)
    cost_rub: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    unavailable: bool = False

    @model_validator(mode="after")
    def confirmed(self) -> Self:
        if self.passed and (
            not self.semantic_checked
            or self.unavailable
            or any(not issue.resolved for issue in self.issues)
        ):
            raise ValueError(
                "Проверка не пройдена при наличии замечаний или без смысловой проверки"
            )
        return self
