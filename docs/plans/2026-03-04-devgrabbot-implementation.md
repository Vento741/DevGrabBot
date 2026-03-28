# DevGrabBot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Telegram-бот с AI-ассистентом для автоматизации обработки фриланс-заявок с Профи.ру — парсинг, AI-анализ, ревью разработчиками, формирование отклика для менеджера.

**Architecture:** Модульная архитектура с Redis-очередью. Parser (Selenium + GraphQL API Профи.ру) → Redis → AI Engine (OpenRouter) → TG Bot (aiogram 3) → PostgreSQL. Деплой: VPS + venv + systemd.

**Tech Stack:** Python 3.11+, aiogram 3, SQLAlchemy 2 + asyncpg, Redis, Selenium, httpx, Alembic, Pydantic v2

**Design doc:** `docs/plans/2026-03-04-devgrabbot-design.md`

**Reference parser:** https://github.com/dobrozor/parser_profiru — использует GraphQL endpoint `rnd.profi.ru/graphql` после Selenium-авторизации для получения токена `prfr_bo_tkn`.

---

## Phase 1: Фундамент (core)

### Task 1: Инициализация проекта и зависимости

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`

**Step 1: Создать pyproject.toml**

```toml
[project]
name = "devgrabbot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "aiogram>=3.4,<4",
    "sqlalchemy[asyncio]>=2.0,<3",
    "asyncpg>=0.29",
    "redis[hiredis]>=5.0",
    "httpx>=0.27",
    "pydantic>=2.0,<3",
    "pydantic-settings>=2.0",
    "alembic>=1.13",
    "selenium>=4.15",
    "webdriver-manager>=4.0",
    "beautifulsoup4>=4.12",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.3"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Создать .env.example**

```env
# Telegram
BOT_TOKEN=your_bot_token
GROUP_CHAT_ID=-100xxxxxxxxxx

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/devgrabbot

# Redis
REDIS_URL=redis://localhost:6379/0

# OpenRouter
OPENROUTER_API_KEY=sk-or-xxxxx
OPENROUTER_MODEL=google/gemini-3.1-flash-lite-preview

# Profiru
PROFIRU_LOGIN=
PROFIRU_PASSWORD=

# Parser
PARSE_INTERVAL_SEC=300
TIME_THRESHOLD_HOURS=24
STOP_WORDS=["WordPress","Битрикс","Опрос"]
```

**Step 3: Создать .gitignore**

```
__pycache__/
*.pyc
.env
*.egg-info/
dist/
.venv/
venv/
.ruff_cache/
```

**Step 4: Инициализировать venv и установить зависимости**

Run: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`

---

### Task 2: Конфигурация (Pydantic Settings)

**Files:**
- Create: `src/__init__.py`
- Create: `src/core/__init__.py`
- Create: `src/core/config.py`
- Test: `tests/test_config.py`

**Step 1: Написать тест**

```python
# tests/test_config.py
import os
import pytest


def test_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.setenv("GROUP_CHAT_ID", "-100123")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "gpt-4o")
    monkeypatch.setenv("PROFIRU_LOGIN", "+71234567890")

    from src.core.config import Settings
    s = Settings()
    assert s.bot_token == "test_token"
    assert s.group_chat_id == -100123
    assert s.openrouter_model == "gpt-4o"


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("GROUP_CHAT_ID", "-1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("PROFIRU_LOGIN", "+7")

    from src.core.config import Settings
    s = Settings()
    assert s.parse_interval_sec == 300
    assert s.time_threshold_hours == 24
    assert s.openrouter_model == "google/gemini-3.1-flash-lite-preview"
```

**Step 2: Убедиться что тест падает**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.core.config'`

**Step 3: Реализовать**

```python
# src/__init__.py
# (пустой)

# src/core/__init__.py
# (пустой)

# src/core/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    bot_token: str
    group_chat_id: int

    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # OpenRouter
    openrouter_api_key: str
    openrouter_model: str = "anthropic/claude-sonnet-4"

    # Profiru
    profiru_login: str
    profiru_password: str = ""

    # Parser
    parse_interval_sec: int = 300
    time_threshold_hours: int = 24
    stop_words: list[str] = ["WordPress", "Битрикс", "Опрос"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

**Step 4: Убедиться что тесты проходят**

Run: `pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ tests/test_config.py
git commit -m "feat(core): конфигурация Pydantic Settings"
```

---

### Task 3: SQLAlchemy модели

**Files:**
- Create: `src/core/models.py`
- Test: `tests/test_models.py`

**Step 1: Написать тест**

```python
# tests/test_models.py
from src.core.models import Order, AiAnalysis, OrderAssignment, ManagerResponse, TeamMember


def test_order_model_has_required_fields():
    fields = {c.name for c in Order.__table__.columns}
    assert {"id", "external_id", "platform", "title", "description",
            "budget", "location", "deadline", "raw_text", "status",
            "created_at"}.issubset(fields)


def test_ai_analysis_model_has_required_fields():
    fields = {c.name for c in AiAnalysis.__table__.columns}
    assert {"id", "order_id", "summary", "stack", "price_min", "price_max",
            "timeline_days", "relevance_score", "complexity",
            "response_draft", "model_used", "created_at"}.issubset(fields)


def test_order_assignment_model_has_required_fields():
    fields = {c.name for c in OrderAssignment.__table__.columns}
    assert {"id", "order_id", "developer_id", "status", "price_final",
            "timeline_final", "stack_final", "custom_notes", "approved_at"}.issubset(fields)


def test_team_member_model_has_required_fields():
    fields = {c.name for c in TeamMember.__table__.columns}
    assert {"id", "tg_id", "tg_username", "name", "role", "is_active"}.issubset(fields)
```

**Step 2: Убедиться что тест падает**

Run: `pytest tests/test_models.py -v`
Expected: FAIL

**Step 3: Реализовать**

```python
# src/core/models.py
import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    new = "new"
    analyzing = "analyzing"
    reviewed = "reviewed"
    assigned = "assigned"
    completed = "completed"
    skipped = "skipped"


class AssignmentStatus(str, enum.Enum):
    pending = "pending"
    editing = "editing"
    approved = "approved"
    sent = "sent"


class TeamRole(str, enum.Enum):
    developer = "developer"
    manager = "manager"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(50), unique=True)
    platform: Mapped[str] = mapped_column(String(50), default="profiru")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.new,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    analyses: Mapped[list["AiAnalysis"]] = relationship(back_populates="order")
    assignments: Mapped[list["OrderAssignment"]] = relationship(back_populates="order")


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    summary: Mapped[str] = mapped_column(Text)
    stack: Mapped[list] = mapped_column(JSON, default=list)
    price_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeline_days: Mapped[str | None] = mapped_column(String(50), nullable=True)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    complexity: Mapped[str] = mapped_column(String(20), default="medium")
    response_draft: Mapped[str] = mapped_column(Text, default="")
    model_used: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    order: Mapped["Order"] = relationship(back_populates="analyses")


