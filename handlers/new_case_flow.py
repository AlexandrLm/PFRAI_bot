# pfr_bot/handlers/new_case_flow.py

import logging
import re
import httpx
from telegram import Update, Message
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler,
)
from datetime import datetime

from states import MainMenu, NewCaseFlow
from keyboards import (
    pension_types_keyboard,
    skip_keyboard,
    gender_keyboard,
    confirm_case_keyboard,
    after_creation_keyboard,
    yes_no_keyboard,
    disability_group_keyboard,
    ocr_suggestion_keyboard,
    standard_documents_keyboard,
)
from api_client import api_client, ApiClientError, TaskTimeoutError
from .start import back_to_main_menu

logger = logging.getLogger(__name__)


# --- Вспомогательные функции (без изменений) ---
def format_case_data(data: dict) -> str:
    """Форматирует собранные данные для подтверждения."""
    pd = data.get("personal_data", {})
    we = data.get("work_experience", {})
    dis = data.get("disability")
    nc = pd.get("name_change_info")
    submitted_docs = data.get("submitted_documents_names", [])

    lines = [
        "Пожалуйста, проверьте введенные данные:",
        "---",
        f"Тип пенсии: {data.get('pension_type_name', 'Не указан')}",
        "\n*Персональные данные:*",
        f"  ФИО: {pd.get('last_name', '...')} {pd.get('first_name', '...')} {pd.get('middle_name', '') or ''}",
        f"  Дата рождения: {pd.get('birth_date', '...')}",
        f"  СНИЛС: {pd.get('snils', '...')}",
        f"  Пол: {pd.get('gender', '...')}",
        f"  Гражданство: {pd.get('citizenship', '...')}",
        f"  Иждивенцы: {pd.get('dependents', '...')}",
    ]
    if nc and nc.get("old_full_name"):
        lines.extend(
            [
                "\n*Смена ФИО:*",
                f"  Предыдущее ФИО: {nc.get('old_full_name', '...')}",
                f"  Дата смены: {nc.get('date_changed', '...')}",
            ]
        )
    if dis and dis.get("group"):
        lines.extend(
            [
                "\n*Инвалидность:*",
                f"  Группа: {dis.get('group', '...')}",
                f"  Дата установления: {dis.get('date', '...')}",
                f"  Номер справки: {dis.get('cert_number', '...')}",
            ]
        )
    lines.extend(
        [
            "\n*Работа и баллы:*",
            f"  Общий стаж (лет): {we.get('total_years', '...')}",
            f"  Пенсионные баллы (ИПК): {data.get('pension_points', '...')}",
            f"  Заявленные льготы: {', '.join(data.get('benefits', [])) or 'Нет'}",
            f"  Есть некорректные документы: {'Да' if data.get('has_incorrect_document') else 'Нет'}",
        ]
    )
    if submitted_docs:
        lines.append("\n*Предоставленные документы:*")
        for doc_name in submitted_docs:
            lines.append(f"  - {doc_name}")
            
    lines.append("---")
    return "\n".join(lines)


def update_context_with_ocr_data(
    context: ContextTypes.DEFAULT_TYPE, doc_type: str, ocr_data: dict
):
    if not ocr_data:
        return
    pd = context.user_data["case_data"]["personal_data"]
    we = context.user_data["case_data"]["work_experience"]
    if doc_type == "passport":
        pd["last_name"] = ocr_data.get("last_name") or pd.get("last_name")
        pd["first_name"] = ocr_data.get("first_name") or pd.get("first_name")
        pd["middle_name"] = ocr_data.get("middle_name") or pd.get("middle_name")
        pd["birth_date"] = ocr_data.get("birth_date") or pd.get("birth_date")
        sex = (ocr_data.get("sex") or "").upper()
        if "МУЖ" in sex:
            pd["gender"] = "Мужской"
        elif "ЖЕН" in sex:
            pd["gender"] = "Женский"
    elif doc_type == "snils":
        if ocr_data.get("snils_number"):
            pd["snils"] = re.sub(r"[\s-]", "", ocr_data["snils_number"])
    elif doc_type == "work_book":
        if ocr_data.get("calculated_total_years") is not None:
            we["total_years"] = int(ocr_data["calculated_total_years"])
        if ocr_data.get("records"):
            we["records"] = ocr_data["records"]


