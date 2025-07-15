import asyncio
from aiogram import F, Router, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.api.client import api_client
from app.bot.keyboards import get_main_menu_keyboard
from app.bot.states import Login

router = Router()


@router.message(CommandStart())
async def handle_start(message: Message):
    await message.answer(
        "👋 Здравствуйте! Я — ваш личный помощник \"Пенсионный Консультант\".\n\n"
        "Я помогу вам:\n"
        "🔹 Определить ваше право на пенсию\n"
        "🔹 Сформировать пакет документов\n"
        "🔹 Распознать данные с фотографий документов\n\n"
        "Для начала работы, пожалуйста, войдите в систему.\n\n"
        "Нажмите /login, чтобы начать."
    )


@router.message(Command("login"))
async def handle_login(message: Message, state: FSMContext):
    await message.answer("🔑 Пожалуйста, введите ваш логин.")
    await state.set_state(Login.entering_login)


@router.message(Login.entering_login, F.text)
async def handle_username_entered(message: Message, state: FSMContext):
    await state.update_data(username=message.text)
    response = await message.answer("🔒 Теперь введите ваш пароль. (Сообщение будет удалено через 15 секунд для безопасности).")
    await state.set_state(Login.entering_password)
    
    # Сохраняем ID сообщения с просьбой ввести пароль, чтобы удалить его позже
    await state.update_data(password_prompt_message_id=response.message_id)


@router.message(Login.entering_password, F.text)
async def handle_password_entered(message: Message, state: FSMContext, bot: Bot):
    user_data = await state.get_data()
    username = user_data.get("username")
    password = message.text
    password_prompt_message_id = user_data.get("password_prompt_message_id")
    chat_id = message.chat.id

    # Удаляем сообщение пользователя с паролем
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        pass # Не страшно, если не получилось

    # Удаляем сообщение бота с просьбой ввести пароль
    if password_prompt_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=password_prompt_message_id)
        except Exception:
            pass

    # Аутентификация
    success = await api_client.login(
        user_id=message.from_user.id, 
        username=username, 
        password=password
    )

    if success:
        await message.answer(
            "✅ Вы успешно вошли в систему!\n\nЧем я могу помочь?",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
    else:
        await message.answer(
            "❌ Ошибка входа. Неверный логин или пароль.\n\n"
            "Попробуйте еще раз, нажав /login."
        )
        await state.clear() # Сбрасываем состояние, чтобы можно было начать заново 