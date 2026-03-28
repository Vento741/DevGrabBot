# Bot — Telegram бот (aiogram 3)

> **Последнее обновление:** 2026-03-25 (manager_panel: toggle доступности парсера; manager_main_menu_kb is_paused param; RedisClient в workflow_data dispatcher)
> **ВАЖНО:** При любых изменениях в модуле `src/bot/` — обновить этот документ!

## Назначение

Telegram бот для взаимодействия команды с заявками. Обрабатывает уведомления, назначения, редактирование откликов.

## Файлы

| Файл | Назначение |
|------|-----------|
| `src/bot/main.py` | Entry point: запуск бота + 2 воркера |
| `src/bot/bot.py` | Инициализация Bot + Dispatcher + роутеры |
| `src/bot/handlers/start.py` | Команда /start |
| `src/bot/handlers/orders.py` | Взятие/пропуск заявок |
| `src/bot/handlers/review.py` | Редактирование и утверждение отклика |
| `src/bot/handlers/manager.py` | Отправка менеджеру |
| `src/bot/handlers/dev_panel.py` | Панель разработчика (CP-5.1, CP-5.4–CP-5.11) |
| `src/bot/handlers/manager_panel.py` | Панель менеджера (CP-6.1, CP-6.4–CP-6.12) |
| `src/bot/keyboards/orders.py` | Клавиатура для заявок |
| `src/bot/keyboards/review.py` | Клавиатура для рецензирования |
| `src/bot/keyboards/dev_panel.py` | Клавиатуры панели разработчика (CP-5.2) |
| `src/bot/keyboards/manager_panel.py` | Клавиатуры панели менеджера (CP-6.2) |
| `src/bot/middlewares/auth.py` | Проверка доступа через team_members + блокировка команд в группах (только ЛС) |
| `src/bot/workers/notification.py` | Воркер уведомлений (Redis → TG) |
| `src/bot/services/notification.py` | Логика форматирования + воркер уведомлений |
| `src/bot/services/matching.py` | Матчинг разработчиков по стеку (CP-4.1) |
| `src/bot/services/analytics.py` | Сервис аналитики (система, разработчики, менеджер) |
| `src/bot/services/scheduler.py` | Планировщик ежедневной рассылки статистики |

---

## Архитектура запуска (main.py)

```python
asyncio.gather(
    run_bot(settings),              # Polling бот
    run_ai_worker(settings),        # AI анализ заявок
    run_notification_worker(settings, bot)  # Уведомления в группу
    run_scheduler_worker(settings),  # Ежедневная рассылка статистики
)
```

Используется `return_when=FIRST_EXCEPTION` для graceful shutdown.

---

## Handlers

### /start, /panel (start.py)
- `/start` — приветственное сообщение с описанием workflow
- `/panel` — быстрый выбор панели (dev / manager)
- Inline-кнопки навигации: «Панель разработчика» (`open:dev`), «Панель менеджера» (`open:manager`)
- Callback-обработчики `open:dev` / `open:manager` — открывают соответствующие панели из стартового сообщения
- Логирует ФИ и Telegram ID

### Заявки (orders.py)

| Callback | Действие |
|---------|---------|
| `take:{order_id}` | Взять заявку: проверка в team_members → создание OrderAssignment(status=editing) → отправка анализа в личку → DM уведомления другим разработчикам (если notify_assignments=True) |
| `skip:{order_id}` | Пропустить: order.status = skipped |
| `original:{order_id}` | Показать raw_text заявки |
| `materials:{order_id}` | Показать прикреплённые материалы заявки: изображения через `send_media_group()`, документы через `send_document()` в личку пользователя |

**CP-1.5: handle_take_order** — в личке разработчику отображается ценовой диапазон + уведомления:
- `<b>AI-оценка цены:</b> от X до Y руб.` — оба значения price_min и price_max из AI-анализа
- `<b>Цена (можно изменить):</b> Y руб.` — стартовое значение для редактирования (price_max)
- После создания OrderAssignment отправляются DM-уведомления всем остальным активным разработчикам с флагом `notify_assignments=True`:
  - Текст: "⚠️ @dev_name взял заявку #DD.MM.YY_N"
  - Помогает знать о назначениях для координации или поддержки

