# Parser — Парсер Профи.ру

> **Последнее обновление:** 2026-03-25 (worker: проверка паузы через redis_client.is_parser_paused() в начале каждой итерации)
> **ВАЖНО:** При любых изменениях в модуле `src/parser/` — обновить этот документ!

## Назначение

Парсинг заказов с Профи.ру через GraphQL API. Авторизация через Selenium. Фильтрация и дедупликация через Redis. **Resilience Layer** обеспечивает защиту от бана.

## Файлы

| Файл | Назначение |
|------|-----------|
| `src/parser/base.py` | Абстрактный BaseParser (интерфейс) |
| `src/parser/worker.py` | Воркер парсера (цикл + resilience) |
| `src/parser/profiru/scraper.py` | Парсер Профи.ру (Selenium + GraphQL + httpx) |
| `src/parser/profiru/filters.py` | Фильтрация по стоп-словам и возрасту |
| `src/parser/resilience/__init__.py` | Resilience Layer — экспорт компонентов |
| `src/parser/resilience/circuit_breaker.py` | Circuit Breaker (3 состояния, порог ошибок) |
| `src/parser/resilience/alert_service.py` | Алерты в Telegram с дедупликацией |
| `src/parser/resilience/token_manager.py` | Кэш токена в Redis + backoff авторизации |
| `src/parser/resilience/request_scheduler.py` | Jitter + адаптивные интервалы по времени суток |
| `src/parser/resilience/health.py` | Мониторинг и метрики в Redis |

---

## Поток данных (с Resilience Layer)

```
Worker loop:
  1. scheduler.get_next_delay() → sleep (с jitter + adaptive)
  2. circuit_breaker.is_open? → пропуск если OPEN
  3. token_manager.get_token()
     → memory cache? → return
     → Redis cache (token + cookies JSON)? → return
     → Selenium auth → сохранить ВСЕ cookies (с backoff, rate limit)
  4. parser.set_session_cookies(token_manager.get_session_cookies())
  5. parser.fetch_orders_raw(token)
     → 401? → invalidate → re-auth → retry (1 раз)
  6. parser.process_raw_orders(raw, token)
     → normalize + enrich prices (с паузами 1-3с между запросами)
  7. token_manager.update_cookies_from_scraper(parser._session_cookies)
  8. filter + dedup + push to Redis
  9. health.save() → Redis
  10. _sleep_with_keepalive(delay) — серия sleep + keep-alive пинги каждые 2 мин
      → keep_alive OK? → обновить cookies в Redis
      → keep_alive 401? → invalidate token (Selenium на следующей итерации)
```

---

## Resilience Layer

### Circuit Breaker (`circuit_breaker.py`)

Три состояния:
- **CLOSED** — нормальная работа, считаем ошибки
- **OPEN** — парсер остановлен (все запросы блокируются, cooldown)
- **HALF_OPEN** — пробная итерация после cooldown

| Параметр | Значение | Конфиг |
|----------|----------|--------|
| Порог ошибок | 5 | `parser_circuit_breaker_threshold` |
| Cooldown | 1800 сек (30 мин) | `parser_circuit_breaker_cooldown_sec` |

### Token Manager (`token_manager.py`)

- **Cookie Persistence** — кэширует ВСЕ cookies сессии (как браузер), не только `prfr_bo_tkn`
- Кэш: memory → Redis (TTL 14400с / 4ч) — и токен, и cookies dict (JSON)
- Rate limit авторизации: не чаще 1 раз в 10 мин (`PARSER_AUTH_COOLDOWN_SEC=600`)
- Exponential backoff между попытками: 30с → 60с → 120с
- Максимум 3 попытки подряд
- Selenium выполняется в ThreadPoolExecutor
- `authorize_selenium()` возвращает `dict[str, str]` (все cookies), не `str`

| Redis ключ | TTL | Описание |
|------------|-----|----------|
| `devgrab:parser:token` | 14400с (4ч) | Кэш токена (`prfr_bo_tkn`) |
| `devgrab:parser:cookies` | 14400с (4ч) | JSON dict всех cookies сессии |
| `devgrab:parser:auth_attempts` | 3600с | Счётчик попыток |

**Ключевые методы:**

| Метод | Назначение |
|-------|-----------|
| `get_token()` | memory → Redis (token + cookies) → Selenium auth |
| `get_session_cookies()` | Получить все cookies для передачи в scraper |
| `invalidate()` | Очистить token + cookies (memory + Redis) |
| `update_cookies_from_scraper(cookies)` | Обновить cookies из Set-Cookie ответов сервера |
| `set_initial_token(token)` | Установить токен из .env конфига |

### Request Scheduler (`request_scheduler.py`)

