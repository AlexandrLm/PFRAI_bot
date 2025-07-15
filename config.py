# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram Bot Configuration ---
# Получите токен у @BotFather в Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВАШ_ТЕЛЕГРАМ_ТОКЕН")

# --- Backend API Configuration ---
# URL вашего развернутого бэкенда
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Учетные данные для входа в API.
# Можно использовать роль 'manager', так как клиенту не нужны админские права.
BACKEND_USERNAME = os.getenv("BACKEND_USERNAME", "manager")
BACKEND_PASSWORD = os.getenv("BACKEND_PASSWORD", "managerPas")

# Максимальное время ожидания завершения долгой задачи (OCR, анализ дела) в секундах
API_TASK_TIMEOUT_SEC = int(os.getenv("API_TASK_TIMEOUT_SEC", 300)) # 5 минут по умолчанию

# Проверка, что токен был указан
if TELEGRAM_BOT_TOKEN == "ВАШ_ТЕЛЕГРАМ_ТОКЕН":
    raise ValueError("Необходимо указать TELEGRAM_BOT_TOKEN в файле .env или напрямую в config.py")

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')