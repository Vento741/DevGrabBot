"""Сервис настроек — CRUD для таблицы settings с fallback на config."""
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.models import Setting

# Валидные ключи промптов (без префикса "prompt_")
PROMPT_KEYS = ("analyze", "response", "roadmap")

# Допустимые ключи для get_config_setting (совпадают с атрибутами Settings)
CONFIG_FALLBACK_KEYS = frozenset({
    "openrouter_model", "parse_interval_sec", "time_threshold_hours",
    "stats_broadcast_hour",
})

_STOP_WORDS_KEY = "stop_words"


# ---------------------------------------------------------------------------
# Базовый CRUD
# ---------------------------------------------------------------------------


async def get_setting(
    session: AsyncSession,
    key: str,
    default: str | None = None,
) -> str | None:
    """Получить значение настройки по ключу.

    Возвращает значение из БД или default если запись не найдена.
    """
    result = await session.execute(select(Setting).where(Setting.key == key))
    row: Setting | None = result.scalar_one_or_none()
    if row is None:
        return default
    return row.value


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    """Установить или обновить значение настройки (upsert).

    Выполняет merge + commit атомарно.
    """
    setting = Setting(key=key, value=value)
    await session.merge(setting)
    await session.commit()


async def delete_setting(session: AsyncSession, key: str) -> bool:
    """Удалить настройку по ключу.

    Возвращает True если запись была найдена и удалена, False — если не существовала.
    """
    result = await session.execute(select(Setting).where(Setting.key == key))
    row: Setting | None = result.scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# Стоп-слова
# ---------------------------------------------------------------------------


async def get_stop_words(session: AsyncSession, config: Settings) -> list[str]:
    """Получить список стоп-слов.

    Приоритет: значение из БД (JSON list) → config.stop_words fallback.
    """
    raw = await get_setting(session, _STOP_WORDS_KEY)
    if raw is not None:
        try:
            words = json.loads(raw)
            if isinstance(words, list):
                return [str(w) for w in words]
        except (json.JSONDecodeError, ValueError):
            pass
    return list(config.stop_words)


async def set_stop_words(session: AsyncSession, words: list[str]) -> None:
    """Сохранить список стоп-слов в БД (перезаписывает существующий)."""
    await set_setting(session, _STOP_WORDS_KEY, json.dumps(words, ensure_ascii=False))


async def _load_stop_words_raw(session: AsyncSession) -> list[str]:
    """Загрузить список стоп-слов из DB. Пустой список если не найден или битый JSON."""
    raw = await get_setting(session, _STOP_WORDS_KEY)
    if raw is not None:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(w) for w in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


async def add_stop_word(session: AsyncSession, word: str) -> list[str]:
    """Добавить стоп-слово в список.

    Загружает текущий список из БД (без config fallback — если в БД нет,
    считается пустым списком), добавляет слово если его ещё нет,
    сохраняет и возвращает обновлённый список.
    """
    current = await _load_stop_words_raw(session)
    if word not in current:
        current.append(word)
        await set_setting(
            session, _STOP_WORDS_KEY, json.dumps(current, ensure_ascii=False)
        )
    return current


async def remove_stop_word(session: AsyncSession, word: str) -> list[str]:
    """Удалить стоп-слово из списка.

    Если слово не найдено — список не изменяется.
    Возвращает обновлённый список.
    """
    current = await _load_stop_words_raw(session)
    if word in current:
        current.remove(word)
        await set_setting(
            session, _STOP_WORDS_KEY, json.dumps(current, ensure_ascii=False)
        )
    return current


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------


def _prompt_db_key(prompt_key: str) -> str:
    """Преобразовать короткий ключ промпта в DB key (например, "analyze" → "prompt_analyze")."""
    return f"prompt_{prompt_key}"


async def get_prompt(session: AsyncSession, prompt_key: str) -> str | None:
    """Получить текст промпта из БД.

    Возвращает текст или None если не задан (вызывающий код делает fallback на файл).
    prompt_key: одно из значений PROMPT_KEYS ("analyze", "response", "roadmap").
    """
    return await get_setting(session, _prompt_db_key(prompt_key))


async def set_prompt(session: AsyncSession, prompt_key: str, text: str) -> None:
    """Сохранить промпт в БД.

    prompt_key: одно из значений PROMPT_KEYS ("analyze", "response", "roadmap").
    """
    await set_setting(session, _prompt_db_key(prompt_key), text)


async def reset_prompt(session: AsyncSession, prompt_key: str) -> None:
    """Удалить промпт из БД (сброс к файловому дефолту).

    prompt_key: одно из значений PROMPT_KEYS ("analyze", "response", "roadmap").
    Если промпт не был задан в БД — ничего не делает.
    """
    await delete_setting(session, _prompt_db_key(prompt_key))


# ---------------------------------------------------------------------------
# Настройки с fallback на config
# ---------------------------------------------------------------------------


async def get_config_setting(
    session: AsyncSession, key: str, config: Settings
) -> str:
    """Получить настройку с fallback на значение из config.

    Поддерживаемые ключи (CONFIG_FALLBACK_KEYS):
      - "openrouter_model"
      - "parse_interval_sec"
      - "time_threshold_hours"

    Если ключ отсутствует в CONFIG_FALLBACK_KEYS — бросает KeyError.
    Приоритет: БД → config.
    """
    if key not in CONFIG_FALLBACK_KEYS:
        raise KeyError(
            f"Ключ '{key}' не поддерживается. "
            f"Допустимые ключи: {sorted(CONFIG_FALLBACK_KEYS)}"
        )

    db_value = await get_setting(session, key)
    if db_value is not None:
        return db_value

    return str(getattr(config, key))
