# Changelog

Все значимые изменения проекта DevGrabBot документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [0.1.0] - 2026-03-04

### Добавлено

#### Phase 1: Фундамент (core)
- **src/core/config.py** — Конфигурация через Pydantic Settings (BOT_TOKEN, DATABASE_URL, REDIS_URL, OpenRouter, Profiru)
- **src/core/models.py** — SQLAlchemy 2 модели: Order, AiAnalysis, OrderAssignment, ManagerResponse, TeamMember, Setting
- **src/core/database.py** — Async SQLAlchemy engine и session factory
- **src/core/redis.py** — Redis клиент с очередями new_orders, analyzed и дедупликацией
- **alembic** — Миграции базы данных (initial tables)

#### Phase 2: Парсер Профи.ру
- **src/parser/base.py** — Абстрактный базовый класс парсера (BaseParser)
- **src/parser/profiru/scraper.py** — Парсер Профи.ру (Selenium auth + GraphQL API)
- **src/parser/profiru/filters.py** — Фильтры заказов (стоп-слова, возраст, дедупликация)
- **src/parser/worker.py** — Воркер парсинга (периодический опрос + Redis-очередь)

#### Phase 3: AI Engine
- **src/ai/openrouter.py** — Клиент OpenRouter API (complete, complete_json)
- **src/ai/prompts/analyze.py** — Промпт анализа заявки (стек, цена, сроки, релевантность)
- **src/ai/prompts/response.py** — Промпт формирования отклика для менеджера
- **src/ai/analyzer.py** — OrderAnalyzer (анализ заявок + генерация откликов)

#### Phase 4: Telegram Bot
- **src/bot/bot.py** — Точка входа бота (aiogram 3 Dispatcher)
- **src/bot/handlers/start.py** — Команда /start
- **src/bot/handlers/orders.py** — Обработка заявок в групповом чате (Взять/Пропустить)
- **src/bot/handlers/review.py** — Редактирование заявки в личке (FSM: цена/сроки/стек/заметки)
- **src/bot/handlers/manager.py** — Уведомления менеджеру + готовый отклик
- **src/bot/keyboards/orders.py** — Inline-клавиатуры для заявок
- **src/bot/keyboards/review.py** — Inline-клавиатуры для ревью
- **src/bot/states.py** — FSM-состояния редактирования
- **src/bot/middlewares/auth.py** — Middleware авторизации (проверка team_members)

#### Phase 5: Интеграция
- **src/ai/worker.py** — AI-воркер (Redis → анализ → БД → очередь уведомлений)
- **src/bot/services/notification.py** — Воркер уведомлений (очередь → групповой чат)

#### Phase 6: DevOps
- **scripts/systemd/** — Unit-файлы: devgrabbot.service, devgrab-parser.service, devgrab-scheduler.service
- **src/bot/main.py** — Единая точка входа (бот + воркеры)

#### Phase 7: Документация и тесты
- **docs/modules/core.md** — Документация модуля core (config, models, database, redis)
- **docs/modules/parser.md** — Документация модуля parser (архитектура, scraper, filters, worker)
- **docs/modules/ai.md** — Документация модуля AI engine (OpenRouter, промпты, analyzer, worker)
- **docs/modules/bot.md** — Документация модуля Telegram bot (handlers, keyboards, FSM, middleware)
- **docs/modules/integration.md** — Документация интеграции (systemd, мониторинг, деплой)
- **CHANGELOG.md** — Журнал изменений

#### Тесты (51 тест — все пройдены)
- **tests/test_config.py** — Тесты конфигурации (2 теста)
- **tests/test_models.py** — Тесты моделей данных (6 тестов)
- **tests/test_openrouter.py** — Тесты OpenRouter клиента (8 тестов)
- **tests/test_analyzer.py** — Тесты AI-анализатора (8 тестов)
- **tests/test_parser_base.py** — Тесты базового парсера (7 тестов)
- **tests/test_filters.py** — Тесты фильтров заказов (20 тестов)

## [0.1.1] - 2026-03-04

### Изменено

#### Парсер: адаптация под реальный API Профи.ру
Сверка и адаптация по [dobrozor/parser_profiru](https://github.com/dobrozor/parser_profiru):

- **scraper.py** — Полностью переписан:
  - GraphQL запрос заменён на реальный `BoSearchBoardItems` (с хешем `#prfrtkn:webbo:...`)
  - Авторизация через форму логин+пароль (не просто cookie), элементы: `auth_login_input`, `input[type=password]`, `enter_with_sms_btn`
  - API headers: добавлены `origin`, `referer`, `x-app-id: BO`, `x-new-auth-compatible: 1`
  - Cookies передаются через параметр `cookies=`, а не в заголовках
  - Фильтрация: выбираются только `type == "SNIPPET"` (реальные заказы)
  - Нормализация: `title` (не `subject`), `price` как `{prefix, suffix, value}` → строка, `geo` → location
  - Timestamps в секундах (не миллисекундах)
  - Добавлены вспомогательные методы: `_format_price()`, `_extract_location()`
  - Anti-detection: `excludeSwitches`, `disable-blink-features`, `remote-debugging-port`
- **config.py** — Добавлен параметр `profiru_password`
- **filters.py** — Стоп-слова проверяются в полях `title`, `description`, `subject`, `type`
- **.env** — Добавлен `PROFIRU_PASSWORD=`
- **docs/modules/parser.md** — Полностью обновлена документация парсера
- **docs/modules/core.md** — Добавлен `profiru_password` в таблицу параметров
