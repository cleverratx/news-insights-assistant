"""Пайплайн новостей (A3-09): сбор → нормализация → сюжеты → разбор → факты → сущности
одним запуском.

Отказоустойчивость на трёх уровнях:

- материал: битая кодировка чинится, пустая страница и платный доступ откатываются к анонсу
  из ленты, нет даты — берётся время сбора; всё это пишется в news_issues и в лог;
- запись: ошибка обработки одной записи пишется в news_issues, остальные обрабатываются;
  запись, упавшая трижды, больше не берётся в работу и не тормозит очередь;
- этап: если этап упал целиком, ошибка пишется в лог и в news_issues, а следующие этапы
  всё равно запускаются — они работают с тем, что уже есть в базе.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.news.classify import Completer, classify_pending
from app.news.dedup import dedup_pending
from app.news.entities import index as index_entities
from app.news.extract import extract_pending
from app.news.fetch import Fetcher, Source, run_once
from app.news.issues import ensure_schema, failure, record
from app.news.normalize import normalize_pending

ISSUES_SQL = """
SELECT stage, kind, count(*) AS count FROM news_issues
WHERE created_at >= $1
GROUP BY stage, kind
ORDER BY stage, kind
"""

ENTITY_WINDOW = timedelta(days=14)

logger = logging.getLogger(__name__)

Step = Callable[[], Awaitable[Any]]


@dataclass
class PipelineReport:
    started_at: datetime
    stages: dict[str, Any] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    issues: dict[str, dict[str, int]] = field(default_factory=dict)


def summary(result: Any) -> Any:
    return asdict(result) if is_dataclass(result) and not isinstance(result, type) else result


async def run_pipeline(
    connection: Any,
    fetcher: Fetcher | None,
    llm: Completer | None,
    sources: Sequence[Source],
    *,
    now: datetime | None = None,
) -> PipelineReport:
    """Один проход по всем этапам. fetcher=None — без сбора: обработать уже собранное."""
    moment = now or datetime.now(UTC)
    await ensure_schema(connection)
    report = PipelineReport(started_at=moment)
    steps: list[tuple[str, Step]] = []
    if fetcher is not None:
        steps.append(("fetch", lambda: run_once(connection, fetcher, sources, moment)))
    steps += [
        ("normalize", lambda: normalize_pending(connection, sources, now=moment)),
        ("dedup", lambda: dedup_pending(connection, now=moment)),
        ("classify", lambda: classify_pending(connection, llm, now=moment)),
        ("extract", lambda: extract_pending(connection, llm, sources, now=moment)),
        ("entities", lambda: index_entities(connection, moment - ENTITY_WINDOW)),
    ]
    for name, step in steps:
        try:
            report.stages[name] = summary(await step())
        except Exception as error:
            logger.exception("Этап %s упал, следующие этапы продолжают работу", name)
            report.failed.append(name)
            report.stages[name] = {"error": failure(error).detail}
            try:
                await record(connection, name, failure(error), now=moment)
            except Exception:
                logger.exception("Не удалось записать сбой этапа %s в news_issues", name)
    try:
        for row in await connection.fetch(ISSUES_SQL, moment):
            report.issues.setdefault(row["stage"], {})[row["kind"]] = row["count"]
    except Exception:
        logger.exception("Не удалось собрать сводку проблем")
    return report
