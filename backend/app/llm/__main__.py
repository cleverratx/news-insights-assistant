import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.llm.client import open_llm_client
from app.llm.errors import LLMError
from app.llm.models import Message, Task
from app.llm.usage import open_budget, report


async def check(tasks: Sequence[Task]) -> None:
    async with open_llm_client() as client:
        for task in tasks:
            result = await client.complete(
                task,
                [Message(role="user", content="Напиши одно слово: готово.")],
            )
            print(
                json.dumps(
                    {
                        "task": task.value,
                        "model": result.model.value,
                        "response_received": bool(result.text.strip()),
                        "usage": result.usage.model_dump(),
                        "cost_rub": str(result.cost_rub),
                        "pricing": result.pricing.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
            )


async def show_usage() -> None:
    settings = get_settings()
    async with open_budget(settings) as budget:
        print((await report(settings, budget)).model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка генерации LLM и учёта бюджета")
    parser.add_argument("--task", choices=list(Task), default=Task.CLASSIFY)
    parser.add_argument("--all-routes", action="store_true")
    parser.add_argument(
        "--usage", action="store_true", help="расход за текущие сутки без вызова LLM"
    )
    args = parser.parse_args()
    tasks = [Task.CLASSIFY, Task.INTERPRET] if args.all_routes else [Task(args.task)]
    try:
        asyncio.run(show_usage() if args.usage else check(tasks))
    except LLMError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
