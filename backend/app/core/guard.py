"""Ограничения: лимиты запросов и запрет слежки.

1. Rate limiting («ограничение частоты»): не больше N проверок в минуту
   и M в сутки на человека. Защищает от спама и от больших счетов за ИИ.
   Храним в памяти процесса — после перезапуска счётчики обнуляются.
   Для учебного проекта на одном компьютере этого достаточно.

2. Запрет слежки: «Вердикт» не помогает следить за конкретными людьми
   (узнать, где человек, прочитать чужую переписку, вычислить по номеру).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

SURVEILLANCE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(узнать|вычислить|найти|определить|отследить)\w*\s+(где\s+(находится|живёт|живет)|местоположени\w*|геолокаци\w*|адрес\w*)\s+(человек|него|неё|нее|мужа|жены|девушки|парня|бывш)",
        r"(отследить|следить|слежк\w*|шпионить)\s+(за\s+)?(человеком|мужем|женой|девушкой|парнем|бывш\w*|ним|ней|телефоном)",
        r"(прочитать|взломать|посмотреть)\s+(чуж\w+\s+)?(переписк\w*|сообщени\w*|whatsapp|ватсап|телеграм)\s+(мужа|жены|девушки|парня|другого|человека|бывш)",
        r"(пробить|вычислить|найти)\s+(человека\s+)?по\s+(номеру|иин|фото)",
    )
)


def is_surveillance_request(text: str) -> bool:
    return any(p.search(text) for p in SURVEILLANCE_PATTERNS)


class RateLimiter:
    """Скользящее окно: помним время последних запросов каждого пользователя."""

    def __init__(self, per_minute: int, per_day: int) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        # deque — «очередь», из которой удобно убирать старые записи слева.
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def check(self, user_id: int, now: float | None = None) -> str | None:
        """None — можно; иначе текст ошибки для пользователя."""
        now = time.time() if now is None else now
        events = self._events[user_id]
        while events and now - events[0] > 86400:
            events.popleft()
        last_minute = sum(1 for t in events if now - t < 60)
        if last_minute >= self.per_minute:
            return "Слишком много проверок подряд. Подождите минуту и попробуйте снова."
        if len(events) >= self.per_day:
            return "Дневной лимит проверок исчерпан. Попробуйте завтра."
        events.append(now)
        return None

    def reset(self) -> None:
        self._events.clear()


class ApiThrottle:
    """Общий лимит запросов к API от одного пользователя: N в минуту (защита от перебора id и спама).

    Отдельно от лимита проверок: это «предохранитель» для всех адресов, а не только для ИИ.
    """

    def __init__(self) -> None:
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int, per_minute: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        events = self._events[user_id]
        while events and now - events[0] > 60:
            events.popleft()
        if len(events) >= per_minute:
            return False
        events.append(now)
        return True

    def reset(self) -> None:
        self._events.clear()


api_throttle = ApiThrottle()
