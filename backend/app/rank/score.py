"""Скоринг релевантности (A4-02): вес признаков компании, свежесть и масштаб события —
итоговый балл и расшифровка вклада каждого признака для блока «почему эта новость для вас».

Балл держится на совпадении с профилем (правила A4-01): нерелевантная компании новость
получает ноль, какой бы свежей и громкой она ни была. Свежесть и масштаб только двигают
релевантную новость выше или ниже: `final = relevance × (0,5 + 0,3 × свежесть + 0,2 × масштаб)`.
Вклад признака — его доля в итоговом балле по весу правила, сумма вкладов равна `final`.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.contracts.common import Significance
from app.contracts.news import FactStatus, Horizon, MaterialKind, NewsItem, Topic
from app.contracts.profile import CompanyProfile
from app.contracts.rank import FeatureMatch, RankedItem, RankStage, ScoreBreakdown
from app.rank.rules import TOPIC_TITLES, Matching, match

RELEVANCE_SHARE = 0.5
RECENCY_SHARE = 0.3
SCALE_SHARE = 0.2
HALF_LIFE_HOURS = {
    Horizon.DAYS: 24.0,
    Horizon.WEEKS: 72.0,
    Horizon.MONTHS: 168.0,
    Horizon.YEARS: 336.0,
}
DEFAULT_HALF_LIFE = 72.0
BANK_HALF_LIFE_FACTOR = 2.0
TOPIC_SCALE = {
    Topic.KEY_RATE: 0.8,
    Topic.SANCTIONS: 0.7,
    Topic.TAXES: 0.7,
    Topic.MACRO: 0.6,
    Topic.REGULATION: 0.6,
    Topic.INFLATION: 0.6,
    Topic.FX: 0.6,
    Topic.COMMODITIES: 0.5,
    Topic.TRADE: 0.5,
    Topic.DEBT_MARKET: 0.5,
    Topic.LOGISTICS: 0.4,
    Topic.LABOR: 0.4,
    Topic.CONSUMPTION: 0.4,
    Topic.INDUSTRY: 0.4,
    Topic.REGIONAL: 0.3,
    Topic.COMPANY: 0.3,
    Topic.OTHER: 0.1,
}
COVERAGE_STEP = 0.1
COVERAGE_MAX = 0.2
NUMBERS_BONUS = 0.1
HIGH = 0.6
MEDIUM = 0.35


@dataclass
class Scored:
    """Балл новости для компании с расшифровкой: из чего сложился и какие признаки дали вклад."""

    company_id: str
    news_id: str
    relevance: float
    recency: float
    scale: float
    final: float
    contributions: list[FeatureMatch] = field(default_factory=list)
    factors: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def significance(self) -> Significance:
        if self.final >= HIGH:
            return Significance.HIGH
        return Significance.MEDIUM if self.final >= MEDIUM else Significance.LOW

    def breakdown(self) -> ScoreBreakdown:
        return ScoreBreakdown(
            relevance=self.relevance,
            recency=self.recency,
            scale=self.scale,
            final=self.final,
            factors=self.factors,
        )

    def ranked(self, rank: int, run_id: str | None = None) -> RankedItem:
        return RankedItem(
            company_id=self.company_id,
            news_id=self.news_id,
            stage=RankStage.CANDIDATE,
            rank=rank,
            score=self.breakdown(),
            significance=self.significance,
            matched_features=self.contributions,
            matched_terms=self.terms,
            run_id=run_id,
        )


def hours_ago(hours: float) -> str:
    if hours < 1:
        return "меньше часа назад"
    if hours < 48:
        return f"{hours:.0f} ч назад"
    return f"{hours / 24:.0f} дн. назад"


def recency(news: NewsItem, now: datetime) -> tuple[float, str]:
    """Свежесть с полураспадом по горизонту влияния: новость, которая скажется через годы,
    устаревает медленнее, чем новость дня. Позиция банка живёт вдвое дольше новостей."""
    hours = max((now - news.published_at).total_seconds() / 3600, 0.0)
    horizon = news.classification.horizon if news.classification else None
    half_life = HALF_LIFE_HOURS[horizon] if horizon else DEFAULT_HALF_LIFE
    if news.kind is MaterialKind.BANK_ANALYTICS:
        half_life *= BANK_HALF_LIFE_FACTOR
    value = round(0.5 ** (hours / half_life), 4)
    return value, f"Опубликовано {hours_ago(hours)}, свежесть {value * 100:.0f}%"


def scale(news: NewsItem, coverage: int = 1) -> tuple[float, str]:
    """Масштаб события: самая крупная тема, число изданий в сюжете и подтверждённые цифры."""
    topics = news.classification.topics if news.classification else [Topic.OTHER]
    top = max(topics, key=lambda topic: TOPIC_SCALE[topic])
    value = TOPIC_SCALE[top]
    parts = [f"тема «{TOPIC_TITLES[top]}»"]
    if coverage > 1:
        value += min(COVERAGE_STEP * (coverage - 1), COVERAGE_MAX)
        parts.append(f"пишут {coverage} изданий")
    if any(fact.numbers and fact.status is FactStatus.CONFIRMED for fact in news.key_facts):
        value += NUMBERS_BONUS
        parts.append("есть подтверждённые цифры")
    value = round(min(value, 1.0), 4)
    return value, f"Масштаб события {value * 100:.0f}%: {', '.join(parts)}"


def contributions(matching: Matching, final: float) -> list[FeatureMatch]:
    best: dict[str, tuple[float, str, str]] = {}
    for hit in sorted(matching.hits, key=lambda item: item.weight, reverse=True):
        best.setdefault(hit.feature_id, (hit.weight, hit.rule.value, hit.reason))
    total = sum(weight for weight, _, _ in best.values())
    if total == 0:
        return []
    return [
        FeatureMatch(
            feature_id=feature_id,
            reason=reason,
            rule=rule,
            weight=weight,
            contribution=round(final * weight / total, 4),
        )
        for feature_id, (weight, rule, reason) in best.items()
    ]


def score(
    profile: CompanyProfile,
    news: NewsItem,
    now: datetime,
    coverage: int = 1,
    matching: Matching | None = None,
) -> Scored:
    if matching is None:
        matching = match(profile, news)
    relevance = matching.score
    fresh, fresh_note = recency(news, now)
    size, size_note = scale(news, coverage)
    modifier = RELEVANCE_SHARE + RECENCY_SHARE * fresh + SCALE_SHARE * size
    final = round(min(relevance * modifier, 1.0), 4)
    factors = [fresh_note, size_note, *matching.penalties]
    if relevance == 0:
        factors.insert(0, "Не совпадает ни с одним признаком компании")
    return Scored(
        company_id=profile.company_id,
        news_id=news.id,
        relevance=relevance,
        recency=fresh,
        scale=size,
        final=final,
        contributions=contributions(matching, final),
        factors=factors,
        terms=matching.terms(),
        warnings=matching.warnings,
    )
