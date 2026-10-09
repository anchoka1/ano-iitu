# Служебные скрипты

| Скрипт | Что делает |
|---|---|
| `init_db.py` | Создаёт базу и таблицы (запускается автоматически из `run.*`) |
| `seed_demo.py` | Наполняет приложение примерами: проверки, договорённость, тренировка, семья, чат |
| `fetch_source.py` | Загружает официальный текст (adilet.zan.kz, egov.kz…) в `data/sources/` |

Запуск из папки проекта:

```
.venv\Scripts\python scripts\seed_demo.py
.venv\Scripts\python scripts\fetch_source.py https://adilet.zan.kz/rus/docs/... --modes prava
```

Оценка качества — в папке `evals/`.
