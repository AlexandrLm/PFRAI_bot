from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.api.client import api_client
from app.bot.keyboards import get_case_history_keyboard, get_case_details_keyboard
from app.bot.utils import split_long_message

router = Router()


@router.callback_query(F.data == "case_history")
async def handle_case_history(callback: CallbackQuery, state: FSMContext):
    """
    Обрабатывает нажатие на кнопку 'Моя история дел'.
    Запрашивает историю дел пользователя и выводит ее.
    """
    await callback.message.edit_text("Запрашиваю вашу историю дел...")
    
    history_data = await api_client.get_case_history(
        user_id=callback.from_user.id,
        limit=5,
        offset=0
    )
    
    if history_data: # Проверяем, что список не пустой
        await callback.message.edit_text(
            "Ваши последние 5 дел:",
            reply_markup=get_case_history_keyboard(history_data, limit=5, current_offset=0)
        )
    else:
        await callback.message.edit_text("Не удалось найти историю ваших дел или у вас их еще нет.")
        
    await callback.answer()


@router.callback_query(F.data.startswith("history_page:"))
async def handle_history_pagination(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает переключение страниц в истории дел."""
    offset = int(callback.data.split(":")[1])
    limit = 5 # Константа, как и в первом запросе
    
    await callback.message.edit_text("Загружаю...")
    
    history_data = await api_client.get_case_history(
        user_id=callback.from_user.id,
        limit=limit,
        offset=offset
    )
    
    if history_data: # Проверяем, что список не пустой
        await callback.message.edit_text(
            "Ваши последние 5 дел:",
            reply_markup=get_case_history_keyboard(history_data, limit=limit, current_offset=offset)
        )
    else:
        await callback.message.edit_text("Больше дел не найдено.")
        
    await callback.answer()


@router.callback_query(F.data.startswith("view_case:"))
async def handle_view_case_details(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Показывает детальную информацию по конкретному делу."""
    case_id = int(callback.data.split(":")[1])
    
    await callback.message.edit_text(f"Загружаю информацию по делу #{case_id}...")
    
    case_details = await api_client.get_case_status(user_id=callback.from_user.id, case_id=case_id)
    
    if case_details and "error" not in case_details:
        # Используем существующий форматер из case_management для красивого вывода
        from .case_management import format_rag_explanation
        
        explanation = case_details.get('final_explanation')
        details_text = f"<b>Дело #{case_id}</b>\n\nСтатус: {case_details.get('final_status')}"
        
        if explanation and explanation.lower() != 'нет':
            formatted_explanation = format_rag_explanation(explanation)
            details_text += f"\nПояснение:\n{formatted_explanation}"
        
        message_parts = split_long_message(details_text)

        # Редактируем исходное сообщение первой частью текста
        await callback.message.edit_text(
            text=message_parts[0],
            reply_markup=get_case_details_keyboard(case_id) if len(message_parts) == 1 else None
        )

        # Если частей больше одной, отправляем остальные новыми сообщениями
        if len(message_parts) > 1:
            for i in range(1, len(message_parts)):
                # Клавиатуру прикрепляем только к последнему сообщению
                reply_markup = get_case_details_keyboard(case_id) if i == len(message_parts) - 1 else None
                await bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=message_parts[i],
                    reply_markup=reply_markup
                )
    else:
        await callback.message.edit_text(f"Не удалось получить информацию по делу #{case_id}.")
        
    await callback.answer()


@router.callback_query(F.data.startswith("download_doc:"))
async def handle_download_document(callback: CallbackQuery, state: FSMContext):
    """Обработчик для скачивания документов (заглушка)."""
    case_id, doc_format = callback.data.split(":")[1].split("_")
    
    await callback.message.answer(f"Функция скачивания дела #{case_id} в формате {doc_format} пока не реализована.")
    await callback.answer() 