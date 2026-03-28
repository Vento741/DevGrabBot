"""Воркер парсинга: бесконечный цикл получения и фильтрации заказов.

Фаза 1 Resilience Layer:
- CircuitBreaker: остановка при 5 ошибках подряд (cooldown 30 мин)
- TokenManager: кэш токена в Redis + backoff авторизации
- RequestScheduler: jitter ±20% + адаптивные интервалы (ночь/день)
- AlertService: уведомления о проблемах в Telegram
- HealthMonitor: метрики в Redis
"""

import asyncio
import logging
import os
import random
from logging.handlers import RotatingFileHandler

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.redis import RedisClient
from src.parser.profiru.scraper import ProfiruParser
from src.parser.resilience.circuit_breaker import CircuitBreaker, CircuitState
from src.parser.resilience.alert_service import AlertService
from src.parser.resilience.token_manager import TokenManager
from src.parser.resilience.request_scheduler import RequestScheduler
from src.parser.resilience.health import HealthMonitor

logger = logging.getLogger(__name__)


async def run_parser_worker(settings: Settings) -> None:
    """Запустить воркер парсинга с Resilience Layer.

    Цикл: scheduler.delay → CB check → token → fetch → filter → dedup → push.
    """
    parser = ProfiruParser(settings)
    redis_client = RedisClient(settings)

    # DB-подключение для обновления стоп-слов и настроек из БД
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    # Используем Redis-подключение из RedisClient (без дублирования)
    redis_conn = redis_client.redis

    # --- Resilience Layer ---
    circuit_breaker = CircuitBreaker(
        threshold=settings.parser_circuit_breaker_threshold,
        cooldown_sec=settings.parser_circuit_breaker_cooldown_sec,
    )
    # Загружаем tg_id всех активных участников для алертов
    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        from src.core.models import TeamMember
        result = await session.execute(
            sa_select(TeamMember.tg_id).where(TeamMember.is_active.is_(True))
        )
        team_chat_ids = [row[0] for row in result.all()]

    alert_service = AlertService(
        bot_token=settings.bot_token,
        chat_ids=team_chat_ids if team_chat_ids else [settings.admin_tg_id],
        dedup_sec=settings.parser_alert_dedup_sec,
    )
    token_manager = TokenManager(
        redis=redis_conn,
        circuit_breaker=circuit_breaker,
        alert_service=alert_service,
        auth_fn=parser.authorize_selenium,
        token_ttl_sec=settings.parser_token_ttl_sec,
        max_auth_attempts=settings.parser_max_auth_attempts,
        auth_cooldown_sec=settings.parser_auth_cooldown_sec,
    )
    scheduler = RequestScheduler(
        base_interval_sec=settings.parse_interval_sec,
        jitter_factor=settings.parser_jitter_factor,
        night_multiplier=settings.parser_night_multiplier,
    )
    health = HealthMonitor(
        redis=redis_conn,
        circuit_breaker=circuit_breaker,
        scheduler=scheduler,
        token_manager=token_manager,
    )

    # Установить начальный токен из конфига (если есть)
    if settings.profiru_token:
        await token_manager.set_initial_token(settings.profiru_token)

    logger.info(
        "Воркер парсера запущен с Resilience Layer "
        "(interval=%d, jitter=%.0f%%, CB threshold=%d, CB cooldown=%d сек)",
        settings.parse_interval_sec,
        settings.parser_jitter_factor * 100,
        settings.parser_circuit_breaker_threshold,
        settings.parser_circuit_breaker_cooldown_sec,
    )

    try:
        while True:
            # 0. Проверить паузу (PM недоступен)
            if await redis_client.is_parser_paused():
                logger.debug("Парсер на паузе (PM недоступен)")
                await asyncio.sleep(10)
                continue

            # 1. Рассчитать задержку (с jitter и adaptive)
            delay = scheduler.get_next_delay()

            # 2. Проверить Circuit Breaker (одно чтение state)
            cb_state = circuit_breaker.state
            if cb_state == CircuitState.OPEN:
                remaining = circuit_breaker.remaining_cooldown_sec
                logger.warning(
                    "Circuit Breaker OPEN — пропуск итерации (cooldown %.0f сек)",
                    remaining,
                )
                await health.save()
                await asyncio.sleep(min(delay, remaining + 10))
                continue

            if cb_state == CircuitState.HALF_OPEN:
                logger.info("Circuit Breaker HALF_OPEN — пробная итерация")
                await alert_service.info("cb_half_open", "Пробная итерация после cooldown")

            try:
                # 3. Обновляем стоп-слова из БД
                await _refresh_filters(parser, session_factory, settings)

                # 4. Получить токен и cookies сессии
                token = await token_manager.get_token()
                if not token:
                    logger.error("Не удалось получить токен — пропуск итерации")
                    scheduler.record_error()
                    health.record_error("Не удалось получить токен")
                    await health.save()
                    await _sleep_with_keepalive(
                        delay, parser, token_manager,
                        settings.parser_keep_alive_interval_sec,
                    )
                    continue

                # 4.1. Передать cookies сессии в scraper (как браузер)
                parser.set_session_cookies(token_manager.get_session_cookies())

                # 5. Парсинг
                new_count = await _parse_iteration(parser, redis_client, token, token_manager)

                # 5.1. Обновить cookies из ответов сервера (Set-Cookie)
                await token_manager.update_cookies_from_scraper(parser._session_cookies)

                # 6. Успех
                scheduler.record_success()
                circuit_breaker.record_success()
                health.record_iteration(new_count)

                if circuit_breaker.total_trips > 0 and circuit_breaker.state == CircuitState.CLOSED:
                    await alert_service.circuit_breaker_recovered()

            except Exception as exc:
                logger.exception("Ошибка в итерации парсера")
                scheduler.record_error()
                circuit_breaker.record_failure()
                health.record_error(str(exc))

                if circuit_breaker.state == CircuitState.OPEN:
                    await alert_service.circuit_breaker_opened(
                        circuit_breaker.failure_count,
                        circuit_breaker.cooldown_sec,
                    )

            # 7. Сохранить health и подождать (с keep-alive)
            await health.save()
            await _sleep_with_keepalive(
                delay, parser, token_manager, settings.parser_keep_alive_interval_sec
            )

    finally:
        logger.info("Воркер парсера завершает работу")
        await parser.close()
        await redis_client.close()
        await alert_service.close()
        await engine.dispose()


