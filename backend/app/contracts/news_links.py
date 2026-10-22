"""A5-16: связи между новостями — сюжеты по общим сущностям и теме."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Text, require_unique
from app.contracts.news import NewsCard, Topic


class LinkKind(StrEnum):
    CONTINUATION = "continuation"
    REINFORCES = "reinforces"
    CONTRADICTS = "contradicts"


class NewsLink(Contract):
    """Связь более новой публикации с более ранней."""

    news_id: Id = Field(description="Более новая публикация")
    linked_id: Id = Field(description="Более ранняя публикация, с которой она связана")
    kind: LinkKind = Field(
        description=(
            "continuation — развитие сюжета о тех же участниках; reinforces — другое событие "
            "той же направленности по той же теме; contradicts — та же тема и участники, "
            "но противоположная оценка"
        )
    )
    label: Text = Field(description="«Продолжение», «Усиливает» или «Противоречит»")
    explanation: Text = Field(description="Коротко: что общего и в чём связь")
    shared: list[Text] = Field(min_length=1, description="Общие участники, отрасли, регионы")
    shared_keys: list[Id] = Field(min_length=1, description="Ключи общих сущностей A3-11")
    topics: list[Topic] = Field(min_length=1, description="Общие темы")
    strength: float = Field(ge=0, description="Сумма весов общих сущностей")
    confidence: Literal["high", "medium"]
    method: Literal["entities"] = Field(
        default="entities", description="Связь построена по общим сущностям и теме, без LLM"
    )


class NewsThread(Contract):
    id: Id
    title: Text
    news: list[NewsCard] = Field(min_length=2, description="Публикации сюжета, ранние первыми")
    entities: list[Text] = Field(default_factory=list, description="Что объединяет сюжет")
    topics: list[Topic] = Field(default_factory=list)
    links: list[NewsLink] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        ids = [card.news_id for card in self.news]
        require_unique(ids, "публикаций")
        if any({link.news_id, link.linked_id} - set(ids) for link in self.links):
            raise ValueError("Связь ссылается на публикацию вне сюжета")
        return self


class NewsThreads(Contract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    generated_at: AwareDatetime
    period_days: int = Field(ge=1, le=30)
    threads: list[NewsThread] = Field(default_factory=list)
    considered: int = Field(ge=0, description="Сюжетов за период, включая одиночные")
    warnings: list[Text] = Field(default_factory=list)


class NewsLinks(Contract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    news_id: Id
    links: list[NewsLink] = Field(default_factory=list)
    cards: list[NewsCard] = Field(default_factory=list, description="Карточки связанных публикаций")
    warnings: list[Text] = Field(default_factory=list)
