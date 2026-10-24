from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from app.api.companies import CompanyId, Connection, company_profile
from app.contracts.profile import CompanyProfile
from app.profile import positions
from app.profile.positions import PositionProfile, QuestionKey
from app.rank.roles import Role

router = APIRouter(prefix="/companies", tags=["positions"])
PositionId = Annotated[str, Path(pattern=r"^position:[0-9a-f]{32}$")]


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    title: str = Field(min_length=1, max_length=120)
    base_role: Role
    context: str = Field(min_length=1, max_length=2000)


class PositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    base_role: Role
    context: str = Field(min_length=1, max_length=2000)


class PositionQuestion(BaseModel):
    key: QuestionKey
    question: str
    why: str


class PositionAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1000)


async def required(connection: Connection, company: str, position_id: str) -> PositionProfile:
    position = await positions.get(connection, company, position_id)
    if position is None:
        raise HTTPException(404, "Должность не найдена в этой компании")
    return position


async def personalize(
    profile: CompanyProfile, connection: Connection, id_: str | None, role: Role | None
) -> CompanyProfile:
    if id_ is None:
        return profile
    position = await required(connection, profile.company_id, id_)
    if role != position.base_role:
        raise HTTPException(
            422, "Основа подбора не соответствует должности. Выберите должность заново."
        )
    return positions.apply(profile, position)


@router.get("/{company_id}/positions", response_model=list[PositionProfile])
async def list_positions(company_id: CompanyId, connection: Connection) -> list[PositionProfile]:
    """Должности компании с отдельным контекстом и уточнениями."""
    await company_profile(company_id, connection)
    return await positions.listing(connection, company_id)


@router.post("/{company_id}/positions", response_model=PositionProfile, status_code=201)
async def create_position(
    company_id: CompanyId, body: PositionCreate, connection: Connection
) -> PositionProfile:
    """Создание должности; повтор запроса с тем же request_id не создаёт копию."""
    await company_profile(company_id, connection)
    position = PositionProfile(
        id="new", company_id=company_id, **body.model_dump(exclude={"request_id"})
    )
    try:
        return await positions.create(connection, position, body.request_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/{company_id}/positions/{position_id}", response_model=PositionProfile)
async def get_position(
    company_id: CompanyId, position_id: PositionId, connection: Connection
) -> PositionProfile:
    """Контекст выбранной должности."""
    return await required(connection, company_id, position_id)


@router.patch("/{company_id}/positions/{position_id}", response_model=PositionProfile)
async def edit_position(
    company_id: CompanyId, position_id: PositionId, body: PositionUpdate, connection: Connection
) -> PositionProfile:
    """Правка контекста с защитой от параллельного изменения."""
    current = await required(connection, company_id, position_id)
    if current.version != body.expected_version:
        raise HTTPException(409, "Должность уже изменена. Обновите данные.")
    try:
        return await positions.update(
            connection, current, body.model_dump(exclude={"expected_version"})
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.get(
    "/{company_id}/positions/{position_id}/questions", response_model=list[PositionQuestion]
)
async def position_questions(
    company_id: CompanyId, position_id: PositionId, connection: Connection
) -> list[PositionQuestion]:
    """Вопросы об обязанностях и решениях выбранной должности."""
    current = await required(connection, company_id, position_id)
    return [
        PositionQuestion(key=key, question=question, why=why)
        for key, (question, why) in positions.QUESTIONS.items()
        if key not in current.answers
    ]


@router.post(
    "/{company_id}/positions/{position_id}/questions/{key}/answer", response_model=PositionProfile
)
async def answer_position(
    company_id: CompanyId,
    position_id: PositionId,
    key: QuestionKey,
    body: PositionAnswerRequest,
    connection: Connection,
) -> PositionProfile:
    """Ответ сразу становится подтверждённой частью контекста должности."""
    current = await required(connection, company_id, position_id)
    if current.version != body.expected_version:
        raise HTTPException(409, "Должность уже изменена. Обновите данные.")
    answers = {**current.answers, key: positions.PositionAnswer(text=body.text)}
    try:
        return await positions.update(connection, current, {"answers": answers})
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