class OrderAssignment(Base):
    __tablename__ = "order_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    developer_id: Mapped[int] = mapped_column(ForeignKey("team_members.id"))
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus), default=AssignmentStatus.pending,
    )
    price_final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeline_final: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stack_final: Mapped[list | None] = mapped_column(JSON, nullable=True)
    custom_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="assignments")
    developer: Mapped["TeamMember"] = relationship(back_populates="assignments")
    manager_response: Mapped["ManagerResponse | None"] = relationship(
        back_populates="assignment", uselist=False,
    )


class ManagerResponse(Base):
    __tablename__ = "manager_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("order_assignments.id"))
    response_text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    assignment: Mapped["OrderAssignment"] = relationship(back_populates="manager_response")


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    tg_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[TeamRole] = mapped_column(Enum(TeamRole))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    assignments: Mapped[list["OrderAssignment"]] = relationship(back_populates="developer")
```

**Step 4: Тесты проходят**

Run: `pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/models.py tests/test_models.py
git commit -m "feat(core): SQLAlchemy модели БД"
```

---

### Task 4: Database и Redis клиенты

**Files:**
- Create: `src/core/database.py`
- Create: `src/core/redis.py`

**Step 1: Реализовать database.py**

```python
# src/core/database.py
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import Settings


def create_engine(settings: Settings):
    return create_async_engine(settings.database_url, echo=False)


def create_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)
```

**Step 2: Реализовать redis.py**

```python
# src/core/redis.py
import json

import redis.asyncio as aioredis

from src.core.config import Settings

QUEUE_NEW_ORDERS = "devgrab:new_orders"
QUEUE_ANALYZED = "devgrab:analyzed"
SENT_ORDERS_SET = "devgrab:sent_order_ids"


class RedisClient:
    def __init__(self, settings: Settings):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def push_order(self, order_data: dict) -> None:
        await self.redis.rpush(QUEUE_NEW_ORDERS, json.dumps(order_data, ensure_ascii=False))

    async def pop_order(self) -> dict | None:
        data = await self.redis.lpop(QUEUE_NEW_ORDERS)
        return json.loads(data) if data else None

    async def push_analyzed(self, analysis_data: dict) -> None:
        await self.redis.rpush(QUEUE_ANALYZED, json.dumps(analysis_data, ensure_ascii=False))

    async def pop_analyzed(self) -> dict | None:
        data = await self.redis.lpop(QUEUE_ANALYZED)
        return json.loads(data) if data else None

    async def is_order_sent(self, external_id: str) -> bool:
        return await self.redis.sismember(SENT_ORDERS_SET, external_id)

    async def mark_order_sent(self, external_id: str) -> None:
        await self.redis.sadd(SENT_ORDERS_SET, external_id)

    async def close(self) -> None:
        await self.redis.aclose()
```

**Step 3: Commit**

```bash
git add src/core/database.py src/core/redis.py
git commit -m "feat(core): Database и Redis клиенты"
```

---

### Task 5: Alembic миграции

**Files:**
- Create: `alembic.ini`
- Create: `src/migrations/env.py`
- Create: `src/migrations/script.py.mako`
- Create: `src/migrations/versions/` (directory)

**Step 1: Инициализировать Alembic**

Run: `cd src && alembic init migrations`

**Step 2: Настроить env.py**

Отредактировать `src/migrations/env.py` — добавить импорт моделей и async-поддержку:
- `target_metadata = Base.metadata` из `src.core.models`
- `sqlalchemy.url` из `Settings().database_url`
- Async `run_migrations_online()` через `connectable = create_async_engine(...)`

**Step 3: Создать первую миграцию**

Run: `cd src && alembic revision --autogenerate -m "initial tables"`

**Step 4: Применить**

Run: `cd src && alembic upgrade head`

**Step 5: Commit**

```bash
git add alembic.ini src/migrations/
git commit -m "chore: настройка Alembic миграций"
```

---

## Phase 2: Парсер Профи.ру

### Task 6: Базовый класс парсера

**Files:**
- Create: `src/parser/__init__.py`
- Create: `src/parser/base.py`
- Test: `tests/test_parser_base.py`

**Step 1: Написать тест**

```python
# tests/test_parser_base.py
from src.parser.base import BaseParser


def test_base_parser_is_abstract():
    """BaseParser нельзя инстанцировать напрямую."""
    import pytest
    with pytest.raises(TypeError):
        BaseParser()


def test_base_parser_has_required_methods():
    assert hasattr(BaseParser, "fetch_orders")
    assert hasattr(BaseParser, "filter_order")
```

**Step 2: Убедиться что тест падает**

Run: `pytest tests/test_parser_base.py -v`
Expected: FAIL

**Step 3: Реализовать**

```python
# src/parser/__init__.py
# (пустой)

# src/parser/base.py
from abc import ABC, abstractmethod


class BaseParser(ABC):
    """Базовый класс для парсеров фриланс-площадок."""

    @abstractmethod
    async def fetch_orders(self) -> list[dict]:
        """Получить список заказов с площадки."""
        ...

    @abstractmethod
    def filter_order(self, order: dict) -> bool:
        """Проверить заказ на соответствие фильтрам. True = проходит."""
        ...
```

**Step 4: Тесты проходят**

Run: `pytest tests/test_parser_base.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/parser/ tests/test_parser_base.py
git commit -m "feat(parser): базовый класс парсера"
```

---

### Task 7: Парсер Профи.ру — авторизация и получение заказов

**Files:**
- Create: `src/parser/profiru/__init__.py`
- Create: `src/parser/profiru/scraper.py`
- Create: `src/parser/profiru/filters.py`

**Reference:** `parser_profiru/app.py` — метод `login()` для Selenium-авторизации и `_fetch_and_process_orders()` для GraphQL API.

**Step 1: Реализовать scraper.py**

```python
# src/parser/profiru/__init__.py
# (пустой)

