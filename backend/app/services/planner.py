"""Планер и трекер задач «Мой план».

Идея: задачи приходят сами из того, что бот уже знает (договорённости, силлабус,
академкалендарь, хабы, афиша, консультации, чек-лист курса), но ВСЕГДА с
подтверждением — это «предложения» (task_suggestions). Молча в план ничего
не добавляется.

Правила:
  - личные задачи видит только владелец; они не участвуют в рейтингах,
    сводках для преподавателей и статистике;
  - за пропущенные задачи бот не стыдит — предлагает перенести;
  - напоминания выключены, пока человек их не включит; есть тихие часы.

Виды: Сегодня, Неделя, Семестр (лента с вехами календаря), Доска (Надо / Делаю /
Готово), Матрица (срочно / важно).
"""

from __future__ import annotations

import html
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select

from backend.app.core.config import get_settings
from backend.app.core.notify import get_notifier
from backend.app.core.textparse import find_deadline
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import User, iso_utc, utcnow
from backend.app.db.models_campus import Board, BoardMember, FocusSession, Task, TaskSuggestion
from backend.app.i18n import t

STATUSES = ("todo", "doing", "done")
REPEATS = ("", "daily", "weekly")
MAX_TASKS = 1000
MAX_TITLE = 256
DEFAULT_REMIND_HOUR = 9
WEEKDAY_RE = re.compile(r"\b(?:до|к|в|во|на)?\s*(понедельник\w*|вторник\w*|сред[уаеы]|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)", re.IGNORECASE)
WEEKDAY_STEMS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6}
TIME_RE = re.compile(r"\b(?:до|к|в|на)?\s*([01]?\d|2[0-3]):([0-5]\d)\b", re.IGNORECASE)
HOUR_RE = re.compile(r"\b(?:до|к|в)\s+([01]?\d|2[0-3])\s*(?:ч\b|час\w*)", re.IGNORECASE)
SUBJECT_RE = re.compile(r"\bпо\s+([A-Za-zА-Яа-яЁё][\w+#.]{1,30})", re.IGNORECASE)
URGENT_RE = re.compile(r"\b(срочно|важно|обязательно)\b|!!", re.IGNORECASE)
NOT_SUBJECTS = {"времени", "почте", "телефону", "возможности", "плану", "делу", "пути", "ошибке", "дому", "очереди"}


class PlannerError(Exception):
    pass


def local_now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


def local_today() -> date:
    return local_now().date()


# ------------------------------------------------------------------ быстрое добавление: разбор текста


def _next_weekday(word: str, today: date) -> date | None:
    word = word.lower()
    for stem, weekday in WEEKDAY_STEMS.items():
        if word.startswith(stem):
            return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)
    return None


def parse_task_text(text: str, today: date | None = None, subjects: list[str] | None = None) -> dict:
    """«сдать лабу по Python в пятницу до 18:00» → что сделать, когда и по какому предмету.

    Без ИИ: даты — тем же разбором, что у договорённостей (core/textparse.py), плюс дни недели и время.
    """
    today = today or local_today()
    raw = " ".join((text or "").split())
    rest = raw
    due_date = due_time = ""

    phrase, iso = find_deadline(raw, today)
    if iso:
        due_date = iso
        rest = rest.replace(phrase, " ", 1)
    else:
        m = WEEKDAY_RE.search(raw)
        if m and (d := _next_weekday(m.group(1), today)):
            due_date = d.isoformat()
            rest = rest.replace(m.group(0), " ", 1)

    m = TIME_RE.search(rest)
    if m:
        due_time = f"{int(m.group(1)):02d}:{m.group(2)}"
        rest = rest.replace(m.group(0), " ", 1)
    elif (m := HOUR_RE.search(rest)):
        due_time = f"{int(m.group(1)):02d}:00"
        rest = rest.replace(m.group(0), " ", 1)
    if due_time and not due_date:
        due_date = today.isoformat()

    subject = ""
    for known in subjects or []:
        if known and re.search(rf"\b{re.escape(known)}\b", raw, re.IGNORECASE):
            subject = known
            break
    if not subject and (m := SUBJECT_RE.search(raw)) and m.group(1).lower() not in NOT_SUBJECTS:
        subject = m.group(1).strip(".,")

    priority = 2 if URGENT_RE.search(raw) else 1
    title = re.sub(r"\s+", " ", URGENT_RE.sub(" ", rest)).strip(" ,.-—")
    title = re.sub(r"\s+(до|к|в|во|на)$", "", title, flags=re.IGNORECASE).strip(" ,.-—")
    title = (title[:1].upper() + title[1:]) if title else raw[:MAX_TITLE]
    return {"title": title[:MAX_TITLE], "due_date": due_date, "due_time": due_time, "subject": subject[:64], "priority": priority}


# ------------------------------------------------------------------ представление задачи


def _due_dt(task) -> datetime | None:
    if not task.due_date:
        return None
    try:
        d = date.fromisoformat(task.due_date)
    except ValueError:
        return None
    hh, mm = (task.due_time.split(":") if task.due_time else (str(DEFAULT_REMIND_HOUR), "00"))
    return datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=ZoneInfo(get_settings().timezone))


def auto_urgent(task, today: date) -> bool:
    if task.urgent is not None:
        return bool(task.urgent)
    if not task.due_date:
        return False
    try:
        return date.fromisoformat(task.due_date) <= today + timedelta(days=2)
    except ValueError:
        return False


