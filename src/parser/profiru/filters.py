"""Фильтры для заказов с Профи.ру."""

import logging
from datetime import datetime, timezone

from src.core.config import Settings

logger = logging.getLogger(__name__)

# Минимальный возраст заказа в секундах (слишком свежие пропускаем).
MIN_ORDER_AGE_SECONDS = 70


class ProfiruFilters:
    """Фильтрация заказов по стоп-словам, возрасту и наличию external_id."""

    def __init__(self, settings: Settings) -> None:
        self.stop_words: list[str] = [w.lower() for w in settings.stop_words]
        self.max_age_hours: int = settings.time_threshold_hours

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    async def refresh_stop_words(self, session, config: Settings) -> None:
        """Обновить стоп-слова из DB (fallback на config.stop_words).

        Args:
            session: AsyncSession — открытая сессия БД.
            config: Settings — для fallback при отсутствии записи в DB.
        """
        from src.core.settings_service import get_stop_words
        words = await get_stop_words(session, config)
        self.stop_words = [w.lower() for w in words]
        logger.debug("Стоп-слова обновлены из DB: %d шт.", len(self.stop_words))

    def is_acceptable(self, order: dict) -> bool:
        """Проверить заказ на соответствие всем фильтрам.

        Returns:
            True если заказ проходит фильтрацию и должен быть обработан.
        """
        if not self._has_external_id(order):
            logger.debug("Заказ отклонён: отсутствует external_id")
            return False

        if not self._check_age(order):
            return False

        if self._contains_stop_words(order):
            return False

        return True

    # ------------------------------------------------------------------
    # Приватные проверки
    # ------------------------------------------------------------------

    def _has_external_id(self, order: dict) -> bool:
        """Заказ должен содержать непустой external_id."""
        return bool(order.get("external_id"))

    def _check_age(self, order: dict) -> bool:
        """Заказ не должен быть слишком свежим и не старше max_age_hours.

        Возвращает True если возраст заказа в допустимом диапазоне:
        [MIN_ORDER_AGE_SECONDS .. max_age_hours * 3600].
        """
        last_update = order.get("last_update_date")
        if not last_update:
            logger.debug(
                "Заказ %s: нет last_update_date, пропускаем проверку возраста",
                order.get("external_id", "?"),
            )
            return True

        try:
            if isinstance(last_update, str):
                # Поддержка ISO-формата с/без timezone
                dt = datetime.fromisoformat(last_update)
            elif isinstance(last_update, (int, float)):
                dt = datetime.fromtimestamp(last_update, tz=timezone.utc)
            else:
                return True

            # Приводим к UTC для сравнения
            now = datetime.now(tz=timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            age_seconds = (now - dt).total_seconds()
        except (ValueError, TypeError, OSError):
            logger.warning(
                "Заказ %s: не удалось распарсить дату '%s'",
                order.get("external_id", "?"),
                last_update,
            )
            return True

        if age_seconds < MIN_ORDER_AGE_SECONDS:
            logger.debug(
                "Заказ %s слишком свежий (%.0f сек), пропускаем",
                order.get("external_id", "?"),
                age_seconds,
            )
            return False

        max_age_seconds = self.max_age_hours * 3600
        if age_seconds > max_age_seconds:
            logger.debug(
                "Заказ %s слишком старый (%.1f ч), пропускаем",
                order.get("external_id", "?"),
                age_seconds / 3600,
            )
            return False

        return True

    def _contains_stop_words(self, order: dict) -> bool:
        """Проверить, содержит ли текст заказа стоп-слова."""
        text_parts: list[str] = []
        for field in ("title", "description", "subject", "type"):
            value = order.get(field)
            if value:
                text_parts.append(str(value))

        combined = " ".join(text_parts).lower()

        for word in self.stop_words:
            if word in combined:
                logger.debug(
                    "Заказ %s содержит стоп-слово '%s'",
                    order.get("external_id", "?"),
                    word,
                )
                return True

        return False