- **Jitter:** ±20% к базовому интервалу
- **Backoff:** при ошибках 2x, 4x, 8x... до 16x
- **Минимум:** 60 секунд

**Адаптивные интервалы (МСК):**
| Время | Множитель | Пример (base=600с) |
|-------|-----------|-------------------|
| 00:00-07:00 | ×3.0 | ~1800с (30 мин) |
| 07:00-10:00 | ×2.0 | ~1200с (20 мин) |
| 10:00-19:00 | ×1.0 | ~600с (10 мин) |
| 19:00-00:00 | ×3.0 | ~1800с (30 мин) |

### Alert Service (`alert_service.py`)

- Алерты об ошибках/предупреждениях в Telegram (GROUP_CHAT_ID)
- Дедупликация: одинаковые алерты не чаще 1 раз в 15 мин
- Отправка через httpx (Telegram Bot API)
- Типы: `error()`, `warning()`, `info()`, `circuit_breaker_opened()`, `auth_failed()`

### Health Monitor (`health.py`)

Сохраняет в Redis (`devgrab:parser:health`) JSON со статусом:
- Итерации: total, last_success, last_error
- CircuitBreaker: state, failure_count, remaining_cooldown
- Scheduler: current_multiplier, consecutive_errors
- TokenManager: has_token

---

## ProfiruParser (scraper.py)

### Единый User-Agent

```
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36
```

Используется и в Selenium, и в httpx — **одинаковый**.

### GraphQL

**URL:** `https://rnd.profi.ru/graphql`
**Операция:** `BoSearchBoardItems`
**Размер страницы:** 20 заказов

### Ключевые методы

| Метод | Назначение |
|-------|-----------|
| `fetch_orders_raw(token)` | GraphQL запрос, возвращает None при 401 |
| `process_raw_orders(raw, token)` | Нормализация + обогащение ценами и материалами (с паузами) |
| `fetch_orders(token)` | Обёртка: raw → process (обратная совместимость) |
| `_fetch_order_details(order_id, token)` | REST API `getOrder` — извлекает цену отклика + materials (изображения из `full_view.ofiles`, документы из `full_view.ofiles_doc`). Возвращает `{"response_price": int\|None, "materials": list\|None}`. URL файлов с `cdn.profi.ru`. Заменил `_fetch_response_price()`. |
| `authorize_selenium()` | Selenium авторизация, возвращает `dict[str, str]` (все cookies) |
| `keep_alive(token)` | GraphQL ping (pageSize=1) для детекции мёртвой сессии |
| `filter_order(order)` | Проверка через ProfiruFilters |
| `set_session_cookies(cookies)` | Установить cookies сессии для HTTP-запросов |

### Cookie Persistence (Anti-Ban)

**Проблема:** Парсер отправлял только `prfr_bo_tkn`, а браузер отправляет ~10+ cookies (`uid`, `sid`, `sl-session` и др.). Из-за этого сессия жила ~8 мин вместо 17+ часов как в браузере.

**Решение:**
1. `authorize_selenium()` извлекает ВСЕ cookies из браузера (`driver.get_cookies()`)
2. TokenManager кэширует cookies dict в Redis как JSON (`devgrab:parser:cookies`)
3. Worker передаёт cookies в scraper перед каждой итерацией (`set_session_cookies()`)
4. Все HTTP-запросы (GraphQL, REST) отправляют полный набор cookies
5. Set-Cookie из ответов сервера парсятся и обновляют cookies (`_update_session_cookies()`)
6. Worker периодически синхронизирует cookies обратно в TokenManager → Redis

### Keep-Alive (Детекция мёртвой сессии)

**Проблема:** Серверная сессия `rnd.profi.ru` имеет непредсказуемый TTL (3-24 мин). Без keep-alive основная итерация может получить 401 и потерять время на переавторизацию mid-iteration.

**Решение:** Между итерациями worker отправляет лёгкие GraphQL запросы (`pageSize=1`) каждые 2 мин. Если сессия мертва — токен инвалидируется проактивно, и следующая итерация сразу делает Selenium auth.

**Важно:** Keep-alive НЕ продлевает сессию (серверный TTL фиксированный), а служит **детектором**. GET на `profi.ru` (другой домен) НЕЛЬЗЯ — перезаписывает cookies и ломает `rnd.profi.ru` сессию.

**Реализация (`_sleep_with_keepalive`):**
1. Вместо `asyncio.sleep(delay)` — серия коротких sleep с keep-alive между ними
2. Keep-alive каждые `parser_keep_alive_interval_sec` (120с, 2 мин)
3. Jitter ±15% к интервалу keep-alive
4. При 401 — `token_manager.invalidate()` + обновление cookies в Redis

