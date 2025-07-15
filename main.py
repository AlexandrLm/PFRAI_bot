# main.py
import logging
from telegram.ext import Application, ConversationHandler

from config import TELEGRAM_BOT_TOKEN, LOG_LEVEL
from handlers.start import start_handler
from handlers.new_case_flow import new_case_conv_handler
from handlers.ocr_flow import ocr_conv_handler
from states import MainMenu

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=getattr(logging, LOG_LEVEL))
logging.getLogger("httpx").setLevel(logging.WARNING)

def main() -> None:
    """Запуск бота."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Главный ConversationHandler, который управляет всем
    # Это позволяет легко возвращаться в главное меню из любого диалога
    main_conversation = ConversationHandler(
        entry_points=[start_handler],
        states={
            MainMenu.CHOOSING_ACTION: [
                new_case_conv_handler,
                ocr_conv_handler,
            ],
        },
        fallbacks=[start_handler], # Если что-то пойдет не так, возвращаемся в начало
    )
    
    application.add_handler(main_conversation)

    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()