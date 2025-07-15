"""Модуль обработки потока создания нового дела в Telegram-боте для ПФР.

Содержит ConversationHandler для сбора данных о новом пенсионном деле,
включая OCR-распознавание документов и взаимодействие с API.
"""

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
)
from datetime import datetime

from states import MainMenu, NewCaseFlow, OcrFlow
from keyboards import (
    pension_types_keyboard,
    skip_keyboard,
    gender_keyboard,
    confirm_case_keyboard,
    after_creation_keyboard,
    yes_no_keyboard,
    disability_group_keyboard,
    ocr_suggestion_keyboard,
)
from api_client import api_client, ApiClientError, TaskTimeoutError
from .start import back_to_main_menu

logger = logging.getLogger(__name__)


# --- Вспомогательные функции (без изменений) ---
def format_case_data(data: dict) -> str:
    """Форматирует собранные данные дела для подтверждения пользователем."""
    pd = data.get("personal_data", {})
    we = data.get("work_experience", {})
    dis = data.get("disability")
    nc = pd.get("name_change_info")

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
            "---",
        ]
    )
    return "\n".join(lines)


def update_context_with_ocr_data(
    context: ContextTypes.DEFAULT_TYPE, doc_type: str, ocr_data: dict
):
    """Обновляет контекст пользователя данными, извлеченными из OCR."""
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
    """Создает callback для обновления прогресса обработки."""
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
    """Обрабатывает ошибки API и возвращает fallback-состояние."""
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
            except (ValueError, KeyError):
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


async def handle_ocr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает фото для OCR-распознавания и обновляет данные."""
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

    try:
        file, filename = await extract_file_from_message(message)
        task_resp = await api_client.submit_ocr_task(
            doc_type, bytes(file.download_as_bytearray()), filename
        )
        task_id = task_resp["task_id"]

        status_resp = await api_client.get_ocr_task_status(
            task_id, progress_callback=create_progress_callback(message)
        )

        if status_resp["status"] == "COMPLETED":
            await message.edit_text("✅ Распознавание завершено!")
            update_context_with_ocr_data(context, doc_type, status_resp["data"])
            return await next_state_func_on_success(update, context)
        else:
            err = status_resp.get("error", {}).get("detail", "Неизвестная ошибка.")
            await message.edit_text(
                f"❌ Ошибка распознавания: {err}\nПожалуйста, введите данные вручную."
            )
            return fallback_state

    except (ApiClientError, httpx.RequestError, TaskTimeoutError) as e:
        await message.delete()
        return await handle_api_error(update, context, e, fallback_state)
    except Exception as e:
        await message.delete()
        logger.error(f"Непредвиденная ошибка в handle_ocr_photo: {e}", exc_info=True)
        await message.reply_text(
            "Произошла критическая ошибка. Пожалуйста, введите данные вручную."
        )
        return fallback_state


# --- ИСПРАВЛЕННЫЕ ФУНКЦИИ-ПЕРЕХОДНИКИ ---


async def send_new_question(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None
) -> None:
    """Отправляет новое сообщение с вопросом, удаляя предыдущее при необходимости."""
    chat_id = update.effective_chat.id
    # Если это был ответ на кнопку, удаляем старое сообщение с кнопками
    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")
    await context.bot.send_message(chat_id, text, reply_markup=reply_markup)


async def proceed_to_snils(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переходит к шагу ввода СНИЛС."""
    await send_new_question(
        update,
        context,
        "Данные из паспорта учтены. Теперь нужен номер СНИЛС.",
        ocr_suggestion_keyboard("ocr_snils", "skip_ocr_snils"),
    )
    return NewCaseFlow.SNILS


async def proceed_to_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переходит к шагу выбора пола."""
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
    """Переходит к шагу ввода гражданства."""
    await send_new_question(update, context, "Введите ваше гражданство (например, РФ):")
    return NewCaseFlow.CITIZENSHIP


async def proceed_to_dependents(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Переходит к шагу ввода количества иждивенцев."""
    await update.message.reply_text("Введите количество иждивенцев (цифрой):")
    return NewCaseFlow.DEPENDENTS


