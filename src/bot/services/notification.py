"""Воркер уведомлений: забирает проанализированные заявки из Redis и отправляет в группу."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.models import TeamMember, TeamRole
from src.core.redis import RedisClient
from src.bot.handlers.orders import format_price_range, relevance_bar
from src.bot.services.matching import format_matches_block, match_developers

# TTL-кэш для активных разработчиков (избегаем запрос к БД на каждое уведомление)
_DEV_CACHE_TTL = 60  # секунд
_dev_cache: list | None = None
_dev_cache_ts: float = 0

logger = logging.getLogger(__name__)


_MSK = timezone(timedelta(hours=3))


def _format_order_time(date_value) -> str:
    """Форматирует время создания заказа: относительное + абсолютное МСК.

    Поддерживает ISO-строку, Unix timestamp (int/float) и строку-число.
    """
    if not date_value:
        return ""
    try:
        if isinstance(date_value, (int, float)):
            dt = datetime.fromtimestamp(date_value, tz=timezone.utc)
        elif isinstance(date_value, str):
            # Попытка как число (timestamp в строке)
            try:
                ts = float(date_value)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except ValueError:
                dt = datetime.fromisoformat(date_value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
        else:
            return ""

        now = datetime.now(tz=timezone.utc)
        diff = now - dt

        # Относительное время
        total_minutes = int(diff.total_seconds() / 60)
        if total_minutes < 0:
            total_minutes = 0
        if total_minutes < 1:
            ago = "только что"
        elif total_minutes < 60:
            ago = f"{total_minutes} мин. назад"
        elif total_minutes < 1440:
            ago = f"{total_minutes // 60} ч. назад"
        else:
            ago = f"{total_minutes // 1440} дн. назад"

        # Абсолютное время в МСК
        dt_msk = dt.astimezone(_MSK)
        abs_time = dt_msk.strftime("%d.%m %H:%M МСК")

        return f"{ago} ({abs_time})"
    except (ValueError, TypeError, OSError):
        return ""


def format_order_notification(data: dict, matches: list | None = None) -> str:
    """Форматирует сообщение о заявке для группового чата.

    Args:
        data: данные из Redis (результат AI-анализа).
        matches: результат match_developers() — список (dev, score, techs).
                 Если передан, добавляется блок «Подходящие разработчики».
    """
    analysis = data.get("analysis", {})
    stack_str = ", ".join(analysis.get("stack", []))
    relevance = analysis.get("relevance_score", 0)
    complexity = analysis.get("complexity", "?")

    # Бюджет клиента: приоритет — данные парсера, потом AI
    parser_budget = data.get("budget", "")
    if parser_budget:
        budget_line = parser_budget
    else:
        budget_stated = analysis.get("client_budget_stated", False)
        budget_text = analysis.get("client_budget_text", "Не указан")
        budget_line = budget_text if budget_stated else "Не указан"

    # Сроки клиента
    deadline_stated = analysis.get("client_deadline_stated", False)
    deadline_text = analysis.get("client_deadline_text", "Не указаны")
    deadline_line = deadline_text if deadline_stated else "Не указаны"

    # Цена отклика (комиссия Профи.ру)
    response_price = data.get("response_price")
    response_price_line = f"{response_price} руб." if response_price else "—"

    # Формат работы и локация из парсера
    work_format = data.get("work_format", "")
    location = data.get("location", "")
    schedule = data.get("schedule", "")
    client_name = data.get("client_name", "")

    # Блок метаданных заказа
    meta_lines = []
    if work_format or location:
        place = f"{work_format}, {location}" if work_format and location else (work_format or location)
        meta_lines.append(f"<b>Формат:</b> {place}")
    if schedule:
        meta_lines.append(f"<b>Когда:</b> {schedule}")
    if client_name:
        meta_lines.append(f"<b>Клиент:</b> {client_name}")
    meta_block = "\n".join(meta_lines) + "\n" if meta_lines else ""

    # Вопросы клиенту
    questions = analysis.get("questions_to_client", [])
    questions_block = ""
    if questions:
        q_list = "\n".join(f"  - {q}" for q in questions[:5])
        questions_block = f"\n<b>Вопросы клиенту:</b>\n{q_list}\n"

    # Риски
    risks = analysis.get("risks", "")
    risks_line = f"\n<b>Риски:</b> {risks}\n" if risks and risks != "Нет явных рисков" else ""

    # Блок подходящих разработчиков (опционально)
    matches_block = ""
    if matches:
        matches_str = format_matches_block(matches)
        if matches_str:
            matches_block = f"\n{matches_str}\n"

    # Время создания заказа
    raw_date = data.get("last_update_date")
    logger.debug("last_update_date для #%s: %r (type=%s)", data.get("external_id"), raw_date, type(raw_date).__name__)
    time_ago = _format_order_time(raw_date)
    time_line = f"\U0001f552 Заказ оставлен {time_ago}\n" if time_ago else ""

    return (
        f"<b>Новая заявка</b> | #{data.get('external_id', '?')}\n"
        f"<b>{data.get('title', '')}</b>\n"
        f"{time_line}\n"
        f"<b>Выжимка:</b>\n{analysis.get('summary', 'Нет данных')}\n\n"
        f"<b>Пожелания заказчика:</b>\n{analysis.get('client_requirements', 'Не уточнены')}\n\n"
        f"{meta_block}"
        f"<b>Бюджет клиента:</b> {budget_line}\n"
        f"<b>Сроки клиента:</b> {deadline_line}\n"
        f"<b>Цена отклика:</b> {response_price_line}\n"
        + (f"\U0001f4ce <b>Материалы:</b> {len(data.get('materials') or [])} шт.\n" if data.get("materials") else "")
        + f"\n<b>Стек:</b> {stack_str}\n"
        f"<b>Наша оценка:</b> {format_price_range(analysis.get('price_min'), analysis.get('price_max'))}\n"
        f"<b>Наши сроки:</b> {analysis.get('timeline_days', '?')} дн.\n"
        f"<b>Сложность:</b> {complexity}\n"
        f"<b>Релевантность:</b> {relevance_bar(relevance)}\n"
        f"{questions_block}"
        f"{risks_line}"
        f"{matches_block}"
    )


def order_actions_keyboard(
    order_id: int, external_id: str, has_materials: bool = False,
):
    """Inline-клавиатура под заявкой в группе."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [
            InlineKeyboardButton(
                text="\u2705 Взять",
                callback_data=f"take:{order_id}",
                style="success",
            ),
            InlineKeyboardButton(
                text="\u274c Пропустить",
                callback_data=f"skip:{order_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Ссылка",
                url=f"https://profi.ru/backoffice/n.php?o={external_id}",
                style="primary",
            ),
            InlineKeyboardButton(
                text="Оригинал",
                callback_data=f"original:{order_id}",
            ),
        ],
    ]
    if has_materials:
        rows.insert(1, [
            InlineKeyboardButton(
                text="\U0001f50d Просмотр деталей",
                callback_data=f"materials:{order_id}",
                style="primary",
            ),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _load_active_developers(session_factory) -> list:
    """Загружает активных разработчиков из БД с TTL-кэшем."""
    global _dev_cache, _dev_cache_ts
    now = time.monotonic()
    if _dev_cache is not None and (now - _dev_cache_ts) < _DEV_CACHE_TTL:
        return _dev_cache

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(
                TeamMember.is_active == True,  # noqa: E712
                TeamMember.role == TeamRole.developer,
            )
        )
        _dev_cache = list(result.scalars().all())
        _dev_cache_ts = now
        return _dev_cache


async def run_notification_worker(settings: Settings):
    """Бесконечный цикл: Redis (analyzed) → TG-группа.

    При каждой заявке:
    1. Загружает активных разработчиков из БД.
    2. Вычисляет матчинг по стеку.
    3. Формирует уведомление с блоком «Подходящие разработчики».
    4. Отправляет в групповой чат.
    """
    redis = RedisClient(settings)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    logger.info("Notification worker запущен")

    try:
        while True:
            data = await redis.pop_analyzed()
            if not data:
                await asyncio.sleep(3)
                continue

            order_id = data.get("order_id")
            external_id = data.get("external_id", "?")

            try:
                # Матчинг разработчиков по стеку заявки
                order_stack: list[str] = data.get("analysis", {}).get("stack", [])
                matches: list = []
                if order_stack:
                    try:
                        developers = await _load_active_developers(session_factory)
                        matches = match_developers(order_stack, developers)
                    except Exception:
                        logger.exception("Не удалось загрузить разработчиков для матчинга")

                text = format_order_notification(data, matches=matches if matches else None)
                has_materials = bool(data.get("materials"))
                keyboard = order_actions_keyboard(order_id, external_id, has_materials=has_materials)

                await bot.send_message(
                    chat_id=settings.group_chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
                logger.info(f"Заявка #{external_id} отправлена в группу")
                await asyncio.sleep(5)

            except Exception:
                logger.exception(f"Ошибка отправки заявки #{external_id} в группу")

    finally:
        await redis.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    asyncio.run(run_notification_worker(settings))
