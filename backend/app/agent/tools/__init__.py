"""A5-02: JSON-протокол и проверяемый реестр инструментов."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from app.contracts.common import Id, Text

if TYPE_CHECKING:
    from app.agent.context import AnalysisContext

FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class Decision(BaseModel):
    """Ровно один вызов инструмента или завершение анализа."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"action": {"const": "tool"}}},
                    "then": {
                        "required": ["tool"],
                        "properties": {
                            "tool": {"type": "string"},
                            "output": {"type": "null"},
                        },
                    },
                    "else": {
                        "properties": {
                            "tool": {"type": "null"},
                            "arguments": {"maxProperties": 0},
                        }
                    },
                }
            ],
        },
    )
    action: Literal["tool", "finish"]
    reason: Text
    tool: Id | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    output: JsonValue = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.action == "tool" and (self.tool is None or self.output is not None):
            raise ValueError("Для шага нужен инструмент, без итогового ответа")
        if self.action == "finish" and (self.tool is not None or self.arguments):
            raise ValueError("Итог не вызывает инструмент")
        ensure_json({"arguments": self.arguments, "output": self.output})
        return self


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data: JsonValue = None
    source_ids: list[Id] = Field(default_factory=list)

    @model_validator(mode="after")
    def finite_json(self) -> Self:
        ensure_json(self.data)
        return self


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Id
    description: Text
    cacheable: bool = False
    arguments_schema: dict[str, JsonValue] = Field(default_factory=dict)
    result_schema: dict[str, JsonValue] = Field(default_factory=dict)

    def result_fields(self) -> list[str]:
        properties = self.result_schema.get("properties", {})
        return list(properties) if isinstance(properties, dict) else []


Handler = Callable[["AnalysisContext", dict[str, JsonValue]], Awaitable[Observation]]


class ToolRegistry:
    """Только явно зарегистрированные async-инструменты, без eval/динамических импортов."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Handler]] = {}
        self._schemas: dict[str, tuple[type[BaseModel] | None, type[BaseModel] | None]] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: Handler,
        *,
        arguments_model: type[BaseModel] | None = None,
        result_model: type[BaseModel] | None = None,
        cacheable: bool = False,
    ) -> None:
        for model in (arguments_model, result_model):
            if model is not None and model.model_config.get("extra") != "forbid":
                raise ValueError("Модель инструмента должна запрещать лишние поля")
        spec = ToolSpec(
            name=name,
            description=description,
            cacheable=cacheable,
            arguments_schema=arguments_model.model_json_schema()
            if arguments_model
            else {"type": "object"},
            result_schema=result_model.model_json_schema() if result_model else {},
        )
        if spec.name in self._tools:
            raise ValueError("Инструмент уже зарегистрирован")
        self._tools[spec.name] = (spec, handler)
        self._schemas[spec.name] = (arguments_model, result_model)

    def is_cacheable(self, name: str) -> bool:
        return self._tools[name][0].cacheable

    def specs(self) -> list[ToolSpec]:
        return [spec.model_copy(deep=True) for spec, _ in self._tools.values()]

    def handler(self, name: str) -> Handler:
        return self._tools[name][1]

    def snapshot(self) -> "ToolRegistry":
        registry = ToolRegistry()
        registry._tools = dict(self._tools)
        registry._schemas = dict(self._schemas)
        return registry

    def validate_arguments(
        self, name: str, arguments: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if name not in self._tools:
            raise ToolProtocolError(
                ToolError(code="unknown_tool", message="Инструмент отсутствует в реестре")
            )
        model, _ = self._schemas[name]
        try:
            ensure_json(arguments)
            if model is None:
                return arguments
            parsed = model.model_validate_json(json.dumps(arguments, allow_nan=False), strict=True)
        except (ValidationError, ValueError, TypeError) as error:
            raise ToolProtocolError(validation_error("invalid_arguments", error)) from None
        return parsed.model_dump(mode="json")

    def validate_result(self, name: str, observation: Observation) -> Observation:
        _, model = self._schemas[name]
        if not isinstance(observation, Observation):
            raise ToolProtocolError(
                ToolError(code="invalid_result", message="Неверный формат ответа инструмента")
            )
        try:
            ensure_json(observation.data)
            if model is not None:
                parsed = model.model_validate_json(
                    json.dumps(observation.data, allow_nan=False), strict=True
                )
                return observation.model_copy(
                    update={"data": parsed.model_dump(mode="json")}, deep=True
                )
        except (ValidationError, ValueError, TypeError) as error:
            raise ToolProtocolError(validation_error("invalid_result", error)) from None
        return observation.model_copy(deep=True)


def ensure_json(data: JsonValue) -> None:
    # JSON не допускает NaN и Infinity, в том числе во вложенных значениях.
    json.dumps(data, allow_nan=False)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: list[str | int]
    type: str


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: Literal[
        "invalid_decision",
        "unknown_tool",
        "invalid_arguments",
        "invalid_result",
        "tool_error",
        "tool_timeout",
    ]
    message: Text
    issues: list[ValidationIssue] = Field(default_factory=list)


class ToolProtocolError(Exception):
    def __init__(self, detail: ToolError) -> None:
        super().__init__(detail.message)
        self.detail = detail


def validation_error(
    code: Literal["invalid_decision", "invalid_arguments", "invalid_result"], error: Exception
) -> ToolError:
    issues = []
    if isinstance(error, ValidationError):
        issues = [
            ValidationIssue(path=list(e["loc"]), type=e["type"])
            for e in error.errors(include_input=False, include_context=False, include_url=False)[
                :20
            ]
        ]
    return ToolError(code=code, message="Ответ не соответствует JSON-схеме", issues=issues)


class ToolResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "ok"}}},
                    "then": {
                        "required": ["observation"],
                        "properties": {
                            "observation": {"type": "object"},
                            "error": {"type": "null"},
                            "error_detail": {"type": "null"},
                        },
                    },
                    "else": {
                        "required": ["error", "error_detail"],
                        "properties": {
                            "observation": {"type": "null"},
                            "error": {"type": "string"},
                            "error_detail": {"type": "object"},
                        },
                    },
                }
            ],
        },
    )
    status: Literal["ok", "error", "timeout"]
    observation: Observation | None = None
    error: str | None = None
    error_detail: ToolError | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "ok":
            if self.observation is None or self.error is not None or self.error_detail is not None:
                raise ValueError("Успешный ответ содержит наблюдение без ошибки")
        elif self.observation is not None or self.error is None or self.error_detail is None:
            raise ValueError("Неуспешный ответ содержит ошибку без наблюдения")
        return self


def unwrap(text: str) -> str:
    """Объект из ответа модели без обёртки ```json и текста вокруг: модели часто так
    отвечают, и строгий разбор отбрасывал оплаченный ответ вместе со всем прогоном."""
    stripped = text.strip()
    fenced = FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1)
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start : end + 1] if 0 <= start < end else stripped


def strict_json(text: str) -> JsonValue:
    """Повтор ключа мог бы спрятать замечание или подменить действие, NaN — не JSON."""

    def constant(value: str) -> None:
        raise ValueError("Нестандартное JSON-число")

    def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Повторяющийся JSON-ключ")
            result[key] = value
        return result

    data: JsonValue = json.loads(unwrap(text), parse_constant=constant, object_pairs_hook=unique)
    return data


def parse_decision(text: str) -> Decision:
    try:
        return Decision.model_validate(strict_json(text), strict=True)
    except (ValidationError, ValueError, TypeError) as error:
        raise ToolProtocolError(validation_error("invalid_decision", error)) from None
