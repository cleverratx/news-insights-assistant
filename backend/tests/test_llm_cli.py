import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from app.llm.__main__ import check
from app.llm.models import AllowedModel, LLMResult, Message, PricingInfo, Task, Usage


async def test_cli_prints_usage_without_generated_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Task] = []

    class FakeClient:
        async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
            calls.append(task)
            return LLMResult(
                text="private generated content",
                model=AllowedModel.DEEPSEEK,
                usage=Usage(prompt_tokens=10, completion_tokens=5),
                cost_rub=Decimal("0.01"),
                pricing=PricingInfo(),
            )

    @asynccontextmanager
    async def fake_open() -> AsyncIterator[FakeClient]:
        yield FakeClient()

    monkeypatch.setattr("app.llm.__main__.open_llm_client", fake_open)
    await check([Task.CLASSIFY, Task.INTERPRET])
    output = capsys.readouterr().out
    assert "private generated content" not in output
    results = [json.loads(line) for line in output.splitlines()]
    assert calls == [Task.CLASSIFY, Task.INTERPRET]
    assert [row["task"] for row in results] == ["classify", "interpret"]
    assert all(row["response_received"] for row in results)
    assert results[0]["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
