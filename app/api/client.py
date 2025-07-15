import logging
from datetime import datetime
from io import BytesIO
from typing import Optional

import aiohttp

from app.config import settings


class ApiClient:
    """Асинхронный клиент для взаимодействия с API пенсионного консультанта."""

    def __init__(self, base_url: str):
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        # Словарь для хранения токенов: {user_id: token}
        self._user_tokens: dict[int, str] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def login(self, user_id: int, username: str, password: str) -> bool:
        """Аутентифицирует пользователя и сохраняет токен."""
        session = await self._get_session()
        try:
            async with session.post(
                f"{self._base_url}/auth/token",
                data={'username': username, 'password': password}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    token = data.get("access_token")
                    if token:
                        self._user_tokens[user_id] = token
                        logging.info(f"Successfully authenticated user {user_id}")
                        return True
                logging.warning(f"Failed to authenticate user {user_id}. Status: {response.status}")
                return False
        except aiohttp.ClientError as e:
            logging.error(f"Login error for user {user_id}: {e}")
            return False

    async def _get_headers(self, user_id: int) -> dict:
        """Возвращает заголовки с токеном авторизации для пользователя."""
        token = self._user_tokens.get(user_id)
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    async def _make_request(
        self, method: str, path: str, user_id: int, **kwargs
    ) -> Optional[dict]:
        """Универсальный метод для выполнения запросов к API."""
        session = await self._get_session()
        headers = await self._get_headers(user_id)
        if "Authorization" not in headers:
            logging.warning(f"No auth token for user {user_id} on request to {path}")
            # В зависимости от логики, можно либо возвращать ошибку, либо пробовать без токена
            # return {"error": "unauthorized"}
        
        # Обновляем заголовки из аргументов
        headers.update(kwargs.pop("headers", {}))

        url = f"{self._base_url}{path}"
        
        try:
            async with session.request(method, url, headers=headers, **kwargs) as response:
                if response.status in [200, 201, 202]:
                    return await response.json()
                elif response.status == 404:
                    return {"error": "not_found"}
                else:
                    logging.error(f"API Error: {response.status} for path {path}. Body: {await response.text()}")
                    return {"error": "api_error", "status_code": response.status}
        except aiohttp.ClientError as e:
            logging.error(f"Request exception for {path}: {e}")
            return None

    async def get_pension_types(self, user_id: int) -> Optional[list]:
        """Получает список доступных типов пенсий."""
        return await self._make_request("GET", "/pension_types", user_id=user_id)

    async def get_required_documents(self, user_id: int, pension_type_id: str) -> Optional[list]:
        """Получает список необходимых документов для типа пенсии."""
        return await self._make_request("GET", f"/pension_documents/{pension_type_id}", user_id=user_id)

    async def create_ocr_task(self, user_id: int, file_content: bytes, document_type: str) -> Optional[dict]:
        """Отправляет документ на OCR."""
        data = aiohttp.FormData()
        data.add_field('image', file_content, content_type='image/jpeg', filename='document.jpg')
        data.add_field('document_type', document_type)
        return await self._make_request(
            "POST", "/document_extractions", user_id=user_id, data=data
        )

    async def get_ocr_task_status(self, user_id: int, task_id: str) -> Optional[dict]:
        """Получает статус задачи OCR."""
        return await self._make_request("GET", f"/document_extractions/{task_id}", user_id=user_id)

    async def create_case(self, user_id: int, case_data: dict) -> Optional[dict]:
        """Создает новое дело."""
        # Копируем данные, чтобы не изменять оригинал в FSM
        data_to_send = case_data.copy()
        
        # Приводим даты к формату YYYY-MM-DD
        if p_data := data_to_send.get("personal_data"):
            if b_date := p_data.get("birth_date"):
                try:
                    dt = datetime.strptime(b_date, "%d.%m.%Y")
                    p_data["birth_date"] = dt.strftime("%Y-%m-%d")
                except ValueError:
                    logging.warning(f"Invalid birth_date format for case creation: {b_date}")
        
        if d_data := data_to_send.get("disability"):
             if d_date := d_data.get("date"):
                try:
                    dt = datetime.strptime(d_date, "%d.%m.%Y")
                    d_data["date"] = dt.strftime("%Y-%m-%d")
                except ValueError:
                    logging.warning(f"Invalid disability date format for case creation: {d_date}")

        return await self._make_request("POST", "/cases", user_id=user_id, json=data_to_send)

    async def get_case_status(self, user_id: int, case_id: int) -> Optional[dict]:
        """Получает статус дела."""
        return await self._make_request("GET", f"/cases/{case_id}", user_id=user_id)

    async def get_case_history(self, user_id: int, limit: int = 5, offset: int = 0) -> Optional[dict]:
        """Получает историю дел пользователя с пагинацией."""
        return await self._make_request(
            "GET",
            f"/cases/history?limit={limit}&offset={offset}",
            user_id=user_id
        )


# Создаем единственный экземпляр клиента
api_client = ApiClient(base_url=settings.api_base_url)