# src/parser/profiru/scraper.py
"""
Парсер заказов с Профи.ру.

Авторизация: Selenium → получение токена prfr_bo_tkn.
Заказы: GraphQL API rnd.profi.ru/graphql.

Адаптировано из: https://github.com/dobrozor/parser_profiru
"""
import asyncio
import logging
import time

import httpx
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from src.core.config import Settings
from src.parser.base import BaseParser
from src.parser.profiru.filters import ProfiruFilters

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://rnd.profi.ru/graphql"
LOGIN_URL = "https://profi.ru/backoffice/n.php"
TOKEN_COOKIE = "prfr_bo_tkn"

GRAPHQL_QUERY = """
query OrdersList {
    ordersList {
        items {
            id
            type
            subject
            description
            price
            lastUpdateDate
            location {
                name
            }
        }
    }
}
"""


class ProfiruParser(BaseParser):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = settings.profiru_password
        self.filters = ProfiruFilters(settings)
        self._http = httpx.AsyncClient(timeout=30)

    async def authenticate(self) -> str:
        """Авторизация через Selenium, возвращает токен."""
        logger.info("Начинаю авторизацию на Профи.ру...")
        token = await asyncio.to_thread(self._selenium_login)
        self.token = token
        logger.info("Авторизация успешна, токен получен")
        return token

    def _selenium_login(self) -> str:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        try:
            driver.get(LOGIN_URL)
            wait = WebDriverWait(driver, 30)

            login_input = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, '[data-testid="auth_login_input"]')
                )
            )
            login_input.clear()
            login_input.send_keys(self.settings.profiru_login)

            sms_button = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, '[data-testid="auth_submit_button"]')
                )
            )
            sms_button.click()

            # Ждём ввода SMS-кода пользователем (до 120 секунд)
            wait120 = WebDriverWait(driver, 120)
            wait120.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'a[data-testid$="_order-snippet"]')
                )
            )

            token = None
            for cookie in driver.get_cookies():
                if cookie["name"] == TOKEN_COOKIE:
                    token = cookie["value"]
                    break

            if not token:
                raise RuntimeError("Не удалось получить токен авторизации")

            return token
        finally:
            driver.quit()

    async def fetch_orders(self) -> list[dict]:
        """Получить заказы через GraphQL API."""
        if not self.token:
            await self.authenticate()

        headers = {
            "Cookie": f"{TOKEN_COOKIE}={self.token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            response = await self._http.post(
                GRAPHQL_URL,
                json={"query": GRAPHQL_QUERY},
                headers=headers,
            )

            if response.status_code == 401:
                logger.warning("Токен истёк, переавторизация...")
                await self.authenticate()
                headers["Cookie"] = f"{TOKEN_COOKIE}={self.token}"
                response = await self._http.post(
                    GRAPHQL_URL,
                    json={"query": GRAPHQL_QUERY},
                    headers=headers,
                )

            response.raise_for_status()
            data = response.json()

            items = data.get("data", {}).get("ordersList", {}).get("items", [])
            orders = []
            for item in items:
                if item.get("type") != "SNIPPET":
                    continue
                orders.append({
                    "external_id": str(item["id"]),
                    "title": item.get("subject", ""),
                    "description": item.get("description", ""),
                    "budget": item.get("price", ""),
                    "location": (item.get("location") or {}).get("name", ""),
                    "last_update": item.get("lastUpdateDate", 0),
                    "raw_text": f"{item.get('subject', '')}\n\n{item.get('description', '')}",
                })

            return orders

        except httpx.HTTPError as e:
            logger.error(f"Ошибка при запросе заказов: {e}")
            return []

    def filter_order(self, order: dict) -> bool:
        return self.filters.is_valid(order)

    async def close(self):
        await self._http.aclose()
```

**Step 2: Реализовать filters.py**

```python
# src/parser/profiru/filters.py
"""Фильтры заказов Профи.ру."""
import time
import logging

from src.core.config import Settings

logger = logging.getLogger(__name__)

MIN_AGE_SECONDS = 70  # Пропускаем слишком свежие заказы


class ProfiruFilters:
    def __init__(self, settings: Settings):
        self.stop_words = [w.lower() for w in settings.stop_words]
        self.max_hours = settings.time_threshold_hours

    def is_valid(self, order: dict) -> bool:
        """Проверить заказ по всем фильтрам."""
        if not order.get("external_id"):
            return False

        # Стоп-слова
        text = f"{order.get('title', '')} {order.get('description', '')}".lower()
        for word in self.stop_words:
            if word in text:
                logger.debug(f"Заказ {order['external_id']} отфильтрован: стоп-слово '{word}'")
                return False

        # Возраст заказа
        last_update = order.get("last_update", 0)
        if last_update:
            age_sec = time.time() - last_update / 1000  # timestamp в мс
            if age_sec < MIN_AGE_SECONDS:
                return False
            if age_sec > self.max_hours * 3600:
                return False

        return True
```

**Step 3: Commit**

```bash
git add src/parser/profiru/
git commit -m "feat(parser): парсер Профи.ру (Selenium auth + GraphQL API)"
```

---

### Task 8: Воркер парсера

**Files:**
- Create: `src/parser/worker.py`

**Step 1: Реализовать**

```python
# src/parser/worker.py
"""Воркер: периодически парсит заказы и кладёт в Redis."""
import asyncio
import logging

from src.core.config import Settings
from src.core.redis import RedisClient
from src.parser.profiru.scraper import ProfiruParser

logger = logging.getLogger(__name__)


async def run_parser_worker(settings: Settings):
    redis = RedisClient(settings)
    parser = ProfiruParser(settings)

    logger.info(f"Parser worker запущен. Интервал: {settings.parse_interval_sec}с")

    try:
        while True:
            try:
                orders = await parser.fetch_orders()
                new_count = 0

                for order in orders:
                    if not parser.filter_order(order):
                        continue

                    if await redis.is_order_sent(order["external_id"]):
                        continue

                    await redis.push_order(order)
                    await redis.mark_order_sent(order["external_id"])
                    new_count += 1

                if new_count:
                    logger.info(f"Найдено {new_count} новых заказов")

            except Exception:
                logger.exception("Ошибка в цикле парсера")

            await asyncio.sleep(settings.parse_interval_sec)
    finally:
        await parser.close()
        await redis.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    asyncio.run(run_parser_worker(settings))
