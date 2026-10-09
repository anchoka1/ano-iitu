"""Демо-данные: наполняет приложение примерами, чтобы показать все экраны.

Что создаётся (для пользователя-разработчика id=1, которым вы входите в
браузере в режиме DEV_MODE):
  - 6 проверок в разных режимах (через настоящий движок — демо или ИИ);
  - договорённость от «Марата», которую вы можете подтвердить;
  - завершённая тренировка (иммунитет) и серия дней;
  - семья с участником «Мама (демо)»;
  - групповой чат «Семейный чат (демо)» со статистикой.

Запуск:
    .venv\\Scripts\\python scripts\\seed_demo.py
    .venv\\Scripts\\python scripts\\seed_demo.py --user 123456789   # для своего Telegram id

Свой Telegram id можно узнать у бота @userinfobot. Повторный запуск
добавит ещё одну порцию примеров.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CHECKS_PER_MINUTE", "1000")

SAMPLES = [
    ("razvod", "Kaspi Bank: ваша карта заблокирована! Служба безопасности. Срочно назовите код из SMS: https://kaspi-secure.top/login"),
    ("pravda", "С понедельника вводят штраф за голосовые сообщения в общих чатах WhatsApp! Разошлите всем, пока не удалили!"),
    ("chek", "Постановление об административном штрафе. Сумма 25 000 тенге. Оплатить до 20.10.2026"),
    ("prava", "Магазин отказывается вернуть деньги за бракованный чайник, чек есть"),
    ("pravda", "В Шымкенте с завтрашнего дня бесплатный проезд для всех школьников"),
]
GROUP_ID = -1001234567890


async def main() -> int:
    parser = argparse.ArgumentParser(description="Демо-данные ANO IITU")
    parser.add_argument("--user", type=int, default=1, help="Telegram id (по умолчанию 1 — пользователь режима разработки)")
    args = parser.parse_args()
    me = args.user

    from backend.app.core.engine import CheckInput, get_verdict_engine
    from backend.app.db import repo
    from backend.app.db.base import get_sessionmaker
    from backend.app.db.init_db import init_db
    from backend.app.services import agreements, family, trainer
    from backend.app.services.planner import local_today

    init_db()
    engine = get_verdict_engine()

    print("Проверки…")
    for mode, text in SAMPLES:
        out = await engine.check(CheckInput(mode=mode, text=text, user_id=me, user_name="Разработчик"))
        print(f"  {out.card.emoji} {mode}: {out.card.title}")
    # Пара проверок «в группе» — для экрана «Мои чаты» и индекса
    with get_sessionmaker()() as session:
        repo.upsert_chat(session, GROUP_ID, "Семейный чат (демо)", "supergroup")
    for text in ("Мама, это мой новый номер, срочно нужны деньги, не звони", "Вы выиграли iPhone! Оплатите доставку по ссылке kaspi-prize.online"):
        await engine.check(CheckInput(mode="razvod", text=text, user_id=me, user_name="Разработчик", chat_id=GROUP_ID))

    print("Договорённость от «Марата» (подтвердите её в приложении)…")
    agreement = await agreements.create_agreement("Марат возвращает 20 000 ₸ до 25 октября", 2, "Марат (демо)")
    print(f"  код {agreement.code}")

    print("Тренажёр…")
    session_data = trainer.start(me, "Разработчик", "bank_security")
    await trainer.reply(me, session_data["id"], "А кто вы? Я сам перезвоню в банк")
    final = await trainer.reply(me, session_data["id"], "Нет, код не скажу, это мошенничество")
    print(f"  иммунитет {final['immunity']}%")

    print("Семья…")
    info = family.create(me, "Разработчик")
    family.join(3, "Мама (демо)", info["invite_token"])

    with get_sessionmaker()() as session:
        user = repo.get_user(session, me)
        user.streak = max(user.streak, 3)
        user.last_active_date = local_today().isoformat()
        session.commit()
    print("Готово! Откройте http://localhost:8000")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
