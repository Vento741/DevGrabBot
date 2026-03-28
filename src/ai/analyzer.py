"""Анализатор заявок через AI."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.ai.openrouter import OpenRouterClient
from src.ai.prompts.analyze import SYSTEM_PROMPT as ANALYZE_PROMPT
from src.ai.prompts.analyze import build_analyze_prompt
from src.ai.prompts.response import SYSTEM_PROMPT as RESPONSE_PROMPT
from src.ai.prompts.response import build_response_prompt, build_response_prompt_v2
from src.ai.prompts.roadmap import SYSTEM_PROMPT as ROADMAP_SYSTEM_PROMPT
from src.ai.prompts.roadmap import build_roadmap_prompt_v2

if TYPE_CHECKING:
    from src.ai.context import OrderContext

logger = logging.getLogger(__name__)


class OrderAnalyzer:
    """Анализатор фриланс-заявок с помощью AI."""

    def __init__(self, client: OpenRouterClient) -> None:
        self.client = client

    async def analyze_order(self, raw_text: str, system_prompt: str | None = None) -> dict:
        """Анализировать заявку, вернуть структурированный результат.

        Args:
            raw_text: Полный текст заявки.
            system_prompt: Кастомный системный промпт (из DB). Если None — используется
                           файловый ANALYZE_PROMPT.
        """
        prompt = system_prompt if system_prompt is not None else ANALYZE_PROMPT
        user_msg = build_analyze_prompt(raw_text)
        result = await self.client.complete_json(prompt, user_msg)
        logger.info(
            "AI-анализ: relevance=%s, price=%s-%s",
            result.get("relevance_score"),
            result.get("price_min"),
            result.get("price_max"),
        )
        return result

    async def generate_response(
        self,
        summary: str,
        stack: list[str],
        price: int,
        timeline: str,
        custom_notes: str = "",
    ) -> str:
        """Сгенерировать текст отклика для менеджера."""
        user_msg = build_response_prompt(
            summary, stack, price, timeline, custom_notes
        )
        return await self.client.complete(RESPONSE_PROMPT, user_msg)

    async def generate_response_v2(
        self,
        context: OrderContext,
        style: dict | None = None,
    ) -> str:
        """Сгенерировать отклик с полным контекстом (v2)."""
        user_msg = build_response_prompt_v2(context, style)
        return await self.client.complete(RESPONSE_PROMPT, user_msg)

    async def generate_roadmap(self, context: OrderContext) -> str:
        """Сгенерировать Pre Roadmap с полным контекстом."""
        user_msg = build_roadmap_prompt_v2(context)
        return await self.client.complete(ROADMAP_SYSTEM_PROMPT, user_msg)
