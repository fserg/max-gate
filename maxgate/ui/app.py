"""Запуск: streamlit run maxgate/ui/app.py. UI не открывает базу Gate."""

import hashlib
import hmac
from datetime import UTC, datetime

import streamlit as st

from maxgate.ui.client import ApiRejected, ApiUnavailable, InternalApiClient, UiSettings

STATES = {
    "new": "Новый",
    "logging_in": "Вход в MAX",
    "password_required": "Нужен пароль 2FA",
    "active": "Активен",
    "paused": "На паузе",
    "session_lost": "Session отозвана",
    "error": "Ошибка",
}


def password_fingerprint(password):
    return hashlib.sha256(password.encode()).hexdigest()


def login(settings):
    expected = settings.ui_password.get_secret_value()
    if st.session_state.get("authenticated") == password_fingerprint(expected):
        return True
    st.title("Max-gate")
    st.caption("Вход для Operator")

    def check():
        supplied = st.session_state.pop("operator_password", "")
        if hmac.compare_digest(supplied.encode(), expected.encode()):
            st.session_state["authenticated"] = password_fingerprint(expected)
            st.session_state.pop("login_error", None)
        else:
            st.session_state["login_error"] = True

    with st.form("operator_login"):
        st.text_input("Пароль Operator", type="password", key="operator_password")
        st.form_submit_button("Войти", on_click=check, type="primary")
    if st.session_state.get("login_error"):
        st.error("Неверный пароль")
    return False


def invoke(client, method, path, data=None, *, clear_keys=()):
    st.session_state["_clear_keys"] = list(clear_keys)
    try:
        client.request(method, path, data)
    except (ApiUnavailable, ApiRejected) as exc:
        st.error(str(exc))
        st.stop()
    st.session_state["notice"] = "Операция принята Gate"
    st.rerun()


def deadline(events, state):
    if state == "password_required":
        candidates = [e for e in events if e["message"].startswith("state=password_required")]
    else:
        candidates = [e for e in events if e["message"].startswith("SMS code requested")]
        starts = [e for e in events if e["message"].startswith("state=logging_in")]
        if starts:
            latest = max(e["ts"] for e in starts)
            candidates = [e for e in candidates if e["ts"] >= latest]
    if not candidates:
        return None
    stamp = datetime.fromisoformat(max(e["ts"] for e in candidates))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return max(0, int(60 - (datetime.now(UTC) - stamp).total_seconds()))


def create_account(client):
    with st.expander("Создать Account"):
        st.warning(
            "Перед входом включите пароль 2FA в MAX: Профиль → Приватность → Пароль для входа."
        )
        with st.form("create_account", clear_on_submit=True):
            name = st.text_input("Название", key="create_name")
            phone = st.text_input("Телефон MAX", placeholder="+7…", key="create_phone")
            token = st.text_input("Токен Telegram-бота", type="password", key="create_token")
            owner = st.number_input(
                "Telegram id Owner", min_value=1, value=79652610, step=1, key="create_owner"
            )
            mode = st.selectbox("Режим Inbox", ["private", "supergroup"], key="create_mode")
            channels = st.checkbox("Переносить Channel", key="create_channels")
            submitted = st.form_submit_button("Создать", type="primary")
        if submitted:
            if not name.strip() or not phone.strip() or not token.strip():
                st.error("Заполните название, телефон и токен бота")
            else:
                invoke(
                    client,
                    "POST",
                    "/accounts",
                    dict(
                        name=name.strip(),
                        phone=phone.strip(),
                        tg_bot_token=token.strip(),
                        owner_tg_user_id=owner,
                        inbox_mode=mode,
                        relay_channels=channels,
                    ),
                    clear_keys=("create_token",),
                )


def login_wizard(client, account, events):
    state, account_id = account["state"], account["id"]
    path = f"/accounts/{account_id}"
    if state in {"new", "session_lost", "error"}:
        st.warning(
            "Для входа в MAX должен быть включён пароль 2FA. Код SMS действует около 60 секунд."
        )
        if st.button(
            "Войти в MAX заново" if state != "new" else "Запросить SMS", key=f"login_{account_id}"
        ):
            invoke(client, "POST", path + "/login")
    if state not in {"logging_in", "password_required"}:
        return
    remaining = deadline(events, state)
    if remaining is None:
        st.info("Gate устанавливает соединение с MAX. Ожидаем запрос кода.")
        return
    st.metric("Осталось на ввод, секунд", remaining)
    st.caption("Ориентировочный срок сервера MAX. Состояние обновляется автоматически.")
    kind = "password" if state == "password_required" else "code"
    with st.form(f"credential_{account_id}_{kind}", clear_on_submit=True):
        credential = st.text_input(
            "Пароль 2FA" if kind == "password" else "Код из SMS",
            type="password",
            key=f"credential_value_{account_id}_{kind}",
        )
        send = st.form_submit_button(
            "Отправить пароль" if kind == "password" else "Отправить код", disabled=remaining == 0
        )
    if send:
        credential = credential.strip() if kind == "code" else credential
        if credential:
            invoke(
                client,
                "POST",
                path + f"/login/{kind}",
                {kind: credential},
                clear_keys=(f"credential_value_{account_id}_{kind}",),
            )
        else:
            st.error("Введите значение")
    if remaining == 0:
        st.warning("Время ввода истекло. Дождитесь завершения попытки и запустите вход заново.")