def auto_important(task) -> bool:
    if task.important is not None:
        return bool(task.important)
    return task.priority >= 2 or task.promise or task.source in ("agreement", "syllabus", "hub", "calendar")


def task_dict(task: Task, today: date | None = None, subtasks: list[Task] | None = None) -> dict:
    today = today or local_today()
    overdue = bool(task.due_date and task.status != "done" and task.due_date < today.isoformat())
    data = {
        "id": task.id, "title": task.title, "note": task.note, "due_date": task.due_date, "due_time": task.due_time,
        "subject": task.subject, "priority": task.priority, "repeat": task.repeat, "status": task.status,
        "parent_id": task.parent_id, "source": task.source, "source_label": t(f"pl.source.{task.source}"),
        "urgent": auto_urgent(task, today), "important": auto_important(task),
        "urgent_set": task.urgent is not None, "important_set": task.important is not None,
        "frog": task.frog_date == today.isoformat(), "promise": task.promise,
        "witness_state": task.witness_state, "witness_name": task.witness_name,
        "board_id": task.board_id, "assignee_id": task.assignee_id, "assignee_name": task.assignee_name,
        "overdue": overdue, "moved_count": task.moved_count, "focus_minutes": task.focus_minutes,
        "created_at": iso_utc(task.created_at), "done_at": iso_utc(task.done_at),
    }
    if subtasks is not None:
        data["subtasks"] = [task_dict(s, today) for s in subtasks]
        data["progress"] = [sum(s.status == "done" for s in subtasks), len(subtasks)]
    return data


