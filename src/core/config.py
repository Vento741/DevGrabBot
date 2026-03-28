"""Конфигурация приложения через Pydantic Settings."""
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
    openrouter_model: str = "google/gemini-3.1-flash-lite-preview"

    # Profiru
    profiru_login: str
    profiru_password: str = ""
    profiru_token: str = ""

    # Parser
    parse_interval_sec: int = 600
    time_threshold_hours: int = 24
    stop_words: list[str] = ["WordPress", "Битрикс", "Опрос"]

    # Parser Resilience
    parser_token_ttl_sec: int = 480  # TTL токена в Redis (8 мин)
    parser_max_auth_attempts: int = 3  # макс. попыток авторизации подряд
    parser_jitter_factor: float = 0.2  # jitter ±20% к интервалу
    parser_night_multiplier: float = 3.0  # множитель интервала ночью (00-07 МСК)
    parser_circuit_breaker_threshold: int = 5  # ошибок до срабатывания CB
    parser_circuit_breaker_cooldown_sec: int = 1800  # 30 мин cooldown CB
    parser_alert_dedup_sec: int = 900  # 15 мин дедупликация алертов
    parser_request_delay_min: float = 1.0  # мин. пауза между запросами (сек)
    parser_request_delay_max: float = 3.0  # макс. пауза между запросами (сек)
    parser_auth_cooldown_sec: int = 120  # мин. интервал между авторизациями (2 мин)
    parser_keep_alive_interval_sec: int = 120  # интервал keep-alive запросов (2 мин)

    # Parser Logging
    parser_log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL, OFF
    parser_log_file: str = "logs/parser.log"  # путь к файлу логов парсера

    # Admin
    admin_tg_id: int = 5161187711

    # Scheduler
    stats_broadcast_hour: int = 9

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
