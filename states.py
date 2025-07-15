# pfr_bot/states.py
from enum import Enum, auto

class MainMenu(Enum):
    """Состояния главного меню."""
    CHOOSING_ACTION = auto()

class OcrFlow(Enum):
    """Состояния для ВНЕШНЕГО диалога распознавания документов."""
    CHOOSE_TYPE = auto()
    UPLOAD_FILE = auto()

class NewCaseFlow(Enum):
    """Состояния для диалога создания нового дела."""
    # Основные данные
    PENSION_TYPE = auto()
    LAST_NAME = auto()
    FIRST_NAME = auto()
    MIDDLE_NAME = auto()
    BIRTH_DATE = auto()
    SNILS = auto()
    GENDER = auto()
    CITIZENSHIP = auto()
    DEPENDENTS = auto()
    WORK_EXPERIENCE_TOTAL = auto()
    PENSION_POINTS = auto()
    
    # Блок про инвалидность
    ASK_DISABILITY = auto()
    GET_DISABILITY_GROUP = auto()
    GET_DISABILITY_DATE = auto()
    GET_DISABILITY_CERT = auto()

    # Блок про смену ФИО
    ASK_NAME_CHANGE = auto()
    GET_OLD_FULL_NAME = auto()
    GET_NAME_CHANGE_DATE = auto()
    
    # Прочие данные
    GET_BENEFITS = auto()
    GET_INCORRECT_DOC_FLAG = auto()
    
    # --- НОВЫЕ СОСТОЯНИЯ ДЛЯ ВСТРОЕННОГО OCR ---
    AWAIT_PASSPORT_PHOTO = auto()
    AWAIT_SNILS_PHOTO = auto()
    AWAIT_WORK_BOOK_PHOTO = auto()
    # --- КОНЕЦ НОВЫХ СОСТОЯНИЙ ---
    
    # Сбор данных о поданных документах
    GET_STANDARD_DOCS = auto()

    # Финальный шаг
    CONFIRM_CREATION = auto()