def _own(session, user_id: int, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise PlannerError(t("pl.err.not_found"))
    if task.board_id is not None:
        if not session.scalar(select(BoardMember.id).where(BoardMember.board_id == task.board_id, BoardMember.user_id == user_id)):
            raise PlannerError(t("pl.err.not_found"))
    elif task.user_id != user_id:
        # Чужая личная задача: отвечаем «не найдена», чтобы не раскрывать даже её существование.
        raise PlannerError(t("pl.err.not_found"))
    return task


def _clean_fields(fields: dict) -> dict:
    out = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "title":
            value = " ".join(str(value).split())[:MAX_TITLE]
            if not value:
                raise PlannerError(t("pl.err.title"))
        elif key == "due_date" and value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise PlannerError(t("pl.err.date")) from exc
        elif key == "due_time" and value and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise PlannerError(t("pl.err.time"))
        elif key == "status" and value not in STATUSES:
            raise PlannerError(t("pl.err.status"))
        elif key == "repeat" and value not in REPEATS:
            raise PlannerError(t("pl.err.repeat"))
        elif key == "priority":
            value = max(0, min(2, int(value)))
        elif key in ("note",):
            value = str(value)[:4000]
        elif key == "subject":
            value = str(value)[:64]
        out[key] = value
    return out


# ------------------------------------------------------------------ базовые действия


def create_task(user_id: int, title: str, user_name: str = "", **fields) -> dict:
    data = _clean_fields({"title": title, **fields})
    with get_sessionmaker()() as session:
        repo.upsert_user(session, user_id, user_name)
        count = session.scalar(select(Task.id).where(Task.user_id == user_id).order_by(Task.id.desc()).offset(MAX_TASKS - 1).limit(1))
        if count:
            raise PlannerError(t("pl.err.too_many", n=MAX_TASKS))
        parent_id = data.get("parent_id")
        if parent_id:
            parent = _own(session, user_id, int(parent_id))
            data.setdefault("subject", parent.subject)
            data["board_id"] = parent.board_id
        task = Task(user_id=user_id, **data)
        session.add(task)
        session.commit()
        return task_dict(task)


def get_task(user_id: int, task_id: int) -> dict:
    with get_sessionmaker()() as session:
        task = _own(session, user_id, task_id)
        subs = list(session.scalars(select(Task).where(Task.parent_id == task.id).order_by(Task.due_date, Task.id)))
        return task_dict(task, subtasks=subs)


def update_task(user_id: int, task_id: int, **fields) -> dict:
    data = _clean_fields(fields)
    with get_sessionmaker()() as session:
        task = _own(session, user_id, task_id)
        if "due_date" in data and data["due_date"] != task.due_date and task.due_date:
            task.moved_count += 1
        if {"due_date", "due_time"} & data.keys():
            task.reminded = False
        for key, value in data.items():
            if key in ("urgent", "important"):
                value = None if value == "auto" else bool(value)
            setattr(task, key, value)
        if data.get("status") == "done" and task.done_at is None:
            task.done_at = utcnow()
        elif "status" in data and data["status"] != "done":
            task.done_at = None
        session.commit()
        result = task_dict(task)
    if data.get("status") == "done":
        _after_done(task_id)
    return result


def complete_task(user_id: int, task_id: int, done: bool = True) -> dict:
    return update_task(user_id, task_id, status="done" if done else "todo")


def _after_done(task_id: int) -> None:
    """Повтор: создаём следующую задачу. Свидетель обещания узнаёт, что слово сдержано."""
    with get_sessionmaker()() as session:
        task = session.get(Task, task_id)
        if task is None:
            return
        if task.repeat and task.due_date:
            step = 1 if task.repeat == "daily" else 7
            nxt = date.fromisoformat(task.due_date) + timedelta(days=step)
            exists = session.scalar(select(Task.id).where(Task.user_id == task.user_id, Task.title == task.title,
                                                          Task.due_date == nxt.isoformat(), Task.status != "done"))
            if not exists:
                session.add(Task(user_id=task.user_id, title=task.title, note=task.note, due_date=nxt.isoformat(), due_time=task.due_time,
                                 subject=task.subject, priority=task.priority, repeat=task.repeat, source=task.source))
        if task.promise and task.witness_state == "accepted" and task.witness_id and not task.witness_notified:
            task.witness_notified = True
            _queue_witness(task, kept=True)
        session.commit()


_witness_queue: list[tuple[int, str]] = []


def _queue_witness(task: Task, kept: bool) -> None:
    owner_name = ""
    with get_sessionmaker()() as session:
        owner = repo.get_user(session, task.user_id)
        owner_name = owner.first_name if owner else ""
    key = "pl.witness.kept" if kept else "pl.witness.missed"
    _witness_queue.append((task.witness_id, html.escape(t(key, name=owner_name or "Друг", title=task.title))))


async def flush_witness_queue() -> int:
    """Отправить накопленные сообщения свидетелям (вызывают API, бот и планировщик)."""
    notifier = get_notifier()
    sent = 0
    while _witness_queue:
        chat_id, text = _witness_queue.pop(0)
        if notifier is not None:
            sent += await notifier.send(chat_id, text)
    return sent


def move_task(user_id: int, task_id: int, days: int | None = None, to_date: str | None = None) -> dict:
    with get_sessionmaker()() as session:
        task = _own(session, user_id, task_id)
        base = local_today()
        if to_date:
            new = to_date
        else:
            start = date.fromisoformat(task.due_date) if task.due_date and task.due_date >= base.isoformat() else base
            new = (start + timedelta(days=days or 1)).isoformat()
    return update_task(user_id, task_id, due_date=new)


def delete_task(user_id: int, task_id: int) -> None:
    with get_sessionmaker()() as session:
        task = _own(session, user_id, task_id)
        session.execute(delete(Task).where(Task.parent_id == task.id))
        session.delete(task)
        session.commit()


def set_frog(user_id: int, task_id: int) -> dict:
    today = local_today().isoformat()
    with get_sessionmaker()() as session:
        for other in session.scalars(select(Task).where(Task.user_id == user_id, Task.frog_date == today)):
            other.frog_date = ""
        task = _own(session, user_id, task_id)
        task.frog_date = today
        session.commit()
        return task_dict(task)


# ------------------------------------------------------------------ виды


def _personal(session, user_id: int, *conditions):
    query = select(Task).where(Task.user_id == user_id, Task.board_id.is_(None), Task.parent_id.is_(None), *conditions)
    return list(session.scalars(query.order_by(Task.due_date == "", Task.due_date, Task.due_time == "", Task.due_time, Task.priority.desc(), Task.id)))


def _with_subs(session, tasks: list[Task], today: date) -> list[dict]:
    if not tasks:
        return []
    subs: dict[int, list[Task]] = {}
    for s in session.scalars(select(Task).where(Task.parent_id.in_([x.id for x in tasks])).order_by(Task.due_date, Task.id)):
        subs.setdefault(s.parent_id, []).append(s)
    return [task_dict(x, today, subs.get(x.id, [])) for x in tasks]


def view(user_id: int, name: str = "today", today: date | None = None, course: str = "") -> dict:
    today = today or local_today()
    iso = today.isoformat()
    with get_sessionmaker()() as session:
        if name == "today":
            open_tasks = _personal(session, user_id, Task.status != "done", or_(Task.due_date <= iso, Task.frog_date == iso), Task.due_date != "")
            frog_only = _personal(session, user_id, Task.status != "done", Task.frog_date == iso, Task.due_date == "")
            done_today = [x for x in _personal(session, user_id, Task.status == "done") if x.done_at and _local_date(x.done_at) == today]
            items = _with_subs(session, open_tasks + frog_only, today)
            return {"view": "today", "date": iso, "overdue": [x for x in items if x["overdue"]],
                    "today": [x for x in items if not x["overdue"]], "done": _with_subs(session, done_today, today),
                    "frog": next((x for x in items if x["frog"]), None)}
        if name == "week":
            end = (today + timedelta(days=6)).isoformat()
            tasks = _with_subs(session, _personal(session, user_id, Task.due_date >= iso, Task.due_date <= end), today)
            overdue = _with_subs(session, _personal(session, user_id, Task.status != "done", Task.due_date != "", Task.due_date < iso), today)
            nodate = _with_subs(session, _personal(session, user_id, Task.status != "done", Task.due_date == ""), today)
            days = []
            for i in range(7):
                d = (today + timedelta(days=i)).isoformat()
                days.append({"date": d, "tasks": [x for x in tasks if x["due_date"] == d]})
            return {"view": "week", "days": days, "overdue": overdue, "nodate": nodate}
        if name == "board":
            week_ago = utcnow() - timedelta(days=7)
            tasks = [x for x in _personal(session, user_id) if x.status != "done" or (x.done_at and _aware(x.done_at) >= week_ago)]
            items = _with_subs(session, tasks, today)
            return {"view": "board", "columns": {s: [x for x in items if x["status"] == s] for s in STATUSES}}
        if name == "matrix":
            items = _with_subs(session, _personal(session, user_id, Task.status != "done"), today)
            quadrants = {"q1": [], "q2": [], "q3": [], "q4": []}
            for x in items:
                key = {(True, True): "q1", (False, True): "q2", (True, False): "q3", (False, False): "q4"}[(x["urgent"], x["important"])]
                quadrants[key].append(x)
            return {"view": "matrix", "quadrants": quadrants}
        if name == "semester":
            return _semester(session, user_id, today, course)
    raise PlannerError(t("pl.err.view"))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _local_date(value: datetime) -> date:
    return _aware(value).astimezone(ZoneInfo(get_settings().timezone)).date()


def _semester(session, user_id: int, today: date, course: str) -> dict:
    """Лента до конца учебного года: вехи академкалендаря (РК, сессия, каникулы) + мои задачи по месяцам."""
    from backend.app.university import calendar as acal

    cal = acal.calendar_for(today)
    end = (cal.year_start + timedelta(days=365)) if cal else today + timedelta(days=150)
    milestones = []
    if cal:
        for e in cal.events:
            if e.end < today or e.start > end or e.kind not in ("midterm", "session", "holidays", "fx", "registration", "final", "thesis", "practice"):
                continue
            if course and course not in e.courses:
                continue
            if not course and not acal.is_general(e):
                continue
            milestones.append(e.public())
    tasks = _with_subs(session, _personal(session, user_id, Task.due_date >= today.isoformat(), Task.due_date <= end.isoformat()), today)
    months: dict[str, dict] = {}
    for m in milestones:
        months.setdefault(m["start"][:7], {"month": m["start"][:7], "milestones": [], "tasks": []})["milestones"].append(m)
    for x in tasks:
        months.setdefault(x["due_date"][:7], {"month": x["due_date"][:7], "milestones": [], "tasks": []})["tasks"].append(x)
    return {"view": "semester", "months": [months[k] for k in sorted(months)],
            "calendar": {"year": cal.academic_year, "url": cal.page_url, "checked": cal.checked} if cal else None}


def today_brief(user_id: int, limit: int = 4) -> list[dict]:
    """Блок «Сегодня» на Главной: ближайшие задачи (просроченные — с мягкой пометкой)."""
    data = view(user_id, "today")
    items = data["overdue"] + data["today"]
    if len(items) < limit:
        week = view(user_id, "week")
        seen = {x["id"] for x in items}
        for day in week["days"][1:]:
            items += [x for x in day["tasks"] if x["id"] not in seen and x["status"] != "done"]
    if len(items) < limit:
        # Ближайшие сроки дальше недели (например, РК через две недели) — тоже показываем, иначе кажется, что дедлайнов нет.
        today = local_today()
        seen = {x["id"] for x in items}
        with get_sessionmaker()() as session:
            later = _personal(session, user_id, Task.status != "done", Task.due_date > (today + timedelta(days=7)).isoformat())
            items += [task_dict(t, today) for t in later if t.id not in seen][: limit - len(items)]
    return items[:limit]


# ------------------------------------------------------------------ предложения («задачи приходят сами»)


def suggest(user_id: int, title: str, source: str, source_ref: str, due_date: str = "", due_time: str = "",
            subject: str = "", note: str = "") -> bool:
    """Предложить задачу. Повторно то же предложение не создаётся (и отклонённое не возвращается)."""
    with get_sessionmaker()() as session:
        exists = session.scalar(select(TaskSuggestion.id).where(TaskSuggestion.user_id == user_id, TaskSuggestion.source == source,
                                                                TaskSuggestion.source_ref == str(source_ref)[:64]))
        if exists:
            return False
        session.add(TaskSuggestion(user_id=user_id, title=" ".join(title.split())[:MAX_TITLE], source=source, source_ref=str(source_ref)[:64],
                                   due_date=due_date, due_time=due_time, subject=subject[:64], note=note[:2000]))
        session.commit()
        return True


def calendar_suggestions(user_id: int, course: str, today: date | None = None, days: int = 30) -> int:
    """Из академкалендаря: рубежный контроль, регистрация на дисциплины, сессия, FX — на ближайший месяц."""
    from backend.app.university import calendar as acal

    if not course:
        return 0
    today = today or local_today()
    added = 0
    for e in acal.events_for(course, today, days_ahead=days, include_ongoing=False):
        if e.kind in ("midterm", "registration", "session", "fx", "thesis"):
            added += suggest(user_id, e.title, "calendar", e.id, due_date=e.start.isoformat(), note=f"{e.source_title}: {e.source_url}")
    return added


def suggestion_dict(s: TaskSuggestion) -> dict:
    return {"id": s.id, "title": s.title, "due_date": s.due_date, "due_time": s.due_time, "subject": s.subject,
            "source": s.source, "source_label": t(f"pl.source.{s.source}"), "note": s.note, "created_at": iso_utc(s.created_at)}


def list_suggestions(user_id: int, course: str = "") -> list[dict]:
    from backend.app.core.features import enabled

    if course and enabled("planner"):
        calendar_suggestions(user_id, course)
    today = local_today().isoformat()
    with get_sessionmaker()() as session:
        rows = session.scalars(select(TaskSuggestion).where(TaskSuggestion.user_id == user_id, TaskSuggestion.status == "pending")
                               .order_by(TaskSuggestion.due_date == "", TaskSuggestion.due_date, TaskSuggestion.id))
        # Прошедшие вехи не предлагаем: они уже не помогут.
        return [suggestion_dict(s) for s in rows if not s.due_date or s.due_date >= today]


def accept_suggestion(user_id: int, suggestion_id: int) -> dict:
    with get_sessionmaker()() as session:
        s = session.get(TaskSuggestion, suggestion_id)
        if s is None or s.user_id != user_id or s.status != "pending":
            raise PlannerError(t("pl.err.suggestion"))
        s.status = "accepted"
        fields = {"due_date": s.due_date, "due_time": s.due_time, "subject": s.subject, "note": s.note,
                  "source": s.source, "source_ref": s.source_ref}
        title = s.title
        session.commit()
    return create_task(user_id, title, **fields)


def dismiss_suggestion(user_id: int, suggestion_id: int) -> None:
    with get_sessionmaker()() as session:
        s = session.get(TaskSuggestion, suggestion_id)
        if s is None or s.user_id != user_id:
            raise PlannerError(t("pl.err.suggestion"))
        s.status = "dismissed"
        session.commit()


def suggest_from_agreement(agreement, user_ids: set[int]) -> int:
    """Срок договорённости → предложенная задача у её участников."""
    import json

    if not agreement.deadline_iso:
        return 0
    what = json.loads(agreement.draft_json or "{}").get("what") or agreement.text
    added = 0
    for uid in user_ids:
        if uid:
            added += suggest(uid, what[:200], "agreement", agreement.code, due_date=agreement.deadline_iso,
                             due_time=agreement.deadline_time or "", note=t("pl.from_agreement", code=agreement.code))
    return added


# ------------------------------------------------------------------ светофор и вердикт недели


def _week_bounds(today: date, next_week: bool = False) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    if next_week:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=6)