### Рецензирование (review.py)

**Inline-кнопки редактирования:**
| Callback | FSM State | Описание |
|---------|----------|---------|
| `edit_price:{id}` | ReviewStates.editing_price | Изменить цену (парсинг числа) |
| `edit_timeline:{id}` | ReviewStates.editing_timeline | Изменить сроки (строка) |
| `edit_stack:{id}` | ReviewStates.editing_stack | Изменить стек (по запятой) |
| `edit_custom:{id}` | ReviewStates.editing_custom | Заметки (произвольный текст) |

**Утверждение:**
| Callback | Действие |
|---------|---------|
| `approve:{assignment_id}` | Собрать OrderContext → AI build_response_prompt_v2() → сохранить ManagerResponse → отправить менеджеру |
| `roadmap:{order_id}` | Собрать OrderContext → AI build_roadmap_prompt_v2() → сохранить в assignment.roadmap_text → отправить в чат |
| `reject_order:{assignment_id}` | Удалить assignment из БД → Order.status=reviewed → обновить кнопки в группе (вернуть «Взять/Пропустить») |

**CP-1.4: handle_roadmap** — обновлён для сохранения результата:
- Загружает `Order.assignments` через selectinload
- Ищет активный assignment (status=editing или pending)
- Использует `OrderContext.from_order_data()` и `build_roadmap_prompt_v2()` с полным контекстом (бюджет, сроки клиента, пожелания)
- Сохраняет `roadmap_text` в `assignment.roadmap_text` после генерации

**CP-1.5: handle_approve** — обновлён для использования полного контекста:
- Вместо ручной сборки параметров использует `OrderContext.from_order_data(order, analysis, assignment)`
- Вместо `build_response_prompt()` вызывает `build_response_prompt_v2(context)`
- Контекст включает: response_draft из AI-анализа, roadmap_text, пожелания клиента, вопросы

### Менеджер (manager.py)

- `send_to_manager()` — находит менеджера (role=manager, is_active) или fallback на group_chat_id; использует `pm_response_kb` вместо `copy_response_kb`
- `handle_pm_sent` — `resp:sent:{response_id}` — переводит ManagerResponse в статус sent_to_client=True + sent_to_client_at=now
- `handle_pm_in_progress` — `resp:progress:{assignment_id}` — переводит OrderAssignment в статус in_progress + in_progress_at=now
- `handle_pm_cancel` — `resp:cancel:{assignment_id}` — переводит OrderAssignment в статус cancelled + cancelled_at=now
- `_notify_developer()` — DM оповещение разработчику о действии менеджера
- `_load_assignment_for_pm()` — загрузка assignment для PM-операций с selectinload
- `copy_response:{response_id}` — отправить текст в `<code>` для копирования

### Панель разработчика (dev_panel.py) — CP-5.1, CP-5.4–CP-5.11

Команда `/dev` открывает главное меню. Все секции работают через inline-кнопки.

