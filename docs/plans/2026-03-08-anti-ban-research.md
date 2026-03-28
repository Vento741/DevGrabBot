# Исследование: Защита парсера от бана на Профи.ру

**Дата:** 2026-03-08
**Статус:** Фаза 1 реализована + Session Warmup + Cookie Persistence из Фазы 2
**Контекст:** Аккаунт забанен через 3 дня работы парсера

---

## Содержание

1. [Диагноз: почему забанили](#1-диагноз-почему-забанили)
2. [Аудит текущего кода — 12 уязвимостей](#2-аудит-текущего-кода)
3. [Профиль защиты Профи.ру](#3-профиль-защиты-профиру)
4. [Стратегия: 3 фазы защиты](#4-стратегия-3-фазы-защиты)
5. [Архитектура: Resilience Layer](#5-архитектура-resilience-layer)
6. [Anti-Detection технологии](#6-anti-detection-технологии)
7. [Прокси: сравнение и рекомендации](#7-прокси-сравнение-и-рекомендации)
8. [Альтернативные подходы](#8-альтернативные-подходы)
9. [Управление сессиями и токенами](#9-управление-сессиями-и-токенами)
10. [Rate Limiting & Backoff](#10-rate-limiting--backoff)
11. [План реализации](#11-план-реализации)

---

## 1. Диагноз: почему забанили

### Хронология
- Парсер запущен 2026-03-04
- До 03:54 (07 марта) — работал нормально, но токен жил ~10 мин
- С 04:05 — cookie `prfr_bo_tkn` перестал выдаваться
- 148 неудачных попыток авторизации за 5.5 часов (каждые 5 мин)
- Бан аккаунта — безвозвратный

### Причины бана (по результатам аудита)

| Фактор | Описание | Критичность |
|--------|----------|-------------|
| **Auth storm** | 6-12 Selenium-логинов в час (норма: 1-2 в день) | КРИТИЧЕСКАЯ |
| **Burst запросов** | 21 запрос за итерацию (1 GraphQL + 20 getOrder) без пауз | КРИТИЧЕСКАЯ |
| **Фиксированный интервал** | Ровно 300 сек — паттерн бота | ВЫСОКАЯ |
| **148 retry без backoff** | При ошибке — продолжал долбить с тем же интервалом | ВЫСОКАЯ |
| **Несовпадение UA** | Selenium: Chrome/120, httpx: Chrome/140+YaBrowser | ВЫСОКАЯ |
| **Один IP** | VPS datacenter IP, не residential | СРЕДНЯЯ |

**Главная причина: слишком частая Selenium-авторизация.** Токен жил ~10 мин, парсер ходил каждые 5 мин → каждая вторая итерация запускала полный логин через браузер. Это 6-12 логинов в час — для антифрод-системы это явный бот.

---

## 2. Аудит текущего кода

### КРИТИЧЕСКИЕ проблемы

| # | Проблема | Файл:строка | Решение |
|---|----------|-------------|---------|
| 1 | Фиксированный интервал 300с без jitter | `worker.py:41` | Добавить ±20% jitter |
| 2 | 21 запрос за итерацию без пауз | `scraper.py:157-163` | Паузы 1-3с между запросами |
| 3 | Несовпадение User-Agent Selenium vs httpx | `scraper.py:31-38, 263-267` | Единый реалистичный UA |
| 4 | Нет кэширования токена | `scraper.py:148` | Redis cache с TTL |

### СЕРЬЁЗНЫЕ проблемы

| # | Проблема | Файл:строка | Решение |
|---|----------|-------------|---------|
| 5 | Нет exponential backoff при ошибках | `worker.py:38-41` | Backoff 300→600→1200→... |
| 6 | Нет обработки 429 + Retry-After | `scraper.py:203-209` | Читать заголовок, backoff |
| 7 | Selenium пересоздаётся каждый раз | `scraper.py:241-341` | Кэшировать driver/cookies |
| 8 | Нет поддержки прокси | `scraper.py:132`, `config.py` | Proxy в httpx и Selenium |
| 9 | Нет кэша цен отклика | `scraper.py:156-163` | Redis cache TTL 1ч |

### Незначительные проблемы

| # | Проблема | Файл:строка |
|---|----------|-------------|
| 10 | Хардкод fingerprint-токена в GraphQL query | `scraper.py:43-44` |
| 11 | Отладочный порт 9222 открыт | `scraper.py:260` |
| 12 | Таймаут Selenium 5с слишком короткий | `scraper.py:308-310` |

---

## 3. Профиль защиты Профи.ру

### Что использует Профи.ру

- **НЕ использует** коммерческие WAF (Cloudflare, Qrator, DataDome)
- **Собственная защита** на бэкенде
- **Сессионная авторизация** — cookie `prfr_bo_tkn`
- **SMS-верификация** — кнопка `enter_with_sms_btn` при авторизации
- **Безвозвратная блокировка аккаунтов** — без объяснения причин

### Предполагаемые механизмы

- Rate limiting на GraphQL API
- Анализ паттернов запросов (частота, объём, timing)
- Fingerprinting браузера
- IP-мониторинг
- Контроль частоты авторизаций

### Структура API

| Endpoint | Назначение |
|----------|-----------|
| `https://rnd.profi.ru/graphql` | GraphQL — заказы |
| `https://profi.ru/backoffice/api/` | REST — детали заказов (getOrder) |
| `https://profi.ru/backoffice/n.php` | Страница авторизации |

---

## 4. Стратегия: 3 фазы защиты

### Фаза 1: Базовая защита (бесплатно, 1-2 дня) — ✅ РЕАЛИЗОВАНО

**Цель:** Устранить все критические уязвимости, которые 100% привели к бану.

1. ✅ **Circuit Breaker** — после 5 ошибок полная остановка на 30 мин (`src/parser/resilience/circuit_breaker.py`)
2. ✅ **Exponential Backoff** — при ошибках: 5мин → 10мин → 20мин → 40мин (`request_scheduler.py`)
3. ✅ **Jitter** — ±20% к интервалу парсинга (240-360с вместо ровных 300) (`request_scheduler.py`)
4. ✅ **Alerting** — уведомления в Telegram при проблемах (`alert_service.py`)
5. ✅ **Кэширование токена в Redis** — переживает перезапуск (`token_manager.py`)
6. ✅ **Lazy re-auth с rate limit** — не чаще 1 авторизации в 2 мин (`token_manager.py`)
7. ✅ **Паузы между запросами** — 1-3с между getOrder запросами (`scraper.py`)
8. ✅ **Единый User-Agent** — одинаковый для Selenium и httpx (`UNIFIED_USER_AGENT`)

### Фаза 2: Anti-Detection (бесплатно, 2-4 дня) — частично реализовано

**Цель:** Сделать парсер менее детектируемым.

1. ⬜ **undetected-chromedriver** или **SeleniumBase UC Mode** — вместо обычного Selenium
2. ✅ **Адаптивные интервалы** — ночью реже, днём чаще (реализовано в Фазе 1: `request_scheduler.py`)
3. ⬜ **Human-like паттерны** — нормальное распределение задержек
4. ⬜ **curl_cffi для GraphQL** — TLS fingerprint реального браузера
5. ✅ **Session warmup** — после авторизации "прогрев" перед запросами (`scraper.py:_warmup_session`)

### Фаза 3: Усиленная защита (платно, по необходимости)

**Цель:** Максимальная устойчивость к банам.

1. **Residential прокси** — SOAX Starter ~$90/мес
2. **Multi-account ротация** — несколько аккаунтов Профи.ру
3. **Email IMAP-парсинг** — параллельный канал получения заказов

---

## 5. Архитектура: Resilience Layer

### Новая структура файлов

```
src/parser/
├── base.py                    # без изменений
├── worker.py                  # МОДИФИЦИРОВАТЬ
├── resilience/
│   ├── __init__.py
│   ├── token_manager.py       # НОВЫЙ — кэш токена + backoff
│   ├── circuit_breaker.py     # НОВЫЙ — остановка при ошибках
│   ├── request_scheduler.py   # НОВЫЙ — jitter + ночной режим
│   ├── alert_service.py       # НОВЫЙ — алерты в Telegram
│   └── health.py              # НОВЫЙ — мониторинг
├── profiru/
│   ├── scraper.py             # МОДИФИЦИРОВАТЬ
│   └── filters.py             # без изменений
```

### Диаграмма компонентов

```
                    ┌──────────────────────────────────┐
                    │         Parser Worker             │
                    │   (src/parser/worker.py)          │
                    └──────┬───────────────┬────────────┘
                           │               │
              ┌────────────▼──┐    ┌───────▼─────────┐
              │RequestScheduler│   │  ProfiruParser   │
              │  (jitter,night,│   │  (scraper.py)    │
              │   adaptive)    │   └───────┬──────────┘
              └────────────────┘           │
                                  ┌────────▼─────────┐
                                  │  TokenManager     │
                                  │  (кэш, backoff,  │
                                  │   Selenium auth)  │
                                  └──┬──────────┬─────┘
                                     │          │
                           ┌─────────▼──┐  ┌────▼──────────┐
                           │   Redis    │  │ CircuitBreaker │
                           │ (token     │  │ (threshold=5,  │
                           │  cache)    │  │  cooldown=30m) │
                           └────────────┘  └────┬──────────┘
                                                │
                                       ┌────────▼─────────┐
                                       │  AlertService    │
                                       │  (TG messages,   │
                                       │   dedup 15 min)  │
                                       └──────────────────┘

                    ┌──────────────────────────────────┐
                    │       HealthMonitor               │
                    │  (metrics → Redis, status check)  │
                    └──────────────────────────────────┘
```

### Поток данных

```
Worker loop:
  1. scheduler.get_next_delay() → sleep (с jitter)
  2. circuit_breaker.is_open()? → skip if open
  3. parser.fetch_orders()
     → token_manager.get_token()
       → in-memory cache? → return
       → Redis cache? → return
       → _refresh_token()
         → circuit_breaker.is_open()? → None
         → backoff delay
         → Selenium auth (in executor)
         → success? → cache to Redis, CB.record_success()
         → fail? → CB.record_failure(), alert.error()
     → GraphQL request with token
     → 401? → token_manager.invalidate() → retry once
  4. filter + dedup + push to Redis
  5. scheduler.record_success() / record_error()
  6. health.save_to_redis()
```

### Новые параметры конфигурации

```python
# Parser Resilience
parser_token_ttl_sec: int = 480              # TTL токена (8 мин)
parser_max_auth_attempts: int = 3            # макс. попыток авторизации
parser_jitter_factor: float = 0.2            # jitter ±20%
parser_night_multiplier: float = 3.0         # множитель ночью
parser_circuit_breaker_threshold: int = 5    # ошибок до срабатывания
parser_circuit_breaker_cooldown_sec: int = 1800  # 30 мин cooldown
parser_alert_dedup_sec: int = 900            # 15 мин дедупликация
```

### Redis ключи

| Ключ | Тип | TTL | Описание |
|------|------|-----|----------|
| `devgrab:parser:token` | String | 480с | Кэш токена (`prfr_bo_tkn`) |
| `devgrab:parser:cookies` | String | 480с | JSON dict всех cookies сессии |
| `devgrab:parser:health` | String | - | JSON статуса |
| `devgrab:parser:auth_attempts` | String | 3600с | Счётчик попыток |

---

## 6. Anti-Detection технологии

### Ранжированный список решений

| # | Технология | Пакет | Сложность | Эффективность | Рекомендация |
|---|-----------|-------|-----------|---------------|--------------|
| 1 | **NoDriver** | `nodriver` | 3/5 | 9/10 | Лучший, но нужен рефакторинг |
| 2 | **SeleniumBase UC Mode** | `seleniumbase` | 2/5 | 8/10 | **Оптимальный баланс** |
| 3 | **Camoufox** | `camoufox` | 4/5 | 9/10 | Overkill для Профи.ру |
| 4 | **undetected-chromedriver** | `undetected-chromedriver` | 1/5 | 7/10 | **Минимальный рефакторинг** |
| 5 | **selenium-stealth** | `selenium-stealth` | 1/5 | 5/10 | Заброшен, базовый уровень |
| 6 | **curl_cffi** | `curl_cffi` | 2/5 | 6/10 | Для GraphQL/API запросов |
| 7 | **Playwright + stealth** | `playwright` | 4/5 | 6/10 | Плагин не обновляется |

### Рекомендуемый выбор

**Вариант A — Быстрый (рекомендую для старта):**
- `undetected-chromedriver` — замена 1 импорта
- Убирает главный вектор детекции (navigator.webdriver, cdc_ строки)
- Время интеграции: 1-2 часа

**Вариант B — Оптимальный:**
- `SeleniumBase UC Mode` — Selenium-совместимый API
- Встроенный anti-detect из коробки
- Время интеграции: 2-4 часа

**Дополнительно для GraphQL запросов:**
- `curl_cffi` вместо `httpx` — подменяет TLS fingerprint на реальный Chrome

---

## 7. Прокси: сравнение и рекомендации

### Типы прокси

| Тип | Цена/GB | Для Профи.ру | Комментарий |
|-----|---------|--------------|-------------|
| Datacenter | $0.5-2 | НЕ подходит | Легко детектятся |
| **Residential** | $3-8 | **Оптимальный** | Выглядят как обычные пользователи |
| Mobile (4G) | $5-15 | Подходит | Дорого, максимальная анонимность |
| ISP (Static) | $2-5 | Хороший | Быстрые, редко блокируют |

### Российские провайдеры

| Провайдер | Тип | Цена | RU IP | Мин. план |
|-----------|-----|------|-------|-----------|
| **SOAX** | Residential | $3.60/GB | 2M+ | $90/мес |
| Oxylabs | Residential | $4-8/GB | 2M+ | $100+ |
| Bright Data | Residential | $8.40/GB | Огромный | $500+ |
| OnlineProxy.io | Mobile 4G | ~$5-10/GB | МТС/Билайн | ~$50 |
| Litport.net | Residential | от $0.09/день | RU | $20+ |

### Рекомендация

**Фаза 1:** Без прокси — оптимизировать rate limiting
**Фаза 2 (если забанят):** SOAX Starter — $90/мес, 25 GB, авторотация RU IP
**Фаза 3 (масштабирование):** Oxylabs или Bright Data

### Можно ли без прокси?

**Да, при условии:**
- Задержки 5-15 сек между запросами
- User-Agent ротация
- Парсинг только в рабочие часы (7:00-23:00)
- Exponential backoff при ошибках
- Максимум 100-300 заказов/день

---

## 8. Альтернативные подходы

| # | Подход | Риск бана | Сложность | Рекомендация |
|---|--------|-----------|-----------|--------------|
| 1 | **Email IMAP-парсинг** | 2/10 | 3/10 | **РЕКОМЕНДУЕТСЯ** — самый безопасный |
| 2 | **GraphQL без Selenium** | 5/10 | 5/10 | **РЕКОМЕНДУЕТСЯ** — убрать Selenium |
| 3 | Mobile API реверс | 6/10 | 8/10 | Запасной вариант |
| 4 | Push-уведомления | 3/10 | 9/10 | Не рекомендуется |
| 5 | Партнёрский API | 1/10 | 7/10 | Не подходит (нет заказов) |
| 6 | RSS | - | - | Не существует |

### Email IMAP-парсинг (основная альтернатива)

Профи.ру отправляет email-уведомления о новых заказах. Можно парсить через IMAP:
- **Риск бана:** минимальный (чтение своей почты)
- **Сложность:** низкая (Python `imaplib` / `imap-tools`)
- **Минусы:** задержка 1-5 мин, неполные данные в письме
- **Стратегия:** Email как триггер → дозапрос деталей через GraphQL

### GraphQL без Selenium (оптимизация)

Если авторизация не требует captcha — можно логиниться через httpx:
- Убирает зависимость от Selenium
- Ускоряет работу, снижает потребление ресурсов
- Нужно исследовать auth flow через DevTools

---

## 9. Управление сессиями и токенами

### Ключевой вывод

**Главная причина бана — слишком частая авторизация (6-12 логинов/час).** Обычный пользователь логинится 1-2 раза в день.

### Стратегии (по приоритету)

| # | Стратегия | Приоритет | Эффект |
|---|-----------|-----------|--------|
| 1 | **Lazy re-auth + rate limit** | КРИТИЧЕСКИЙ | Убирает 90% лишних логинов |
| 2 | **Redis token cache** | ВЫСОКИЙ | Токен переживает перезапуск |
| 3 | **Auth backoff** | ВЫСОКИЙ | Защита при проблемах |
| 4 | **Cookie persistence** | ВЫСОКИЙ | Полная сессия сохраняется — ✅ РЕАЛИЗОВАНО |
| 5 | **Token refresh через API** | СРЕДНИЙ | 0 Selenium-логинов |
| 6 | **Session warmup** | НИЗКИЙ | Антидетекция |
| 7 | **Multi-account rotation** | НИЗКИЙ | Резервные аккаунты |

---

## 10. Rate Limiting & Backoff

### Текущая проблема
148 неудачных попыток за 5.5 часов → бан. Нет backoff, нет circuit breaker.

### Решение (по приоритету)

| # | Стратегия | Приоритет | Эффект | Реализация |
|---|-----------|-----------|--------|------------|
| 1 | **Circuit Breaker** | КРИТИЧЕСКИЙ | 148→5 попыток | `aiobreaker` или свой |
| 2 | **Exponential Backoff** | КРИТИЧЕСКИЙ | Автозамедление | `tenacity` или свой |
| 3 | **Jitter** | КРИТИЧЕСКИЙ | Рандомизация паттерна | `random.uniform(-0.2, 0.2)` |
| 4 | **Alerting** | ВЫСОКИЙ | Узнаём о проблемах сразу | Telegram Bot API |
| 5 | **Adaptive intervals** | СРЕДНИЙ | Оптимизация по времени суток | Ночь x3, рабочие x1 |
| 6 | **Human-like patterns** | СРЕДНИЙ | Нормальное распределение | `random.gauss()` |

### Формулы

**Exponential backoff с jitter:**
```python
delay = min(base_delay * (2 ** attempt), max_delay)
jitter = random.uniform(0, delay * 0.5)
total_delay = delay + jitter
```

**Адаптивные интервалы (МСК):**
| Время | Интервал | Причина |
|-------|----------|---------|
| 00:00-07:00 | 30 мин | Ночь, нет заказов |
| 07:00-10:00 | 10 мин | Утро, заказчики просыпаются |
| 10:00-19:00 | 5 мин | Рабочее время |
| 19:00-00:00 | 15 мин | Вечер |

---

## 11. План реализации

### Фаза 1: Базовая защита (1-2 дня) — ✅ ВЫПОЛНЕНО

1. ✅ Создать `src/parser/resilience/` (5 модулей)
2. ✅ `circuit_breaker.py` — 3 состояния, threshold=5, cooldown=30мин
3. ✅ `alert_service.py` — алерты в Telegram с дедупликацией
4. ✅ `token_manager.py` — Redis кэш + backoff + lazy re-auth
5. ✅ `request_scheduler.py` — jitter + ночной режим + adaptive
6. ✅ `health.py` — мониторинг и метрики
7. ✅ Модифицировать `worker.py` — интеграция всех компонентов
8. ✅ Модифицировать `scraper.py` — вынос авторизации, паузы между запросами
9. ✅ Обновить `config.py` — 10 новых параметров
10. ✅ Тестирование + code review (`/simplify`)

### Фаза 2: Anti-Detection (2-4 дня) — частично

1. ⬜ Установить `undetected-chromedriver` или `seleniumbase`
2. ✅ Рефакторинг Selenium-авторизации (выполнено в Фазе 1)
3. ✅ Единый User-Agent для всех запросов (выполнено в Фазе 1)
4. ⬜ Рассмотреть `curl_cffi` для GraphQL
5. ✅ Session warmup после авторизации (`_warmup_session`)
6. ✅ **Cookie Persistence** — полная сессия как в браузере (все cookies кэшируются в Redis)

### Фаза 3: Усиленная защита (по необходимости)

1. Интеграция прокси (SOAX или аналог)
2. Email IMAP-парсинг как параллельный канал
3. Multi-account ротация

---

## Источники

### Anti-Detection
- [NoDriver GitHub](https://github.com/ultrafunkamsterdam/nodriver)
- [SeleniumBase UC Mode](https://seleniumbase.io/help_docs/uc_mode/)
- [Camoufox GitHub](https://github.com/daijro/camoufox)
- [undetected-chromedriver](https://pypi.org/project/undetected-chromedriver/)
- [curl_cffi GitHub](https://github.com/lexiforest/curl_cffi)

### Session Management
- [Zyte: Advanced Session Management](https://www.zyte.com/learn/advanced-use-cases-for-session-management-in-web-scraping/)
- [ScrapingBee: Best Practices](https://www.scrapingbee.com/blog/web-scraping-best-practices/)
- [ZenRows: Bot Detection Bypass](https://www.zenrows.com/blog/bypass-bot-detection)

### Прокси
- [SOAX Russia Proxies](https://soax.com/proxies/locations/russia)
- [Oxylabs Residential](https://oxylabs.io/products/mobile-proxies)
- [httpx Proxies Docs](https://www.python-httpx.org/advanced/proxies/)

### Альтернативы
- [sspat/profiru — Partnership API](https://github.com/sspat/profiru)
- [Профи.ру справка — уведомления](https://help.profi.ru/ru/articles/2689262)

### Оригинальный парсер
- [dobrozor/parser_profiru](https://github.com/dobrozor/parser_profiru)
