import logging
from datetime import datetime
from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, PhotoSize
from io import BytesIO
import re
import asyncio

from app.api.client import api_client
from app.bot.keyboards import (
    get_pension_types_keyboard,
    get_yes_no_keyboard,
    get_confirmation_keyboard,
    get_data_input_method_keyboard,
    get_check_ocr_keyboard,
    get_skip_keyboard,
    get_gender_keyboard,
    get_document_upload_keyboard,
    get_verification_keyboard,
)
from app.bot.states import NewCase, CheckStatus
from app.bot.utils import split_long_message

router = Router()


# --- Начало создания дела ---

@router.callback_query(F.data == "new_case")
async def handle_start_new_case(callback: CallbackQuery, state: FSMContext):
    """
    Обрабатывает нажатие кнопки 'Начать новое дело' из главного меню.
    Запрашивает у API типы пенсий и предлагает их пользователю.
    """
    await callback.message.edit_text("Загружаю доступные типы пенсий...")
    
    pension_types = await api_client.get_pension_types(user_id=callback.from_user.id)
    
    if pension_types:
        keyboard = get_pension_types_keyboard(pension_types)
        await callback.message.edit_text(
            "Выберите тип пенсии, на который вы претендуете:",
            reply_markup=keyboard
        )
        await state.set_state(NewCase.choosing_pension_type)
    else:
        await callback.message.edit_text(
            "К сожалению, не удалось загрузить типы пенсий. Попробуйте позже."
        )
        await callback.answer()
        await state.clear()


# Старая логика с выбором метода ввода пока не нужна по новому ТЗ
# @router.message(F.text == "Создать новое дело")
# async def handle_new_case(message: Message, state: FSMContext):
#     await message.answer(
#         "Как вы хотите предоставить данные для нового дела?",
#         reply_markup=get_data_input_method_keyboard(),
#     )
#     await state.set_state(NewCase.choosing_input_method)


# @router.message(NewCase.choosing_input_method, F.text.in_(["Заполнить вручную", "Загрузить документы (OCR)"]))
# async def handle_input_method_chosen(message: Message, state: FSMContext):
#     ... # (код старого обработчика)


# --- Выбор типа пенсии (общий для обоих сценариев) ---
async def ask_for_next_document(message: Message, state: FSMContext):
    """Запрашивает следующий документ из списка в FSM."""
    data = await state.get_data()
    docs_to_upload = data.get("docs_to_upload", [])
    current_doc_index = data.get("current_doc_index", 0)

    if current_doc_index < len(docs_to_upload):
        doc = docs_to_upload[current_doc_index]
        await message.answer(
            f"Пожалуйста, загрузите следующий документ: <b>{doc['name']}</b>\n"
            f"<i>{doc['description']}</i>"
        )
        await state.set_state(NewCase.uploading_documents_cycle)
    else:
        # Все документы загружены
        await message.answer(
            "Все необходимые документы приняты. Обработка может занять некоторое время.\n\n"
            "Нажмите на кнопку ниже, чтобы проверить готовность.",
            reply_markup=get_check_ocr_keyboard()
        )
        await state.set_state(NewCase.checking_ocr_results)


@router.callback_query(NewCase.choosing_pension_type, F.data.startswith("pension_type:"))
async def handle_pension_type_chosen(
    callback: CallbackQuery, state: FSMContext
):
    pension_type_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    chosen_type_name = "Неизвестный тип"
    if callback.message.reply_markup:
        for row in callback.message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data == callback.data:
                    chosen_type_name = button.text
                    break
            if chosen_type_name != "Неизвестный тип":
                break
    
    await state.update_data(
        pension_type_id=pension_type_id,
        pension_type_name=chosen_type_name
    )

    await callback.message.edit_text(f"Выбран тип пенсии: <b>{chosen_type_name}</b>")
    
    # По новому ТЗ, после выбора типа пенсии мы сразу переходим к сбору personal_data
    # или к загрузке документов. Логику OCR перенесем на следующий шаг.
    
    await callback.message.answer("📝 Введите вашу **фамилию**.")
    await state.set_state(NewCase.entering_last_name)
    await callback.answer()


# --- Цепочка сбора персональных данных ---

