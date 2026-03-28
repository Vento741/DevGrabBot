"""Тесты конфигурации."""


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
