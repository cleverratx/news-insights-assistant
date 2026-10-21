from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Score, Text, require_unique


class MaterialKind(StrEnum):
    NEWS = "news"
    BANK_ANALYTICS = "bank_analytics"


class Channel(StrEnum):
    RSS = "rss"
    HTML = "html"
    PDF = "pdf"
    API = "api"
    MANUAL = "manual"


class Topic(StrEnum):
    KEY_RATE = "key_rate"
    INFLATION = "inflation"
    FX = "fx"
    COMMODITIES = "commodities"
    TRADE = "trade"
    LOGISTICS = "logistics"
    SANCTIONS = "sanctions"
    REGULATION = "regulation"
    TAXES = "taxes"
    LABOR = "labor"
    CONSUMPTION = "consumption"
    DEBT_MARKET = "debt_market"
    MACRO = "macro"
    INDUSTRY = "industry"
    REGIONAL = "regional"
    COMPANY = "company"
    OTHER = "other"


class FactStatus(StrEnum):
    CONFIRMED = "confirmed"
    REPORTED = "reported"
    FORECAST = "forecast"
    ASSUMPTION = "assumption"


class Source(Contract):
    name: Text = Field(description="Издание или подразделение банка")
    channel: Channel
    url: HttpUrl | None = None


class Fragment(Contract):
    """Кусок текста, на который можно сослаться точно: абзац новости, раздел обзора."""

    id: Id
    text: Text
    locator: Text | None = Field(default=None, description="Страница, раздел или номер абзаца")


class KeyFact(Contract):
    id: Id
    text: Text
    status: FactStatus = Field(
        description=(
            "confirmed — подтверждено источником, reported — со слов участника, "
            "forecast — прогноз, assumption — предположение"
        )
    )
    fragment_id: Id | None = None
    who: list[Text] = Field(
        default_factory=list, description="Участники: компании, ведомства, люди"
    )
    when: Text | None = Field(default=None, description="Когда произошло или вступит в силу")
    numbers: list[Text] = Field(
        default_factory=list, description="Цифры факта, как в тексте: «14%», «3,3 млрд долларов»"
    )
    source_says: Text | None = Field(
        default=None, description="Для reported и forecast — чьи это слова или прогноз"
    )


class Horizon(StrEnum):
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


class Classification(Contract):
    topics: list[Topic] = Field(min_length=1, description="Тип события")
    okved_codes: list[Text] = Field(default_factory=list)
    segments: list[Id] = Field(
        default_factory=list, description="Затронутые сегменты рынка из справочника ОКВЭД"
    )
    industries: list[Text] = Field(default_factory=list, description="Отрасли словами")
    regions: list[Text] = Field(default_factory=list)
    countries: list[Text] = Field(default_factory=list)
    entities: list[Text] = Field(default_factory=list)
    sentiment: Literal["positive", "negative", "neutral", "mixed"] | None = None
    horizon: Horizon | None = Field(
        default=None,
        description="Когда скажется на бизнесе: days — сразу, weeks — в ближайшие недели, "
        "months — в пределах года, years — дольше",
    )
    confidence: Score | None = None
    method: Literal["llm", "rules"] = Field(
        default="llm", description="llm — разобрала модель, rules — запасной разбор по словарю"
    )
    model: Text | None = Field(default=None, description="Какая разрешённая модель разобрала")


class Dedup(Contract):
    content_hash: Text
    cluster_id: Id | None = Field(default=None, description="Сюжет, в который склеены дубликаты")
    duplicate_of: Id | None = None


class NewsItem(Contract):
    """Единица ленты: внешняя новость или материал аналитики банка."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: Id
    kind: MaterialKind
    source: Source
    url: HttpUrl | None = None
    title: Text
    published_at: AwareDatetime
    collected_at: AwareDatetime
    language: Text = "ru"
    text: Text
    summary: Text | None = None
    fragments: list[Fragment] = Field(default_factory=list)
    key_facts: list[KeyFact] = Field(default_factory=list)
    classification: Classification | None = Field(
        default=None, description="Пусто, пока материал не прошёл классификацию"
    )
    dedup: Dedup

    @model_validator(mode="after")
    def consistent_references(self) -> Self:
        require_unique((fragment.id for fragment in self.fragments), "фрагментов")
        require_unique((fact.id for fact in self.key_facts), "фактов")
        fragment_ids = {fragment.id for fragment in self.fragments}
        dangling = [
            fact.id
            for fact in self.key_facts
            if fact.fragment_id is not None and fact.fragment_id not in fragment_ids
        ]
        if dangling:
            raise ValueError(f"Факты ссылаются на несуществующие фрагменты: {', '.join(dangling)}")
        if self.dedup.duplicate_of == self.id:
            raise ValueError("Материал не может быть дубликатом самого себя")
        return self


class StoryMatch(StrEnum):
    FIRST = "first"
    EXACT = "exact"
    SIMILAR = "similar"


class StoryItem(Contract):
    """Публикация в сюжете: какое издание, когда и насколько она похожа на сюжет."""

    news_id: Id
    source: Text = Field(description="Издание")
    url: HttpUrl | None = Field(default=None, description="Ссылка на первоисточник")
    title: Text
    published_at: AwareDatetime
    match: StoryMatch = Field(
        description="first — первая публикация, exact — перепечатка, similar — то же событие"
    )
    similarity: Score | None = Field(default=None, description="Сходство с ближайшей публикацией")
    duplicate_of: Id | None = None


class NewsStory(Contract):
    """Одно событие из нескольких изданий — одна карточка; ссылки на все публикации сохранены."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: Id = Field(description="Dedup.cluster_id публикаций сюжета")
    representative_id: Id = Field(
        description="Публикация для карточки: официальный источник, иначе первая, иначе полнее"
    )
    title: Text
    first_published_at: AwareDatetime
    last_published_at: AwareDatetime
    items: list[StoryItem] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        ids = [item.news_id for item in self.items]
        require_unique(ids, "публикаций сюжета")
        if self.representative_id not in ids:
            raise ValueError("Публикация для карточки должна быть в сюжете")
        if self.first_published_at > self.last_published_at:
            raise ValueError("Сюжет начинается позже, чем заканчивается")
        return self
