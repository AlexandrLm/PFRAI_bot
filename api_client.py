# pfr_bot/api_client.py
import logging
import asyncio
from typing import List, Dict, Any, Optional, Callable, Awaitable

import httpx

# Импортируем новый параметр
from config import BACKEND_URL, BACKEND_USERNAME, BACKEND_PASSWORD, API_TASK_TIMEOUT_SEC

logger = logging.getLogger(__name__)

class ApiClientError(Exception):
    """Базовое исключение для ошибок API клиента."""
    pass

class AuthError(ApiClientError):
    """Ошибка аутентификации."""
    pass

class TaskTimeoutError(ApiClientError):
    """Ошибка, возникающая при превышении времени ожидания задачи."""
    pass

class ApiClient:
    """
    Асинхронный клиент для взаимодействия с API Пенсионного Консультанта.
    """
    def __init__(self, base_url: str, username: str, password: str):
        self._base_url = base_url
        self._username = username
        self._password = password
        self._token: Optional[str] = None
        # Увеличим таймаут по умолчанию для ожидания ответов от LLM
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)

    async def _get_auth_headers(self) -> Dict[str, str]:
        """Получает заголовок для аутентификации. Если токена нет, пытается войти."""
        if not self._token:
            await self.login()
        
        if not self._token:
             raise AuthError("Не удалось получить токен аутентификации.")

        return {"Authorization": f"Bearer {self._token}"}

    async def login(self):
        """Выполняет вход в систему и сохраняет токен."""
        logger.info("Попытка входа в API...")
        try:
            response = await self._client.post(
                "/api/v1/auth/token",
                data={"username": self._username, "password": self._password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0 # Логин должен быть быстрым
            )
            response.raise_for_status()
            self._token = response.json()["access_token"]
            logger.info("Успешный вход в API.")
        except httpx.HTTPStatusError as e:
            logger.error(f"Ошибка аутентификации в API: {e.response.status_code} - {e.response.text}")
            self._token = None
            raise AuthError(f"Ошибка аутентификации: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Сетевая ошибка при входе в API: {e}")
            self._token = None
            raise ApiClientError(f"Сетевая ошибка: не удалось подключиться к {e.request.url}") from e
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при входе в API: {e}", exc_info=True)
            self._token = None
            raise ApiClientError("Непредвиденная ошибка при аутентификации") from e

    async def _make_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Обертка для выполнения запросов с автоматической аутентификацией."""
        try:
            headers = await self._get_auth_headers()
            response = await self._client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            # Если 401, возможно, токен истек. Попробуем обновить и повторить.
            if e.response.status_code == 401 and 'Authorization' in e.request.headers:
                logger.warning("Токен мог истечь, попытка перелогина...")
                self._token = None # Сбрасываем токен
                headers = await self._get_auth_headers()
                response = await self._client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return response
            logger.error(f"Ошибка API запроса {method} {url}: {e.response.status_code} - {e.response.text}")
            raise # Передаем исключение дальше, чтобы его можно было обработать в хендлерах
        except (httpx.RequestError, ApiClientError) as e: # Ловим сетевые ошибки и ошибки аутентификации
            logger.error(f"Ошибка при выполнении запроса {method} {url}: {e}")
            raise ApiClientError(f"Ошибка сети или аутентификации при доступе к {url}") from e


    async def get_pension_types(self) -> List[Dict[str, Any]]:
        """Получает список доступных типов пенсий."""
        response = await self._make_request("GET", "/api/v1/pension_types")
        return response.json()

    async def get_pension_documents(self, pension_type_id: str) -> List[Dict[str, Any]]:
        """Получает список требуемых документов для типа пенсии."""
        response = await self._make_request("GET", f"/api/v1/pension_documents/{pension_type_id}")
        return response.json()

    async def submit_ocr_task(self, document_type: str, image_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Отправляет документ на OCR обработку."""
        files = {'image': (filename, image_bytes)}
        data = {'document_type': document_type}
        
        response = await self._make_request(
            "POST",
            "/api/v1/document_extractions", 
            files=files, 
            data=data
        )
        return response.json()

    async def poll_task_status(self, endpoint_url: str, progress_callback: Optional[Callable[[int], Awaitable[None]]] = None, timeout_sec: int = API_TASK_TIMEOUT_SEC, min_interval: int = 2, max_interval: int = 10) -> Dict[str, Any]:
        laps = 0
        current_interval = min_interval
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout_sec:
            if progress_callback:
                await progress_callback(laps)
            
            try:
                response = await self._make_request("GET", endpoint_url)
                status_data = response.json()
                current_status = status_data.get('status') or status_data.get('final_status')
                if current_status in ['COMPLETED', 'СООТВЕТСТВУЕТ', 'НЕ СООТВЕТСТВУЕТ', 'FAILED', 'ERROR_PROCESSING']:
                    logger.info(f"Задача {endpoint_url} завершена со статусом {current_status}.")
                    return status_data
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    logger.warning(f"Серверная ошибка {e.response.status_code}, повтор через {current_interval} сек")
                else:
                    raise
            
            await asyncio.sleep(current_interval)
            laps += 1
            current_interval = min(current_interval * 1.5, max_interval)
        
        raise TaskTimeoutError(f"Время ожидания задачи {endpoint_url} истекло ({timeout_sec} сек).")


    async def get_ocr_task_status(
        self,
        task_id: str,
        progress_callback: Optional[Callable[[int], Awaitable[None]]] = None
    ) -> Dict[str, Any]:
        """Проверяет статус задачи OCR с использованием опроса."""
        endpoint = f"/api/v1/document_extractions/{task_id}"
        return await self.poll_task_status(endpoint, progress_callback=progress_callback)

    async def create_case(self, case_data: Dict[str, Any]) -> Dict[str, Any]:
        """Создает новое дело."""
        response = await self._make_request("POST", "/api/v1/cases", json=case_data)
        return response.json()

    async def get_case_status(
        self,
        case_id: int,
        progress_callback: Optional[Callable[[int], Awaitable[None]]] = None
    ) -> Dict[str, Any]:
        """Получает статус дела по его ID с использованием опроса."""
        endpoint = f"/api/v1/cases/{case_id}/status"
        return await self.poll_task_status(endpoint, progress_callback=progress_callback)

# Синглтон экземпляр клиента
api_client = ApiClient(BACKEND_URL, BACKEND_USERNAME, BACKEND_PASSWORD)