@router.message(NewCase.entering_last_name, F.text)
async def handle_last_name(message: Message, state: FSMContext):
    await state.update_data(last_name=message.text)
    await message.answer(f"Принято: {message.text}.\n\n📝 Введите ваше **имя**.")
    await state.set_state(NewCase.entering_first_name)


@router.message(NewCase.entering_first_name, F.text)
async def handle_first_name(message: Message, state: FSMContext):
    await state.update_data(first_name=message.text)
    await message.answer(
        f"Принято: {message.text}.\n\n📝 Введите ваше **отчество**.",
        reply_markup=get_skip_keyboard("Пропустить"),
    )
    await state.set_state(NewCase.entering_middle_name)


@router.callback_query(NewCase.entering_middle_name, F.data == "skip")
async def handle_skip_middle_name(callback: CallbackQuery, state: FSMContext):
    await state.update_data(middle_name=None)
    await callback.message.edit_text("Отчество пропущено.")
    await callback.message.answer("📅 Введите дату рождения в формате **ДД.ММ.ГГГГ**.")
    await state.set_state(NewCase.entering_birth_date)
    await callback.answer()


@router.message(NewCase.entering_middle_name, F.text)
async def handle_middle_name(message: Message, state: FSMContext):
    await state.update_data(middle_name=message.text)
    await message.answer(f"Принято: {message.text}.")
    await message.answer("📅 Введите дату рождения в формате **ДД.ММ.ГГГГ**.")
    await state.set_state(NewCase.entering_birth_date)


