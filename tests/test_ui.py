import copy
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from urllib.error import HTTPError, URLError
from uuid import uuid4

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
        self.reads = []
        self.available = True
        self.events = []

    def get(self, path):
        self.reads.append(path)
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


def click(app, key, **values):
    action, _, account_id = key.rpartition("_")
    if action in {"logout", "login_again", "delete"}:
        counter = f"{action}_dialog_revision_{account_id}"
        generation = app.session_state[counter] if counter in app.session_state else 0
        if generation:
            key = f"{key}_{generation}"
    for name, value in values.items():
        app.session_state[name] = value
    app.session_state[key] = {"value": True, "event_id": str(uuid4())}
    app.run()
    assert not app.exception
    return app


def component(app, key):
    return next(e for e in app.get("component_instance") if e.proto.id.endswith("-" + key))


def is_disabled(app, key):
    return json.loads(component(app, key).proto.json_args)["props"]["disabled"]


def select_tab(app, tab):
    app.session_state["tabs_1"] = tab
    app.run()
    assert not app.exception


def sign_in(app, *, open_account=True):
    app.run()
    click(app, "operator_login", operator_password="ui-test-password")
    if open_account:
        click(app, "open_1")


def test_password_gate_and_account_actions(ui):
    app, api = ui
    app.run()
    assert component(app, "operator_password")
    assert component(app, "operator_login")
    assert not api.reads and not api.calls
    assert not any(e.proto.id.endswith("-open_1") for e in app.get("component_instance"))
    click(app, "operator_login", operator_password="wrong")
    assert app.error[0].value == "Неверный пароль"
    assert not api.reads and not api.calls
    click(app, "operator_login", operator_password_1="ui-test-password")
    click(app, "open_1")
    assert not app.exception
    assert any('<span class="badge active">Активен</span>' in e.value for e in app.markdown)
    click(app, "pause_1")
    assert api.calls[-1] == ("POST", "/accounts/1/pause", None)
    click(app, "resume_1")
    assert api.calls[-1] == ("POST", "/accounts/1/resume", None)
    select_tab(app, "Чаты MAX")
    click(app, "mute_1", tabs_1="Чаты MAX")
    assert api.calls[-1][1] == "/accounts/1/chats/1/mute"


def test_unavailable_bridge_hides_mutations(ui):
    app, api = ui
    api.available = False
    sign_in(app, open_account=False)
    assert "Bridge недоступен" in app.error[0].value
    assert is_disabled(app, "create_open")
    assert not any(e.proto.id.endswith("-create_submit") for e in app.get("component_instance"))
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
    assert any("Осталось" in text.value for text in app.markdown)
    click(app, "credential_send_1_password", credential_value_1_password="fake-two-factor")
    assert api.calls[-1] == ("POST", "/accounts/1/login/password", {"password": "fake-two-factor"})
    api.events[0]["ts"] = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    app.run()
    assert is_disabled(app, "credential_send_1_password")


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
    click(app, "back_1")
    click(app, "create_open")
    click(
        app,
        "create_submit",
        create_name="Second Account",
        create_phone="+1234567890",
        create_token="new-bot-secret",
    )
    assert api.calls[-1][0:2] == ("POST", "/accounts")
    assert api.calls[-1][2]["tg_bot_token"] == "new-bot-secret"
    assert "create_token" not in app.session_state
    assert "creating_account" not in app.session_state
    click(app, "open_1")
    click(app, "save_1", name_1="Renamed")
    assert api.calls[-1][0:2] == ("PATCH", "/accounts/1")
    assert api.calls[-1][2]["name"] == "Renamed"
    click(app, "delete_1")
    assert api.calls[-1][0] == "PATCH"
    app.session_state["delete_dialog_1_0"] = {"open": False, "confirm": True}
    app.run()
    assert not app.exception
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
    assert is_disabled(app, "save_1")
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
    click(app, "credential_send_1_password", credential_value_1_password=value)
    assert api.calls[-1] == ("POST", "/accounts/1/login/password", {"password": value})


@pytest.mark.parametrize("bound", [True, False])
def test_create_topic_button_and_inbox_guidance(ui, bound):
    app, api = ui
    original_get = api.get

    def get(path):
        data = original_get(path)
        if path.endswith("/chats"):
            data[0]["topic_id"] = None
        return data

    api.get = get
    if not bound:
        api.account["inbox_chat_id"] = None
    sign_in(app)
    select_tab(app, "Чаты MAX")
    click(app, "topic_1", tabs_1="Чаты MAX")
    if bound:
        assert api.calls[-1] == ("POST", "/accounts/1/chats/1/topic", None)
    else:
        assert not api.calls
        assert any("подключите Inbox" in e.value for e in app.error)


