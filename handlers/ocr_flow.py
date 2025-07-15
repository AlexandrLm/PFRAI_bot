# pfr_bot/handlers/ocr_flow.py
import logging
import httpx
from telegram import Update
from telegram.ext import (
    ContextTypes, 
    ConversationHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters,
    CommandHandler
)
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from states import MainMenu, OcrFlow
from keyboards import ocr_document_type_keyboard, main_menu_keyboard
# Импортируем кастомные исключения
from api_client import api_client, ApiClientError, TaskTimeoutError
# Удаляем ошибочный импорт, оставив только нужное
from .start import back_to_main_menu
# Импортируем общие вспомогательные функции
from .new_case_flow import create_progress_callback, handle_api_error

logger = logging.getLogger(__name__)

async def ocr_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога распознавания документов."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text="Какой тип документа вы хотите распознать?",
        reply_markup=ocr_document_type_keyboard()
    )
    return OcrFlow.CHOOSE_TYPE

async def ocr_choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора типа документа."""
    query = update.callback_query
    await query.answer()
    
    doc_type = query.data.replace("ocr_type_", "")
    context.user_data['ocr_doc_type'] = doc_type
    
    ocr_doc_name_map = {
        'passport': 'паспорт', 
        'snils': 'СНИЛС', 
        'work_book': 'трудовую книжку',
        'other': 'другой документ'
    }
    doc_name = ocr_doc_name_map.get(doc_type, "документ")
    
    # Инициализируем хранилище файлов
    context.user_data['ocr_files'] = []

    await query.edit_message_text(
        text=f"Отлично! Теперь отправьте мне фотографию или скан документа ({doc_name}).\n"
             f"Если это многостраничный документ (например, трудовая книжка), отправьте все страницы по очереди и нажмите 'Готово'.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data="ocr_done")]])
    )
    return OcrFlow.UPLOAD_FILE

async def ocr_upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Прием файла(ов) для OCR."""
    message = update.message
    if not (message.photo or message.document):
        await message.reply_text("Пожалуйста, отправьте фото или PDF-файл.")
        return OcrFlow.UPLOAD_FILE

    if 'ocr_files' not in context.user_data:
        context.user_data['ocr_files'] = []

    file = await (message.photo[-1] if message.photo else message.document).get_file()
    file_bytes = await file.download_as_bytearray()
    
    filename = f"{file.file_unique_id}.jpg" if message.photo else file.file_path.split('/')[-1]
    
    context.user_data['ocr_files'].append({'bytes': file_bytes, 'filename': filename})

    doc_type = context.user_data.get('ocr_doc_type')
    # Считаем, что 'other' и 'work_book' тоже могут быть многостраничными для гибкости
    is_multi_page = doc_type in ['work_book', 'other'] 

    if is_multi_page:
        await message.reply_text(
            'Фото добавлено. Отправьте следующее или нажмите "Готово".',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data="ocr_done")]])
        )
        return OcrFlow.UPLOAD_FILE
    else:
        # Для одностраничных документов (паспорт, СНИЛС) сразу запускаем обработку
        return await ocr_process_files(update, context)

