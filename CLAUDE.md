# DevGrabBot

## Project
Telegram bot + AI assistant for automating freelance order processing (Профи.ру → AI analysis → developer review → manager response).

## Team
- Денис (web-dusha) — senior fullstack dev
- Imei Rhen — senior fullstack dev
- Кирилл (Kirill Goriainov) — product manager
- Герман - senior fullstack dev
- Данил - senior fullstack dev

## Development Workflow
**Локальная разработка → GitHub → Деплой на сервер**

1. **Разработка** — только локально (Windows). Код не запускается локально, только правки.
2. **Push** — после правок пушим в GitHub: `git@github.com:Vento741/DevGrabBot.git` (ветка `main`)
3. **Deploy** — деплоим на VPS: `ssh devgrabbot-vps` → pull → restart services
4. **Тесты** — только на сервере (бот и все зависимости работают там)

### SSH доступ к серверу
```
Host devgrabbot-vps
    HostName 5.101.181.11
    User root
    IdentityFile ~/.ssh/devgrabbot_vps
    ServerAliveInterval 60
    ServerAliveCountMax 3
```
> **TODO:** Создать отдельный SSH-ключ `~/.ssh/devgrabbot_vps`. Пока используется `jeremy-vps` (Host jeremy-vps, IdentityFile ~/.ssh/jeremy_vps_nopass).

### Deploy Commands (с сервера)
```bash
cd /var/www/BOTS/DevGrabBot
git pull origin main
pip install -e . --break-system-packages
alembic upgrade head
sudo systemctl restart devgrabbot devgrab-parser devgrab-scheduler
```

### Quick Deploy (с локалки)
```bash
ssh jeremy-vps "cd /var/www/BOTS/DevGrabBot && git pull origin main && sudo systemctl restart devgrabbot devgrab-parser devgrab-scheduler"
```

## Architecture
Modular: Parser → Redis queue → AI Engine (OpenRouter) → TG Bot (aiogram 3) → PostgreSQL
Deploy: VPS + venv + systemd (NO Docker)
Pattern matches WB Price Bot in same workspace.

## Stack
Python 3.11+, aiogram 3, SQLAlchemy 2 + asyncpg, Redis, Selenium + BS4, httpx, Alembic, Pydantic v2

## Key paths
- `src/bot/` — TG bot (aiogram 3, handlers/keyboards/middlewares)
  - `src/bot/handlers/dev_panel.py` — панель разработчика (стек, стоп-слова, промпты, команда, настройки, заявки, статистика)
  - `src/bot/handlers/manager_panel.py` — панель менеджера (отклики, стиль, профиль, заявки, разработчики, аналитика)
  - `src/bot/services/matching.py` — матчинг разработчиков по стеку
  - `src/bot/states.py` — FSM states (DevPanelStates, ManagerPanelStates)
- `src/parser/` — Profiru parser (Selenium + GraphQL)
- `src/ai/` — AI engine (OpenRouter client, prompts, analyzer)
  - `src/ai/context.py` — OrderContext (полный контекст заявки для всех этапов AI)
- `src/core/` — config, database, redis, models
  - `src/core/settings_service.py` — CRUD для settings таблицы (стоп-слова, промпты, config из DB)
- `src/migrations/` — Alembic migrations

## Bot Commands
- `/start` — приветствие + кнопки навигации к панелям
- `/panel` — быстрый выбор панели (dev / manager)
- `/dev` — панель разработчика (стек, стоп-слова, промпты, команда, настройки, заявки, статистика)
- `/manager` — панель менеджера (отклики, стиль, профиль, заявки, разработчики, аналитика)

## Conventions
- All communication in Russian (team and bot messages)
- Order IDs follow format: #DD.MM.YY_N (e.g. #24.02.26_1)
- AI model is configurable via settings (OpenRouter)
- DB DateTime columns are naive (no timezone) — use `datetime.utcnow()` not `datetime.now(timezone.utc)`
- systemd services: devgrabbot.service, devgrab-parser.service, devgrab-scheduler.service

