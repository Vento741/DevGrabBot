"""Воркер парсинга Kwork: простой цикл opollинга с дедупликацией.

В отличие от Profi.ru воркера, Kwork не требует Selenium/keep-alive —
API stateless, токен живёт ~30 дней, auto-relogin встроен в pykwork.
"""

import asyncio
import logging

from src.core.config import Settings
from src.core.redis import RedisClient
from src.parser.kwork.scraper import KworkParser

logger = logging.getLogger(__name__)

KWORK_SEEN_IDS_KEY = "devgrab:parser:kwork:seen_ids"


async def run_kwork_worker(settings: Settings) -> None:
    """Запустить простой воркер парсинга Kwork.

    Цикл: poll → normalize → filter → dedup (Redis set) → push в new_orders.
    """
    parser = KworkParser(settings)
    redis_client = RedisClient(settings)
    redis_conn = redis_client.redis

    # Startup sweep: прогреть seen_ids текущими проектами чтобы не залить
    # очередь дублями на первом запуске.
    try:
        logger.info("Kwork worker: startup sweep...")
        orders = await parser.fetch_orders()
        for order in orders:
            await redis_conn.sadd(KWORK_SEEN_IDS_KEY, order["external_id"])
        await redis_conn.expire(KWORK_SEEN_IDS_KEY, settings.kwork_dedup_ttl_sec)
        logger.info("Kwork: startup sweep засеял %d ID", len(orders))
    except Exception:
        logger.exception("Kwork: ошибка startup sweep (продолжаем)")

    interval = settings.kwork_poll_interval_sec
    logger.info("Kwork воркер запущен (интервал %d сек)", interval)

    try:
        while True:
            if await redis_client.is_parser_paused():
                logger.debug("Kwork parser на паузе")
                await asyncio.sleep(10)
                continue

            try:
                await _kwork_iteration(parser, redis_client, redis_conn, settings)
            except Exception:
                logger.exception("Kwork: ошибка итерации")

            await asyncio.sleep(interval)
    finally:
        logger.info("Kwork worker завершает работу")
        await parser.close()
        await redis_client.close()


async def _kwork_iteration(parser, redis_client, redis_conn, settings: Settings) -> None:
    """Одна итерация: fetch → filter → dedup → push."""
    # Обновить стоп-слова из БД
    try:
        from src.core.database import create_engine, create_session_factory
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            await parser.filters.refresh_stop_words(session, settings)
        await engine.dispose()
    except Exception:
        logger.debug("Kwork: не удалось обновить стоп-слова", exc_info=True)

    orders = await parser.fetch_orders()
    if not orders:
        return

    new_count = 0
    for order in orders:
        if not parser.filter_order(order):
            continue
        external_id = order["external_id"]

        # Дедуп через Redis set (локальный для Kwork)
        if await redis_conn.sismember(KWORK_SEEN_IDS_KEY, external_id):
            continue
        # Плюс общая дедуп-проверка в очереди
        if await redis_client.is_order_sent(external_id):
            continue

        await redis_client.push_order(order)
        await redis_client.mark_order_sent(external_id)
        await redis_conn.sadd(KWORK_SEEN_IDS_KEY, external_id)
        new_count += 1

    # Обновить TTL на set (чтоб не рос бесконечно)
    await redis_conn.expire(KWORK_SEEN_IDS_KEY, settings.kwork_dedup_ttl_sec)

    if new_count:
        logger.info("Kwork: добавлено %d новых заказов в очередь", new_count)
