"""Функции волн 1–3, которые не относятся к планеру, опросам и хабам.

Слух недели, GPA-калькулятор, обратный отсчёт, разбор силлабуса, чек-лист курса,
помощник обращений, квиз первокурсника, значки, капсула времени, карьерный трек,
рейтинг групп, отчёт по договорённости.

Факты об университете — только из data/iitu/*.json и data/sources/*.md (официальные
источники с датой сверки). Чего там нет — честно говорим «не знаю» и куда обратиться.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import delete, func, select

from backend.app.cards.schema import ResumeReview, SyllabusCard, SyllabusDeadline
from backend.app.core.config import get_settings
from backend.app.core.textparse import find_deadline
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import Agreement, Chat, Check, User, iso_utc
from backend.app.db.models_campus import Capsule, ChecklistMark, FocusSession, Task
from backend.app.i18n import t
from backend.app.security.masking import mask_sensitive
from backend.app.university.reference import load_json


class CampusError(Exception):
    pass


def week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# ------------------------------------------------------------------ Слух недели

VERDICT_LABEL = {"green": "правда", "red": "неправда", "yellow": "частично", "unknown": "не подтверждено"}


def _rumor_key(text: str) -> str:
    norm = re.sub(r"[^\w\s]", " ", (text or "").lower())
    norm = " ".join(norm.split())[:200]
    return hashlib.sha1(norm.encode()).hexdigest()


def pick_rumor(today: date) -> int | None:
    """Самый проверяемый за неделю слух (/pravda) с ясным вердиктом. Тексты с телефонами/картами не берём."""
    from backend.app.core.engine import card_from_json

    since = datetime.now(timezone.utc) - timedelta(days=7)
    groups: dict[str, dict] = {}
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Check).where(Check.mode == "pravda", Check.created_at >= since, Check.status != "unknown"))
        for check in rows:
            if mask_sensitive(check.input_text) != check.input_text or len(check.input_text) < 15:
                continue
            card = card_from_json(check.card_json)
            if card.kind == "support":
                continue
            g = groups.setdefault(_rumor_key(check.input_text), {"users": set(), "latest": check})
            g["users"].add(check.user_id)
            if check.created_at >= g["latest"].created_at:
                g["latest"] = check
    need = max(1, get_settings().rumor_min_users)
    ranked = sorted((g for g in groups.values() if len(g["users"]) >= need),
                    key=lambda g: (len(g["users"]), g["latest"].created_at), reverse=True)
    return ranked[0]["latest"].id if ranked else None


def rumor_of_week(today: date | None = None) -> dict | None:
    from backend.app.core.engine import card_from_json

    today = today or date.today()
    key = f"rumor:{week_key(today)}"
    with get_sessionmaker()() as session:
        saved = repo.get_meta(session, key)
    check_id = int(saved) if saved.isdigit() else pick_rumor(today)
    if not check_id:
        return None
    with get_sessionmaker()() as session:
        if not saved:
            repo.set_meta(session, key, str(check_id))
        check = repo.get_check(session, check_id)
        if check is None:
            return None
        card = card_from_json(check.card_json)
    return {
        "check_id": check.id, "week": week_key(today), "text": check.input_text[:500], "status": card.status,
        "verdict_label": VERDICT_LABEL.get(card.status, ""), "title": card.title, "card": card.model_dump(exclude={"notes"}),
        "sources": [s.model_dump() for s in card.sources],
    }


# ------------------------------------------------------------------ GPA-калькулятор


@lru_cache
def grading() -> dict:
    return load_json("grading.json")


def letter_for(percent: float) -> dict | None:
    """Буква и цифровой эквивалент для итоговой оценки в % (QM-02, табл. 1). Проценты округляем до целого."""
    value = round(percent)
    return next((s for s in grading()["letter_scale"] if s["min_percent"] <= value <= s["max_percent"]), None)


def final_needed(admission: float, target: float, r1: float | None = None, r2: float | None = None) -> dict:
    """Сколько нужно на экзамене, чтобы выйти на итоговую оценку target (%). Считаем на лету, ничего не сохраняем.

    R-11, п. 4.9: итоговая = (РК1 + РК2) / 2 × 0,6 + экзамен × 0,4. Рейтинг допуска = (РК1 + РК2) / 2;
    если он ниже 50%, к экзамену не допускают (п. 4.8). Можно передать РК1 и РК2 — тогда допуск считаем сами.
    """
    g = grading()
    if r1 is not None and r2 is not None:
        if not (0 <= r1 <= 100 and 0 <= r2 <= 100):
            raise CampusError(t("cm.gpa.range"))
        admission = (r1 + r2) / 2
    if not 0 <= admission <= 100 or not 0 <= target <= 100:
        raise CampusError(t("cm.gpa.range"))
    w = g["final_weight_percent"] / 100
    need = (target - (1 - w) * admission) / w
    target_letter = letter_for(target)
    result = {
        "admission": round(admission, 1), "target": target, "final_weight": g["final_weight_percent"], "need": round(need, 1),
        "reachable": need <= 100, "already": need <= 0,
        "admitted": admission >= g["admission_min_percent"], "admission_min": g["admission_min_percent"], "admission_note": g["admission_note"],
        "max_total": round((1 - w) * admission + w * 100, 1),
        "pass_total": g["pass_total_percent"], "fx_range": g["fx_range_percent"],
        "need_for_pass": round(max(0.0, (g["pass_total_percent"] - (1 - w) * admission) / w), 1),
        "target_letter": target_letter, "formula": g["formula"],
        "source": {"title": g["source_title"], "url": g["source_url"], "checked": g["checked"]},
        "letter_scale": g["letter_scale"], "letter_scale_loaded": bool(g["letter_scale"]),
        "stipend_known": bool(g.get("stipend")), "stipend_note": g.get("stipend_note", ""), "note": g["pass_note"],
    }
    if g["letter_scale"]:
        # Для каждой положительной буквы — сколько нужно на экзамене (только достижимые).
        result["letters"] = [{"letter": s["letter"], "points": s["points"], "min_percent": s["min_percent"],
                              "need": round(max(0.0, (s["min_percent"] - (1 - w) * admission) / w), 1)}
                             for s in g["letter_scale"] if s["min_percent"] >= g["pass_total_percent"]
                             and (s["min_percent"] - (1 - w) * admission) / w <= 100]
    return result


def gpa(courses: list[dict]) -> dict:
    """GPA = Σ(цифровой эквивалент × кредиты) / Σ кредитов (QM-02, п. 17). Можно передать букву или % вместо балла.

    Баллы никуда не сохраняются.
    """
    g = grading()
    by_letter = {s["letter"]: s["points"] for s in g["letter_scale"]}
    rows = []
    for c in courses:
        credits = float(c.get("credits") or 0)
        if credits <= 0:
            continue
        if c.get("letter"):
            letter = str(c["letter"]).strip().upper().replace("–", "-")
            if letter in ("P", "NP"):
                continue  # «зачтено/не зачтено» в GPA не учитываются
            if letter not in by_letter:
                raise CampusError(t("cm.gpa.letter_unknown", letter=letter))
            points = by_letter[letter]
        elif c.get("percent") not in (None, ""):
            found = letter_for(float(c["percent"]))
            if found is None:
                raise CampusError(t("cm.gpa.range"))
            points = found["points"]
        else:
            points = float(c.get("points") or 0)
        if not 0 <= points <= 4:
            raise CampusError(t("cm.gpa.points_range"))
        rows.append((points, credits))
    total_credits = sum(cr for _, cr in rows)
    if total_credits <= 0:
        raise CampusError(t("cm.gpa.no_credits"))
    value = sum(p * cr for p, cr in rows) / total_credits
    return {"gpa": round(value, 2), "credits": total_credits, "source": {"title": g["source_title"], "url": g["source_url"]},
            "letter_scale_loaded": bool(g["letter_scale"]), "transfer": g.get("transfer", []), "transfer_note": g.get("transfer_note", ""),
            "note": g.get("gpa_note", "")}


# ------------------------------------------------------------------ Обратный отсчёт


def countdown(course: str, today: date | None = None) -> list[dict]:
    """Сколько дней до рубежного контроля, сессии и каникул — по академическому календарю."""
    from backend.app.university import calendar as acal

    today = today or date.today()
    cal = acal.calendar_for(today)
    if cal is None:
        return []
    out = []
    for kind in ("midterm", "session", "holidays"):
        for e in cal.events:
            if e.kind != kind or e.end < today:
                continue
            if course and course not in e.courses:
                continue
            if not course and not acal.is_general(e) and kind != "holidays":
                continue
            ongoing = e.start <= today <= e.end
            out.append({"kind": kind, "label": t(f"cm.countdown.{kind}"), "title": e.title, "start": e.start.isoformat(),
                        "dates": e.dates_label(), "days": 0 if ongoing else (e.start - today).days, "ongoing": ongoing,
                        "emoji": e.emoji, "source_url": e.source_url})
            break
    return sorted(out, key=lambda x: x["days"])


# ------------------------------------------------------------------ Разбор силлабуса

SYLLABUS_HINTS = re.compile(r"силлабус|syllabus|рубежн\w*|midterm|оценивани\w*|grading|\b\d{1,2}\s*(?:-?я\s*)?недел\w*|week\s*\d|"
                            r"политика\s+курса|course\s+policy|итоговый\s+контроль|final\s+exam|пересдач\w*|посещаемост\w*|attendance",
                            re.IGNORECASE)
WEIGHT_RE = re.compile(r"(\d{1,3})\s*%")
WEEK_RE = re.compile(r"\b(\d{1,2})\s*(?:-?(?:я|й|ая|ой))?\s*недел\w*|\bweek\s*(\d{1,2})", re.IGNORECASE)
DEADLINE_WORDS = re.compile(r"сда\w*|срок|дедлайн|deadline|due|контрольн\w*|рубежн\w*|midterm|экзамен\w*|exam|защит\w*|"
                            r"лабораторн\w*|lab\b|эссе|essay|проект\w*|project|quiz|тест\w*|presentation|презентац\w*", re.IGNORECASE)


def looks_like_syllabus(text: str) -> bool:
    return len(set(m.group(0).lower()[:6] for m in SYLLABUS_HINTS.finditer(text or ""))) >= 2


def syllabus_rules(text: str, today: date) -> SyllabusCard:
    """Разбор без ИИ: строки со сроками, весами и правилами. Только то, что есть в тексте."""
    card = SyllabusCard()
    if not text.strip():
        card.missing.append(t("cm.syllabus.no_text"))
        return card
    lines = [ln.strip(" •-–—\t") for ln in re.split(r"[\n;]+", text) if ln.strip()]
    first = lines[0] if lines else ""
    m = re.search(r"(?:дисциплин\w*|курс|course|предмет)\s*[:«\"]\s*([^»\"\n]{3,80})", text, re.IGNORECASE)
    card.course = (m.group(1) if m else (first if len(first) <= 80 and not WEIGHT_RE.search(first) else "")).strip(" «»\"")
    for line in lines:
        low = line.lower()
        weight = WEIGHT_RE.search(line)
        if re.search(r"пересда\w*|retake|fx", low):
            card.retake_rules.append(line[:300])
            continue
        if re.search(r"пропуск\w*|посещ\w*|опоздан\w*|attendance|absence|late", low):
            card.absence_rules.append(line[:300])
            continue
        if re.search(r"chatgpt|нейросет\w*|\bии\b|\bai\b|искусственн\w+\s+интеллект", low):
            card.ai_policy.append(line[:300])
            continue
        phrase, iso = find_deadline(line, today)
        week = WEEK_RE.search(line)
        if DEADLINE_WORDS.search(line) and (iso or week):
            title = line
            for part in (phrase, week.group(0) if week else ""):
                if part:
                    title = title.replace(part, " ")
            title = WEIGHT_RE.sub(" ", title)
            title = " ".join(title.split()).strip(" ,.:-—()")
            title = re.sub(r"[\s,:—-]*(?:сдать\s+|сдача\s+)?(?:до|к|deadline|due)$", "", title, flags=re.IGNORECASE).strip(" ,.:-—()") or line
            card.deadlines.append(SyllabusDeadline(title=title[:160], date=iso, when_text=(phrase or (week.group(0) if week else "")),
                                                   weight=weight.group(0) if weight else ""))
        elif weight:
            card.grading.append(line[:200])
    if not card.deadlines:
        card.missing.append(t("cm.syllabus.no_deadlines"))
    if not card.grading:
        card.missing.append(t("cm.syllabus.no_grading"))
    if not card.retake_rules:
        card.missing.append(t("cm.syllabus.no_retake"))
    return card


def syllabus_html(card: SyllabusCard) -> str:
    import html

    e = html.escape
    lines = [f"📚 <b>{e(t('cm.syllabus.title'))}</b>" + (f" · {e(card.course)}" if card.course else ""), ""]
    if card.deadlines:
        lines.append(f"<b>{e(t('cm.syllabus.deadlines'))}</b>")
        for d in card.deadlines[:15]:
            when = d.date or d.when_text
            lines.append(f"• {e(d.title)}" + (f" — {e(when)}" if when else "") + (f" ({e(d.weight)})" if d.weight else ""))
    for key, items in (("cm.syllabus.grading", card.grading), ("cm.syllabus.retake", card.retake_rules),
                       ("cm.syllabus.absence", card.absence_rules), ("cm.syllabus.ai", card.ai_policy)):
        if items:
            lines += ["", f"<b>{e(t(key))}</b>"] + [f"• {e(x)}" for x in items[:8]]
    if card.missing:
        lines += ["", f"⚠️ {e(t('cm.syllabus.missing'))}"] + [f"• {e(x)}" for x in card.missing[:6]]
    lines += ["", f"<i>{e(t('cm.syllabus.disclaimer'))}</i>"]
    return "\n".join(lines)


def syllabus_to_plan(user_id: int, card: SyllabusCard, subject: str = "") -> int:
    """Кнопка «Поставить напоминания»: дедлайны с датой → задачи плана (это и есть подтверждение пользователя)."""
    from backend.app.services import planner

    added = 0
    for d in card.deadlines:
        if d.date:
            planner.create_task(user_id, d.title, due_date=d.date, subject=(subject or card.course)[:64], source="syllabus",
                                source_ref=f"{card.course[:30]}:{d.title[:30]}", priority=2 if d.weight else 1,
                                note=t("cm.syllabus.weight_note", weight=d.weight) if d.weight else "")
            added += 1
    return added


# ------------------------------------------------------------------ Чек-лист «Мой курс»


def checklist(user_id: int, course: str) -> dict:
    from backend.app.university.navigator import navigator
    from backend.app.university.reference import nav_course

    data = navigator()["courses"].get(nav_course(course or "1"))
    if data is None:
        raise CampusError(t("cm.checklist.no_course"))
    with get_sessionmaker()() as session:
        marks = {m.item_key for m in session.scalars(select(ChecklistMark).where(ChecklistMark.user_id == user_id, ChecklistMark.course == course))}
    items = [{"key": f"i{i}", "title": item["title"], "text": item["text"], "source": item.get("source", ""), "done": f"i{i}" in marks}
             for i, item in enumerate(data["items"])]
    done = sum(x["done"] for x in items)
    return {"course": course, "title": data["title"], "items": items, "done": done, "total": len(items),
            "label": t("cm.checklist.progress", course=data["title"], done=done, total=len(items))}


def checklist_mark(user_id: int, course: str, key: str, done: bool) -> dict:
    valid = {x["key"] for x in checklist(user_id, course)["items"]}
    if key not in valid:
        raise CampusError(t("cm.checklist.no_item"))
    with get_sessionmaker()() as session:
        row = session.scalar(select(ChecklistMark).where(ChecklistMark.user_id == user_id, ChecklistMark.course == course, ChecklistMark.item_key == key))
        if done and row is None:
            session.add(ChecklistMark(user_id=user_id, course=course, item_key=key))
        elif not done and row is not None:
            session.delete(row)
        session.commit()
    return checklist(user_id, course)


# ------------------------------------------------------------------ Помощник обращений


@lru_cache
def appeals() -> dict:
    return load_json("appeals.json")


def appeal_types() -> list[dict]:
    from backend.app.university.catalog import get_service

    out = []
    for a in appeals()["types"]:
        service = get_service(a.get("service", ""))
        out.append({**{k: v for k, v in a.items() if k != "template"},
                    "contacts": service["contacts"] if service else [], "service_title": service["title"] if service else ""})
    return out


def appeal_draft(type_id: str, fields: dict) -> dict:
    """Черновик обращения. Бот готовит текст, отправляет человек сам. Кризисные сигналы — контакты помощи."""
    from backend.app.university import crisis

    item = next((a for a in appeals()["types"] if a["id"] == type_id), None)
    if item is None:
        raise CampusError(t("cm.appeal.unknown"))
    values = {f["id"]: " ".join(str(fields.get(f["id"], "")).split())[:1500] or "________" for f in item["fields"]}
    text = item["template"].format(**values)
    level = crisis.detect(" ".join(str(v) for v in fields.values()))
    return {"text": mask_sensitive(text), "where": item["where"], "deadline": item["deadline"], "tips": item["tips"],
            "source_title": item["source_title"], "source_url": item["source_url"], "crisis": crisis.support_card(level).model_dump() if level else None}


# ------------------------------------------------------------------ Квиз первокурсника


@lru_cache
def quiz() -> dict:
    return load_json("quiz.json")


def quiz_questions() -> dict:
    data = quiz()
    return {"source_title": data["source_title"], "source_url": data["source_url"], "questions": data["questions"]}


def quiz_score(user_id: int, score: int) -> dict:
    from backend.app.services import prefs

    total = len(quiz()["questions"])
    score = max(0, min(total, int(score)))
    best = max(score, int(prefs.get(user_id, "quiz.best", "0") or 0))
    prefs.set_value(user_id, "quiz.best", str(best))
    return {"score": score, "best": best, "total": total}


# ------------------------------------------------------------------ Значки (только положительные, видны только себе)


def badges(user_id: int) -> list[dict]:
    from backend.app.db.models_campus import HubMember, Post
    from backend.app.services import prefs

    with get_sessionmaker()() as session:
        checks = session.scalar(select(func.count()).where(Check.user_id == user_id, Check.mode == "pravda")) or 0
        agreements_done = session.scalar(select(func.count()).select_from(Agreement).where(
            (Agreement.creator_id == user_id) | (Agreement.counterparty_id == user_id), Agreement.status == "done")) or 0
        tasks_done = session.scalar(select(func.count()).select_from(Task).where(Task.user_id == user_id, Task.status == "done")) or 0
        focus = session.scalar(select(func.count()).select_from(FocusSession).where(FocusSession.user_id == user_id, FocusSession.finished.is_(True))) or 0
        user = repo.get_user(session, user_id)
        mentor = session.scalar(select(func.count()).select_from(HubMember).where(HubMember.user_id == user_id, HubMember.role == "mentor")) or 0
        helpful = session.scalar(select(func.coalesce(func.sum(Post.score), 0)).where(
            Post.author_hash == __import__("backend.app.services.anon", fromlist=["author_hash"]).author_hash(user_id),
            Post.kind.in_(("senior_a", "faq")))) or 0
    quiz_best = int(prefs.get(user_id, "quiz.best", "0") or 0)
    defs = [
        ("checker", "🔎", checks >= 10, checks, 10),
        ("word", "🤝", agreements_done >= 3, agreements_done, 3),
        ("doer", "✅", tasks_done >= 20, tasks_done, 20),
        ("focus", "🍅", focus >= 10, focus, 10),
        ("streak", "🔥", (user.streak if user else 0) >= 7, user.streak if user else 0, 7),
        ("quiz", "🎓", quiz_best >= 8, quiz_best, 8),
        ("mentor", "🧭", mentor > 0, mentor, 1),
        ("helper", "💡", helpful >= 5, helpful, 5),
    ]
    return [{"id": key, "emoji": emoji, "title": t(f"cm.badge.{key}"), "hint": t(f"cm.badge.{key}.hint"), "earned": bool(earned),
             "progress": [min(value, need), need]} for key, emoji, earned, value, need in defs]


# ------------------------------------------------------------------ Капсула времени


def capsule_date(course: str, today: date | None = None) -> str:
    """«Письмо себе» приходит в конце семестра — на следующий день после экзаменационной сессии твоего курса
    (по академическому календарю). Календаря нет — через 4 месяца."""
    from backend.app.university.calendar import next_events

    today = today or date.today()
    sessions = next_events(course if course in ("1", "2", "3", "4") else "1", today, limit=3, kinds={"session"})
    if sessions:
        return (sessions[0].end + timedelta(days=1)).isoformat()
    return (today + timedelta(days=120)).isoformat()


def capsule_create(user_id: int, course: str, text: str) -> dict:
    text = (text or "").strip()
    if len(text) < 10:
        raise CampusError(t("cm.capsule.short"))
    deliver = capsule_date(course)
    with get_sessionmaker()() as session:
        if session.scalar(select(func.count()).select_from(Capsule).where(Capsule.user_id == user_id, Capsule.delivered.is_(False))) >= 3:
            raise CampusError(t("cm.capsule.too_many"))
        item = Capsule(user_id=user_id, text=text[:4000], deliver_on=deliver)
        session.add(item)
        session.commit()
        return {"id": item.id, "deliver_on": deliver}


def capsule_list(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        # Текст до срока не показываем даже автору — это и есть «капсула».
        return [{"id": c.id, "deliver_on": c.deliver_on, "delivered": c.delivered, "created_at": iso_utc(c.created_at),
                 "text": c.text if c.delivered else ""} for c in session.scalars(select(Capsule).where(Capsule.user_id == user_id))]


def capsule_delete(user_id: int, capsule_id: int) -> None:
    with get_sessionmaker()() as session:
        item = session.get(Capsule, capsule_id)
        if item is None or item.user_id != user_id:
            raise CampusError(t("cm.capsule.not_found"))
        session.delete(item)
        session.commit()


def capsules_due(today: date) -> list[tuple[int, str]]:
    import html

    out = []
    with get_sessionmaker()() as session:
        for c in session.scalars(select(Capsule).where(Capsule.delivered.is_(False), Capsule.deliver_on <= today.isoformat())):
            c.delivered = True
            out.append((c.user_id, f"💌 <b>{html.escape(t('cm.capsule.arrived'))}</b>\n\n{html.escape(c.text)}"))
        session.commit()
    return out


# ------------------------------------------------------------------ Карьерный трек


def career() -> dict:
    from backend.app.university.catalog import get_service

    service = get_service("career") or {}
    return {"service": service, "page_url": "https://iitu.edu.kz/ru/for-students/career-center/",
            "note": t("cm.career.note")}


RESUME_SECTIONS = {
    "contacts": r"@|e-?mail|телефон|\+7|linkedin|telegram",
    "education": r"образован|education|университет|университ|iitu|муит|бакалавр",
    "skills": r"навык|skills|стек|python|java|sql|javascript|figma|excel",
    "projects": r"проект|project|github|pet",
    "experience": r"опыт|experience|стажир|intern|работ",
    "languages": r"язык|english|англ|ielts|казах|русск",
}


def resume_rules(text: str) -> ResumeReview:
    low = (text or "").lower()
    found = [k for k, rx in RESUME_SECTIONS.items() if re.search(rx, low)]
    missing = [t(f"cm.resume.section.{k}") for k in RESUME_SECTIONS if k not in found]
    improve = []
    if len(text) > 3500:
        improve.append(t("cm.resume.too_long"))
    if not re.search(r"\d+\s*%|\d+\s*(пользовател|users|человек|раз)", low):
        improve.append(t("cm.resume.numbers"))
    if "github" not in low and "portfolio" not in low and "портфолио" not in low:
        improve.append(t("cm.resume.portfolio"))
    improve.append(t("cm.resume.verbs"))
    strengths = [t(f"cm.resume.section.{k}") for k in found][:4]
    return ResumeReview(summary=t("cm.resume.summary", found=len(found), total=len(RESUME_SECTIONS)),
                        strengths=[t("cm.resume.has", what=s) for s in strengths], improve=improve, missing_sections=missing)


# ------------------------------------------------------------------ Рейтинг групп


def group_rating(days: int = 30) -> list[dict]:
    """Соревнование потоков на основе индекса чата: кто чаще проверяет слухи и держит договорённости.

    Участвуют только группы, которые сами включили рейтинг (/reiting). Людей не оцениваем.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    with get_sessionmaker()() as session:
        for chat in session.scalars(select(Chat).where(Chat.rating_opt_in.is_(True), Chat.active.is_(True))):
            stats = repo.chat_stats(session, chat.id, since)
            kept = session.scalar(select(func.count()).select_from(Agreement).where(Agreement.chat_id == chat.id, Agreement.status == "done",
                                                                                Agreement.created_at >= since)) or 0
            score = stats["total"] * 2 + stats["votes"] + kept * 5
            out.append({"title": chat.title or t("cm.rating.group"), "checks": stats["total"], "votes": stats["votes"], "kept": kept, "score": score})
    out.sort(key=lambda x: x["score"], reverse=True)
    return [{**x, "place": i + 1} for i, x in enumerate(out[:30])]


