"""Воркер уведомлений: забирает проанализированные заявки из Redis и отправляет dev'ам в личку."""
import asyncio
import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select, update

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.models import TeamMember, TeamRole, Order, OrderNotification, OrderStatus
from src.core.redis import RedisClient
from src.bot.handlers.orders import format_price_range, relevance_bar
from src.bot.services.matching import format_matches_block, match_developers
from src.bot.utils.platforms import (
    get_order_url,
    get_platform_badge,
    get_platform_label,
    is_platform_enabled_for_user,
)

# TTL-кэш для активных разработчиков (избегаем запрос к БД на каждое уведомление)
_DEV_CACHE_TTL = 60  # секунд
_dev_cache: list | None = None
_dev_cache_ts: float = 0

logger = logging.getLogger(__name__)


_MSK = timezone(timedelta(hours=3))

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Только настоящие HTML-теги: начинаются с буквы или "/". Пропускаем выражения
# типа "a < b & c > d" где < и > используются как символы.
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _sanitize_for_html(text: str) -> str:
    """Очистить произвольный текст для безопасной вставки в parse_mode=HTML.

    Kwork/Profi.ru могут отдавать description с HTML-тегами (<br>, <p>, …)
    которые TG Bot API не понимает и ломает парсинг. Снимаем все теги,
    конвертим <br> в \\n и экранируем <>& на случай если текст сам содержит
    символы которые TG примет за HTML.
    """
    if not text:
        return ""
    text = _BR_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    return html.escape(text)


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

    platform = data.get("platform", "profiru")
    platform_label = get_platform_label(platform)

    return (
        f"<b>Новая заявка</b> | #{data.get('external_id', '?')}\n"
        f"<b>Источник:</b> {platform_label}\n"
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


def format_group_card(analyzed: dict) -> str:
    """Расширенная карточка заявки для публикации в групповом чате.

    Содержит: оригинал (до 800 симв.), AI-резюме, стек, нашу оценку,
    сроки, сложность, релевантность, материалы, цену отклика, возраст.
    Общая длина не превышает ~2800 символов.
    """
    platform = analyzed.get("platform", "profiru")
    platform_label = get_platform_label(platform)
    raw_external_id = analyzed.get("external_id", "?") or "?"
    # external_id может содержать '#' — убираем лишний перед форматированием
    external_id = raw_external_id.lstrip("#")

    # Заголовок (полностью — обрезается только если > 200 симв.)
    raw_title = analyzed.get("title", "") or ""
    if len(raw_title) > 200:
        raw_title = raw_title[:197] + "..."
    title = _sanitize_for_html(raw_title)

    # Бюджет клиента
    budget = analyzed.get("budget", "") or ""
    analysis = analyzed.get("analysis", {}) or {}
    if not budget:
        budget_stated = analysis.get("client_budget_stated", False)
        budget = analysis.get("client_budget_text", "") if budget_stated else ""
    budget = _sanitize_for_html(budget)
    budget_header = f"  |  💰 {budget}" if budget else ""

    # Оригинал задачи (description / raw_text) — до 800 символов.
    # Санитайзим HTML: снимаем <br>, <p> и пр., которые TG Bot API не понимает.
    raw_description = (
        analyzed.get("description")
        or analyzed.get("raw_text")
        or ""
    ).strip()
    description = _sanitize_for_html(raw_description)
    if description:
        if len(description) > 800:
            description = description[:797] + "..."
        description_block = f"\n\n<b>📝 Оригинал ТЗ:</b>\n<i>{description}</i>"
    else:
        description_block = ""

    # Формат работы + локация + когда (из getOrder Profi.ru) — тоже санитайзим
    work_format = _sanitize_for_html((analyzed.get("work_format") or "").strip())
    location = _sanitize_for_html((analyzed.get("location") or "").strip())
    schedule = _sanitize_for_html((analyzed.get("schedule") or "").strip())
    details_lines = []
    if work_format:
        if location and location != work_format:
            details_lines.append(f"<b>🏠 Формат:</b> {work_format} ({location})")
        else:
            details_lines.append(f"<b>🏠 Формат:</b> {work_format}")
    elif location:
        details_lines.append(f"<b>📍 Локация:</b> {location}")
    if schedule:
        details_lines.append(f"<b>📅 Когда:</b> {schedule}")
    details_block = ("\n" + "\n".join(details_lines)) if details_lines else ""

    # AI-резюме
    summary = _sanitize_for_html((analysis.get("summary") or "").strip())
    summary_block = f"\n\n<b>🤖 AI-резюме:</b> {summary}" if summary else ""

    # Стек
    stack_list = analysis.get("stack") or []
    stack_str = _sanitize_for_html(", ".join(stack_list)) if stack_list else "—"
    stack_block = f"\n<b>💻 Стек:</b> {stack_str}"

    # Наша оценка (цена)
    price_min = analysis.get("price_min")
    price_max = analysis.get("price_max")
    price_str = format_price_range(price_min, price_max)
    price_block = f"\n<b>💵 Наша оценка:</b> {price_str}" if price_str else ""

    # Сроки
    timeline_days = analysis.get("timeline_days")
    if timeline_days:
        timeline_block = f"\n<b>⏳ Сроки:</b> {timeline_days} дн."
    else:
        timeline_block = ""

    # Сложность
    complexity = analysis.get("complexity") or ""
    complexity_block = f"\n<b>🧩 Сложность:</b> {complexity}" if complexity else ""

    # Релевантность (0-100, с визуальным лейблом HIGH/MEDIUM/LOW)
    relevance = analysis.get("relevance_score")
    relevance_block = (
        f"\n<b>🎯 Релевантность:</b> {relevance_bar(int(relevance))}"
        if relevance is not None else ""
    )

    # Материалы
    materials = analyzed.get("materials") or []
    materials_block = f"\n<b>📎 Материалы:</b> {len(materials)} шт." if materials else ""

    # Цена отклика (Profi.ru)
    response_price = analyzed.get("response_price")
    response_price_block = f"\n<b>💬 Цена отклика:</b> {response_price} ₽" if response_price else ""

    # Возраст заявки
    raw_date = analyzed.get("last_update_date") or analyzed.get("created_at")
    time_ago = _format_order_time(raw_date)
    time_block = f"  |  <b>⏰ Свежесть:</b> {time_ago}" if time_ago else ""

    # Собираем карточку
    card = (
        f"{platform_label}  |  #{external_id}{budget_header}\n"
        f"<b>{title}</b>"
        f"{description_block}"
        f"{details_block}"
        f"{summary_block}"
        f"\n"
        f"{stack_block}"
        f"{price_block}"
        f"{timeline_block}"
        f"{complexity_block}"
        f"{relevance_block}"
        f"{materials_block}"
        f"{response_price_block}{time_block}"
    )

    # Жёсткий лимит — обрезаем если превысили 2900 симв. (с запасом до TG 4096)
    if len(card) > 2900:
        card = card[:2897] + "..."

    return card


async def _publish_to_group(
    bot: Bot,
    session_factory,
    settings: Settings,
    order_id: int,
    analyzed: dict,
) -> int | None:
    """Публикует короткую карточку заявки в групповой чат.

    Возвращает message_id опубликованного сообщения или None при ошибке/выключенной группе.
    """
    if not getattr(settings, "group_chat_enabled", True):
        return None
    group_chat_id = getattr(settings, "group_chat_id", None)
    if not group_chat_id:
        return None

    from src.bot.keyboards.orders import pm_group_actions_kb

    text = format_group_card(analyzed)
    platform = analyzed.get("platform", "profiru")
    external_id = analyzed.get("external_id", "?")
    platform_url = get_order_url(platform, external_id)
    has_materials = bool(analyzed.get("materials"))
    markup = pm_group_actions_kb(order_id, platform_url=platform_url, has_materials=has_materials)

    try:
        msg = await bot.send_message(
            chat_id=group_chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("Не удалось опубликовать заявку #%s в группу: %s", order_id, exc)
        return None

    # Atomic UPDATE: пишем group_message_id только если поле ещё NULL.
    # Защита от race с pmg:hide — если hide успел обнулить раньше нас,
    # rowcount=0 → удаляем только что отправленное сообщение.
    async with session_factory() as session:
        result = await session.execute(
            update(Order)
            .where(Order.id == order_id, Order.group_message_id.is_(None))
            .values(group_message_id=msg.message_id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if result.rowcount == 0:
            logger.info(
                "Race: order_id=%s group_message_id уже занят, удаляю новое сообщение %s",
                order_id, msg.message_id,
            )
            try:
                await bot.delete_message(group_chat_id, msg.message_id)
            except Exception:
                pass
            return None

    return msg.message_id


def order_actions_keyboard(
    order_id: int,
    external_id: str,
    has_materials: bool = False,
    platform: str = "profiru",
):
    """Inline-клавиатура под заявкой в группе."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    link_url = get_order_url(platform, external_id) or ""
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
                url=link_url or "https://profi.ru/",
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
    """Бесконечный цикл: Redis (analyzed) → DM каждому подходящему dev'у.

    При каждой заявке:
    1. Загружает активных разработчиков из БД.
    2. Вычисляет матчинг по стеку.
    3. Отправляет уведомление каждому совпавшему dev'у в личку.
    4. Сохраняет OrderNotification для трекинга message_id.
    5. Если нет совпадений — авто-пропуск (status=skipped).
    """
    redis = RedisClient(settings)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    logger.info("Notification worker запущен (DM mode)")

    try:
        while True:
            data = await redis.pop_analyzed()
            if not data:
                await asyncio.sleep(3)
                continue

            order_id = data.get("order_id")
            external_id = data.get("external_id", "?")

            try:
                # Публикация в групповой чат (до матчинга — всегда, если включено)
                group_msg_id = await _publish_to_group(
                    bot, session_factory, settings, order_id, data,
                )
                if group_msg_id:
                    logger.info(
                        "Заявка #%s опубликована в группу, message_id=%s",
                        external_id, group_msg_id,
                    )

                # Матчинг разработчиков по стеку заявки
                order_stack: list[str] = data.get("analysis", {}).get("stack", [])
                matches: list = []
                if order_stack:
                    try:
                        developers = await _load_active_developers(session_factory)
                        matches = match_developers(order_stack, developers)
                    except Exception:
                        logger.exception("Не удалось загрузить разработчиков для матчинга")

                if not matches:
                    if not group_msg_id:
                        # Группа выключена И матчей нет — ставим skipped (fallback)
                        async with session_factory() as session:
                            order_result = await session.execute(
                                select(Order).where(Order.id == order_id)
                            )
                            order = order_result.scalar_one_or_none()
                            if order:
                                order.status = OrderStatus.skipped
                                await session.commit()
                        logger.info(
                            "Заявка #%s — нет матчей и группа выключена, авто-пропуск",
                            external_id,
                        )
                    else:
                        logger.info(
                            "Заявка #%s — нет матчей, опубликована в группу без DM-рассылки",
                            external_id,
                        )
                    continue

                has_materials = bool(data.get("materials"))
                order_platform = data.get("platform", "profiru")
                keyboard = order_actions_keyboard(
                    order_id, external_id,
                    has_materials=has_materials,
                    platform=order_platform,
                )

                # Отправляем каждому совпавшему dev'у в личку
                sent_count = 0
                async with session_factory() as session:
                    for dev, score, matched_techs in matches:
                        if not getattr(dev, "notify_assignments", True):
                            continue
                        # v2.4: проверка что dev подписан на платформу заявки
                        dev_platforms = getattr(dev, "platforms", None)
                        if not is_platform_enabled_for_user(dev_platforms, order_platform):
                            logger.debug(
                                "Dev %s не подписан на %s — пропуск",
                                dev.name, order_platform,
                            )
                            continue

                        # Персональный текст с инфой о матче
                        match_info = f"\n<b>Совпадение:</b> {', '.join(matched_techs)} — {score}%\n"
                        text = format_order_notification(data) + match_info

                        try:
                            msg = await bot.send_message(
                                chat_id=dev.tg_id,
                                text=text,
                                reply_markup=keyboard,
                            )
                            # Сохраняем трекинг уведомления
                            notification = OrderNotification(
                                order_id=order_id,
                                developer_id=dev.id,
                                message_id=msg.message_id,
                            )
                            session.add(notification)
                            sent_count += 1
                        except Exception:
                            logger.warning(
                                "Не удалось отправить заявку #%s dev'у %s (tg_id=%s)",
                                external_id, dev.name, dev.tg_id,
                            )

                    await session.commit()

                logger.info("Заявка #%s отправлена %d dev'ам в личку", external_id, sent_count)
                await asyncio.sleep(2)

            except Exception:
                logger.exception("Ошибка отправки заявки #%s", external_id)

    finally:
        await redis.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    asyncio.run(run_notification_worker(settings))