```

**Step 2: Commit**

```bash
git add src/parser/worker.py
git commit -m "feat(parser): воркер очереди парсинга"
```

---

## Phase 3: AI Engine

### Task 9: OpenRouter клиент

**Files:**
- Create: `src/ai/__init__.py`
- Create: `src/ai/openrouter.py`
- Test: `tests/test_openrouter.py`

**Step 1: Написать тест**

```python
# tests/test_openrouter.py
import pytest
from src.ai.openrouter import OpenRouterClient


def test_openrouter_client_init():
    client = OpenRouterClient(api_key="test", model="test-model")
    assert client.model == "test-model"


def test_openrouter_build_payload():
    client = OpenRouterClient(api_key="test", model="gpt-4o")
    payload = client._build_payload("system prompt", "user message")
    assert payload["model"] == "gpt-4o"
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
```

**Step 2: Убедиться что тест падает**

Run: `pytest tests/test_openrouter.py -v`
Expected: FAIL

**Step 3: Реализовать**

```python
# src/ai/__init__.py
# (пустой)

# src/ai/openrouter.py
"""Клиент OpenRouter API."""
import json
import logging

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self._http = httpx.AsyncClient(timeout=60)

    def _build_payload(self, system_prompt: str, user_message: str) -> dict:
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
        """Отправить запрос и вернуть JSON-ответ."""
        text = await self.complete(system_prompt, user_message)

        # Извлечь JSON из ответа (может быть обёрнут в ```json ... ```)
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])

        return json.loads(text)

    async def close(self):
        await self._http.aclose()
```

**Step 4: Тесты проходят**

Run: `pytest tests/test_openrouter.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ai/ tests/test_openrouter.py
git commit -m "feat(ai): OpenRouter API клиент"
```

---

### Task 10: AI-промпты

**Files:**
- Create: `src/ai/prompts/__init__.py`
- Create: `src/ai/prompts/analyze.py`
- Create: `src/ai/prompts/response.py`

**Step 1: Реализовать промпт анализа**

```python
# src/ai/prompts/__init__.py
# (пустой)

# src/ai/prompts/analyze.py
"""Промпт для анализа заявки."""

SYSTEM_PROMPT = """Ты — аналитик IT-команды из 2 senior fullstack-разработчиков.

Стек команды:
- Python, JavaScript/TypeScript
- React, Next.js
- Telegram-боты (aiogram)
- AI/ML интеграции (OpenRouter, OpenAI, LangChain)
- Парсеры (Selenium, BeautifulSoup, Playwright)
- PostgreSQL, Redis, MongoDB
- Docker, Linux, VPS

Команда НЕ берёт:
- WordPress, Битрикс, Tilda, конструкторы
- Вакансии на полный рабочий день
- Обучение / менторство
- Проекты без ТЗ с бюджетом до 10 000 руб.
- Опросы

Проанализируй заявку и верни JSON (только JSON, без обёрток):
{
  "summary": "Краткая выжимка (2-3 предложения)",
  "stack": ["технология1", "технология2"],
  "price_min": число_в_рублях,
  "price_max": число_в_рублях,
  "timeline_days": "X-Y",
  "relevance_score": число_0_100,
  "complexity": "low|medium|high",
  "response_draft": "Черновик предложения для отклика (3-5 предложений)"
}

relevance_score: 0 = совсем не подходит, 100 = идеально подходит.
Цену оценивай по рынку РФ для senior-разработчиков.
"""


def build_analyze_prompt(raw_text: str) -> str:
    return f"Заявка с фриланс-площадки:\n\n{raw_text}"
```

**Step 2: Реализовать промпт отклика**

```python
# src/ai/prompts/response.py
"""Промпт для формирования отклика менеджеру."""

SYSTEM_PROMPT = """Ты — копирайтер IT-команды. Формируй профессиональный отклик на заявку
для фриланс-площадки Профи.ру.

Правила:
- Стиль: конкретный, без воды, профессиональный
- Упоминай релевантный опыт команды
- Указывай сроки и стоимость
- Отклик должен быть готов к копированию и отправке на площадке
- Длина: 5-10 предложений
- Обращение на "вы"
- НЕ используй шаблонные фразы вроде "мы молодая динамичная команда"

Верни только текст отклика, без обёрток и пояснений.
"""


def build_response_prompt(
    summary: str,
    stack: list[str],
    price: int,
    timeline: str,
    custom_notes: str = "",
) -> str:
    parts = [
        f"Краткое описание заявки: {summary}",
        f"Стек: {', '.join(stack)}",
        f"Цена: {price} руб.",
        f"Сроки: {timeline} дней",
    ]
    if custom_notes:
        parts.append(f"Дополнительные пожелания разработчика: {custom_notes}")

    return "\n".join(parts)
```

**Step 3: Commit**

```bash
git add src/ai/prompts/
git commit -m "feat(ai): промпты анализа заявок и формирования откликов"
```

---

### Task 11: AI Analyzer — логика анализа заявок

**Files:**
- Create: `src/ai/analyzer.py`
- Test: `tests/test_analyzer.py`

**Step 1: Написать тест**

```python
# tests/test_analyzer.py
from unittest.mock import AsyncMock, patch
import pytest

from src.ai.analyzer import OrderAnalyzer


@pytest.mark.asyncio
async def test_analyze_order_calls_openrouter():
    mock_client = AsyncMock()
    mock_client.complete_json.return_value = {
        "summary": "TG-бот для визового центра",
        "stack": ["Python", "Selenium"],
        "price_min": 35000,
        "price_max": 50000,
        "timeline_days": "10-14",
        "relevance_score": 92,
        "complexity": "medium",
        "response_draft": "Предлагаем решение...",
    }

    analyzer = OrderAnalyzer(mock_client)
    result = await analyzer.analyze_order("Нужен TG-бот для визового центра...")

    assert result["relevance_score"] == 92
    assert "Python" in result["stack"]
    mock_client.complete_json.assert_called_once()


