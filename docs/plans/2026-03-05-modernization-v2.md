# DevGrabBot v2.0 — План модернизации

> Последнее обновление: 2026-03-05
> Статус: В разработке
> Авторы: Денис (web-dusha), Imei Rhen, Кирилл (PM)

---

## Содержание

1. [Аудит текущего состояния](#1-аудит-текущего-состояния)
2. [Критические баги AI-сервиса](#2-критические-баги-ai-сервиса)
3. [Панель разработчика](#3-панель-разработчика)
4. [Панель менеджера](#4-панель-менеджера)
5. [Изменения в моделях данных](#5-изменения-в-моделях-данных)
6. [Изменения в AI-промптах](#6-изменения-в-ai-промптах)
7. [Чекпоинты реализации](#7-чекпоинты-реализации)
8. [Тестирование](#8-тестирование)

---

## 1. Аудит текущего состояния

### 1.1 Что работает (v0.1.0)

| Компонент | Статус | Описание |
|-----------|--------|----------|
| Parser (Profiru) | OK | Selenium auth + GraphQL, фильтры, worker |
| AI Analysis | OK с багами | OpenRouter анализ, но проблемы с контекстом (см. п.2) |
| Bot Notification | OK | Redis → группа с кнопками [Взять]/[Пропустить] |
| Developer Flow | Базовый | Взятие заявки → редактирование → утверждение |
| Manager Flow | Минимальный | Только получение готового отклика + копирование |
| Auth Middleware | OK | Проверка team_members, блокировка неавторизованных |
| Tests | 51 passed | Покрытие: config, models, openrouter, analyzer, parser, filters |

### 1.2 Что отсутствует

| Функционал | Приоритет | Описание |
|------------|-----------|----------|
| Панель разработчика | HIGH | Нет UI для управления стоп-словами, промптами, стеком |
| Панель менеджера | HIGH | Нет UI для стиля откликов, выбора исполнителя |
| Стек разработчика | HIGH | TeamMember не хранит технологический стек |
| Матчинг по стеку | HIGH | Нет автоматического подбора разработчика под заявку |
| Управление промптами | MEDIUM | Промпты захардкожены в файлах, нет UI |
| Управление стоп-словами | MEDIUM | Стоп-слова только в .env, нет UI |
| Стиль менеджера | MEDIUM | Нет профиля стиля откликов для менеджера |
| История откликов | LOW | Нет просмотра истории отправленных откликов |
| Статистика | LOW | Нет аналитики по заявкам |

---

## 2. Критические баги AI-сервиса

### 2.1 БАГ: Потеря контекста между этапами генерации

**Проблема:** На каждом этапе генерации (анализ → отклик → roadmap) модель получает РАЗНЫЙ контекст, что приводит к несогласованности данных.

**Детальный анализ потока данных:**

#### Этап 1: Первичный анализ (✅ полный контекст)
```
Файл: src/ai/prompts/analyze.py → build_analyze_prompt()
Вход: raw_text (полный текст заявки)
Модель видит: ВСЁ — полный текст + системный промпт с профилем команды
Выход: JSON {summary, price_min, price_max, timeline, stack, ...}
```
**Здесь всё корректно** — модель видит оригинал заявки целиком.

#### Этап 2: Взятие заявки разработчиком (✅ данные из БД)
```
Файл: src/bot/handlers/orders.py → handle_take_order()
Что показывается: analysis.summary, analysis.stack, analysis.price_max,
                  extra_data.client_requirements, extra_data.questions, extra_data.risks
Откуда: AiAnalysis из БД (сохранённые данные этапа 1)
```
**Проблема:** `price_final` инициализируется как `analysis.price_max` — разработчик видит ТОЛЬКО максимальную цену, диапазон теряется.

```python
# src/bot/handlers/orders.py, строка ~95
assignment = OrderAssignment(
    order_id=order.id,
    developer_id=member.id,
    status=AssignmentStatus.editing,
    price_final=analysis.price_max,        # ← Только MAX!
    timeline_final=analysis.timeline_days,
    stack_final=analysis.stack,
)
```

#### Этап 3: Генерация Pre Roadmap (⚠️ частичный контекст)
```
Файл: src/ai/prompts/roadmap.py → build_roadmap_prompt()
Модель получает:
  - order.title (заголовок)
  - analysis.summary (AI-выжимка)
  - analysis.stack (стек)
  - order.raw_text (оригинал заявки)
Модель НЕ получает:
  - analysis.price_min / price_max (ценовой диапазон!)
  - assignment.price_final (цена разработчика!)
  - assignment.timeline_final (сроки разработчика!)
  - assignment.custom_notes (заметки разработчика!)
```

**Результат:** Roadmap генерирует СВОИ оценки бюджета (80 000-120 000 руб.), которые могут полностью противоречить:
- AI-оценке из этапа 1 (25 000-45 000 руб.)
- Цене разработчика (45 000 руб.)

#### Этап 4: Генерация финального отклика (⚠️ минимальный контекст)
```
Файл: src/ai/prompts/response.py → build_response_prompt()
Модель получает:
  - summary (AI-выжимка)
  - stack (стек)
  - price (одна цена — price_final)
  - timeline (сроки)
  - custom_notes (заметки)
Модель НЕ получает:
  - Оригинал заявки (raw_text!)
  - Пожелания клиента (client_requirements!)
  - Вопросы к клиенту (questions_to_client!)
  - Риски (risks!)
  - Бюджет клиента (client_budget_text!)
  - Сроки клиента (client_deadline_text!)
  - Pre Roadmap (если был сгенерирован!)
```

**Результат:** Финальный отклик — это "голая" генерация без полного контекста заявки. Модель фантазирует детали, которые могут не соответствовать оригиналу.

### 2.2 БАГ: Цена на разных этапах

| Этап | Что видно | Источник | Пример |
|------|-----------|----------|--------|
| Группа (уведомление) | `от 25 000 до 45 000 руб.` | AI analysis: price_min + price_max | Корректно |
| Разработчик (взятие) | `45 000 руб.` | assignment.price_final = analysis.price_max | Потерян MIN |
| Pre Roadmap | `80 000 – 120 000 руб.` | Модель сама оценивает (нет входных цен!) | Конфликт! |
| Финальный отклик | `45 000 руб.` | assignment.price_final | Без контекста MIN |

### 2.3 БАГ: response_draft не используется

В `analyze.py` модель генерирует `response_draft` — черновик отклика. Он показывается разработчику, но при нажатии "Утвердить" генерируется НОВЫЙ отклик через `response.py` промпт, игнорируя черновик полностью.

### 2.4 Решения

#### Решение A: Единый контекст (Context Chain)
Создать объект `OrderContext`, который собирает ВСЕ данные и передаётся на каждом этапе генерации.

```python
class OrderContext:
    """Полный контекст заявки для всех этапов AI-генерации"""
    # Оригинал
    raw_text: str
    title: str
    external_id: str

    # Данные клиента
    client_name: str
    client_budget: str
    client_deadline: str
    client_requirements: str
    work_format: str

    # AI-анализ
    summary: str
    stack: list[str]
    price_min: int
    price_max: int
    timeline_days: str
    complexity: str
    relevance_score: int
    questions: list[str]
    risks: str
    response_draft: str

    # Решения разработчика
    price_final: int
    timeline_final: str
    stack_final: list[str]
    custom_notes: str

    # Pre Roadmap (если сгенерирован)
    roadmap_text: str | None
```

#### Решение B: Обновить все промпты
Каждый промпт (`roadmap.py`, `response.py`) должен получать ПОЛНЫЙ контекст, а не выборочные поля.

---

## 3. Панель разработчика

### 3.1 Функционал

#### Главное меню разработчика (команда /dev или /panel)
```
🛠 Панель разработчика

├── 📋 Мои заявки
│   ├── Активные (в работе)
│   ├── Завершённые
│   └── Пропущенные
│
├── 🔧 Мой стек
│   ├── Просмотр текущего стека
│   ├── Редактировать стек
│   └── Приоритеты (основной/дополнительный)
│
├── 🚫 Стоп-слова
│   ├── Просмотр текущих
│   ├── Добавить стоп-слово
│   └── Удалить стоп-слово
│
├── 🤖 Промпты
│   ├── Просмотр промпта анализа
│   ├── Просмотр промпта отклика
│   ├── Просмотр промпта roadmap
│   └── Редактировать промпт (через DB Settings)
│
├── 👥 Команда
│   ├── Список участников
│   ├── Добавить разработчика
│   ├── Добавить менеджера
│   └── Деактивировать участника
│
├── ⚙️ Настройки
│   ├── AI-модель (текущая + выбор)
│   ├── Интервал парсинга
│   ├── Порог времени заявок
│   └── Минимальный бюджет
│
└── 📊 Статистика
    ├── Всего заявок обработано
    ├── Среднее время отклика
    ├── Конверсия (взято/пропущено)
    └── Распределение по стеку
```

### 3.2 Мой стек — детальное описание

**Цель:** Каждый разработчик указывает свои технологии. При появлении новой заявки бот автоматически отмечает (@mention) разработчиков с наиболее подходящим стеком.

**Модель данных — новое поле `TeamMember`:**
```python
# Добавить в models.py
class TeamMember(Base):
    ...
    tech_stack: Mapped[list] = mapped_column(JSON, default=[])
    # Формат: [{"name": "Python", "level": "primary"}, {"name": "React", "level": "secondary"}]

    stack_priority: Mapped[dict] = mapped_column(JSON, default={})
    # Формат: {"primary": ["Python", "FastAPI", "aiogram"], "secondary": ["React", "Next.js"]}
```

**FSM для редактирования стека:**
```
/dev → "Мой стек" → показать текущий стек
  → "Редактировать" → FSM: ввод primary стека (через запятую)
  → "Добавить secondary" → FSM: ввод secondary стека
  → "Очистить" → сброс стека
```

**Матчинг стека при новой заявке:**
```python
def match_developers(order_stack: list[str], developers: list[TeamMember]) -> list[tuple[TeamMember, float]]:
    """Возвращает список (разработчик, score) отсортированный по совпадению"""
    results = []
    for dev in developers:
        primary = set(s.lower() for s in dev.stack_priority.get("primary", []))
        secondary = set(s.lower() for s in dev.stack_priority.get("secondary", []))
        order_set = set(s.lower() for s in order_stack)

        primary_match = len(primary & order_set)
        secondary_match = len(secondary & order_set)
        score = primary_match * 2 + secondary_match  # Primary весит x2

        if score > 0:
            results.append((dev, score))

    return sorted(results, key=lambda x: x[1], reverse=True)
```

**В уведомлении группы (notification.py):**
```
Новая заявка | #87236044
Создание сайта-визитки
...
Стек: Next.js, React, Tailwind CSS, Vercel

👤 Подходящие разработчики:
  @web_dusha (Next.js, React — 90%)
  @imei_rhen (React — 45%)

[Взять] [Пропустить]
```

### 3.3 Управление стоп-словами

**Текущее состояние:** Стоп-слова в `config.py` → `stop_words: list[str]`, загружаются из `.env`.

**Новое:** Перенести стоп-слова в таблицу `settings` (key=`stop_words`, value=JSON list). Приоритет: БД > .env > default.

**Flow:**
```
/dev → "Стоп-слова" → показать текущий список
  → "Добавить" → FSM: ввод слова → сохранить в DB
  → "Удалить" → InlineKeyboard с текущими словами → callback удаления
```

**Формат хранения:**
```python
# settings table
key = "stop_words"
value = '["WordPress", "Битрикс", "Опрос", "1С", "Joomla"]'
```

### 3.4 Управление промптами

**Текущее состояние:** 3 промпта захардкожены:
- `src/ai/prompts/analyze.py` — ANALYZE_PROMPT (~111 строк)
- `src/ai/prompts/response.py` — RESPONSE_PROMPT (~19 строк)
- `src/ai/prompts/roadmap.py` — ROADMAP_PROMPT (~28 строк)

**Новое:** Хранить промпты в таблице `settings` с fallback на файловые промпты.

```python
# Логика загрузки промпта
async def get_prompt(session, prompt_key: str) -> str:
    """Загрузить промпт: DB → файл (fallback)"""
    result = await session.execute(
        select(Setting).where(Setting.key == f"prompt_{prompt_key}")
    )
    setting = result.scalar_one_or_none()
    if setting:
        return setting.value
    # Fallback на файловые промпты
    return DEFAULT_PROMPTS[prompt_key]
```

**Flow:**
```
/dev → "Промпты" → список промптов:
  1. Анализ заявки (analyze) — 111 строк
  2. Генерация отклика (response) — 19 строк
  3. Pre Roadmap (roadmap) — 28 строк
  → Выбрать промпт → показать текст (truncated)
  → "Редактировать" → FSM: ввод нового текста
  → "Сбросить к дефолту" → удалить из DB
```

**Ограничение Telegram:** Сообщения до 4096 символов. Для длинных промптов — отправка файлом (.txt) + загрузка через файл.

### 3.5 Управление командой

**Flow:**
```
/dev → "Команда" → список участников:
  1. Денис (@web_dusha) — developer ✅
  2. Imei Rhen (@imei_rhen) — developer ✅
  3. Кирилл (@kirill_pm) — manager ✅

  → "Добавить" → FSM:
    1. Ввод Telegram ID или @username
    2. Выбор роли: developer / manager
    3. Ввод имени
    → Создание TeamMember в БД

  → Выбрать участника:
    → "Деактивировать" → is_active = False
    → "Изменить роль" → developer ↔ manager
    → "Изменить стек" → FSM стека (для developers)
```

### 3.6 Настройки AI

**Flow:**
```
/dev → "Настройки" →
  1. AI-модель: google/gemini-3.1-flash-lite-preview
     → "Изменить" → FSM: ввод model ID → сохранить в DB settings

  2. Интервал парсинга: 300 сек
     → "Изменить" → FSM: ввод числа → сохранить в DB settings

  3. Порог времени: 24 ч
     → "Изменить" → FSM: ввод числа → сохранить в DB settings

  4. Минимальный бюджет: 10 000 руб.
     → "Изменить" → FSM: ввод числа → сохранить в DB settings
```

---

## 4. Панель менеджера

### 4.1 Функционал

#### Главное меню менеджера (команда /manager или /panel)
```
📋 Панель менеджера

├── 📨 Входящие отклики
│   ├── Новые (ожидают отправки)
│   ├── Отправленные
│   └── Все отклики
│
├── 📝 Стиль откликов
│   ├── Просмотр текущего стиля
│   ├── Редактировать стиль
│   ├── Шаблоны фраз
│   └── Тон общения (формальный/неформальный/дружелюбный)
│
├── 👤 Профиль менеджера
│   ├── Имя для откликов
│   ├── Подпись
│   ├── Контактные данные
│   └── Рабочие часы
│
├── 📋 Заявки
│   ├── Все заявки (с фильтрами)
│   ├── По разработчикам
│   ├── По статусу
│   └── Поиск по ID
│
├── 👥 Разработчики
│   ├── Список разработчиков + стек
│   ├── Текущая нагрузка
│   ├── Назначить разработчика на заявку
│   └── История по разработчику
│
└── 📊 Аналитика
    ├── Заявок за период (день/неделя/месяц)
    ├── Средний чек
    ├── Конверсия откликов
    ├── Топ-стеки по заявкам
    └── Время от заявки до отклика
```

### 4.2 Стиль откликов

**Цель:** Менеджер (Кирилл) задаёт стиль, в котором AI генерирует отклики. Это влияет на `RESPONSE_PROMPT`.

**Модель данных — новая таблица или settings:**
```python
# settings table entries:
"manager_style_tone"     → "professional"  # professional / friendly / casual
"manager_style_intro"    → "Мы — команда из двух fullstack-разработчиков..."
"manager_style_signature" → "С уважением, команда DevGrab"
"manager_style_rules"    → "Не использовать слова: дешево, быстро. Всегда предлагать созвон."
"manager_style_examples" → '[{"good": "...", "bad": "..."}]'  # Примеры хороших/плохих откликов
```

**Интеграция со стилем в промпте:**
```python
def build_response_prompt_v2(context: OrderContext, style: ManagerStyle) -> str:
    """Строит промпт с учётом стиля менеджера"""
    prompt = f"""
Выжимка заявки: {context.summary}
Оригинал заявки: {context.raw_text}

Данные клиента:
- Бюджет клиента: {context.client_budget}
- Сроки клиента: {context.client_deadline}
- Пожелания: {context.client_requirements}

Наши параметры:
- Стек: {', '.join(context.stack_final)}
- Бюджет: {context.price_final} руб.
- Сроки: {context.timeline_final}

{f'Заметки разработчика: {context.custom_notes}' if context.custom_notes else ''}
{f'Pre Roadmap (для контекста): {context.roadmap_text}' if context.roadmap_text else ''}

Стиль отклика:
- Тон: {style.tone}
- Вступление: {style.intro}
- Правила: {style.rules}

Сформируй отклик для отправки заказчику.
"""
    return prompt
```

**Flow:**
```
/manager → "Стиль откликов" →
  1. Тон: Professional
     → Выбор из 3: Professional / Friendly / Casual

  2. Вступление: "Мы — команда из двух fullstack-разработчиков..."
     → "Редактировать" → FSM ввод текста

  3. Правила стиля: "Не использовать: дешево, быстро..."
     → "Редактировать" → FSM ввод текста

  4. Примеры:
     → "Добавить хороший пример" → FSM ввод
     → "Добавить плохой пример" → FSM ввод
```

### 4.3 Workflow менеджера с заявкой

**Текущий flow (v0.1.0):**
```
Разработчик утверждает → менеджер получает сообщение → копирует текст → вставляет на Профи.ру
```

**Новый flow (v2.0):**
```
1. Разработчик утверждает отклик
2. Менеджер получает уведомление с кнопками:
   [✅ Отправить как есть]
   [✏️ Отредактировать]
   [🔄 Перегенерировать]
   [👤 Сменить разработчика]
   [❌ Отклонить]

3. При "Отправить как есть":
   → Текст копируется в буфер (кнопка "Скопировать")
   → Статус: assignment.status = sent
   → Логирование времени отправки

4. При "Отредактировать":
   → FSM: менеджер вводит свой текст
   → Обновляет manager_response.response_text
   → Подтверждает → статус sent

5. При "Перегенерировать":
   → AI генерирует новый отклик с текущим контекстом
   → Менеджер видит обновлённый вариант
   → Может повторить или утвердить

6. При "Сменить разработчика":
   → Показать список активных разработчиков
   → Выбрать нового → создать новый OrderAssignment
   → Уведомить нового разработчика

7. При "Отклонить":
   → FSM: причина отклонения
   → Уведомить разработчика о причине
   → Статус: assignment.status = rejected (новый статус!)
```

### 4.4 Выбор разработчика менеджером

**Сценарий:** Менеджер видит заявку в группе и хочет назначить конкретного разработчика.

**Добавить кнопку в group notification:**
```
[Взять] [Пропустить]
[📌 Назначить разработчика] ← НОВАЯ кнопка (видна только менеджерам)
[Ссылка] [Оригинал]
```

**Flow:**
```
Менеджер нажимает "Назначить разработчика"
  → Inline: список активных разработчиков с их стеком
  → Менеджер выбирает разработчика
  → OrderAssignment создаётся
  → Разработчик получает уведомление в DM
  → В группе: "Назначена: @developer_name (менеджером)"
```

### 4.5 Аналитика менеджера

```python
# Новые query функции
async def get_stats(session, period_days=30):
    return {
        "total_orders": count(orders where created_at > period),
        "taken_orders": count(assignments),
        "sent_responses": count(manager_responses),
        "avg_price": avg(assignment.price_final),
        "avg_response_time": avg(manager_response.sent_at - order.created_at),
        "by_developer": {dev.name: count(assignments)},
        "by_stack": {tech: count(analyses where tech in stack)},
        "conversion": taken_orders / total_orders * 100,
        "by_complexity": {"low": N, "medium": N, "high": N},
    }
```

---

## 5. Изменения в моделях данных

### 5.1 Новые поля в существующих таблицах

```python
# TeamMember — добавить:
tech_stack: Mapped[list] = mapped_column(JSON, default=[])
# Формат: ["Python", "FastAPI", "React", "Next.js"]

stack_priority: Mapped[dict] = mapped_column(JSON, default={})
# Формат: {"primary": ["Python", "FastAPI"], "secondary": ["React", "Next.js"]}

bio: Mapped[str] = mapped_column(Text, default="")
# Описание разработчика для AI-контекста
```

```python
# OrderAssignment — добавить:
roadmap_text: Mapped[str | None] = mapped_column(Text, nullable=True)
# Сохранять сгенерированный roadmap для передачи в контекст отклика

assigned_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True)
# Кто назначил: NULL = разработчик сам взял, ID = менеджер назначил

rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
# Причина отклонения менеджером
```

```python
# ManagerResponse — добавить:
edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
# Если менеджер отредактировал текст перед отправкой

sent_to_client: Mapped[bool] = mapped_column(Boolean, default=False)
# Подтверждение, что отправлено клиенту

sent_to_client_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

### 5.2 Новый enum

```python
class AssignmentStatus(str, Enum):
    pending = "pending"
    editing = "editing"
    approved = "approved"
    sent = "sent"
    rejected = "rejected"      # ← НОВЫЙ: менеджер отклонил
    reassigned = "reassigned"  # ← НОВЫЙ: передано другому разработчику
```

### 5.3 Таблица settings — стандартные ключи

| key | value (пример) | Описание |
|-----|-----------------|----------|
| `stop_words` | `["WordPress","Битрикс"]` | Стоп-слова для фильтрации |
| `prompt_analyze` | `<текст промпта>` | Кастомный промпт анализа |
| `prompt_response` | `<текст промпта>` | Кастомный промпт отклика |
| `prompt_roadmap` | `<текст промпта>` | Кастомный промпт roadmap |
| `ai_model` | `google/gemini-3.1-flash-lite-preview` | AI-модель |
| `parse_interval` | `300` | Интервал парсинга (сек) |
| `time_threshold` | `24` | Порог свежести заявок (часы) |
| `min_budget` | `10000` | Минимальный бюджет |
| `manager_style_tone` | `professional` | Тон откликов |
| `manager_style_intro` | `Мы — команда...` | Вступление |
| `manager_style_rules` | `Не использовать...` | Правила стиля |
| `manager_style_signature` | `С уважением, ...` | Подпись |

---

## 6. Изменения в AI-промптах

### 6.1 Новый build_roadmap_prompt (с полным контекстом)

```python
def build_roadmap_prompt_v2(context: OrderContext) -> str:
    parts = [
        f"Проект: {context.title}",
        f"\nAI-выжимка: {context.summary}",
        f"\nСтек: {', '.join(context.stack_final or context.stack) or 'не определён'}",
        f"\nБюджет разработчика: {context.price_final} руб." if context.price_final else "",
        f"AI-оценка: от {context.price_min} до {context.price_max} руб." if context.price_min else "",
        f"Бюджет клиента: {context.client_budget}" if context.client_budget else "",
        f"\nСроки разработчика: {context.timeline_final}" if context.timeline_final else "",
        f"Сроки клиента: {context.client_deadline}" if context.client_deadline else "",
        f"\nПожелания клиента: {context.client_requirements}" if context.client_requirements else "",
        f"\nОригинал заявки:\n{context.raw_text}",
    ]
    return "\n".join(p for p in parts if p)
```

### 6.2 Новый build_response_prompt (с полным контекстом + стиль менеджера)

```python
def build_response_prompt_v2(context: OrderContext, style: dict | None = None) -> str:
    parts = [
        f"Выжимка заявки: {context.summary}",
        f"Стек: {', '.join(context.stack_final or context.stack)}",
        f"Бюджет: {context.price_final} руб.",
        f"Сроки: {context.timeline_final}",
    ]

    # Контекст клиента
    if context.client_requirements:
        parts.append(f"\nПожелания клиента: {context.client_requirements}")
    if context.client_budget:
        parts.append(f"Бюджет клиента: {context.client_budget}")
    if context.client_deadline:
        parts.append(f"Сроки клиента: {context.client_deadline}")

    # Контекст разработчика
    if context.custom_notes:
        parts.append(f"\nЗаметки разработчика: {context.custom_notes}")
    if context.roadmap_text:
        parts.append(f"\nPre Roadmap (для контекста):\n{context.roadmap_text}")

    # Стиль менеджера
    if style:
        parts.append(f"\nСтиль отклика:")
        if style.get("tone"):
            parts.append(f"- Тон: {style['tone']}")
        if style.get("intro"):
            parts.append(f"- Вступление команды: {style['intro']}")
        if style.get("rules"):
            parts.append(f"- Правила: {style['rules']}")

    # Оригинал для максимального контекста
    parts.append(f"\nОригинал заявки:\n{context.raw_text}")

    parts.append("\nСформируй отклик для отправки заказчику.")
    return "\n".join(parts)
```

### 6.3 Обновить ANALYZE_PROMPT — системный промпт

Добавить в конец JSON-схемы:
```json
{
  "response_draft": "черновик отклика (3-5 предложений, ОБЯЗАТЕЛЬНО с конкретной ценой из price_min-price_max и сроками из timeline_days)"
}
```

---

## 7. Чекпоинты реализации

### Фаза 1: Исправление AI-контекста (КРИТИЧНО)
**Оценка: 2-3 дня**

- [ ] **CP-1.1** Создать класс `OrderContext` в `src/ai/context.py`
- [ ] **CP-1.2** Обновить `build_roadmap_prompt()` — передавать цены, сроки, заметки разработчика
- [ ] **CP-1.3** Обновить `build_response_prompt()` — передавать полный контекст + стиль менеджера
- [ ] **CP-1.4** Сохранять roadmap_text в OrderAssignment (новое поле)
- [ ] **CP-1.5** При взятии заявки сохранять price_min И price_max (не только max)
- [ ] **CP-1.6** Передавать response_draft в контекст для reference
- [ ] **CP-1.7** Тесты: проверить что промпты содержат все необходимые поля
- [ ] **CP-1.8** Миграция Alembic: добавить поля roadmap_text, assigned_by, rejection_reason в order_assignments

### Фаза 2: Расширение моделей данных
**Оценка: 1-2 дня**

- [ ] **CP-2.1** Добавить поля в TeamMember: tech_stack, stack_priority, bio
- [ ] **CP-2.2** Добавить поля в ManagerResponse: edited_text, sent_to_client, sent_to_client_at
- [ ] **CP-2.3** Добавить enum values: rejected, reassigned в AssignmentStatus
- [ ] **CP-2.4** Миграция Alembic: все новые поля
- [ ] **CP-2.5** Обновить тесты моделей
- [ ] **CP-2.6** Обновить docs/services/core.md

### Фаза 3: Сервис настроек (Settings Service)
**Оценка: 1 день**

- [ ] **CP-3.1** Создать `src/core/settings_service.py` — CRUD для Settings таблицы
- [ ] **CP-3.2** Функции: get_setting, set_setting, get_stop_words, set_stop_words, get_prompt, set_prompt
- [ ] **CP-3.3** Загрузка стоп-слов: DB → .env fallback → default
- [ ] **CP-3.4** Загрузка промптов: DB → файловый fallback
- [ ] **CP-3.5** Загрузка настроек: DB → config fallback
- [ ] **CP-3.6** Интегрировать с parser/filters.py (стоп-слова из DB)
- [ ] **CP-3.7** Интегрировать с ai/prompts (промпты из DB)
- [ ] **CP-3.8** Тесты settings_service

### Фаза 4: Матчинг разработчиков по стеку
**Оценка: 1 день**

- [ ] **CP-4.1** Создать `src/bot/services/matching.py` — алгоритм матчинга
- [ ] **CP-4.2** Функция match_developers(order_stack, developers) → [(dev, score)]
- [ ] **CP-4.3** Обновить notification.py — добавить секцию "Подходящие разработчики" с @mention
- [ ] **CP-4.4** Тесты матчинга (пустой стек, partial match, full match)
- [ ] **CP-4.5** Обновить docs/services/bot.md

### Фаза 5: Панель разработчика (Bot Handlers)
**Оценка: 3-4 дня**

- [ ] **CP-5.1** Создать `src/bot/handlers/dev_panel.py` — роутер панели разработчика
- [ ] **CP-5.2** Создать `src/bot/keyboards/dev_panel.py` — клавиатуры панели
- [ ] **CP-5.3** Создать `src/bot/states.py` — расширить FSM states (DevPanelStates)
- [ ] **CP-5.4** Реализовать: "Мой стек" — просмотр и редактирование
- [ ] **CP-5.5** Реализовать: "Стоп-слова" — просмотр, добавление, удаление
- [ ] **CP-5.6** Реализовать: "Промпты" — просмотр и редактирование (через FSM + файлы)
- [ ] **CP-5.7** Реализовать: "Команда" — список, добавление, деактивация
- [ ] **CP-5.8** Реализовать: "Настройки" — AI-модель, интервалы, пороги
- [ ] **CP-5.9** Реализовать: "Мои заявки" — активные, завершённые, пропущенные
- [ ] **CP-5.10** Реализовать: "Статистика" — базовая аналитика
- [ ] **CP-5.11** Подключить роутер в bot.py (dispatcher)
- [ ] **CP-5.12** Тесты хендлеров панели
- [ ] **CP-5.13** Обновить docs/services/bot.md

### Фаза 6: Панель менеджера (Bot Handlers)
**Оценка: 3-4 дня**

- [ ] **CP-6.1** Создать `src/bot/handlers/manager_panel.py` — роутер панели менеджера
- [ ] **CP-6.2** Создать `src/bot/keyboards/manager_panel.py` — клавиатуры
- [ ] **CP-6.3** Расширить FSM states (ManagerPanelStates)
- [ ] **CP-6.4** Реализовать: "Входящие отклики" — новые, отправленные
- [ ] **CP-6.5** Реализовать: "Стиль откликов" — тон, вступление, правила
- [ ] **CP-6.6** Реализовать: "Профиль менеджера" — имя, подпись, контакты
- [ ] **CP-6.7** Реализовать: "Заявки" — фильтрация, поиск, по статусу
- [ ] **CP-6.8** Реализовать: "Разработчики" — список + стек + нагрузка
- [ ] **CP-6.9** Реализовать: "Назначить разработчика" — кнопка в группе + flow
- [ ] **CP-6.10** Реализовать: расширенные кнопки отклика (редактировать, перегенерировать, отклонить)
- [ ] **CP-6.11** Реализовать: "Аналитика" — базовые метрики
- [ ] **CP-6.12** Подключить роутер в bot.py
- [ ] **CP-6.13** Тесты хендлеров панели менеджера
- [ ] **CP-6.14** Обновить docs/services/bot.md

### Фаза 7: Интеграция и тестирование
**Оценка: 2 дня**

- [ ] **CP-7.1** E2E тест: полный flow от заявки до отправки менеджеру
- [ ] **CP-7.2** Проверить AI-контекст на реальных заявках (цены, сроки, стек)
- [ ] **CP-7.3** Проверить матчинг разработчиков на реальных стеках
- [ ] **CP-7.4** Проверить работу панелей в Telegram
- [ ] **CP-7.5** Обновить все docs/services/*.md
- [ ] **CP-7.6** Обновить CLAUDE.md (новые хендлеры, states, модели)
- [ ] **CP-7.7** Обновить CHANGELOG.md
- [ ] **CP-7.8** Создать миграцию → `alembic upgrade head` на проде
- [ ] **CP-7.9** Перезапуск сервисов → проверка логов

---

## 8. Тестирование

### 8.1 Новые тесты (ожидаемые)

| Файл | Кол-во | Покрытие |
|------|--------|----------|
| `test_order_context.py` | ~8 | OrderContext, сборка контекста, edge cases |
| `test_prompts_v2.py` | ~10 | Все промпты содержат необходимые поля |
| `test_matching.py` | ~8 | Матчинг стека, пустой стек, scoring |
| `test_settings_service.py` | ~10 | CRUD settings, fallback, стоп-слова, промпты |
| `test_dev_panel.py` | ~15 | Все хендлеры панели разработчика |
| `test_manager_panel.py` | ~12 | Все хендлеры панели менеджера |
| **Итого** | ~63 | + к существующим 51 = ~114 тестов |

### 8.2 Критерии приёмки

- [ ] Все 114+ тестов проходят
- [ ] AI-отклик содержит цену, совпадающую с price_final разработчика
- [ ] Roadmap использует цены из AI-анализа/разработчика
- [ ] В уведомлении группы отображаются подходящие разработчики
- [ ] Менеджер может отредактировать/отклонить отклик
- [ ] Стоп-слова из DB применяются при фильтрации
- [ ] Промпты из DB загружаются вместо файловых

---

## Приоритеты реализации

```
ВЫСШИЙ:  Фаза 1 (AI-контекст) → Фаза 2 (модели) → Фаза 3 (settings)
ВЫСОКИЙ: Фаза 4 (матчинг) → Фаза 5 (панель разработчика)
СРЕДНИЙ: Фаза 6 (панель менеджера) → Фаза 7 (интеграция)
```

**Общая оценка: 13-17 дней** на всю модернизацию при работе двух разработчиков.