| Callback / команда | FSM State | Действие |
|-------------------|-----------|---------|
| `/dev` | — | Главное меню панели |
| `dev:back` | — (clear) | Вернуться в главное меню |
| `dev:stack` | — | Показать стек разработчика |
| `stack:edit_primary` | editing_primary_stack | Ввод primary стека через запятую |
| `stack:edit_secondary` | editing_secondary_stack | Ввод secondary стека через запятую |
| `stack:clear` | — | Очистить stack_priority и tech_stack |
| `dev:stopwords` | — | Список стоп-слов |
| `sw:del:{word}` | — | Удалить стоп-слово |
| `sw:add` | adding_stop_word | Добавить стоп-слово |
| `dev:prompts` | — | Список промптов |
| `prompt:{key}` | — | Просмотр промпта (custom/default) |
| `prompt:edit:{key}` | editing_prompt | Редактировать промпт |
| `prompt:reset:{key}` | — | Сбросить промпт к файловому дефолту |
| `dev:team` | — | Список участников команды |
| `team:member:{id}` | — | Детали участника |
| `team:toggle:{id}` | — | Переключить is_active |
| `team:stack:{id}` | editing_primary_stack | Редактировать стек другого участника |
| `team:add` | adding_member_tg_id | Добавить участника (3 шага FSM) |
| `role:{value}` | → adding_member_name | Выбор роли при добавлении |
| `dev:settings` | — | Текущие настройки |
| `set:{key}` | editing_setting_value | Изменить настройку |
| `dev:orders` | — | Фильтр заявок разработчика |
| `orders:sent` | — | sent назначения |
| `orders:cancelled` | — | cancelled назначения |
| `orders:all` | — | Все назначения с clickable order buttons |
| `dev:order:{assignment_id}` | — | Полная карточка заявки (detail view) + индикатор `📎 Материалы: N шт.` если есть |
| `dev:toggle_notify` | — | Переключение notify_assignments (уведомления о заявках) |
| `dev:stats` | — | Статистика через analytics.py (системная + личная за 7 дн. и всё время) |

**Особенности реализации:**
- `team:stack:{id}` → FSM `editing_primary_stack`, в state.data сохраняется `target_member_id`. Хендлер `process_primary_stack` проверяет наличие `target_member_id` и сохраняет в нужного участника.
- Промпты: truncate до 3900 символов + "..." перед отображением.
- Статистика: использует `analytics.get_developer_stats(session, dev_id, days)` и `get_system_stats(session)` для отображения за 7 дн. и всё время.
- `dev:toggle_notify`: переключает флаг `notify_assignments` разработчика (уведомления о новых назначениях от других девов).

### Панель менеджера (manager_panel.py) — CP-6.1, CP-6.4–CP-6.12

Команда `/manager` открывает главное меню. Все секции работают через inline-кнопки.

| Callback / команда | FSM State | Действие |
|-------------------|-----------|---------|
| `/manager` | — (clear) | Главное меню панели менеджера |
| `mgr:back` | — (clear) | Вернуться в главное меню |
| `mgr:responses` | — | Входящие отклики: фильтр |
| `resp:new` | — | Отклики где sent_to_client=False |
| `resp:sent` | — | Отклики где sent_to_client=True |
| `resp:all` | — | Все отклики (до 10 штук) |
| `resp:detail:{id}` | — | Полный текст отклика + кнопки действий |
| `resp:edit:{id}` | editing_response_text | Редактировать текст → сохранить в edited_text |
| `resp:sent:{response_id}` | — | Отправить отклик клиенту: sent_to_client=True, sent_to_client_at=now |
| `resp:progress:{assignment_id}` | — | Перевести в работу: status=in_progress, in_progress_at=now + DM разработчику |
| `resp:cancel:{assignment_id}` | — | Отменить заявку: status=cancelled, cancelled_at=now + DM разработчику |
| `resp:regen:{assignment_id}` | — | Перегенерация через AI с учётом стиля менеджера |
| `mgr:style` | — | Текущий стиль + style_settings_kb |
| `style:tone` | editing_style_tone | Ввод тона → set_setting("manager_style_tone", ...) |
| `style:intro` | editing_style_intro | Ввод вступления → set_setting("manager_style_intro", ...) |
| `style:rules` | editing_style_rules | Ввод правил → set_setting("manager_style_rules", ...) |
| `mgr:profile` | — | Текущий профиль + profile_settings_kb |
| `profile:name` | editing_profile_name | Ввод имени → set_setting("manager_profile_name", ...) |
| `profile:signature` | editing_profile_signature | Ввод подписи → set_setting("manager_profile_signature", ...) |
| `profile:contacts` | editing_profile_contacts | Ввод контактов → set_setting("manager_profile_contacts", ...) |
| `mgr:orders` | — | Раздел заявок: orders_status_kb |
| `morders:new` | — | Заявки со статусами new/analyzing/reviewed |
| `morders:assigned` | — | Заявки со статусом assigned |
| `morders:completed` | — | Заявки со статусом completed |
| `morders:all` | — | Все заявки (до 15) |
| `morders:search` | searching_order | Поиск по external_id (ilike) |
| `mgr:devs` | — | Список разработчиков (role=developer) |
| `mdev:{id}` | — | Детали разработчика: стек, активных, завершённых |
| `mdev:history:{id}` | — | История заявок разработчика (до 10) |
| `mdev:assign:{id}` | — | Назначить разработчика: список свободных заявок → assign:{dev_id}:{order_id} |
| `assign:{dev_id}:{order_id}` | — | Создание OrderAssignment с assigned_by=менеджер |
| `copy_response:{id}` | — | Отправляет текст отклика отдельным сообщением для копирования |
| `mgr:analytics` | — | Аналитика через analytics.py: разбивка по девам, расходы за 7 дн. и месяц |
| `mgr:toggle_available` | — | Переключение паузы парсера: `redis_client.is_parser_paused()` → `set_parser_paused()` / `set_parser_resumed()` → смешное сообщение в групповой чат |