async def ocr_done_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка нажатия кнопки 'Готово'."""
    query = update.callback_query
    await query.answer()
    
    files = context.user_data.get('ocr_files', [])
    if not files:
        await query.edit_message_text('Вы не отправили ни одного файла. Загрузка отменена.')
        return await ocr_end(update, context)
        
    await query.edit_message_text(f'Начинаю обработку {len(files)} файла(ов)...')
    return await ocr_process_files(update, context)

async def ocr_process_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускает задачи OCR для всех загруженных файлов."""
    files = context.user_data.pop('ocr_files', [])
    doc_type = context.user_data.get('ocr_doc_type')
    chat = update.effective_chat

    if not files:
        # Этот случай в основном обрабатывается в ocr_done_button, но для надежности оставим
        return await ocr_end(update, context)

    all_results = []
    has_errors = False

    for i, file_data in enumerate(files):
        filename = file_data['filename']
        progress_message = await chat.send_message(f"Обработка файла {i+1}/{len(files)} ('{filename}')...")
        
        try:
            task_response = await api_client.submit_ocr_task(doc_type, file_data['bytes'], filename)
            status_response = await api_client.poll_task_status(
                f'/api/v1/document_extractions/{task_response["task_id"]}',
                create_progress_callback(progress_message)
            )
            
            if status_response['status'] == 'COMPLETED':
                data = status_response['data']
                all_results.append(data)
                result_text = format_ocr_result(data, doc_type)
                await progress_message.edit_text(result_text, parse_mode='Markdown')
            else:
                has_errors = True
                error_info = status_response.get('error', {'detail': 'Неизвестная ошибка'})
                await progress_message.edit_text(f"❌ Ошибка обработки файла: {error_info.get('detail')}")
        
        except ApiClientError as e:
            has_errors = True
            await handle_api_error(e, progress_message)
        except TaskTimeoutError as e:
            has_errors = True
            logger.warning(f"Таймаут OCR задачи: {e}")
            await progress_message.edit_text("⏳ Сервер слишком долго обрабатывал запрос. Попробуйте позже.")
        except Exception as e:
            has_errors = True
            logger.error(f"Критическая ошибка при обработке OCR файла '{filename}': {e}", exc_info=True)
            await progress_message.edit_text('💥 Произошла непредвиденная ошибка при обработке файла.')

    # Если мы в процессе создания дела, сохраняем все результаты и возвращаемся
    if context.user_data.get('from_new_case'):
        if 'ocr_data' not in context.user_data:
            context.user_data['ocr_data'] = []
        context.user_data['ocr_data'].extend(all_results)
        
        # Уведомляем пользователя о результате перед возвратом
        if not all_results and has_errors:
            await chat.send_message("Не удалось обработать ни один из документов. Возврат в меню создания дела.")
        elif has_errors:
            await chat.send_message("Часть документов обработана с ошибками. Данные из успешно обработанных документов сохранены. Возврат в меню создания дела.")
        else:
            await chat.send_message("Все документы успешно обработаны. Возврат в меню создания дела.")

        # Получаем функцию для возврата из контекста
        return_to_new_case_func = context.user_data.pop("return_to_new_case_func", None)
        if return_to_new_case_func:
            return await return_to_new_case_func(update, context)

    return await ocr_end(update, context)


def format_ocr_result(data: dict, doc_type: str) -> str:
    """Форматирует результат OCR в читаемый текст с использованием Markdown."""
    if not data:
        return "*Не удалось извлечь данные.*"
    
    doc_name_map = {
        'passport': 'паспорта', 
        'snils': 'СНИЛС', 
        'work_book': 'трудовой книжки',
        'other': 'документа'
    }
    doc_name = doc_name_map.get(doc_type, "документа")
    
    lines = [f"*Извлеченные данные из {doc_name}:*"]
    
    # Рекурсивная функция для красивого вывода
    def format_dict(d: dict, indent_level=0):
        indent = "  " * indent_level
        for key, value in d.items():
            # Пропускаем пустые значения для чистоты вывода
            if value is None or value == '' or value == []:
                continue

            key_str = key.replace('_', ' ').capitalize()
            if isinstance(value, dict):
                lines.append(f"{indent}*{key_str}:*")
                format_dict(value, indent_level + 1)
            elif isinstance(value, list):
                lines.append(f"{indent}*{key_str}:*")
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        lines.append(f"{indent}  - `Запись {i+1}`")
                        format_dict(item, indent_level + 2)
            else:
                lines.append(f"{indent}- *{key_str}:* `{value}`")

    format_dict(data)
            
    if len(lines) == 1:
        return "*Данные не найдены или пусты.*"
        
    return "\n".join(lines)


async def ocr_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершает диалог OCR и очищает данные."""
    context.user_data.pop('ocr_doc_type', None)
    context.user_data.pop('ocr_files', None)
    context.user_data.pop('from_new_case', None)
    context.user_data.pop('ocr_data', None)
    
    # Если мы не возвращаемся в создание дела, покажем главное меню, чтобы пользователь не остался в "пустоте"
    if not context.user_data.get('from_new_case'):
         await update.effective_chat.send_message(
            "Распознавание завершено.",
            reply_markup=main_menu_keyboard()
        )

    return ConversationHandler.END

# Новый обработчик команды /cancel внутри диалога
async def ocr_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог OCR и возвращает в главное меню."""
    await update.message.reply_text(
        "Распознавание документа отменено.",
        reply_markup=main_menu_keyboard()
    )
    return await ocr_end(update, context)

ocr_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(ocr_start, pattern="^ocr_document$")],
    states={
        OcrFlow.CHOOSE_TYPE: [CallbackQueryHandler(ocr_choose_type, pattern="^ocr_type_")],
        OcrFlow.UPLOAD_FILE: [
            MessageHandler(filters.PHOTO | filters.Document.PDF, ocr_upload_file),
            CallbackQueryHandler(ocr_done_button, pattern="^ocr_done$")
        ],
    },
    fallbacks=[CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
               CommandHandler("start", back_to_main_menu),
               CallbackQueryHandler(ocr_cancel, pattern="^cancel_ocr$")],
)