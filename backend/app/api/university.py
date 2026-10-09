"""API университетских разделов Mini App (МУИТ).

GET   /api/iitu/reference   — курсы, факультеты, программы, кафедры, языки (для онбординга)
PATCH /api/me/profile       — роль, курс, факультет, программа, кафедра, язык
GET   /api/home             — главная: профиль, «важно на этой неделе», последние новости
GET   /api/news             — лента новостей (кэш; при недоступности источника — с пометкой)
GET   /api/navigator        — «Что тебя ждёт» для курса
GET   /api/calendar         — события академкалендаря для курса на N дней
GET   /api/services         — каталог «куда идти»
GET   /api/about            — цифры для экрана «О боте»
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db import repo
from backend.app.db.base import get_db
from backend.app.modes.registry import MODES
from backend.app.rag.store import get_store
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.university import calendar, catalog, news, reference
from backend.app.university.calendar import academic_year_start
from backend.app.university.navigator import course_block, course_ids, year_changes

router = APIRouter(prefix="/api")


@router.get("/iitu/reference")
def iitu_reference() -> dict:
    return {
        "courses": reference.courses(), "languages": reference.languages(), "departments": reference.departments(),
        "faculties": [{"id": f["id"], "short": f["short"], "title": f["title"], "url": f["url"], "programs": f["programs"]}
                      for f in reference.faculties()],
    }


class ProfileIn(BaseModel):
    role: str
    course: str = ""
    faculty: str = ""
    program: str = ""
    department: str = ""
    ui_lang: str = "ru"


@router.patch("/me/profile")
def update_profile(body: ProfileIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    checks = (("role", body.role), ("course", body.course), ("faculty", body.faculty), ("program", body.program),
              ("department", body.department), ("lang", body.ui_lang))
    bad = [kind for kind, value in checks if not reference.is_valid(kind, value)]
    if bad or not body.role:
        raise HTTPException(status_code=422, detail="Неверные значения профиля: " + ", ".join(bad or ["role"]))
    lang_ready = next((lang["ready"] for lang in reference.languages() if lang["id"] == body.ui_lang), False)
    user = repo.upsert_user(db, current.user.id, current.user.first_name, current.user.language_code)
    old_course = user.course
    repo.set_profile(db, user, academic_year=academic_year_start(date.today()), role=body.role, course=body.course,
                     faculty=body.faculty, program=body.program, department=body.department,
                     ui_lang=body.ui_lang if lang_ready else "ru")
    changes = year_changes(user.course) if user.role == "student" and user.course and user.course != old_course else []
    return {**profile_dict(user), "changes": changes}


def profile_dict(user) -> dict:
    return {
        "role": user.role, "course": user.course, "faculty": user.faculty, "program": user.program,
        "department": user.department, "ui_lang": user.ui_lang, "onboarded": user.onboarded,
        "profile_label": reference.profile_label(user.role, user.course, user.faculty, user.program, user.department),
        "news_subscribed": user.news_subscribed, "calendar_reminders": user.calendar_reminders,
    }


@router.get("/home")
def home(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    user = repo.upsert_user(db, current.user.id, current.user.first_name, current.user.language_code)
    today = date.today()
    course = user.course if user.role == "student" else ""

    def relevant(e) -> bool:
        # Без курса показываем общее для большинства (1–3 курс и праздники), а не узкие события выпускников.
        if e.kind in ("study", "holidays") or e.id.startswith("g_ext"):
            return False
        return bool(course) or calendar.is_general(e)

    week = [e.public() for e in calendar.events_for(course, today, days_ahead=7) if relevant(e)]
    upcoming = [] if week else [e.public() for e in calendar.next_events(
        course, today, limit=8, kinds={"midterm", "session", "fx", "registration", "thesis", "final", "practice", "start"}) if relevant(e)][:2]
    feed = news.list_news(3)
    cal = calendar.calendar_for(today)
    return {
        "profile": profile_dict(user), "week": week, "upcoming": upcoming, "news": feed["items"],
        "news_stale": feed["stale"], "news_updated_at": feed["updated_at"],
        "calendar": {"year": cal.academic_year, "checked": cal.checked, "url": cal.page_url} if cal else None,
    }


@router.get("/news")
def news_list(limit: int = 20, source: str = "", current: CurrentUser = Depends(get_current_user)) -> dict:
    if source not in ("", "site", "telegram"):
        raise HTTPException(status_code=400, detail="Неизвестный источник новостей.")
    return news.list_news(max(1, min(limit, 50)), source)


@router.get("/navigator")
def navigator(course: str = "", current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not course:
        user = repo.upsert_user(db, current.user.id, current.user.first_name, current.user.language_code)
        course = user.course or "1"
    block = course_block(course, date.today())
    if block is None:
        raise HTTPException(status_code=404, detail="Нет навигатора для этого курса.")
    from backend.app.core.features import enabled

    extra = {}
    if enabled("ask_senior"):
        # «Спроси старшекурсника»: лучшие ответы после модерации владельцем — в навигатор по курсам.
        from backend.app.services.community import promoted_for_course

        extra["senior_answers"] = promoted_for_course(course)
    return {**block, **extra, "courses": [{"id": c, "title": reference.course_title(c)} for c in course_ids()]}  # noqa: E501


@router.get("/calendar")
def calendar_events(course: str = "", days: int = 30, current: CurrentUser = Depends(get_current_user)) -> dict:
    today = date.today()
    cal = calendar.calendar_for(today)
    if cal is None:
        return {"events": [], "loaded": False, "url": calendar.CALENDAR_PAGE}
    events = [e.public() for e in calendar.events_for(course, today, max(1, min(days, 366)))]
    return {"events": events, "loaded": True, "year": cal.academic_year, "checked": cal.checked, "url": cal.page_url}


@router.get("/services")
def services(current: CurrentUser = Depends(get_current_user)) -> dict:
    return {"services": catalog.services(), "checked": catalog.catalog().get("checked", "")}


@router.get("/about")
def about() -> dict:
    sources = [s for s in get_store().sources if not s.is_demo]
    return {
        "official_sources": len(sources), "modes": len(MODES), "services": len(catalog.services()),
        "checked": max((s.checked or s.date for s in sources), default=""),
        "source_pages": sorted({s.url for s in sources if s.url.startswith("https://iitu.edu.kz")}),
    }