**Особенности реализации:**
- Стиль хранится в `settings` таблице: `manager_style_tone`, `manager_style_intro`, `manager_style_rules`.
- Профиль: `manager_profile_name`, `manager_profile_signature`, `manager_profile_contacts`.
- `resp:regen` — загружает стиль менеджера и инжектирует в system prompt перед вызовом AI.
- `resp:sent` — отправляет в групповой чат сообщение вида: "✅ Отклик отправлен: #DD.MM.YY_N | Цена: X руб."
- `resp:progress` — переводит assignment в in_progress, оповещает разработчика DM с возможностью откатить
- `resp:cancel` — переводит assignment в cancelled, оповещает разработчика с причиной отмены
- `mgr:toggle_available` — получает `redis_client` из `data["redis_client"]` (dispatcher workflow_data); при паузе отправляет смешное сообщение-заглушку в группу; при возобновлении — сообщение о перезапуске парсера.
- Аналитика: использует `analytics.get_manager_stats(session, days)` для разбивки по девам и расходов за 7/30 дней.
- Список откликов / заявок обрезается до 4096 символов (лимит TG).
- Callback `mdev:{id}` использует отрицательный фильтр (`~F.data.startswith("mdev:history:")`) чтобы не перехватывать вложенные паттерны.
- `_ASSIGNMENT_STATUS_LABEL` содержит иконки для всех статусов: pending, editing, approved, sent, rejected, reassigned, in_progress, cancelled.

---

## Analytics Service (src/bot/services/analytics.py)

Сервис для сбора и форматирования статистики по заявкам, разработчикам и менеджеру.

### get_system_stats(session)

Возвращает общесистемную статистику:
- Всего заявок, проанализировано, назначено, завершено
- Среднее время до отклика, средняя цена
- Топ-3 стека по популярности

**Возвращает:**
```python
{
    "total_orders": int,
    "analyzed": int,
    "assigned": int,
    "completed": int,
    "avg_response_time": str,  # "14 дн."
    "avg_price": int,
    "top_stacks": [(stack_name, count), ...]
}
```

### get_developer_stats(session, dev_id, days)

Возвращает статистику разработчика за период:
- Активных заявок, завершённых, отклонено
- Средняя цена отклика, среднее время выполнения
- Рейтинг матчинга (средний % совпадения стека)

**Возвращает:**
```python
{
    "active": int,
    "completed": int,
    "rejected": int,
    "avg_price": int,
    "avg_completion_days": float,
    "match_rating": float,  # 0-100%
}
```

### get_all_developers_stats(session, days)

Возвращает статистику всех активных разработчиков (для аналитики менеджера):
- Таблица: разработчик → активных, завершённых, средняя цена

**Возвращает:**
```python
[
    {
        "name": str,
        "active": int,
        "completed": int,
        "avg_price": int,
        "total_revenue": int,
    },
    ...
]
```

### get_manager_stats(session, days)

Возвращает статистику менеджера по откликам:
- Всего откликов, отправлено клиентам, конверсия (%)
- Топ-3 стека по отправленным откликам
- Дневная/недельная/месячная разбивка

