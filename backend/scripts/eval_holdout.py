"""Прогон на новостях, которых не было в разметке (A4-07): лента не пустая и не ломается.

Отложенный набор — свежий сбор RSS без единой публикации из эталонной разметки A4-06.
Лента строится для тех же компаний в нескольких сценариях: только новые новости, вместе
с аналитикой банка, модель недоступна, модель вернула мусор, модель всё подтвердила,
зашумлённые данные. В каждом сценарии проверяются инварианты ленты, а по первым пятёркам —
то же качество, что и в эталоне (precision@5 по слепой разметке). Отчёт — eval/holdout.md.

    freeze  отложенный набор из базы: свежие новости минус размеченные, профили — из эталона
    checks  инварианты ленты во всех сценариях; --write обновляет таблицу в отчёте
    pool    лист разметки первых пятёрок: топ-5 ленты, топ-5 обычной ленты, 5 случайных
    score   precision@5 по разметке; --write обновляет таблицу в отчёте
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import eval_relevance as reference

from app.api.feed import build_feed
from app.contracts.feed import Feed
from app.contracts.news import MaterialKind, NewsItem
from app.contracts.profile import CompanyProfile
from app.llm.errors import BudgetExceededError
from app.llm.models import AllowedModel, LLMResult, Message, PricingInfo, Task, Usage
from app.rank.diversity import MAX_RUN, event_key

HOLDOUT_DIR = reference.ROOT / "eval" / "holdout"
SNAPSHOT = HOLDOUT_DIR / "snapshot.json"
LABELS = HOLDOUT_DIR / "labels.csv"
REPORT = reference.ROOT / "eval" / "holdout.md"
LIMIT = 20
TOLERANCE = 0.02
KEYS = re.compile(r"^\[(n\d+)\]", re.MULTILINE)


async def freeze(path: Path = SNAPSHOT) -> None:
    import asyncpg

    from app.core.config import get_settings
    from app.core.db import postgres_dsn
    from app.search.service import load

    base = reference.load_snapshot()
    labeled = {news_id for _, news_id in reference.read_labels()}
    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        now = datetime.now(UTC).replace(microsecond=0)
        items, _ = await load(connection, {MaterialKind.NEWS}, now)
    finally:
        await connection.close()
    fresh = [item for item in items if item.id not in labeled]
    snapshot = {
        "now": now.isoformat(),
        "reference_now": base.now.isoformat(),
        "profiles": [profile.model_dump(mode="json") for profile in base.profiles],
        "materials": [
            reference.sanitize(item).model_dump(mode="json")
            for item in sorted(fresh, key=lambda item: item.id)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    newer = sum(item.published_at > base.now for item in fresh)
    print(
        f"Отложено: {len(fresh)} из {len(items)} новостей, вышли после эталона: {newer}, "
        f"момент {now.isoformat()}"
    )


def reference_now(path: Path = SNAPSHOT) -> datetime:
    return datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["reference_now"])


def answer(text: str) -> LLMResult:
    return LLMResult(
        text=text,
        model=AllowedModel.GLM,
        usage=Usage(prompt_tokens=0, completion_tokens=0),
        cost_rub=Decimal(0),
        pricing=PricingInfo(),
    )


class Echo:
    """Модель, которая подтверждает всех кандидатов: проверяет путь ленты после модели."""

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        verdicts = [
            {"id": key, "relevant": True, "score": 0.8, "reason": "Подтверждено тестовой моделью"}
            for key in KEYS.findall(messages[-1].content)
        ]
        return answer(json.dumps({"verdicts": verdicts}, ensure_ascii=False))


class Garbage:
    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        return answer("Извините, не могу ответить в формате JSON")


class Down:
    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        raise BudgetExceededError("Budget has been exceeded")


def noisy(items: Sequence[NewsItem], now: datetime) -> list[NewsItem]:
    """Зашумлённый поток: без разбора, без аннотации и ссылки, на английском, из будущего и
    повторы той же публикации под другим id."""
    result: list[NewsItem] = []
    for index, item in enumerate(items):
        case = index % 6
        if case == 0:
            result.append(item.model_copy(update={"classification": None}))
        elif case == 1:
            result.append(item.model_copy(update={"summary": None, "url": None, "image": None}))
        elif case == 2:
            result.append(
                item.model_copy(update={"language": "en", "text": "Company news in English."})
            )
        elif case == 3:
            result.append(item.model_copy(update={"published_at": now + timedelta(hours=3)}))
        elif case == 4:
            copy = item.model_copy(update={"id": f"{item.id}:copy"})
            result += [item, copy]
        else:
            result.append(item)
    return result


@dataclass
class Scenario:
    name: str
    materials: list[NewsItem]
    llm: Any = None
    verified: bool | None = False
    days: int = 7


@dataclass
class Outcome:
    scenario: str
    company: str
    items: int
    verified: bool
    violations: list[str] = field(default_factory=list)


def scenarios(snapshot: reference.Snapshot) -> list[Scenario]:
    news = list(snapshot.materials)
    bank = [
        item
        for item in reference.load_snapshot().materials
        if item.kind is MaterialKind.BANK_ANALYTICS
    ]
    return [
        Scenario("Только новые новости", news),
        Scenario("Новые новости и аналитика банка", [*news, *bank]),
        Scenario("Только за последние сутки", news, days=1),
        Scenario("Модель недоступна: бюджет исчерпан", news, Down()),
        Scenario("Модель вернула не JSON", news, Garbage()),
        Scenario("Модель подтвердила кандидатов", news, Echo(), verified=True),
        Scenario("Зашумлённые данные", noisy(news, snapshot.now)),
    ]


def inspect(
    feed: Feed, profile: CompanyProfile, materials: Sequence[NewsItem], expect_verified: bool | None
) -> list[str]:
    """Инварианты ленты. Пустой список — лента в порядке."""
    problems = []
    if not feed.items:
        problems.append("лента пустая")
    Feed.model_validate_json(feed.model_dump_json())
    by_id = {item.id: item for item in materials}
    known = {feature.id for feature in profile.features}
    if [item.rank for item in feed.items] != list(range(1, len(feed.items) + 1)):
        problems.append("места идут не подряд")
    ids = [item.news_id for item in feed.items]
    if len(set(ids)) != len(ids):
        problems.append("материал повторяется")
    stories = [item.story_id for item in feed.items if item.story_id]
    if len(set(stories)) != len(stories):
        problems.append("сюжет повторяется")
    if any(news_id not in by_id for news_id in ids):
        problems.append("карточка не из поданных материалов")
    if expect_verified is not None and feed.verified is not expect_verified:
        problems.append(f"verified={feed.verified}, ожидалось {expect_verified}")
    for item in feed.items:
        material = by_id.get(item.news_id)
        if material is not None and material.classification is None:
            problems.append(f"{item.news_id}: в ленте материал без разбора")
        if not item.score.relevance:
            problems.append(f"{item.news_id}: в ленте материал без совпадений с компанией")
        if not item.explanation.strip():
            problems.append(f"{item.news_id}: нет объяснения")
        if not item.features:
            problems.append(f"{item.news_id}: нет признаков компании")
        unknown = [found.feature_id for found in item.features if found.feature_id not in known]
        if unknown:
            problems.append(f"{item.news_id}: признаков нет в профиле: {', '.join(unknown)}")
        parts = [found.contribution for found in item.features if found.contribution is not None]
        if parts and abs(sum(parts) - item.score.final) > TOLERANCE:
            problems.append(f"{item.news_id}: вклады не сходятся с баллом")
        if not 0 <= item.score.final <= 1:
            problems.append(f"{item.news_id}: балл вне 0–1")
    events = [event_key(by_id[news_id]) for news_id in ids if news_id in by_id]
    for start in range(len(events) - MAX_RUN):
        run = events[start : start + MAX_RUN + 1]
        alternative = any(event != run[0] for event in events[start + MAX_RUN :])
        if len(set(run)) == 1 and alternative:
            problems.append(f"больше {MAX_RUN} подряд об одном событии: {run[0]}")
            break
    return problems


async def run_checks(snapshot: reference.Snapshot) -> list[Outcome]:
    outcomes = []
    for scenario in scenarios(snapshot):
        for profile in snapshot.profiles:
            company = profile.company_id
            try:
                feed = await build_feed(
                    profile,
                    scenario.materials,
                    scenario.llm,
                    now=snapshot.now,
                    days=scenario.days,
                    limit=LIMIT,
                )
            except Exception as error:
                outcomes.append(Outcome(scenario.name, company, 0, False, [f"сбой: {error!r}"]))
                continue
            problems = inspect(feed, profile, scenario.materials, scenario.verified)
            outcomes.append(
                Outcome(scenario.name, company, len(feed.items), feed.verified, problems)
            )
    return outcomes


def checks_table(snapshot: reference.Snapshot, outcomes: Sequence[Outcome], newer: int) -> str:
    companies = [profile.company_id for profile in snapshot.profiles]
    lines = [
        "| Сценарий | " + " | ".join(companies) + " | Нарушения |",
        "| --- | " + " | ".join("---" for _ in companies) + " | --- |",
    ]
    for name in dict.fromkeys(outcome.scenario for outcome in outcomes):
        row = [outcome for outcome in outcomes if outcome.scenario == name]
        cells = [f"{outcome.items}" + (" ✓ модель" if outcome.verified else "") for outcome in row]
        problems = [f"{outcome.company}: {p}" for outcome in row for p in outcome.violations]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {'; '.join(problems) or 'нет'} |")
    total = sum(len(outcome.violations) for outcome in outcomes)
    lines += [
        "",
        f"В ячейках — число карточек в ленте (до {LIMIT}). Прогонов: {len(outcomes)}, "
        f"нарушений: {total}.",
        "",
        f"Отложенный набор: {len(snapshot.materials)} новостей, ни одной из эталонной разметки; "
        f"вышли после эталонного снимка: {newer}. Снимок: {snapshot.now.isoformat()}.",
    ]
    return "\n".join(lines)


async def checks(write: bool, path: Path = SNAPSHOT, block: str = "checks") -> int:
    snapshot = reference.load_snapshot(path)
    outcomes = await run_checks(snapshot)
    newer = sum(item.published_at > reference_now(path) for item in snapshot.materials)
    table = checks_table(snapshot, outcomes, newer)
    print(table)
    if write:
        reference.write_report(table, REPORT, block)
    return sum(len(outcome.violations) for outcome in outcomes)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Прогон ленты на новостях вне разметки")
    parser.add_argument("command", choices=["freeze", "checks", "pool", "score"])
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--llm", action="store_true", help="score: проверка кандидатов моделью")
    parser.add_argument("--write", action="store_true", help="обновить таблицу в отчёте")
    parser.add_argument("--block", help="имя блока в отчёте, по умолчанию checks или results")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        asyncio.run(freeze())
    elif args.command == "checks":
        if asyncio.run(checks(args.write, args.snapshot, args.block or "checks")):
            raise SystemExit(1)
    elif args.command == "pool":
        asyncio.run(
            reference.pool(
                reference.load_snapshot(args.snapshot),
                LABELS,
                system_top=reference.K,
                baseline_top=reference.K,
                random_extra=reference.K,
            )
        )
    else:
        snapshot = reference.load_snapshot(args.snapshot)
        labels = reference.read_labels(LABELS)
        table = asyncio.run(
            reference.with_model(
                args.llm,
                args.snapshot,
                lambda llm, cache: reference.score(snapshot, labels, llm, cache),
            )
        )
        print(table)
        if args.write:
            reference.write_report(table, REPORT, args.block or reference.RESULTS)


if __name__ == "__main__":
    main()
