"""Helper'ы для работы с платформами источников заявок (multi-platform v2.4)."""

from __future__ import annotations

# Лейблы платформ (HTML-safe, с эмодзи-иконкой).
PLATFORM_LABELS: dict[str, str] = {
    "profiru": "🇷🇺 Профи.ру",
    "kwork": "💼 Kwork",
}

# Короткие иконки для компактного вывода в списках.
PLATFORM_BADGES: dict[str, str] = {
    "profiru": "🇷🇺",
    "kwork": "💼",
}

# Человекочитаемые имена для кнопок настроек.
PLATFORM_NAMES: dict[str, str] = {
    "profiru": "Профи.ру",
    "kwork": "Kwork",
}

ALL_PLATFORMS: tuple[str, ...] = ("profiru", "kwork")


def _to_key(platform) -> str:
    """Нормализовать платформу (str | Enum | None) в ключ dict'а."""
    if platform is None:
        return ""
    # SQLAlchemy Enum отдаёт экземпляр enum с .value
    value = getattr(platform, "value", platform)
    return str(value).lower()


def get_order_url(platform, external_id: str) -> str | None:
    """Построить URL заявки на платформе по её external_id.

    Возвращает None для неизвестных платформ или пустого external_id.
    """
    if not external_id:
        return None
    key = _to_key(platform)
    if key == "profiru":
        return f"https://profi.ru/backoffice/n.php?o={external_id}"
    if key == "kwork":
        return f"https://kwork.ru/projects/{external_id}"
    return None


def get_platform_label(platform) -> str:
    """Вернуть красивый лейбл платформы с эмодзи."""
    key = _to_key(platform)
    return PLATFORM_LABELS.get(key, str(platform) if platform else "?")


def get_platform_badge(platform) -> str:
    """Вернуть короткую иконку платформы (для списков/заголовков)."""
    key = _to_key(platform)
    return PLATFORM_BADGES.get(key, "")


def get_platform_name(platform) -> str:
    """Вернуть читаемое имя платформы без иконки."""
    key = _to_key(platform)
    return PLATFORM_NAMES.get(key, str(platform) if platform else "?")


def is_platform_enabled_for_user(user_platforms: list[str] | None, order_platform) -> bool:
    """Проверить что пользователь подписан на получение заявок с платформы.

    Default: если user_platforms пуст или None — считаем что подписан на всё.
    """
    if not user_platforms:
        return True
    key = _to_key(order_platform)
    return key in {_to_key(p) for p in user_platforms}
