import logging
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    api_base_url: str
    api_admin_username: str
    api_admin_password: str
    api_manager_username: str
    api_manager_password: str
    log_level: str = "INFO"


settings = Settings()

logging.basicConfig(level=settings.log_level)
