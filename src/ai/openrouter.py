"""Клиент OpenRouter API."""
import json
import logging

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient:
    """Асинхронный клиент для работы с OpenRouter API."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._http = httpx.AsyncClient(timeout=60)

    def _build_payload(self, system_prompt: str, user_message: str) -> dict:
        """Собрать payload для запроса к API."""
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
        }

    async def complete(self, system_prompt: str, user_message: str) -> str:
        """Отправить запрос и вернуть текст ответа."""
        payload = self._build_payload(system_prompt, user_message)
        response = await self._http.post(
            OPENROUTER_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def complete_json(self, system_prompt: str, user_message: str) -> dict:
        """Отправить запрос и вернуть распарсенный JSON-ответ."""
        text = await self.complete(system_prompt, user_message)
        text = text.strip()
        # Убираем markdown-обёртку ```json ... ```
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        return json.loads(text)

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