async def _sleep_with_keepalive(
    total_delay: float,
    parser: ProfiruParser,
    token_manager: TokenManager,
    keep_alive_interval: int,
) -> None:
    """Ожидание с периодическими keep-alive запросами.

    Вместо одного длинного sleep — серия коротких с keep-alive между ними.
    Это поддерживает сессию живой (как JS в браузере делает фоновые запросы).
    """
    if keep_alive_interval <= 0 or total_delay <= keep_alive_interval:
        await asyncio.sleep(total_delay)
        return

    remaining = total_delay
    while remaining > 0:
        sleep_chunk = min(keep_alive_interval, remaining)
        # Добавляем jitter ±15% к интервалу keep-alive
        jitter = random.uniform(-0.15, 0.15) * sleep_chunk
        actual_sleep = max(30, sleep_chunk + jitter)
        await asyncio.sleep(actual_sleep)
        remaining -= actual_sleep

        if remaining <= 30:
            break

        # Keep-alive запрос
        token = token_manager._token
        if token:
            parser.set_session_cookies(token_manager.get_session_cookies())
            alive = await parser.keep_alive(token)
            if alive:
                # Обновляем cookies в Redis
                await token_manager.update_cookies_from_scraper(parser._session_cookies)
            else:
                logger.info("Keep-alive: сессия истекла, будет переавторизация")
                await token_manager.invalidate()


async def _refresh_filters(parser: ProfiruParser, session_factory, settings: Settings) -> None:
    """Обновить стоп-слова и настройки фильтров из БД."""
    try:
        async with session_factory() as session:
            await parser.filters.refresh_stop_words(session, settings)
    except Exception:
        logger.debug("Не удалось обновить стоп-слова из БД, используем текущие")


async def _parse_iteration(
    parser: ProfiruParser,
    redis_client: RedisClient,
    token: str,
    token_manager: TokenManager,
) -> int:
    """Одна итерация парсинга: получение, фильтрация, дедупликация, отправка.

    Поток: GraphQL (raw) → 401? re-auth → normalize + enrich → filter → push.

    Returns:
        Количество новых заказов, добавленных в очередь.
    """
    # 1. GraphQL запрос (raw — возвращает None при 401)
    raw_orders = await parser.fetch_orders_raw(token)

    if raw_orders is None:
        # 401 — токен невалиден
        logger.info("Токен невалиден (401), запрашиваем новый")
        await token_manager.invalidate()
        token = await token_manager.get_token()
        if not token:
            raise RuntimeError("Авторизация не удалась после 401")
        raw_orders = await parser.fetch_orders_raw(token)
        if raw_orders is None:
            raise RuntimeError("Повторный 401 после переавторизации")

    if not raw_orders:
        logger.debug("Нет заказов от парсера")
        return 0

    # 2. Нормализация + обогащение ценами (с паузами между запросами)
    orders = await parser.process_raw_orders(raw_orders, token)

    if not orders:
        return 0

    new_count = 0
    for order in orders:
        if not parser.filter_order(order):
            continue

        external_id = order.get("external_id", "")
        if await redis_client.is_order_sent(external_id):
            continue

        await redis_client.push_order(order)
        await redis_client.mark_order_sent(external_id)
        new_count += 1

    if new_count:
        logger.info("Добавлено %d новых заказов в очередь", new_count)
    else:
        logger.debug("Новых заказов не обнаружено")

    return new_count


def setup_parser_logging(settings: Settings) -> None:
    """Настроить логирование парсера: файл (с ротацией) + консоль.

    Уровни через PARSER_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL, OFF.
    OFF — полностью отключает логирование парсера.
    """
    level_str = settings.parser_log_level.upper()

    # OFF — отключить логирование парсера
    if level_str == "OFF":
        logging.getLogger("src.parser").setLevel(logging.CRITICAL + 1)
        return

    level = getattr(logging, level_str, logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Корневой логгер парсера
    parser_logger = logging.getLogger("src.parser")
    parser_logger.setLevel(level)

    # Консоль
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    parser_logger.addHandler(console)

    # Файл с ротацией (5 MB × 3 файла)
    log_file = settings.parser_log_file
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    parser_logger.addHandler(file_handler)

    # Не пробрасываем логи парсера в root logger (избежать дублирования)
    parser_logger.propagate = False

    parser_logger.info(
        "Логирование парсера настроено: level=%s, file=%s", level_str, log_file
    )


if __name__ == "__main__":
    settings = Settings()
    setup_parser_logging(settings)
    asyncio.run(run_parser_worker(settings))
