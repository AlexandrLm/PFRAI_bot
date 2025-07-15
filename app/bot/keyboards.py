from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from datetime import datetime


def get_yes_no_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопками 'Да' и 'Нет'."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Да"),
        KeyboardButton(text="Нет"),
    )
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_data_input_method_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с выбором способа ввода данных."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Заполнить вручную"),
        KeyboardButton(text="Загрузить документы (OCR)"),
    )
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Создать новое дело"),
        KeyboardButton(text="Проверить статус дела"),
    )
    builder.row(KeyboardButton(text="Распознать документ"))
    # Можно добавить другие кнопки, например, "Мои дела" или "Помощь"
    # builder.row(KeyboardButton(text="Помощь"))
    return builder.as_markup(resize_keyboard=True)


def get_pension_types_keyboard(
    pension_types: list[dict],
) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру с выбором типа пенсии."""
    builder = InlineKeyboardBuilder()
    for p_type in pension_types:
        builder.row(
            InlineKeyboardButton(
                text=p_type["display_name"],
                callback_data=f"pension_type:{p_type['id']}",
            )
        )
    return builder.as_markup()


def get_skip_keyboard(text: str) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру с одной кнопкой для пропуска шага."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=text, callback_data="skip"))
    return builder.as_markup()


def get_gender_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора пола."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Мужской", callback_data="gender:male"),
        InlineKeyboardButton(text="Женский", callback_data="gender:female"),
    )
    return builder.as_markup()


def get_ocr_doc_type_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора типа документа для OCR."""
    buttons = {
        "Паспорт": "passport",
        "СНИЛС": "snils",
        "Трудовая книжка": "work_book",
        "Другой документ": "other",
    }
    builder = InlineKeyboardBuilder()
    for text, callback_data in buttons.items():
        builder.row(
            InlineKeyboardButton(text=text, callback_data=f"ocr_type:{callback_data}")
        )
    return builder.as_markup()


def get_main_menu_keyboard():
    """Возвращает клавиатуру главного меню."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🆕 Начать новое дело", callback_data="new_case")
    )
    builder.row(
        InlineKeyboardButton(text="🗂 Моя история дел", callback_data="case_history")
    )
    # TODO: Добавить кнопки для админа, когда будет реализована проверка ролей
    # builder.row(
    #     InlineKeyboardButton(text="⚙️ Управление документами RAG", callback_data="manage_rag")
    # )
    return builder.as_markup()


def get_document_upload_keyboard(
    required_docs: list, uploaded_docs: dict = None
) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для управления загрузкой документов.
    Динамически обновляет статус каждой кнопки.
    """
    if uploaded_docs is None:
        uploaded_docs = {}
        
    builder = InlineKeyboardBuilder()
    
    for doc in required_docs:
        doc_type = doc.get("ocr_type")
        doc_name = doc.get("name")
        
        status_icon = ""
        if doc_info := uploaded_docs.get(doc_type):
            status = doc_info.get("status")
            if status == "PROCESSING":
                status_icon = " ⏳"
            elif status == "COMPLETED":
                status_icon = " ✅"
            elif status == "FAILED":
                status_icon = " ❌"

        builder.row(
            InlineKeyboardButton(
                text=f"📸 {doc_name}{status_icon}",
                callback_data=f"upload_doc:{doc_type}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(
            text="📎 Загрузить другой документ", callback_data="upload_other_doc"
        )
    )
    
    # Кнопку "Далее" показываем, только если все критичные документы загружены
    all_critical_done = True
    for doc in required_docs:
        if doc.get('is_critical'):
            doc_type = doc.get("ocr_type")
            if doc_type not in uploaded_docs or uploaded_docs[doc_type].get("status") != "COMPLETED":
                all_critical_done = False
                break
    
    if all_critical_done:
         builder.row(
            InlineKeyboardButton(
                text="➡️ Далее", callback_data="docs_upload_next_step"
            )
        )
    else:
        # Можно добавить кнопку для пропуска, если это предусмотрено логикой
        builder.row(
             InlineKeyboardButton(
                text="✅ Пропустить и ввести данные вручную", callback_data="skip_doc_upload"
            )
        )

    return builder.as_markup()


def get_verification_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для верификации распознанных данных."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👍 Данные верны", callback_data="ocr_data_correct"),
        InlineKeyboardButton(text="✏️ Исправить вручную", callback_data="ocr_data_edit"),
    )
    return builder.as_markup()


def get_case_history_keyboard(cases: list, limit: int, current_offset: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для списка дел с пагинацией."""
    builder = InlineKeyboardBuilder()

    for case in cases:
        case_id = case.get('id')
        case_date = case.get('created_at')
        status = case.get('final_status', 'N/A')
        
        # Форматируем дату для более красивого вида
        try:
            date_obj = datetime.fromisoformat(case_date)
            formatted_date = date_obj.strftime('%d.%m.%Y')
        except (ValueError, TypeError):
            formatted_date = case_date

        builder.row(
            InlineKeyboardButton(
                text=f"🔹 Дело #{case_id} от {formatted_date} - Статус: {status}",
                callback_data=f"view_case:{case_id}"
            )
        )
        
    pagination_row = []
    # Кнопка "Назад"
    if current_offset > 0:
        prev_offset = max(0, current_offset - limit)
        pagination_row.append(
            InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"history_page:{prev_offset}")
        )
        
    # Кнопка "Вперед" - упрощенная логика
    if len(cases) == limit:
        next_offset = current_offset + limit
        pagination_row.append(
            InlineKeyboardButton(text="➡️ След.", callback_data=f"history_page:{next_offset}")
        )
        
    if pagination_row:
        builder.row(*pagination_row)

    return builder.as_markup()


def get_case_details_keyboard(case_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для детального просмотра дела."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 Скачать решение (PDF)", callback_data=f"download_doc:{case_id}_pdf"),
        InlineKeyboardButton(text="📄 Скачать решение (DOCX)", callback_data=f"download_doc:{case_id}_docx")
    )
    # Можно добавить кнопку для удаления, если это необходимо
    # builder.row(
    #     InlineKeyboardButton(text="🗑 Удалить дело", callback_data=f"delete_case:{case_id}")
    # )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад к истории", callback_data="case_history")
    )
    return builder.as_markup()


def get_check_ocr_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для проверки статуса OCR."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 Проверить готовность", callback_data="check_ocr_results"
        )
    )
    return builder.as_markup()


def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для подтверждения создания дела."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить и отправить", callback_data="confirm_creation"
        )
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_creation")
    )
    return builder.as_markup()