**Возвращает:**
```python
{
    "total_responses": int,
    "sent": int,
    "conversion": float,  # %
    "top_stacks": [(stack, count), ...],
    "daily_breakdown": {date: count, ...},
}
```

### get_daily_broadcast_text(session)

Возвращает HTML-текст для ежедневной рассылки статистики в групповой чат.

**Формат вывода:**
```html
<b>📊 Статистика за день</b>

<b>Заявки:</b>
  Новых: 3 | Проанализировано: 3
  Назначено: 2 | Завершено: 1

<b>Отклики:</b>
  Отправлено: 2 | Средняя цена: 55 000 руб.

<b>Топ-3 разработчика:</b>
  1. @web_dusha — 2 активных, 15 000 руб.
  2. @imei_rhen — 1 активный, 45 000 руб.
```

---

## Scheduler Service (src/bot/services/scheduler.py)

Асинхронный планировщик для ежедневной рассылки статистики в групповой чат.

### run_scheduler_worker(settings)

4-й asyncio task в main.py. Запускает бесконечный цикл:

1. Получить из `settings` текущий час рассылки (по умолчанию 9 UTC)
2. Жди до начала часа
3. Загрузить дневную статистику: `get_daily_broadcast_text(session)`
4. Если за день была активность (хотя бы 1 новая заявка или отклик) — отправить в групповой чат
5. **Auto-archive:** архивировать (status=cancelled) все заявки со статусом `approved`, созданные >5 дней назад через `_auto_archive_stale_assignments()`
6. Спать до следующего дня

**Настройка:**
- Ключ в `settings`: `"scheduler_stats_hour"` (целое число 0-23, UTC)
- Команда `/manager` → раздел "Настройки" → "Час статистики" → ввод числа

**Auto-archive логика:**
- Проверяет OrderAssignment с status=approved и approved_at < now() - 5 дней
- Переводит в status=cancelled, cancelled_at=now()
- Отправляет DM разработчику об архивации
- Запускается один раз в день в час рассылки статистики

**Примечания:**
- Если за день нет активности — сообщение не отправляется
- Рассылка срабатывает один раз в день в настраиваемый час
- При перезагрузке сервиса рассылка не дублируется

---

## Keyboards

### order_actions_kb(order_id, has_materials=False)
```
[✅ Взять] [❌ Пропустить]
[🔍 Просмотр деталей]  ← только если has_materials=True
```

### review_actions_kb(assignment_id, external_id)
```
[💰 Цена] [📅 Сроки] [🛠 Стек] [📝 Заметки]
[📋 Pre Roadmap] [📄 Оригинал]
[🔗 Профи.ру (ссылка)]
[✅ Утвердить и отправить]
```

### pm_response_kb(response_id, assignment_id, status)
```
[✅ Отклик отправлен] [⏳ В работе] [❌ Отмена]
[📋 Просмотр]
```
Progressive button hiding: скрывает кнопки в соответствии со статусом assignment.

### pm_status_badge_kb(status)
```
Terminal state badge с иконкой статуса (для sent/in_progress/cancelled).
```

### copy_response_kb(response_id)
```
[Скопировать отклик]
```

### Клавиатуры панели разработчика (CP-5.2) — `keyboards/dev_panel.py`

