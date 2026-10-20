import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from app.llm.client import open_llm_client
from app.llm.errors import LLMError
from app.llm.models import Message, Task


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка генерации LLM и учёта бюджета")
    parser.add_argument("--task", choices=list(Task), default=Task.CLASSIFY)
    parser.add_argument("--all-routes", action="store_true")
    args = parser.parse_args()
    tasks = [Task.CLASSIFY, Task.INTERPRET] if args.all_routes else [Task(args.task)]
    try:
        asyncio.run(check(tasks))
    except LLMError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