def traffic_light(user_id: int, course: str = "", today: date | None = None) -> dict:
    """Перегружена ли следующая неделя: мои дедлайны + РК/сессия из календаря. Предлагает, что начать раньше."""
    from backend.app.university import calendar as acal

    today = today or local_today()
    start, end = _week_bounds(today, next_week=True)
    with get_sessionmaker()() as session:
        tasks = _personal(session, user_id, Task.status != "done", Task.due_date >= start.isoformat(), Task.due_date <= end.isoformat())
        early = [task_dict(x, today) for x in tasks[:3]]
        pending = session.scalars(select(TaskSuggestion).where(TaskSuggestion.user_id == user_id, TaskSuggestion.status == "pending",
                                                               TaskSuggestion.due_date >= start.isoformat(), TaskSuggestion.due_date <= end.isoformat())).all()
    exams = []
    cal = acal.calendar_for(today)
    if cal and course:
        exams = [e.public() for e in cal.events if e.kind in ("midterm", "session", "fx") and course in e.courses
                 and e.start <= end and e.end >= start]
    load = len(tasks) + len(pending)
    level = "red" if load >= 5 or (load >= 3 and exams) else "yellow" if load >= 3 or exams else "green"
    return {"level": level, "week_start": start.isoformat(), "week_end": end.isoformat(), "tasks": len(tasks),
            "suggested": len(pending), "exams": exams, "start_early": early,
            "text": t(f"pl.traffic.{level}", n=len(tasks) + len(pending), exams=len(exams))}