## Commands

### Локальные (Windows)
- `git add . && git commit -m "..." && git push` — push изменений в GitHub
- `ssh jeremy-vps "cd /var/www/BOTS/DevGrabBot && git pull origin main && sudo systemctl restart devgrabbot"` — quick deploy

### Серверные (VPS, ssh jeremy-vps)
- `pip install -e . --break-system-packages` — install in dev mode
- `alembic upgrade head` — run migrations
- `python3 -m pytest tests/ -v` — run tests
- `sudo systemctl restart devgrabbot` — restart bot
- `sudo systemctl restart devgrab-parser` — restart parser
- `sudo systemctl restart devgrab-scheduler` — restart scheduler
- `sudo journalctl -u devgrabbot -f` — view bot logs
- `sudo journalctl -u devgrab-parser -f` — view parser logs

## Gotchas
- Use `python3` not `python` (Ubuntu 24.04)
- DB password must NOT contain `!` (URL encoding issue)
- Alembic env.py needs `sys.path.insert` for src module imports
- `.env` GROUP_CHAT_ID — replace placeholder with real ID
- pip needs `--break-system-packages` flag (system Python)

## Agents (`.claude/agents/`)
- [core-specialist](.claude/agents/core-specialist.md) — config, models, DB, Redis, migrations
- [bot-specialist](.claude/agents/bot-specialist.md) — handlers, keyboards, FSM, notification worker
- [parser-specialist](.claude/agents/parser-specialist.md) — Selenium, GraphQL, filters, scraper
- [ai-engine-specialist](.claude/agents/ai-engine-specialist.md) — OpenRouter, prompts, analyzer
- [infra-specialist](.claude/agents/infra-specialist.md) — systemd, deploy, tests, dependencies

## Documentation — Service Docs
Детальная документация по каждому сервису для быстрой ориентации AI-агента:
- [Core (ядро)](docs/services/core.md) — config, models (6 таблиц, 3 enum), database, redis
- [Bot (Telegram)](docs/services/bot.md) — handlers, keyboards, middlewares, workers, FSM
- [Parser (парсер)](docs/services/parser.md) — Selenium + GraphQL, фильтры, воркер
- [AI Engine](docs/services/ai-engine.md) — OpenRouter, промпты, анализатор, воркер
- [Infrastructure](docs/services/infrastructure.md) — systemd, Alembic, тесты, деплой

## Documentation — Design & Plans
- [Архитектура, модели данных, AI-промпты, стек](docs/plans/2026-03-04-devgrabbot-design.md)
- [План реализации — 24 задачи, 7 фаз](docs/plans/2026-03-04-devgrabbot-implementation.md)
- [**Модернизация v2.0** — AI-баги, панели dev/manager, 7 фаз, 60+ чекпоинтов](docs/plans/2026-03-05-modernization-v2.md)

## MANDATORY: Documentation Sync Rule

**ПОСЛЕ КАЖДОГО ИЗМЕНЕНИЯ КОДА — ОБЯЗАТЕЛЬНО ОБНОВИТЬ ДОКУМЕНТАЦИЮ:**

1. Изменения в `src/core/` → обновить `docs/services/core.md`
2. Изменения в `src/bot/` → обновить `docs/services/bot.md`
3. Изменения в `src/parser/` → обновить `docs/services/parser.md`
4. Изменения в `src/ai/` → обновить `docs/services/ai-engine.md`
5. Изменения в инфраструктуре (systemd, deps, .env) → обновить `docs/services/infrastructure.md`
6. Новые agents/skills → обновить этот CLAUDE.md

**Что обновлять в доке:**
- Дату "Последнее обновление" в шапке документа
- Добавленные/изменённые файлы, функции, классы
- Новые переменные конфигурации
- Изменённые потоки данных или API

**Это критически важно** — без актуальной документации AI-агент в новой сессии потеряет контекст проекта.
