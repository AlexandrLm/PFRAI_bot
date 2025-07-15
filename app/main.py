import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.api.client import api_client
from app.bot.handlers import case_management, ocr, auth, history
from app.config import settings


async def main():
    logging.basicConfig(level=settings.log_level)

    bot = Bot(token=settings.bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Подключаем роутеры
    dp.include_router(auth.router)
    dp.include_router(case_management.router)
    dp.include_router(ocr.router)
    dp.include_router(history.router)

    # Пропускаем накопившиеся апдейты и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)

    # Запуск бота
    try:
        logging.info("Bot started...")
        await dp.start_polling(bot)
    finally:
        logging.info("Bot stopped.")
        await bot.session.close()
        # Закрываем сессию API клиента
        await api_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped by user")
