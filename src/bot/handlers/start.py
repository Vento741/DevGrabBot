"""Обработчик команды /start."""
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

router = Router(name="start")


def _start_kb() -> InlineKeyboardMarkup:
    """Клавиатура стартового сообщения с навигацией к панелям."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Панель разработчика", callback_data="open:dev",
                ),
                InlineKeyboardButton(
                    text="Панель менеджера", callback_data="open:manager",
                ),
            ],
        ],
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Приветственное сообщение при первом запуске."""
    await message.answer(
        "<b>DevGrabBot</b>\n\n"
        "Бот для автоматической обработки фриланс-заявок.\n\n"
        "Как это работает:\n"
        "1. Парсер собирает заявки с Профи.ру\n"
        "2. AI анализирует каждую заявку (стек, цена, сроки)\n"
        "3. Релевантные заявки приходят в групповой чат\n"
        "4. Разработчик берёт заявку, корректирует отклик\n"
        "5. Готовый отклик уходит менеджеру для отправки\n\n"
        "Выберите панель:",
        reply_markup=_start_kb(),
    )
    logger.info(
        "Пользователь %s (%s) запустил бота",
        message.from_user.full_name if message.from_user else "?",
        message.from_user.id if message.from_user else "?",
    )


@router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    """Команда /panel — показать выбор панели."""
    await message.answer(
        "<b>Выберите панель:</b>",
        reply_markup=_start_kb(),
    )
    logger.info(
        "Пользователь %s (%s) открыл /panel",
        message.from_user.full_name if message.from_user else "?",
        message.from_user.id if message.from_user else "?",
    )


@router.callback_query(F.data == "open:dev")
async def open_dev_panel(callback: CallbackQuery) -> None:
    """Открыть панель разработчика из стартового меню."""
    from src.bot.keyboards.dev_panel import dev_main_menu_kb
    from src.core.config import Settings

    admin_tg_id = Settings().admin_tg_id
    tg_id = callback.from_user.id if callback.from_user else None
    is_admin = tg_id == admin_tg_id

    await callback.answer()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Панель разработчика</b>\n\nВыберите раздел:",
        reply_markup=dev_main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "open:manager")
async def open_manager_panel(callback: CallbackQuery, **kwargs: object) -> None:
    """Открыть панель менеджера из стартового меню."""
    from src.core.models import TeamRole
    from src.bot.keyboards.manager_panel import manager_main_menu_kb

    member = kwargs.get("member")
    if member and member.role == TeamRole.developer:
        await callback.answer(
            "Панель менеджера доступна только менеджерам.", show_alert=True,
        )
        return

    is_paused = False
    redis_client = kwargs.get("redis_client")
    if redis_client:
        is_paused = await redis_client.is_parser_paused()

    await callback.answer()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Панель менеджера</b>\n\nВыберите раздел:",
        reply_markup=manager_main_menu_kb(is_paused=is_paused),
    )
