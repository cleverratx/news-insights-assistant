from typing import Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Score, Text, require_unique
from app.contracts.news import MaterialKind


class SearchHit(Contract):
    """Найденный фрагмент: цитата из него дословно и ссылка на документ и место в нём."""

    doc_id: Id = Field(description="NewsItem.id: news:… или bank:…")
    kind: MaterialKind
    title: Text
    source: Text
    url: HttpUrl | None = Field(default=None, description="Первоисточник новости")
    document: Text = Field(description="Как найти место: «документ, стр. N, абзац M»")
    published_at: AwareDatetime
    fragment_id: Id
    locator: Text | None = None
    quote: Text = Field(description="Дословная цитата из фрагмента")
    score: Score = Field(description="Итоговая релевантность")
    bm25: float = Field(ge=0, allow_inf_nan=False)
    rerank: Score | None = Field(default=None, description="Оценка модели, если переранжировали")


class SearchResult(Contract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    query: Text
    hits: list[SearchHit] = Field(default_factory=list)
    reranked: bool = Field(description="Порядок уточнила модель; false — только BM25")
    model: Text | None = None
    searched: int = Field(ge=0, description="Сколько фрагментов было в поиске")
    warnings: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_hits(self) -> Self:
        require_unique((f"{hit.doc_id}#{hit.fragment_id}" for hit in self.hits), "находок")
        return self
