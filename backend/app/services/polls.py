"""Анонимные опросы: «Опрос после пары», «Пульс МУИТ», «Пульс группы».

Анонимность настоящая:
  - в ответе нет id пользователя, только хеш (services/anon.py) — чтобы нельзя было ответить дважды;
  - время ответа хранится только датой;
  - результаты скрыты, пока ответов меньше пяти (ANON_MIN_ANSWERS), — иначе в маленькой
    группе автора легко вычислить; свободные ответы показываются вперемешку.
Оцениваем предметы, процессы и сервисы, а не людей.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from datetime import date
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.app.db.base import get_sessionmaker
from backend.app.db.models import iso_utc
from backend.app.db.models_campus import Poll, PollAnswer
from backend.app.i18n import t
from backend.app.services import anon
from backend.app.university.reference import load_json

STOPWORDS = set("и в во на не что как это по к ко с со у о об от до за из для то же ли бы а но или да нет мне меня я мы вы он она они "
                "было был была были есть очень всё все так там тут где когда почему про при без над под тема темы".split())


class PollError(Exception):
    pass


@lru_cache
def pulse_questions() -> list[dict]:
    return load_json("pulse.json")["questions"]


def week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _dict(poll: Poll, count: int, mine: bool, with_results: dict | None = None) -> dict:
    data = {"id": poll.id, "kind": poll.kind, "question": poll.question, "options": json.loads(poll.options_json or "[]"),
            "subject": poll.subject, "open": poll.open, "week": poll.week_key, "answers": count, "answered": mine,
            "min_answers": anon.min_answers(), "visible": anon.visible(count), "created_at": iso_utc(poll.created_at),
            "hub_id": poll.hub_id, "circle_id": poll.circle_id}
    if with_results is not None:
        data["results"] = with_results
    return data


def _count(session, poll_id: int) -> int:
    return session.scalar(select(func.count()).select_from(PollAnswer).where(PollAnswer.poll_id == poll_id)) or 0


def create(kind: str, question: str, owner_id: int | None = None, options: list[str] | None = None, hub_id: int | None = None,
           circle_id: int | None = None, chat_id: int | None = None, subject: str = "", week: str = "") -> dict:
    question = " ".join((question or "").split())[:512]
    if len(question) < 3:
        raise PollError(t("cm.poll.err.question"))
    options = [o.strip()[:80] for o in (options or []) if o.strip()][:6]
    with get_sessionmaker()() as session:
        poll = Poll(kind=kind, question=question, owner_id=owner_id, options_json=json.dumps(options, ensure_ascii=False),
                    hub_id=hub_id, circle_id=circle_id, chat_id=chat_id, subject=subject[:64], week_key=week)
        session.add(poll)
        session.commit()
        return _dict(poll, 0, False)


def get(poll_id: int, user_id: int | None = None) -> dict:
    with get_sessionmaker()() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            raise PollError(t("cm.poll.err.not_found"))
        mine = bool(user_id and session.scalar(select(PollAnswer.id).where(PollAnswer.poll_id == poll_id,
                                                                           PollAnswer.voter_hash == anon.voter_hash(f"poll:{poll_id}", user_id))))
        count = _count(session, poll_id)
        results = results_for(session, poll) if anon.visible(count) and (poll.kind != "class" or poll.owner_id == user_id) else None
        return _dict(poll, count, mine, results)


def answer(poll_id: int, user_id: int, option: int | None = None, text: str = "") -> dict:
    """Ответить анонимно. Повторно нельзя. Кризисные сигналы — вернём флаг, чтобы показать контакты помощи."""
    from backend.app.university import crisis

    text = " ".join((text or "").split())[:1000]
    with get_sessionmaker()() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            raise PollError(t("cm.poll.err.not_found"))
        if not poll.open:
            raise PollError(t("cm.poll.err.closed"))
        options = json.loads(poll.options_json or "[]")
        if options:
            if option is None or not 0 <= option < len(options):
                raise PollError(t("cm.poll.err.option"))
            text = ""
        elif len(text) < 2:
            raise PollError(t("cm.poll.err.text"))
        session.add(PollAnswer(poll_id=poll_id, voter_hash=anon.voter_hash(f"poll:{poll_id}", user_id), option=option if options else None,
                               text=text, day=date.today().isoformat()))
        try:
            session.commit()
        except IntegrityError as exc:
            raise PollError(t("cm.poll.err.already")) from exc
    result = get(poll_id, user_id)
    result["crisis"] = crisis.detect(text) if text else ""
    return result


def results_for(session, poll: Poll) -> dict:
    rows = list(session.scalars(select(PollAnswer).where(PollAnswer.poll_id == poll.id)))
    options = json.loads(poll.options_json or "[]")
    if options:
        counts = Counter(r.option for r in rows)
        total = len(rows) or 1
        return {"options": [{"text": o, "count": counts.get(i, 0), "percent": round(100 * counts.get(i, 0) / total)} for i, o in enumerate(options)],
                "average": round(sum((r.option or 0) + 1 for r in rows) / total, 2) if poll.kind == "group_pulse" else None}
    texts = [r.text for r in rows if r.text]
    random.shuffle(texts)  # порядок ответов не должен выдавать, кто ответил первым
    words = Counter(w for txt in texts for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", txt.lower()) if w not in STOPWORDS)
    return {"topics": [{"word": w, "count": c} for w, c in words.most_common(8) if c >= 2], "texts": texts[:100]}


def results(poll_id: int, user_id: int) -> dict:
    """Итоги — автору опроса (после пары) или всем (Пульс). Пока ответов меньше пяти — только число ответов."""
    with get_sessionmaker()() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            raise PollError(t("cm.poll.err.not_found"))
        if poll.kind == "class" and poll.owner_id != user_id:
            raise PollError(t("cm.poll.err.owner"))
        count = _count(session, poll_id)
        if not anon.visible(count):
            return _dict(poll, count, False)
        return _dict(poll, count, False, results_for(session, poll))


def close(poll_id: int, user_id: int) -> dict:
    with get_sessionmaker()() as session:
        poll = session.get(Poll, poll_id)
        if poll is None or poll.owner_id != user_id:
            raise PollError(t("cm.poll.err.owner"))
        poll.open = False
        session.commit()
    return results(poll_id, user_id)


def my_polls(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        polls = list(session.scalars(select(Poll).where(Poll.owner_id == user_id, Poll.kind == "class").order_by(Poll.created_at.desc()).limit(30)))
        return [_dict(p, _count(session, p.id), False) for p in polls]


# ------------------------------------------------------------------ Пульс МУИТ


def current_pulse(today: date | None = None, user_id: int | None = None) -> dict:
    """Вопрос недели. Создаётся сам при первом обращении на этой неделе; прошлая неделя закрывается."""
    today = today or date.today()
    week = week_key(today)
    questions = pulse_questions()
    q = questions[today.isocalendar().week % len(questions)]
    with get_sessionmaker()() as session:
        poll = session.scalar(select(Poll).where(Poll.kind == "pulse", Poll.week_key == week))
        if poll is None:
            for old in session.scalars(select(Poll).where(Poll.kind == "pulse", Poll.open.is_(True))):
                old.open = False
            poll = Poll(kind="pulse", question=q["text"], options_json=json.dumps(q["options"], ensure_ascii=False), week_key=week, subject=q["topic"])
            session.add(poll)
            session.commit()
        poll_id = poll.id
    data = get(poll_id, user_id)
    previous = None
    with get_sessionmaker()() as session:
        last = session.scalar(select(Poll).where(Poll.kind == "pulse", Poll.week_key < week).order_by(Poll.week_key.desc()))
        if last is not None:
            count = _count(session, last.id)
            previous = _dict(last, count, False, results_for(session, last) if anon.visible(count) else None)
    data["previous"] = previous
    return data


# ------------------------------------------------------------------ Пульс группы (нагрузка по предмету 1–5)

LOAD_OPTIONS = ["1 — легко", "2", "3", "4", "5 — очень тяжело"]


def group_pulse(hub_id: int, subject: str, today: date | None = None, create_if_missing: bool = True) -> dict | None:
    today = today or date.today()
    week = week_key(today)
    with get_sessionmaker()() as session:
        poll = session.scalar(select(Poll).where(Poll.kind == "group_pulse", Poll.hub_id == hub_id, Poll.week_key == week))
        if poll is None and create_if_missing:
            poll = Poll(kind="group_pulse", hub_id=hub_id, question=t("cm.gpulse.question", subject=subject),
                        options_json=json.dumps(LOAD_OPTIONS, ensure_ascii=False), week_key=week, subject=subject[:64])
            session.add(poll)
            session.commit()
        if poll is None:
            return None
        poll_id = poll.id
    return poll_id and get(poll_id)


def group_pulse_history(hub_id: int, weeks: int = 8) -> list[dict]:
    """Динамика нагрузки по неделям. Недели, где ответов меньше пяти, — без цифр."""
    out = []
    with get_sessionmaker()() as session:
        polls = list(session.scalars(select(Poll).where(Poll.kind == "group_pulse", Poll.hub_id == hub_id).order_by(Poll.week_key.desc()).limit(weeks)))
        for poll in reversed(polls):
            count = _count(session, poll.id)
            avg = results_for(session, poll)["average"] if anon.visible(count) else None
            out.append({"week": poll.week_key, "answers": count, "average": avg})
    return out
