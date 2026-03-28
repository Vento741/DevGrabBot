"""Token Manager — кэширование cookies Профи.ру в Redis + backoff авторизации.

- Кэширует ВСЕ cookies сессии в Redis (переживает перезапуск)
- Lazy re-auth: не авторизуется чаще чем раз в N секунд
- Exponential backoff при неудачных попытках авторизации
- Интеграция с CircuitBreaker и AlertService
"""

import asyncio
import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import redis.asyncio as aioredis

from src.parser.resilience.circuit_breaker import CircuitBreaker
from src.parser.resilience.alert_service import AlertService

logger = logging.getLogger(__name__)

REDIS_TOKEN_KEY = "devgrab:parser:token"
REDIS_COOKIES_KEY = "devgrab:parser:cookies"
REDIS_AUTH_ATTEMPTS_KEY = "devgrab:parser:auth_attempts"


class TokenManager:
    """Управление токеном и cookies авторизации с кэшированием и backoff.

    Args:
        redis: подключение к Redis
        circuit_breaker: экземпляр CircuitBreaker
        alert_service: экземпляр AlertService
        auth_fn: синхронная функция авторизации (Selenium), возвращает dict cookies
        token_ttl_sec: TTL токена в Redis
        max_auth_attempts: максимум попыток авторизации подряд
        auth_cooldown_sec: минимальный интервал между авторизациями
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        circuit_breaker: CircuitBreaker,
        alert_service: AlertService,
        auth_fn: Callable[[], dict[str, str]],
        token_ttl_sec: int = 480,
        max_auth_attempts: int = 3,
        auth_cooldown_sec: int = 120,
    ) -> None:
        self._redis = redis
        self._cb = circuit_breaker
        self._alert = alert_service
        self._auth_fn = auth_fn
        self._token_ttl = token_ttl_sec
        self._max_attempts = max_auth_attempts
        self._auth_cooldown = auth_cooldown_sec
        self._token: str | None = None  # in-memory cache (prfr_bo_tkn)
        self._cookies: dict[str, str] = {}  # все cookies сессии
        self._last_auth_time: float = 0.0
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def get_token(self) -> str | None:
        """Получить валидный токен (memory → Redis → Selenium auth).

        Returns:
            Токен или None если получить не удалось.
        """
        # 1. In-memory cache
        if self._token:
            return self._token

        # 2. Redis cache (токен)
        cached = await self._redis.get(REDIS_TOKEN_KEY)
        if cached:
            self._token = cached
            # Восстанавливаем cookies из Redis
            cookies_json = await self._redis.get(REDIS_COOKIES_KEY)
            if cookies_json:
                try:
                    self._cookies = json.loads(cookies_json)
                except (json.JSONDecodeError, TypeError):
                    self._cookies = {}
            logger.debug("Токен восстановлен из Redis (cookies: %d шт)", len(self._cookies))
            return self._token

        # 3. Selenium авторизация
        return await self._refresh_token()

    def get_session_cookies(self) -> dict[str, str]:
        """Получить все cookies сессии для передачи в scraper."""
        return dict(self._cookies)

    async def invalidate(self) -> None:
        """Инвалидировать текущий токен и cookies (при 401)."""
        self._token = None
        self._cookies = {}
        await self._redis.delete(REDIS_TOKEN_KEY)
        await self._redis.delete(REDIS_COOKIES_KEY)
        logger.info("Токен и cookies инвалидированы")

    async def _refresh_token(self) -> str | None:
        """Получить новый токен через Selenium с backoff и rate limiting."""
        # Проверяем CB
        if self._cb.is_open:
            logger.warning("Авторизация заблокирована Circuit Breaker")
            return None

        # Rate limit: не чаще чем раз в auth_cooldown секунд
        elapsed = time.monotonic() - self._last_auth_time
        if elapsed < self._auth_cooldown:
            wait = self._auth_cooldown - elapsed
            logger.info(
                "Rate limit авторизации: ждём %.0f сек (cooldown %d сек)",
                wait,
                self._auth_cooldown,
            )
            await asyncio.sleep(wait)

        for attempt in range(1, self._max_attempts + 1):
            try:
                # Exponential backoff между попытками
                if attempt > 1:
                    delay = min(30 * (2 ** (attempt - 2)), 300)
                    jitter = random.uniform(0, delay * 0.5)
                    total_delay = delay + jitter
                    logger.info(
                        "Backoff перед попыткой %d: %.0f сек",
                        attempt,
                        total_delay,
                    )
                    await asyncio.sleep(total_delay)

                logger.info("Авторизация через Selenium (попытка %d/%d)", attempt, self._max_attempts)
                self._last_auth_time = time.monotonic()

                # Выполняем синхронный Selenium в executor — возвращает dict cookies
                loop = asyncio.get_running_loop()
                cookies_dict = await loop.run_in_executor(self._executor, self._auth_fn)

                # Успех — извлекаем токен и сохраняем все cookies
                token = cookies_dict.get("prfr_bo_tkn", "")
                self._token = token
                self._cookies = cookies_dict
                await self._redis.set(REDIS_TOKEN_KEY, token, ex=self._token_ttl)
                await self._redis.set(
                    REDIS_COOKIES_KEY, json.dumps(cookies_dict), ex=self._token_ttl
                )
                self._cb.record_success()
                logger.info(
                    "Токен и cookies закэшированы (TTL %d сек, cookies: %d шт)",
                    self._token_ttl, len(cookies_dict),
                )

                if attempt > 1:
                    await self._alert.auth_success()

                return token

            except Exception as exc:
                self._cb.record_failure()
                logger.error(
                    "Ошибка авторизации (попытка %d/%d): %s",
                    attempt,
                    self._max_attempts,
                    exc,
                )
                await self._alert.auth_failed(attempt, self._max_attempts, str(exc))

                if self._cb.is_open:
                    await self._alert.circuit_breaker_opened(
                        self._cb.failure_count,
                        self._cb.cooldown_sec,
                    )
                    return None

        logger.error("Все %d попыток авторизации исчерпаны", self._max_attempts)
        return None

    async def update_cookies_from_scraper(self, cookies: dict[str, str]) -> None:
        """Обновить cookies сессии из ответов сервера (Set-Cookie).

        Scraper обновляет свои _session_cookies при каждом HTTP-ответе,
        и worker периодически передаёт их сюда для кэширования в Redis.
        """
        if not cookies:
            return
        self._cookies.update(cookies)
        # Если в cookies есть обновлённый prfr_bo_tkn — обновляем и токен
        new_token = cookies.get("prfr_bo_tkn")
        if new_token and new_token != self._token:
            self._token = new_token
            await self._redis.set(REDIS_TOKEN_KEY, new_token, ex=self._token_ttl)
            logger.info("Токен обновлён из cookies ответа (TTL %d сек)", self._token_ttl)
        # Обновляем cookies в Redis
        await self._redis.set(
            REDIS_COOKIES_KEY, json.dumps(self._cookies), ex=self._token_ttl
        )

    async def set_initial_token(self, token: str) -> None:
        """Установить начальный токен из конфига (если есть)."""
        if token:
            self._token = token
            await self._redis.set(REDIS_TOKEN_KEY, token, ex=self._token_ttl)
            logger.info("Начальный токен установлен из конфига")

    def to_dict(self) -> dict:
        """Состояние для мониторинга."""
        return {
            "has_token": self._token is not None,
            "token_ttl_sec": self._token_ttl,
            "max_auth_attempts": self._max_attempts,
            "auth_cooldown_sec": self._auth_cooldown,
        }
