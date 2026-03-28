# AI Engine — Анализатор заявок

> **Последнее обновление:** 2026-03-05 (CP-3.6/3.7 — интеграция SettingsService: промпты и модель из DB)
> **ВАЖНО:** При любых изменениях в модуле `src/ai/` — обновить этот документ!

## Назначение

AI-движок для анализа фриланс-заявок через OpenRouter API. Оценивает релевантность, формирует черновик отклика, генерирует roadmap.

## Файлы

| Файл | Назначение |
|------|-----------|
| `src/ai/openrouter.py` | HTTP-клиент OpenRouter API |
| `src/ai/analyzer.py` | Бизнес-логика: analyze_order(), generate_response(), generate_response_v2(), generate_roadmap() |
| `src/ai/context.py` | OrderContext — полный контекст заявки для всех этапов генерации |
| `src/ai/worker.py` | AI-воркер (Redis → анализ → DB → Redis) |
| `src/ai/prompts/analyze.py` | Промпт анализа заявки |
| `src/ai/prompts/response.py` | Промпт формирования отклика (build_response_prompt, build_response_prompt_v2) |
| `src/ai/prompts/roadmap.py` | Промпт генерации roadmap (build_roadmap_prompt, build_roadmap_prompt_v2) |

---

## Поток обработки

```
Redis:new_orders → pop_order()
    ↓
AI Worker (worker.py)
    ├─ Проверка дубликата в БД (external_id)
    ├─ Сохранение Order (status=analyzing)
    ├─ OrderAnalyzer.analyze_order(raw_text)
    │   └─ OpenRouterClient.complete_json()
    ├─ Сохранение AiAnalysis в БД
    ├─ Order.status → reviewed
    └─ push_analyzed() → Redis:analyzed
```

---

## OpenRouterClient (openrouter.py)

**API URL:** `https://openrouter.ai/api/v1/chat/completions`
**Temperature:** 0.3 (фиксировано, детерминированные ответы)
**Timeout:** 60 сек

### Методы

| Метод | Возврат | Описание |
|-------|---------|---------|
| `complete(system, user)` | str | Текстовый ответ от AI |
| `complete_json(system, user)` | dict | JSON-ответ (с очисткой markdown) |
| `close()` | None | Закрытие HTTP-клиента |

### Очистка markdown
```python
if text.startswith("```"):
    lines = text.split("\n")
    text = "\n".join(lines[1:-1])  # Убираем обёртку
return json.loads(text)
```

---

## OrderAnalyzer (analyzer.py)

### analyze_order(raw_text, system_prompt=None) → dict

**Вход:** полный текст заявки; опционально `system_prompt` (строка из DB)
**Выход:** структурированный JSON-анализ

Если `system_prompt` передан — используется он. Если `None` — fallback на файловый `ANALYZE_PROMPT`.

```json
{
    "summary": "Краткая выжимка (3-5 предложений)",
    "client_requirements": "Конкретные требования",
    "client_budget_stated": true,
    "client_budget_text": "50 000 - 100 000 руб.",
    "client_deadline_stated": false,
    "client_deadline_text": "Не указаны",
    "questions_to_client": ["Вопрос 1", "Вопрос 2"],
    "stack": ["Python", "FastAPI", "aiogram"],
    "price_min": 35000,
    "price_max": 50000,
    "timeline_days": 12,
    "relevance_score": 85,
    "complexity": "medium",
    "risks": "Описание рисков",
    "response_draft": "Черновик отклика"
}
```

### generate_response(summary, stack, price, timeline, custom_notes) → str

Генерация финального отклика для заказчика (3-5 предложений, без шаблонов). Устаревший метод — использовать `generate_response_v2`.

### generate_response_v2(context, style) → str

Генерация отклика с полным контекстом через `OrderContext`. Передаёт клиентские данные, вопросы, черновик из анализа и Pre Roadmap (если есть). Поддерживает настройки стиля менеджера (`style` dict с ключами `tone`, `intro`, `rules`).

### generate_roadmap(context) → str

Генерация Pre Roadmap с полным контекстом через `OrderContext`. Передаёт бюджет разработчика (без этого модель фантазирует цифры), сроки, стек, данные клиента.

---

## OrderContext (context.py)

Датакласс-агрегатор всех данных заявки для передачи между этапами генерации. Решает проблему потери контекста между `analyze_order` → `generate_roadmap` → `generate_response_v2`.

### Поля

| Группа | Поля |
|--------|------|
| Оригинал заявки | `raw_text`, `title`, `external_id` |
| Данные клиента | `client_name`, `client_budget`, `client_deadline`, `client_requirements` |
| AI-анализ | `summary`, `stack`, `price_min`, `price_max`, `timeline_days`, `complexity`, `relevance_score`, `questions`, `risks`, `response_draft` |
| Решения разработчика | `price_final`, `timeline_final`, `stack_final`, `custom_notes` |
| Pre Roadmap | `roadmap_text` |

### Свойства

| Свойство | Описание |
|----------|---------|
| `effective_stack` | `stack_final` если задан, иначе `stack` из AI |
| `effective_price` | `price_final` если задан, иначе `price_max` из AI |
| `effective_timeline` | `timeline_final` если задан, иначе `timeline_days` из AI |

### Конструктор из моделей БД

```python
context = OrderContext.from_order_data(
    order=order,          # Order model
    analysis=analysis,    # AiAnalysis model (опционально)
    assignment=assignment # OrderAssignment model (опционально)
)
```

---

## Промпты

### analyze.py — Анализ заявки

**Роль:** AI-ассистент команды из 2 senior fullstack-разработчиков

**Компетенции (берём):**
- Python: FastAPI, Django, aiogram, Selenium, Scrapy, asyncio
- JS/TS: React, Next.js, Node.js, Express
- AI/ML: OpenAI API, LangChain, RAG, векторные БД
- БД: PostgreSQL, Redis, MongoDB, ClickHouse
- DevOps: Linux, Docker, CI/CD, systemd, Nginx

**Не берём:** WordPress, Битрикс, 1С, мобилки, gamedev, дизайн без разработки

**Оценка relevance_score:**
| Диапазон | Описание |
|---------|---------|
| 90-100 | Идеально: наш стек + бюджет + ТЗ |
| 70-89 | Хорошо: наш стек, есть нюансы |
| 40-69 | Частично: часть в нашем стеке |
| 10-39 | Плохо: не наш профиль |
| 0-9 | Не подходит |

**Complexity:**
| Уровень | Сроки | Примеры |
|---------|-------|---------|
| low | до 3 дней | лендинг, бот, парсер |
| medium | 3-14 дней | сайт с бэкендом, API |
| high | >14 дней | платформа, ML |

### response.py — Отклик

**Стиль:** конкретный, деловой, без воды
- Не начинать с "Здравствуйте"
- Релевантный опыт (1 предложение)
- Конкретные сроки и стоимость
- Следующий шаг (созвон/ТЗ)
- 3-5 предложений максимум

### roadmap.py — Дорожная карта

Структурированный HTML-текст для Telegram:
- Этапы: Discovery → MVP → Доработки → Деплой
- Конкретные задачи по этапам
- Итого: дни + стек + бюджет

---

## AI Worker (worker.py)

### run_ai_worker(settings)

```python
while True:
    order_data = await redis.pop_order()
    if not order_data:
        await asyncio.sleep(5)  # Пауза при пустой очереди
        continue

    async with session_factory() as session:
        # 1. Проверка дубликата
        existing = select(Order).where(Order.external_id == external_id)

        # 2. Загрузка промпта из DB (fallback на файловый ANALYZE_PROMPT)
        analyze_prompt = await get_prompt(session, "analyze") or ANALYZE_PROMPT

        # 3. Загрузка модели из DB (fallback на settings.openrouter_model)
        current_model = await get_config_setting(session, "openrouter_model", settings)
        if current_model != ai_client.model:
            ai_client.model = current_model  # обновляем на лету

        # 4. Создание Order (status=analyzing)
        # 5. AI анализ → analyze_order(raw_text, system_prompt=analyze_prompt)
        # 6. Сохранение AiAnalysis + extra_data (JSON)
        # 7. Order.status = reviewed
        # 8. push_analyzed() → Redis
