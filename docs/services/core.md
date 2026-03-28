# Core — Ядро системы

> **Последнее обновление:** 2026-03-25 (RedisClient: PARSER_PAUSED_KEY + методы управления паузой парсера)
> **ВАЖНО:** При любых изменениях в модуле `src/core/` — обновить этот документ!

## Назначение

Центральный модуль: конфигурация, модели данных, подключение к БД и Redis.

## Файлы

| Файл | Назначение |
|------|-----------|
| `src/core/config.py` | Pydantic Settings v2 — загрузка из `.env` |
| `src/core/models.py` | SQLAlchemy 2 модели (6 таблиц, 3 enum) |
| `src/core/database.py` | Async engine + session factory |
| `src/core/redis.py` | Redis клиент для очередей и дедупликации |
| `src/core/settings_service.py` | CRUD сервис для таблицы settings (стоп-слова, промпты, config-параметры) |

---

## config.py — Settings

```python
class Settings(BaseSettings):
    # Telegram
    bot_token: str
    group_chat_id: int

    # Database
    database_url: str  # postgresql+asyncpg://user:pass@host:port/db

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # OpenRouter (AI)
    openrouter_api_key: str
    openrouter_model: str = "google/gemini-3.1-flash-lite-preview"

    # Profiru
    profiru_login: str
    profiru_password: str = ""
    profiru_token: str = ""

    # Parser
    parse_interval_sec: int = 300
    time_threshold_hours: int = 24
    stop_words: list[str] = ["WordPress", "Битрикс", "Опрос"]

    # Parser Resilience
    parser_token_ttl_sec: int = 480        # TTL токена в Redis (8 мин)
    parser_max_auth_attempts: int = 3      # макс. попыток авторизации
    parser_jitter_factor: float = 0.2      # jitter ±20%
    parser_night_multiplier: float = 3.0   # множитель ночью
    parser_circuit_breaker_threshold: int = 5    # ошибок до CB OPEN
    parser_circuit_breaker_cooldown_sec: int = 1800  # 30 мин cooldown
    parser_alert_dedup_sec: int = 900      # 15 мин дедупликация
    parser_request_delay_min: float = 1.0  # мин. пауза между запросами
    parser_request_delay_max: float = 3.0  # макс. пауза между запросами
    parser_auth_cooldown_sec: int = 120    # мин. интервал между авторизациями

    # Parser Logging
    parser_log_level: str = "INFO"         # DEBUG, INFO, WARNING, ERROR, CRITICAL, OFF
    parser_log_file: str = "logs/parser.log"  # путь к файлу логов парсера

    # Stats broadcast
    stats_broadcast_hour: int = 9  # UTC час для ежедневной рассылки статистики в группу
```

**Источник:** `.env` файл (UTF-8)

---

## models.py — Модели данных

### Enum-ы

| Enum | Значения |
|------|---------|
| `OrderStatus` | `new`, `analyzing`, `reviewed`, `assigned`, `completed`, `skipped` |
| `AssignmentStatus` | `pending`, `editing`, `approved`, `sent`, `rejected`, `reassigned`, `in_progress`, `cancelled` |
| `TeamRole` | `developer`, `manager` |

### Таблицы

#### Order (orders)
Заказы с Профи.ру

| Поле | Тип | Описание |
|------|-----|---------|
| `id` | int PK | ID в системе |
| `external_id` | str UNIQUE | ID на Профи.ру |
| `platform` | str | "profiru" |
| `title` | str(500) | Название |
| `description` | TEXT | Описание |
| `budget` | str(200) NULL | Бюджет (текст) |
| `location` | str(200) NULL | Локация |
| `deadline` | str(200) NULL | Дедлайн |
| `raw_text` | TEXT | Полный текст заявки |
| `materials` | JSON NULL | Прикреплённые файлы/изображения: `[{"type": "image"|"file", "url": "https://cdn.profi.ru/...", "name": "...", "preview": "..."}]` |
| `status` | OrderStatus | Статус (default: new) |
| `created_at` | datetime | Время добавления |

**Связи:** `analyses` → AiAnalysis[], `assignments` → OrderAssignment[]

#### AiAnalysis (ai_analyses)
Результаты AI-анализа