def test_dashboard_card_back_navigation(ui):
    app, api = ui
    sign_in(app, open_account=False)
    assert component(app, "open_1")
    assert "selected_account" not in app.session_state
    click(app, "open_1")
    assert app.session_state["selected_account"] == 1
    assert component(app, "tabs_1")
    click(app, "back_1")
    assert "selected_account" not in app.session_state
    assert component(app, "open_1")
    assert not api.calls


@pytest.mark.parametrize(
    "action,endpoint", [("logout", "logout"), ("login_again", "login"), ("delete", None)]
)
def test_confirmation_can_cancel_and_reopen(ui, action, endpoint):
    app, api = ui
    sign_in(app)
    click(app, f"{action}_1")
    app.session_state[f"{action}_dialog_1_0"] = {"open": False, "confirm": False}
    app.run()
    assert not api.calls
    click(app, f"{action}_1")
    assert component(app, f"{action}_dialog_1_1")
    assert not api.calls
    app.session_state[f"{action}_dialog_1_1"] = {"open": False, "confirm": True}
    app.run()
    assert api.calls == [
        ("POST", f"/accounts/1/{endpoint}", None) if endpoint else ("DELETE", "/accounts/1", None)
    ]
    app.run()
    assert len(api.calls) == 1


def test_bridge_outage_disables_cached_card(ui):
    app, api = ui
    sign_in(app)
    api.available = False
    app.run()
    assert is_disabled(app, "pause_1")
    assert is_disabled(app, "save_1")
    click(app, "pause_1")
    assert not api.calls


def test_sms_waiting_countdown_and_trim(ui):
    app, api = ui
    api.account["state"] = "logging_in"
    sign_in(app)
    assert any("Ожидаем запрос кода" in e.value for e in app.info)
    api.events = [
        dict(ts=datetime.now(UTC).isoformat(), message="SMS code requested", level="INFO")
    ]
    app.run()
    click(app, "credential_send_1_code", credential_value_1_code=" 123456 ")
    assert api.calls[-1] == ("POST", "/accounts/1/login/code", {"code": "123456"})
    assert "credential_value_1_code" not in app.session_state
    assert component(app, "credential_value_1_code_1")