@pytest.mark.asyncio
async def test_generate_response_calls_openrouter():
    mock_client = AsyncMock()
    mock_client.complete.return_value = "Здравствуйте! Предлагаем..."

    analyzer = OrderAnalyzer(mock_client)
    result = await analyzer.generate_response(
        summary="TG-бот",
        stack=["Python"],
        price=40000,
        timeline="10-14",
    )

    assert result == "Здравствуйте! Предлагаем..."
    mock_client.complete.assert_called_once()
```

**Step 2: Убедиться что тест падает**

Run: `pytest tests/test_analyzer.py -v`
Expected: FAIL

**Step 3: Реализовать**

```python
# src/ai/analyzer.py
"""Анализатор заявок через AI."""
import logging

from src.ai.openrouter import OpenRouterClient
from src.ai.prompts.analyze import SYSTEM_PROMPT as ANALYZE_PROMPT, build_analyze_prompt
from src.ai.prompts.response import SYSTEM_PROMPT as RESPONSE_PROMPT, build_response_prompt

logger = logging.getLogger(__name__)


class OrderAnalyzer:
    def __init__(self, client: OpenRouterClient):
        self.client = client

    async def analyze_order(self, raw_text: str) -> dict:
        """Анализировать заявку, вернуть структурированный результат."""
        user_msg = build_analyze_prompt(raw_text)
        result = await self.client.complete_json(ANALYZE_PROMPT, user_msg)
        logger.info(
            f"AI-анализ: relevance={result.get('relevance_score')}, "
            f"price={result.get('price_min')}-{result.get('price_max')}"
        )
        return result

    async def generate_response(
        self,
        summary: str,
        stack: list[str],
        price: int,
        timeline: str,
        custom_notes: str = "",
    ) -> str:
        """Сгенерировать текст отклика для менеджера."""
        user_msg = build_response_prompt(summary, stack, price, timeline, custom_notes)
        return await self.client.complete(RESPONSE_PROMPT, user_msg)
```

**Step 4: Тесты проходят**

Run: `pytest tests/test_analyzer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ai/analyzer.py tests/test_analyzer.py
git commit -m "feat(ai): анализатор заявок (analyze + generate response)"
```

---

## Phase 4: Telegram Bot

### Task 12: Основа бота и /start

**Files:**
- Create: `src/bot/__init__.py`
- Create: `src/bot/bot.py`
- Create: `src/bot/handlers/__init__.py`
- Create: `src/bot/handlers/start.py`

**Step 1: Реализовать точку входа бота**

```python
# src/bot/__init__.py
# (пустой)

# src/bot/bot.py
"""Точка входа Telegram-бота."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.core.config import Settings
from src.bot.handlers import start, orders, review, manager

logger = logging.getLogger(__name__)


async def run_bot(settings: Settings):
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Регистрация роутеров
    dp.include_router(start.router)
    dp.include_router(orders.router)
    dp.include_router(review.router)
    dp.include_router(manager.router)

    # Передаём зависимости через workflow_data
    dp["settings"] = settings

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    asyncio.run(run_bot(settings))
```

**Step 2: Реализовать /start**

```python
# src/bot/handlers/__init__.py
# (пустой)

# src/bot/handlers/start.py
"""Хендлер /start — регистрация участника команды."""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "DevGrabBot — бот-ассистент для обработки фриланс-заявок.\n\n"
        "Я парсю заявки с Профи.ру, анализирую их через AI, "
        "и помогаю сформировать отклик.\n\n"
        "Заявки приходят в групповой чат. "
        "Нажмите «Взять» чтобы начать работу с заявкой."
    )
```

**Step 3: Commit**

```bash
git add src/bot/
git commit -m "feat(bot): основа бота и /start"
```

---

### Task 13: Клавиатуры (inline keyboards)

**Files:**
- Create: `src/bot/keyboards/__init__.py`
- Create: `src/bot/keyboards/orders.py`
- Create: `src/bot/keyboards/review.py`

**Step 1: Реализовать клавиатуры**

```python
# src/bot/keyboards/__init__.py
# (пустой)

# src/bot/keyboards/orders.py
"""Клавиатуры для заявок в групповом чате."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def order_actions_kb(order_id: int) -> InlineKeyboardMarkup:
    """Кнопки под заявкой в группе: Взять / Пропустить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Взять", callback_data=f"take:{order_id}"),
            InlineKeyboardButton(text="Пропустить", callback_data=f"skip:{order_id}"),
        ],
    ])


# src/bot/keyboards/review.py
"""Клавиатуры для ревью заявки в личке."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def review_actions_kb(assignment_id: int) -> InlineKeyboardMarkup:
    """Кнопки редактирования в личке разработчика."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Изменить цену", callback_data=f"edit_price:{assignment_id}",
            ),
            InlineKeyboardButton(
                text="Изменить сроки", callback_data=f"edit_timeline:{assignment_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Изменить стек", callback_data=f"edit_stack:{assignment_id}",
            ),
            InlineKeyboardButton(
                text="Своё сообщение", callback_data=f"edit_custom:{assignment_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Утвердить и отправить менеджеру",
                callback_data=f"approve:{assignment_id}",
            ),
        ],
    ])


def copy_response_kb(response_id: int) -> InlineKeyboardMarkup:
    """Кнопка копирования отклика для менеджера."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Скопировать отклик",
                callback_data=f"copy_response:{response_id}",
            ),
        ],
    ])
```

**Step 2: Commit**

```bash
git add src/bot/keyboards/
git commit -m "feat(bot): inline-клавиатуры заявок и ревью"
```

---

### Task 14: FSM-состояния для редактирования

**Files:**
- Create: `src/bot/states.py`

**Step 1: Реализовать**

```python
# src/bot/states.py
"""FSM-состояния для редактирования заявки."""
from aiogram.fsm.state import State, StatesGroup


class ReviewStates(StatesGroup):
    editing_price = State()
    editing_timeline = State()
    editing_stack = State()
    editing_custom = State()
```

**Step 2: Commit**

```bash
git add src/bot/states.py
git commit -m "feat(bot): FSM-состояния редактирования"
```

---

### Task 15: Хендлер заявок в групповом чате

**Files:**
- Create: `src/bot/handlers/orders.py`

**Step 1: Реализовать**

```python
# src/bot/handlers/orders.py
"""Хендлеры заявок в групповом чате."""
import logging

from aiogram import Bot, Router
from aiogram.types import CallbackQuery

