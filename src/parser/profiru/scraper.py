"""Парсер заказов с Профи.ру через GraphQL API.

Адаптировано из https://github.com/dobrozor/parser_profiru

Фаза 1 Resilience:
- Единый User-Agent для Selenium и httpx
- Паузы 1-3с между запросами getOrder
- Авторизация вынесена в отдельный метод для TokenManager
- Обработка 429 (rate limit)
"""

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from src.core.config import Settings
from src.parser.base import BaseParser
from src.parser.profiru.filters import ProfiruFilters

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://rnd.profi.ru/graphql"

# Единый User-Agent для Selenium и httpx
UNIFIED_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Заголовки API — из оригинала dobrozor/parser_profiru
API_HEADERS = {
    "origin": "https://rnd.profi.ru",
    "referer": "https://rnd.profi.ru/backoffice/n.php",
    "user-agent": UNIFIED_USER_AGENT,
    "x-app-id": "BO",
    "x-new-auth-compatible": "1",
    "content-type": "application/json",
}

# GraphQL запрос — из оригинала dobrozor/parser_profiru
ORDERS_QUERY = (
    "#prfrtkn:webbo:36bb338fde61287ba8723d0687db52f33ab381d8"
    ":9b53a063284429f629f81506c40339c13822dd22\n\n"
    "      query BoSearchBoardItems("
    "$filter: BoSearchFrontFiltersInput!, "
    "$useSavedFilter: Boolean, "
    "$allVerticals: Boolean, "
    "$searchQuery: String, "
    "$searchEntities: [BoSearchEntityInput!], "
    "$searchId: ID, "
    "$nextCursor: String, "
    "$pageSize: Int, "
    "$boSortUp: Int, "
    "$minScore: Float, "
    "$coordinates: BoSearchAreaInput, "
    "$clusterId: ID, "
    "$sort: BoSearchSortEnum"
    ") @domain(domains: [BO_BOARD, BO_BOARD_LIST]) {\n"
    "  boSearchBoardItems(\n"
    "    filter: $filter\n"
    "    useSavedFilter: $useSavedFilter\n"
    "    allVerticals: $allVerticals\n"
    "    searchQuery: $searchQuery\n"
    "    searchEntities: $searchEntities\n"
    "    searchId: $searchId\n"
    "    nextCursor: $nextCursor\n"
    "    pageSize: $pageSize\n"
    "    boSortUp: $boSortUp\n"
    "    minScore: $minScore\n"
    "    coordinates: $coordinates\n"
    "    clusterId: $clusterId\n"
    "    sort: $sort\n"
    "  ) {\n"
    "    nextCursor\n"
    "    serverTs\n"
    "    totalCount\n"
    "    items {\n"
    "      id\n"
    "      type\n"
    "      ... on BoSearchSnippet {\n"
    "        title\n"
    "        description\n"
    "        isReposted\n"
    "        lastUpdateDate\n"
    "        geo {\n"
    "          orderLocation {\n"
    "            address\n"
    "            geoplaces {\n"
    "              name\n"
    "              distance\n"
    "            }\n"
    "          }\n"
    "          remote {\n"
    "            address\n"
    "          }\n"
    "        }\n"
    "        price {\n"
    "          prefix\n"
    "          suffix\n"
    "          value\n"
    "        }\n"
    "        isFresh\n"
    "        clientInfo {\n"
    "          name\n"
    "        }\n"
    "        schedule\n"
    "      }\n"
    "    }\n"
    "  }\n"
    "}"
)

ORDERS_VARIABLES = {
    "allVerticals": True,
    "searchQuery": "",
    "searchEntities": [],
    "pageSize": 20,
    "useSavedFilter": True,
    "sort": "DEFAULT",
    "filter": {},
}

# Кэш пути ChromeDriver (устанавливается один раз)
_chromedriver_path: str | None = None


def _get_chromedriver_path() -> str:
    """Получить путь к ChromeDriver (кэшируется после первой установки)."""
    global _chromedriver_path
    if _chromedriver_path is None:
        _chromedriver_path = ChromeDriverManager().install()
    return _chromedriver_path


