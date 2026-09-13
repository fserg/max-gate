import copy
import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from urllib.error import HTTPError, URLError

import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from maxgate.ui.client import ApiRejected, ApiUnavailable, InternalApiClient


class FakeApi:
    def __init__(self):
        self.account = dict(
            id=1,
            name="Dev Account",
            phone="+10000000000",
            owner_tg_user_id=10,
            inbox_mode="private",
            inbox_chat_id=20,
            relay_channels=False,
            state="active",
            state_reason=None,
        )
        self.calls = []
        self.available = True
        self.events = []

    def get(self, path):
        if not self.available:
            raise ApiUnavailable("Bridge недоступен. Действия временно недоступны.")
        if path == "/health":
            return {"status": "ok"}
        if path == "/accounts":
            return [copy.deepcopy(self.account)]
        if path.endswith("/events"):
            return self.events
        return [
            dict(
                id=1,
                max_title="Group",
                max_chat_id=30,
                max_chat_type="CHAT",
                topic_id=50,
                muted=False,
            )
        ]

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path.endswith("/pause"):
            self.account["state"] = "paused"
        if path.endswith("/resume"):
            self.account["state"] = "active"
        return {}


@pytest.fixture
def ui(monkeypatch):
    api = FakeApi()
    config = NS(
        ui_password=SecretStr("ui-test-password"),
        internal_token=SecretStr("fake-internal"),
        internal_url="http://fake",
    )
    monkeypatch.setattr("maxgate.ui.client.UiSettings", lambda: config)
    monkeypatch.setattr("maxgate.ui.client.InternalApiClient", lambda *_: api)
    app = AppTest.from_file("maxgate/ui/app.py", default_timeout=10)
    yield app, api


def sign_in(app):
    app.run()
    app.text_input(key="operator_password").set_value("ui-test-password")
    app.button[0].click().run()
    assert not app.exception


def test_password_gate_and_account_actions(ui):
    app, api = ui
    app.run()
    assert len(app.dataframe) == 0
    app.text_input(key="operator_password").set_value("wrong")
    app.button[0].click().run()
    assert app.error[0].value == "Неверный пароль"
    app.text_input(key="operator_password").set_value("ui-test-password")
    app.button[0].click().run()
    assert not app.exception
    assert any("active" in value.value for value in app.markdown)
    app.button(key="pause_1").click().run()
    assert api.calls[-1] == ("POST", "/accounts/1/pause", None)
    app.button(key="resume_1").click().run()
    assert api.calls[-1] == ("POST", "/accounts/1/resume", None)
    app.button(key="mute_1").click().run()
    assert api.calls[-1][1] == "/accounts/1/chats/1/mute"


def test_unavailable_bridge_hides_mutations(ui):
    app, api = ui
    api.available = False
    sign_in(app)
    assert "Bridge недоступен" in app.error[0].value
    assert not app.text_input
    assert not api.calls


def test_login_countdown_and_password_form(ui):
    app, api = ui
    api.account["state"] = "password_required"
    api.events = [
        dict(
            id=1, level="INFO", ts=datetime.now(UTC).isoformat(), message="state=password_required"
        )
    ]
    sign_in(app)
    assert any("Осталось" in metric.label for metric in app.metric)
    app.text_input(key="credential_value_1_password").set_value("fake-two-factor")
    next(button for button in app.button if button.label == "Отправить пароль").click().run()
    assert api.calls[-1] == ("POST", "/accounts/1/login/password", {"password": "fake-two-factor"})
    api.events[0]["ts"] = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    app.run()
    assert next(button for button in app.button if button.label == "Отправить пароль").disabled


def test_internal_api_client_bearer_and_safe_errors():
    calls = []

    class Response(io.BytesIO):
        pass

    def transport(request, timeout):
        calls.append(request)
        return Response(b'{"status":"ok"}')

    client = InternalApiClient("http://fake", "test-secret", transport=transport)
    assert client.get("/health") == {"status": "ok"}
    assert calls[0].get_header("Authorization") == "Bearer test-secret"

    def unavailable(*_, **kwargs):
        raise URLError("secret must not be printed")

    client._transport = unavailable
    with pytest.raises(ApiUnavailable, match="Bridge недоступен"):
        client.get("/health")

    def rejected(*_, **kwargs):
        raise HTTPError("http://fake", 401, "secret must not be printed", {}, None)

    client._transport = rejected
    with pytest.raises(ApiRejected, match="отклонил токен"):
        client.get("/accounts")


def test_creation_settings_and_delete_use_api(ui):
    app, api = ui
    sign_in(app)
    app.text_input(key="create_name").set_value("Second Account")
    app.text_input(key="create_phone").set_value("+1234567890")
    app.text_input(key="create_token").set_value("new-bot-secret")
    next(button for button in app.button if button.label == "Создать").click().run()
    assert api.calls[-1][0:2] == ("POST", "/accounts")
    assert api.calls[-1][2]["tg_bot_token"] == "new-bot-secret"
    assert app.text_input(key="create_token").value == ""
    next(text for text in app.text_input if text.label == "Название Account").set_value("Renamed")
    next(button for button in app.button if button.label == "Сохранить настройки").click().run()
    assert api.calls[-1][0:2] == ("PATCH", "/accounts/1")
    assert api.calls[-1][2]["name"] == "Renamed"
    assert app.button(key="delete_1").disabled
    app.checkbox(key="delete_confirm_1").check().run()
    app.button(key="delete_1").click().run()
    assert api.calls[-1] == ("DELETE", "/accounts/1", None)


def test_secondary_api_outage_hides_creation(ui):
    app, api = ui
    original_get = api.get

    def failed(path):
        if path.endswith("/events"):
            raise ApiUnavailable("Bridge недоступен")
        return original_get(path)

    api.get = failed
    sign_in(app)
    assert not app.text_input
    assert not api.calls


@pytest.mark.parametrize("value", ["  spaced password  ", "  "])
def test_ui_password_preserves_spaces(ui, value):
    app, api = ui
    api.account["state"] = "password_required"
    api.events = [
        dict(
            id=1, level="INFO", ts=datetime.now(UTC).isoformat(), message="state=password_required"
        )
    ]
    sign_in(app)
    app.text_input(key="credential_value_1_password").set_value(value)
    next(b for b in app.button if b.label == "Отправить пароль").click().run()
    assert api.calls[-1] == ("POST", "/accounts/1/login/password", {"password": value})
