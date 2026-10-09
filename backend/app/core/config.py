"""Настройки приложения.

Суть: все параметры (токен бота, режимы, порт) живут в файле .env или в
переменных окружения. Здесь мы описываем их одним классом, а библиотека
pydantic-settings сама читает .env, приводит типы ("true" -> True,
"8000" -> 8000) и подставляет значения по умолчанию.

Почему так: код нигде не читает os.environ напрямую — все настройки в
одном месте, с типами и пояснениями.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта: этот файл лежит в backend/app/core/config.py,
# parents[0] = core, [1] = app, [2] = backend, [3] = корень проекта.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Все настройки. Имя поля = имя переменной в .env (регистр не важен)."""

    # SettingsConfigDict — «инструкция» для pydantic-settings:
    # откуда читать .env, в какой кодировке и что делать с лишними полями.
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # неизвестные переменные в .env не считаются ошибкой
    )

    # --- режимы ---
    # auto  — демо, пока не вставлен ключ ИИ (LLM_API_KEY); с ключом — реальный ИИ
    # true  — всегда демо (заготовки); false — всегда реальный ИИ
    mock_llm: str = "auto"
    # По умолчанию False: если .env потерялся, проверка подписи НЕ отключится.
    dev_mode: bool = False

    # --- сервер ---
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    # --- Telegram ---
    telegram_bot_token: str = ""
    webapp_url: str = ""
    initdata_max_age_seconds: int = 86400

    # cloudflare — при старте сервера поднять бесплатный HTTPS-туннель и
    # подставить его адрес в WEBAPP_URL автоматически. Пусто — без туннеля.
    tunnel: str = ""

    # Короткое имя Mini App из @BotFather (/newapp) — для ссылок
    # t.me/бот/имя?startapp=... в группах. Пусто — ссылки ведут на бота.
    webapp_short_name: str = ""

    # --- LLM ---
    # gemini | groq | openrouter | openai (любой совместимый, нужен LLM_BASE_URL) | ollama | anthropic
    llm_provider: str = "gemini"
    # Ключ и модель для gemini / groq / openrouter / openai. Модель пустая — берётся по умолчанию.
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    # Запасной сервис (gemini | groq | openrouter): сюда уходят запросы, если основной
    # ответил ошибкой (лимит, нет связи), и фото, если основной их не понимает (groq).
    backup_llm_provider: str = ""
    backup_llm_api_key: str = ""
    backup_llm_model: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    # Насколько глубоко модель «думает»: low | medium | high
    llm_effort: str = "medium"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    # Дневной лимит токенов на одного пользователя (защита от расходов)
    llm_daily_tokens_per_user: int = 200_000

    # --- Лимиты (антиспам) ---
    checks_per_minute: int = 6
    checks_per_day: int = 100

    # --- Анализ ссылок ---
    # Узнавать возраст домена через RDAP (официальный реестр доменов).
    # Саму ссылку при этом НЕ открываем. false — без интернета.
    link_check_online: bool = True

    # --- Расписание ---
    timezone: str = "Asia/Almaty"
    weekly_index_weekday: int = 6  # день недели индекса чата: 0=пн ... 6=вс
    weekly_index_hour: int = 19
    scheduler_enabled: bool = True

    # --- Университет (МУИТ / IITU) ---
    # Лента новостей: официальный сайт и официальный Telegram-канал.
    # Другие источники не добавляем без решения владельца проекта.
    news_site_url: str = "https://iitu.edu.kz/ru/news/"
    news_telegram_channel: str = "iitu_channel"
    news_enabled: bool = True
    news_refresh_minutes: int = 60
    # Сколько новых новостей пересказывать ИИ за одно обновление (экономия лимитов).
    news_summary_per_run: int = 4
    # Сколько новых новостей максимум рассылать подписчикам за одно обновление.
    news_notify_max: int = 3
    # Напоминания академического календаря: за сколько дней до события.
    calendar_remind_days: int = 2

    # --- Новые функции: планер, опросы, хабы, сообщество ---
    # Telegram id владельцев бота через запятую: модерация, подтверждение преподавателей и менторов.
    # Свой id бот подскажет командой /myid.
    owner_ids: str = ""
    # «Соль» для анонимных ответов (хеш вместо id). Пусто — сгенерируется и сохранится в базе.
    anon_salt: str = ""
    # Результаты анонимных опросов не показываются, пока ответов меньше этого числа.
    anon_min_answers: int = 5
    # Слух недели: в какой день (0=пн) и час рассылать подписчикам; сколько разных людей должны были его проверить.
    rumor_weekday: int = 0
    rumor_hour: int = 12
    rumor_min_users: int = 2
    # Утренняя сводка и «Лягушка дня»: час рассылки подписчикам.
    morning_hour: int = 8
    # Светофор недели (пятница) и «Вердикт недели» (воскресенье): час рассылки подписчикам.
    traffic_weekday: int = 4
    traffic_hour: int = 18
    week_verdict_weekday: int = 6
    week_verdict_hour: int = 20
    # Пульс МУИТ и пульс группы: новый вопрос в этот день недели.
    pulse_weekday: int = 1
    pulse_hour: int = 12

    # --- роли и модерация ---
    # Telegram id через запятую. Админ модерирует и назначает модераторов; модератор — только модерирует.
    # OWNER_IDS (выше) — старое имя для админов, тоже работает.
    admin_ids: str = ""
    moderator_ids: str = ""
    # Служебный чат модераторов (id группы, обычно начинается с -100). Пусто — заявки приходят модераторам в личку.
    mod_chat_id: str = ""

    # --- поиск в интернете для режима «Правда» ---
    # tavily | brave | ddg (DuckDuckGo, без ключа) | groq (дорого по токенам) | off. Пусто — выбрать самому:
    # есть SEARCH_API_KEY → tavily; иначе → ddg.
    search_provider: str = ""
    search_api_key: str = ""

    # --- файлы и личные данные ---
    # Ключ шифрования файлов и чувствительных полей (Fernet). Пусто — сервер сам создаст ключ при запуске
    # и допишет его в .env. Потеряете ключ — зашифрованные файлы не прочитать.
    data_encryption_key: str = ""
    max_upload_mb: int = 10
    # Сколько дней хранить загруженные файлы (фото к объявлениям). Потом удаляются автоматически.
    file_retention_days: int = 30
    # Общий лимит запросов к API от одного пользователя в минуту (защита от перебора и спама).
    api_requests_per_minute: int = 120

    # --- база данных ---
    database_url: str = "sqlite:///data/verdikt.db"

    @property
    def resolved_database_url(self) -> str:
        """Адрес базы, где относительный путь отсчитан от корня проекта.

        Зачем: если запустить сервер из другой папки, относительный путь
        "data/verdikt.db" указывал бы не туда. Здесь мы превращаем его
        в абсолютный и заодно создаём папку для файла базы.
        """
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw_path = self.database_url[len(prefix):]
            if raw_path != ":memory:":
                path = Path(raw_path)
                if not path.is_absolute():
                    path = PROJECT_ROOT / path
                path.parent.mkdir(parents=True, exist_ok=True)
                # as_posix() — прямые слеши: SQLAlchemy так надёжнее на Windows
                return prefix + path.as_posix()
        return self.database_url

    @property
    def bot_enabled(self) -> bool:
        """Есть ли токен бота (без проверки, что он правильный)."""
        return bool(self.telegram_bot_token.strip())

    @property
    def use_mock_llm(self) -> bool:
        """Работать на заготовках (True) или на настоящей модели (False)."""
        value = self.mock_llm.strip().lower()
        if value in ("true", "1", "yes"):
            return True
        # Без ключа облачная модель не работает — остаёмся в демо.
        # Поэтому и для "auto", и для "false" ответ одинаковый: демо, только если ключа нет.
        # Ollama работает на вашем компьютере и ключа не требует.
        return not self.llm_key_present

    @property
    def llm_key_present(self) -> bool:
        provider = self.llm_provider.strip().lower()
        if provider == "ollama":
            return True
        if provider == "anthropic":
            return bool(self.anthropic_api_key.strip())
        return bool(self.llm_api_key.strip() or (self.backup_llm_provider.strip() and self.backup_llm_api_key.strip()))

    @property
    def llm_model_name(self) -> str:
        """Какая модель реально используется (для логов и баннера)."""
        from backend.app.llm.openai_compat import PRESETS

        provider = self.llm_provider.strip().lower()
        if provider == "anthropic":
            return self.anthropic_model
        if provider == "ollama":
            return self.ollama_model
        return self.llm_model.strip() or PRESETS.get(provider, {}).get("model", "")

    @property
    def webapp_is_https(self) -> bool:
        """Telegram открывает Mini App только по HTTPS-адресу."""
        return self.webapp_url.strip().lower().startswith("https://")


@lru_cache
def get_settings() -> Settings:
    """Возвращает настройки (один и тот же объект при каждом вызове).

    @lru_cache — декоратор-«запоминалка»: первый вызов читает .env,
    следующие возвращают готовый результат. В тестах кэш сбрасывают
    через get_settings.cache_clear().
    """
    return Settings()