def create_progress_callback(message: Message):
    async def progress_callback(laps: int):
        indicators = ["⏳", "⌛"]
        try:
            await message.edit_text(f"Обработка... {indicators[laps % 2]}")
        except Exception:
            pass

    return progress_callback


async def handle_api_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    e: Exception,
    fallback_state: int,
) -> int:
    # Определяем, куда отправлять ответное сообщение
    reply_target = (
        update.callback_query.message if update.callback_query else update.message
    )
    error_message = "Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже."

    if isinstance(e, TaskTimeoutError):
        error_message = "Обработка занимает слишком много времени. Попробуйте позже или введите данные вручную."
    elif isinstance(e, ApiClientError):
        error_message = f"Ошибка клиента API: {e}"
    elif isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 422:
            try:
                details = e.response.json().get("detail", [])
                if details and isinstance(details, list):
                    error_lines = [
                        f"Ошибка валидации данных (код: {e.response.status_code}):"
                    ]
                    for err in details:
                        field_path = ".".join(map(str, err.get("loc", ["unknown"])[1:]))
                        error_lines.append(f"  - Поле '{field_path}': {err.get('msg')}")
                    error_message = "\n".join(error_lines)
                else:
                    error_message = f"Ошибка валидации (код: {e.response.status_code}): {e.response.text}"
            except Exception:
                error_message = f"Ошибка валидации (код: {e.response.status_code}). Не удалось разобрать детали."
        else:
            error_message = (
                f"Ошибка сервера (код: {e.response.status_code}). Попробуйте позже."
            )

    logger.error(f"Ошибка API в диалоге new_case: {e}", exc_info=True)
    await reply_target.reply_text(error_message)
    if fallback_state:
        return fallback_state
    return await cancel_case(update, context)


