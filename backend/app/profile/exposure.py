"""Платёжная экспозиция по всем платежам направления, без обрезки топом карточек."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.contracts.common import Contract, Id, Score, Text
from app.profile.graph import PAYMENTS_SQL, linked


class PaymentExposure(Contract):
    company_id: Id
    direction: Literal["incoming", "outgoing"]
    total_amount_rub: Decimal = Field(ge=0, allow_inf_nan=False)
    matched_amount_rub: Decimal = Field(ge=0, allow_inf_nan=False)
    share: Score | None = Field(description="Доля выбранных платежей; null при нулевом обороте")
    matched_partners: list[Id] = Field(default_factory=list)
    unknown_segment_amount_rub: Decimal = Field(ge=0, allow_inf_nan=False)
    source_fields: list[Text] = Field(default_factory=lambda: ["PAYER", "BENEFICIARY", "PAY_AMT"])
    warnings: list[Text] = Field(default_factory=list)


async def payment_exposure(
    connection: Any,
    company_id: str,
    direction: Literal["incoming", "outgoing"],
    partner_ids: list[str],
    segments: list[str],
) -> PaymentExposure:
    total = matched = unknown = Decimal(0)
    partners: set[str] = set()
    for record in await connection.fetch(PAYMENTS_SQL, company_id):
        if record["direction"] != direction:
            continue
        amount = Decimal(str(record["amount_rub"]))
        if not amount.is_finite() or amount < 0:
            raise ValueError("Некорректная сумма платежа")
        total += amount
        partner = linked(record, "partner_id")
        segment = partner.segment
        if segment is None:
            unknown += amount
        if partner_ids and partner.company_id not in partner_ids:
            continue
        if segments and segment not in segments:
            continue
        matched += amount
        partners.add(partner.company_id)
    warnings = []
    if total == 0:
        warnings.append("Нет ненулевых платежей направления; доля не определена")
    if segments and unknown:
        warnings.append(
            "У части контрагентов неизвестен сегмент; они не включены в отраслевой отбор"
        )
    return PaymentExposure(
        company_id=company_id,
        direction=direction,
        total_amount_rub=total,
        matched_amount_rub=matched,
        share=float(matched / total) if total else None,
        matched_partners=sorted(partners),
        unknown_segment_amount_rub=unknown,
        warnings=warnings,
    )