from src.bot.keyboards.orders import order_actions_kb
from src.bot.keyboards.review import review_actions_kb

logger = logging.getLogger(__name__)
router = Router()


def format_order_message(analysis: dict, order: dict) -> str:
    """Форматировать сообщение о заявке для группового чата."""
    stack_str = ", ".join(analysis.get("stack", []))
    return (
        f"<b>Новая заявка #{order.get('external_id', '?')}</b>\n\n"
        f"<b>Выжимка:</b>\n{analysis.get('summary', 'Нет данных')}\n\n"
        f"<b>Стек:</b> {stack_str}\n"
        f"<b>Цена:</b> {analysis.get('price_min', '?')} - {analysis.get('price_max', '?')} руб.\n"
        f"<b>Сроки:</b> {analysis.get('timeline_days', '?')} дней\n"
        f"<b>Релевантность:</b> {analysis.get('relevance_score', 0)}%\n"
        f"<b>Сложность:</b> {analysis.get('complexity', '?')}"
    )


async def send_order_to_group(bot: Bot, chat_id: int, order_id: int, analysis: dict, order: dict):
    """Отправить заявку в групповой чат с кнопками."""
    text = format_order_message(analysis, order)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=order_actions_kb(order_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("take:"))
async def on_take_order(callback: CallbackQuery):
    """Разработчик берёт заявку."""
    order_id = int(callback.data.split(":")[1])

    # TODO: сохранить assignment в БД, получить analysis
    # Пока заглушка — отправляем в личку
    await callback.answer("Заявка ваша! Подробности в личных сообщениях.")

    await callback.message.edit_text(
        callback.message.text + f"\n\n<b>Взял:</b> @{callback.from_user.username}",
        reply_markup=None,
    )

    # Отправить в личку разработчику
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=(
            f"<b>Заявка #{order_id} — ваша!</b>\n\n"
            "AI-анализ загружается...\n\n"
            "Используйте кнопки ниже для редактирования."
        ),
        reply_markup=review_actions_kb(order_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("skip:"))
async def on_skip_order(callback: CallbackQuery):
    """Разработчик пропускает заявку."""
    await callback.answer("Пропущено")
```

**Step 2: Commit**

```bash
git add src/bot/handlers/orders.py
git commit -m "feat(bot): хендлер заявок в групповом чате"
```

---

### Task 16: Хендлер ревью (личка разработчика)

**Files:**
- Create: `src/bot/handlers/review.py`

**Step 1: Реализовать**

```python
# src/bot/handlers/review.py
"""Хендлеры ревью заявки в личке разработчика."""
import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.review import review_actions_kb
from src.bot.states import ReviewStates

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(lambda c: c.data and c.data.startswith("edit_price:"))
async def on_edit_price(callback: CallbackQuery, state: FSMContext):
    assignment_id = int(callback.data.split(":")[1])
    await state.set_state(ReviewStates.editing_price)
    await state.update_data(assignment_id=assignment_id)
    await callback.answer()
    await callback.message.answer("Введите новую цену (число в рублях):")