def week_verdict(user_id: int, today: date | None = None) -> dict:
    """Личная карточка «Вердикт недели»: сделано, перенесено, что горит на следующей неделе. Видна только владельцу."""
    today = today or local_today()
    start, end = _week_bounds(today)
    n_start, n_end = _week_bounds(today, next_week=True)
    with get_sessionmaker()() as session:
        mine = list(session.scalars(select(Task).where(Task.user_id == user_id, Task.board_id.is_(None))))
    done = [x for x in mine if x.status == "done" and x.done_at and start <= _local_date(x.done_at) <= end]
    planned = [x for x in mine if x.due_date and start.isoformat() <= x.due_date <= end.isoformat()]
    moved = [x for x in mine if x.moved_count and x.status != "done" and x.due_date >= start.isoformat()]
    hot = [x for x in mine if x.status != "done" and x.due_date and n_start.isoformat() <= x.due_date <= n_end.isoformat()]
    open_now = [x for x in planned if x.status != "done"]
    ratio = len(done) / max(1, len(done) + len(open_now))
    status = "green" if ratio >= 0.7 else "yellow" if ratio >= 0.4 or not (done or open_now) else "red"
    return {
        "week_start": start.isoformat(), "week_end": end.isoformat(), "status": status,
        "done": [x.title for x in done], "moved": [x.title for x in moved], "open": [x.title for x in open_now], "hot": [x.title for x in hot],
        "title": t("pl.verdict.title", done=len(done), total=len(done) + len(open_now)),
    }


def week_verdict_card(user_id: int, today: date | None = None):
    """То же — в формате карточки вердикта (для картинки, которой можно поделиться по желанию)."""
    from backend.app.cards.schema import Reason, VerdictCard

    v = week_verdict(user_id, today)
    reasons = [Reason(text=t("pl.verdict.done_line", items=", ".join(v["done"][:5]) or "—"))]
    if v["moved"]:
        reasons.append(Reason(text=t("pl.verdict.moved_line", items=", ".join(v["moved"][:5]))))
    if v["open"]:
        reasons.append(Reason(text=t("pl.verdict.open_line", items=", ".join(v["open"][:5]))))
    do = [t("pl.verdict.hot_line", items=", ".join(v["hot"][:5]))] if v["hot"] else [t("pl.verdict.calm")]
    return VerdictCard(status=v["status"], kind="document", title=v["title"], reasons=reasons, do=do,
                       dont=[t("pl.verdict.no_shame")], confidence=100)


# ------------------------------------------------------------------ «Разбей на шаги»


