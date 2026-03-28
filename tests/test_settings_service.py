"""Тесты сервиса настроек (settings_service.py).

Используют AsyncMock для session — реальная БД не нужна.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.config import Settings
from src.core.settings_service import (
    PROMPT_KEYS,
    CONFIG_FALLBACK_KEYS,
    get_setting,
    set_setting,
    delete_setting,
    get_stop_words,
    set_stop_words,
    add_stop_word,
    remove_stop_word,
    get_prompt,
    set_prompt,
    reset_prompt,
    get_config_setting,
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_session(scalar_value=None):
    """Создать mock AsyncSession.

    scalar_value — значение, которое вернёт .scalar_one_or_none().
    """
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = scalar_value
    session.execute.return_value = execute_result
    return session


def _make_setting(key: str, value: str):
    """Создать mock объект Setting."""
    setting = MagicMock()
    setting.key = key
    setting.value = value
    return setting


def _make_config(**kwargs) -> Settings:
    """Создать Settings с минимальными обязательными полями."""
    defaults = {
        "bot_token": "test_token",
        "group_chat_id": -100123,
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "openrouter_api_key": "sk-test",
        "profiru_login": "+71234567890",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Тесты get_setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_setting_returns_value_from_db():
    """get_setting возвращает значение из БД если запись найдена."""
    setting_obj = _make_setting("some_key", "some_value")
    session = _make_session(scalar_value=setting_obj)

    result = await get_setting(session, "some_key")

    assert result == "some_value"
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_setting_returns_default_when_not_found():
    """get_setting возвращает default если запись не найдена."""
    session = _make_session(scalar_value=None)

    result = await get_setting(session, "missing_key", default="fallback")

    assert result == "fallback"


@pytest.mark.asyncio
async def test_get_setting_returns_none_by_default():
    """get_setting возвращает None если default не передан и запись не найдена."""
    session = _make_session(scalar_value=None)

    result = await get_setting(session, "missing_key")

    assert result is None


# ---------------------------------------------------------------------------
# Тесты set_setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_setting_calls_merge_and_commit():
    """set_setting вызывает session.merge() и session.commit()."""
    session = AsyncMock()

    await set_setting(session, "my_key", "my_value")

    session.merge.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_set_setting_merges_correct_object():
    """set_setting передаёт в merge объект Setting с правильными key и value."""
    from src.core.models import Setting

    session = AsyncMock()

    await set_setting(session, "test_key", "test_value")

    call_args = session.merge.call_args
    merged_obj = call_args[0][0]
    assert isinstance(merged_obj, Setting)
    assert merged_obj.key == "test_key"
    assert merged_obj.value == "test_value"


# ---------------------------------------------------------------------------
# Тесты delete_setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_setting_returns_true_when_found():
    """delete_setting возвращает True и вызывает delete+commit если запись найдена."""
    setting_obj = _make_setting("del_key", "val")
    session = _make_session(scalar_value=setting_obj)

    result = await delete_setting(session, "del_key")

    assert result is True
    session.delete.assert_called_once_with(setting_obj)
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_setting_returns_false_when_not_found():
    """delete_setting возвращает False если запись не существует."""
    session = _make_session(scalar_value=None)

    result = await delete_setting(session, "nonexistent_key")

    assert result is False
    session.delete.assert_not_called()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты get_stop_words
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stop_words_from_db():
    """get_stop_words возвращает список из БД если запись есть."""
    words = ["Django", "Laravel", "Joomla"]
    setting_obj = _make_setting("stop_words", json.dumps(words))
    session = _make_session(scalar_value=setting_obj)
    config = _make_config()

    result = await get_stop_words(session, config)

    assert result == words


@pytest.mark.asyncio
async def test_get_stop_words_from_config_fallback():
    """get_stop_words возвращает config.stop_words если в БД нет записи."""
    session = _make_session(scalar_value=None)
    config = _make_config()

    result = await get_stop_words(session, config)

    assert result == list(config.stop_words)


@pytest.mark.asyncio
async def test_get_stop_words_fallback_on_invalid_json():
    """get_stop_words падает на config если в БД некорректный JSON."""
    setting_obj = _make_setting("stop_words", "not-a-json")
    session = _make_session(scalar_value=setting_obj)
    config = _make_config()

    result = await get_stop_words(session, config)

    assert result == list(config.stop_words)


@pytest.mark.asyncio
async def test_get_stop_words_fallback_on_non_list_json():
    """get_stop_words падает на config если JSON не является списком."""
    setting_obj = _make_setting("stop_words", json.dumps({"key": "val"}))
    session = _make_session(scalar_value=setting_obj)
    config = _make_config()

    result = await get_stop_words(session, config)

    assert result == list(config.stop_words)


# ---------------------------------------------------------------------------
# Тесты add_stop_word
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_stop_word_to_existing_list():
    """add_stop_word добавляет слово к существующему списку в БД."""
    existing = ["WordPress", "Битрикс"]
    session = AsyncMock()

    # Первый вызов execute (get_setting внутри add_stop_word) → возвращает existing
    execute_result_get = MagicMock()
    execute_result_get.scalar_one_or_none.return_value = _make_setting(
        "stop_words", json.dumps(existing)
    )
    # Второй вызов execute (get_setting внутри set_setting через merge) — не нужен,
    # set_setting не вызывает execute
    session.execute.return_value = execute_result_get

    result = await add_stop_word(session, "Joomla")

    assert "Joomla" in result
    assert "WordPress" in result
    assert "Битрикс" in result
    session.merge.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_add_stop_word_no_duplicate():
    """add_stop_word не добавляет слово если оно уже в списке."""
    existing = ["WordPress", "Битрикс"]
    session = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = _make_setting(
        "stop_words", json.dumps(existing)
    )
    session.execute.return_value = execute_result

    result = await add_stop_word(session, "WordPress")

    assert result.count("WordPress") == 1
    # merge не должен вызваться — список не изменился
    session.merge.assert_not_called()


@pytest.mark.asyncio
async def test_add_stop_word_to_empty_db():
    """add_stop_word создаёт новый список если в БД ничего нет."""
    session = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    session.execute.return_value = execute_result

    result = await add_stop_word(session, "NewWord")

    assert result == ["NewWord"]
    session.merge.assert_called_once()
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Тесты remove_stop_word
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_stop_word_from_existing_list():
    """remove_stop_word удаляет слово из списка."""
    existing = ["WordPress", "Битрикс", "Joomla"]
    session = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = _make_setting(
        "stop_words", json.dumps(existing)
    )
    session.execute.return_value = execute_result

    result = await remove_stop_word(session, "Битрикс")

    assert "Битрикс" not in result
    assert "WordPress" in result
    assert "Joomla" in result
    session.merge.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_remove_stop_word_not_in_list():
    """remove_stop_word не изменяет список если слово не найдено."""
    existing = ["WordPress"]
    session = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = _make_setting(
        "stop_words", json.dumps(existing)
    )
    session.execute.return_value = execute_result

    result = await remove_stop_word(session, "Bitrix")

    assert result == ["WordPress"]
    session.merge.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты get_prompt / set_prompt / reset_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_prompt_returns_text_from_db():
    """get_prompt возвращает текст промпта из БД."""
    prompt_text = "Ты — AI-ассистент для анализа заказов."
    session = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = _make_setting(
        "prompt_analyze", prompt_text
    )
    session.execute.return_value = execute_result

    result = await get_prompt(session, "analyze")

    assert result == prompt_text


@pytest.mark.asyncio
async def test_get_prompt_returns_none_if_not_set():
    """get_prompt возвращает None если промпт не задан в БД."""
    session = _make_session(scalar_value=None)

    result = await get_prompt(session, "response")

    assert result is None


@pytest.mark.asyncio
async def test_set_prompt_saves_with_correct_key():
    """set_prompt сохраняет промпт с правильным префиксом 'prompt_'."""
    from src.core.models import Setting

    session = AsyncMock()

    await set_prompt(session, "roadmap", "Текст промпта roadmap")

    call_args = session.merge.call_args[0][0]
    assert isinstance(call_args, Setting)
    assert call_args.key == "prompt_roadmap"
    assert call_args.value == "Текст промпта roadmap"
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_reset_prompt_deletes_from_db():
    """reset_prompt удаляет промпт из БД (делегирует delete_setting)."""
    setting_obj = _make_setting("prompt_analyze", "old prompt text")
    session = _make_session(scalar_value=setting_obj)

    await reset_prompt(session, "analyze")

    session.delete.assert_called_once_with(setting_obj)
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_reset_prompt_does_nothing_if_not_set():
    """reset_prompt не падает если промпт не был задан."""
    session = _make_session(scalar_value=None)

    # Не должен бросить исключение
    await reset_prompt(session, "response")

    session.delete.assert_not_called()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты get_config_setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_config_setting_from_db():
    """get_config_setting возвращает значение из БД если есть."""
    session = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = _make_setting(
        "openrouter_model", "anthropic/claude-3-haiku"
    )
    session.execute.return_value = execute_result

    config = _make_config()
    result = await get_config_setting(session, "openrouter_model", config)

    assert result == "anthropic/claude-3-haiku"


@pytest.mark.asyncio
async def test_get_config_setting_from_config_fallback():
    """get_config_setting возвращает значение из config если в БД нет."""
    session = _make_session(scalar_value=None)
    config = _make_config()

    result = await get_config_setting(session, "parse_interval_sec", config)

    assert result == str(config.parse_interval_sec)


@pytest.mark.asyncio
async def test_get_config_setting_fallback_openrouter_model():
    """get_config_setting возвращает openrouter_model из config как строку."""
    session = _make_session(scalar_value=None)
    config = _make_config()

    result = await get_config_setting(session, "openrouter_model", config)

    assert result == config.openrouter_model


@pytest.mark.asyncio
async def test_get_config_setting_fallback_time_threshold():
    """get_config_setting возвращает time_threshold_hours из config как строку."""
    session = _make_session(scalar_value=None)
    config = _make_config()

    result = await get_config_setting(session, "time_threshold_hours", config)

    assert result == str(config.time_threshold_hours)


@pytest.mark.asyncio
async def test_get_config_setting_raises_on_unknown_key():
    """get_config_setting бросает KeyError для неподдерживаемых ключей."""
    session = _make_session(scalar_value=None)
    config = _make_config()

    with pytest.raises(KeyError, match="unknown_key"):
        await get_config_setting(session, "unknown_key", config)


# ---------------------------------------------------------------------------
# Тесты констант
# ---------------------------------------------------------------------------


def test_prompt_keys_constant():
    """PROMPT_KEYS содержит нужные ключи."""
    assert set(PROMPT_KEYS) == {"analyze", "response", "roadmap"}


def test_config_fallback_keys_constant():
    """CONFIG_FALLBACK_KEYS содержит нужные ключи."""
    assert CONFIG_FALLBACK_KEYS == frozenset({"openrouter_model", "parse_interval_sec", "time_threshold_hours"})