def test_create_validation_and_cancel_clear_token(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")
    click(app, "create_submit", create_token="temporary-secret")
    assert any("Заполните название" in e.value for e in app.error)
    assert not api.calls
    click(app, "create_cancel")
    assert "create_token" not in app.session_state
    # AppTest retains the dismissed dialog tree; supply its stale native select state.
    app.session_state["create_mode"] = "private"
    click(app, "create_open")
    assert component(app, "create_token_1")


def test_operator_logout_clears_authentication(ui):
    app, api = ui
    sign_in(app)
    assert "operator_password" not in app.session_state
    click(app, "operator_logout")
    assert "authenticated" not in app.session_state
    assert component(app, "operator_password")
    assert not api.calls


def test_events_escape_message_html(ui):
    app, api = ui
    api.events = [
        dict(ts=datetime.now(UTC).isoformat(), level="ERROR", message="<script>alert(1)</script>")
    ]
    sign_in(app)
    select_tab(app, "События")
    assert any("&lt;script&gt;" in e.value for e in app.markdown)


@pytest.mark.parametrize("action", ["logout", "login_again", "delete"])
def test_closed_confirmation_ignores_replayed_opener_after_pause(ui, action):
    app, api = ui
    sign_in(app)
    opener = f"{action}_1"
    event = {"value": True, "event_id": "original-open"}
    app.session_state[opener] = event
    app.run()
    app.session_state[f"{action}_dialog_1_0"] = {"open": False, "confirm": False}
    app.run()
    click(app, "pause_1")
    # Simulate remount: the frontend retains a click, library bookkeeping was reset.
    app.session_state[opener + "__non_resettable_state"] = {"value": False, "event_id": "remounted"}
    app.session_state[opener] = event
    app.run()
    assert f"{action}_pending_1" not in app.session_state
    assert not any("_dialog_1_" in e.proto.id for e in app.get("component_instance"))
    assert api.calls == [("POST", "/accounts/1/pause", None)]


def test_rejected_create_keeps_dialog_and_renders_empty_token(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")

    def reject(*args):
        raise ApiRejected("Проверьте поля")

    api.request = reject
    click(
        app, "create_submit", create_name="New", create_phone="+123", create_token="synthetic-token"
    )
    app.run()
    assert app.session_state["creating_account"]
    token = component(app, "create_token_1")
    assert json.loads(token.proto.json_args)["props"]["defaultValue"] == ""
    assert "create_token" not in app.session_state


def test_open_create_disables_submit_during_outage(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")
    api.available = False
    app.run()
    assert is_disabled(app, "create_submit")
    click(
        app, "create_submit", create_name="New", create_phone="+123", create_token="synthetic-token"
    )
    assert not api.calls
    assert app.session_state["creating_account"]
    # Replay an event accepted while disabled after availability/props change.
    event = {"value": True, "event_id": "offline-submit"}
    app.session_state["create_submit"] = event
    app.run()
    api.available = True
    app.session_state["create_submit__non_resettable_state"] = {
        "value": False,
        "event_id": "remounted",
    }
    app.session_state["create_submit"] = event
    app.session_state["create_name"] = "New"
    app.session_state["create_phone"] = "+123"
    app.session_state["create_token"] = "synthetic-token"
    app.run()
    assert not is_disabled(app, "create_submit")
    assert not api.calls


def test_journal_has_one_escaped_markup_block(ui):
    app, api = ui
    api.events = [
        dict(ts=datetime.now(UTC).isoformat(), level="ERROR", message=f"<b>{n}</b>")
        for n in range(1000)
    ]
    sign_in(app)
    select_tab(app, "События")
    rows = [e.value for e in app.markdown if '<div class="event">' in e.value]
    assert len(rows) == 1
    assert rows[0].count('<div class="event">') == 1000
    assert "&lt;b&gt;999&lt;/b&gt;" in rows[0]


def test_rename_topic_from_chat_row(ui):
    app, api = ui
    sign_in(app)
    select_tab(app, "Чаты MAX")
    click(app, "rename_1", tabs_1="Чаты MAX")
    click(app, "rename_save_1", tabs_1="Чаты MAX", rename_value_1="  Моё название  ")
    assert api.calls == [("PATCH", "/accounts/1/chats/1/topic", {"name": "Моё название"})]
    select_tab(app, "Чаты MAX")
    assert len(api.calls) == 1


def test_rename_topic_validation_cancel_and_safe_error(ui):
    app, api = ui
    sign_in(app)
    select_tab(app, "Чаты MAX")
    click(app, "rename_1", tabs_1="Чаты MAX")
    click(app, "rename_save_1", tabs_1="Чаты MAX", rename_value_1="   ")
    assert any("от 1 до 128" in e.value for e in app.error)
    assert not api.calls
    click(app, "rename_cancel_1", tabs_1="Чаты MAX")
    select_tab(app, "Чаты MAX")
    assert not any(e.proto.id.endswith("-rename_save_1") for e in app.get("component_instance"))
    click(app, "rename_1", tabs_1="Чаты MAX")

    def rejected(*_):
        raise ApiRejected("Telegram не смог переименовать Topic. Попробуйте позже.")

    api.request = rejected
    click(app, "rename_save_1", tabs_1="Чаты MAX", rename_value_1_1="Моё название")
    assert any("Telegram не смог" in e.value for e in app.error)


def test_no_rename_without_topic_or_when_bridge_unavailable(ui):
    app, api = ui
    sign_in(app)
    select_tab(app, "Чаты MAX")
    api.available = False
    select_tab(app, "Чаты MAX")
    assert is_disabled(app, "rename_1")
    click(app, "rename_1", tabs_1="Чаты MAX")
    assert not api.calls
    api.available = True
    original_get = api.get

    def get(path):
        data = original_get(path)
        if path.endswith("/chats"):
            data[0]["topic_id"] = None
        return data

    api.get = get
    select_tab(app, "Чаты MAX")
    assert not any(e.proto.id.endswith("-rename_1") for e in app.get("component_instance"))


def test_rename_api_error_does_not_expose_response_or_credentials():
    def rejected(*_, **kwargs):
        raise HTTPError("http://fake", 502, "secret-token", {}, io.BytesIO(b"secret-token"))

    client = InternalApiClient("http://fake", "secret-token", transport=rejected)
    with pytest.raises(ApiRejected, match="Telegram не смог переименовать Topic") as error:
        client.patch("/accounts/1/chats/1/topic", {"name": "Title"})
    assert "secret-token" not in str(error.value)