| Параметр | Значение | Конфиг |
|----------|----------|--------|
| Интервал | 120 сек (2 мин) | `parser_keep_alive_interval_sec` |
| Тип запроса | GraphQL (rnd.profi.ru), pageSize=1 | — |

### Паузы между запросами

Между каждым запросом `getOrder` (REST API) — случайная пауза:
- Минимум: `parser_request_delay_min` (1.0 сек)
- Максимум: `parser_request_delay_max` (3.0 сек)

### Materials — извлечение прикреплённых файлов

Метод `_fetch_order_details()` (бывший `_fetch_response_price()`) извлекает из REST API `getOrder` не только цену отклика, но и прикреплённые материалы:

- **Изображения** — из поля `full_view.ofiles` (type: `"image"`)
- **Документы** — из поля `full_view.ofiles_doc` (type: `"file"`)
- **URL** — с CDN `cdn.profi.ru`

Формат возвращаемого значения:
```python
{
    "response_price": int | None,
    "materials": [
        {"type": "image", "url": "https://cdn.profi.ru/...", "name": "photo.jpg", "preview": "https://..."},
        {"type": "file", "url": "https://cdn.profi.ru/...", "name": "ТЗ.pdf", "preview": None},
    ] | None
}
```

Materials передаются через pipeline: parser → AI worker → Order.materials (JSON) → notification/dev panel.

### Обработка 429

При получении HTTP 429 (Rate Limit) — логирование + пропуск (не крэш).

### authorize_selenium() → dict[str, str] (all cookies)

**Chrome конфигурация:**
- headless=new, no-sandbox, disable-gpu, 1920x1080
- Anti-detection: excludeSwitches, disable-blink-features
- **Таймауты увеличены:** implicit=10, wait=15, page_load=10

**Шаги авторизации:**
1. `profi.ru/backoffice/n.php`
2. Ввод логина: `[data-testid="auth_login_input"]`
3. Ввод пароля: `input[type="password"]`
4. Клик: `[data-testid="enter_with_sms_btn"]`
5. Ожидание загрузки: `a[data-testid$="_order-snippet"]` (timeout=10s)
6. Извлечение ВСЕХ cookies (`driver.get_cookies()` → dict)
7. **Session Warmup** — прогрев сессии (имитация пользователя)
8. Возврат dict всех cookies (включая `prfr_bo_tkn`, `uid`, `sid`, `sl-session` и др.)

### Session Warmup (`_warmup_session`)

После авторизации браузер имитирует поведение обычного пользователя:
1. Пауза 2-4 сек — "осмотр" страницы
2. Плавный скролл вниз (3 шага по 300px, паузы 0.8-1.5с)
3. Скролл обратно наверх
4. Клик на первый заказ + возврат назад (если есть)

**Общее время прогрева:** ~10-15 сек. Это снижает риск детекции бота — обычный пользователь после логина не делает API-запросы мгновенно.

---

## ProfiruFilters (filters.py)

### is_acceptable(order) → bool

Последовательная проверка:
1. **external_id** — должен быть непустой
2. **Возраст** — от 70 сек до `time_threshold_hours` (24ч)
3. **Стоп-слова** — проверка в title, description, subject, type

---

## Worker (worker.py)

### run_parser_worker(settings)

Инициализация:
1. ProfiruParser, RedisClient, DB session factory
2. CircuitBreaker, AlertService, TokenManager, RequestScheduler, HealthMonitor
3. Установка начального токена из конфига

Цикл:
```python
while True:
    if await redis_client.is_parser_paused():
        await asyncio.sleep(10)
        continue
    delay = scheduler.get_next_delay()
    if circuit_breaker.is_open:
        sleep(min(delay, remaining_cooldown))
        continue
    token = token_manager.get_token()
    parser.set_session_cookies(token_manager.get_session_cookies())
    raw = parser.fetch_orders_raw(token)
    if raw is None:  # 401
        token_manager.invalidate() → re-auth → retry
    orders = parser.process_raw_orders(raw, token)
    token_manager.update_cookies_from_scraper(parser._session_cookies)
    filter → dedup → push
    health.save()
    sleep(delay)
```

---

## Обработка ошибок

| Ошибка | Обработка |
|--------|----------|
| HTTP 401 | TokenManager: invalidate → re-auth (max 3 попытки) |
| HTTP 429 | Логирование, пропуск (scheduler backoff) |
| HTTP иной статус | Лог + return [] |
| GraphQL errors | Проверка "unauthorized" → None, иначе [] |
| Selenium ошибка | RuntimeError → CB.record_failure() + AlertService |
| 5+ ошибок подряд | Circuit Breaker → OPEN (30 мин cooldown) |
| Redis ошибка | Проваливается вверх (crash-resume через systemd) |