```

**Обработка ошибок:** exception логируется, цикл продолжает

**Интеграция с SettingsService:** на каждой итерации перед анализом загружаются актуальные промпт и модель из DB. Смена модели в DB применяется без перезапуска сервиса.

### extra_data (JSON-колонка)

Дополнительные поля из AI-анализа:
- `client_requirements` — требования клиента
- `client_budget_stated/text` — бюджет
- `client_deadline_stated/text` — сроки
- `questions_to_client` — уточняющие вопросы
- `risks` — риски

---

## Systemd

```ini
# devgrab-scheduler.service (по сути AI Worker)
ExecStart=/usr/bin/python3 -m src.ai.worker
Restart=on-failure
RestartSec=10s
After=postgresql.service redis.service
```

## Конфигурация (.env)

```
OPENROUTER_API_KEY=<ключ>
OPENROUTER_MODEL=google/gemini-3.1-flash-lite-preview
```

Смена модели: изменить `OPENROUTER_MODEL` → `systemctl restart devgrab-scheduler`
Список моделей: https://openrouter.ai/models

## Зависимости

- `httpx>=0.27` — async HTTP клиент
- `sqlalchemy[asyncio]>=2.0` — ORM

## Интеграция с SettingsService (CP-3.6/3.7)

Все три компонента поддерживают динамическую загрузку настроек из `src/core/settings_service.py`:

| Компонент | Ключ | Fallback |
|-----------|------|---------|
| `worker.py` | `get_prompt(session, "analyze")` | файловый `ANALYZE_PROMPT` |
| `worker.py` | `get_config_setting(session, "openrouter_model", settings)` | `settings.openrouter_model` |
| `review.py / handle_approve` | `get_prompt(session, "response")` | файловый `DEFAULT_RESPONSE_PROMPT` |
| `review.py / handle_roadmap` | `get_prompt(session, "roadmap")` | файловый `DEFAULT_ROADMAP_PROMPT` |

Смена модели в DB применяется без перезапуска — `ai_client.model` обновляется на лету при каждой итерации воркера.

---

## Gotchas

- Temperature=0.3 захардкожена (для консистентности)
- Timeout API=60 сек (для больших заявок может быть мало)
- Redis poll interval=5 сек при пустой очереди
- Проверка дубликатов ДО загрузки промпта и ДО анализа — экономия API-квоты
- JSON-ответы от AI иногда в markdown-обёртке — есть обработка
- `analyze_order(system_prompt=None)` — обратная совместимость: старый код без параметра работает
