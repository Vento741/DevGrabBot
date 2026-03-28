"""Circuit Breaker — остановка парсера при каскадных ошибках.

Три состояния:
- CLOSED: нормальная работа, считаем ошибки
- OPEN: парсер остановлен (cooldown), все запросы блокируются
- HALF_OPEN: пробная итерация после cooldown
"""

import enum
import logging
import time

logger = logging.getLogger(__name__)


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit Breaker для парсера.

    Args:
        threshold: количество ошибок подряд до перехода в OPEN
        cooldown_sec: время в секундах до перехода из OPEN в HALF_OPEN
    """

    def __init__(self, threshold: int = 5, cooldown_sec: int = 1800) -> None:
        self._threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._total_trips = 0  # сколько раз сработал CB

    @property
    def state(self) -> CircuitState:
        """Текущее состояние с автопереходом OPEN → HALF_OPEN по таймеру."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.cooldown_sec:
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit Breaker: OPEN → HALF_OPEN (прошло %.0f сек)",
                    elapsed,
                )
        return self._state

    @property
    def is_open(self) -> bool:
        """True если запросы заблокированы (OPEN)."""
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def total_trips(self) -> int:
        return self._total_trips

    @property
    def remaining_cooldown_sec(self) -> float:
        """Оставшееся время cooldown в секундах (0 если не в OPEN)."""
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        remaining = self.cooldown_sec - elapsed
        return max(0.0, remaining)

    def record_success(self) -> None:
        """Записать успешную операцию — сбросить счётчик, вернуть в CLOSED."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit Breaker: HALF_OPEN → CLOSED (успешная операция)")
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Записать ошибку. При достижении порога — перейти в OPEN."""
        self._failure_count += 1

        if self._state == CircuitState.HALF_OPEN:
            # Неудача в пробном запросе — сразу обратно в OPEN
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._total_trips += 1
            logger.warning(
                "Circuit Breaker: HALF_OPEN → OPEN (неудача в пробном запросе, "
                "cooldown %d сек)",
                self.cooldown_sec,
            )
            return

        if self._failure_count >= self._threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._total_trips += 1
            logger.warning(
                "Circuit Breaker: CLOSED → OPEN (%d ошибок подряд, "
                "cooldown %d сек)",
                self._failure_count,
                self.cooldown_sec,
            )

    def reset(self) -> None:
        """Принудительный сброс в CLOSED."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        logger.info("Circuit Breaker: принудительный сброс в CLOSED")

    def to_dict(self) -> dict:
        """Состояние для мониторинга."""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "threshold": self._threshold,
            "cooldown_sec": self.cooldown_sec,
            "remaining_cooldown_sec": round(self.remaining_cooldown_sec, 1),
            "total_trips": self._total_trips,
        }
