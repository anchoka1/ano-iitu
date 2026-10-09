"""Оценка качества «Вердикта» на наборе тестовых сообщений.

Запуск (из папки проекта):
    .venv\\Scripts\\python evals\\run_eval.py            # режим из .env (демо или реальный ИИ)
    .venv\\Scripts\\python evals\\run_eval.py --mock     # принудительно демо-режим
    .venv\\Scripts\\python evals\\run_eval.py --mode razvod

Что считаем:
  - точность (accuracy): доля сообщений, где статус совпал с ожидаемым;
  - честные «не знаю»: из сообщений, где правильный ответ ⚪, сколько бот так и ответил;
  - ложная уверенность: бот уверенно (🟢/🔴) ответил там, где правильно ⚪ — самая неприятная ошибка;
  - опасные промахи: ожидалось 🔴, а бот сказал 🟢.
С реальным ИИ каждый прогон стоит денег (≈ 58 запросов) — запускайте осознанно.
Результат сохраняется в evals/results/ (папка в .gitignore).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Оценка качества проверок")
    parser.add_argument("--mock", action="store_true", help="демо-режим без ИИ")
    parser.add_argument("--mode", help="только один режим (razvod, pravda, ...)")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    # Отдельная временная база — оценка не засоряет вашу историю.
    tmp = Path(tempfile.mkdtemp())
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp / 'eval.db').as_posix()}"
    os.environ["CHECKS_PER_MINUTE"] = "100000"
    os.environ["CHECKS_PER_DAY"] = "100000"
    os.environ["LINK_CHECK_ONLINE"] = "false"
    if args.mock:
        os.environ["MOCK_LLM"] = "true"

    from backend.app.core.config import get_settings
    from backend.app.core.engine import CheckInput, EngineError, get_verdict_engine
    from backend.app.db.init_db import init_db

    init_db()
    settings = get_settings()
    engine = get_verdict_engine()
    print(f"Режим: {'ДЕМО (без ИИ)' if settings.use_mock_llm else 'реальный ИИ: ' + settings.llm_provider + ' / ' + settings.llm_model_name}")

    items = [json.loads(line) for line in (ROOT / "evals" / "messages.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.mode:
        items = [i for i in items if i["mode"] == args.mode]

    rows = []
    for n, item in enumerate(items, 1):
        try:
            out = await engine.check(CheckInput(mode=item["mode"], text=item["text"], user_id=1, save=False))
            got = out.card.status
        except EngineError as exc:
            got = f"error: {exc}"
        ok = got == item["expected"]
        rows.append({**item, "got": got, "ok": ok})
        print(f"[{n:2}/{len(items)}] {'✔' if ok else '✘'} {item['id']:4} {item['mode']:7} ожидалось {item['expected']:8} получено {got}")

    total = len(rows)
    correct = sum(r["ok"] for r in rows)
    expected_unknown = [r for r in rows if r["expected"] == "unknown"]
    honest = sum(r["got"] == "unknown" for r in expected_unknown)
    overconfident = sum(r["got"] in ("green", "red") for r in expected_unknown)
    dangerous = sum(r["expected"] == "red" and r["got"] == "green" for r in rows)

    by_mode: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_mode[r["mode"]]["total"] += 1
        by_mode[r["mode"]]["ok"] += r["ok"]

    print("\n================ ИТОГ ================")
    print(f"Точность: {correct}/{total} = {correct / total:.0%}")
    if expected_unknown:
        print(f"Честные «не знаю»: {honest}/{len(expected_unknown)} = {honest / len(expected_unknown):.0%}")
        print(f"Ложная уверенность (🟢/🔴 вместо ⚪): {overconfident}")
    print(f"Опасные промахи (🔴 → 🟢): {dangerous}")
    for mode, c in sorted(by_mode.items()):
        print(f"  {mode:8} {c['ok']}/{c['total']}")

    results_dir = ROOT / "evals" / "results"
    results_dir.mkdir(exist_ok=True)
    path = results_dir / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({"mode": "demo" if settings.use_mock_llm else settings.llm_model_name, "accuracy": correct / total,
                                "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПодробности: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
