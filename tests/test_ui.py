import copy
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.error import HTTPError, URLError

import pytest
from pydantic import SecretStr
from streamlit.components.v2.bidi_component.main import _make_trigger_id
from streamlit.proto.WidgetStates_pb2 import WidgetState
from streamlit.testing.v1 import AppTest, element_tree
from streamlit.testing.v1.errors import AppTestError
from streamlit_shadcn_ui.v2._component import private_component_key

from maxgate.ui.client import ApiRejected, ApiUnavailable, InternalApiClient


class FakeApi:
    def __init__(self):
        self.account = dict(
            id=1,
            name="Dev Account",
            phone="+10000000000",
            owner_tg_user_id=10,
            extra_owner_tg_user_ids=[],
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
    # AppTest currently omits V2 widgets from the browser state snapshot.
    # Include their persistent cells so reruns exercise real tab/input retention.
    original_widget_state = element_tree.get_widget_state

    def widget_state(node):
        if getattr(node, "type", None) == "bidi_component":
            state = node.root.session_state[node.proto.id]
            return WidgetState(
                id=node.proto.id,
                json_value=json.dumps({k: v for k, v in state.items() if k in {"meta", "state"}}),
            )
        return original_widget_state(node)

    monkeypatch.setattr(element_tree, "get_widget_state", widget_state)
    api = FakeApi()
    config = NS(
        ui_password=SecretStr("ui-test-password"),
        internal_token=SecretStr("fake-internal"),
        internal_url="http://fake",
    )
    monkeypatch.setattr("maxgate.ui.client.UiSettings", lambda: config)
    monkeypatch.setattr("maxgate.ui.client.InternalApiClient", lambda *_: api)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "maxgate/ui/app.py", default_timeout=10
    )
    yield app, api


def click(app, key, **values):
    for name, value in values.items():
        set_value(app, name, value)
    native = next((b for b in app.button if b.key == key), None)
    if native is not None:
        if native.disabled:
            with pytest.raises(AppTestError):
                native.click()
        else:
            native.click()
        app.run()
    else:
        trigger(app, key, "click", True)
    assert not app.exception
    return app


def mount_key(key):
    # AppTest does not yet expose a V2 interaction API. Feed the real component
    # transport state; production code only uses the library's public API.
    return private_component_key(key=key, kind="", identity={})


def component(app, key):
    native = next((b for b in [*app.button, *app.text_input] if b.key == key), None)
    if native is not None:
        return native
    for root in app.get("bidi_component"):
        if root.proto.id.endswith("-" + mount_key(key)):
            return root
        data = json.loads(root.proto.json)
        for node in element_nodes(data.get("props", {}).get("nodes", [])):
            if node["id"].split("/")[-1] == key:
                return NS(
                    proto=NS(id=root.proto.id, json=json.dumps({"props": node["props"]})),
                    node_id=node["id"],
                )
    raise StopIteration(key)


def element_nodes(nodes):
    for node in nodes:
        yield node
        yield from element_nodes(node.get("children", []))


def rendered_chat_nodes(app):
    for root in app.get("bidi_component"):
        data = json.loads(root.proto.json)
        if data["kind"] == "elements":
            yield from element_nodes(data["props"]["nodes"])


def has_component(app, key):
    try:
        component(app, key)
        return True
    except StopIteration:
        return False


def set_value(app, key, value):
    native = next((field for field in app.text_input if field.key == key), None)
    if native is not None:
        native.set_value(value)
        return
    data = json.loads(component(app, key).proto.json)
    state = dict(data["state"])
    if data["kind"] == "tabs":
        value = next(o["value"] for o in data["props"]["options"] if o["label"] == value)
    state.update(value=value, clientRevision=state["clientRevision"] + 1)
    app.session_state[mount_key(key)] = {"state": state}


def trigger(app, key, event, value):
    states = app._tree.get_widget_states()
    target = component(app, key)
    if getattr(target, "node_id", None):
        value = [{"nodeId": target.node_id, "type": event, "sequence": 1}]
        event = "events"
    widget = states.widgets.add(id=_make_trigger_id(target.proto.id, "events"))
    widget.json_trigger_value = json.dumps([{"event": event, "value": value}])
    app._run(states)


def decide(app, key, confirmed):
    trigger(app, key, "decision", confirmed)
    assert not app.exception


def is_disabled(app, key):
    native = next((b for b in app.button if b.key == key), None)
    if native is not None:
        return native.disabled
    return json.loads(component(app, key).proto.json)["props"]["disabled"]


