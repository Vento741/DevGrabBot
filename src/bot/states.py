"""FSM-состояния для Telegram-бота."""
from aiogram.fsm.state import State, StatesGroup


class ReviewStates(StatesGroup):
    """Состояния редактирования отклика разработчиком."""

    editing_price = State()
    editing_timeline = State()
    editing_stack = State()
    editing_custom = State()
    editing_response_draft = State()


class DevPanelStates(StatesGroup):
    """Состояния панели разработчика."""

    # Мой стек
    editing_primary_stack = State()
    editing_secondary_stack = State()

    # Стоп-слова
    adding_stop_word = State()

    # Промпты
    selecting_prompt = State()
    editing_prompt = State()

    # Команда
    adding_member_tg_id = State()
    adding_member_role = State()
    adding_member_name = State()

    # Настройки
    editing_setting_value = State()


class ManagerPanelStates(StatesGroup):
    """Состояния панели менеджера."""

    # Стиль откликов
    editing_style_tone = State()
    editing_style_intro = State()
    editing_style_rules = State()

    # Профиль
    editing_profile_name = State()
    editing_profile_signature = State()
    editing_profile_contacts = State()

    # Редактирование отклика
    editing_response_text = State()

    # Поиск заявки
    searching_order = State()


class PlatformFilterStates(StatesGroup):
    """Состояния ввода значений фильтров платформ (Phase 4 v2.5)."""

    waiting_budget = State()               # "min max" или "-" для unset
    waiting_age = State()                  # число часов (max_age_hours)
    waiting_min_age = State()              # число секунд (min_age_seconds)
    waiting_max_response_price = State()   # число рублей (profiru)
    waiting_min_hired_percent = State()    # число 0-100 (kwork)
    waiting_offers = State()               # "min max" (kwork)
    waiting_min_time_left_hours = State()  # число часов (kwork)