async def start_ocr_in_new_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переходит в состояние ожидания фото для OCR внутри диалога создания дела."""
    query = update.callback_query
    await query.answer()

    doc_type_map = {
        "passport": "паспорта",
        "snils": "СНИЛС",
        "work_book": "трудовой книжки",
    }
    
    doc_type = context.user_data.get("current_ocr_type")
    doc_name = doc_type_map.get(doc_type, "документа")
    
    state_to_return = context.user_data.get("current_ocr_state")

    await query.edit_message_text(
        f"Пожалуйста, загрузите фото или скан-копию документа ({doc_name})."
    )
    
    return state_to_return


async def handle_ocr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    doc_type = context.user_data.get("current_ocr_type")
    next_state_func_on_success = context.user_data.get("ocr_return_func")
    current_ocr_state = context.user_data.get("current_ocr_state")

    state_fallback_map = {
        NewCaseFlow.AWAIT_PASSPORT_PHOTO: NewCaseFlow.LAST_NAME,
        NewCaseFlow.AWAIT_SNILS_PHOTO: NewCaseFlow.SNILS,
        NewCaseFlow.AWAIT_WORK_BOOK_PHOTO: NewCaseFlow.WORK_EXPERIENCE_TOTAL,
    }
    fallback_state = state_fallback_map.get(current_ocr_state)

    if not all([doc_type, next_state_func_on_success, fallback_state]):
        await message.reply_text(
            "Произошла внутренняя ошибка. Пожалуйста, начните заново."
        )
        return await cancel_case(update, context)

    if message.document:
        file = await message.document.get_file()
        filename = message.document.file_name
    elif message.photo:
        file = await message.photo[-1].get_file()
        filename = f"{file.file_id}.jpg"
    else:
        await message.reply_text("Пожалуйста, отправьте изображение или PDF-файл.")
        return current_ocr_state

    ocr_doc_name_map = {
        "passport": "паспорт",
        "snils": "СНИЛС",
        "work_book": "трудовую книжку",
    }
    progress_message = await message.reply_text(
        f"Файл получен. Распознаю {ocr_doc_name_map.get(doc_type, 'документ')}..."
    )

    file_bytes = await file.download_as_bytearray()

    try:
        task_resp = await api_client.submit_ocr_task(
            doc_type, bytes(file_bytes), filename
        )
        task_id = task_resp["task_id"]

        status_resp = await api_client.get_ocr_task_status(
            task_id, progress_callback=create_progress_callback(progress_message)
        )

        if status_resp["status"] == "COMPLETED":
            await progress_message.edit_text("✅ Распознавание завершено!")
            update_context_with_ocr_data(context, doc_type, status_resp["data"])
            return await next_state_func_on_success(update, context)
        else:
            err = status_resp.get("error", {}).get("detail", "Неизвестная ошибка.")
            await progress_message.edit_text(
                f"❌ Ошибка распознавания: {err}\nПожалуйста, введите данные вручную."
            )
            return fallback_state

    except (ApiClientError, httpx.RequestError, TaskTimeoutError) as e:
        await progress_message.delete()
        return await handle_api_error(update, context, e, fallback_state)
    except Exception as e:
        await progress_message.delete()
        logger.error(f"Непредвиденная ошибка в handle_ocr_photo: {e}", exc_info=True)
        await message.reply_text(
            "Произошла критическая ошибка. Пожалуйста, введите данные вручную."
        )
        return fallback_state


# --- ИСПРАВЛЕННЫЕ ФУНКЦИИ-ПЕРЕХОДНИКИ ---


async def send_new_question(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None
) -> None:
    """Надежно отправляет новое сообщение с вопросом."""
    chat_id = update.effective_chat.id
    # Если это был ответ на кнопку, удаляем старое сообщение с кнопками
    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")
    await context.bot.send_message(chat_id, text, reply_markup=reply_markup)


async def proceed_to_snils(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_new_question(
        update,
        context,
        "Данные из паспорта учтены. Теперь нужен номер СНИЛС.",
        ocr_suggestion_keyboard("ocr_snils", "skip_ocr_snils"),
    )
    return NewCaseFlow.SNILS


async def proceed_to_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pd = context.user_data["case_data"]["personal_data"]
    if pd.get("gender"):
        # Пол уже есть из паспорта, пропускаем шаг
        return await proceed_to_citizenship(update, context)

    await send_new_question(
        update, context, "Номер СНИЛС распознан. Выберите ваш пол:", gender_keyboard()
    )
    return NewCaseFlow.GENDER


async def proceed_to_citizenship(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await send_new_question(update, context, "Введите ваше гражданство (например, РФ):")
    return NewCaseFlow.CITIZENSHIP


async def proceed_to_dependents(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await update.message.reply_text("Введите количество иждивенцев (цифрой):")
    return NewCaseFlow.DEPENDENTS


async def proceed_to_work_experience(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await send_new_question(
        update,
        context,
        "Теперь укажите ваш общий трудовой стаж.",
        ocr_suggestion_keyboard("ocr_work_book", "skip_ocr_work_book"),
    )
    return NewCaseFlow.WORK_EXPERIENCE_TOTAL


async def proceed_to_pension_points(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await send_new_question(
        update,
        context,
        "Введите количество ваших пенсионных баллов (ИПК), можно дробное число:",
    )
    return NewCaseFlow.PENSION_POINTS


async def proceed_to_disability_check(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await send_new_question(
        update,
        context,
        "У вас есть установленная группа инвалидности?",
        yes_no_keyboard("disability_yes", "disability_no"),
    )
    return NewCaseFlow.ASK_DISABILITY


async def proceed_to_name_change_check(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await send_new_question(
        update,
        context,
        "Вы меняли ФИО?",
        yes_no_keyboard("name_change_yes", "name_change_no"),
    )
    return NewCaseFlow.ASK_NAME_CHANGE


async def proceed_to_benefits(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await send_new_question(
        update,
        context,
        "Перечислите через запятую коды или названия ваших льгот (например: ветеран труда, северный стаж). Если нет, пропустите.",
        skip_keyboard(),
    )
    return NewCaseFlow.GET_BENEFITS


async def proceed_to_incorrect_docs_check(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Переходит к вопросу о некорректно оформленных документах."""
    # Вместо перехода к подтверждению, переходим к выбору документов
    return await ask_standard_documents(update, context)


