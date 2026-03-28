# Infrastructure — Инфраструктура и деплой

> **Последнее обновление:** 2026-03-05
> **ВАЖНО:** При любых изменениях в инфраструктуре — обновить этот документ!

## Архитектура деплоя

```
VPS (Ubuntu 24.04)
├── Python 3.11+ (venv)
├── PostgreSQL (asyncpg)
├── Redis (hiredis)
├── Chrome/ChromeDriver (headless, для Selenium)
└── systemd (3 сервиса)
```

**NO Docker** — прямая установка на VPS.

---

## Systemd сервисы

### devgrabbot.service (основной)
- **Запускает:** Telegram бот + AI Worker + Notification Worker
- **Команда:** `python3 -m src.bot.main`
- **Restart:** on-failure (10s)
- **Зависит от:** postgresql, redis

### devgrab-parser.service (парсер)
- **Запускает:** Парсер Профи.ру
- **Команда:** `python3 -m src.parser.worker`
- **Restart:** on-failure (30s)
- **Зависит от:** redis

### devgrab-scheduler.service (AI воркер)
- **Запускает:** AI-воркер анализа (альтернативный запуск)
- **Команда:** `python3 -m src.ai.worker`
- **Restart:** on-failure (10s)
- **Зависит от:** postgresql, redis

**Расположение:** `scripts/systemd/`

### Управление

```bash
# Статус
sudo systemctl status devgrabbot
sudo systemctl status devgrab-parser
sudo systemctl status devgrab-scheduler

# Перезапуск
sudo systemctl restart devgrabbot

# Логи
sudo journalctl -u devgrabbot -f
sudo journalctl -u devgrab-parser -f --since "1 hour ago"

# Включить автозапуск
sudo systemctl enable devgrabbot devgrab-parser devgrab-scheduler
```

---

## Alembic (миграции)

**Конфиг:** `alembic.ini`
**Директория:** `src/migrations/`
**Текущие миграции:** 1 (initial_tables — 6 таблиц)

```bash
# Применить миграции
alembic upgrade head

# Создать новую миграцию
alembic revision --autogenerate -m "описание"

# Откатить
alembic downgrade -1

# Текущая версия
alembic current
```

**Gotcha:** `env.py` содержит `sys.path.insert` для импорта моделей.

---

## Тесты

```bash
# Запуск всех тестов
python3 -m pytest tests/

# С подробным выводом
python3 -m pytest tests/ -v

# Конкретный модуль
python3 -m pytest tests/test_filters.py -v
```

**Статус:** 51 тест, все passing (0.87s)

| Модуль | Тестов | Описание |
|--------|--------|---------|
| test_analyzer.py | 8 | OrderAnalyzer, промпты |
| test_config.py | 2 | Загрузка Settings |
| test_filters.py | 21 | Фильтрация заказов |
| test_models.py | 6 | SQLAlchemy модели |
| test_openrouter.py | 8 | OpenRouter API клиент |
| test_parser_base.py | 7 | Абстрактный парсер |

**pytest.ini в pyproject.toml:**
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## Зависимости (pyproject.toml)

### Production
| Пакет | Версия | Назначение |
|-------|--------|-----------|
| aiogram | >=3.4,<4 | Telegram бот |
| sqlalchemy[asyncio] | >=2.0,<3 | ORM |
| asyncpg | >=0.29 | PostgreSQL |
| redis[hiredis] | >=5.0 | Redis |
| httpx | >=0.27 | HTTP клиент |
| pydantic | >=2.0,<3 | Валидация |
| pydantic-settings | >=2.0 | .env конфигурация |
| alembic | >=1.13 | Миграции |
| selenium | >=4.15 | Браузер |
| webdriver-manager | >=4.0 | ChromeDriver |
| beautifulsoup4 | >=4.12 | HTML парсинг |
| python-dotenv | >=1.0 | .env |

### Development
| Пакет | Версия | Назначение |
|-------|--------|-----------|
| pytest | >=8.0 | Тесты |
| pytest-asyncio | >=0.23 | Async тесты |
| ruff | >=0.3 | Линтер/форматер |

### Установка
```bash
pip install -e .                    # Dev режим
pip install -e ".[dev]"             # С dev-зависимостями
```

---

## Переменные окружения (.env)

```bash
# Telegram
BOT_TOKEN=<токен от @BotFather>
GROUP_CHAT_ID=<ID группы>          # ⚠️ заменить плейсхолдер!

# Database
DATABASE_URL=postgresql+asyncpg://devgrabbot:password@localhost:5432/devgrabbot
# ⚠️ Пароль НЕ должен содержать !

# Redis
REDIS_URL=redis://localhost:6379/0

# OpenRouter
OPENROUTER_API_KEY=<ключ>
OPENROUTER_MODEL=google/gemini-3.1-flash-lite-preview

# Profiru
PROFIRU_LOGIN=DubinaDV4
PROFIRU_TOKEN=<токен>

# Parser
PARSE_INTERVAL_SEC=300
TIME_THRESHOLD_HOURS=24
STOP_WORDS=["WordPress","Битрикс","Опрос"]
```

---

## Структура проекта

```
/var/www/BOTS/DevGrabBot/
├── src/
│   ├── core/           # Конфиг, модели, БД, Redis
│   ├── bot/            # Telegram бот (aiogram 3)
│   ├── parser/         # Парсер Профи.ру
│   ├── ai/             # AI Engine (OpenRouter)
│   └── migrations/     # Alembic
├── tests/              # 51 тест
├── scripts/systemd/    # 3 .service файла
├── docs/
│   ├── services/       # Документация по сервисам
│   ├── modules/        # Модульная документация
│   └── plans/          # Design + Implementation
├── .claude/agents/     # AI-агенты для Claude Code
├── pyproject.toml
├── alembic.ini
├── .env
├── .env.example
└── CLAUDE.md
```

---

## Известные проблемы

1. **GROUP_CHAT_ID** — плейсхолдер `-1001234567890` (заменить!)
2. **Пароль БД** — не использовать `!` (URL encoding)
3. **python3 vs python** — на Ubuntu 24.04 только python3
4. **OPENROUTER_API_KEY** — не заполнен в .env.example
5. **Selenium на VPS** — нужны системные пакеты для Chrome
