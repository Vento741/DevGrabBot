"""Тесты для клиента OpenRouter API."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.openrouter import OPENROUTER_URL, OpenRouterClient


class TestOpenRouterClientInit:
    """Тесты инициализации клиента."""

    def test_init_stores_api_key_and_model(self) -> None:
        client = OpenRouterClient(api_key="test-key", model="test/model")
        assert client.api_key == "test-key"
        assert client.model == "test/model"

    def test_init_creates_http_client(self) -> None:
        client = OpenRouterClient(api_key="k", model="m")
        assert client._http is not None


class TestBuildPayload:
    """Тесты формирования payload."""

    def test_build_payload_structure(self) -> None:
        client = OpenRouterClient(api_key="k", model="google/gemini-flash")
        payload = client._build_payload("system msg", "user msg")

        assert payload["model"] == "google/gemini-flash"
        assert payload["temperature"] == 0.3
        assert len(payload["messages"]) == 2
        assert payload["messages"][0] == {
            "role": "system",
            "content": "system msg",
        }
        assert payload["messages"][1] == {
            "role": "user",
            "content": "user msg",
        }

    def test_build_payload_preserves_content(self) -> None:
        client = OpenRouterClient(api_key="k", model="m")
        long_text = "A" * 5000
        payload = client._build_payload("sys", long_text)
        assert payload["messages"][1]["content"] == long_text


class TestComplete:
    """Тесты метода complete."""

    @pytest.mark.asyncio
    async def test_complete_returns_content(self) -> None:
        client = OpenRouterClient(api_key="test-key", model="test/model")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "AI ответ"}}]
        }
        mock_response.raise_for_status = MagicMock()
        client._http.post = AsyncMock(return_value=mock_response)

        result = await client.complete("sys", "usr")
        assert result == "AI ответ"

        client._http.post.assert_called_once()
        call_kwargs = client._http.post.call_args
        assert call_kwargs[0][0] == OPENROUTER_URL
        assert "Bearer test-key" in call_kwargs[1]["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_complete_json_parses_response(self) -> None:
        client = OpenRouterClient(api_key="k", model="m")
        expected = {"relevance_score": 85, "stack": ["Python"]}
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(expected)}}]
        }
        mock_response.raise_for_status = MagicMock()
        client._http.post = AsyncMock(return_value=mock_response)

        result = await client.complete_json("sys", "usr")
        assert result == expected

    @pytest.mark.asyncio
    async def test_complete_json_strips_markdown_fence(self) -> None:
        client = OpenRouterClient(api_key="k", model="m")
        wrapped = '```json\n{"key": "value"}\n```'
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": wrapped}}]
        }
        mock_response.raise_for_status = MagicMock()
        client._http.post = AsyncMock(return_value=mock_response)

        result = await client.complete_json("sys", "usr")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        client = OpenRouterClient(api_key="k", model="m")
        client._http.aclose = AsyncMock()
        await client.close()
        client._http.aclose.assert_called_once()