def select_tab(app, tab):
    set_value(app, "tabs_1", tab)
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
    assert not app.get("component_instance")
    assert all(e.proto.isolate_styles for e in app.get("bidi_component"))
    assert not api.reads and not api.calls
    assert not has_component(app, "open_1")
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
    assert not has_component(app, "create_submit")
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
        create_owner="42",
    )
    assert api.calls[-1][0:2] == ("POST", "/accounts")
    assert api.calls[-1][2]["tg_bot_token"] == "new-bot-secret"
    assert api.calls[-1][2]["extra_owner_tg_user_ids"] == []
    assert mount_key("create_token") not in app.session_state
    assert "creating_account" not in app.session_state
    click(app, "open_1")
    click(app, "save_1", name_1="Renamed")
    assert api.calls[-1][0:2] == ("PATCH", "/accounts/1")
    assert api.calls[-1][2]["name"] == "Renamed"
    click(app, "delete_1")
    assert api.calls[-1][0] == "PATCH"
    decide(app, "delete_dialog_1_0", True)
    assert not app.exception
    assert api.calls[-1] == ("DELETE", "/accounts/1", None)


def test_several_owner_ids_are_split_into_primary_and_extra(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")
    click(
        app,
        "create_submit",
        create_name="Family",
        create_phone="+1234567890",
        create_token="new-bot-secret",
        create_owner="42, 77 77",
    )
    assert api.calls[-1][2]["owner_tg_user_id"] == 42
    assert api.calls[-1][2]["extra_owner_tg_user_ids"] == [77]
    click(app, "open_1")
    click(app, "save_1", owner_1="10, 11")
    assert api.calls[-1][0:2] == ("PATCH", "/accounts/1")
    assert api.calls[-1][2]["owner_tg_user_id"] == 10
    assert api.calls[-1][2]["extra_owner_tg_user_ids"] == [11]
    calls = len(api.calls)
    click(app, "save_1", owner_1="10, wife")
    assert any("Telegram id Owner" in e.value for e in app.error)
    assert len(api.calls) == calls


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
    app.run()
    assert json.loads(component(app, f"{action}_dialog_1_0").proto.json)["props"]["show"]
    decide(app, f"{action}_dialog_1_0", False)
    assert not api.calls
    click(app, f"{action}_1")
    assert component(app, f"{action}_dialog_1_1")
    assert not api.calls
    decide(app, f"{action}_dialog_1_1", True)
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
    assert mount_key("credential_value_1_code") not in app.session_state
    assert component(app, "credential_value_1_code_1")


def test_create_validation_and_cancel_clear_token(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")
    click(app, "create_submit", create_token="temporary-secret")
    assert any("Заполните название" in e.value for e in app.error)
    assert not api.calls
    click(app, "create_cancel")
    assert mount_key("create_token") not in app.session_state
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
def test_closed_confirmation_stays_closed_after_pause_and_rerun(ui, action):
    app, api = ui
    sign_in(app)
    click(app, f"{action}_1")
    app.run()
    assert json.loads(component(app, f"{action}_dialog_1_0").proto.json)["props"]["show"]
    decide(app, f"{action}_dialog_1_0", False)
    click(app, "pause_1")
    app.run()
    assert f"{action}_pending_1" not in app.session_state
    assert not has_component(app, f"{action}_dialog_1_1")
    assert api.calls == [("POST", "/accounts/1/pause", None)]


def test_rejected_create_keeps_dialog_and_renders_empty_token(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")

    def reject(*args):
        raise ApiRejected("Проверьте поля")

    api.request = reject
    click(
        app,
        "create_submit",
        create_name="New",
        create_phone="+1234567890",
        create_token="synthetic-token",
        create_owner="42",
    )
    app.run()
    assert app.session_state["creating_account"]
    token = component(app, "create_token_1")
    assert json.loads(token.proto.json)["state"]["value"] == ""
    assert mount_key("create_token") not in app.session_state


def test_open_create_disables_submit_during_outage(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")
    api.available = False
    app.run()
    assert is_disabled(app, "create_submit")
    click(
        app,
        "create_submit",
        create_name="New",
        create_phone="+1234567890",
        create_token="synthetic-token",
    )
    assert not api.calls
    assert app.session_state["creating_account"]
    api.available = True
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
    assert not has_component(app, "rename_save_1")
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
    assert not has_component(app, "rename_1")


def test_rename_api_error_does_not_expose_response_or_credentials():
    def rejected(*_, **kwargs):
        raise HTTPError("http://fake", 502, "secret-token", {}, io.BytesIO(b"secret-token"))

    client = InternalApiClient("http://fake", "secret-token", transport=rejected)
    with pytest.raises(ApiRejected, match="Telegram не смог переименовать Topic") as error:
        client.patch("/accounts/1/chats/1/topic", {"name": "Title"})
    assert "secret-token" not in str(error.value)


def test_saved_topic_title_after_new_ui_session(ui):
    app, api = ui
    original = api.get

    def get(path):
        result = original(path)
        if path.endswith("/chats"):
            result[0]["topic_title"] = "Анна (бывший ГБ ИП Олешко)"
        return result

    api.get = get
    sign_in(app)
    select_tab(app, "Чаты MAX")
    assert any(
        n["props"].get("text") == "Анна (бывший ГБ ИП Олешко)" for n in rendered_chat_nodes(app)
    )


def test_chat_list_search_without_pagination_or_button_iframes(ui):
    app, api = ui
    original = api.get

    def get(path):
        result = original(path)
        if path.endswith("/chats"):
            return [
                dict(result[0], id=i, max_title=f"Chat {i}", topic_id=None if i % 2 else 50)
                for i in range(1, 201)
            ]
        return result

    api.get = get
    sign_in(app)
    select_tab(app, "Чаты MAX")
    assert not any(
        e.proto.id.rsplit("-", 1)[-1].startswith(("topic_", "rename_", "mute_"))
        for e in app.get("component_instance")
    )
    roots = [json.loads(e.proto.json) for e in app.get("bidi_component")]
    assert sum(r["kind"] == "elements" for r in roots) == 4
    assert all(
        len(list(element_nodes(r["props"]["nodes"]))) <= 1000
        for r in roots
        if r["kind"] == "elements"
    )
    buttons = [n["props"] for n in rendered_chat_nodes(app) if n["type"] == "button"]
    assert sum(b["text"] == "Mute" for b in buttons) == 200
    assert sum(b["text"] == "Создать Topic" for b in buttons) == 100
    assert sum(b["text"] == "Переименовать" for b in buttons) == 100
    assert all(b["variant"] == "secondary" for b in buttons if b["text"] == "Mute")
    assert all(b["variant"] == "outline" for b in buttons if b["text"] != "Mute")
    assert not any(s.key.startswith("chat_page_") for s in app.selectbox)
    set_value(app, "tabs_1", "Чаты MAX")
    app.text_input(key="chat_search_1").set_value("Chat 200").run()
    assert has_component(app, "rename_200")
    assert not has_component(app, "rename_2")


def test_last_chat_actions_and_inline_rename_across_batches(ui):
    app, api = ui
    original = api.get

    def get(path):
        result = original(path)
        if path.endswith("/chats"):
            return [dict(result[0], id=i, max_title=f"Chat {i}") for i in range(1, 52)]
        return result

    api.get = get
    sign_in(app)
    select_tab(app, "Чаты MAX")
    click(app, "rename_51", tabs_1="Чаты MAX")
    assert has_component(app, "rename_value_51")
    click(app, "rename_save_51", rename_value_51="Last chat title")
    assert api.calls == [("PATCH", "/accounts/1/chats/51/topic", {"name": "Last chat title"})]
    click(app, "mute_51")
    assert api.calls[-1] == ("POST", "/accounts/1/chats/51/mute", None)
    app.run()
    assert len(api.calls) == 2


def test_create_invalid_phone_explains_format(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")
    click(
        app,
        "create_submit",
        create_name="111",
        create_phone="111",
        create_token="111",
        create_owner="111",
    )
    assert any("Телефон MAX" in e.value and "+" in e.value for e in app.error)
    assert not api.calls


def test_topics_disabled_error_is_actionable():
    def rejected(request, timeout):
        raise HTTPError(
            "http://fake", 400, "Bad Request", {}, io.BytesIO(b"Bot requires has_topics_enabled")
        )

    client = InternalApiClient("http://fake", "dummy", transport=rejected)
    with pytest.raises(ApiRejected, match="BotFather"):
        client.request("POST", "/accounts", {})


def test_create_error_survives_refresh(ui):
    app, api = ui
    sign_in(app, open_account=False)
    click(app, "create_open")

    def reject(*args):
        raise ApiRejected("Включите Topics в BotFather")

    api.request = reject
    click(
        app,
        "create_submit",
        create_name="111",
        create_phone="+79181111111",
        create_token="dummy",
        create_owner="42",
    )
    app.run()
    assert any("BotFather" in e.value for e in app.error)
