from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.agent.digest import context_text
from app.agent.roles import lens_for
from app.api import positions as api
from app.api.companies import get_connection
from app.main import create_app
from app.profile import positions
from app.profile.positions import PositionProfile
from app.rank.roles import Role
from app.rank.snapshots import digest
from tests.test_rank_rules import profile


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, PositionProfile] = {}

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.startswith("INSERT INTO position_profiles"):
            self.rows.setdefault(args[0], PositionProfile.model_validate_json(args[2]))
        if sql.startswith("UPDATE position_profiles"):
            current = self.rows.get(args[0])
            if current is None or current.company_id != args[1] or current.version != args[4]:
                return "UPDATE 0"
            self.rows[args[0]] = PositionProfile.model_validate_json(args[3])
            return "UPDATE 1"
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, str] | None:
        found = self.rows.get(args[1])
        if found is None or found.company_id != args[0]:
            return None
        return {"item": found.model_dump_json()}

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        return [
            {"item": p.model_dump_json()} for p in self.rows.values() if p.company_id == args[0]
        ]


def position() -> PositionProfile:
    return PositionProfile(
        id="position:" + "a" * 32,
        company_id=profile().company_id,
        title="Директор по закупкам",
        base_role=Role.LOGISTICS,
        context="Отвечаю за стоимость сырья и непрерывность поставок",
    )


async def test_create_retry_is_idempotent_and_company_isolation() -> None:
    db = Store()
    request_id = uuid4()
    first = await positions.create(db, position(), request_id)
    again = await positions.create(db, position(), request_id)
    assert first == again
    assert len(db.rows) == 1
    assert await positions.get(db, "other", first.id) is None
    with pytest.raises(ValueError, match="другими данными"):
        await positions.create(
            db, position().model_copy(update={"context": "Другой контекст"}), request_id
        )


async def test_concurrent_updates_do_not_overwrite_each_other() -> None:
    db = Store()
    first = await positions.create(db, position(), uuid4())
    updated = await positions.update(db, first, {"context": "Новый контекст"})
    assert updated.version == 2
    with pytest.raises(ValueError, match="уже изменена"):
        await positions.update(db, first, {"context": "Устаревшая правка"})
    assert (await positions.get(db, first.company_id, first.id)) == updated


def test_context_is_scoped_changes_cache_and_personalizes_role_view() -> None:
    company = profile()
    custom = positions.apply(company, position())
    assert not any(f.id.startswith("feature:position:") for f in company.features)
    assert positions.from_profile(company) is None
    assert positions.from_profile(custom) == position()
    assert digest(company) != digest(custom)
    lens = lens_for(Role.LOGISTICS, custom)
    assert lens.title == "Директор по закупкам"
    assert "стоимость сырья" in lens.focus
    assert "стоимость сырья" in context_text(custom, Role.LOGISTICS, None)
    assert lens_for(Role.LOGISTICS).title != lens.title
    with pytest.raises(ValueError, match="другой компании"):
        positions.apply(company, position().model_copy(update={"company_id": "other"}))


def test_empty_context_and_unknown_answer_slot_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PositionProfile.model_validate({**position().model_dump(), "context": "   "})
    with pytest.raises(ValidationError):
        PositionProfile.model_validate(
            {**position().model_dump(), "answers": {"unknown": {"text": "x"}}}
        )


async def test_api_creation_answer_edit_and_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Store()
    app = create_app()

    async def connection() -> Any:
        return db

    async def company(company_id: str, connection: Any) -> Any:
        return profile().model_copy(update={"company_id": company_id})

    app.dependency_overrides[get_connection] = connection
    monkeypatch.setattr(api, "company_profile", company)
    base = "/api/companies/one/positions"
    body = {
        "request_id": str(uuid4()),
        "title": "Директор по закупкам",
        "base_role": "logistics",
        "context": "Сырьё и поставки",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(base, json=body)
        assert response.status_code == 201
        saved = response.json()
        path = f"{base}/{saved['id']}"
        assert (await client.post(base, json=body)).json()["id"] == saved["id"]
        assert (await client.get(path.replace("/one/", "/other/"))).status_code == 404
        assert len((await client.get(f"{path}/questions")).json()) == 4
        answer = await client.post(
            f"{path}/questions/horizon/answer", json={"expected_version": 1, "text": "Квартал"}
        )
        assert answer.status_code == 200
        assert answer.json()["answers"]["horizon"]["text"] == "Квартал"
        assert len((await client.get(f"{path}/questions")).json()) == 3
        conflict = await client.patch(
            path,
            json={
                "expected_version": 1,
                "title": "Закупки",
                "base_role": "ceo",
                "context": "Новая задача",
            },
        )
        assert conflict.status_code == 409
        assert (await client.get(path)).json()["version"] == 2
        assert (await client.post(base, json={**body, "context": " "})).status_code == 422
    with pytest.raises(HTTPException) as mismatch:
        await api.personalize(
            profile().model_copy(update={"company_id": "one"}), db, saved["id"], Role.CFO
        )
    assert mismatch.value.status_code == 422