@router.message(ReviewStates.editing_price)
async def process_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.answer("Пожалуйста, введите число. Например: 45000")
        return

    data = await state.get_data()
    assignment_id = data["assignment_id"]
    await state.clear()

    # TODO: обновить assignment в БД, перегенерировать через AI
    await message.answer(
        f"Цена обновлена: {price} руб.\n\n"
        "AI перегенерирует предложение...",
        reply_markup=review_actions_kb(assignment_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("edit_timeline:"))
async def on_edit_timeline(callback: CallbackQuery, state: FSMContext):
    assignment_id = int(callback.data.split(":")[1])
    await state.set_state(ReviewStates.editing_timeline)
    await state.update_data(assignment_id=assignment_id)
    await callback.answer()
    await callback.message.answer("Введите новые сроки (например: 10-14 дней):")


@router.message(ReviewStates.editing_timeline)
async def process_timeline(message: Message, state: FSMContext):
    data = await state.get_data()
    assignment_id = data["assignment_id"]
    await state.clear()

    # TODO: обновить assignment в БД, перегенерировать через AI
    await message.answer(
        f"Сроки обновлены: {message.text}\n\n"
        "AI перегенерирует предложение...",
        reply_markup=review_actions_kb(assignment_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("edit_stack:"))
async def on_edit_stack(callback: CallbackQuery, state: FSMContext):
    assignment_id = int(callback.data.split(":")[1])
    await state.set_state(ReviewStates.editing_stack)
    await state.update_data(assignment_id=assignment_id)
    await callback.answer()
    await callback.message.answer("Введите стек через запятую (например: Python, aiogram, PostgreSQL):")


@router.message(ReviewStates.editing_stack)
async def process_stack(message: Message, state: FSMContext):
    data = await state.get_data()
    assignment_id = data["assignment_id"]
    stack = [s.strip() for s in message.text.split(",")]
    await state.clear()

    # TODO: обновить assignment в БД, перегенерировать через AI
    await message.answer(
        f"Стек обновлён: {', '.join(stack)}\n\n"
        "AI перегенерирует предложение...",
        reply_markup=review_actions_kb(assignment_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("edit_custom:"))
async def on_edit_custom(callback: CallbackQuery, state: FSMContext):
    assignment_id = int(callback.data.split(":")[1])
    await state.set_state(ReviewStates.editing_custom)
    await state.update_data(assignment_id=assignment_id)
    await callback.answer()
    await callback.message.answer("Введите произвольное сообщение / пожелания к отклику:")


@router.message(ReviewStates.editing_custom)
async def process_custom(message: Message, state: FSMContext):
    data = await state.get_data()
    assignment_id = data["assignment_id"]
    await state.clear()

    # TODO: обновить assignment в БД, перегенерировать через AI
    await message.answer(
        f"Пожелания сохранены.\n\n"
        "AI перегенерирует предложение...",
        reply_markup=review_actions_kb(assignment_id),
    )
```

**Step 2: Commit**

```bash
git add src/bot/handlers/review.py
git commit -m "feat(bot): хендлер ревью заявки (FSM, редактирование)"
```

---

### Task 17: Хендлер утверждения и уведомления менеджеру

**Files:**
- Create: `src/bot/handlers/manager.py`

**Step 1: Реализовать**

```python
# src/bot/handlers/manager.py
"""Хендлер утверждения заявки и уведомления менеджеру."""
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from src.bot.keyboards.review import copy_response_kb

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(lambda c: c.data and c.data.startswith("approve:"))
async def on_approve(callback: CallbackQuery):
    """Разработчик утверждает заявку → уведомление менеджеру."""
    assignment_id = int(callback.data.split(":")[1])

    await callback.answer("Заявка утверждена и сохранена!")

    # Уведомление разработчику
    await callback.message.edit_text(
        callback.message.text + "\n\n<b>Статус: УТВЕРЖДЕНО</b>\n"
        "Отклик отправлен менеджеру.",
        reply_markup=None,
    )

    # TODO: получить данные из БД, сгенерировать отклик через AI
    # TODO: получить tg_id менеджера из team_members
    # Заглушка:
    response_text = "Здравствуйте! Предлагаем решение на Python + aiogram..."

    # Отправить менеджеру
    # await callback.bot.send_message(
    #     chat_id=manager_tg_id,
    #     text=(
    #         f"<b>Заявка #{assignment_id} утверждена!</b>\n"
    #         f"Исполнитель: @{callback.from_user.username}\n\n"
    #         f"<b>Готовый отклик:</b>\n{response_text}\n\n"
    #         f"Цена: ... руб. | Сроки: ... дней"
    #     ),
    #     reply_markup=copy_response_kb(assignment_id),
    # )
    logger.info(f"Заявка {assignment_id} утверждена @{callback.from_user.username}")


@router.callback_query(lambda c: c.data and c.data.startswith("copy_response:"))
async def on_copy_response(callback: CallbackQuery):
    """Менеджер копирует отклик."""
    response_id = int(callback.data.split(":")[1])
    # TODO: получить текст из БД и отправить отдельным сообщением (легко скопировать)
    await callback.answer("Текст отклика отправлен отдельным сообщением")
    await callback.message.answer(
        "Здравствуйте! [текст отклика будет здесь]"
    )
```

**Step 2: Commit**

```bash
git add src/bot/handlers/manager.py
git commit -m "feat(bot): утверждение заявки и уведомление менеджеру"
```

---

## Phase 5: Интеграция модулей

### Task 18: Scheduler — связывает парсер, AI и бота

**Files:**
- Create: `src/scheduler.py`

**Step 1: Реализовать**

```python
# src/scheduler.py
"""
Scheduler: связующий модуль.
1. Забирает новые заявки из Redis (от парсера)
2. Прогоняет через AI-анализ
3. Сохраняет в БД
4. Отправляет в групповой чат бота
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.models import Order, AiAnalysis, OrderStatus
from src.core.redis import RedisClient
from src.ai.openrouter import OpenRouterClient
from src.ai.analyzer import OrderAnalyzer
from src.bot.handlers.orders import send_order_to_group

logger = logging.getLogger(__name__)


async def run_scheduler(settings: Settings):
    redis = RedisClient(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    ai_client = OpenRouterClient(settings.openrouter_api_key, settings.openrouter_model)
    analyzer = OrderAnalyzer(ai_client)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    logger.info("Scheduler запущен")

    try:
        while True:
            order_data = await redis.pop_order()

            if not order_data:
                await asyncio.sleep(5)
                continue

            logger.info(f"Обработка заказа: {order_data.get('external_id')}")

            async with session_factory() as session:
                # Проверяем дубликат в БД
                existing = await session.execute(
                    select(Order).where(Order.external_id == order_data["external_id"])
                )
                if existing.scalar_one_or_none():
                    logger.debug(f"Заказ {order_data['external_id']} уже в БД, пропускаем")
                    continue

                # Сохраняем заказ
                order = Order(
                    external_id=order_data["external_id"],
                    platform="profiru",
                    title=order_data.get("title", ""),
                    description=order_data.get("description", ""),
                    budget=order_data.get("budget", ""),
                    location=order_data.get("location", ""),
                    raw_text=order_data.get("raw_text", ""),
                    status=OrderStatus.analyzing,
                )
                session.add(order)
                await session.flush()

                # AI-анализ
                try:
                    analysis_data = await analyzer.analyze_order(order.raw_text)
                except Exception:
                    logger.exception(f"Ошибка AI-анализа заказа {order.external_id}")
                    order.status = OrderStatus.new
                    await session.commit()
                    continue

                analysis = AiAnalysis(
                    order_id=order.id,
                    summary=analysis_data.get("summary", ""),
                    stack=analysis_data.get("stack", []),
                    price_min=analysis_data.get("price_min"),
                    price_max=analysis_data.get("price_max"),
                    timeline_days=analysis_data.get("timeline_days"),
                    relevance_score=analysis_data.get("relevance_score", 0),
                    complexity=analysis_data.get("complexity", "medium"),
                    response_draft=analysis_data.get("response_draft", ""),
                    model_used=settings.openrouter_model,
                )
                session.add(analysis)
                order.status = OrderStatus.reviewed

                await session.commit()

                # Отправляем в групповой чат
                await send_order_to_group(
                    bot=bot,
                    chat_id=settings.group_chat_id,
                    order_id=order.id,
                    analysis=analysis_data,
                    order=order_data,
                )
                logger.info(f"Заказ {order.external_id} отправлен в группу")

    finally:
        await ai_client.close()
        await redis.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    asyncio.run(run_scheduler(settings))
```

**Step 2: Commit**

```bash
git add src/scheduler.py
git commit -m "feat(scheduler): связующий модуль (парсер → AI → бот)"
```

---

### Task 19: Полная интеграция хендлеров с БД

**Files:**
- Modify: `src/bot/handlers/orders.py` — добавить работу с БД
- Modify: `src/bot/handlers/review.py` — добавить работу с БД и AI
- Modify: `src/bot/handlers/manager.py` — добавить работу с БД и AI
- Modify: `src/bot/bot.py` — добавить middleware с session_factory

Этот таск заменяет TODO-заглушки в хендлерах на реальную работу с БД и AI:
- `on_take_order` → создаёт `OrderAssignment` в БД, загружает `AiAnalysis`
- `process_price/timeline/stack/custom` → обновляет `OrderAssignment`, вызывает AI-перегенерацию
- `on_approve` → генерирует отклик через AI, сохраняет `ManagerResponse`, отправляет менеджеру

**Step 1: Реализовать интеграцию (подробная реализация будет в сессии)**

Ключевые изменения:
1. В `bot.py` — передать `session_factory` и `OrderAnalyzer` через `dp["session_factory"]` и `dp["analyzer"]`
2. В хендлерах — получать session через middleware или `callback.bot.get("session_factory")`
3. Заменить все `# TODO` на реальные операции с БД

**Step 2: Commit**

```bash
git commit -m "feat(bot): полная интеграция хендлеров с БД и AI"
```

---

## Phase 6: Systemd и деплой

### Task 20: Systemd unit-файлы

**Files:**
- Create: `scripts/systemd/devgrabbot.service`
- Create: `scripts/systemd/devgrab-parser.service`
- Create: `scripts/systemd/devgrab-scheduler.service`

**Step 1: Реализовать**

```ini
# scripts/systemd/devgrabbot.service
[Unit]
Description=DevGrabBot Telegram Bot
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=devgrab
WorkingDirectory=/opt/devgrabbot
EnvironmentFile=/opt/devgrabbot/.env
ExecStart=/opt/devgrabbot/.venv/bin/python -m src.bot.bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```ini
# scripts/systemd/devgrab-parser.service
[Unit]
Description=DevGrabBot Parser Worker
After=network.target redis.service

[Service]
Type=simple
User=devgrab
WorkingDirectory=/opt/devgrabbot
EnvironmentFile=/opt/devgrabbot/.env
ExecStart=/opt/devgrabbot/.venv/bin/python -m src.parser.worker
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```ini
# scripts/systemd/devgrab-scheduler.service
[Unit]
Description=DevGrabBot Scheduler (AI analysis + notifications)
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=devgrab
WorkingDirectory=/opt/devgrabbot
EnvironmentFile=/opt/devgrabbot/.env
ExecStart=/opt/devgrabbot/.venv/bin/python -m src.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Step 2: Commit**

```bash
git add scripts/
git commit -m "chore: systemd unit-файлы для VPS деплоя"
```

---

### Task 21: Скрипт деплоя

**Files:**
- Create: `scripts/deploy.sh`

**Step 1: Реализовать**

```bash
#!/bin/bash
# scripts/deploy.sh — деплой на VPS
set -euo pipefail

APP_DIR="/opt/devgrabbot"
VENV="$APP_DIR/.venv"
SERVICES="devgrabbot devgrab-parser devgrab-scheduler"

echo "=== Обновление кода ==="
cd "$APP_DIR"
git pull origin main

echo "=== Обновление зависимостей ==="
$VENV/bin/pip install -e .

echo "=== Миграции БД ==="
cd src && $VENV/bin/alembic upgrade head && cd ..

echo "=== Перезапуск сервисов ==="
for svc in $SERVICES; do
    sudo systemctl restart "$svc"
    echo "$svc перезапущен"
done

echo "=== Статус ==="
for svc in $SERVICES; do
    sudo systemctl status "$svc" --no-pager -l | head -5
done

echo "=== Деплой завершён ==="
```

**Step 2: Commit**

```bash
chmod +x scripts/deploy.sh
git add scripts/deploy.sh
git commit -m "chore: скрипт деплоя на VPS"
```

---

## Phase 7: Тестирование и финализация

### Task 22: Интеграционные тесты

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_integration.py`

**Step 1: Реализовать conftest**

```python
# tests/conftest.py
import pytest
from unittest.mock import AsyncMock

from src.core.config import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test")
    monkeypatch.setenv("GROUP_CHAT_ID", "-1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("PROFIRU_LOGIN", "+7")
    return Settings()


@pytest.fixture
def mock_ai_client():
    client = AsyncMock()
    client.complete_json.return_value = {
        "summary": "Тестовая заявка",
        "stack": ["Python"],
        "price_min": 30000,
        "price_max": 50000,
        "timeline_days": "7-10",
        "relevance_score": 85,
        "complexity": "medium",
        "response_draft": "Предлагаем решение...",
    }
    client.complete.return_value = "Готовый отклик"
    return client
```

**Step 2: Реализовать интеграционный тест**

```python
# tests/test_integration.py
import pytest
from src.ai.analyzer import OrderAnalyzer


@pytest.mark.asyncio
async def test_full_analysis_flow(mock_ai_client):
    """Полный цикл: анализ заявки → генерация отклика."""
    analyzer = OrderAnalyzer(mock_ai_client)

    # Анализ
    analysis = await analyzer.analyze_order("Нужен TG-бот для визового центра")
    assert analysis["relevance_score"] == 85

    # Генерация отклика
    response = await analyzer.generate_response(
        summary=analysis["summary"],
        stack=analysis["stack"],
        price=40000,
        timeline=analysis["timeline_days"],
    )
    assert response == "Готовый отклик"
```

**Step 3: Тесты проходят**

Run: `pytest tests/ -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/
git commit -m "test: интеграционные тесты"
```

---

### Task 23: Финальная проверка

**Step 1: Запустить все тесты**

Run: `pytest tests/ -v --tb=short`
Expected: ALL PASS

**Step 2: Проверить линтер**

Run: `ruff check src/ tests/`
Expected: No errors

**Step 3: Проверить структуру**

Run: `find src -name "*.py" | sort`
Expected: все файлы на месте

**Step 4: Финальный коммит**

```bash
git add -A
git commit -m "chore: финальная проверка и cleanup"
```

---

### Task 24: Обновить CLAUDE.md

**Step 1:** Обновить CLAUDE.md с командами запуска и тестирования.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: обновление CLAUDE.md"
```

---

## Summary

| Phase | Tasks | Что делаем |
|-------|-------|-----------|
| 1. Фундамент | 1-5 | pyproject.toml, config, models, DB, Redis, Alembic |
| 2. Парсер | 6-8 | BaseParser, ProfiruParser (Selenium + GraphQL), worker |
| 3. AI Engine | 9-11 | OpenRouter клиент, промпты, analyzer |
| 4. TG Bot | 12-17 | aiogram 3, keyboards, FSM, handlers (group + DM) |
| 5. Интеграция | 18-19 | Scheduler, интеграция handlers с БД + AI |
| 6. Деплой | 20-21 | systemd, deploy.sh |
| 7. Тесты | 22-24 | Integration tests, lint, cleanup |

**Всего: 24 задачи, 7 фаз.**
