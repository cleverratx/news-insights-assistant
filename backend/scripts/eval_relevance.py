"""Замер качества отбора (A4-06): эталонная разметка пар «компания — материал» и precision@5.

Три шага, методика — в eval/relevance.md:

    freeze  снимок из базы: профили компаний, материалы и момент времени. Тексты СМИ и
            аналитики кейса в снимок не попадают — только заголовок, лид, ссылка и разбор,
            по которым считается балл. Снимок делает замер воспроизводимым без базы и сети.
    pool    лист разметки: по каждой компании топ-10 ленты, топ-5 обычной ленты (свежие
            сверху) и случайные материалы. Пары перемешаны, баллов и мест в листе нет.
    score   precision@5 ленты и обычной ленты по разметке; --write обновляет таблицу в отчёте.

    uv run python scripts/eval_relevance.py score
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.api.feed import DEFAULT_DAYS, build_feed
from app.contracts.news import MaterialKind, NewsItem
from app.contracts.profile import CompanyProfile

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "eval" / "relevance"
SNAPSHOT = EVAL_DIR / "snapshot.json"
LABELS = EVAL_DIR / "labels.csv"
REPORT = ROOT / "eval" / "relevance.md"
COMPANIES = ("U_900001", "U_900002", "U_900003")
K = 5
SYSTEM_TOP = 10
BASELINE_TOP = 5
RANDOM_EXTRA = 8
SEED = 2026
LEAD = 300
FACT_TEXT = "Текст факта в снимок не входит"
FIELDS = (
    "company_id",
    "news_id",
    "kind",
    "source",
    "published",
    "title",
    "lead",
    "url",
    "label",
    "rationale",
)
RESULTS = re.compile(r"(<!-- results:start -->\n).*?(<!-- results:end -->)", re.DOTALL)


@dataclass
class Snapshot:
    now: datetime
    profiles: list[CompanyProfile]
    materials: list[NewsItem]


def lead(item: NewsItem) -> str:
    first = item.fragments[0].text if item.fragments else item.text
    return first if len(first) <= LEAD else first[:LEAD].rsplit(" ", 1)[0] + "…"


def sanitize(item: NewsItem) -> NewsItem:
    """Материал для снимка: всё, что нужно баллу, без текста источника. От фактов остаются
    статус и цифры — по ним считается масштаб события. У аналитики банка нет и лида: это
    материалы кейса."""
    facts = [
        fact.model_copy(
            update={"text": FACT_TEXT, "fragment_id": None, "who": [], "source_says": None}
        )
        for fact in item.key_facts
    ]
    if item.kind is MaterialKind.BANK_ANALYTICS:
        return item.model_copy(
            update={"text": item.title, "summary": None, "fragments": [], "key_facts": facts}
        )
    return item.model_copy(update={"text": lead(item), "fragments": [], "key_facts": facts})


def load_snapshot(path: Path = SNAPSHOT) -> Snapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(
        now=datetime.fromisoformat(data["now"]),
        profiles=[CompanyProfile.model_validate(item) for item in data["profiles"]],
        materials=[NewsItem.model_validate(item) for item in data["materials"]],
    )


async def freeze(companies: Sequence[str], path: Path) -> None:
    import asyncpg

    from app.api.companies import company_profile
    from app.core.config import get_settings
    from app.core.db import postgres_dsn
    from app.search.service import load

    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        now = datetime.now(UTC).replace(microsecond=0)
        profiles = [await company_profile(company, connection) for company in companies]
        items, warnings = await load(
            connection, {MaterialKind.BANK_ANALYTICS, MaterialKind.NEWS}, now
        )
    finally:
        await connection.close()
    if warnings:
        print("\n".join(warnings))
    snapshot = {
        "now": now.isoformat(),
        "profiles": [profile.model_dump(mode="json") for profile in profiles],
        "materials": [
            sanitize(item).model_dump(mode="json")
            for item in sorted(items, key=lambda item: item.id)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Снимок: {len(profiles)} компаний, {len(items)} материалов, момент {now.isoformat()}")


async def system_ranking(profile: CompanyProfile, snapshot: Snapshot, top: int) -> list[str]:
    """Лента так, как её увидит клиент без модели: правила, балл, сюжеты, разнообразие."""
    feed = await build_feed(profile, snapshot.materials, None, now=snapshot.now, limit=top)
    return [item.news_id for item in feed.items]


def window(snapshot: Snapshot) -> list[NewsItem]:
    since = snapshot.now - timedelta(days=DEFAULT_DAYS)
    return [
        item
        for item in snapshot.materials
        if item.kind is MaterialKind.BANK_ANALYTICS or item.published_at >= since
    ]


def baseline_ranking(snapshot: Snapshot, top: int) -> list[str]:
    """Обычная новостная лента: одна публикация на сюжет, свежие сверху, для всех одинаково."""
    seen: set[str] = set()
    ranking = []
    news = [item for item in window(snapshot) if item.kind is MaterialKind.NEWS]
    for item in sorted(news, key=lambda item: item.published_at, reverse=True):
        story = item.dedup.cluster_id or item.id
        if story not in seen:
            seen.add(story)
            ranking.append(item.id)
    return ranking[:top]


async def pool(snapshot: Snapshot, path: Path) -> None:
    by_id = {item.id: item for item in snapshot.materials}
    rows = []
    for profile in snapshot.profiles:
        chosen = list(
            dict.fromkeys(
                [
                    *await system_ranking(profile, snapshot, SYSTEM_TOP),
                    *baseline_ranking(snapshot, BASELINE_TOP),
                ]
            )
        )
        rng = random.Random(f"{SEED}:{profile.company_id}")  # noqa: S311 — воспроизводимая выборка
        rest = sorted(item.id for item in window(snapshot) if item.id not in chosen)
        chosen += rng.sample(rest, min(RANDOM_EXTRA, len(rest)))
        rng.shuffle(chosen)
        for news_id in chosen:
            item = by_id[news_id]
            rows.append(
                {
                    "company_id": profile.company_id,
                    "news_id": news_id,
                    "kind": item.kind.value,
                    "source": item.source.name,
                    "published": item.published_at.date().isoformat(),
                    "title": item.title,
                    "lead": "" if item.kind is MaterialKind.BANK_ANALYTICS else item.text,
                    "url": str(item.url or ""),
                    "label": "",
                    "rationale": "",
                }
            )
    write_labels(rows, path)
    print(f"Лист разметки: {len(rows)} пар → {path}")


def write_labels(rows: Sequence[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_labels(path: Path = LABELS) -> dict[tuple[str, str], int]:
    labels: dict[tuple[str, str], int] = {}
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if row["label"].strip():
                value = int(row["label"])
                if value not in (0, 1, 2):
                    raise ValueError(f"Оценка вне шкалы 0–2: {row['company_id']} {row['news_id']}")
                labels[(row["company_id"], row["news_id"])] = value
    return labels


@dataclass
class Precision:
    value: float
    strict: float
    unjudged: list[str]


def precision_at(
    company: str, ranking: Sequence[str], labels: dict[tuple[str, str], int], k: int = K
) -> Precision:
    """Доля релевантных в первых k. Если лента короче k, пустые места — не попадания: так
    метрика не награждает за то, что система показала мало."""
    top = list(ranking[:k])
    unjudged = [news_id for news_id in top if (company, news_id) not in labels]
    marks = [labels.get((company, news_id), 0) for news_id in top]
    return Precision(
        value=sum(mark >= 1 for mark in marks) / k,
        strict=sum(mark == 2 for mark in marks) / k,
        unjudged=unjudged,
    )


def ceiling(company: str, labels: dict[tuple[str, str], int], k: int = K) -> float:
    relevant = sum(1 for (owner, _), mark in labels.items() if owner == company and mark >= 1)
    return min(relevant, k) / k


def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    return len(a & b) / len(a | b) if a | b else 0.0


def percent(value: float) -> str:
    return f"{value * 100:.0f}%"


async def score(snapshot: Snapshot, labels: dict[tuple[str, str], int]) -> str:
    baseline = baseline_ranking(snapshot, K)
    lines = [
        "| Компания | Лента P@5 | Лента P@5, только «прямо» | Обычная лента P@5 | Потолок P@5 "
        "| Карточек в ленте |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    totals: list[tuple[float, float, float, float]] = []
    tops: dict[str, list[str]] = {}
    problems = []
    for profile in snapshot.profiles:
        company = profile.company_id
        ranking = await system_ranking(profile, snapshot, K)
        tops[company] = ranking
        system = precision_at(company, ranking, labels)
        plain = precision_at(company, baseline, labels)
        best = ceiling(company, labels)
        for news_id in [*system.unjudged, *plain.unjudged]:
            problems.append(f"{company} {news_id}")
        totals.append((system.value, system.strict, plain.value, best))
        lines.append(
            f"| {company} | {percent(system.value)} | {percent(system.strict)} "
            f"| {percent(plain.value)} | {percent(best)} | {len(ranking)} |"
        )
    count = len(totals) or 1
    means = [sum(row[index] for row in totals) / count for index in range(4)]
    lines.append(
        f"| **Среднее** | **{percent(means[0])}** | {percent(means[1])} "
        f"| {percent(means[2])} | {percent(means[3])} | |"
    )
    companies = list(tops)
    overlaps = [
        jaccard(tops[left], tops[right])
        for index, left in enumerate(companies)
        for right in companies[index + 1 :]
    ]
    if overlaps:
        lines.append("")
        lines.append(
            f"Пересечение первых {K} карточек между компаниями (Жаккар), в среднем: "
            f"{percent(sum(overlaps) / len(overlaps))}; у обычной ленты — 100%."
        )
    lines.append("")
    lines.append(
        f"Размечено пар: {len(labels)}. Снимок: {snapshot.now.isoformat()}, "
        f"материалов: {len(snapshot.materials)}."
    )
    if problems:
        lines.append("")
        lines.append(
            "Не размечены пары из первых 5, они считаются промахом: " + ", ".join(problems)
        )
    return "\n".join(lines)


def write_report(table: str, path: Path = REPORT) -> None:
    text = path.read_text(encoding="utf-8")
    if not RESULTS.search(text):
        raise SystemExit(f"В {path} нет блока <!-- results:start --> … <!-- results:end -->")
    path.write_text(
        RESULTS.sub(lambda found: found.group(1) + table + "\n" + found.group(2), text),
        encoding="utf-8",
        newline="\n",
    )


def main(argv: Sequence[str] | None = None) -> Any:
    parser = argparse.ArgumentParser(description="Замер precision@5 отбора по эталонной разметке")
    parser.add_argument("command", choices=["freeze", "pool", "score"], default="score", nargs="?")
    parser.add_argument("--companies", nargs="+", default=list(COMPANIES))
    parser.add_argument("--write", action="store_true", help="score: обновить таблицу в отчёте")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        asyncio.run(freeze(args.companies, SNAPSHOT))
    elif args.command == "pool":
        asyncio.run(pool(load_snapshot(), LABELS))
    else:
        table = asyncio.run(score(load_snapshot(), read_labels()))
        print(table)
        if args.write:
            write_report(table)


if __name__ == "__main__":
    main()
