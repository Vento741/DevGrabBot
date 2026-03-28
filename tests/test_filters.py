"""Тесты фильтров заказов Профи.ру."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.parser.profiru.filters import MIN_ORDER_AGE_SECONDS, ProfiruFilters


def _make_settings(
    stop_words: list[str] | None = None,
    time_threshold_hours: int = 24,
) -> MagicMock:
    """Создать мок Settings с нужными параметрами."""
    settings = MagicMock()
    settings.stop_words = stop_words if stop_words is not None else ["wordpress", "битрикс"]
    settings.time_threshold_hours = time_threshold_hours
    return settings


def _make_order(
    external_id: str = "12345",
    subject: str = "Разработка сайта",
    description: str = "Нужен лендинг",
    last_update_date: str | None = None,
    age_seconds: float | None = None,
    **kwargs: object,
) -> dict:
    """Создать тестовый заказ.

    Args:
        age_seconds: если задан, last_update_date вычисляется автоматически.
    """
    if age_seconds is not None:
        dt = datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds)
        last_update_date = dt.isoformat()

    order: dict = {
        "external_id": external_id,
        "subject": subject,
        "description": description,
        "last_update_date": last_update_date,
    }
    order.update(kwargs)
    return order


class TestExternalId:
    """Проверка наличия external_id."""

    def test_order_without_external_id_rejected(self) -> None:
        filters = ProfiruFilters(_make_settings())
        order = _make_order(external_id="")
        assert filters.is_acceptable(order) is False

    def test_order_missing_external_id_key_rejected(self) -> None:
        filters = ProfiruFilters(_make_settings())
        order = {"subject": "test", "description": "test"}
        assert filters.is_acceptable(order) is False

    def test_order_with_external_id_passes(self) -> None:
        filters = ProfiruFilters(_make_settings())
        order = _make_order(age_seconds=300)
        assert filters.is_acceptable(order) is True


class TestStopWords:
    """Проверка фильтрации по стоп-словам."""

    def test_stop_word_in_subject(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["wordpress"]))
        order = _make_order(subject="Сайт на WordPress", age_seconds=300)
        assert filters.is_acceptable(order) is False

    def test_stop_word_in_description(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["битрикс"]))
        order = _make_order(description="Нужна доработка Битрикс", age_seconds=300)
        assert filters.is_acceptable(order) is False

    def test_stop_word_case_insensitive(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["wordpress"]))
        order = _make_order(subject="WORDPRESS сайт", age_seconds=300)
        assert filters.is_acceptable(order) is False

    def test_no_stop_words_passes(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["wordpress"]))
        order = _make_order(
            subject="Разработка на React",
            description="Нужен SPA",
            age_seconds=300,
        )
        assert filters.is_acceptable(order) is True

    def test_empty_stop_words_list(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=[]))
        order = _make_order(subject="WordPress сайт", age_seconds=300)
        assert filters.is_acceptable(order) is True

    def test_stop_word_in_type_field(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["опрос"]))
        order = _make_order(age_seconds=300, type="Опрос")
        assert filters.is_acceptable(order) is False

    def test_stop_word_in_title_field(self) -> None:
        """Стоп-слово в поле title (формат GraphQL API)."""
        filters = ProfiruFilters(_make_settings(stop_words=["wordpress"]))
        order = _make_order(age_seconds=300, title="Сайт на WordPress")
        assert filters.is_acceptable(order) is False


class TestOrderAge:
    """Проверка фильтрации по возрасту заказа."""

    def test_too_fresh_order_rejected(self) -> None:
        """Заказ моложе MIN_ORDER_AGE_SECONDS отклоняется."""
        filters = ProfiruFilters(_make_settings())
        order = _make_order(age_seconds=30)  # 30 сек < 70 сек
        assert filters.is_acceptable(order) is False

    def test_order_at_minimum_age_passes(self) -> None:
        """Заказ старше MIN_ORDER_AGE_SECONDS проходит."""
        filters = ProfiruFilters(_make_settings())
        order = _make_order(age_seconds=MIN_ORDER_AGE_SECONDS + 10)
        assert filters.is_acceptable(order) is True

    def test_too_old_order_rejected(self) -> None:
        """Заказ старше time_threshold_hours отклоняется."""
        filters = ProfiruFilters(_make_settings(time_threshold_hours=24))
        order = _make_order(age_seconds=25 * 3600)  # 25 часов
        assert filters.is_acceptable(order) is False

    def test_order_within_age_range_passes(self) -> None:
        """Заказ в допустимом диапазоне проходит."""
        filters = ProfiruFilters(_make_settings(time_threshold_hours=24))
        order = _make_order(age_seconds=2 * 3600)  # 2 часа
        assert filters.is_acceptable(order) is True

    def test_order_without_date_passes(self) -> None:
        """Заказ без last_update_date проходит проверку возраста."""
        filters = ProfiruFilters(_make_settings())
        order = _make_order(last_update_date=None)
        assert filters.is_acceptable(order) is True

    def test_order_with_unix_timestamp(self) -> None:
        """Поддержка unix timestamp в last_update_date."""
        filters = ProfiruFilters(_make_settings())
        ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).timestamp()
        order = _make_order()
        order["last_update_date"] = ts
        assert filters.is_acceptable(order) is True

    def test_order_with_invalid_date_passes(self) -> None:
        """Невалидная дата не блокирует заказ."""
        filters = ProfiruFilters(_make_settings())
        order = _make_order()
        order["last_update_date"] = "not-a-date"
        assert filters.is_acceptable(order) is True


class TestCombinedFilters:
    """Комбинированные проверки."""

    def test_all_filters_pass(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["wordpress"]))
        order = _make_order(
            external_id="999",
            subject="React приложение",
            description="Нужен SPA",
            age_seconds=600,
        )
        assert filters.is_acceptable(order) is True

    def test_stop_word_overrides_valid_age(self) -> None:
        filters = ProfiruFilters(_make_settings(stop_words=["wordpress"]))
        order = _make_order(
            subject="Сайт WordPress",
            age_seconds=600,
        )
        assert filters.is_acceptable(order) is False

    def test_no_external_id_overrides_everything(self) -> None:
        filters = ProfiruFilters(_make_settings())
        order = _make_order(
            external_id="",
            subject="React",
            age_seconds=600,
        )
        assert filters.is_acceptable(order) is False