class ProfiruParser(BaseParser):
    """Парсер Профи.ру: авторизация через Selenium, данные через GraphQL."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.filters = ProfiruFilters(settings)
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=30.0)
        self._request_delay_min = settings.parser_request_delay_min
        self._request_delay_max = settings.parser_request_delay_max
        self._session_cookies: dict[str, str] = {}  # все cookies сессии

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    def set_session_cookies(self, cookies: dict[str, str]) -> None:
        """Установить cookies сессии (из Selenium или из кэша)."""
        self._session_cookies = dict(cookies)

    async def fetch_orders(self, token: str | None = None) -> list[dict]:
        """Получить список заказов (обратная совместимость). Скрывает 401."""
        if not token:
            return []
        raw = await self._request_orders(token)
        if raw is None:
            return []
        return await self.process_raw_orders(raw, token)

    async def fetch_orders_raw(self, token: str) -> list[dict] | None:
        """Запросить заказы через GraphQL.

        Returns:
            Список сырых snippet-ов, пустой список при ошибке, None при 401.
        """
        return await self._request_orders(token)

    async def process_raw_orders(self, raw_orders: list[dict], token: str) -> list[dict]:
        """Нормализовать и обогатить сырые заказы ценами.

        Args:
            raw_orders: список сырых snippet-ов из GraphQL
            token: токен для запросов getOrder

        Returns:
            Список нормализованных заказов с ценами отклика.
        """
        normalized = [self._normalize(item) for item in raw_orders]

        # Обогащаем деталями с REST API (цена отклика + материалы, с паузами)
        for order in normalized:
            ext_id = order.get("external_id")
            if ext_id:
                details = await self._fetch_order_details(ext_id, token)
                if details.get("response_price") is not None:
                    order["response_price"] = details["response_price"]
                if details.get("materials"):
                    order["materials"] = details["materials"]
                # Пауза после HTTP-запроса (anti-ban)
                await self._random_delay()

        return normalized

    def filter_order(self, order: dict) -> bool:
        """Проверить заказ через ProfiruFilters."""
        return self.filters.is_acceptable(order)

    # ------------------------------------------------------------------
    # GraphQL
    # ------------------------------------------------------------------

    async def _request_orders(self, token: str) -> list[dict] | None:
        """Выполнить GraphQL-запрос к API Профи.ру.

        Returns:
            Список сырых snippet-элементов, None при 401, пустой список при ошибке.
        """
        payload = {
            "query": ORDERS_QUERY,
            "variables": ORDERS_VARIABLES,
        }

        # Отправляем все cookies сессии (как браузер), prfr_bo_tkn обязательно
        request_cookies = dict(self._session_cookies)
        request_cookies["prfr_bo_tkn"] = token

        try:
            response = await self._http.post(
                GRAPHQL_URL,
                json=payload,
                headers=API_HEADERS,
                cookies=request_cookies,
            )
        except httpx.HTTPError as exc:
            logger.error("Ошибка HTTP-запроса к Профи.ру: %s", exc)
            return []

        # Обновляем cookies сессии из Set-Cookie (как делает браузер)
        self._update_session_cookies(response)

        if response.status_code in (401, 403):
            logger.warning("Профи.ру вернул %d — сессия невалидна", response.status_code)
            return None

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            logger.warning("Профи.ру вернул 429 (rate limit), Retry-After: %s", retry_after)
            return []

        if response.status_code != 200:
            logger.error(
                "Профи.ру вернул статус %d: %s",
                response.status_code,
                response.text[:500],
            )
            return []

        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            logger.error("Невалидный JSON от Профи.ру")
            return []

        # Проверяем наличие ошибок GraphQL
        if "errors" in data:
            errors = data["errors"]
            for err in errors:
                msg = err.get("message", "").lower()
                if "unauthorized" in msg or "auth" in msg:
                    return None
            logger.error("GraphQL ошибки: %s", errors)
            return []

        items: list[dict] = (
            data.get("data", {}).get("boSearchBoardItems", {}).get("items") or []
        )

        # Оставляем только snippet-ы (реальные заказы)
        snippets = [item for item in items if item.get("type") == "SNIPPET"]

        logger.info("Получено %d заказов (из %d элементов) из GraphQL API", len(snippets), len(items))
        return snippets

    # ------------------------------------------------------------------
    # Keep-alive (поддержание сессии между итерациями)
    # ------------------------------------------------------------------

    async def keep_alive(self, token: str) -> bool:
        """Лёгкий GraphQL запрос для поддержания сессии на rnd.profi.ru.

        Отправляет минимальный запрос (pageSize=1) + проверяет ответ на
        HTTP 401 и GraphQL auth errors. Обновляет cookies из Set-Cookie.

        Returns:
            True если сессия жива, False при 401 (сессия истекла).
        """
        payload = {
            "query": ORDERS_QUERY,
            "variables": {**ORDERS_VARIABLES, "pageSize": 1},
        }

        request_cookies = dict(self._session_cookies)
        request_cookies["prfr_bo_tkn"] = token

        try:
            response = await self._http.post(
                GRAPHQL_URL,
                json=payload,
                headers=API_HEADERS,
                cookies=request_cookies,
            )
            self._update_session_cookies(response)

            if response.status_code == 401:
                logger.debug("Keep-alive: HTTP 401 — сессия истекла")
                return False

            if response.status_code != 200:
                logger.debug("Keep-alive: статус %d", response.status_code)
                return response.status_code != 429

            # Проверяем GraphQL-ошибки авторизации
            try:
                data = response.json()
                if "errors" in data:
                    for err in data["errors"]:
                        msg = err.get("message", "").lower()
                        if "unauthorized" in msg or "auth" in msg:
                            logger.debug("Keep-alive: GraphQL unauthorized — сессия истекла")
                            return False
            except (ValueError, KeyError):
                pass

            logger.debug("Keep-alive: сессия активна")
            return True

        except httpx.HTTPError as exc:
            logger.debug("Keep-alive: ошибка HTTP — %s", exc)
            return True

    # ------------------------------------------------------------------
    # Selenium авторизация (вызывается из TokenManager)
    # ------------------------------------------------------------------

    def authorize_selenium(self) -> dict[str, str]:
        """Авторизоваться на Профи.ру через headless Chrome и получить все cookies.

        Использует логин+пароль через форму авторизации.
        Единый User-Agent совпадает с httpx-запросами.

        Returns:
            Словарь всех cookies сессии (включая prfr_bo_tkn).

        Raises:
            RuntimeError: если не удалось получить токен.
        """
        logger.info("Запуск Selenium для авторизации на Профи.ру")

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Единый User-Agent (совпадает с httpx)
        options.add_argument(f"--user-agent={UNIFIED_USER_AGENT}")

        service = Service(_get_chromedriver_path())
        driver = webdriver.Chrome(service=service, options=options)
        driver.implicitly_wait(10)

        wait_timeout = 15
        page_load_timeout = 10

        try:
            logger.info("Переход на страницу авторизации")
            driver.get("https://profi.ru/backoffice/n.php")

            # 1. Ввод логина
            login_input = WebDriverWait(driver, wait_timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, '[data-testid="auth_login_input"]')
                )
            )
            login_input.send_keys(self.settings.profiru_login)
            logger.info("Логин введён")

            # 2. Ввод пароля
            password_input = WebDriverWait(driver, wait_timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'input[type="password"]')
                )
            )
            password_input.send_keys(self.settings.profiru_password)
            logger.info("Пароль введён")

            # 3. Клик по кнопке «Продолжить»
            login_button = WebDriverWait(driver, wait_timeout).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, '[data-testid="enter_with_sms_btn"]')
                )
            )
            login_button.click()
            logger.info("Кнопка авторизации нажата")

            # 4. Ожидание загрузки страницы заказов
            driver.set_page_load_timeout(page_load_timeout)
            try:
                WebDriverWait(driver, page_load_timeout).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, 'a[data-testid$="_order-snippet"]')
                    )
                )
            except TimeoutException:
                logger.debug("Таймаут загрузки страницы заказов (ожидаемо)")
            finally:
                driver.set_page_load_timeout(300)

            # 5. Проверка: не застряли ли на странице логина
            if "login-form" in driver.current_url:
                raise RuntimeError(
                    "Авторизация не удалась — проверьте логин/пароль"
                )

            # 6. Извлечение ВСЕХ cookies (как делает браузер)
            selenium_cookies = driver.get_cookies()
            cookies_dict = {c["name"]: c["value"] for c in selenium_cookies}

            if "prfr_bo_tkn" not in cookies_dict:
                raise RuntimeError("Cookie prfr_bo_tkn не найден после авторизации")

            logger.info(
                "Cookies получены: %d шт (prfr_bo_tkn длина: %d)",
                len(cookies_dict),
                len(cookies_dict["prfr_bo_tkn"]),
            )

            # 7. Прогрев сессии — имитация поведения пользователя
            self._warmup_session(driver)

            return cookies_dict

        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Ошибка авторизации на Профи.ру: {exc}") from exc
        finally:
            driver.quit()

    # ------------------------------------------------------------------
    # Session Warmup (после авторизации)
    # ------------------------------------------------------------------

    @staticmethod
    def _warmup_session(driver: webdriver.Chrome) -> None:
        """Прогрев сессии после авторизации — имитация поведения пользователя.

        После логина обычный пользователь не уходит сразу — он просматривает
        заказы, скроллит страницу, кликает. Это снижает риск детекции.
        """
        logger.info("Прогрев сессии после авторизации...")
        start = time.monotonic()

        try:
            # 1. Пауза — пользователь смотрит на страницу после логина
            time.sleep(random.uniform(2.0, 4.0))

            # 2. Плавный скролл вниз (3 шага)
            for i in range(1, 4):
                driver.execute_script(
                    f"window.scrollTo({{top: {i * 300}, behavior: 'smooth'}});"
                )
                time.sleep(random.uniform(0.8, 1.5))

            # 3. Скролл обратно наверх
            time.sleep(random.uniform(0.5, 1.0))
            driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
            time.sleep(random.uniform(1.0, 2.0))

            # 4. Попробовать кликнуть на первый заказ (если есть) и вернуться
            try:
                snippets = driver.find_elements(
                    By.CSS_SELECTOR, 'a[data-testid$="_order-snippet"]'
                )
                if snippets:
                    snippets[0].click()
                    time.sleep(random.uniform(2.0, 3.5))
                    driver.back()
                    time.sleep(random.uniform(1.5, 2.5))
            except Exception:
                # Не критично — просто пропускаем клик
                pass

        except Exception:
            logger.debug("Ошибка при прогреве сессии (не критично)")

        elapsed = time.monotonic() - start
        logger.info("Прогрев сессии завершён за %.1f сек", elapsed)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_session_cookies(self, response: httpx.Response) -> None:
        """Обновить cookies сессии из Set-Cookie ответа (как делает браузер).

        Браузер автоматически обновляет cookies при каждом ответе.
        Это поддерживает сессию живой и предотвращает 401.
        """
        for cookie_header in response.headers.get_list("set-cookie"):
            # Формат: name=value; path=/; ...
            name_value = cookie_header.split(";", 1)[0].strip()
            if "=" in name_value:
                name, value = name_value.split("=", 1)
                name = name.strip()
                value = value.strip()
                if name and value:
                    self._session_cookies[name] = value

    async def _random_delay(self) -> None:
        """Случайная пауза между запросами (anti-ban)."""
        delay = random.uniform(self._request_delay_min, self._request_delay_max)
        await asyncio.sleep(delay)

    @staticmethod
    def _resolve_geo_location(loc: dict | None) -> str:
        """Извлечь адрес или имя места из объекта локации."""
        if not loc:
            return ""
        address = loc.get("address", "")
        if address:
            return address
        places = loc.get("geoplaces") or []
        if places:
            return places[0].get("name", "")
        return ""

    # ------------------------------------------------------------------
    # Нормализация
    # ------------------------------------------------------------------

    @staticmethod
    def _format_price(price_data: dict | None) -> str:
        """Форматировать объект цены {prefix, suffix, value} в строку."""
        if not price_data:
            return ""
        prefix = price_data.get("prefix", "") or ""
        suffix = price_data.get("suffix", "") or ""
        value = price_data.get("value", "") or ""
        price_str = f"{prefix} {value} {suffix}".strip().replace("  ", " ")
        return price_str if price_str else ""

    @staticmethod
    def _extract_location(geo: dict | None) -> str:
        """Извлечь локацию из объекта geo."""
        if not geo:
            return ""
        for key in ("orderLocation", "remote"):
            result = ProfiruParser._resolve_geo_location(geo.get(key))
            if result:
                return result
        return ""

    @staticmethod
    def _format_work_format(geo: dict) -> str:
        """Определить формат работы: Дистанционно / На выезде / адрес."""
        if not geo:
            return ""
        if geo.get("remote"):
            return "Дистанционно"
        return ProfiruParser._resolve_geo_location(geo.get("orderLocation"))

    async def _fetch_order_details(self, external_id: str, token: str) -> dict:
        """Получить детали заказа через REST API Профи.ру.

        Вызывает метод getOrder REST API (/backoffice/api/) и извлекает:
        - full_view.price.price — цену отклика (с учётом скидки)
        - full_view.ofiles — прикреплённые изображения
        - full_view.ofiles_doc — прикреплённые документы

        Returns:
            Словарь с ключами: response_price (int|None), materials (list|None).
        """
        result: dict = {"response_price": None, "materials": None}

        request_data = {
            "meta": {
                "method": "getOrder",
                "ui_type": "WEB",
                "ui_app": "BO",
                "ui_ver": "1",
                "ui_os": "0.0",
            },
            "data": {"order_id": external_id},
        }
        try:
            rest_cookies = dict(self._session_cookies)
            rest_cookies["prfr_bo_tkn"] = token
            resp = await self._http.post(
                "https://profi.ru/backoffice/api/",
                data={"request": json.dumps(request_data)},
                headers={"accept": "application/json", "user-agent": UNIFIED_USER_AGENT},
                cookies=rest_cookies,
            )
            # Обновляем cookies сессии из Set-Cookie (как браузер)
            self._update_session_cookies(resp)
            if resp.status_code == 429:
                logger.warning("Rate limit на getOrder для заказа %s", external_id)
                return result
            if resp.status_code != 200:
                return result

            body = resp.json()
            full_view = (
                body.get("data", {})
                .get("order", {})
                .get("full_view", {})
            )

            # Цена отклика
            price_info = full_view.get("price", {})
            price = price_info.get("price")
            if price is not None:
                result["response_price"] = int(price)

            # Материалы: изображения (ofiles) + документы (ofiles_doc)
            materials: list[dict] = []

            for item in full_view.get("ofiles") or []:
                src = item.get("src", "")
                if src:
                    url = f"https://{src}" if not src.startswith("http") else src
                    preview = item.get("preview", "")
                    if preview and not preview.startswith("http"):
                        preview = f"https://{preview}"
                    materials.append({
                        "type": "image",
                        "url": url,
                        "preview": preview,
                        "name": src.rsplit("/", 1)[-1] if "/" in src else src,
                    })

            for item in full_view.get("ofiles_doc") or []:
                src = item.get("src", "")
                if src:
                    url = f"https://{src}" if not src.startswith("http") else src
                    materials.append({
                        "type": "file",
                        "url": url,
                        "name": item.get("name", src.rsplit("/", 1)[-1] if "/" in src else src),
                    })

            if materials:
                result["materials"] = materials
                logger.info(
                    "Заказ %s: найдено %d материалов (%d изобр., %d док.)",
                    external_id,
                    len(materials),
                    sum(1 for m in materials if m["type"] == "image"),
                    sum(1 for m in materials if m["type"] == "file"),
                )

            logger.info(
                "Детали заказа %s: цена отклика=%s, материалов=%d",
                external_id, result["response_price"], len(materials),
            )
        except Exception:
            logger.exception("Не удалось получить детали заказа %s", external_id)

        return result

    @staticmethod
    def _normalize(raw: dict) -> dict:
        """Преобразовать сырой snippet GraphQL в унифицированный формат."""
        geo = raw.get("geo") or {}
        title = raw.get("title", "")
        desc = raw.get("description", "") or ""
        budget = ProfiruParser._format_price(raw.get("price"))
        location = ProfiruParser._extract_location(geo)
        work_format = ProfiruParser._format_work_format(geo)
        client_name = (raw.get("clientInfo") or {}).get("name", "")
        schedule = raw.get("schedule", "") or ""

        # Собираем полный текст для AI — включая все метаданные
        raw_parts = [title, "", desc]
        if budget:
            raw_parts.append(f"\nБюджет: {budget}")
        if location:
            raw_parts.append(f"Локация: {location}")
        if work_format:
            raw_parts.append(f"Формат: {work_format}")
        if schedule:
            raw_parts.append(f"Даты: {schedule}")
        if client_name:
            raw_parts.append(f"Клиент: {client_name}")

        return {
            "external_id": str(raw.get("id", "")),
            "title": title,
            "description": desc,
            "budget": budget,
            "location": location,
            "work_format": work_format,
            "last_update_date": raw.get("lastUpdateDate"),
            "is_fresh": raw.get("isFresh", False),
            "client_name": client_name,
            "schedule": schedule,
            "response_price": None,
            "materials": None,
            "raw_text": "\n".join(raw_parts),
            "source": "profiru",
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._http.aclose()
