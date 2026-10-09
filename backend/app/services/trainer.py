"""Тренажёр: начать сценарий, ответить, завершить с разбором."""

from __future__ import annotations

import json

from sqlalchemy import select

from backend.app.core.engine import get_verdict_engine
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import TrainerSession, iso_utc
from backend.app.services.planner import local_today
from backend.app.trainer.scenarios import SCENARIOS, get_scenario

MAX_USER_MESSAGES = 8


class TrainerError(Exception):
    pass


def scenarios() -> list[dict]:
    return [{"id": s.id, "title": s.title, "emoji": s.emoji, "description": s.description} for s in SCENARIOS]


def _to_dict(item: TrainerSession) -> dict:
    scenario = get_scenario(item.scenario)
    return {
        "id": item.id, "scenario": item.scenario, "title": scenario.title if scenario else item.scenario,
        "messages": json.loads(item.messages_json), "finished": item.finished, "immunity": item.immunity,
        "review": json.loads(item.review_json) if item.review_json else None,
    }


def start(user_id: int, user_name: str, scenario_id: str) -> dict:
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise TrainerError("Такого сценария нет.")
    with get_sessionmaker()() as session:
        repo.upsert_user(session, user_id, user_name)
        item = repo.create_trainer_session(session, user_id, scenario.id, [{"role": "bot", "text": scenario.opener}])
        return _to_dict(item)


def get(user_id: int, session_id: int) -> dict:
    with get_sessionmaker()() as session:
        return _to_dict(_load(session, session_id, user_id))


def _load(session, session_id: int, user_id: int) -> TrainerSession:
    item = session.get(TrainerSession, session_id)
    if item is None or item.user_id != user_id:
        raise TrainerError("Тренировка не найдена.")
    return item


async def reply(user_id: int, session_id: int, text: str) -> dict:
    text = text.strip()[:500]
    if not text:
        raise TrainerError("Напишите ответ.")
    with get_sessionmaker()() as session:
        item = _load(session, session_id, user_id)
        if item.finished:
            raise TrainerError("Эта тренировка уже завершена.")
        messages = json.loads(item.messages_json)
        scenario_id = item.scenario
    messages.append({"role": "user", "text": text})
    scenario = get_scenario(scenario_id)
    answer = await get_verdict_engine().trainer_reply(scenario, messages, user_id)
    messages.append({"role": "bot", "text": answer.message})
    user_count = sum(1 for m in messages if m["role"] == "user")
    finished = answer.finished or user_count >= MAX_USER_MESSAGES
    with get_sessionmaker()() as session:
        item = _load(session, session_id, user_id)
        item.messages_json = json.dumps(messages, ensure_ascii=False)
        session.commit()
    if finished:
        return await finish(user_id, session_id)
    with get_sessionmaker()() as session:
        return _to_dict(_load(session, session_id, user_id))


async def finish(user_id: int, session_id: int) -> dict:
    with get_sessionmaker()() as session:
        item = _load(session, session_id, user_id)
        if item.finished:
            return _to_dict(item)
        messages = json.loads(item.messages_json)
        scenario = get_scenario(item.scenario)
    review = await get_verdict_engine().trainer_review(scenario, messages, user_id)
    with get_sessionmaker()() as session:
        item = _load(session, session_id, user_id)
        item.finished = True
        item.immunity = review.immunity
        item.review_json = review.model_dump_json()
        user = repo.get_user(session, user_id)
        if user:
            repo.touch_streak(user, local_today())  # тренировка тоже продлевает серию дней
        session.commit()
        return _to_dict(item)


def history(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        rows = session.scalars(
            select(TrainerSession).where(TrainerSession.user_id == user_id).order_by(TrainerSession.created_at.desc()).limit(20)
        )
        return [{"id": r.id, "scenario": r.scenario, "finished": r.finished, "immunity": r.immunity,
                 "created_at": iso_utc(r.created_at)} for r in rows]