---

## Логирование

### setup_parser_logging(settings)

Настраивается в `worker.py` при старте. Пишет в файл (с ротацией) и консоль.

| Параметр | Конфиг | По умолчанию | Описание |
|----------|--------|-------------|----------|
| Уровень | `PARSER_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL, OFF |
| Файл | `PARSER_LOG_FILE` | `logs/parser.log` | Путь к файлу логов |

- **Ротация:** 5 MB × 3 файла (parser.log, parser.log.1, parser.log.2, parser.log.3)
- **OFF:** полностью отключает логи парсера
- **Формат:** `2026-03-08 12:00:00 [INFO] src.parser.worker: сообщение`

### Просмотр логов

```bash
# В реальном времени
tail -f logs/parser.log

# Через journalctl (systemd)
sudo journalctl -u devgrab-parser -f

# Только ошибки
grep "\[ERROR\]" logs/parser.log
```

---

## Конфигурация (.env)

```
# Основные
PROFIRU_LOGIN=...
PROFIRU_PASSWORD=...
PROFIRU_TOKEN=           # Если есть — используется как начальный

# Parser
PARSE_INTERVAL_SEC=600
TIME_THRESHOLD_HOURS=24
STOP_WORDS=["WordPress","Битрикс","Опрос"]

# Parser Logging
PARSER_LOG_LEVEL=DEBUG               # DEBUG, INFO, WARNING, ERROR, CRITICAL, OFF
PARSER_LOG_FILE=logs/parser.log      # Путь к файлу логов

# Parser Resilience
PARSER_TOKEN_TTL_SEC=14400            # TTL токена в Redis (4 часа)
PARSER_MAX_AUTH_ATTEMPTS=3            # Макс. попыток авторизации
PARSER_JITTER_FACTOR=0.2             # Jitter ±20%
PARSER_NIGHT_MULTIPLIER=3.0          # Множитель ночью
PARSER_CIRCUIT_BREAKER_THRESHOLD=5   # Ошибок до CB OPEN
PARSER_CIRCUIT_BREAKER_COOLDOWN_SEC=1800  # 30 мин cooldown
PARSER_ALERT_DEDUP_SEC=900           # 15 мин дедупликация
PARSER_REQUEST_DELAY_MIN=1.0         # Мин. пауза между запросами
PARSER_REQUEST_DELAY_MAX=3.0         # Макс. пауза между запросами
PARSER_AUTH_COOLDOWN_SEC=600         # Мин. интервал между авторизациями (10 мин)
PARSER_KEEP_ALIVE_INTERVAL_SEC=120   # Интервал keep-alive пингов (2 мин)
```

## Redis ключи

| Ключ | Тип | TTL | Описание |
|------|-----|-----|----------|
| `devgrab:parser:token` | String | 14400с (4ч) | Кэш токена (`prfr_bo_tkn`) |
| `devgrab:parser:cookies` | String | 14400с (4ч) | JSON dict всех cookies сессии |
| `devgrab:parser:health` | String | - | JSON статуса парсера |
| `devgrab:parser:auth_attempts` | String | 3600с | Счётчик попыток авторизации |
| `devgrab:parser:paused` | String | без TTL | Флаг паузы — устанавливается PM через панель менеджера |

## Systemd

```ini
# devgrab-parser.service
ExecStart=/usr/bin/python3 -m src.parser.worker
Restart=on-failure
RestartSec=30s
After=redis.service
```

## Зависимости

- `selenium>=4.15` — браузерная автоматизация
- `webdriver-manager>=4.0` — управление ChromeDriver
- `httpx>=0.27` — async HTTP клиент
- `redis>=5.0` — кэширование токена и health

## Gotchas

- Headless Chrome требует системных зависимостей на VPS
- CSS-селекторы зависят от UI Профи.ру — могут сломаться при обновлении
- User-Agent должен быть **одинаковый** в Selenium и httpx (UNIFIED_USER_AGENT)
- Если profiru_token задан — используется как начальный, но может быть перезаписан через Selenium
- MIN_ORDER_AGE_SECONDS=70 — слишком свежие заказы пропускаются
- **Не менять** `parser_auth_cooldown_sec` < 60 — риск бана
- Keep-alive НЕЛЬЗЯ делать GET на `profi.ru` — перезаписывает cookies и ломает `rnd.profi.ru` сессию
- Серверная сессия `rnd.profi.ru` имеет непредсказуемый TTL (3-24 мин), keep-alive не продлевает её
- При паузе (`devgrab:parser:paused`) воркер делает `asyncio.sleep(10)` и пропускает итерацию полностью — без авторизации, без запросов, без keepalive; пауза управляется PM через кнопку в панели менеджера
