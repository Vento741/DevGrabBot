"""Alert Service — уведомления о проблемах парсера в Telegram.

Дедупликация: одинаковые алерты не отправляются чаще чем раз в N секунд.
"""

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AlertService:
    """Отправка алертов парсера в Telegram (всем участникам команды).

    Args:
        bot_token: токен Telegram бота
        chat_ids: список ID чатов для алертов
        dedup_sec: минимальный интервал между одинаковыми алертами
    """

    # Максимум уникальных ключей в кэше дедупликации
    _MAX_DEDUP_KEYS = 50

    def __init__(
        self,
        bot_token: str,
        chat_ids: list[int],
        dedup_sec: int = 900,
    ) -> None:
        self._chat_ids = chat_ids
        self._dedup_sec = dedup_sec
        self._last_sent: dict[str, float] = {}
        self._send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._http = httpx.AsyncClient(timeout=10.0)

    async def error(self, key: str, message: str) -> None:
        """Отправить алерт об ошибке (с дедупликацией)."""
        await self._send(key, f"🚨 *Парсер: ошибка*\n\n{message}")

    async def warning(self, key: str, message: str) -> None:
        """Отправить предупреждение (с дедупликацией)."""
        await self._send(key, f"⚠️ *Парсер: предупреждение*\n\n{message}")

    async def info(self, key: str, message: str) -> None:
        """Отправить информационное сообщение (с дедупликацией)."""
        await self._send(key, f"ℹ️ *Парсер*\n\n{message}")

    async def circuit_breaker_opened(self, failure_count: int, cooldown_sec: int) -> None:
        """Алерт: Circuit Breaker сработал."""
        await self.error(
            "cb_opened",
            f"Circuit Breaker сработал!\n"
            f"Ошибок подряд: {failure_count}\n"
            f"Парсер остановлен на {cooldown_sec // 60} мин",
        )

    async def circuit_breaker_recovered(self) -> None:
        """Алерт: Circuit Breaker восстановился."""
        await self.info("cb_recovered", "Circuit Breaker восстановлен, парсер работает")

    async def auth_failed(self, attempt: int, max_attempts: int, error: str) -> None:
        """Алерт: ошибка авторизации."""
        await self.error(
            "auth_failed",
            f"Ошибка авторизации (попытка {attempt}/{max_attempts})\n"
            f"Ошибка: {error}",
        )

    async def auth_success(self) -> None:
        """Алерт: успешная авторизация после проблем."""
        await self.info("auth_success", "Авторизация успешна, токен обновлён")

    async def _send(self, key: str, text: str) -> None:
        """Отправить сообщение с дедупликацией по ключу."""
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        if now - last < self._dedup_sec:
            logger.debug("Алерт '%s' дедуплицирован (осталось %.0f сек)", key, self._dedup_sec - (now - last))
            return

        sent = False
        for chat_id in self._chat_ids:
            try:
                resp = await self._http.post(self._send_url, json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
                if resp.status_code == 200:
                    sent = True
                else:
                    logger.warning("Не удалось отправить алерт '%s' в chat %s: %d", key, chat_id, resp.status_code)
            except Exception:
                logger.exception("Ошибка отправки алерта '%s' в chat %s", key, chat_id)

        if sent:
            self._last_sent[key] = now
            if len(self._last_sent) > self._MAX_DEDUP_KEYS:
                oldest_key = min(self._last_sent, key=self._last_sent.get)
                del self._last_sent[oldest_key]
            logger.info("Алерт '%s' отправлен", key)

    async def close(self) -> None:
        await self._http.aclose()
