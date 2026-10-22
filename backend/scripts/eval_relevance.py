"""Замер качества отбора (A4-06): эталонная разметка пар «компания — материал» и precision@5.

Три шага, методика — в eval/relevance.md:

    freeze  снимок из базы: профили компаний, материалы и момент времени. Тексты СМИ и
            аналитики кейса в снимок не попадают — только заголовок, лид, ссылка и разбор,
            по которым считается балл. Снимок делает замер воспроизводимым без базы и сети.
    pool    лист разметки: по каждой компании топ-10 ленты, топ-5 обычной ленты (свежие
            сверху) и случайные материалы. Пары перемешаны, баллов и мест в листе нет.
    score   precision@5 ленты и обычной ленты по разметке; --write обновляет таблицу в отчёте.

Рабочий режим с моделью:

    reclassify  копия снимка, где разбор новостей заменён разбором модели из базы (A3-04)
    score --llm лента с проверкой кандидатов моделью (A4-03); вердикты сохраняются в файл
                рядом со снимком, поэтому повторный замер идёт без ключа и без затрат

    uv run python scripts/eval_relevance.py score
    uv run python scripts/eval_relevance.py reclassify          # нужна база с разбором модели
    uv run python scripts/eval_relevance.py score --llm --snapshot snapshot_llm.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
from collections import Counter
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.api.feed import DEFAULT_DAYS, build_feed
from app.cache.store import Cached, digest
from app.contracts.feed import Feed
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
RESULTS = "results"
RECLASSIFY_SQL = "SELECT item FROM news_items WHERE news_id = ANY($1::text[])"


@dataclass
class Snapshot:
    now: datetime
    profiles: list[CompanyProfile]
    materials: list[NewsItem]

    @property
    def methods(self) -> dict[str, int]:
        """Кто разобрал новости снимка: llm — модель, rules — запасной словарь."""
        return dict(
            Counter(
                item.classification.method
                for item in self.materials
                if item.kind is MaterialKind.NEWS and item.classification
            )
        )


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


async def reclassify(source: Path, target: Path) -> None:
    """Копия снимка с разбором модели: для каждой новости, которую модель разобрала в базе,
    подставляется её классификация. Тексты, факты, профили и момент снимка не меняются, так
    что словарь и модель сравниваются на одних и тех же новостях."""
    import asyncpg

    from app.core.config import get_settings
    from app.core.db import postgres_dsn

    data = json.loads(source.read_text(encoding="utf-8"))
    snapshot = load_snapshot(source)
    ids = [item.id for item in snapshot.materials if item.kind is MaterialKind.NEWS]
    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        records = await connection.fetch(RECLASSIFY_SQL, ids)
    finally:
        await connection.close()
    found = {
        item.id: item.classification
        for item in (NewsItem.model_validate_json(record["item"]) for record in records)
        if item.classification is not None and item.classification.method == "llm"
    }
    materials = [
        item.model_copy(update={"classification": found[item.id]}) if item.id in found else item
        for item in snapshot.materials
    ]
    data["materials"] = [item.model_dump(mode="json") for item in materials]
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Разбор модели подставлен для {len(found)} из {len(ids)} новостей → {target}")


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


class JsonCache:
    """Ответы модели в файле рядом со снимком, с тем же интерфейсом, что у LLMCache: ключ —
    задача, версия промпта и хеш входа. Повторный замер берёт вердикты отсюда."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, dict[str, Any]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    @staticmethod
    def key(kind: str, version: str, prompt: str) -> str:
        return f"{kind}:{version}:{digest(prompt)}"

    async def get(
        self, kind: str, version: str, prompt: str, now: datetime | None = None
    ) -> Cached | None:
        found = self.data.get(self.key(kind, version, prompt))
        if found is None:
            return None
        return Cached(
            result=found["result"],
            model=found["model"],
            created_at=datetime.fromisoformat(found["created_at"]),
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
        self.data[self.key(kind, version, prompt)] = {
            "result": result,
            "model": model,
            "cost_rub": str(cost_rub),
            "created_at": (now or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        }
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def verdicts_path(snapshot: Path) -> Path:
    return snapshot.with_name(snapshot.stem + ".verdicts.json")


async def feed_of(
    profile: CompanyProfile, snapshot: Snapshot, top: int, llm: Any = None, cache: Any = None
) -> Feed:
    """Лента так, как её увидит клиент: правила, важность, сюжеты, разнообразие, а с llm —
    и проверка кандидатов моделью."""
    return await build_feed(
        profile, snapshot.materials, llm, now=snapshot.now, limit=top, cache=cache
    )


async def system_ranking(
    profile: CompanyProfile, snapshot: Snapshot, top: int, llm: Any = None, cache: Any = None
) -> list[str]:
    feed = await feed_of(profile, snapshot, top, llm, cache)
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


async def pool(
    snapshot: Snapshot,
    path: Path,
    system_top: int = SYSTEM_TOP,
    baseline_top: int = BASELINE_TOP,
    random_extra: int = RANDOM_EXTRA,
) -> None:
    by_id = {item.id: item for item in snapshot.materials}
    rows = []
    for profile in snapshot.profiles:
        chosen = list(
            dict.fromkeys(
                [
                    *await system_ranking(profile, snapshot, system_top),
                    *baseline_ranking(snapshot, baseline_top),
                ]
            )
        )
        rng = random.Random(f"{SEED}:{profile.company_id}")  # noqa: S311 — воспроизводимая выборка
        rest = sorted(item.id for item in window(snapshot) if item.id not in chosen)
        chosen += rng.sample(rest, min(random_extra, len(rest)))
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


async def score(
    snapshot: Snapshot, labels: dict[tuple[str, str], int], llm: Any = None, cache: Any = None
) -> str:
    baseline = baseline_ranking(snapshot, K)
    lines = [
        "| Компания | Лента P@5 | Лента P@5, только «прямо» | Точность показанных "
        "| Обычная лента P@5 | Потолок P@5 | Карточек в ленте |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    totals: list[tuple[float, float, float, float]] = []
    feeds: list[Feed] = []
    problems = []
    hits = shown = 0
    for profile in snapshot.profiles:
        company = profile.company_id
        feed = await feed_of(profile, snapshot, K, llm, cache)
        feeds.append(feed)
        ranking = [item.news_id for item in feed.items]
        system = precision_at(company, ranking, labels)
        plain = precision_at(company, baseline, labels)
        best = ceiling(company, labels)
        for news_id in [*system.unjudged, *plain.unjudged]:
            problems.append(f"{company} {news_id}")
        totals.append((system.value, system.strict, plain.value, best))
        relevant = sum(labels.get((company, news_id), 0) >= 1 for news_id in ranking)
        hits, shown = hits + relevant, shown + len(ranking)
        lines.append(
            f"| {company} | {percent(system.value)} | {percent(system.strict)} "
            f"| {relevant} из {len(ranking)} | {percent(plain.value)} | {percent(best)} "
            f"| {len(ranking)} |"
        )
    count = len(totals) or 1
    means = [sum(row[index] for row in totals) / count for index in range(4)]
    lines.append(
        f"| **Среднее** | **{percent(means[0])}** | {percent(means[1])} "
        f"| **{percent(hits / shown) if shown else '—'}** ({hits} из {shown}) "
        f"| {percent(means[2])} | {percent(means[3])} | |"
    )
    tops = [[item.news_id for item in feed.items] for feed in feeds]
    overlaps = [
        jaccard(tops[left], tops[right])
        for left in range(len(tops))
        for right in range(left + 1, len(tops))
    ]
    if overlaps:
        lines.append("")
        lines.append(
            f"Пересечение первых {K} карточек между компаниями (Жаккар), в среднем: "
            f"{percent(sum(overlaps) / len(overlaps))}; у обычной ленты — 100%."
        )
    cards = [item for feed in feeds for item in feed.items]
    levels = Counter(item.importance.title for item in cards)
    checked = sum(item.stage.value == "reranked" for item in cards)
    lines += [
        "",
        f"Размечено пар: {len(labels)}. Снимок: {snapshot.now.isoformat()}, "
        f"материалов: {len(snapshot.materials)}. Разбор новостей: "
        + ", ".join(f"{name} — {number}" for name, number in sorted(snapshot.methods.items()))
        + ".",
        "",
        f"В первых {K} карточках: проверено моделью {checked} из {len(cards)}; уровни важности — "
        + (", ".join(f"{title} {number}" for title, number in levels.most_common()) or "нет")
        + ".",
    ]
    if problems:
        lines.append("")
        lines.append(
            "Не размечены пары из первых 5, они считаются промахом: " + ", ".join(problems)
        )
    return "\n".join(lines)


def write_report(table: str, path: Path = REPORT, block: str = RESULTS) -> None:
    pattern = re.compile(rf"(<!-- {block}:start -->\n).*?(<!-- {block}:end -->)", re.DOTALL)
    text = path.read_text(encoding="utf-8")
    if not pattern.search(text):
        raise SystemExit(f"В {path} нет блока <!-- {block}:start --> … <!-- {block}:end -->")
    path.write_text(
        pattern.sub(lambda found: found.group(1) + table + "\n" + found.group(2), text),
        encoding="utf-8",
        newline="\n",
    )


async def with_model(use: bool, snapshot_path: Path, run: Any) -> Any:
    """Запускает run(llm, cache): с моделью и файлом вердиктов, если use, иначе без них. Без
    ключа модель не вызывается, но сохранённые вердикты всё равно берутся из файла."""
    if not use:
        return await run(None, None)
    from app.core.config import get_settings
    from app.llm.client import open_llm_client
    from app.llm.errors import LLMConfigurationError

    cache = JsonCache(verdicts_path(snapshot_path))
    async with AsyncExitStack() as stack:
        try:
            llm = await stack.enter_async_context(open_llm_client(get_settings()))
        except LLMConfigurationError:
            llm = None
        return await run(llm, cache)


def main(argv: Sequence[str] | None = None) -> Any:
    parser = argparse.ArgumentParser(description="Замер precision@5 отбора по эталонной разметке")
    parser.add_argument(
        "command", choices=["freeze", "reclassify", "pool", "score"], default="score", nargs="?"
    )
    parser.add_argument("--companies", nargs="+", default=list(COMPANIES))
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--labels", type=Path, default=LABELS)
    parser.add_argument("--out", type=Path, help="reclassify: куда записать снимок с моделью")
    parser.add_argument("--llm", action="store_true", help="проверка кандидатов моделью")
    parser.add_argument("--write", action="store_true", help="score: обновить таблицу в отчёте")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--block", default=RESULTS, help="score --write: имя блока в отчёте")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        asyncio.run(freeze(args.companies, SNAPSHOT))
    elif args.command == "reclassify":
        target = args.out or args.snapshot.with_name(args.snapshot.stem + "_llm.json")
        asyncio.run(reclassify(args.snapshot, target))
    elif args.command == "pool":
        asyncio.run(pool(load_snapshot(args.snapshot), args.labels))
    else:
        snapshot = load_snapshot(args.snapshot)
        labels = read_labels(args.labels)
        table = asyncio.run(
            with_model(
                args.llm, args.snapshot, lambda llm, cache: score(snapshot, labels, llm, cache)
            )
        )
        print(table)
        if args.write:
            write_report(table, args.report, args.block)


if __name__ == "__main__":
    main()