@router.message(NewCase.entering_birth_date, F.text)
async def handle_birth_date(message: Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%d.%m.%Y")
        await state.update_data(birth_date=message.text)
        await message.answer(f"Принято: {message.text}.\n\n📝 Введите номер **СНИЛС** (11 цифр, можно с пробелами и дефисами).")
        await state.set_state(NewCase.entering_snils)
    except ValueError:
        await message.answer(
            "Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ:"
        )
        await state.set_state(NewCase.entering_birth_date)


@router.message(NewCase.entering_snils, F.text)
async def handle_snils(message: Message, state: FSMContext):
    snils = message.text.replace("-", "").replace(" ", "")
    if snils.isdigit() and len(snils) == 11:
        await state.update_data(snils=snils)
        await message.answer(
            f"Принято.\n\n👫 Укажите ваш **пол**.",
            reply_markup=get_gender_keyboard(),
        )
        await state.set_state(NewCase.entering_gender)
    else:
        await message.answer(
            "Неверный формат СНИЛС. Номер должен содержать 11 цифр. Попробуйте еще раз:"
        )
        await state.set_state(NewCase.entering_snils)


@router.callback_query(NewCase.entering_gender, F.data.startswith("gender:"))
async def handle_gender_callback(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split(":")[1]
    gender_text = "Мужской" if gender == "male" else "Женский"
    await state.update_data(gender=gender_text)
    await callback.message.edit_text(f"Выбран пол: {gender_text}")
    await callback.message.answer(" гражданство.")
    await state.set_state(NewCase.entering_citizenship)
    await callback.answer()


@router.message(NewCase.entering_citizenship, F.text)
async def handle_citizenship(message: Message, state: FSMContext):
    await state.update_data(citizenship=message.text)
    await message.answer(f"Принято: {message.text}.\n\n👨‍👩‍👧‍👦 Укажите **количество иждивенцев** (цифрой, 0 если нет).")
    await state.set_state(NewCase.entering_dependents)


@router.message(NewCase.entering_dependents, F.text)
async def handle_dependents(message: Message, state: FSMContext):
    if message.text.isdigit():
        dependents = int(message.text)
        if dependents >= 0:
            await state.update_data(dependents=dependents)
            await message.answer(
                f"Принято: {dependents}.\n\n✅ Сбор персональных данных завершен."
            )
            
            # --- Начало сценария загрузки документов ---
            data = await state.get_data()
            pension_type_id = data.get("pension_type_id")
            user_id = message.from_user.id
            
            await message.answer("Теперь давайте разберемся с документами. Загружаю список...")
            
            required_docs = await api_client.get_required_documents(
                user_id=user_id, pension_type_id=pension_type_id
            )
            
            if not required_docs:
                await message.answer("Не удалось получить список документов для этого типа пенсии. Пропускаем этот шаг.")
                # Если документов нет, можно сразу переходить к сводке
                await show_summary_and_ask_for_confirmation(message, state)
                return

            await state.update_data(
                required_docs=required_docs,
                uploaded_docs={}, # {doc_type: task_id/data}
            )

            # Формируем сообщение со списком документов и клавиатурой
            docs_message_lines = ["Для этого типа пенсии требуются:\n"]
            for doc in required_docs:
                status = "❗️" if doc.get('is_critical') else "🔹"
                docs_message_lines.append(f"{status} {doc.get('name')}")
            
            await message.answer(
                "\n".join(docs_message_lines),
                reply_markup=get_document_upload_keyboard(required_docs)
            )
            await state.set_state(NewCase.managing_documents)

        else:
            await message.answer("Количество иждивенцев не может быть отрицательным. Попробуйте снова:")
            await state.set_state(NewCase.entering_dependents)
    else:
        await message.answer(
            "Пожалуйста, введите количество иждивенцев цифрой (например, 0, 1, 2...):"
        )
        await state.set_state(NewCase.entering_dependents)


# --- Сбор данных об инвалидности (старая логика, пока убираем) ---
# --- Новый блок: управление загрузкой документов ---

@router.callback_query(NewCase.managing_documents, F.data.startswith("upload_doc:"))
async def handle_upload_doc_button(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает нажатие на кнопку 'Загрузить фото ...'"""
    doc_type_to_upload = callback.data.split(":")[1]
    await state.update_data(current_upload_doc_type=doc_type_to_upload)
    
    await callback.message.answer(f"Пришлите мне фотографию или скан для документа: <b>{doc_type_to_upload}</b>")
    await state.set_state(NewCase.uploading_document)
    await callback.answer()


@router.message(NewCase.uploading_document, F.photo)
async def handle_document_photo_upload(message: Message, state: FSMContext, bot: Bot):
    """Принимает фото документа, отправляет на OCR и начинает опрос статуса."""
    data = await state.get_data()
    doc_type = data.get("current_upload_doc_type")

    if not doc_type:
        await message.answer("Произошла ошибка, не могу определить тип документа. Пожалуйста, начните заново.")
        await state.clear()
        return

    # Отправляем уведомление пользователю
    progress_message = await message.answer(f"⏳ Получил фото для '{doc_type}'. Начинаю распознавание, это может занять до минуты...")

    # Скачиваем фото
    photo: PhotoSize = message.photo[-1]
    image_bytes_io = BytesIO()
    await bot.download(file=photo.file_id, destination=image_bytes_io)
    image_bytes = image_bytes_io.getvalue()
    
    # Отправляем на OCR
    result = await api_client.create_ocr_task(
        user_id=message.from_user.id,
        file_content=image_bytes,
        document_type=doc_type
    )

    if not result or "task_id" not in result:
        await progress_message.edit_text(f"❌ К сожалению, не удалось начать обработку документа '{doc_type}'. Попробуйте загрузить еще раз.")
        # Возвращаемся к выбору документов
        await state.set_state(NewCase.managing_documents)
        return

    task_id = result["task_id"]
    await progress_message.edit_text(f"Распознавание для '{doc_type}' запущено. ID задачи: `{task_id}`. Ожидайте результата. Я проверю его через несколько секунд.")
    
    # Сохраняем таску
    uploaded_docs = data.get("uploaded_docs", {})
    uploaded_docs[doc_type] = {"task_id": task_id, "status": "PROCESSING"}
    await state.update_data(uploaded_docs=uploaded_docs)
    
    # Запускаем опрос статуса задачи
    asyncio.create_task(poll_ocr_status(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        task_id=task_id,
        doc_type=doc_type,
        state=state,
        bot=bot
    ))

    # Сразу возвращаем пользователя к управлению документами, не дожидаясь окончания опроса
    required_docs = data.get("required_docs", [])
    await message.answer(
        "Вы можете загрузить следующий документ или дождаться результатов обработки.",
        reply_markup=get_document_upload_keyboard(required_docs, uploaded_docs)
    )
    await state.set_state(NewCase.managing_documents)


async def poll_ocr_status(user_id: int, chat_id: int, task_id: str, doc_type: str, state: FSMContext, bot: Bot):
    """Асинхронно опрашивает статус OCR задачи и обрабатывает результат."""
    # Простой поллинг с несколькими попытками
    for _ in range(10): # Например, 10 попыток с интервалом 5 секунд
        await asyncio.sleep(5) 
        
        result = await api_client.get_ocr_task_status(user_id=user_id, task_id=task_id)
        
        if result and result.get("status") == "COMPLETED":
            data_from_fsm = await state.get_data()
            uploaded_docs = data_from_fsm.get("uploaded_docs", {})
            
            # Сохраняем результат и обновляем статус
            ocr_data = result.get("data", {})
            uploaded_docs[doc_type] = {"task_id": task_id, "status": "COMPLETED", "data": ocr_data}
            await state.update_data(uploaded_docs=uploaded_docs, last_ocr_result=ocr_data)
            
            # Показываем результат пользователю для верификации
            verification_message = "✅ Распознавание завершено! Проверьте данные:\n\n"
            for key, value in ocr_data.items():
                verification_message += f"<b>{FIELD_MAP.get(key, key)}:</b> {value}\n"
            
            await bot.send_message(
                chat_id,
                verification_message,
                reply_markup=get_verification_keyboard()
            )
            await state.set_state(NewCase.verifying_document_data)
            return # Выходим из цикла и задачи

        elif result and result.get("status") == "FAILED":
            data_from_fsm = await state.get_data()
            uploaded_docs = data_from_fsm.get("uploaded_docs", {})
            uploaded_docs[doc_type]["status"] = "FAILED"
            await state.update_data(uploaded_docs=uploaded_docs)
            
            error_detail = result.get("error", {}).get("detail", "Неизвестная ошибка")
            await bot.send_message(chat_id, f"❌ К сожалению, не удалось распознать данные с документа '{doc_type}'. Ошибка: {error_detail}")
            # Обновляем клавиатуру, чтобы показать ошибку
            required_docs = data_from_fsm.get("required_docs", [])
            await bot.send_message(chat_id, "Попробуйте загрузить его снова или выберите другой документ.", reply_markup=get_document_upload_keyboard(required_docs, uploaded_docs))
            return

    # Если вышли из цикла по таймауту
    await bot.send_message(chat_id, f"⏳ Обработка документа '{doc_type}' затягивается. Я сообщу, когда будет готово. Вы можете продолжать.")


@router.callback_query(NewCase.verifying_document_data, F.data == "ocr_data_correct")
async def handle_ocr_data_correct(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает подтверждение корректности данных OCR."""
    await callback.message.edit_text("Отлично! Сохраняю распознанные данные.")
    
    data = await state.get_data()
    last_ocr_result = data.get("last_ocr_result", {})
    
    # Обновляем основные поля в FSM из данных OCR
    update_data = {}
    for key, value in last_ocr_result.items():
        # TODO: Добавить более умный маппинг, если поля в OCR и FSM называются по-разному
        if key in ["last_name", "first_name", "middle_name", "birth_date", "snils_number"]:
            # Простое сопоставление для примера
            if key == "snils_number":
                update_data["snils"] = value
            else:
                update_data[key] = value

    await state.update_data(**update_data)
    
    # Возвращаемся к управлению документами
    required_docs = data.get("required_docs", [])
    uploaded_docs = data.get("uploaded_docs", {})
    await callback.message.answer(
        "Вы можете загрузить следующий документ.",
        reply_markup=get_document_upload_keyboard(required_docs, uploaded_docs)
    )
    await state.set_state(NewCase.managing_documents)
    await callback.answer()


@router.callback_query(NewCase.verifying_document_data, F.data == "ocr_data_edit")
async def handle_ocr_data_edit(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает запрос на ручное исправление данных OCR."""
    await callback.message.edit_text("Функция ручного редактирования пока в разработке.")
    
    # Возвращаемся к управлению документами
    data = await state.get_data()
    required_docs = data.get("required_docs", [])
    uploaded_docs = data.get("uploaded_docs", {})
    await callback.message.answer(
        "Пока что вы можете загрузить этот документ еще раз или продолжить с другими.",
        reply_markup=get_document_upload_keyboard(required_docs, uploaded_docs)
    )
    await state.set_state(NewCase.managing_documents)
    await callback.answer()


@router.callback_query(NewCase.managing_documents, F.data == "docs_upload_next_step")
async def handle_docs_upload_next_step(callback: CallbackQuery, state: FSMContext):
    """
    Вызывается после загрузки всех необходимых документов.
    Показывает итоговую сводку перед отправкой.
    """
    await callback.message.edit_text("Отлично, все необходимые документы на месте. Готовлю итоговую сводку...")
    await show_summary_and_ask_for_confirmation(callback.message, state)
    await callback.answer()


@router.callback_query(NewCase.managing_documents, F.data == "skip_doc_upload")
async def handle_skip_doc_upload(callback: CallbackQuery, state: FSMContext):
    """
    Обрабатывает пропуск шага загрузки документов.
    """
    await callback.message.edit_text("Вы решили пропустить загрузку документов и ввести все данные вручную.")
    # Тут нам нужно собрать недостающие данные, которые мы обычно получаем из OCR.
    # Для простоты сценария, пока просто переходим к итоговой сводке.
    # В реальном проекте здесь была бы цепочка FSM для ввода данных о стаже и т.д.
    await show_summary_and_ask_for_confirmation(callback.message, state)
    await callback.answer()


# Старая логика сбора данных об инвалидности и т.д. будет заменена
# на логику верификации OCR и итоговой сводки после этапа документов.

# --- Финальное подтверждение и отправка ---

async def show_summary_and_ask_for_confirmation(message: Message, state: FSMContext):
    """Показывает сводку и запрашивает подтверждение."""
    data = await state.get_data()
    
    summary_parts = [f"<b>Тип пенсии:</b> {data.get('pension_type_name', 'Не указан')}"]
    
    summary_parts.append("\n<b>Персональные данные:</b>")
    summary_parts.append(f"  ФИО: {data.get('last_name', '')} {data.get('first_name', '')} {data.get('middle_name', 'Нет')}")
    summary_parts.append(f"  Дата рождения: {data.get('birth_date', 'Не указана')}")
    summary_parts.append(f"  СНИЛС: {data.get('snils', 'Не указан')}")
    summary_parts.append(f"  Пол: {data.get('gender', 'Не указан')}")
    summary_parts.append(f"  Гражданство: {data.get('citizenship', 'Не указано')}")
    summary_parts.append(f"  Иждивенцы: {data.get('dependents', 'Не указано')}")

    if data.get('disability_group'):
        summary_parts.append("\n<b>Инвалидность:</b>")
        summary_parts.append(f"  Группа: {data.get('disability_group')}")
        summary_parts.append(f"  Дата установления: {data.get('disability_date')}")
        summary_parts.append(f"  Номер справки: {data.get('disability_cert_number', 'Отсутствует')}")
        
    if data.get('work_experience_total_years') is not None:
        summary_parts.append("\n<b>Трудовой стаж:</b>")
        summary_parts.append(f"  Общий стаж: {data.get('work_experience_total_years')} лет")

    if data.get('pension_points') is not None:
        summary_parts.append(f"\n<b>Пенсионные баллы (ИПК):</b> {data.get('pension_points')}")
        
    summary_text = "\n".join(summary_parts)
    
    await message.answer(
        "Пожалуйста, проверьте все введенные данные:\n\n" + summary_text,
        reply_markup=get_confirmation_keyboard()
    )
    await state.set_state(NewCase.confirming_case_creation)


@router.callback_query(NewCase.confirming_case_creation, F.data == "cancel_creation")
async def handle_cancel_creation(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Создание дела отменено.")
    await state.clear()


@router.callback_query(NewCase.confirming_case_creation, F.data == "confirm_creation")
async def handle_confirm_creation(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отправляю данные на сервер...")
    
    data = await state.get_data()
    user_id = callback.from_user.id
    
    personal_data = {
        "last_name": data.get("last_name"),
        "first_name": data.get("first_name"),
        "middle_name": data.get("middle_name"),
        "birth_date": data.get("birth_date"),
        "snils": data.get("snils"),
        "gender": data.get("gender"),
        "citizenship": data.get("citizenship"),
        "dependents": data.get("dependents"),
    }
    
    case_payload = {
        "personal_data": personal_data,
        "pension_type": data.get("pension_type_id"),
    }
    
    if data.get("disability_group"):
        case_payload["disability"] = {
            "group": data.get("disability_group"),
            "date": data.get("disability_date"),
            "cert_number": data.get("disability_cert_number"),
        }

    if data.get("work_experience_total_years") is not None:
        case_payload["work_experience"] = {
            "total_years": data.get("work_experience_total_years"),
        }
        
    if data.get("pension_points") is not None:
        case_payload["pension_points"] = data.get("pension_points")

    result = await api_client.create_case(user_id=user_id, case_data=case_payload)
    
    if result and result.get("case_id"):
        await callback.message.answer(
            f"✅ Дело успешно создано! Его номер: <b>{result['case_id']}</b>\n"
            f"Статус: {result.get('final_status', 'N/A')}\n"
            f"Пояснение: {result.get('explanation', 'Нет')}"
        )
    else:
        await callback.message.answer(
            "❌ Произошла ошибка при создании дела. Попробуйте позже."
        )
        
    await state.clear()


# --- Проверка статуса ---

@router.message(F.text == "Проверить статус дела")
async def handle_check_status_start(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, введите ID вашего дела или ID задачи OCR:")
    await state.set_state(CheckStatus.entering_id)


FIELD_MAP = {
    "last_name": "Фамилия",
    "first_name": "Имя",
    "middle_name": "Отчество",
    "birth_date": "Дата рождения",
    "sex": "Пол",
    "gender": "Пол",
    "birth_place": "Место рождения",
    "passport_series": "Серия паспорта",
    "passport_number": "Номер паспорта",
    "issue_date": "Дата выдачи",
    "issuing_authority": "Кем выдан",
    "department_code": "Код подразделения",
    "snils_number": "Номер СНИЛС",
    "calculated_total_years": "Общий стаж (рассчитано)",
    "records": "Записи о трудовой деятельности",
    "date_in": "Дата приема",
    "date_out": "Дата увольнения",
    "organization": "Организация",
    "position": "Должность",
    "identified_document_type": "Определенный тип документа",
    "standardized_document_type": "Стандартный тип документа",
    "extracted_fields": "Извлеченные поля",
    "multimodal_assessment": "Оценка документа (LLM)",
    "text_llm_reasoning": "Анализ документа (LLM)",
}


def format_ocr_result(result: dict) -> str:
    """Красиво форматирует результат OCR задачи."""
    status = result.get("status", "НЕИЗВЕСТНО")
    task_id = result.get("task_id", "")
    
    lines = [f"<b>Задача OCR:</b> <code>{task_id}</code>"]
    lines.append(f"<b>Статус:</b> {status}")
    
    if status == "COMPLETED" and result.get("data"):
        lines.append("\n<b>Извлеченные данные:</b>")
        data = result["data"]
        for key, value in data.items():
            if not value:  # Пропускаем пустые значения
                continue
            
            display_name = FIELD_MAP.get(key, key)
            
            if isinstance(value, list) and key == "records":
                lines.append(f"  <b>{display_name}:</b>")
                for item in value:
                    record_line = ", ".join(
                        f"{FIELD_MAP.get(k, k)}: {v}" for k, v in item.items() if v
                    )
                    lines.append(f"    - {record_line}")
            elif isinstance(value, dict):
                lines.append(f"  <b>{display_name}:</b>")
                for sub_key, sub_value in value.items():
                    lines.append(f"    - {sub_key}: {sub_value}")
            else:
                lines.append(f"  <b>{display_name}:</b> {value}")
            
    elif status == "FAILED" and result.get("error"):
        lines.append(f"<b>Ошибка:</b> {result['error'].get('detail', 'Неизвестная ошибка')}")
        
    return "\n".join(lines)

def format_rag_explanation(text: str) -> str:
    """
    Форматирует текст от RAG в HTML для Telegram, поддерживая разные маркеры.
    """
    if not text:
        return "Нет"

    # 1. Замена разделителей
    text = text.replace('---', '\n')

    lines = text.split('\n')
    processed_lines = []

    for line in lines:
        stripped_line = line.lstrip()
        indent_space = ' ' * (len(line) - len(stripped_line))

        # 2. Убираем маркеры заголовков ###
        if stripped_line.startswith('###'):
            processed_line = indent_space + stripped_line.replace('###', '').lstrip()
            processed_lines.append(processed_line)
            continue
            
        # 3. Обрабатываем маркеры списков " - " или "* "
        if stripped_line.startswith('- ') or stripped_line.startswith('* '):
            # Заменяем дефис/звездочку на "•" и сохраняем отступ
            processed_line = indent_space + '• ' + stripped_line[2:]
            processed_lines.append(processed_line)
            continue
            
        # 4. Для строк, не требующих обработки
        processed_lines.append(line)

    final_text = '\n'.join(processed_lines)
    
    # 5. Обработка старого формата жирного текста **text** для обратной совместимости
    final_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', final_text)

    return final_text


@router.message(CheckStatus.entering_id, F.text)
async def handle_id_for_status_check(message: Message, state: FSMContext, bot: Bot):
    entity_id = message.text
    user_id = message.from_user.id
    await message.answer(f"Ищу информацию по ID: <code>{entity_id}</code>...")

    # 1. Проверяем, не ID ли это OCR задачи
    ocr_result = await api_client.get_ocr_task_status(user_id=user_id, task_id=entity_id)
    if ocr_result and ocr_result.get("error") != "not_found":
        formatted_text = format_ocr_result(ocr_result)
        await message.answer(formatted_text)
        await state.clear()
        return

    # 2. Если не OCR, проверяем, не ID ли это дела
    if entity_id.isdigit():
        case_result = await api_client.get_case_status(user_id=user_id, case_id=int(entity_id))
        if case_result and case_result.get("error") != "not_found":
            
            status_text = f"Статус дела: {case_result.get('final_status')}"
            explanation = case_result.get('final_explanation')

            if explanation and explanation.lower() != 'нет':
                formatted_explanation = format_rag_explanation(explanation)
                status_text += f"\nПояснение:\n{formatted_explanation}"
            
            # Разбиваем сообщение на части, если оно слишком длинное
            message_parts = split_long_message(status_text)
            for part in message_parts:
                await message.answer(part)

            await state.clear()
            return

    # 3. Если ничего не найдено
    await message.answer(f"Не удалось найти дело или OCR задачу с ID: {entity_id}")
    await state.clear() 

# Устаревшая логика, которая вызывает ошибку
# @router.callback_query(NewCase.checking_ocr_results, F.data == "check_ocr_results")
# async def handle_check_ocr_results(callback: CallbackQuery, state: FSMContext):
#     await callback.message.edit_text("Проверяю готовность документов...")
#     
#     data = await state.get_data()
#     ocr_tasks_ids = data.get("ocr_tasks", [])
#     user_id = callback.from_user.id
#     
#     final_data = {}
#     all_completed = True
#     
#     for task_id in ocr_tasks_ids:
#         status_result = await api_client.get_ocr_task_status(user_id=user_id, task_id=task_id)
#         if not status_result:
#             await callback.message.answer(f"⚠️ Не удалось получить статус задачи {task_id}. Попробуйте проверить позже.")
#             all_completed = False
#             break
#         
#         if status_result.get("status") == "PROCESSING":
#             await callback.message.answer("⏳ Документы еще в обработке. Пожалуйста, подождите еще немного и попробуйте снова.")
#             all_completed = False
#             break
#             
#         if status_result.get("status") == "FAILED":
#             error_msg = status_result.get("error", {}).get("detail", "Неизвестная ошибка")
#             await callback.message.answer(f"❌ Ошибка при обработке одного из документов: {error_msg}\n\nК сожалению, придется начать заново.")
#             await state.clear()
#             return
#             
#         if status_result.get("status") == "COMPLETED":
#             if isinstance(status_result.get("data"), dict):
#                 final_data.update(status_result["data"])
# 
#     if not all_completed:
#         await callback.message.answer("Попробуйте проверить еще раз через несколько секунд.", reply_markup=get_check_ocr_keyboard())
#         await callback.answer()
#         return
# 
#     ocr_bday = final_data.get("birth_date") # YYYY-MM-DD
#     if ocr_bday:
#         try:
#             dt_bday = datetime.strptime(ocr_bday, "%Y-%m-%d")
#             final_data["birth_date"] = dt_bday.strftime("%d.%m.%Y")
#         except (ValueError, TypeError):
#             final_data["birth_date"] = None 
#             
#     await state.update_data(
#         last_name=final_data.get("last_name"),
#         first_name=final_data.get("first_name"),
#         middle_name=final_data.get("middle_name"),
#         birth_date=final_data.get("birth_date"),
#         snils=final_data.get("snils_number"),
#         gender=final_data.get("sex") or final_data.get("gender"),
#         work_experience_total_years=final_data.get("calculated_total_years"),
#     )
# 
#     await callback.message.answer("✅ Все документы успешно обработаны!")
#     await show_summary_and_ask_for_confirmation(callback.message, state) 