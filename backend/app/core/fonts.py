"""Поиск шрифта с кириллицей для картинок и PDF.

Pillow и fpdf2 рисуют текст шрифтом из файла .ttf. Встроенные шрифты
этих библиотек не знают русских букв, поэтому берём системный шрифт:
Windows — Arial/Segoe UI, macOS — Arial, Linux — DejaVu/Noto.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CANDIDATES = (
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
)


class FontNotFound(Exception):
    pass


@lru_cache
def find_fonts() -> tuple[str, str]:
    """Возвращает (обычный, жирный) шрифт. Жирного нет — используем обычный."""
    for regular, bold in _CANDIDATES:
        if Path(regular).is_file():
            return regular, bold if Path(bold).is_file() else regular
    raise FontNotFound(
        "Не найден шрифт с кириллицей. Установите шрифт DejaVu Sans (Linux: sudo apt install fonts-dejavu)."
    )
