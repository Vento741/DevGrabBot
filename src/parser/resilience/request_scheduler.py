"""Request Scheduler — jitter + адаптивные интервалы по времени суток.

Интервалы парсинга (МСК = UTC+3):
- 00:00-07:00 — ночь: base × night_multiplier (по умолчанию ×3)
- 07:00-10:00 — утро: base × 2
- 10:00-19:00 — рабочее время: base (как есть)
- 19:00-00:00 — вечер: base × 3
"""

import logging
import random
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


class RequestScheduler:
    """Планировщик интервалов парсинга с jitter и адаптивностью.

    Args:
        base_interval_sec: базовый интервал (из настроек)
        jitter_factor: фактор jitter (±N% к интервалу)
        night_multiplier: множитель для ночи
    """

    def __init__(
        self,
        base_interval_sec: int = 300,
        jitter_factor: float = 0.2,
        night_multiplier: float = 3.0,
    ) -> None:
        self._base = base_interval_sec
        self._jitter = jitter_factor
        self._night_mult = night_multiplier
        self._consecutive_errors = 0

    def get_next_delay(self) -> float:
        """Рассчитать задержку до следующей итерации (секунды).

        Учитывает: время суток, jitter, backoff при ошибках.
        """
        # Базовый интервал × множитель времени суток
        multiplier = self._get_time_multiplier()
        interval = self._base * multiplier

        # Backoff при последовательных ошибках
        if self._consecutive_errors > 0:
            backoff = min(2 ** self._consecutive_errors, 16)  # max 16x
            interval *= backoff
            logger.info(
                "Backoff: %d ошибок подряд, множитель x%d",
                self._consecutive_errors,
                backoff,
            )

        # Jitter: ±N%
        jitter_range = interval * self._jitter
        jitter = random.uniform(-jitter_range, jitter_range)
        delay = max(60.0, interval + jitter)  # минимум 1 минута

        logger.info(
            "Следующая итерация через %.0f сек (base=%d, mult=%.1f, jitter=%.0f, errors=%d)",
            delay,
            self._base,
            multiplier,
            jitter,
            self._consecutive_errors,
        )
        return delay

    def record_success(self) -> None:
        """Итерация успешна — сбросить счётчик ошибок."""
        self._consecutive_errors = 0

    def record_error(self) -> None:
        """Итерация с ошибкой — увеличить счётчик."""
        self._consecutive_errors += 1

    def _get_time_multiplier(self) -> float:
        """Множитель интервала в зависимости от времени суток (МСК)."""
        now_msk = datetime.now(MSK)
        hour = now_msk.hour

        if 0 <= hour < 7:
            return self._night_mult  # ночь
        elif 7 <= hour < 10:
            return 2.0  # утро
        elif 10 <= hour < 19:
            return 1.0  # рабочее время
        else:
            return self._night_mult  # вечер

    def to_dict(self) -> dict:
        """Состояние для мониторинга (без side effects)."""
        now_msk = datetime.now(MSK)
        multiplier = self._get_time_multiplier()
        return {
            "base_interval_sec": self._base,
            "jitter_factor": self._jitter,
            "night_multiplier": self._night_mult,
            "consecutive_errors": self._consecutive_errors,
            "current_multiplier": multiplier,
            "current_time_msk": now_msk.strftime("%H:%M"),
        }
