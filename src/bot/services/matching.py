"""Матчинг разработчиков по стеку технологий."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def match_developers(
    order_stack: list[str],
    developers: list[Any],
) -> list[tuple[Any, int, list[str]]]:
    """Сопоставляет разработчиков с требуемым стеком заявки.

    Алгоритм оценки:
    - Совпадение в primary даёт вес 2.
    - Совпадение в secondary даёт вес 1.
    - score = (primary_matches * 2 + secondary_matches) / max_possible * 100
    - max_possible = len(order_stack) * 2 (если бы всё было primary).

    Сравнение технологий — case-insensitive.

    Args:
        order_stack: список технологий из заявки, например ["Python", "FastAPI"].
        developers: список объектов TeamMember (или duck-type совместимых).

    Returns:
        Список кортежей (developer, score_percent, matched_techs),
        отсортированный по score_percent по убыванию.
        Возвращаются только разработчики с score > 0.
    """
    if not order_stack:
        return []

    max_possible = len(order_stack) * 2
    order_stack_lower = [tech.lower() for tech in order_stack]

    results: list[tuple[Any, int, list[str]]] = []

    for dev in developers:
        stack_priority: dict = getattr(dev, "stack_priority", {}) or {}
        primary: list[str] = stack_priority.get("primary", []) or []
        secondary: list[str] = stack_priority.get("secondary", []) or []

        primary_lower = {t.lower() for t in primary}
        secondary_lower = {t.lower() for t in secondary}

        matched_techs: list[str] = []
        raw_score = 0

        for idx, tech_lower in enumerate(order_stack_lower):
            original_tech = order_stack[idx]
            if tech_lower in primary_lower:
                raw_score += 2
                matched_techs.append(original_tech)
            elif tech_lower in secondary_lower:
                raw_score += 1
                matched_techs.append(original_tech)

        if raw_score == 0:
            continue

        score_percent = round(raw_score / max_possible * 100)
        results.append((dev, score_percent, matched_techs))

    results.sort(key=lambda item: item[1], reverse=True)

    logger.debug(
        "Матчинг завершён: стек %s, подходящих разработчиков %d",
        order_stack,
        len(results),
    )

    return results


def format_matches_block(matches: list[tuple[Any, int, list[str]]]) -> str:
    """Форматирует блок «Подходящие разработчики» для уведомления.

    Args:
        matches: результат match_developers().

    Returns:
        Строка HTML для вставки в текст уведомления.
        Пустая строка если совпадений нет.
    """
    if not matches:
        return ""

    lines = ["<b>Подходящие разработчики:</b>"]
    for dev, score, techs in matches:
        username = getattr(dev, "tg_username", None)
        name = getattr(dev, "name", "Неизвестно")
        mention = f"@{username}" if username else name
        techs_str = ", ".join(techs)
        lines.append(f"  {mention} ({techs_str} — {score}%)")

    return "\n".join(lines)