| Поле | Тип | Описание |
|------|-----|---------|
| `id` | int PK | |
| `order_id` | int FK | → orders.id |
| `summary` | TEXT | Краткая сводка |
| `stack` | JSON | Технологии [] |
| `price_min` | int NULL | Мин. цена (руб.) |
| `price_max` | int NULL | Макс. цена (руб.) |
| `timeline_days` | str(50) NULL | Сроки |
| `relevance_score` | int | 0-100 |
| `complexity` | str(20) | easy/medium/hard |
| `response_draft` | TEXT | Черновик отклика |
| `model_used` | str(100) | Модель OpenRouter |
| `extra_data` | JSON NULL | Вопросы, риски и др. |
| `created_at` | datetime | |

#### OrderAssignment (order_assignments)
Назначение разработчику

| Поле | Тип | Описание |
|------|-----|---------|
| `id` | int PK | |
| `order_id` | int FK | → orders.id |
| `developer_id` | int FK | → team_members.id |
| `status` | AssignmentStatus | default: pending |
| `price_final` | int NULL | Итоговая цена |
| `timeline_final` | str(100) NULL | Итоговые сроки |
| `stack_final` | JSON NULL | Финальный стек |
| `custom_notes` | TEXT NULL | Заметки разработчика |
| `approved_at` | datetime NULL | |
| `taken_at` | datetime NULL | Время взятия заявки разработчиком |
| `roadmap_text` | TEXT NULL | Сгенерированный roadmap (передаётся в контекст отклика) |
| `assigned_by` | int FK NULL | → team_members.id; NULL = сам взял, ID = менеджер назначил |
| `rejection_reason` | TEXT NULL | Причина отклонения менеджером |
| `group_message_id` | bigint NULL | ID сообщения в групповом чате (для обновления при отказе) |
| `in_progress_at` | datetime NULL | Время переведения в статус in_progress менеджером |
| `cancelled_at` | datetime NULL | Время отмены заявки менеджером |

#### ManagerResponse (manager_responses)
Финальный отклик менеджера

| Поле | Тип | Описание |
|------|-----|---------|
| `id` | int PK | |
| `assignment_id` | int FK | → order_assignments.id |
| `response_text` | TEXT | Текст отклика |
| `sent_at` | datetime | |
| `edited_text` | TEXT NULL | Текст, если менеджер отредактировал перед отправкой |
| `sent_to_client` | bool | Подтверждение отправки клиенту (default: false) |
| `sent_to_client_at` | datetime NULL | Время отправки клиенту |

#### TeamMember (team_members)
Члены команды

| Поле | Тип | Описание |
|------|-----|---------|
| `id` | int PK | |
| `tg_id` | BIGINT UNIQUE | Telegram ID |
| `tg_username` | str(100) NULL | @username |
| `name` | str(200) | Имя |
| `role` | TeamRole | developer/manager |
| `is_active` | bool | default: True |
| `tech_stack` | JSON | Стек технологий, напр. `["Python", "FastAPI", "React"]` |
| `stack_priority` | JSON | Приоритеты стека: `{"primary": [...], "secondary": [...]}` |
| `bio` | TEXT | Описание разработчика для AI-контекста |
| `notify_assignments` | bool | default: True; вкл/выкл уведомления о взятых заявках |

#### Setting (settings)
Key-value хранилище настроек

| Поле | Тип | Описание |
|------|-----|---------|
| `key` | str(100) PK | Ключ |
| `value` | TEXT | Значение |

---

## database.py

```python
create_engine(settings: Settings) → AsyncEngine
create_session_factory(engine) → async_sessionmaker[AsyncSession]
```

- `echo=False` (без SQL-логов)
- `expire_on_commit=False` (объекты не очищаются после commit)

---

## redis.py — RedisClient

### Очереди (Redis Lists, FIFO)

| Константа | Ключ Redis | Назначение |
|-----------|-----------|-----------|
| `QUEUE_NEW_ORDERS` | `devgrab:new_orders` | Парсер → AI Worker |
| `QUEUE_ANALYZED` | `devgrab:analyzed` | AI Worker → Notification |
| `SENT_ORDERS_SET` | `devgrab:sent_order_ids` | Дедупликация (Set) |
| `PARSER_PAUSED_KEY` | `devgrab:parser:paused` | Флаг паузы парсера (PM toggle) |

### Методы

