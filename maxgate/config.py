from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAXGATE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    secret_key: SecretStr
    data_dir: Path = Path("/data")
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)
    shutdown_timeout: float = Field(default=30, ge=0)
    max_app_version: str | None = None
    internal_token: SecretStr | None = None
    ui_password: SecretStr | None = None
    tel: SecretStr | None = Field(default=None, validation_alias="TEL")
    tg_bot_token: SecretStr | None = Field(default=None, validation_alias="TG_BOT_TOKEN")
    max_pass: SecretStr | None = Field(default=None, validation_alias="MAX_PASS")

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir.resolve() / 'maxgate.db'}"
