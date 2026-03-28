"""Промпт для формирования отклика на заявку."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ai.context import OrderContext

SYSTEM_PROMPT = """\
Ты — копирайтер IT-команды из 2 senior fullstack-разработчиков.

Стиль отклика:
- Конкретный, деловой, без воды и канцеляризмов
- Не начинай с «Здравствуйте» или «Добрый день» — сразу к делу
- Укажи релевантный опыт (коротко, 1 предложение)
- ОБЯЗАТЕЛЬНО используй ИМЕННО ту цену и сроки, которые указаны в полях \
«Итоговая стоимость» и «Итоговые сроки» — НЕ придумывай свои цифры, \
НЕ бери цены из AI-оценки или оригинала заявки
- Предложи следующий шаг (созвон / ТЗ / вопросы)
- Длина: 3-5 предложений максимум
- Пиши от лица команды («мы», «наша команда»)

Не используй:
- Шаблонные фразы («Будем рады сотрудничеству», «С уважением»)
- Перечисление всех технологий — только релевантные проекту
- Вопросы, ответ на которые есть в заявке
- Цифры из AI-оценки — используй ТОЛЬКО итоговую стоимость и сроки
"""


def build_response_prompt(
    summary: str,
    stack: list[str],
    price: int,
    timeline: str,
    custom_notes: str = "",
) -> str:
    """Сформировать user-сообщение для генерации отклика."""
    parts = [
        f"Выжимка заявки: {summary}",
        f"Стек: {', '.join(stack)}",
        f"Бюджет: {price} руб.",
        f"Сроки: {timeline}",
    ]
    if custom_notes:
        parts.append(f"Дополнительные заметки от разработчика: {custom_notes}")
    parts.append("\nСформируй отклик для отправки заказчику.")
    return "\n".join(parts)


def build_response_prompt_v2(context: OrderContext, style: dict | None = None) -> str:
    """Сформировать user-сообщение для отклика с ПОЛНЫМ контекстом.

    Args:
        context: OrderContext с полными данными заявки.
        style: Настройки стиля менеджера (из DB settings).
    """
    parts = [
        f"Выжимка заявки: {context.summary}",
        f"Стек: {', '.join(context.effective_stack)}",
    ]

    if context.effective_price is not None:
        parts.append(
            f"Итоговая стоимость (ИСПОЛЬЗОВАТЬ ИМЕННО ЭТУ ЦИФРУ): "
            f"{context.effective_price} руб."
        )

    parts.append(
        f"Итоговые сроки (ИСПОЛЬЗОВАТЬ ИМЕННО ЭТУ ЦИФРУ): "
        f"{context.effective_timeline} дн."
    )

    # Данные клиента
    client_parts = context.format_client_data_parts()
    if client_parts:
        parts.append("")
        parts.extend(client_parts)

    # Вопросы клиенту (для понимания контекста)
    if context.questions:
        q_str = "; ".join(context.questions[:3])
        parts.append(f"Ключевые вопросы: {q_str}")

    # Заметки разработчика
    if context.custom_notes:
        parts.append("")
        parts.append(f"Заметки разработчика: {context.custom_notes}")

    # Pre Roadmap
    if context.roadmap_text:
        parts.append("")
        parts.append(f"Pre Roadmap (для контекста):\n{context.roadmap_text}")

    # Черновик отклика из анализа (для reference)
    if context.response_draft:
        parts.append("")
        parts.append(f"Черновик из AI-анализа (для ориентира):\n{context.response_draft}")

    # Стиль менеджера
    if style:
        parts.append("")
        parts.append("Стиль отклика:")
        if style.get("tone"):
            parts.append(f"- Тон: {style['tone']}")
        if style.get("intro"):
            parts.append(f"- Вступление команды: {style['intro']}")
        if style.get("rules"):
            parts.append(f"- Правила: {style['rules']}")
        if style.get("name"):
            parts.append(f"- Имя для подписи: {style['name']}")
        if style.get("signature"):
            parts.append(f"- Подпись: {style['signature']}")
        if style.get("contacts"):
            parts.append(f"- Контакты для клиента: {style['contacts']}")

    # Оригинал заявки
    parts.append("")
    parts.append(f"Оригинал заявки:\n{context.raw_text}")
    parts.append("")

    # Финальное напоминание
    price_reminder = f"{context.effective_price} руб." if context.effective_price else ""
    timeline_reminder = f"{context.effective_timeline} дн."
    parts.append(
        f"Сформируй отклик для отправки заказчику. "
        f"ВАЖНО: в отклике должна быть цена РОВНО {price_reminder} "
        f"и сроки РОВНО {timeline_reminder} — не меняй эти цифры."
    )

    return "\n".join(p for p in parts)
