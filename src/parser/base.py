"""Базовый абстрактный класс для парсеров фриланс-площадок."""

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
