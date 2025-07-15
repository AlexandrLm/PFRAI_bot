# handlers/start.py
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from keyboards import main_menu_keyboard
from states import MainMenu

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отправляет приветственное сообщение и главное меню."""
    user = update.effective_user
    text = (
        f"Здравствуйте, {user.first_name}!\n\n"
        "Я бот-помощник для оформления пенсии. Я помогу вам собрать данные для "
        "подачи заявления или распознать информацию с ваших документов.\n\n"
        "Выберите действие:"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    return MainMenu.CHOOSING_ACTION

async def start_over(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Редактирует сообщение, чтобы показать главное меню (для колбеков)."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    text = (
        f"Здравствуйте, {user.first_name}!\n\n"
        "Я бот-помощник для оформления пенсии. Я помогу вам собрать данные для "
        "подачи заявления или распознать информацию с ваших документов.\n\n"
        "Выберите действие:"
    )
    await query.edit_message_text(text=text, reply_markup=main_menu_keyboard())
    return MainMenu.CHOOSING_ACTION


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Возвращает пользователя в главное меню."""
    query = update.callback_query
    await query.answer()
    text = "Вы в главном меню. Выберите действие:"
    await query.edit_message_text(text, reply_markup=main_menu_keyboard())
    return MainMenu.CHOOSING_ACTION

start_handler = CommandHandler("start", start)