| Функция | Описание | Callback-prefix |
|---------|---------|----------------|
| `dev_main_menu_kb()` | Главное меню панели | `dev:` |
| `stack_actions_kb()` | Действия с личным стеком | `stack:` |
| `stop_words_kb(words)` | Стоп-слова + удаление (2 в ряд) | `sw:del:`, `sw:add` |
| `prompts_list_kb()` | Список промптов (analyze/response/roadmap) | `prompt:` |
| `prompt_actions_kb(prompt_key)` | Редактировать / сбросить промпт | `prompt:edit:`, `prompt:reset:` |
| `team_list_kb(members, show_add)` | Список участников команды | `team:member:`, `team:add` |
| `member_actions_kb(member_id, is_active)` | Действия с участником | `team:toggle:`, `team:stack:` |
| `role_select_kb()` | Выбор роли (developer/manager) | `role:` |
| `settings_kb(current_settings)` | Настройки с текущими значениями + кнопки "Час статистики" и "Уведомления о заявках" | `set:`, `dev:toggle_notify` |
| `orders_filter_kb()` | Фильтр заявок [Отправленные/Архив/Все] | `orders:sent/cancelled/all` |
| `orders_list_kb(assignments)` | Clickable order buttons с иконками статуса | `dev:order:{id}` |
| `order_detail_kb(assignment_id, has_materials=False)` | Detail view buttons (назад, редактирование); показывает `🔍 Просмотр материалов` если has_materials=True | `dev:back`, `materials:` |
| `back_to_dev_kb()` | Кнопка "Назад" в главное меню | `dev:back` |

Callback-namespace панели не пересекается с существующими (`take:`, `skip:`, `edit_price:`, `approve:`, `copy_response:`).

### Клавиатуры панели менеджера (CP-6.2) — `keyboards/manager_panel.py`

| Функция | Описание | Callback-prefix |
|---------|---------|----------------|
| `manager_main_menu_kb(is_paused: bool = False)` | Главное меню панели менеджера; кнопка toggle меняет цвет и текст в зависимости от `is_paused` (зелёная «Принимаем заявки» / красная «Пауза приёма») | `mgr:` |
| `responses_filter_kb()` | Фильтр входящих откликов | `resp:new/sent/all`, `mgr:back` |
| `response_actions_kb(response_id, assignment_id)` | Действия с конкретным откликом; кнопка "Отправлено клиенту" на полной ширине в первом ряду | `resp:edit:`, `resp:regen:`, `resp:mark_sent:`, `copy_response:`, `mgr:responses` |
| `style_settings_kb()` | Настройки стиля откликов | `style:tone/intro/rules`, `mgr:back` |
| `profile_settings_kb()` | Настройки профиля менеджера | `profile:name/signature/contacts`, `mgr:back` |
| `orders_status_kb()` | Заявки по статусу | `morders:new/assigned/completed/all/search`, `mgr:back` |
| `developers_list_kb(developers)` | Список разработчиков | `mdev:{id}`, `mgr:back` |
| `developer_detail_kb(developer_id)` | Детали разработчика | `mdev:assign:`, `mdev:history:`, `mgr:devs` |
| `back_to_manager_kb()` | Кнопка "Назад" в главное меню | `mgr:back` |

Callback-namespace панели менеджера (`mgr:`, `resp:`, `style:`, `profile:`, `morders:`, `mdev:`) не пересекается с namespace панели разработчика (`dev:`, `stack:`, `sw:`, `prompt:`, `team:`, `set:`, `orders:`).

### Клавиатуры уведомлений (keyboards/review.py)

| Функция | Описание | Callback-prefix |
|---------|---------|----------------|
| `pm_response_kb(response_id, assignment_id, status)` | PM-действия с прогрессивным скрытием кнопок | `resp:sent:`, `resp:progress:`, `resp:cancel:` |
| `pm_status_badge_kb(status)` | Terminal state badge клавиатура | — |

---

## Middleware — AuthMiddleware

- Пропускает `/start` без проверки
- Для всех остальных: проверяет `team_members` (is_active=True)
- Работает на двух очередях: `message` + `callback_query`
- Отказ: "У вас нет доступа" + WARNING лог

---

## Notification Worker

Бесконечный цикл:
1. `redis.pop_analyzed()` — получить результат AI-анализа
2. Загрузить активных разработчиков (`role=developer, is_active=True`) из БД
3. Вычислить матчинг: `match_developers(order_stack, developers)`
4. Форматировать сообщение с блоком «Подходящие разработчики»
5. `bot.send_message(group_chat_id)` с inline-клавиатурой
6. `asyncio.sleep(5)` между сообщениями

Воркер создаёт собственный `engine + session_factory` при запуске.

