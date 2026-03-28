"""Полный контекст заявки для всех этапов AI-генерации."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.models import AiAnalysis, Order, OrderAssignment

# Сентинельные значения из AI-анализа (используются в промптах)
MISSING_BUDGET = "Не указан"
MISSING_DEADLINE = "Не указаны"
MISSING_REQUIREMENTS = "Не уточнены"


@dataclass
class OrderContext:
    """Собирает ВСЕ данные заявки для передачи на каждом этапе генерации.

    Два слоя данных:
    - AI-анализ (stack, price_min/price_max, timeline_days) — исходные оценки
    - Решения разработчика (stack_final, price_final, timeline_final) — переопределения
    Свойства effective_* выбирают данные разработчика с fallback на AI.
    """

    # Оригинал заявки
    raw_text: str = ""
    title: str = ""
    external_id: str = ""

    # Данные клиента (из extra_data AI-анализа)
    client_name: str = ""
    client_budget: str = ""
    client_deadline: str = ""
    client_requirements: str = ""

    # AI-анализ
    summary: str = ""
    stack: list[str] = field(default_factory=list)
    price_min: int | None = None
    price_max: int | None = None
    timeline_days: str = ""
    complexity: str = "medium"
    relevance_score: int = 0
    questions: list[str] = field(default_factory=list)
    risks: str = ""
    response_draft: str = ""

    # Решения разработчика
    price_final: int | None = None
    timeline_final: str = ""
    stack_final: list[str] = field(default_factory=list)
    custom_notes: str = ""

    # Pre Roadmap (если сгенерирован)
    roadmap_text: str | None = None

    @classmethod
    def from_order_data(
        cls,
        order: Order,
        analysis: AiAnalysis | None = None,
        assignment: OrderAssignment | None = None,
    ) -> OrderContext:
        """Собрать контекст из моделей БД."""
        ctx = cls(
            raw_text=order.raw_text or "",
            title=order.title or "",
            external_id=order.external_id or "",
        )

        if analysis:
            extra = analysis.extra_data or {}
            ctx.summary = analysis.summary or ""
            ctx.stack = analysis.stack or []
            ctx.price_min = analysis.price_min
            ctx.price_max = analysis.price_max
            ctx.timeline_days = analysis.timeline_days or ""
            ctx.complexity = analysis.complexity or "medium"
            ctx.relevance_score = analysis.relevance_score or 0
            ctx.response_draft = analysis.response_draft or ""

            # Данные из extra_data
            ctx.client_requirements = extra.get("client_requirements", "")
            ctx.client_budget = extra.get("client_budget_text", "")
            ctx.client_deadline = extra.get("client_deadline_text", "")
            ctx.questions = extra.get("questions_to_client", [])
            ctx.risks = extra.get("risks", "")

        if assignment:
            ctx.price_final = assignment.price_final
            ctx.timeline_final = assignment.timeline_final or ""
            ctx.stack_final = assignment.stack_final or []
            ctx.custom_notes = assignment.custom_notes or ""
            ctx.roadmap_text = assignment.roadmap_text or None

        return ctx

    # --- Предикаты для клиентских данных ---

    @property
    def has_client_budget(self) -> bool:
        return bool(self.client_budget) and self.client_budget != MISSING_BUDGET

    @property
    def has_client_deadline(self) -> bool:
        return bool(self.client_deadline) and self.client_deadline != MISSING_DEADLINE

    @property
    def has_client_requirements(self) -> bool:
        return bool(self.client_requirements) and self.client_requirements != MISSING_REQUIREMENTS

    # --- Эффективные значения (разработчик > AI) ---

    @property
    def effective_stack(self) -> list[str]:
        """Стек: приоритет у разработчика, fallback на AI."""
        return self.stack_final if self.stack_final else self.stack

    @property
    def effective_price(self) -> int | None:
        """Цена: приоритет у разработчика, fallback на price_max."""
        return self.price_final or self.price_max or None

    @property
    def effective_timeline(self) -> str:
        """Сроки: приоритет у разработчика, fallback на AI."""
        return self.timeline_final or self.timeline_days or "по договорённости"

    def format_client_data_parts(self) -> list[str]:
        """Форматирует блок данных клиента для промптов."""
        parts: list[str] = []
        if self.has_client_requirements:
            parts.append(f"Пожелания клиента: {self.client_requirements}")
        if self.has_client_budget:
            parts.append(f"Бюджет клиента: {self.client_budget}")
        if self.has_client_deadline:
            parts.append(f"Сроки клиента: {self.client_deadline}")
        return parts