def set_rating_opt_in(chat_id: int, title: str, chat_type: str, on: bool) -> None:
    with get_sessionmaker()() as session:
        chat = repo.upsert_chat(session, chat_id, title, chat_type)
        chat.rating_opt_in = on
        session.commit()


# ------------------------------------------------------------------ Отчёт по договорённости


def agreement_report(code: str, user_id: int) -> dict:
    """Кто подтвердил договорённость, кто отказался, кто ещё не ответил. Только для автора."""
    from backend.app.services import agreements as agreements_service
    from backend.app.services import circles as circles_service

    agreement = agreements_service.get(code)
    if agreement is None:
        raise CampusError(t("agr.err.not_found"))
    if agreement.creator_id != user_id:
        raise CampusError(t("cm.report.only_creator"))
    people = agreements_service.participants(agreement)
    if not agreement.multi and agreement.counterparty_id:
        people = [{"user_id": agreement.counterparty_id, "name": agreement.counterparty_name, "accepted": agreement.status in ("confirmed", "done")}]
    answered = {p["user_id"] for p in people}
    waiting: list[str] = []
    if agreement.circle_id:
        try:
            info = circles_service.info(agreement.circle_id, user_id)
            waiting = [m["name"] for m in info.get("members", []) if m["user_id"] not in answered and m["user_id"] != user_id]
        except circles_service.CircleError:
            waiting = []
    return {"code": code, "confirmed": [p["name"] for p in people if p["accepted"]], "declined": [p["name"] for p in people if not p["accepted"]],
            "waiting": waiting, "known_members": bool(agreement.circle_id), "status": agreement.status}


# ------------------------------------------------------------------ удаление данных пользователя


def delete_user(session, user_id: int) -> None:
    session.execute(delete(Capsule).where(Capsule.user_id == user_id))
    session.execute(delete(ChecklistMark).where(ChecklistMark.user_id == user_id))


def get_user_course(user_id: int) -> str:
    with get_sessionmaker()() as session:
        user = session.scalar(select(User).where(User.telegram_id == user_id))
        return user.course if user and user.role == "student" else ""