def account_card(client, account, events):
    account_id = account["id"]
    path = f"/accounts/{account_id}"
    st.subheader(f"Account {account_id} · {account['name']}")
    st.write(f"Состояние: **{STATES[account['state']]}** (`{account['state']}`)")
    if account.get("state_reason"):
        st.error(account["state_reason"])
    st.caption(f"MAX: {account['phone']} · Inbox: {account['inbox_chat_id'] or 'ещё не подключён'}")
    if account["state"] == "active" and account["inbox_chat_id"] is None:
        st.info("Owner должен отправить боту /start, чтобы подключить Inbox.")
    login_wizard(client, account, events)
    left, middle, right = st.columns(3)
    with left:
        if st.button(
            "Пауза",
            disabled=account["state"] not in {"active", "logging_in", "password_required"},
            key=f"pause_{account_id}",
        ):
            invoke(client, "POST", path + "/pause")
    with middle:
        if st.button(
            "Возобновить", disabled=account["state"] != "paused", key=f"resume_{account_id}"
        ):
            invoke(client, "POST", path + "/resume")
    with right:
        if st.button("Logout — удалить Session", key=f"logout_{account_id}"):
            invoke(client, "POST", path + "/logout")
    if account["state"] in {"active", "paused"}:
        with st.expander("Повторный вход в MAX"):
            st.warning("Текущая Session будет заменена. Потребуются новый код SMS и пароль 2FA.")
            if st.button("Войти в MAX заново", key=f"login_again_{account_id}"):
                invoke(client, "POST", path + "/login")
    with st.form(f"edit_{account_id}"):
        name = st.text_input("Название Account", value=account["name"])
        owner = st.number_input("Owner", min_value=1, value=account["owner_tg_user_id"], step=1)
        mode = st.selectbox(
            "Inbox",
            ["private", "supergroup"],
            index=["private", "supergroup"].index(account["inbox_mode"]),
        )
        channels = st.checkbox("Relay Channel", value=account["relay_channels"])
        st.caption(
            "Смена Owner или режима Inbox потребует нового /start. Старые Topic сохранятся в Telegram."
        )
        submitted = st.form_submit_button("Сохранить настройки")
    if submitted:
        invoke(
            client,
            "PATCH",
            path,
            dict(name=name, owner_tg_user_id=owner, inbox_mode=mode, relay_channels=channels),
        )
    with st.expander("Удаление Account"):
        confirmed = st.checkbox(
            "Удалить Account и его данные из Gate", key=f"delete_confirm_{account_id}"
        )
        st.caption("Topic в Telegram останутся.")
        if st.button("Удалить Account", disabled=not confirmed, key=f"delete_{account_id}"):
            invoke(client, "DELETE", path)


def chat_links(client, account, chats):
    if not chats:
        st.info("ChatLink появятся после первого входа в MAX")
        return
    for link in chats:
        label, action = st.columns([5, 1])
        label.write(
            f"**{link['max_title'] or link['max_chat_id']}** · {link['max_chat_type']} · "
            f"Topic {link['topic_id'] or 'не создан'}" + (" · Muted" if link["muted"] else "")
        )
        with action:
            if st.button("Unmute" if link["muted"] else "Mute", key=f"mute_{link['id']}"):
                verb = "unmute" if link["muted"] else "mute"
                invoke(client, "POST", f"/accounts/{account['id']}/chats/{link['id']}/{verb}")


@st.fragment(run_every=2.0)
def dashboard(client):
    for key in st.session_state.pop("_clear_keys", []):
        st.session_state[key] = ""
    try:
        client.get("/health")
        accounts = client.get("/accounts")
    except (ApiUnavailable, ApiRejected) as exc:
        st.error(str(exc))
        st.button("Обновить", key="refresh_unavailable")
        return
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    columns = st.columns(3)
    columns[0].metric("Account", len(accounts))
    columns[1].metric("Активны", sum(a["state"] == "active" for a in accounts))
    columns[2].metric(
        "Нужно внимание",
        sum(a["state"] in {"error", "session_lost", "password_required"} for a in accounts),
    )
    st.dataframe(
        [
            {"Account": a["id"], "Название": a["name"], "Состояние": STATES[a["state"]]}
            for a in accounts
        ],
        hide_index=True,
        width="stretch",
    )
    if not accounts:
        create_account(client)
        return
    by_id = {a["id"]: a for a in accounts}
    selected = st.selectbox(
        "Account", list(by_id), format_func=lambda key: f"{key} · {by_id[key]['name']}"
    )
    account = by_id[selected]
    try:
        events = client.get(f"/accounts/{selected}/events")
        chats = client.get(f"/accounts/{selected}/chats")
    except (ApiUnavailable, ApiRejected) as exc:
        st.error(str(exc))
        return
    create_account(client)
    card, journal, links = st.tabs(["Карточка", "События", "Чаты MAX"])
    with card:
        account_card(client, account, events)
    with journal:
        st.dataframe(events, hide_index=True, width="stretch")
    with links:
        chat_links(client, account, chats)


def main():
    st.set_page_config(page_title="Max-gate · Operator", page_icon="↔️", layout="wide")
    try:
        settings = UiSettings()
    except Exception:
        st.error("UI не настроен: задайте MAXGATE_UI_PASSWORD и MAXGATE_INTERNAL_TOKEN")
        st.stop()
    if not login(settings):
        st.stop()
    st.title("Max-gate")
    st.caption("Управление Account · MAX ↔ Telegram")
    if st.sidebar.button("Выйти из UI"):
        st.session_state.clear()
        st.rerun()
    dashboard(InternalApiClient(settings.internal_url, settings.internal_token))


if __name__ == "__main__":
    main()