def steps_preview_rules(title: str, due_date: str, today: date | None = None) -> list[dict]:
    """Шаги по шаблону (без ИИ). Бот планирует работу, но не делает её за студента."""
    today = today or local_today()
    low = title.lower()
    if re.search(r"курсов|диплом|проект|реферат|эссе|essay|project", low):
        names = t("pl.steps.project").split("|")
    elif re.search(r"экзамен|сесси|рубежк|рк\b|midterm|final|тест|контрольн", low):
        names = t("pl.steps.exam").split("|")
    elif re.search(r"лаб|lab|задач|домашк|дз|assignment", low):
        names = t("pl.steps.lab").split("|")
    else:
        names = t("pl.steps.default").split("|")
    try:
        due = date.fromisoformat(due_date) if due_date else today + timedelta(days=14)
    except ValueError:
        due = today + timedelta(days=14)
    span = max(0, (due - today).days)
    steps = []
    for i, name in enumerate(names):
        offset = round(span * (i + 1) / (len(names) + (1 if span >= len(names) else 0))) if span else 0
        steps.append({"title": name.strip(), "due_date": min(due, today + timedelta(days=offset)).isoformat()})
    return steps


async def steps_preview(user_id: int, task_id: int) -> list[dict]:
    from backend.app.core.engine import EngineError, get_verdict_engine

    task = get_task(user_id, task_id)
    try:
        steps = await get_verdict_engine().split_steps(task["title"], task["due_date"], user_id)
    except EngineError:
        steps = []
    return steps or steps_preview_rules(task["title"], task["due_date"])


def add_steps(user_id: int, task_id: int, steps: list[dict]) -> dict:
    get_task(user_id, task_id)
    for step in steps[:12]:
        if (step.get("title") or "").strip():
            create_task(user_id, step["title"], parent_id=task_id, due_date=step.get("due_date") or "")
    return get_task(user_id, task_id)


# ------------------------------------------------------------------ «Договор с собой»


def make_promise(user_id: int, task_id: int, witness: bool) -> dict:
    with get_sessionmaker()() as session:
        task = _own(session, user_id, task_id)
        if task.board_id is not None:
            raise PlannerError(t("pl.err.promise_board"))
        task.promise = True
        if witness and not task.witness_token:
            task.witness_token = secrets.token_urlsafe(9)[:12]
            task.witness_state = "invited"
        session.commit()
        return {**task_dict(task), "witness_token": task.witness_token}


def witness_info(token: str) -> dict | None:
    with get_sessionmaker()() as session:
        task = session.scalar(select(Task).where(Task.witness_token == token))
        if task is None:
            return None
        owner = repo.get_user(session, task.user_id)
        return {"title": task.title, "due_date": task.due_date, "owner_name": owner.first_name if owner else "", "state": task.witness_state,
                "owner_id": task.user_id}


def witness_answer(token: str, user_id: int, user_name: str, accept: bool) -> dict:
    with get_sessionmaker()() as session:
        task = session.scalar(select(Task).where(Task.witness_token == token))
        if task is None:
            raise PlannerError(t("pl.err.not_found"))
        if task.user_id == user_id:
            raise PlannerError(t("pl.err.own_witness"))
        if task.witness_state not in ("invited",):
            raise PlannerError(t("pl.err.witness_done"))
        repo.upsert_user(session, user_id, user_name)
        task.witness_state = "accepted" if accept else "declined"
        task.witness_id = user_id if accept else None
        task.witness_name = user_name[:128] if accept else ""
        session.commit()
        return {"state": task.witness_state, "title": task.title, "owner_id": task.user_id}


def overdue_promises(today: date) -> int:
    """Срок обещания прошёл, а оно не выполнено: свидетель узнаёт об этом один раз (бережно)."""
    n = 0
    with get_sessionmaker()() as session:
        for task in session.scalars(select(Task).where(Task.promise.is_(True), Task.witness_state == "accepted", Task.witness_notified.is_(False),
                                                       Task.status != "done", Task.due_date != "", Task.due_date < today.isoformat())):
            task.witness_notified = True
            _queue_witness(task, kept=False)
            n += 1
        session.commit()
    return n


# ------------------------------------------------------------------ командная доска


def _board_member(session, board_id: int, user_id: int) -> Board:
    board = session.get(Board, board_id)
    if board is None or not session.scalar(select(BoardMember.id).where(BoardMember.board_id == board_id, BoardMember.user_id == user_id)):
        raise PlannerError(t("pl.err.board"))
    return board


def create_board(user_id: int, user_name: str, title: str = "", agreement_code: str = "", circle_id: int | None = None) -> dict:
    """Доска из договорённости в чате (участники — те, кто её подтвердил) или из «Группы»."""
    from backend.app.services import agreements as agreements_service
    from backend.app.services import circles as circles_service

    members: dict[int, str] = {user_id: user_name}
    if agreement_code:
        agreement = agreements_service.get(agreement_code)
        if agreement is None or not agreements_service.is_participant(agreement, user_id):
            raise PlannerError(t("pl.err.board_source"))
        members[agreement.creator_id] = agreement.creator_name
        if agreement.counterparty_id:
            members[agreement.counterparty_id] = agreement.counterparty_name
        for p in agreements_service.participants(agreement):
            if p["accepted"]:
                members[p["user_id"]] = p["name"]
        import json

        title = title or (json.loads(agreement.draft_json or "{}").get("what") or agreement.text)[:120]
    elif circle_id:
        try:
            info = circles_service.info(circle_id, user_id)
        except circles_service.CircleError as exc:
            raise PlannerError(t("pl.err.board_source")) from exc
        for m in info.get("members", []):
            members[m["user_id"]] = m["name"]
        title = title or info["title"]
    title = " ".join((title or "").split())[:128]
    if not title:
        raise PlannerError(t("pl.err.title"))
    with get_sessionmaker()() as session:
        board = Board(title=title, owner_id=user_id, agreement_code=agreement_code, circle_id=circle_id)
        session.add(board)
        session.flush()
        for uid, name in members.items():
            session.add(BoardMember(board_id=board.id, user_id=uid, name=(name or "")[:128]))
        session.commit()
        board_id = board.id
    return board_view(user_id, board_id)


