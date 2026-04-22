"""Фильтры для заказов с Профи.ру (и универсальные для всех платформ)."""

import logging
import re
from datetime import datetime, timezone

from src.core.config import Settings

logger = logging.getLogger(__name__)

# Минимальный возраст заказа в секундах (слишком свежие пропускаем).
MIN_ORDER_AGE_SECONDS = 70

# Регексп для извлечения первого числа из строки бюджета.
_BUDGET_RE = re.compile(r"(\d[\d\s\u00a0]*)", re.IGNORECASE)


def _parse_budget(s: str | None) -> int | None:
    """Парсит строку бюджета в целое число (рублей). None если не удалось.

    Примеры:
        '5 000 ₽'     -> 5000
        'от 5000 ₽'   -> 5000  (берём первое число)
        '5000-10000'  -> 5000
        'договорная'  -> None
        'бесплатно'   -> None
        '' / None     -> None
    """
    if not s or not isinstance(s, str):
        return None
    low = s.lower()
    if "договор" in low or "бесплат" in low or "free" in low:
        return None
    m = _BUDGET_RE.search(s)
    if not m:
        return None
    digits = m.group(1).replace(" ", "").replace("\u00a0", "")
    try:
        return int(digits)
    except ValueError:
        return None


class ProfiruFilters:
    """Фильтрация заказов по стоп-словам, возрасту, platform config.

    Используется и для Profi.ru и для Kwork (общие поля: стоп-слова, возраст).
    Platform-specific проверки вынесены в `platform_check()` / `kwork_check()`.
    """

    def __init__(self, settings: Settings) -> None:
        self.stop_words: list[str] = [w.lower() for w in settings.stop_words]
        self.max_age_hours: int = settings.time_threshold_hours
        # Хранилища конфигов (None = конфиг не загружен, используем дефолты)
        self._profiru_config = None  # PlatformConfig | None
        self._kwork_config = None    # PlatformConfig | None

    # ------------------------------------------------------------------
    # Публичный API — обновление конфигов
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

    def apply_config(self, platform: str, config) -> None:
        """Применить глобальный PlatformConfig для заданной платформы.

        Вызывается в начале каждой итерации парсера (аналогично refresh_stop_words).
        Override общих фильтров (max_age_hours / min_age_seconds) если задан в конфиге.

        Args:
            platform: "profiru" или "kwork".
            config: PlatformConfig instance.
        """
        if platform == "profiru":
            self._profiru_config = config
            if config.max_age_hours is not None:
                self.max_age_hours = config.max_age_hours
                logger.debug(
                    "Profi.ru: max_age_hours переопределён из platform_config: %d",
                    config.max_age_hours,
                )
        elif platform == "kwork":
            self._kwork_config = config
            if config.max_age_hours is not None:
                self.max_age_hours = config.max_age_hours
                logger.debug(
                    "Kwork: max_age_hours переопределён из platform_config: %d",
                    config.max_age_hours,
                )
        else:
            logger.warning("apply_config: неизвестная платформа '%s'", platform)

    # ------------------------------------------------------------------
    # Публичный API — основная фильтрация
    # ------------------------------------------------------------------

    def is_acceptable(self, order: dict) -> bool:
        """Проверить заказ на соответствие базовым фильтрам (external_id, возраст, стоп-слова).

        Returns:
            True если заказ проходит базовую фильтрацию.
        """
        if not self._has_external_id(order):
            logger.debug("Заказ отклонён: отсутствует external_id")
            return False

        if not self._check_age(order):
            return False

        if self._contains_stop_words(order):
            return False

        return True

    def platform_check(self, order: dict) -> tuple[bool, str]:
        """Profi.ru-специфичные проверки по platform config.

        Вызывается ДВАЖДЫ:
        - pre-filter (до _fetch_order_details): без response_price
        - post-filter (после _fetch_order_details): полный набор вкл. response_price

        Returns:
            (accepted, reason) — accepted=True если заказ прошёл.
        """
        config = self._profiru_config
        if config is None:
            return True, ""

        # 1. Платформа отключена
        if not config.enabled:
            return False, "Платформа Profi.ru выключена"

        # 2. Фильтр по бюджету (min_budget / max_budget / include_no_budget)
        budget_result = self._check_budget(order, config)
        if budget_result is not None:
            return budget_result

        # 3. Цена отклика (только если уже обогащено)
        response_price = order.get("response_price")
        if (
            response_price is not None
            and config.profiru.max_response_price is not None
            and response_price > config.profiru.max_response_price
        ):
            return (
                False,
                f"Цена отклика {response_price}₽ > лимита {config.profiru.max_response_price}₽",
            )

        # 4. Только удалённая работа
        if config.profiru.only_remote:
            work_format = order.get("work_format", "") or ""
            if "дистанц" not in work_format.lower() and "удалён" not in work_format.lower():
                return False, f"Требуется дистанционная работа, формат: '{work_format}'"

        # 5. Исключить бесплатные заказы
        if config.profiru.exclude_free:
            budget_val = _parse_budget(order.get("budget"))
            raw_budget = (order.get("budget") or "").lower()
            if budget_val == 0 or "бесплат" in raw_budget or "free" in raw_budget:
                return False, "Бесплатный заказ исключён"

        return True, ""

    def kwork_check(self, order: dict) -> tuple[bool, str]:
        """Kwork-специфичные проверки по platform config.

        Returns:
            (accepted, reason) — accepted=True если заказ прошёл.
        """
        config = self._kwork_config
        if config is None:
            return True, ""

        # 1. Платформа отключена
        if not config.enabled:
            return False, "Платформа Kwork выключена"

        # 2. Фильтр по бюджету
        budget_result = self._check_budget(order, config)
        if budget_result is not None:
            return budget_result

        kwork_cfg = config.kwork

        # 3. Фильтр по категориям (если список непустой)
        # Проект проходит если его подкатегория ИЛИ её корневая категория
        # есть в списке. Это позволяет указать "11" (корневая "Разработка и IT")
        # и пропускать все её подкатегории (41, 81, 255, …).
        if kwork_cfg.category_ids:
            cat_id = order.get("kwork_category_id")
            parent_id = order.get("kwork_parent_category_id")
            allowed = set(kwork_cfg.category_ids)
            in_sub = cat_id is not None and cat_id in allowed
            in_parent = parent_id is not None and parent_id in allowed
            if not (in_sub or in_parent):
                return (
                    False,
                    f"Категория {cat_id} (parent={parent_id}) не в списке {kwork_cfg.category_ids}",
                )

        # 4. Мин. % найма клиента
        if kwork_cfg.min_hired_percent is not None:
            hired_pct = order.get("kwork_hired_pct")
            if hired_pct is not None and hired_pct < kwork_cfg.min_hired_percent:
                return (
                    False,
                    f"hired_pct={hired_pct} < min_hired_percent={kwork_cfg.min_hired_percent}",
                )

        # 5. Диапазон откликов
        offers = order.get("kwork_offers")
        if offers is not None:
            if kwork_cfg.min_offers is not None and offers < kwork_cfg.min_offers:
                return False, f"Откликов {offers} < min_offers={kwork_cfg.min_offers}"
            if kwork_cfg.max_offers is not None and offers > kwork_cfg.max_offers:
                return False, f"Откликов {offers} > max_offers={kwork_cfg.max_offers}"

        # 6. Исключить заказы с обязательным портфолио
        if kwork_cfg.exclude_portfolio_required:
            if order.get("kwork_user_need_portfolio"):
                return False, "Требуется портфолио (excluded)"

        # 7. Мин. оставшееся время
        if kwork_cfg.min_time_left_hours is not None:
            time_left = order.get("kwork_time_left_hours")
            if time_left is not None and time_left < kwork_cfg.min_time_left_hours:
                return (
                    False,
                    f"Осталось {time_left}ч < min_time_left_hours={kwork_cfg.min_time_left_hours}",
                )

        return True, ""

    # ------------------------------------------------------------------
    # Приватные — базовые проверки
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
        for field in ("title", "description", "subject", "type", "raw_text"):
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

    def _check_budget(
        self, order: dict, config
    ) -> tuple[bool, str] | None:
        """Общая проверка бюджета (min_budget / max_budget / include_no_budget).

        Returns:
            None — бюджет-фильтр пройден (продолжаем).
            (False, reason) — заказ отклонён.
            (True, "") — явно принят (бюджет отсутствует и include_no_budget=True).
        """
        min_b = config.min_budget
        max_b = config.max_budget
        include_no = config.include_no_budget

        if min_b is None and max_b is None:
            # Бюджет-фильтр не задан — пропускаем всё
            return None

        budget_val = _parse_budget(order.get("budget"))

        if budget_val is None:
            # Бюджет не указан (договорная / None)
            if not include_no:
                return False, "Бюджет не указан (include_no_budget=False)"
            # include_no_budget=True — пропускаем бюджет-фильтр для этого заказа
            return None

        if min_b is not None and budget_val < min_b:
            return False, f"Бюджет {budget_val}₽ < min_budget={min_b}₽"
        if max_b is not None and budget_val > max_b:
            return False, f"Бюджет {budget_val}₽ > max_budget={max_b}₽"

        return None