async def proceed_to_work_experience(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Переходит к шагу ввода трудового стажа."""
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
    """Переходит к шагу ввода пенсионных баллов."""
    await send_new_question(
        update,
        context,
        "Введите количество ваших пенсионных баллов (ИПК), можно дробное число:",
    )
    return NewCaseFlow.PENSION_POINTS


async def proceed_to_disability_check(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Переходит к проверке наличия инвалидности."""
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
    """Переходит к проверке смены ФИО."""
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
    """Переходит к шагу ввода льгот."""
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
    """Переходит к проверке наличия некорректных документов."""
    await send_new_question(
        update,
        context,
        "Есть ли среди представленных вами документов некорректно оформленные?",
        yes_no_keyboard("incorrect_docs_yes", "incorrect_docs_no"),
    )
    return NewCaseFlow.GET_INCORRECT_DOC_FLAG


async def proceed_to_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Переходит к подтверждению данных."""
    summary = format_case_data(context.user_data["case_data"])
    await send_new_question(
        update,
        context,
        summary,
        confirm_case_keyboard(),
    )
    return NewCaseFlow.CONFIRM_CREATION


async def get_required_documents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pt_id = context.user_data['case_data']['pension_type']
    try:
        docs = await api_client.get_pension_documents(pt_id)
        doc_list = '\n'.join([f"- {doc['name']}: {doc['description']}" for doc in docs])
        await update.message.reply_text(f"Для выбранного типа пенсии требуются документы:\n{doc_list}\n\nУкажите, какие из них у вас есть (через запятую, или 'все'):")
        return NewCaseFlow.GET_SUBMITTED_DOCS
    except Exception as e:
        return await handle_api_error(update, context, e, NewCaseFlow.GET_INCORRECT_DOC_FLAG)


async def get_submitted_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.lower()
    context.user_data['case_data']['submitted_documents'] = text.split(',') if text != 'все' else []  # Логика для 'все'
    return await proceed_to_confirmation(update, context)

async def back_to_new_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Логика возврата после OCR
    return NewCaseFlow.WORK_EXPERIENCE_TOTAL


# --- Шаги сбора данных (логика без изменений, только вызовы proceed_to_... ) ---


async def case_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс создания нового дела."""
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
    """Получает тип пенсии от пользователя."""
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
    """Получает фамилию пользователя."""
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "ocr_passport":
            context.user_data.update(
                {
                    "current_ocr_type": "passport",
                    "ocr_return_func": proceed_to_snils,
                    "current_ocr_state": NewCaseFlow.AWAIT_PASSPORT_PHOTO,
                }
            )
            await query.edit_message_text(
                "Пожалуйста, загрузите фото главной страницы паспорта."
            )
            return NewCaseFlow.AWAIT_PASSPORT_PHOTO
        elif query.data == "skip_ocr_passport":
            await query.edit_message_text("Хорошо, введите вашу фамилию:")
            return NewCaseFlow.LAST_NAME

    context.user_data["case_data"]["personal_data"]["last_name"] = update.message.text
    await update.message.reply_text("Введите ваше имя:")
    return NewCaseFlow.FIRST_NAME


async def get_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает имя пользователя."""
    context.user_data["case_data"]["personal_data"]["first_name"] = update.message.text
    await update.message.reply_text(
        "Введите ваше отчество (или пропустите):", reply_markup=skip_keyboard()
    )
    return NewCaseFlow.MIDDLE_NAME


async def get_middle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["case_data"]["personal_data"]["middle_name"] = update.message.text
    await update.message.reply_text("Введите дату рождения в формате ДД.ММ.ГГГГ:")
    return NewCaseFlow.BIRTH_DATE


async def skip_middle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["case_data"]["personal_data"]["middle_name"] = None
    await query.edit_message_text("Введите дату рождения в формате ДД.ММ.ГГГГ:")
    return NewCaseFlow.BIRTH_DATE


async def get_birth_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        birth_date = datetime.strptime(update.message.text, "%d.%m.%Y")
        if birth_date > datetime.now():
            raise ValueError("Date in future")
        context.user_data["case_data"]["personal_data"]["birth_date"] = (
            birth_date.strftime("%Y-%m-%d")
        )
        return await proceed_to_snils(update, context)
    except (ValueError, TypeError):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
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
    query = update.callback_query
    await query.answer()
    context.user_data["case_data"]["personal_data"]["gender"] = query.data.replace(
        "gender_", ""
    )
    return await proceed_to_citizenship(update, context)


async def get_citizenship(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["case_data"]["personal_data"]["citizenship"] = update.message.text
    return await proceed_to_dependents(update, context)


async def get_dependents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        dependents = int(update.message.text)
        if dependents < 0:
            raise ValueError
        context.user_data["case_data"]["personal_data"]["dependents"] = dependents
        return await proceed_to_work_experience(update, context)
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите целое неотрицательное число."
        )
        return NewCaseFlow.DEPENDENTS


async def get_work_experience(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "ocr_work_book":
            context.user_data.update(
                {
                    "current_ocr_type": "work_book",
                    "ocr_return_func": proceed_to_pension_points,
                    "current_ocr_state": NewCaseFlow.AWAIT_WORK_BOOK_PHOTO,
                }
            )
            await query.edit_message_text(
                "Загрузите фото всех страниц трудовой книжки с записями о работе."
            )
            return NewCaseFlow.AWAIT_WORK_BOOK_PHOTO
        elif query.data == "skip_ocr_work_book":
            await query.edit_message_text(
                "Введите ваш общий трудовой стаж (полных лет):"
            )
            return NewCaseFlow.WORK_EXPERIENCE_TOTAL

    try:
        total_years = int(update.message.text)
        if total_years < 0:
            raise ValueError
        context.user_data["case_data"]["work_experience"]["total_years"] = total_years
        return await proceed_to_pension_points(update, context)
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите целое неотрицательное число."
        )
        return NewCaseFlow.WORK_EXPERIENCE_TOTAL


async def get_pension_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        points = float(update.message.text.replace(",", "."))
        if points < 0:
            raise ValueError
        context.user_data["case_data"]["pension_points"] = points
        return await proceed_to_disability_check(update, context)
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите положительное число.")
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
    query = update.callback_query
    await query.answer()
    context.user_data["case_data"]["has_incorrect_document"] = (
        query.data == "incorrect_docs_yes"
    )
    return await proceed_to_confirmation(update, context)


async def send_case_data(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Отправляет данные дела на API и возвращает case_id."""
    data_to_send = context.user_data['case_data'].copy()
    data_to_send.pop("pension_type_name", None)
    if not data_to_send.get("disability"):
        data_to_send["disability"] = None
    if not data_to_send.get("personal_data", {}).get("name_change_info"):
        if "name_change_info" in data_to_send.get("personal_data", {}):
            data_to_send["personal_data"]["name_change_info"] = None

    response = await api_client.create_case(data_to_send)
    case_id = response.get('case_id')
    if not case_id:
        raise ValueError('Не удалось создать дело: не получен ID.')
    return case_id

async def poll_case_status(case_id: str, progress_message: Message) -> dict:
    """Опрашивает статус дела с прогрессом."""
    return await api_client.get_case_status(case_id, progress_callback=create_progress_callback(progress_message))

async def format_case_result(case_id: str, status_response: dict) -> str:
    """Форматирует результат анализа дела."""
    final_status = status_response.get('final_status')
    explanation = status_response.get('explanation', 'Нет объяснения.')
    confidence = status_response.get('confidence_score')
    result_text = f'Итог по делу №{case_id}:\n\n*Статус:* {final_status}\n\n'
    if confidence is not None:
        result_text += f'*Уверенность системы:* {confidence*100:.1f}%\n\n'
    result_text += f'*Объяснение:*\n{explanation}'
    return result_text

async def confirm_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждает создание дела, отправляет данные и получает результат."""
    query = update.callback_query
    await query.answer()
    progress_message = await query.edit_message_text('Отправляю данные на обработку...')
    try:
        case_id = await send_case_data(context)
        await progress_message.edit_text(f'✅ Ваше дело №{case_id} принято. Идет анализ данных...')
        status_response = await poll_case_status(case_id, progress_message)
        result_text = format_case_result(case_id, status_response)
        await progress_message.edit_text(text=result_text, reply_markup=after_creation_keyboard(), parse_mode='Markdown')
        context.user_data.clear()
        return NewCaseFlow.CONFIRM_CREATION
    except (ApiClientError, httpx.RequestError, TaskTimeoutError, ValueError) as e:
        await progress_message.delete()
        return await handle_api_error(update, context, e, None)
    except Exception as e:
        await progress_message.delete()
        logger.error(f'Критическая ошибка в confirm_creation: {e}', exc_info=True)
        await query.message.reply_text('❌ Произошла критическая ошибка. Пожалуйста, попробуйте позже.')
        context.user_data.clear()
        return ConversationHandler.END


async def handle_after_creation_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Обрабатывает выбор после создания дела."""
    query = update.callback_query
    await query.answer()
    if query.data == "new_case":
        return await case_start(update, context)
    elif query.data == "main_menu":
        return await back_to_main_menu(update, context)


async def cancel_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет создание дела и возвращается в главное меню."""
    query = update.callback_query
    if query:
        await query.answer()
    context.user_data.clear()
    return await back_to_main_menu(update, context)


async def extract_file_from_message(message: Message) -> tuple:
    """Извлекает файл из сообщения (photo или document)."""
    if message.document:
        file = await message.document.get_file()
        filename = message.document.file_name
    elif message.photo:
        file = await message.photo[-1].get_file()
        filename = f'{file.file_id}.jpg'
    else:
        raise ValueError('Пожалуйста, отправьте изображение или PDF-файл.')
    return file, filename


new_case_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(case_start, pattern="^new_case$")],
    states={
        NewCaseFlow.PENSION_TYPE: [
            CallbackQueryHandler(get_pension_type, pattern="^pt_")
        ],
        NewCaseFlow.LAST_NAME: [
            CallbackQueryHandler(
                get_last_name, pattern="^(ocr_passport|skip_ocr_passport)$"
            ),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_last_name),
        ],
        NewCaseFlow.FIRST_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_first_name)
        ],
        NewCaseFlow.MIDDLE_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_middle_name),
            CallbackQueryHandler(skip_middle_name, pattern="^skip_step$"),
        ],
        NewCaseFlow.BIRTH_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_birth_date)
        ],
        NewCaseFlow.SNILS: [
            CallbackQueryHandler(get_snils, pattern="^(ocr_snils|skip_ocr_snils)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_snils),
        ],
        NewCaseFlow.GENDER: [CallbackQueryHandler(get_gender, pattern="^gender_")],
        NewCaseFlow.CITIZENSHIP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_citizenship)
        ],
        NewCaseFlow.DEPENDENTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_dependents)
        ],
        NewCaseFlow.WORK_EXPERIENCE_TOTAL: [
            CallbackQueryHandler(
                get_work_experience, pattern="^(ocr_work_book|skip_ocr_work_book)$"
            ),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_work_experience),
        ],
        NewCaseFlow.PENSION_POINTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_pension_points)
        ],
        NewCaseFlow.ASK_DISABILITY: [
            CallbackQueryHandler(ask_disability, pattern="^disability_")
        ],
        NewCaseFlow.GET_DISABILITY_GROUP: [
            CallbackQueryHandler(get_disability_group, pattern="^dis_group_")
        ],
        NewCaseFlow.GET_DISABILITY_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_disability_date)
        ],
        NewCaseFlow.GET_DISABILITY_CERT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_disability_cert),
            CallbackQueryHandler(skip_disability_cert, pattern="^skip_step$"),
        ],
        NewCaseFlow.ASK_NAME_CHANGE: [
            CallbackQueryHandler(ask_name_change, pattern="^name_change_")
        ],
        NewCaseFlow.GET_OLD_FULL_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_old_full_name)
        ],
        NewCaseFlow.GET_NAME_CHANGE_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_change_date)
        ],
        NewCaseFlow.GET_BENEFITS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_benefits),
            CallbackQueryHandler(skip_benefits, pattern="^skip_step$"),
        ],
        NewCaseFlow.GET_INCORRECT_DOC_FLAG: [
            CallbackQueryHandler(get_incorrect_doc_flag, pattern="^incorrect_docs_")
        ],
        NewCaseFlow.GET_SUBMITTED_DOCS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_submitted_docs),
        ],
        NewCaseFlow.AWAIT_PASSPORT_PHOTO: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, handle_ocr_photo)
        ],
        NewCaseFlow.AWAIT_SNILS_PHOTO: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, handle_ocr_photo)
        ],
        NewCaseFlow.AWAIT_WORK_BOOK_PHOTO: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, handle_ocr_photo)
        ],
        NewCaseFlow.CONFIRM_CREATION: [
            CallbackQueryHandler(confirm_creation, pattern="^confirm_case$"),
            CallbackQueryHandler(
                handle_after_creation_choice, pattern="^(new_case|main_menu)$"
            ),
        ],
    },
    fallbacks=[CallbackQueryHandler(cancel_case, pattern="^cancel_case$")],
    map_to_parent={ConversationHandler.END: MainMenu.CHOOSING_ACTION},
    # ИСПРАВЛЕНИЕ: Добавляем параметр, чтобы убрать предупреждение
    per_chat=True,
    per_user=True,
    per_message=True,
)
