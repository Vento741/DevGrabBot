"""Промпт для генерации Pre Roadmap проекта."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ai.context import OrderContext

SYSTEM_PROMPT = """\
Ты — senior fullstack-разработчик. На основе описания проекта составь \
предварительный roadmap разработки.

Формат ответа — структурированный текст (НЕ JSON), пригодный для Telegram (HTML-теги).

Структура roadmap:

1. <b>Этап 1: Название</b> (N дней)
   - Задача 1
   - Задача 2

2. <b>Этап 2: Название</b> (N дней)
   - Задача 1
   - ...

В конце укажи:
<b>Итого:</b> X дней | стек: ... | ориентир по бюджету: ... руб.

Правила:
- Разбивай на логические этапы (Discovery, MVP, Доработки, Деплой)
- Каждый этап — конкретные задачи, не абстракции
- Сроки реалистичные для команды из 2 senior fullstack
- Пиши кратко и по делу, без воды
- Максимум 5-7 этапов
"""


def build_roadmap_prompt(
    title: str,
    summary: str,
    stack: list[str],
    raw_text: str,
) -> str:
    """Сформировать user-сообщение для генерации roadmap."""
    stack_str = ", ".join(stack) if stack else "не определён"
    return (
        f"Проект: {title}\n\n"
        f"AI-выжимка: {summary}\n\n"
        f"Стек: {stack_str}\n\n"
        f"Оригинал заявки:\n{raw_text}"
    )


def build_roadmap_prompt_v2(context: OrderContext) -> str:
    """Сформировать user-сообщение для roadmap с ПОЛНЫМ контекстом.

    Args:
        context: OrderContext с полными данными заявки.
    """
    parts = [
        f"Проект: {context.title}",
        "",
        f"AI-выжимка: {context.summary}",
        "",
        f"Стек: {', '.join(context.effective_stack) or 'не определён'}",
    ]

    # Ценовая информация (критично — без неё модель фантазирует бюджет!)
    if context.price_final:
        parts.append("")
        parts.append(f"Бюджет разработчика: {context.price_final} руб.")
    if context.price_min or context.price_max:
        price_range = []
        if context.price_min:
            price_range.append(f"от {context.price_min}")
        if context.price_max:
            price_range.append(f"до {context.price_max}")
        parts.append(f"AI-оценка бюджета: {' '.join(price_range)} руб.")

    # Сроки
    if context.timeline_final:
        parts.append("")
        parts.append(f"Сроки разработчика: {context.timeline_final}")
    elif context.timeline_days:
        parts.append("")
        parts.append(f"AI-оценка сроков: {context.timeline_days} дн.")

    # Клиентские данные
    client_parts = context.format_client_data_parts()
    if client_parts:
        parts.append("")
        parts.extend(client_parts)

    # Заметки разработчика
    if context.custom_notes:
        parts.append("")
        parts.append(f"Заметки разработчика: {context.custom_notes}")

    # Оригинал заявки
    parts.append("")
    parts.append(f"Оригинал заявки:\n{context.raw_text}")

    return "\n".join(p for p in parts)