async def proceed_to_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    summary = format_case_data(context.user_data["case_data"])
    await send_new_question(
        update,
        context,
        summary,
        confirm_case_keyboard(),
    )
    return NewCaseFlow.CONFIRM_CREATION


async def back_to_new_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Возвращает пользователя в диалог создания дела после OCR."""
    # Восстанавливаем данные OCR в основной диалог.
    # Это может быть полезно для автозаполнения.
    if context.user_data.get("ocr_data"):
        # Логика автозаполнения может быть расширена здесь
        pass

    # Просто возвращаемся к последнему шагу, на котором мы были
    await send_new_question(
        update,
        context,
        "Возвращаемся к созданию дела. Нажмите 'Готово', когда закончите с документами.",
        reply_markup=standard_documents_keyboard(
            context.user_data.get("standard_documents_list", []),
            context.user_data.get("selected_documents", []),
        ),
    )
    return NewCaseFlow.GET_STANDARD_DOCS


# --- Шаги сбора данных (логика без изменений, только вызовы proceed_to_... ) ---


async def case_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["case_data"] = {
        "personal_data": {"name_change_info": {}},
        "work_experience": {},
        "benefits": [],
        "disability": {},
        "submitted_documents": [],
        "has_incorrect_document": False,
        "other_documents_extracted_data": [],
    }
    try:
        pension_types = await api_client.get_pension_types()
        context.user_data["pension_types_list"] = pension_types
        await query.edit_message_text(
            "Давайте начнем. Выберите тип пенсии:",
            reply_markup=pension_types_keyboard(pension_types),
        )
        return NewCaseFlow.PENSION_TYPE
    except (ApiClientError, httpx.RequestError) as e:
        return await handle_api_error(update, context, e, None)


async def get_pension_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pt_id = query.data.replace("pt_", "")
    pt_list = context.user_data.get("pension_types_list", [])
    pt_name = next(
        (pt["display_name"] for pt in pt_list if pt["id"] == pt_id), "Неизвестный тип"
    )
    context.user_data["case_data"]["pension_type"] = pt_id
    context.user_data["case_data"]["pension_type_name"] = pt_name
    await query.edit_message_text(
        "Отлично. Теперь нужно ввести ваши персональные данные. "
        "Хотите, я попробую заполнить их с фото вашего паспорта?",
        reply_markup=ocr_suggestion_keyboard("ocr_passport", "skip_ocr_passport"),
    )
    return NewCaseFlow.LAST_NAME


async def get_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает фамилию и предлагает распознать паспорт."""
    # Сохраняем предыдущий ответ (тип пенсии)
    query = update.callback_query
    pension_type_id = query.data.replace("pt_", "")
    
    # Находим имя типа пенсии для отображения
    pension_types = context.user_data.get("pension_types_list", [])
    pension_type_name = next(
        (pt["display_name"] for pt in pension_types if pt["id"] == pension_type_id),
        pension_type_id,
    )
    
    context.user_data["case_data"]["pension_type"] = pension_type_id
    context.user_data["case_data"]["pension_type_name"] = pension_type_name

    # Настраиваем OCR
    context.user_data["current_ocr_type"] = "passport"
    context.user_data["current_ocr_state"] = NewCaseFlow.AWAIT_PASSPORT_PHOTO
    context.user_data["ocr_return_func"] = proceed_to_first_name

    await send_new_question(
        update,
        context,
        "Введите вашу фамилию или загрузите фото паспорта для автозаполнения.",
        reply_markup=ocr_suggestion_keyboard(
            ocr_callback="start_ocr", skip_callback="skip_ocr"
        ),
    )
    return NewCaseFlow.LAST_NAME


async def get_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает имя."""
    if update.message:
        context.user_data["case_data"]["personal_data"][
            "last_name"
        ] = update.message.text
    # Используем send_new_question для чистоты диалога
    await send_new_question(update, context, "Введите ваше имя:")
    return NewCaseFlow.FIRST_NAME


async def get_middle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает отчество."""
    context.user_data["case_data"]["personal_data"]["first_name"] = update.message.text
    # Используем send_new_question
    await send_new_question(
        update, context, "Введите ваше отчество (или пропустите):", reply_markup=skip_keyboard()
    )
    return NewCaseFlow.MIDDLE_NAME


