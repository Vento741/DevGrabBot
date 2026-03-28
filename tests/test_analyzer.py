"""Тесты для анализатора заявок."""
import json
from unittest.mock import AsyncMock

import pytest

from src.ai.analyzer import OrderAnalyzer
from src.ai.openrouter import OpenRouterClient
from src.ai.prompts.analyze import build_analyze_prompt
from src.ai.prompts.response import build_response_prompt


@pytest.fixture
def mock_client() -> OpenRouterClient:
    """Создать мок-клиент OpenRouter."""
    client = OpenRouterClient(api_key="test", model="test/model")
    client.complete = AsyncMock()
    client.complete_json = AsyncMock()
    return client


@pytest.fixture
def analyzer(mock_client: OpenRouterClient) -> OrderAnalyzer:
    """Создать анализатор с мок-клиентом."""
    return OrderAnalyzer(client=mock_client)


class TestAnalyzeOrder:
    """Тесты метода analyze_order."""

    @pytest.mark.asyncio
    async def test_analyze_order_calls_complete_json(
        self, analyzer: OrderAnalyzer, mock_client: OpenRouterClient
    ) -> None:
        mock_client.complete_json.return_value = {
            "summary": "Telegram-бот для записи к врачу",
            "stack": ["Python", "aiogram"],
            "price_min": 30000,
            "price_max": 50000,
            "timeline_days": 7,
            "relevance_score": 88,
            "complexity": "medium",
            "response_draft": "Сделаем бота за 7 дней.",
        }

        result = await analyzer.analyze_order("Нужен TG-бот для клиники")

        mock_client.complete_json.assert_called_once()
        assert result["relevance_score"] == 88
        assert result["complexity"] == "medium"
        assert "Python" in result["stack"]

    @pytest.mark.asyncio
    async def test_analyze_order_passes_raw_text(
        self, analyzer: OrderAnalyzer, mock_client: OpenRouterClient
    ) -> None:
        mock_client.complete_json.return_value = {"relevance_score": 50}
        raw = "Парсер авито"

        await analyzer.analyze_order(raw)

        call_args = mock_client.complete_json.call_args
        user_msg = call_args[0][1]
        assert "Парсер авито" in user_msg


class TestGenerateResponse:
    """Тесты метода generate_response."""

    @pytest.mark.asyncio
    async def test_generate_response_returns_text(
        self, analyzer: OrderAnalyzer, mock_client: OpenRouterClient
    ) -> None:
        mock_client.complete.return_value = "Готовы взять проект. Сроки — 10 дней."

        result = await analyzer.generate_response(
            summary="Парсер маркетплейса",
            stack=["Python", "Selenium"],
            price=45000,
            timeline="10 дней",
        )

        assert result == "Готовы взять проект. Сроки — 10 дней."
        mock_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_response_with_custom_notes(
        self, analyzer: OrderAnalyzer, mock_client: OpenRouterClient
    ) -> None:
        mock_client.complete.return_value = "Отклик"

        await analyzer.generate_response(
            summary="Бот",
            stack=["Python"],
            price=20000,
            timeline="5 дней",
            custom_notes="Есть похожий проект в портфолио",
        )

        call_args = mock_client.complete.call_args
        user_msg = call_args[0][1]
        assert "похожий проект в портфолио" in user_msg


class TestPromptBuilders:
    """Тесты функций формирования промптов."""

    def test_build_analyze_prompt_includes_text(self) -> None:
        result = build_analyze_prompt("Нужен сайт-визитка")
        assert "Нужен сайт-визитка" in result
        assert "Профи.ру" in result

    def test_build_response_prompt_includes_all_fields(self) -> None:
        result = build_response_prompt(
            summary="Парсер",
            stack=["Python", "BS4"],
            price=30000,
            timeline="5 дней",
        )
        assert "Парсер" in result
        assert "Python" in result
        assert "BS4" in result
        assert "30000" in result
        assert "5 дней" in result

    def test_build_response_prompt_with_notes(self) -> None:
        result = build_response_prompt(
            summary="Бот",
            stack=["aiogram"],
            price=10000,
            timeline="3 дня",
            custom_notes="Срочно",
        )
        assert "Срочно" in result

    def test_build_response_prompt_without_notes(self) -> None:
        result = build_response_prompt(
            summary="Бот",
            stack=["aiogram"],
            price=10000,
            timeline="3 дня",
        )
        assert "заметки" not in result.lower() or "Дополнительные" not in result