| Метод | Операция | Описание |
|-------|---------|---------|
| `push_order(dict)` | RPUSH | Добавить заказ в очередь |
| `pop_order()` | LPOP | Извлечь заказ (FIFO) |
| `push_analyzed(dict)` | RPUSH | Результат анализа в очередь |
| `pop_analyzed()` | LPOP | Извлечь для уведомления |
| `is_order_sent(id)` | SISMEMBER | Проверка дубликата |
| `mark_order_sent(id)` | SADD | Пометить обработанным |
| `get_queue_length()` | LLEN | Длина очереди |
| `is_parser_paused()` | GET | `True` если парсер на паузе (ключ существует) |
| `set_parser_paused()` | SET | Установить флаг паузы (без TTL) |
| `set_parser_resumed()` | DELETE | Снять флаг паузы |
| `close()` | aclose | Закрыть соединение |

---

## Поток данных

```
Парсер → push_order() → Redis:new_orders
                              ↓ pop_order()
                         AI Worker → save to DB
                              ↓ push_analyzed()
                         Redis:analyzed
                              ↓ pop_analyzed()
                         Notification → TG Group
                              ↓ mark_order_sent()
                         Redis:sent_order_ids
```

## Зависимости

- `pydantic-settings>=2.0` — конфигурация
- `sqlalchemy[asyncio]>=2.0` — ORM
- `asyncpg>=0.29` — PostgreSQL driver
- `redis[hiredis]>=5.0` — Redis client

---

## settings_service.py — Сервис настроек

Набор standalone async-функций для работы с таблицей `settings` (key-value store).
Все функции принимают `AsyncSession` первым аргументом.

### Константы

| Константа | Тип | Назначение |
|-----------|-----|-----------|
| `PROMPT_KEYS` | `tuple[str, ...]` | Валидные ключи промптов: `("analyze", "response", "roadmap")` |
| `CONFIG_FALLBACK_KEYS` | `dict[str, str]` | Маппинг DB key → config attr: `openrouter_model`, `parse_interval_sec`, `time_threshold_hours`, `stats_broadcast_hour` |

### Базовый CRUD

| Функция | Сигнатура | Описание |
|---------|-----------|---------|
| `get_setting` | `(session, key, default=None) → str \| None` | Получить значение; None или default если не найдено |
| `set_setting` | `(session, key, value) → None` | Upsert через `session.merge()` + `commit()` |
| `delete_setting` | `(session, key) → bool` | Удалить; True = удалено, False = не существовало |

### Стоп-слова (key="stop_words", JSON list)

| Функция | Сигнатура | Описание |
|---------|-----------|---------|
| `get_stop_words` | `(session, config) → list[str]` | DB → config.stop_words fallback |
| `set_stop_words` | `(session, words) → None` | Перезаписать весь список |
| `add_stop_word` | `(session, word) → list[str]` | Добавить слово; вернуть обновлённый список |
| `remove_stop_word` | `(session, word) → list[str]` | Удалить слово; вернуть обновлённый список |

### Промпты (key="prompt_{name}")

| Функция | Сигнатура | Описание |
|---------|-----------|---------|
| `get_prompt` | `(session, prompt_key) → str \| None` | Получить из DB; None → вызывающий делает fallback на файл |
| `set_prompt` | `(session, prompt_key, text) → None` | Сохранить промпт в БД |
| `reset_prompt` | `(session, prompt_key) → None` | Удалить из БД (сброс к файловому дефолту) |

**DB-ключи промптов:** `prompt_analyze`, `prompt_response`, `prompt_roadmap`

### Настройки с fallback на config

| Функция | Сигнатура | Описание |
|---------|-----------|---------|
| `get_config_setting` | `(session, key, config) → str` | DB → config fallback; KeyError для неизвестных ключей |

**Поддерживаемые ключи:** `openrouter_model`, `parse_interval_sec`, `time_threshold_hours`, `stats_broadcast_hour`

### Транзакционная модель

- `set_setting` и `delete_setting` — атомарны, делают `commit()` сами
- `get_stop_words`, `get_prompt`, `get_config_setting` — только чтение, `commit()` не вызывают
- `add_stop_word` / `remove_stop_word` / `set_stop_words` — делегируют в `set_setting`, commit внутри
- `set_prompt` / `reset_prompt` — делегируют в `set_setting` / `delete_setting`, commit внутри

---

## Gotchas

- Пароль БД НЕ должен содержать `!` (проблемы с URL-кодированием)
- `python3` а не `python` (Ubuntu 24.04)
- Alembic env.py требует `sys.path.insert` для импорта модулей
