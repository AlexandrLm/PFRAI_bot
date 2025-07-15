# pfr_bot/handlers/ocr_flow.py
import logging
import httpx
from telegram import Update
from telegram.ext import (
    ContextTypes, 
    ConversationHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters
)
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from states import MainMenu, OcrFlow
from keyboards import ocr_document_type_keyboard, main_menu_keyboard
# Импортируем кастомные исключения
from api_client import api_client, ApiClientError, TaskTimeoutError
from .start import back_to_main_menu
# Импортируем общие вспомогательные функции
from .new_case_flow import create_progress_callback, handle_api_error, back_to_new_case

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
    
    await query.edit_message_text(
        text=f"Отлично! Теперь отправьте мне фотографию или скан документа ({doc_name})."
    )
    return OcrFlow.UPLOAD_FILE

async def ocr_upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Прием файла и запуск задачи OCR."""
    message = update.message
    doc_type = context.user_data.get('ocr_doc_type')
    is_multi_page = doc_type == 'work_book'
    if 'ocr_files' not in context.user_data:
        context.user_data['ocr_files'] = []
    
    if message.photo or message.document:
        file = await (message.photo[-1] if message.photo else message.document).get_file()
        file_bytes = await file.download_as_bytearray()
        context.user_data['ocr_files'].append({'bytes': file_bytes, 'filename': file.file_unique_id + '.jpg'})
        if is_multi_page:
            await message.reply_text('Фото добавлено. Отправьте следующее или нажмите "Готово".', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Готово', callback_data='ocr_done')]]))
            return OcrFlow.UPLOAD_FILE
    elif update.callback_query and update.callback_query.data == 'ocr_done':
        files = context.user_data.pop('ocr_files', [])
        if not files:
            await message.reply_text('Нет файлов для обработки.')
            return await ocr_end(update, context)
        # Обработка нескольких файлов (для простоты, отправляем первый; доработать для мульти)
        file_bytes = files[0]['bytes']
        filename = files[0]['filename']
    else:
        await message.reply_text('Пожалуйста, отправьте фото или PDF.')
        return OcrFlow.UPLOAD_FILE
    
    progress_message = await message.reply_text('Обработка...')
    try:
        task_response = await api_client.submit_ocr_task(doc_type, file_bytes, filename)
        status_response = await api_client.poll_task_status(f'/api/v1/document_extractions/{task_response["task_id"]}', create_progress_callback(progress_message))
        if status_response['status'] == 'COMPLETED':
            data = status_response['data']
            if context.user_data.get('from_new_case'):
                context.user_data['ocr_data'] = data
                return await back_to_new_case(update, context)  # Предполагаемая функция
            result_text = format_ocr_result(data, doc_type)
            await progress_message.edit_text(result_text)
        else:
            await progress_message.edit_text('Ошибка: ' + str(status_response.get('error')))
        return await ocr_end(update, context)
    except Exception as e:
        await progress_message.edit_text('Ошибка обработки.')
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
    """Завершает диалог OCR."""
    context.user_data.pop('ocr_doc_type', None)
    return ConversationHandler.END


ocr_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(ocr_start, pattern="^ocr_document$")],
    states={
        OcrFlow.CHOOSE_TYPE: [CallbackQueryHandler(ocr_choose_type, pattern="^ocr_type_")],
        OcrFlow.UPLOAD_FILE: [MessageHandler(filters.PHOTO | filters.Document.PDF, ocr_upload_file)],
    },
    fallbacks=[CallbackQueryHandler(back_to_main_menu, pattern="^cancel_ocr$")],
    map_to_parent={
        ConversationHandler.END: MainMenu.CHOOSING_ACTION,
    },
    per_message=True,
    per_chat=True,
    per_user=True,
)