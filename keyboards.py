# pfr_bot/keyboards.py
from typing import List, Dict, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    buttons = [
        [InlineKeyboardButton("Создать новое дело", callback_data="new_case")],
        [InlineKeyboardButton("Распознать документ", callback_data="ocr_document")],
    ]
    return InlineKeyboardMarkup(buttons)

def ocr_document_type_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора типа документа для OCR."""
    buttons = [
        [InlineKeyboardButton("Паспорт", callback_data="ocr_type_passport")],
        [InlineKeyboardButton("СНИЛС", callback_data="ocr_type_snils")],
        [InlineKeyboardButton("Трудовая книжка", callback_data="ocr_type_work_book")],
        [InlineKeyboardButton("Другой документ", callback_data="ocr_type_other")],
        [InlineKeyboardButton("« Отмена", callback_data="cancel_ocr")],
    ]
    return InlineKeyboardMarkup(buttons)

def pension_types_keyboard(pension_types: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Динамическая клавиатура для выбора типа пенсии."""
    buttons = [
        [InlineKeyboardButton(pt['display_name'], callback_data=f"pt_{pt['id']}")]
        for pt in pension_types
    ]
    buttons.append([InlineKeyboardButton("« Отмена", callback_data="cancel_case")])
    return InlineKeyboardMarkup(buttons)

def skip_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Пропустить'."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="skip_step")]])

def gender_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора пола."""
    buttons = [
        [InlineKeyboardButton("Мужской", callback_data="gender_Мужской")],
        [InlineKeyboardButton("Женский", callback_data="gender_Женский")],
    ]
    return InlineKeyboardMarkup(buttons)

def confirm_case_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения создания дела."""
    buttons = [
        [InlineKeyboardButton("✅ Подтвердить и отправить", callback_data="confirm_case")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel_case")],
    ]
    return InlineKeyboardMarkup(buttons)

def after_creation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура, отображаемая после успешного получения результата по делу."""
    buttons = [
        [InlineKeyboardButton("Создать еще одно дело", callback_data="new_case")],
        [InlineKeyboardButton("В главное меню", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(buttons)

def yes_no_keyboard(yes_callback: str, no_callback: str) -> InlineKeyboardMarkup:
    """Универсальная клавиатура Да/Нет."""
    buttons = [
        [InlineKeyboardButton("Да", callback_data=yes_callback)],
        [InlineKeyboardButton("Нет", callback_data=no_callback)],
    ]
    return InlineKeyboardMarkup(buttons)

def disability_group_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора группы инвалидности."""
    buttons = [
        [
            InlineKeyboardButton("1", callback_data="dis_group_1"),
            InlineKeyboardButton("2", callback_data="dis_group_2"),
            InlineKeyboardButton("3", callback_data="dis_group_3"),
        ],
        [InlineKeyboardButton("Ребенок-инвалид", callback_data="dis_group_child")],
    ]
    return InlineKeyboardMarkup(buttons)

# --- НОВАЯ КЛАВИАТУРА ---
def ocr_suggestion_keyboard(ocr_callback: str, skip_callback: str) -> InlineKeyboardMarkup:
    """Клавиатура с предложением загрузить фото для OCR."""
    buttons = [
        [InlineKeyboardButton("📄 Загрузить фото", callback_data=ocr_callback)],
        [InlineKeyboardButton("Пропустить и ввести вручную", callback_data=skip_callback)],
    ]
    return InlineKeyboardMarkup(buttons)
# --- КОНЕЦ НОВОЙ КЛАВИАТУРЫ ---