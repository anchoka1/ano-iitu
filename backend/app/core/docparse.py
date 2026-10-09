"""Загруженные файлы: тип по содержимому, лимит размера, извлечение текста из PDF и DOCX.

Правила безопасности:
  - тип определяем по первым байтам («магическим числам»), а не по расширению или MIME из браузера;
  - исполняемые файлы, скрипты, HTML и архивы (кроме DOCX) не принимаем;
  - DOCX — это zip: проверяем, что внутри word/document.xml, и ограничиваем распакованный размер (zip-бомба);
  - содержимое файла никогда не пишется в логи.

PDF без текстового слоя (скан) отдаём картинками страниц — их читает модель со зрением.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from backend.app.core.config import get_settings

IMAGE_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
DOC_TYPES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_DOCX_XML = 30 * 1024 * 1024
MAX_PDF_PAGES = 60


class DocError(Exception):
    """Понятная ошибка для человека. code: too_big | bad_type | unreadable | encrypted | empty | scan."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def max_bytes() -> int:
    return max(1, get_settings().max_upload_mb) * 1024 * 1024


def sniff(raw: bytes) -> str:
    """pdf | docx | jpeg | png | webp | gif — или DocError('bad_type')."""
    head = raw[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = set(z.namelist())
                if "word/document.xml" in names and "[Content_Types].xml" in names and not any(n.lower().endswith((".exe", ".dll", ".js", ".vbs", ".bin")) or "vbaProject" in n for n in names):
                    return "docx"
        except zipfile.BadZipFile:
            pass
    raise DocError("bad_type")


def check_upload(raw: bytes, allowed: tuple[str, ...]) -> str:
    """Размер и тип. Возвращает тип по содержимому."""
    if not raw:
        raise DocError("empty")
    if len(raw) > max_bytes():
        raise DocError("too_big")
    kind = sniff(raw)
    if kind not in allowed:
        raise DocError("bad_type")
    return kind


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise DocError("encrypted")
            except Exception as exc:  # noqa: BLE001 — любой сбой расшифровки = «защищён паролем»
                raise DocError("encrypted") from exc
        parts = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — битая страница не должна ронять весь разбор
                continue
        return _clean("\n".join(parts))
    except DocError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise DocError("unreadable") from exc


def pdf_images(raw: bytes, max_pages: int = 4) -> list[tuple[str, bytes]]:
    """Картинки страниц скана (обычно одна картинка на страницу), переведённые в JPEG."""
    from PIL import Image
    from pypdf import PdfReader

    out: list[tuple[str, bytes]] = []
    try:
        reader = PdfReader(io.BytesIO(raw))
        for page in reader.pages[:max_pages]:
            images = list(page.images)
            if not images:
                continue
            biggest = max(images, key=lambda im: len(im.data))
            img = Image.open(io.BytesIO(biggest.data))
            img = img.convert("RGB")
            img.thumbnail((2000, 2000))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            out.append(("image/jpeg", buf.getvalue()))
    except Exception:  # noqa: BLE001 — не получилось достать картинки = скан не прочитать
        return out
    return out


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(raw: bytes) -> str:
    """Текст абзацев и таблиц DOCX (стандартная библиотека, без внешних зависимостей)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML:
                raise DocError("too_big")
            xml = z.read(info)
    except DocError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise DocError("unreadable") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocError("unreadable") from exc
    body = root.find(f"{_W}body")
    if body is None:
        return ""

    def para(p) -> str:
        return "".join((" " if n.tag == f"{_W}tab" else n.text or "") for n in p.iter() if n.tag in (f"{_W}t", f"{_W}tab"))

    lines: list[str] = []

    def walk(container) -> None:
        for block in container:
            if block.tag == f"{_W}p":
                if (text := para(block)).strip():
                    lines.append(text)
            elif block.tag == f"{_W}tbl":
                for row in block.iter(f"{_W}tr"):
                    cells = [" ".join(para(p) for p in cell.iter(f"{_W}p")).strip() for cell in row.findall(f"{_W}tc")]
                    if any(cells):
                        lines.append(" | ".join(cells))
            elif block.tag == f"{_W}sdt":  # блоки «содержимого» (оглавление, поля) — внутри обычные абзацы
                content = block.find(f"{_W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    return _clean("\n".join(lines))


def extract(raw: bytes, kind: str) -> str:
    if kind == "pdf":
        return pdf_text(raw)
    if kind == "docx":
        return docx_text(raw)
    return ""
