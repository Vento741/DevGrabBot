"""Тесты для базового класса парсера."""

import pytest

from src.parser.base import BaseParser


class ConcreteParser(BaseParser):
    """Конкретная реализация для тестирования абстрактного класса."""

    def __init__(self) -> None:
        self._orders: list[dict] = []

    async def fetch_orders(self) -> list[dict]:
        return self._orders

    def filter_order(self, order: dict) -> bool:
        return bool(order.get("subject"))


class IncompleteParser(BaseParser):
    """Неполная реализация -- отсутствует filter_order."""

    async def fetch_orders(self) -> list[dict]:
        return []


class TestBaseParserInterface:
    """Проверка контракта абстрактного класса BaseParser."""

    def test_cannot_instantiate_abstract_class(self) -> None:
        """Нельзя создать экземпляр BaseParser напрямую."""
        with pytest.raises(TypeError):
            BaseParser()  # type: ignore[abstract]

    def test_cannot_instantiate_incomplete_subclass(self) -> None:
        """Нельзя создать экземпляр подкласса без всех абстрактных методов."""
        with pytest.raises(TypeError):
            IncompleteParser()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        """Конкретный подкласс с обоими методами создаётся без ошибок."""
        parser = ConcreteParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_fetch_orders_returns_list(self) -> None:
        """fetch_orders возвращает список."""
        parser = ConcreteParser()
        parser._orders = [{"subject": "test"}]
        result = await parser.fetch_orders()
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_orders_empty(self) -> None:
        """fetch_orders может вернуть пустой список."""
        parser = ConcreteParser()
        result = await parser.fetch_orders()
        assert result == []

    def test_filter_order_accepts(self) -> None:
        """filter_order возвращает True для валидного заказа."""
        parser = ConcreteParser()
        assert parser.filter_order({"subject": "Разработка сайта"}) is True

    def test_filter_order_rejects(self) -> None:
        """filter_order возвращает False для пустого subject."""
        parser = ConcreteParser()
        assert parser.filter_order({"subject": ""}) is False
        assert parser.filter_order({}) is False