**Формат уведомления:**
```
Новая заявка | #external_id
НАЗВАНИЕ

Выжимка: ...
Пожелания заказчика: ...

Бюджет клиента: ...
Сроки клиента: ...

Стек: Python, aiogram, ...
Наша оценка: 45 000 – 80 000 руб.
Наши сроки: 14 дн.
Сложность: medium
Релевантность: ████████░░ 85%

📎 Материалы: 3 шт.   ← если есть materials

Подходящие разработчики:
  @web_dusha (Python, FastAPI — 90%)
  @imei_rhen (React — 45%)

[Взять] [Пропустить] [Ссылка] [Оригинал]
[🔍 Просмотр деталей]  ← если есть materials
```

---

## Matching Service (CP-4.1)

**Файл:** `src/bot/services/matching.py`

### match_developers(order_stack, developers)

```python
def match_developers(
    order_stack: list[str],
    developers: list[Any],
) -> list[tuple[Any, int, list[str]]]:
    ...
```

Алгоритм:
- Совпадение технологии в `stack_priority["primary"]` → вес 2
- Совпадение в `stack_priority["secondary"]` → вес 1
- `score = (primary_matches * 2 + secondary_matches) / (len(order_stack) * 2) * 100`
- Сравнение case-insensitive
- Возвращает только разработчиков с `score > 0`, отсортированных по убыванию score

**Возвращает:** `[(developer, score_percent, matched_techs)]`

### format_matches_block(matches)

Форматирует список матчей в HTML-строку для вставки в уведомление.
Если `tg_username` отсутствует — использует `name`.

**Пример вывода:**
```
<b>Подходящие разработчики:</b>
  @web_dusha (Python, FastAPI — 90%)
  @imei_rhen (React — 45%)
```

---

## Полный Workflow

```
ГРУППА: Уведомление о заявке → [Взять] или [Пропустить]
    ↓ (Взять)
ЛИЧКА РАЗРАБОТЧИКА: AI-анализ + кнопки редактирования
    ↓ (Редактирование цены/сроков/стека)
ЛИЧКА: [Утвердить и отправить]
    ↓
AI генерирует текст отклика → ManagerResponse в БД
    ↓
МЕНЕДЖЕРУ: Детали + текст отклика → [Скопировать]
```

---

## Systemd

```ini
# devgrabbot.service
ExecStart=/usr/bin/python3 -m src.bot.main
Restart=on-failure
RestartSec=10s
After=postgresql.service redis.service
```

## FSM States

```python
class ReviewStates(StatesGroup):
    editing_price = State()
    editing_timeline = State()
    editing_stack = State()
    editing_custom = State()


class DevPanelStates(StatesGroup):
    """CP-5.3: Состояния панели разработчика."""
    # Мой стек
    editing_primary_stack = State()
    editing_secondary_stack = State()
    # Стоп-слова
    adding_stop_word = State()
    # Промпты
    selecting_prompt = State()
    editing_prompt = State()
    # Команда
    adding_member_tg_id = State()
    adding_member_role = State()
    adding_member_name = State()
    # Настройки
    editing_setting_value = State()


class ManagerPanelStates(StatesGroup):
    """CP-6.3: Состояния панели менеджера."""
    # Стиль откликов
    editing_style_tone = State()
    editing_style_intro = State()
    editing_style_rules = State()
    # Профиль
    editing_profile_name = State()
    editing_profile_signature = State()
    editing_profile_contacts = State()
    # Редактирование отклика
    editing_response_text = State()
    # Поиск заявки
    searching_order = State()
```

## Зависимости

- `aiogram>=3.4,<4` — Telegram framework
- `sqlalchemy[asyncio]>=2.0` — ORM (через core)
- `redis[hiredis]>=5.0` — Redis (через core)

## Gotchas

- Parse mode: HTML для всех сообщений
- Selectinload для lazy-loading связей (order.analyses, developer)
- GROUP_CHAT_ID в .env — заменить плейсхолдер на реальный ID
- `RedisClient` регистрируется в `dp.workflow_data["redis_client"]` при старте бота в `bot.py`; доступен в хендлерах через `data["redis_client"]`
