"""Кеш разборов моделью (A3-08): один и тот же вход уходит в модель один раз.

Ключ — задача, версия и хеш входа. Вход — это то, что видит модель (текст новости
в промпте, запрос с фрагментами), поэтому перепечатка под другим id, повторный сбор,
пересоздание базы и --retry-rules берут готовый разбор и не тратят дневной лимит.

Версия считается из текста системного промпта и схемы ответа. Поменяли промпт или схему —
получили новую версию: старые результаты не выдаются за новые, но остаются в таблице,
и видно, как менялся разбор одного и того же входа. Хранится уже проверенный ответ модели,
а не сырой текст: из кеша не может прийти то, что не прошло схему.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    kind text NOT NULL,
    version text NOT NULL,
    input_hash text NOT NULL,
    result jsonb NOT NULL,
    model text NOT NULL,
    cost_rub numeric NOT NULL DEFAULT 0,
    hits integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    last_hit_at timestamptz,
    PRIMARY KEY (kind, version, input_hash)
);
"""
GET_SQL = """
UPDATE llm_cache SET hits = hits + 1, last_hit_at = $4
WHERE kind = $1 AND version = $2 AND input_hash = $3
RETURNING result, model, created_at
"""
PUT_SQL = """
INSERT INTO llm_cache (kind, version, input_hash, result, model, cost_rub, created_at)
VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
ON CONFLICT (kind, version, input_hash) DO UPDATE SET
    result = EXCLUDED.result, model = EXCLUDED.model, cost_rub = EXCLUDED.cost_rub,
    created_at = EXCLUDED.created_at
"""
HISTORY_SQL = """
SELECT version, model, result, hits, created_at FROM llm_cache
WHERE kind = $1 AND input_hash = $2
ORDER BY created_at
"""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def version_of(*parts: str) -> str:
    """Версия разбора: хеш промпта и схемы ответа. Правка любой части — новая версия."""
    return digest("\x00".join(parts))[:12]


def schema_text(model: Any) -> str:
    return json.dumps(model.model_json_schema(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class Cached:
    result: dict[str, Any]
    model: str
    created_at: datetime


@dataclass(frozen=True)
class Version:
    version: str
    model: str
    result: dict[str, Any]
    hits: int
    created_at: datetime


class LLMCache:
    """Кеш поверх соединения с PostgreSQL. Таблица создаётся при первом обращении."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.ready = False

    async def ensure(self) -> None:
        if not self.ready:
            await self.connection.execute(SCHEMA)
            self.ready = True

    async def get(
        self, kind: str, version: str, prompt: str, now: datetime | None = None
    ) -> Cached | None:
        await self.ensure()
        record = await self.connection.fetchrow(
            GET_SQL, kind, version, digest(prompt), now or datetime.now(UTC)
        )
        if record is None:
            return None
        result = record["result"]
        return Cached(
            result=json.loads(result) if isinstance(result, str) else result,
            model=record["model"],
            created_at=record["created_at"],
        )

    async def put(
        self,
        kind: str,
        version: str,
        prompt: str,
        result: dict[str, Any],
        model: str,
        cost_rub: Any = 0,
        now: datetime | None = None,
    ) -> None:
        await self.ensure()
        await self.connection.execute(
            PUT_SQL,
            kind,
            version,
            digest(prompt),
            json.dumps(result, ensure_ascii=False),
            model,
            cost_rub,
            now or datetime.now(UTC),
        )

    async def history(self, kind: str, prompt: str) -> list[Version]:
        """Все версии разбора одного входа: как менялся ответ при правке промпта."""
        await self.ensure()
        return [
            Version(
                version=record["version"],
                model=record["model"],
                result=json.loads(record["result"])
                if isinstance(record["result"], str)
                else record["result"],
                hits=record["hits"],
                created_at=record["created_at"],
            )
            for record in await self.connection.fetch(HISTORY_SQL, kind, digest(prompt))
        ]