def list_boards(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        ids = select(BoardMember.board_id).where(BoardMember.user_id == user_id)
        return [{"id": b.id, "title": b.title, "agreement_code": b.agreement_code, "is_owner": b.owner_id == user_id}
                for b in session.scalars(select(Board).where(Board.id.in_(ids)).order_by(Board.created_at.desc()))]


def board_view(user_id: int, board_id: int) -> dict:
    today = local_today()
    with get_sessionmaker()() as session:
        board = _board_member(session, board_id, user_id)
        members = [{"user_id": m.user_id, "name": m.name or "Участник"} for m in session.scalars(select(BoardMember).where(BoardMember.board_id == board_id))]
        tasks = list(session.scalars(select(Task).where(Task.board_id == board_id, Task.parent_id.is_(None)).order_by(Task.due_date == "", Task.due_date, Task.id)))
        items = [task_dict(x, today) for x in tasks]
        return {"id": board.id, "title": board.title, "is_owner": board.owner_id == user_id, "members": members,
                "agreement_code": board.agreement_code, "columns": {s: [x for x in items if x["status"] == s] for s in STATUSES}}


async def add_board_task(user_id: int, board_id: int, title: str, assignee_id: int | None = None, due_date: str = "") -> dict:
    data = _clean_fields({"title": title, "due_date": due_date})
    with get_sessionmaker()() as session:
        board = _board_member(session, board_id, user_id)
        assignee = None
        if assignee_id:
            assignee = session.scalar(select(BoardMember).where(BoardMember.board_id == board_id, BoardMember.user_id == assignee_id))
            if assignee is None:
                raise PlannerError(t("pl.err.assignee"))
        task = Task(user_id=user_id, board_id=board_id, assignee_id=assignee.user_id if assignee else None,
                    assignee_name=assignee.name if assignee else "", source="agreement" if board.agreement_code else "manual", **data)
        session.add(task)
        session.commit()
        result = task_dict(task)
        board_title = board.title
    if assignee_id and assignee_id != user_id and (notifier := get_notifier()):
        with get_sessionmaker()() as session:
            user = repo.get_user(session, assignee_id)
        if user and user.bot_started:
            await notifier.send(assignee_id, html.escape(t("pl.board.assigned", board=board_title, title=result["title"],
                                                            due=result["due_date"] or "—")))
    return result


# ------------------------------------------------------------------ фокус («Помидор»)


def focus_start(user_id: int, minutes: int = 25, task_id: int | None = None) -> dict:
    minutes = max(5, min(90, int(minutes)))
    with get_sessionmaker()() as session:
        if task_id:
            _own(session, user_id, task_id)
        for old in session.scalars(select(FocusSession).where(FocusSession.user_id == user_id, FocusSession.finished.is_(False))):
            old.finished = True
        now = utcnow()
        item = FocusSession(user_id=user_id, task_id=task_id, minutes=minutes, started_at=now, ends_at=now + timedelta(minutes=minutes))
        session.add(item)
        session.commit()
        return {"id": item.id, "minutes": minutes, "ends_at": iso_utc(item.ends_at), "task_id": task_id, "room": focus_room()}


def focus_finish(user_id: int, session_id: int) -> dict:
    with get_sessionmaker()() as session:
        item = session.get(FocusSession, session_id)
        if item is None or item.user_id != user_id:
            raise PlannerError(t("pl.err.not_found"))
        if not item.finished:
            item.finished = True
            spent = max(1, min(item.minutes, round((utcnow() - _aware(item.started_at)).total_seconds() / 60)))
            if item.task_id and (task := session.get(Task, item.task_id)):
                task.focus_minutes += spent
        session.commit()
    return {"finished": True, "room": focus_room()}


def focus_room() -> dict:
    """Сколько человек учится прямо сейчас — только число, без имён."""
    now = utcnow()
    with get_sessionmaker()() as session:
        active = {r[0] for r in session.execute(select(FocusSession.user_id).where(FocusSession.finished.is_(False), FocusSession.ends_at > now)).all()}
        today_start = now - timedelta(hours=24)
        sessions_today = len(session.scalars(select(FocusSession.id).where(FocusSession.started_at >= today_start)).all())
    return {"now": len(active), "today": sessions_today}


def my_focus(user_id: int) -> dict | None:
    now = utcnow()
    with get_sessionmaker()() as session:
        item = session.scalar(select(FocusSession).where(FocusSession.user_id == user_id, FocusSession.finished.is_(False), FocusSession.ends_at > now))
        return {"id": item.id, "minutes": item.minutes, "ends_at": iso_utc(item.ends_at), "task_id": item.task_id} if item else None


# ------------------------------------------------------------------ экспорт .ics и удаление


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def export_ics(user_id: int) -> str:
    """Задачи со сроками → файл календаря (.ics), который открывают Google/Apple/Outlook-календари."""
    with get_sessionmaker()() as session:
        tasks = list(session.scalars(select(Task).where(Task.user_id == user_id, Task.board_id.is_(None), Task.due_date != "")))
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Verdikt//Moi plan//RU", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Мой план", f"X-WR-TIMEZONE:{get_settings().timezone}"]
    for task in tasks:
        d = task.due_date.replace("-", "")
        lines += ["BEGIN:VEVENT", f"UID:task-{task.id}@ano-iitu", f"DTSTAMP:{stamp}"]
        if task.due_time:
            hh, mm = task.due_time.split(":")
            lines += [f"DTSTART;TZID={get_settings().timezone}:{d}T{hh}{mm}00", "DURATION:PT30M"]
        else:
            nxt = (date.fromisoformat(task.due_date) + timedelta(days=1)).strftime("%Y%m%d")
            lines += [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nxt}"]
        summary = ("✅ " if task.status == "done" else "") + task.title + (f" ({task.subject})" if task.subject else "")
        lines.append(f"SUMMARY:{_ics_escape(summary)}")
        if task.note:
            lines.append(f"DESCRIPTION:{_ics_escape(task.note[:500])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def delete_all(user_id: int, session=None) -> None:
    """Удаляет все задачи и данные планера пользователя (в т. ч. доски, которые он создал)."""
    own = session is None
    session = session or get_sessionmaker()()
    try:
        owned = [b.id for b in session.scalars(select(Board).where(Board.owner_id == user_id))]
        if owned:
            session.execute(delete(Task).where(Task.board_id.in_(owned)))
            session.execute(delete(BoardMember).where(BoardMember.board_id.in_(owned)))
            session.execute(delete(Board).where(Board.id.in_(owned)))
        session.execute(delete(BoardMember).where(BoardMember.user_id == user_id))
        for task in session.scalars(select(Task).where(Task.assignee_id == user_id)):
            task.assignee_id, task.assignee_name = None, ""
        for task in session.scalars(select(Task).where(Task.witness_id == user_id)):
            task.witness_id, task.witness_name, task.witness_state = None, "", "declined"
        session.execute(delete(Task).where(Task.user_id == user_id, Task.board_id.is_(None)))
        session.execute(delete(TaskSuggestion).where(TaskSuggestion.user_id == user_id))
        session.execute(delete(FocusSession).where(FocusSession.user_id == user_id))
        from backend.app.db.models_campus import Pref

        session.execute(delete(Pref).where(Pref.user_id == user_id, Pref.key.like("plan.%")))
        session.commit()
    finally:
        if own:
            session.close()


# ------------------------------------------------------------------ напоминания (планировщик)


def due_reminders(now: datetime) -> list[tuple[int, str, int]]:
    """(кому, текст, id задачи) — личные задачи тех, кто включил напоминания, с учётом тихих часов.

    Исполнители задач командной доски получают напоминание как участники договорённости.
    """
    from backend.app.services import prefs

    out: list[tuple[int, str, int]] = []
    horizon = (now.date() + timedelta(days=2)).isoformat()
    with get_sessionmaker()() as session:
        tasks = list(session.scalars(select(Task).where(Task.status != "done", Task.reminded.is_(False), Task.due_date != "",
                                                       Task.due_date <= horizon, Task.due_date >= (now.date() - timedelta(days=1)).isoformat())))
        started = {u.telegram_id for u in session.scalars(select(User).where(User.bot_started.is_(True)))}
        for task in tasks:
            target = task.assignee_id if task.board_id is not None else task.user_id
            if not target or target not in started:
                continue
            if task.board_id is None and not prefs.get_bool(target, "plan.remind"):
                continue
            if prefs.quiet_now(target, now.hour):
                continue
            try:
                offset = int(prefs.get(target, "plan.remind_offset") or 60)
            except ValueError:
                offset = 60
            due = _due_dt(task)
            if due is None or now < due - timedelta(minutes=offset):
                continue
            task.reminded = True
            when = task.due_time or t("pl.today_word") if task.due_date == now.date().isoformat() else task.due_date
            out.append((target, html.escape(t("pl.remind", title=task.title, when=when)), task.id))
        session.commit()
    return out


def morning_text(user_id: int, course: str = "") -> str:
    """Утренняя сводка: что сегодня — дедлайны, события, одна новость, один проверенный слух."""
    from backend.app.services import campus
    from backend.app.university import calendar as acal
    from backend.app.university import news

    e = html.escape
    today = local_today()
    data = view(user_id, "today", today)
    lines = [f"☀️ <b>{e(t('pl.morning.title'))}</b>", ""]
    items = data["overdue"] + data["today"]
    if items:
        lines.append(f"<b>{e(t('pl.morning.tasks'))}</b>")
        lines += [f"• {e(x['title'])}" + (f" — {e(x['due_time'])}" if x["due_time"] else "") for x in items[:6]]
    else:
        lines.append(e(t("pl.morning.no_tasks")))
    events = [ev for ev in acal.events_for(course, today, days_ahead=0) if ev.kind not in ("study",) and (course or acal.is_general(ev))]
    if events:
        lines += ["", f"<b>{e(t('pl.morning.events'))}</b>"] + [f"{ev.emoji} {e(ev.title)}" for ev in events[:3]]
    feed = news.list_news(1)["items"]
    if feed:
        lines += ["", f"📰 <a href=\"{e(feed[0]['url'])}\">{e(feed[0]['title'])}</a>"]
    rumor = campus.rumor_of_week()
    if rumor:
        lines += ["", f"🔎 {e(t('cm.rumor.short', verdict=rumor['verdict_label'], title=rumor['title']))}"]
    return "\n".join(lines)
