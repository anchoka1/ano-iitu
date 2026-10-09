"""Справочник МУИТ: курсы, факультеты, программы, кафедры, языки (data/iitu/reference.json)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data" / "iitu"


def load_json(name: str) -> dict:
    path: Path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache
def reference() -> dict:
    return load_json("reference.json")


def courses() -> list[dict]:
    return reference()["courses"]


def course_title(course_id: str) -> str:
    return next((c["title"] for c in courses() if c["id"] == course_id), "")


def faculties() -> list[dict]:
    return reference()["faculties"]


def faculty(faculty_id: str) -> dict | None:
    return next((f for f in faculties() if f["id"] == faculty_id), None)


def is_master(course_id: str) -> bool:
    """Магистратура: общий «master» или трек календаря (m2, m1w26, m2w25, m1w27)."""
    return course_id == "master" or (course_id.startswith("m") and course_id[1:2].isdigit())


def level_of(course_id: str) -> str:
    if is_master(course_id):
        return "master"
    return {"phd": "phd"}.get(course_id, "bachelor")


def nav_course(course_id: str) -> str:
    """Блок навигатора для курса: все треки магистратуры — общий блок «master»."""
    return "master" if is_master(course_id) else course_id


def programs(faculty_id: str, course_id: str = "") -> list[dict]:
    fac = faculty(faculty_id)
    if fac is None:
        return []
    level = level_of(course_id) if course_id else ""
    return [p for p in fac["programs"] if not level or p["level"] == level]


def program_title(code: str) -> str:
    for fac in faculties():
        for p in fac["programs"]:
            if p["code"] == code:
                return p["title"]
    return ""


def departments() -> list[dict]:
    return reference()["departments"]


def department_title(dep_id: str) -> str:
    return next((d["title"] for d in departments() if d["id"] == dep_id), "")


def languages() -> list[dict]:
    return reference()["languages"]


def is_valid(kind: str, value: str) -> bool:
    """Проверка значений, пришедших из кнопок или Mini App (не доверяем вводу)."""
    if value == "":
        return True
    if kind == "course":
        return any(c["id"] == value for c in courses())
    if kind == "faculty":
        return faculty(value) is not None
    if kind == "program":
        return bool(program_title(value))
    if kind == "department":
        return any(d["id"] == value for d in departments())
    if kind == "role":
        return value in ("student", "teacher")
    if kind == "lang":
        return any(lang["id"] == value for lang in languages())
    return False


def profile_label(role: str, course: str, faculty_id: str, program: str, department: str) -> str:
    """Короткая подпись профиля: «Студент · 1 курс · ФКТК» или «Преподаватель · Кибербезопасность»."""
    if role == "teacher":
        parts = ["Преподаватель", department_title(department)]
    elif role == "student":
        fac = faculty(faculty_id)
        parts = ["Студент", course_title(course), fac["short"] if fac else "", program_title(program)]
    else:
        return ""
    return " · ".join(p for p in parts if p)