async def get_birth_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает дату рождения."""
    if update.callback_query and update.callback_query.data == "skip_step":
        context.user_data["case_data"]["personal_data"]["middle_name"] = None
    elif update.message:
        context.user_data["case_data"]["personal_data"][
            "middle_name"
        ] = update.message.text

    await send_new_question(update, context, "Введите вашу дату рождения в формате ДД.ММ.ГГГГ.")
    return NewCaseFlow.BIRTH_DATE


async def skip_middle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["case_data"]["personal_data"]["middle_name"] = None
    await query.edit_message_text("Введите дату рождения в формате ДД.ММ.ГГГГ:")
    return NewCaseFlow.BIRTH_DATE


async def get_snils(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "ocr_snils":
            context.user_data.update(
                {
                    "current_ocr_type": "snils",
                    "ocr_return_func": proceed_to_gender,
                    "current_ocr_state": NewCaseFlow.AWAIT_SNILS_PHOTO,
                }
            )
            await query.edit_message_text("Пожалуйста, загрузите фото вашего СНИЛС.")
            return NewCaseFlow.AWAIT_SNILS_PHOTO
        elif query.data == "skip_ocr_snils":
            await query.edit_message_text("Хорошо, введите номер СНИЛС (11 цифр):")
            return NewCaseFlow.SNILS

    snils = re.sub(r"[\s-]", "", update.message.text)
    if not snils.isdigit() or len(snils) != 11:
        await update.message.reply_text(
            "СНИЛС должен содержать 11 цифр. Попробуйте еще раз:"
        )
        return NewCaseFlow.SNILS
    context.user_data["case_data"]["personal_data"]["snils"] = snils
    return await proceed_to_gender(update, context)


async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает пол."""
    if update.message:
        snils = re.sub(r"[\s-]", "", update.message.text)
        if not snils.isdigit() or len(snils) != 11:
            await update.message.reply_text("СНИЛС должен содержать 11 цифр. Попробуйте еще раз.")
            return NewCaseFlow.SNILS
        context.user_data["case_data"]["personal_data"]["snils"] = snils
    await send_new_question(update, context, "Выберите ваш пол.", reply_markup=gender_keyboard())
    return NewCaseFlow.GENDER


