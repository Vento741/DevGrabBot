"""Health Monitor — метрики и статус парсера в Redis.

Сохраняет текущее состояние всех resilience-компонентов в Redis
для мониторинга и отладки.
"""

import json
import logging
from datetime import datetime

import redis.asyncio as aioredis

from src.parser.resilience.circuit_breaker import CircuitBreaker
from src.parser.resilience.request_scheduler import RequestScheduler
from src.parser.resilience.token_manager import TokenManager

logger = logging.getLogger(__name__)

REDIS_HEALTH_KEY = "devgrab:parser:health"


class HealthMonitor:
    """Мониторинг состояния парсера.

    Args:
        redis: подключение к Redis
        circuit_breaker: экземпляр CircuitBreaker
        scheduler: экземпляр RequestScheduler
        token_manager: экземпляр TokenManager
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        circuit_breaker: CircuitBreaker,
        scheduler: RequestScheduler,
        token_manager: TokenManager,
    ) -> None:
        self._redis = redis
        self._cb = circuit_breaker
        self._scheduler = scheduler
        self._tm = token_manager
        self._total_iterations = 0
        self._total_orders = 0
        self._last_success: str | None = None
        self._last_error: str | None = None
        self._last_error_msg: str | None = None

    def record_iteration(self, orders_count: int = 0) -> None:
        """Записать успешную итерацию."""
        self._total_iterations += 1
        self._total_orders += orders_count
        self._last_success = datetime.utcnow().isoformat()

    def record_error(self, error_msg: str) -> None:
        """Записать ошибку."""
        self._last_error = datetime.utcnow().isoformat()
        self._last_error_msg = error_msg[:200]

    async def save(self) -> None:
        """Сохранить полное состояние в Redis."""
        state = {
            "updated_at": datetime.utcnow().isoformat(),
            "total_iterations": self._total_iterations,
            "total_orders": self._total_orders,
            "last_success": self._last_success,
            "last_error": self._last_error,
            "last_error_msg": self._last_error_msg,
            "circuit_breaker": self._cb.to_dict(),
            "scheduler": self._scheduler.to_dict(),
            "token_manager": self._tm.to_dict(),
        }
        try:
            await self._redis.set(REDIS_HEALTH_KEY, json.dumps(state, ensure_ascii=False))
        except Exception:
            logger.debug("Не удалось сохранить health в Redis")

    async def get_status(self) -> dict | None:
        """Прочитать статус из Redis."""
        data = await self._redis.get(REDIS_HEALTH_KEY)
        if data:
            return json.loads(data)
        return None
