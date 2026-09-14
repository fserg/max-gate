import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class UiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAXGATE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    ui_password: SecretStr = Field(min_length=1)
    internal_token: SecretStr = Field(min_length=1)
    internal_url: str = "http://127.0.0.1:8787"


class ApiUnavailable(RuntimeError):
    pass


class ApiRejected(RuntimeError):
    pass


class InternalApiClient:
    def __init__(self, url, token, *, transport=urlopen):
        self.url = url.rstrip("/")
        self._token = token.get_secret_value() if isinstance(token, SecretStr) else token
        self._transport = transport

    def request(self, method, path, data=None):
        request = Request(
            self.url + path,
            method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
        )
        try:
            with self._transport(request, timeout=8) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            # Only translate a known response; never render arbitrary bodies (credentials).
            if method == "POST" and path == "/accounts" and exc.code == 400:
                if exc.read(256) == b"Bot requires has_topics_enabled":
                    raise ApiRejected(
                        "У бота выключены темы. Включите Topics в BotFather для этого бота "
                        "и повторите создание учетки. Темы нужны для режима личного Inbox."
                    ) from None
            messages = {
                400: "Проверьте поля и состояние учетки.",
                401: "InternalApi отклонил токен доступа.",
                412: "Сначала подключите Inbox: Owner должен отправить /start боту.",
                409: "Этот Telegram-бот уже назначен другой учетке.",
                404: "Учетка или ChatLink больше не существует.",
                502: "Gate не смог выполнить операцию. Проверьте журнал учетки.",
            }
            if method == "PATCH" and path.endswith("/topic"):
                messages.update(
                    {
                        400: "Проверьте название (от 1 до 128 символов) и наличие Topic.",
                        502: "Telegram не смог переименовать Topic. Проверьте доступ бота к Topic и попробуйте позже.",
                    }
                )
            raise ApiRejected(messages.get(exc.code, f"InternalApi: HTTP {exc.code}")) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ApiUnavailable("Bridge недоступен. Действия временно недоступны.") from None

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None):
        return self.request("POST", path, data)

    def patch(self, path, data):
        return self.request("PATCH", path, data)

    def delete(self, path):
        return self.request("DELETE", path)
