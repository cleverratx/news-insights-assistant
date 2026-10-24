from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.profile import CompanyProfile, FeatureKind, ProfileFeature
from app.rank.roles import Role

QuestionKey = Literal["responsibilities", "decisions", "metrics", "horizon"]
QUESTIONS: dict[QuestionKey, tuple[str, str]] = {
    "responsibilities": (
        "За что вы отвечаете в компании?",
        "Поможет отделить ваши задачи от общей повестки компании.",
    ),
    "decisions": (
        "Какие решения вы принимаете чаще всего?",
        "В разборе будет больше внимания к решениям, которые зависят от вас.",
    ),
    "metrics": (
        "За какими показателями вы следите?",
        "Подсветим последствия событий для важных вам показателей.",
    ),
    "horizon": (
        "На какой срок вы обычно планируете?",
        "Поможет выбрать подходящий горизонт последствий и действий.",
    ),
}


class PositionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=1000)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PositionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str
    company_id: str
    title: str = Field(min_length=1, max_length=120)
    base_role: Role
    context: str = Field(min_length=1, max_length=2000)
    answers: dict[QuestionKey, PositionAnswer] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


SCHEMA = """
CREATE TABLE IF NOT EXISTS position_profiles (
    id text PRIMARY KEY,
    company_id text NOT NULL,
    version integer NOT NULL,
    item jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS position_profiles_company ON position_profiles (company_id);
"""


async def get(connection: Any, company: str, id_: str) -> PositionProfile | None:
    await connection.execute(SCHEMA)
    row = await connection.fetchrow(
        "SELECT item FROM position_profiles WHERE company_id = $1 AND id = $2", company, id_
    )
    return PositionProfile.model_validate_json(row["item"]) if row else None


async def listing(connection: Any, company: str) -> list[PositionProfile]:
    await connection.execute(SCHEMA)
    rows = await connection.fetch(
        "SELECT item FROM position_profiles WHERE company_id = $1 ORDER BY id", company
    )
    return [PositionProfile.model_validate_json(row["item"]) for row in rows]


async def create(connection: Any, position: PositionProfile, request_id: UUID) -> PositionProfile:
    position = position.model_copy(update={"id": f"position:{request_id.hex}"})
    await connection.execute(SCHEMA)
    await connection.execute(
        "INSERT INTO position_profiles (id, company_id, version, item) "
        "VALUES ($1, $2, 1, $3::jsonb) "
        "ON CONFLICT (id) DO NOTHING",
        position.id,
        position.company_id,
        position.model_dump_json(),
    )
    saved = await get(connection, position.company_id, position.id)
    if saved is None or saved.model_dump(
        include={"title", "base_role", "context"}
    ) != position.model_dump(include={"title", "base_role", "context"}):
        raise ValueError("Профиль уже создан с другими данными. Обновите страницу.")
    return saved


async def update(
    connection: Any, current: PositionProfile, changes: dict[str, Any]
) -> PositionProfile:
    updated = PositionProfile.model_validate(
        {
            **current.model_dump(),
            **changes,
            "version": current.version + 1,
            "updated_at": datetime.now(UTC),
        }
    )
    result = await connection.execute(
        "UPDATE position_profiles SET version = $3, item = $4::jsonb "
        "WHERE id = $1 AND company_id = $2 AND version = $5",
        current.id,
        current.company_id,
        updated.version,
        updated.model_dump_json(),
        current.version,
    )
    if result != "UPDATE 1":
        raise ValueError("Должность уже изменена. Обновите данные перед сохранением.")
    return updated


def apply(profile: CompanyProfile, position: PositionProfile) -> CompanyProfile:
    if position.company_id != profile.company_id:
        raise ValueError("Должность относится к другой компании")
    feature = ProfileFeature(
        id=f"feature:{position.id}",
        kind=FeatureKind.KNOWLEDGE,
        label=f"Должность читателя: {position.title}. Контекст: {position.context}. "
        + " ".join(
            f"{QUESTIONS[key][0]} {answer.text}" for key, answer in position.answers.items()
        ),
        value=position.model_dump_json(),
        source_fields=[position.id],
    )
    return profile.model_copy(update={"features": [feature, *profile.features]})


def from_profile(profile: CompanyProfile) -> PositionProfile | None:
    for feature in profile.features:
        if feature.id.startswith("feature:position:") and isinstance(feature.value, str):
            return PositionProfile.model_validate_json(feature.value)
    return None