async def get_citizenship(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает гражданство."""
    query = update.callback_query
    gender = query.data.replace("gender_", "")
    context.user_data["case_data"]["personal_data"]["gender"] = gender
    await send_new_question(update, context, "Введите ваше гражданство.")
    return NewCaseFlow.CITIZENSHIP


async def get_dependents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает количество иждивенцев."""
    context.user_data["case_data"]["personal_data"]["citizenship"] = update.message.text
    # Используем send_new_question
    await send_new_question(update, context, "Введите количество иждивенцев (цифрой).")
    return NewCaseFlow.DEPENDENTS


async def get_work_experience(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Получает стаж работы."""
    try:
        dependents = int(update.message.text)
        if dependents < 0:
            raise ValueError
        context.user_data["case_data"]["personal_data"]["dependents"] = dependents
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите целое неотрицательное число."
        )
        return NewCaseFlow.DEPENDENTS
    
    context.user_data["current_ocr_type"] = "work_book"
    context.user_data["current_ocr_state"] = NewCaseFlow.AWAIT_WORK_BOOK_PHOTO
    context.user_data["ocr_return_func"] = proceed_to_pension_points
    
    await send_new_question(
        update,
        context,
        "Введите ваш общий трудовой стаж в годах или загрузите фото трудовой книжки.",
        reply_markup=ocr_suggestion_keyboard("start_ocr", "skip_ocr"),
    )
    return NewCaseFlow.WORK_EXPERIENCE_TOTAL


async def get_pension_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает пенсионные баллы."""
    we = context.user_data["case_data"]["work_experience"]
    if update.message:
        try:
            we["total_years"] = int(update.message.text)
        except (ValueError, TypeError):
            await update.message.reply_text("Пожалуйста, введите стаж в виде целого числа.")
            return NewCaseFlow.WORK_EXPERIENCE_TOTAL
    elif update.callback_query and context.user_data.get("ocr_data"):
        ocr_data = context.user_data.pop("ocr_data")
        update_context_with_ocr_data(context, "work_book", ocr_data)

    await send_new_question(
        update, context, "Введите количество ваших пенсионных баллов (ИПК)."
    )
    return NewCaseFlow.PENSION_POINTS


async def ask_disability(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "disability_yes":
        await query.edit_message_text(
            "Выберите группу инвалидности:", reply_markup=disability_group_keyboard()
        )
        return NewCaseFlow.GET_DISABILITY_GROUP
    else:
        context.user_data["case_data"]["disability"] = None
        return await proceed_to_name_change_check(update, context)


async def get_disability_group(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    group = query.data.replace("dis_group_", "")
    context.user_data["case_data"]["disability"]["group"] = group
    await query.edit_message_text(
        "Введите дату установления инвалидности (ДД.ММ.ГГГГ):"
    )
    return NewCaseFlow.GET_DISABILITY_DATE


async def get_disability_date(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    try:
        dis_date = datetime.strptime(update.message.text, "%d.%m.%Y").strftime(
            "%Y-%m-%d"
        )
        context.user_data["case_data"]["disability"]["date"] = dis_date
        await update.message.reply_text(
            "Введите номер справки МСЭ (или пропустите):", reply_markup=skip_keyboard()
        )
        return NewCaseFlow.GET_DISABILITY_CERT
    except ValueError:
        await update.message.reply_text(
            "Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:"
        )
        return NewCaseFlow.GET_DISABILITY_DATE


async def get_disability_cert(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.user_data["case_data"]["disability"]["cert_number"] = update.message.text
    return await proceed_to_name_change_check(update, context)


async def skip_disability_cert(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["case_data"]["disability"]["cert_number"] = None
    return await proceed_to_name_change_check(update, context)


async def ask_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "name_change_yes":
        await query.edit_message_text("Введите предыдущее полное ФИО:")
        return NewCaseFlow.GET_OLD_FULL_NAME
    else:
        context.user_data["case_data"]["personal_data"]["name_change_info"] = None
        return await proceed_to_benefits(update, context)


async def get_old_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["case_data"]["personal_data"]["name_change_info"][
        "old_full_name"
    ] = update.message.text
    await update.message.reply_text("Введите дату смены ФИО (ДД.ММ.ГГГГ):")
    return NewCaseFlow.GET_NAME_CHANGE_DATE


async def get_name_change_date(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    try:
        change_date = datetime.strptime(update.message.text, "%d.%m.%Y").strftime(
            "%Y-%m-%d"
        )
        context.user_data["case_data"]["personal_data"]["name_change_info"][
            "date_changed"
        ] = change_date
        return await proceed_to_benefits(update, context)
    except ValueError:
        await update.message.reply_text(
            "Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:"
        )
        return NewCaseFlow.GET_NAME_CHANGE_DATE


async def get_benefits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    benefits_list = [b.strip() for b in update.message.text.split(",")]
    context.user_data["case_data"]["benefits"] = benefits_list
    return await proceed_to_incorrect_docs_check(update, context)


async def skip_benefits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["case_data"]["benefits"] = []
    return await proceed_to_incorrect_docs_check(update, context)


async def get_incorrect_doc_flag(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Получает флаг о некорректно оформленных документах."""
    query = update.callback_query
    await query.answer()

    if query.data == "yes_incorrect_docs":
        context.user_data["case_data"]["has_incorrect_document"] = True
    else:
        context.user_data["case_data"]["has_incorrect_document"] = False

    # Переход к новому шагу
    return await ask_standard_documents(update, context)


async def ask_standard_documents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрашивает у пользователя предоставленные стандартные документы."""
    pension_type_id = context.user_data.get("case_data", {}).get("pension_type")
    if not pension_type_id:
        await send_new_question(
            update, context, "Не удалось определить тип пенсии. Начните заново."
        )
        return await cancel_case(update, context)

    try:
        documents = await api_client.get_pension_documents(pension_type_id)
        if not documents:
            await send_new_question(
                update, context, "Для данного типа пенсии нет стандартных документов. Перехожу к подтверждению."
            )
            return await proceed_to_confirmation(update, context)

        context.user_data["standard_documents_list"] = documents
        context.user_data["selected_documents"] = []

        await send_new_question(
            update,
            context,
            "Отметьте, какие из стандартных документов вы предоставляете:",
            reply_markup=standard_documents_keyboard(documents, []),
        )
        return NewCaseFlow.GET_STANDARD_DOCS
    except (ApiClientError, httpx.RequestError) as e:
        # При ошибке API пропускаем этот шаг и идем к подтверждению
        logger.error(f"Не удалось получить список документов для {pension_type_id}: {e}")
        await send_new_question(
            update, context, "Не удалось загрузить список документов. Этот шаг будет пропущен."
        )
        return await proceed_to_confirmation(update, context)


async def handle_standard_document_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Обрабатывает выбор/снятие выбора документа."""
    query = update.callback_query
    await query.answer()

    doc_id = query.data.replace("std_doc_", "")
    selected = context.user_data.get("selected_documents", [])

    if doc_id in selected:
        selected.remove(doc_id)
    else:
        selected.append(doc_id)

    context.user_data["selected_documents"] = selected

    documents = context.user_data.get("standard_documents_list", [])
    await query.edit_message_reply_markup(
        reply_markup=standard_documents_keyboard(documents, selected)
    )
    return NewCaseFlow.GET_STANDARD_DOCS


async def finish_standard_document_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Завершает выбор документов и переходит к подтверждению."""
    query = update.callback_query
    await query.answer()

    selected_ids = context.user_data.get("selected_documents", [])
    all_docs = context.user_data.get("standard_documents_list", [])

    context.user_data["case_data"]["submitted_documents"] = selected_ids
    context.user_data["case_data"]["submitted_documents_names"] = [
        doc["name"] for doc in all_docs if doc["id"] in selected_ids
    ]

    context.user_data.pop("standard_documents_list", None)
    context.user_data.pop("selected_documents", None)

    return await proceed_to_confirmation(update, context)


async def confirm_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    progress_message = await query.edit_message_text("Отправляю данные на обработку...")
    try:
        data_to_send = context.user_data["case_data"].copy()
        data_to_send.pop("pension_type_name", None)
        if not data_to_send.get("disability"):
            data_to_send["disability"] = None
        if not data_to_send.get("personal_data", {}).get("name_change_info"):
            if "name_change_info" in data_to_send.get("personal_data", {}):
                data_to_send["personal_data"]["name_change_info"] = None

        response = await api_client.create_case(data_to_send)
        case_id = response.get("case_id")
        if not case_id:
            await progress_message.edit_text(
                "❌ Не удалось создать дело: не получен ID."
            )
            return ConversationHandler.END

        await progress_message.edit_text(
            f"✅ Ваше дело №{case_id} принято. Идет анализ данных..."
        )

        status_response = await api_client.get_case_status(
            case_id, progress_callback=create_progress_callback(progress_message)
        )

        final_status = status_response.get("final_status")
        explanation = status_response.get("explanation", "Нет объяснения.")
        confidence = status_response.get("confidence_score")
        result_text = f"Итог по делу №{case_id}:\n\n*Статус:* {final_status}\n\n"
        if confidence is not None:
            result_text += f"*Уверенность системы:* {confidence*100:.1f}%\n\n"
        result_text += f"*Объяснение:*\n{explanation}"

        await progress_message.edit_text(
            text=result_text,
            reply_markup=after_creation_keyboard(),
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return NewCaseFlow.CONFIRM_CREATION

    except (ApiClientError, httpx.RequestError, TaskTimeoutError) as e:
        await progress_message.delete()
        return await handle_api_error(update, context, e, None)
    except Exception as e:
        await progress_message.delete()
        logger.error(f"Критическая ошибка в confirm_creation: {e}", exc_info=True)
        await query.message.reply_text(
            "❌ Произошла критическая ошибка. Пожалуйста, попробуйте позже."
        )
        context.user_data.clear()
        return ConversationHandler.END


async def handle_after_creation_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "new_case":
        return await case_start(update, context)
    elif query.data == "main_menu":
        return await back_to_main_menu(update, context)


async def cancel_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    context.user_data.clear()
    return await back_to_main_menu(update, context)


new_case_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(case_start, pattern="^new_case$")],
    states={
        NewCaseFlow.PENSION_TYPE: [
            CallbackQueryHandler(get_pension_type, pattern="^pt_")
        ],
        NewCaseFlow.LAST_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_first_name),
            CallbackQueryHandler(get_last_name, pattern="^skip_ocr$"),
            CallbackQueryHandler(start_ocr_in_new_case, pattern="^start_ocr$"),
        ],
        NewCaseFlow.AWAIT_PASSPORT_PHOTO: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, handle_ocr_photo)
        ],
        NewCaseFlow.FIRST_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_middle_name)
        ],
        NewCaseFlow.MIDDLE_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_birth_date),
            CallbackQueryHandler(skip_middle_name, pattern="^skip_step$"),
        ],
        NewCaseFlow.BIRTH_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_snils)
        ],
        NewCaseFlow.SNILS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_gender),
            CallbackQueryHandler(get_snils, pattern="^skip_ocr$"),
            CallbackQueryHandler(start_ocr_in_new_case, pattern="^start_ocr$"),
        ],
        NewCaseFlow.AWAIT_SNILS_PHOTO: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, handle_ocr_photo)
        ],
        NewCaseFlow.GENDER: [CallbackQueryHandler(get_citizenship, pattern="^gender_")],
        NewCaseFlow.CITIZENSHIP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_dependents)
        ],
        NewCaseFlow.DEPENDENTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_work_experience)
        ],
        NewCaseFlow.WORK_EXPERIENCE_TOTAL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_pension_points),
            CallbackQueryHandler(get_work_experience, pattern="^skip_ocr$"),
            CallbackQueryHandler(start_ocr_in_new_case, pattern="^start_ocr$"),
        ],
        NewCaseFlow.AWAIT_WORK_BOOK_PHOTO: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, handle_ocr_photo)
        ],
        NewCaseFlow.PENSION_POINTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_disability)
        ],
        # Блок инвалидности
        NewCaseFlow.ASK_DISABILITY: [
            CallbackQueryHandler(get_disability_group, pattern="^yes_disability$"),
            CallbackQueryHandler(ask_name_change, pattern="^no_disability$"),
        ],
        NewCaseFlow.GET_DISABILITY_GROUP: [
            CallbackQueryHandler(get_disability_date, pattern="^dis_group_")
        ],
        NewCaseFlow.GET_DISABILITY_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_disability_cert)
        ],
        NewCaseFlow.GET_DISABILITY_CERT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name_change),
            CallbackQueryHandler(skip_disability_cert, pattern="^skip_step$"),
        ],
        # Блок смены ФИО
        NewCaseFlow.ASK_NAME_CHANGE: [
            CallbackQueryHandler(get_old_full_name, pattern="^yes_name_change$"),
            CallbackQueryHandler(get_benefits, pattern="^no_name_change$"),
        ],
        NewCaseFlow.GET_OLD_FULL_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_change_date)
        ],
        NewCaseFlow.GET_NAME_CHANGE_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_benefits)
        ],
        NewCaseFlow.GET_BENEFITS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_incorrect_doc_flag),
            CallbackQueryHandler(skip_benefits, pattern="^skip_step$"),
        ],
        NewCaseFlow.GET_INCORRECT_DOC_FLAG: [
            CallbackQueryHandler(
                get_incorrect_doc_flag, pattern=r"^(yes|no)_incorrect_docs$"
            )
        ],
        NewCaseFlow.GET_STANDARD_DOCS: [
            CallbackQueryHandler(
                handle_standard_document_selection, pattern="^std_doc_(?!done)"
            ),
            CallbackQueryHandler(
                finish_standard_document_selection, pattern="^std_doc_done$"
            ),
        ],
        NewCaseFlow.CONFIRM_CREATION: [
            CallbackQueryHandler(confirm_creation, pattern="^confirm_case$"),
            CallbackQueryHandler(
                handle_after_creation_choice, pattern="^(new_case|main_menu)$"
            ),
        ],
    },
    fallbacks=[CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
               CommandHandler("start", back_to_main_menu),
               CallbackQueryHandler(cancel_case, pattern="^cancel_case$")],
)
