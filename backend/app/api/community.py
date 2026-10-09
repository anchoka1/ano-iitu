"""API контента сообщества (один формат для всех видов) и записи на консультацию.

GET    /api/posts?kind=&hub_id=&parent_id=&order=new|top   — лента (одобренное + мои на модерации)
POST   /api/posts {kind, title, body, data, hub_id, parent_id}
GET    /api/posts/mine
GET    /api/posts/{id}, DELETE /api/posts/{id} (своё)
POST   /api/posts/{id}/vote {value}, /report {reason}, /ack, /going {on}, /contact {text}, /hide (автор доски вопросов)
GET    /api/posts/{id}/acks          — отчёт «кто подтвердил» (автору)
POST   /api/posts/{id}/photo         — фото к потеряшке (base64, до 2 МБ, хранится зашифрованным); GET /api/posts/{id}/photo
GET    /api/slots?owner_id=&hub_id=, POST /api/slots, POST /api/slots/{id}/book {cancel}, DELETE /api/slots/{id}
GET    /api/reviews/summary?hub_id=&subject=
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.app.core.features import require
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import community, files

router = APIRouter(prefix="/api")
MAX_PHOTO = 2 * 1024 * 1024


def _c(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except community.CommunityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _flag(kind: str) -> None:
    cfg = community.KINDS.get(kind)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Неизвестный раздел.")
    require(cfg["flag"])


class PostIn(BaseModel):
    kind: str
    title: str = Field(default="", max_length=256)
    body: str = Field(default="", max_length=4000)
    data: dict = Field(default_factory=dict)
    hub_id: int | None = None
    parent_id: int | None = None


class VoteIn(BaseModel):
    value: int = Field(default=1, ge=-1, le=1)


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class OnIn(BaseModel):
    on: bool = True


class TextIn(BaseModel):
    text: str = Field(min_length=2, max_length=800)


class PhotoIn(BaseModel):
    file_base64: str
    file_type: str


class SlotsIn(BaseModel):
    starts: list[str] = Field(min_length=1, max_length=30)
    minutes: int = 15
    place: str = Field(default="", max_length=128)
    hub_id: int | None = None


class BookIn(BaseModel):
    cancel: bool = False


@router.get("/posts")
def posts(kind: str, hub_id: int | None = None, parent_id: int | None = None, order: str = "new",
          current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    _flag(kind)
    return _c(community.list_posts, kind, current.user.id, hub_id, parent_id, 100, True, order)


@router.post("/posts")
async def post_create(body: PostIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    _flag(body.kind)
    from backend.app.university import crisis

    post = _c(community.create_post, body.kind, current.user.id, current.user.first_name, body.title, body.body, body.data, body.hub_id, body.parent_id)
    if post.get("pending"):
        await community.announce_pending(post["id"])
    if post.get("crisis"):
        post["support"] = crisis.support_card(post["crisis"]).model_dump()
    return post


@router.get("/posts/mine")
def posts_mine(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return community.my_posts(current.user.id)


@router.get("/posts/{post_id}")
def post_get(post_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return community.get_post(post_id, current.user.id)
    except community.CommunityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/posts/{post_id}")
def post_delete(post_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    _c(community.delete_own, post_id, current.user.id)
    files.delete_ref(f"post:{post_id}")
    return {"deleted": True}


@router.post("/posts/{post_id}/vote")
def post_vote(post_id: int, body: VoteIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _c(community.vote, post_id, current.user.id, body.value)


@router.post("/posts/{post_id}/report")
async def post_report(post_id: int, body: ReasonIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await community.report(post_id, current.user.id, body.reason)
    except community.CommunityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/posts/{post_id}/ack")
def post_ack(post_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _c(community.ack, post_id, current.user.id, current.user.first_name)


@router.get("/posts/{post_id}/acks")
def post_acks(post_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return community.ack_report(post_id, current.user.id)
    except community.CommunityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/posts/{post_id}/going")
def post_going(post_id: int, body: OnIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("events")
    return _c(community.going, post_id, current.user.id, current.user.first_name, body.on)


@router.post("/posts/{post_id}/contact")
async def post_contact(post_id: int, body: TextIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        sent = await community.contact_author(post_id, current.user.id, current.user.first_name, current.user.username, body.text)
    except community.CommunityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"sent": sent}


@router.post("/posts/{post_id}/hide")
def post_hide(post_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    _c(community.hide_by_container_owner, post_id, current.user.id)
    return {"hidden": True}


@router.post("/posts/{post_id}/photo")
def post_photo(post_id: int, body: PhotoIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    """Фото к своему объявлению: тип по содержимому (только картинка), до 2 МБ, хранится зашифрованным
    под случайным именем вне публичной папки; удаляется через FILE_RETENTION_DAYS или вместе с объявлением."""
    from backend.app.api.flows import decode_upload, require_consent
    from backend.app.core import docparse

    post = _c(community.get_post, post_id, current.user.id)
    if not post["mine"] or post["kind"] not in ("lost", "event", "team"):
        raise HTTPException(status_code=403, detail="Фото можно добавить только к своему объявлению.")
    require_consent(current.user.id)
    raw = decode_upload(body.file_base64)
    if len(raw) > MAX_PHOTO:
        raise HTTPException(status_code=413, detail="Фото больше 2 МБ.")
    try:
        kind = docparse.check_upload(raw, ("jpeg", "png", "webp"))
    except docparse.DocError as exc:
        raise HTTPException(status_code=400, detail="Нужна картинка JPEG, PNG или WebP.") from exc
    files.delete_ref(f"post:{post_id}")
    files.save(current.user.id, "post_photo", f"post:{post_id}", raw, docparse.IMAGE_TYPES[kind])
    return {"ok": True}


@router.get("/posts/{post_id}/photo")
def post_photo_get(post_id: int, current: CurrentUser = Depends(get_current_user)) -> Response:
    """Только через проверку прав: объявление одобрено, или оно моё, или я модератор. Прямых ссылок на файл нет."""
    _c(community.get_post, post_id, current.user.id)
    row = files.find(f"post:{post_id}", "post_photo")
    data = files.read(row) if row else None
    if data is None:
        raise HTTPException(status_code=404, detail="Фото нет.")
    return Response(data, media_type=row.mime, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/reviews/summary")
def reviews_summary(hub_id: int | None = None, subject: str = "", current: CurrentUser = Depends(get_current_user)) -> dict:
    require("subject_reviews")
    return community.review_summary(hub_id, subject)


# ------------------------------------------------------------------ консультации


@router.get("/slots")
def slots(owner_id: int | None = None, hub_id: int | None = None, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("consultations")
    return community.list_slots(current.user.id, owner_id, hub_id)


@router.post("/slots")
def slots_create(body: SlotsIn, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("consultations")
    return _c(community.create_slots, current.user.id, current.user.first_name, body.starts, body.minutes, body.place, body.hub_id)


@router.post("/slots/{slot_id}/book")
async def slot_book(slot_id: int, body: BookIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("consultations")
    try:
        return await community.book_slot(slot_id, current.user.id, current.user.first_name, body.cancel)
    except community.CommunityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/slots/{slot_id}")
def slot_delete(slot_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("consultations")
    _c(community.delete_slot, slot_id, current.user.id)
    return {"deleted": True}

