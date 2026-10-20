"""Сборка CompanyProfile из модулей профиля: отрасль, базовые признаки, продукты, граф связей,
производные признаки и положение в отрасли. Один вход — для API и для инструментов агента."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app.contracts.profile import (
    CompanyProfile,
    DataQuality,
    FeatureKind,
    IndustryResolution,
    MarketPosition,
    ProfileFeature,
)
from app.profile.derived import derive, merge_exposures
from app.profile.features import base_features
from app.profile.graph import CompanyLinks, fetch_links, link_profile
from app.profile.okved import industry
from app.profile.products import product_profile
from app.profile.row import CompanyRow, source_field

MIN_PEERS = 10

COMPANY_SQL = "SELECT * FROM companies WHERE company_id = $1"
PEERS_SQL = """
SELECT count(*) AS peers,
       count(*) FILTER (WHERE revenue_rub > $2) + 1 AS rank,
       coalesce(sum(revenue_rub), 0) AS total
FROM companies
WHERE revenue_rub > 0 AND {condition}
"""
CONDITIONS = {
    "okved_code": "okved_code = $1",
    "okved_group": "split_part(okved_code, '.', 1) = $1",
}


@dataclass(frozen=True)
class Peers:
    basis: Literal["okved_code", "okved_group"]
    count: int
    rank: int
    total: Decimal


async def fetch_peers(connection: Any, row: CompanyRow) -> Peers | None:
    """Место по выручке среди компаний того же кода ОКВЭД, а если их мало — того же класса."""
    if not row.okved_code or not row.revenue_rub:
        return None
    keys: dict[Literal["okved_code", "okved_group"], str] = {
        "okved_code": row.okved_code,
        "okved_group": row.okved_code[:2],
    }
    for basis, key in keys.items():
        record = await connection.fetchrow(
            PEERS_SQL.format(condition=CONDITIONS[basis]), key, row.revenue_rub
        )
        if record["peers"] >= MIN_PEERS or basis == "okved_group":
            if not record["peers"]:
                return None
            return Peers(basis, record["peers"], record["rank"], record["total"])
    return None


def market_position(row: CompanyRow, peers: Peers) -> tuple[MarketPosition, ProfileFeature]:
    revenue = row.revenue_rub or Decimal(0)
    percentile = 1.0 if peers.count == 1 else (peers.count - peers.rank) / (peers.count - 1)
    position = MarketPosition(
        peer_basis=peers.basis,
        peer_count=peers.count,
        revenue_rank=peers.rank,
        revenue_percentile=round(percentile, 4),
        peer_revenue_share=min(1.0, float(revenue / peers.total)) if peers.total else 0.0,
    )
    scope = (
        f"с ОКВЭД {row.okved_code}"
        if peers.basis == "okved_code"
        else f"с ОКВЭД класса {row.okved_code[:2] if row.okved_code else ''}"
    )
    feature = ProfileFeature(
        id="feature:market_rank",
        kind=FeatureKind.FINANCE,
        label=f"{peers.rank}-е место по выручке среди {peers.count} компаний {scope} в базе банка",
        value=peers.rank,
        source_fields=[source_field("revenue_rub"), source_field("okved_code")],
        derived=True,
    )
    return position, feature


def industry_feature(row: CompanyRow) -> ProfileFeature | None:
    result = industry(row.okved_code, row.okved_name)
    if result.resolved_by is IndustryResolution.UNKNOWN:
        return None
    by_code = result.resolved_by is IndustryResolution.CODE
    basis = f"ОКВЭД {result.okved_code}" if by_code else "по названию вида деятельности"
    used = (
        ["okved_code", *(["okved_name"] if row.okved_name else [])] if by_code else ["okved_name"]
    )
    return ProfileFeature(
        id="feature:industry",
        kind=FeatureKind.INDUSTRY,
        label=f"Отрасль: {result.segment_name} ({basis})",
        value=result.segment,
        source_fields=[source_field(column) for column in used],
        derived=not by_code,
    )


def assemble(row: CompanyRow, links: CompanyLinks, peers: Peers | None = None) -> CompanyProfile:
    base = base_features(row)
    products = product_profile(row)
    graph = link_profile(row, links)
    derived = derive(row, base, products, graph)
    position, rank_feature = market_position(row, peers) if peers else (None, None)
    industry_info = industry_feature(row)
    resolved = industry(row.okved_code, row.okved_name)
    missing = [*base.missing_fields, *products.missing_fields, *graph.missing_fields]
    warnings = [*base.warnings, *products.warnings, *graph.warnings, *derived.warnings]
    if resolved.resolved_by is IndustryResolution.UNKNOWN:
        missing.append(source_field("okved_code"))
    elif resolved.resolved_by is IndustryResolution.NAME:
        warnings.append("Код ОКВЭД пустой или неизвестный: отрасль определена по названию")
    return CompanyProfile(
        company_id=row.company_id,
        client_type=base.client_type,
        ownership=base.ownership,
        industry=resolved,
        scale=base.scale,
        location=base.location,
        products=products.products,
        foreign_trade=products.foreign_trade,
        group=graph.group,
        counterparties=graph.counterparties,
        linked_persons_count=graph.linked_persons_count,
        person_regions=graph.person_regions,
        related_companies=graph.related_companies,
        has_key_owner_mark=row.has_key_owner_mark,
        market_position=position,
        features=[
            *([industry_info] if industry_info else []),
            *base.features,
            *([rank_feature] if rank_feature else []),
            *products.features,
            *graph.features,
            *derived.features,
        ],
        exposures=merge_exposures(products.exposures, graph.exposures, derived.exposures),
        data_quality=DataQuality(
            missing_fields=list(dict.fromkeys(missing)),
            warnings=list(dict.fromkeys(warnings)),
        ),
    )


async def load_profile(connection: Any, company_id: str) -> CompanyProfile | None:
    record = await connection.fetchrow(COMPANY_SQL, company_id)
    if record is None:
        return None
    row = CompanyRow.from_record(record)
    links = await fetch_links(connection, row)
    peers = await fetch_peers(connection, row)
    return assemble(row, links, peers)
