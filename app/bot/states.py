from aiogram.fsm.state import State, StatesGroup


class Login(StatesGroup):
    entering_login = State()
    entering_password = State()


class NewCase(StatesGroup):
    choosing_pension_type = State()
    # choosing_input_method = State() # <-- Старое состояние, можно удалить или оставить
    
    # Сбор персональных данных
    entering_last_name = State()
    entering_first_name = State()
    entering_middle_name = State()
    entering_birth_date = State()
    entering_snils = State()
    entering_gender = State()
    entering_citizenship = State()
    entering_dependents = State()

    # Управление документами
    managing_documents = State()
    uploading_document = State()
    verifying_document_data = State()

    # Финальное подтверждение
    confirming_case_creation = State()


class Ocr(StatesGroup):
    choosing_document_type = State()
    uploading_document = State()


class CheckStatus(StatesGroup):
    entering_id = State()
