"""Парсер Kwork — получение новых заявок (projects / WantWorker) через pykwork.

Использует официальный mobile API через библиотеку `kwork` (pykwork 0.2.0+).
Авторизация: login + password (+ опционально phone_last, proxy).
Токен сохраняется в settings таблице БД через SettingsService.

NB: у Kwork projects НЕТ поля attachments/materials — пропускаем v2.3 пайплайн.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.core.config import Settings
from src.parser.base import BaseParser
from src.parser.kwork.rate_limiter import InProcessRateLimiter
from src.parser.profiru.filters import ProfiruFilters  # универсальный по полям

logger = logging.getLogger(__name__)

# Флаг-индикатор: поймана ли капча (error_code == 118). При True парсер
# временно останавливается до ручного разблока.
CAPTCHA_ERROR_CODE = 118


class KworkParser(BaseParser):
    """Парсер Kwork через pykwork.

    Lazy-init клиента: `ensure_client()` на первом вызове fetch_orders.
    Токен кэшируется в DB через SettingsService (key `kwork_token`).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None
        self._rate_limiter = InProcessRateLimiter(
            rps=settings.kwork_rps, burst=settings.kwork_burst,
        )
        # Используем те же фильтры что и Profi.ru — стоп-слова универсальны,
        # возраст работает по `last_update_date` (унифицируем через нормализацию).
        self.filters = ProfiruFilters(settings)
        self._token: str | None = None
        self._captcha_detected: bool = False
        # Категории для фильтра (CSV → list[int])
        try:
            self._categories: list[int] = [
                int(x) for x in settings.kwork_categories.split(",") if x.strip()
            ]
        except ValueError:
            logger.warning(
                "Некорректный KWORK_CATEGORIES=%r, используем [11]",
                settings.kwork_categories,
            )
            self._categories = [11]

    # ------------------------------------------------------------------
    # BaseParser API
    # ------------------------------------------------------------------

    async def fetch_orders(self) -> list[dict]:
        """Получить актуальный список проектов из Kwork API."""
        if self._captcha_detected:
            logger.warning("Kwork parser заблокирован капчей — пропуск итерации")
            return []

        client = await self._ensure_client()

        try:
            await self._rate_limiter.acquire()
            projects = await client.get_projects(
                categories_ids=self._categories,
                page=1,
            )
        except Exception as exc:
            if await self._handle_kwork_exception(exc):
                return []
            raise

        logger.info("Kwork: получено %d проектов (категории %s)",
                    len(projects), self._categories)

        cutoff = int(time.time()) - self._settings.time_threshold_hours * 3600
        normalized: list[dict] = []
        for project in projects:
            try:
                normalized.append(self._normalize(project, cutoff))
            except Exception:
                logger.exception("Ошибка нормализации проекта Kwork")
                continue

        return [n for n in normalized if n is not None]

    def filter_order(self, order: dict) -> bool:
        return self.filters.is_acceptable(order)

    async def close(self) -> None:
        """Закрыть клиент pykwork."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.debug("Ошибка закрытия Kwork client", exc_info=True)
            self._client = None

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------

    async def _ensure_client(self):
        """Lazy-init клиента pykwork с восстановлением токена из БД."""
        if self._client is not None:
            return self._client

        try:
            from kwork import Kwork
        except ImportError as exc:
            raise RuntimeError(
                "Библиотека pykwork не установлена. "
                "Выполни: pip install 'kwork[proxy]>=0.2.0'"
            ) from exc

        if not self._settings.kwork_login or not self._settings.kwork_password:
            raise RuntimeError(
                "KWORK_LOGIN/KWORK_PASSWORD не заданы в .env"
            )

        # Восстанавливаем токен из БД (если есть)
        cached_token = await self._load_cached_token()

        kwargs: dict[str, Any] = {
            "login": self._settings.kwork_login,
            "password": self._settings.kwork_password,
            "timeout": 30.0,
            "retry_max_attempts": 3,
            "relogin_on_auth_error": True,
        }
        if self._settings.kwork_phone_last:
            kwargs["phone_last"] = self._settings.kwork_phone_last
        if self._settings.kwork_proxy:
            kwargs["proxy"] = self._settings.kwork_proxy

        client = Kwork(**kwargs)
        if cached_token:
            # pykwork хранит токен в self._token, подставляем вручную
            client._token = cached_token
            logger.info("Kwork: токен восстановлен из БД")

        self._client = client
        return client

    async def _load_cached_token(self) -> str | None:
        """Прочитать кэшированный токен Kwork из settings таблицы."""
        from src.core.database import create_engine, create_session_factory
        from src.core.settings_service import get_setting

        engine = create_engine(self._settings)
        session_factory = create_session_factory(engine)
        try:
            async with session_factory() as session:
                token = await get_setting(session, "kwork_token")
                return token or None
        except Exception:
            logger.debug("Не удалось прочитать kwork_token из БД", exc_info=True)
            return None
        finally:
            await engine.dispose()

    async def _save_cached_token(self, token: str) -> None:
        """Сохранить токен Kwork в settings таблицу."""
        if not token:
            return
        from src.core.database import create_engine, create_session_factory
        from src.core.settings_service import set_setting

        engine = create_engine(self._settings)
        session_factory = create_session_factory(engine)
        try:
            async with session_factory() as session:
                await set_setting(session, "kwork_token", token)
                logger.info("Kwork: токен сохранён в БД")
        except Exception:
            logger.exception("Не удалось сохранить kwork_token в БД")
        finally:
            await engine.dispose()

    async def _handle_kwork_exception(self, exc: Exception) -> bool:
        """Обработать исключения pykwork. Возвращает True если ошибка обработана."""
        msg = str(exc)
        lower = msg.lower()
        # Captcha: в pykwork это KworkHTTPException с error_code == 118
        error_code = getattr(exc, "error_code", None)
        if error_code == CAPTCHA_ERROR_CODE or "captcha" in lower:
            self._captcha_detected = True
            logger.critical(
                "Kwork: поймана капча (error_code=%s) — парсер приостановлен, "
                "требуется ручная разблокировка",
                error_code,
            )
            return True

        # 403 IP-blocked
        status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if status == 403:
            logger.error("Kwork: 403 (IP блокирован?) — пауза 5 мин")
            await asyncio.sleep(300)
            return True

        return False

    # ------------------------------------------------------------------
    # Нормализация
    # ------------------------------------------------------------------

    def _normalize(self, project: Any, cutoff: int) -> dict | None:
        """Преобразовать WantWorker Pydantic-модель в унифицированный dict."""
        external_id = str(getattr(project, "id", "") or "")
        if not external_id:
            return None

        title = getattr(project, "title", "") or ""
        description = getattr(project, "description", "") or ""
        price = getattr(project, "price", None)
        date_confirm = getattr(project, "date_confirm", None)
        offers = getattr(project, "offers", None)
        time_left = getattr(project, "time_left", None)
        hired_pct = getattr(project, "user_hired_percent", None)

        # Проверка возраста: Kwork отдаёт date_confirm как Unix ts.
        if date_confirm is not None and date_confirm < cutoff:
            # Слишком старый — ProfiruFilters._check_age тоже это проверит,
            # но здесь отсекаем раньше чтобы не нормализовать зря.
            return None

        budget = f"{price}₽" if price else None
        raw_parts = [title, "", description]
        if budget:
            raw_parts.append(f"\nБюджет: {budget}")
        if offers is not None:
            raw_parts.append(f"Откликов: {offers}")
        if time_left is not None:
            raw_parts.append(f"Осталось часов: {time_left}")
        if hired_pct is not None:
            raw_parts.append(f"Процент найма клиента: {hired_pct}%")

        return {
            "platform": "kwork",
            "external_id": external_id,
            "url": f"https://kwork.ru/projects/{external_id}",
            "title": title,
            "description": description,
            "budget": budget,
            "location": None,
            "work_format": "Удалённо",
            "last_update_date": date_confirm,  # unix ts — ProfiruFilters._check_age поймёт
            "is_fresh": False,
            "client_name": getattr(project, "username", "") or "",
            "schedule": None,
            "response_price": None,
            "materials": None,
            "raw_text": "\n".join(raw_parts),
            # Kwork-специфичные поля (для аналитики)
            "kwork_offers": offers,
            "kwork_time_left_hours": time_left,
            "kwork_hired_pct": hired_pct,
            "kwork_category_id": getattr(project, "category_id", None),
            "kwork_parent_category_id": getattr(project, "parent_category_id", None),
        }
