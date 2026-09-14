"""Запуск: streamlit run maxgate/ui/app.py. UI не открывает базу Gate."""

import hashlib
import hmac
import re
from datetime import UTC, datetime
from html import escape

import streamlit as st
import streamlit_shadcn_ui as ui

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
CSS = """
<style>
html,body,#root {height:100%;overflow:hidden}
:root {font-family:Inter,system-ui,sans-serif;color:#09090b}
.stApp,.stApp p,.stApp h1,.stApp h3,.stApp label {font-family:Inter,system-ui,sans-serif!important}
.stApp p {font-size:14px!important}
.stApp [data-testid="stCaptionContainer"] p {font-size:13px!important;color:#71717a}
.stElementContainer:has(> [data-testid="stMarkdown"] style) {display:none}
[class*="st-key-field_"] {gap:6px!important}
.stApp [data-testid="stHeadingWithActionElements"] a {display:none}
.st-key-topbar {height:56px}
[data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {position:sticky;top:0;z-index:99;background:white}
[data-testid="stAppViewContainer"] {background:white}
header[data-testid="stHeader"] {display:none}
.stMainBlockContainer {max-width:1040px;padding:0 16px 40px}
[data-testid="stVerticalBlock"] {gap:16px}
[data-testid="stHorizontalBlock"] {gap:12px}
p, label {font-size:14px} h1 {font-size:24px!important;font-weight:600!important;padding:0!important}
h3 {font-size:16px!important;font-weight:600!important;padding:0!important}
[data-testid="stCaptionContainer"] p,.subtle {color:#71717a;font-size:13px}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:#e4e4e7!important;border-radius:8px!important;box-shadow:0 1px 2px #0000000d}
[data-baseweb="select"]>div {background:white;border-color:#e4e4e7;border-radius:6px;min-height:36px;font-size:14px}
.st-key-topbar {position:sticky;top:0;z-index:99;background:white;border-bottom:1px solid #e4e4e7;padding:9.5px 0;margin-bottom:12px}
.st-key-topbar [data-testid="stHorizontalBlock"] {align-items:center;flex-wrap:nowrap}
.st-key-topbar [data-testid="stColumn"] {min-width:0!important}
.st-key-topbar [data-testid="stMarkdownContainer"],.st-key-topbar p {margin:0!important}
.brand {display:flex;align-items:center;gap:10px;font-size:18px;font-weight:600;white-space:nowrap}
.logo {display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;background:#18181b;color:white;border-radius:7px;font-size:14px}
.bridge {display:inline-flex;align-items:center;gap:6px;border:1px solid #e4e4e7;border-radius:9999px;padding:3px 9px;font-size:12px;white-space:nowrap}
.dot {width:8px;height:8px;border-radius:100%;background:#16a34a}.offline .dot {background:#dc2626}
.badge {display:inline-block;border-radius:9999px;padding:2px 10px;font-size:12px;font-weight:600;background:#f4f4f5;color:#3f3f46;white-space:nowrap}
.active {background:#dcfce7;color:#166534}.logging_in,.password_required,.warning {background:#fef3c7;color:#92400e}
.session_lost,.error {background:#fee2e2;color:#991b1b}.topic {background:#e0e7ff;color:#3730a3}.muted {background:#18181b;color:white}
.account-title {display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
/* The native button covers the card, retaining keyboard focus and its accessible name. */
[class*="st-key-account_tile_"] {position:relative;gap:0!important;padding:16px!important;min-height:96px;transition:border-color .15s,box-shadow .15s}
[class*="st-key-account_tile_"]:hover {border-color:#a1a1aa!important;box-shadow:0 2px 5px #00000010}
[class*="st-key-account_tile_"] .stElementContainer:has([data-testid="stButton"]) {position:absolute;inset:0;width:100%!important;height:100%!important;z-index:1}
[class*="st-key-account_tile_"] [data-testid="stButton"] {height:100%}
[class*="st-key-account_tile_"] button {width:100%;height:100%;background:transparent!important;border:0!important;border-radius:8px;color:transparent!important}
[class*="st-key-account_tile_"] button:focus-visible {outline:2px solid #18181b;outline-offset:3px}
.account-tile-heading {display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}
.account-tile-heading strong {overflow-wrap:anywhere;min-width:0;font-size:15px;color:#09090b}
.account-list-heading {margin-top:8px;padding-bottom:12px}
.account-list-heading h3 {line-height:24px;margin:0 0 4px;color:#09090b}
.account-list-heading .subtle {font-size:13px;line-height:20px}
.st-key-account_list {gap:12px!important}
.account-tile-details {display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 16px;margin:0;padding-bottom:12px;font-size:13px;line-height:20px}
.account-tile-details dt,.account-tile-details dd {font-size:13px!important;line-height:20px}
.account-tile-details dt {color:#71717a}
.account-tile-details dd {margin:0;color:#09090b;overflow-wrap:anywhere}
.account-tile-reason {margin-top:10px;font-size:12px;line-height:18px;color:#71717a;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.account-tile-reason.error,.account-tile-reason.session_lost {color:#991b1b;background:transparent}
.st-key-danger {border-color:#fecaca!important}
.st-key-login_panel {max-width:380px;margin:18vh auto 0;padding:28px 24px;border-radius:12px!important;background:white}
[data-testid="stAppViewContainer"]:has(.st-key-login_panel) {background:#fafafa}
.countdown {font-size:40px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.2}
/* Shrink action columns to their buttons; let the descriptive column wrap. */
:is(.st-key-account_heading,.st-key-settings_heading,.st-key-danger) > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {flex-wrap:wrap;gap:8px;align-items:flex-start}
:is(.st-key-account_heading,.st-key-settings_heading,.st-key-danger) > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {flex:1 1 240px!important;min-width:0!important;width:auto!important}
:is(.st-key-account_heading,.st-key-settings_heading,.st-key-danger) > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {flex:0 0 auto!important;min-width:0!important;width:auto!important;margin-left:auto}
.st-key-actions {width:320px!important;max-width:100%}
.st-key-actions:has([class*="st-key-resume_"]) {width:350px!important}
.st-key-actions [data-testid="stHorizontalBlock"] {flex-wrap:wrap;gap:8px;justify-content:flex-end}
.st-key-actions [data-testid="stColumn"] {flex:0 0 auto!important;min-width:0!important}
.st-key-actions [data-testid="stColumn"]:nth-child(1) {width:90px!important}
.st-key-actions:has([class*="st-key-resume_"]) [data-testid="stColumn"]:nth-child(1) {width:120px!important}
.st-key-actions [data-testid="stColumn"]:nth-child(2) {width:134px!important}
.st-key-actions [data-testid="stColumn"]:nth-child(3) {width:80px!important}
.st-key-settings_heading > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {width:190px!important}
.st-key-danger > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {width:150px!important}
.st-key-topbar [data-testid="stHorizontalBlock"] {gap:8px;flex-wrap:wrap}
.st-key-topbar [data-testid="stColumn"]:first-child {flex:1 1 auto!important;width:auto!important}
.st-key-topbar [data-testid="stColumn"]:nth-child(2) {flex:0 0 auto!important;width:max-content!important;margin-left:auto}
.st-key-topbar [data-testid="stColumn"]:last-child {flex:0 0 80px!important;width:80px!important}
[class*="st-key-rename_form_actions_"] {gap:8px!important;flex-wrap:nowrap!important;justify-content:flex-end}
[class*="st-key-rename_form_actions_"] > [class*="st-key-rename_save_"] {flex:0 0 112px!important;width:112px!important}
[class*="st-key-rename_form_actions_"] > [class*="st-key-rename_cancel_"] {flex:0 0 96px!important;width:96px!important}
.event {display:grid;grid-template-columns:110px 80px minmax(0,1fr);gap:12px;padding:12px 0;border-bottom:1px solid #f4f4f5;font-size:13px}
.event code {white-space:pre-wrap;overflow-wrap:anywhere;color:#3f3f46;font-size:12.5px;background:none}
.event small {color:#a1a1aa}.event .badge {font-size:11px}
@media(max-width:640px) {
 .stMainBlockContainer {padding:0 16px 24px}
 .brand {font-size:15px;gap:6px}.bridge {font-size:10px;padding:3px 5px}
 .event {grid-template-columns:90px 1fr}.event code {grid-column:1/-1}
 .st-key-login_panel {margin-top:15vh}
}
</style>
"""


def markup(value):
    st.markdown(value, unsafe_allow_html=True)


def badge(text, kind=""):
    return f'<span class="badge {escape(kind)}">{escape(str(text))}</span>'


def button(text, key, *, disabled=False, variant="default", width="content"):
    with st.container(key=key):
        clicked = ui.button(text, key=key, variant=variant, disabled=disabled, width=width)
    return clicked and not disabled


def field(label, key, default="", *, secret=False, placeholder=None):
    revision = st.session_state.get(f"revision_{key}", 0)
    widget_key = f"{key}_{revision}" if revision else key
    with st.container(key=f"field_{key}"):
        return ui.input(
            label,
            value=str(default),
            type="password" if secret else "text",
            placeholder=placeholder or label,
            key=widget_key,
        )


def clear_field(key):
    revision = st.session_state.get(f"revision_{key}", 0)
    st.session_state.pop(f"{key}_{revision}" if revision else key, None)
    st.session_state[f"revision_{key}"] = revision + 1


def password_fingerprint(password):
    return hashlib.sha256(password.encode()).hexdigest()


def login(settings):
    expected = settings.ui_password.get_secret_value()
    if st.session_state.get("authenticated") == password_fingerprint(expected):
        return True
    with st.container(border=True, key="login_panel"):
        markup('<div class="brand"><span class="logo">↔</span>Max-gate</div>')
        st.caption("Вход для Operator")
        revision = st.session_state.get("revision_operator_password", 0)
        password_key = f"operator_password_{revision}" if revision else "operator_password"
        with st.form("operator_login_form", border=False, enter_to_submit=True):
            supplied = st.text_input("Пароль Operator", key=password_key, type="password")
            submitted = st.form_submit_button(
                "Войти", key="operator_login", type="primary", width="stretch"
            )
        if submitted:
            clear_field("operator_password")
            if hmac.compare_digest(supplied.encode(), expected.encode()):
                st.session_state["authenticated"] = password_fingerprint(expected)
                st.session_state.pop("login_error", None)
            else:
                st.session_state["login_error"] = True
            st.rerun()
        if st.session_state.get("login_error"):
            st.error("Неверный пароль")
        st.caption("UI не хранит секреты и не открывает базу Gate")
    return False


def invoke(client, method, path, data=None, *, clear_keys=(), error_key=None):
    st.session_state["_clear_keys"] = list(clear_keys)
    try:
        client.request(method, path, data)
    except (ApiUnavailable, ApiRejected) as exc:
        if error_key:
            st.session_state[error_key] = str(exc)
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


def owner_id(value):
    try:
        result = int(value)
        if result > 0:
            return result
    except (ValueError, TypeError):
        pass
    st.error("Telegram id Owner должен быть положительным целым числом")
    return None


def inbox_mode(key, default="private"):
    return st.selectbox(
        "Режим Inbox",
        ["private", "supergroup"],
        index=["private", "supergroup"].index(default),
        key=key,
        format_func=lambda v: (
            v
            + (
                " — личный чат Owner с ботом"
                if v == "private"
                else " — приватная супергруппа-форум"
            )
        ),
    )


def close_create():
    st.session_state.pop("create_error", None)
    st.session_state.pop("creating_account", None)
    clear_field("create_token")


@st.dialog("Создать учетку", width="small", on_dismiss=close_create)
def create_account(client, *, disabled=False):
    disabled = disabled or st.session_state.get("bridge_unavailable", False)
    st.caption("Один аккаунт MAX + один Telegram-бот, принадлежащие одному Owner.")
    st.warning("Перед входом включите пароль 2FA в MAX: Профиль → Приватность → Пароль для входа.")
    name = field("Название", "create_name", placeholder="напр. Отдел продаж")
    phone = field("Телефон MAX", "create_phone", placeholder="+7…")
    token = field("Токен Telegram-бота", "create_token", secret=True)
    st.caption("Один бот — одна учетка. Для private Inbox включите Topics у бота в BotFather.")
    owner = field("Telegram id Owner", "create_owner", placeholder="напр. 123456789")
    mode = inbox_mode("create_mode")
    channels = ui.switch(label="Переносить Channel", key="create_channels")
    st.caption("По умолчанию Channel не отражаются")
    left, right = st.columns(2)
    with left:
        if button("Отмена", "create_cancel", variant="outline"):
            close_create()
            st.rerun()
    with right:
        submitted = button("Создать", "create_submit", disabled=disabled)
    if submitted:
        st.session_state.pop("create_error", None)
        if not name.strip() or not phone.strip() or not token.strip():
            st.session_state["create_error"] = "Заполните название, телефон и токен бота"
        elif not re.fullmatch(r"\+\d{7,15}", phone.strip()):
            st.session_state["create_error"] = (
                "Телефон MAX: введите + и от 7 до 15 цифр, например +79991234567."
            )
        elif (parsed_owner := owner_id(owner)) is not None:
            invoke(
                client,
                "POST",
                "/accounts",
                dict(
                    name=name.strip(),
                    phone=phone.strip(),
                    tg_bot_token=token.strip(),
                    owner_tg_user_id=parsed_owner,
                    inbox_mode=mode,
                    relay_channels=channels,
                ),
                clear_keys=("create_token",),
                error_key="create_error",
            )

    if error := st.session_state.get("create_error"):
        st.error(error)


def confirm_action(client, account_id, action, clicked, *, disabled=False):
    titles = {
        "delete": "Удалить учетку",
        "logout": "Удалить Session",
        "login_again": "Войти заново",
    }
    descriptions = {
        "delete": "Учетка и её данные будут удалены из Gate. Topic в Telegram останутся.",
        "logout": "Session будет удалена. Для входа в MAX потребуются новый код SMS и пароль 2FA.",
        "login_again": "Текущая Session будет заменена. Потребуются новый код SMS и пароль 2FA.",
    }
    counter = f"{action}_dialog_revision_{account_id}"
    key = f"{action}_dialog_{account_id}_{st.session_state.get(counter, 0)}"
    pending = f"{action}_pending_{account_id}"
    if clicked:
        st.session_state[pending] = True
    if disabled or not st.session_state.get(pending):
        return
    confirmed = ui.alert_dialog(
        show=True,
        title=titles[action],
        description=descriptions[action],
        confirm_label=titles[action],
        cancel_label="Отмена",
        key=key,
    )
    if confirmed is not None:
        st.session_state.pop(pending, None)
        st.session_state[counter] = st.session_state.get(counter, 0) + 1
        if confirmed:
            path = f"/accounts/{account_id}"
            if action == "delete":
                invoke(client, "DELETE", path)
            invoke(client, "POST", path + ("/login" if action == "login_again" else "/logout"))
        st.rerun()


def login_wizard(client, account, events, *, disabled=False):
    state, account_id = account["state"], account["id"]
    path = f"/accounts/{account_id}"
    if state in {"new", "session_lost", "error"}:
        with st.container(border=True):
            st.subheader("Вход в MAX")
            st.warning(
                "Для входа в MAX должен быть включён пароль 2FA. Код SMS действует около 60 секунд."
            )
            if button(
                "Войти в MAX заново" if state != "new" else "Запросить SMS",
                f"login_{account_id}",
                disabled=disabled,
            ):
                invoke(client, "POST", path + "/login")
    if state not in {"logging_in", "password_required"}:
        return
    with st.container(border=True):
        remaining = deadline(events, state)
        kind = "password" if state == "password_required" else "code"
        st.subheader("Нужен пароль 2FA" if kind == "password" else "Код из SMS отправлен")
        if remaining is None:
            st.info("Gate устанавливает соединение с MAX. Ожидаем запрос кода.")
            return
        left, right = st.columns(2)
        with left:
            color = "#dc2626" if remaining <= 10 else "#d97706" if remaining <= 25 else "#09090b"
            markup(
                f'<div class="countdown" style="color:{color}">{remaining} с</div>'
                '<div class="subtle">Осталось на ввод, секунд</div>'
            )
            st.progress(remaining / 60)
            markup(
                f'<style>[data-testid="stProgressBar"] [role="progressbar"]>div'
                f"{{background:{color}}}</style>"
            )
            st.caption("Ориентировочный срок сервера MAX. Состояние обновляется автоматически.")
        with right:
            credential = field(
                "Пароль 2FA" if kind == "password" else "Код из SMS",
                f"credential_value_{account_id}_{kind}",
                secret=True,
                placeholder="Пароль 2FA" if kind == "password" else "6 цифр",
            )
            send = button(
                "Отправить пароль" if kind == "password" else "Отправить код",
                f"credential_send_{account_id}_{kind}",
                disabled=disabled or remaining == 0,
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


def account_settings(client, account, *, disabled=False):
    account_id = account["id"]
    with st.container(border=True, key="settings"):
        with st.container(key="settings_heading"):
            left, right = st.columns([3, 1])
            with left:
                st.subheader("Настройки")
                st.caption(
                    "Смена Owner или режима Inbox потребует нового /start. "
                    "Старые Topic сохранятся в Telegram."
                )
            with right:
                save = button(
                    "Сохранить настройки",
                    f"save_{account_id}",
                    disabled=disabled,
                    width="stretch",
                )

        columns = st.columns(4)
        with columns[0]:
            name = field("Название учетки", f"name_{account_id}", account["name"])
        with columns[1]:
            owner = field("Owner · Telegram id", f"owner_{account_id}", account["owner_tg_user_id"])
        with columns[2]:
            mode = inbox_mode(f"mode_{account_id}", account["inbox_mode"])
        with columns[3]:
            st.markdown("Relay Channel")
            channels = ui.switch(
                value=account["relay_channels"],
                label="Переносить Channel",
                key=f"channels_{account_id}",
            )
        if save:
            if (parsed_owner := owner_id(owner)) is not None:
                invoke(
                    client,
                    "PATCH",
                    f"/accounts/{account_id}",
                    dict(
                        name=name,
                        owner_tg_user_id=parsed_owner,
                        inbox_mode=mode,
                        relay_channels=channels,
                    ),
                )
    with st.container(border=True, key="danger"):
        left, right = st.columns([3, 1])
        with left:
            st.subheader("Удаление учетки")
            st.caption("Учетка и её данные будут удалены из Gate. Topic в Telegram останутся.")
        with right:
            clicked = button(
                "Удалить учетку",
                f"delete_{account_id}",
                variant="destructive",
                width="stretch",
                disabled=disabled,
            )
        confirm_action(client, account_id, "delete", clicked, disabled=disabled)


def chat_links(client, account, chats, *, disabled=False):
    with st.container(border=True):
        st.subheader("Чаты MAX")
        st.caption(
            f"{len(chats)} ChatLink · {sum(c['topic_id'] is not None for c in chats)} с Topic · "
            "появляются после первого входа в MAX"
        )
        if not chats:
            st.info("ChatLink появятся после первого входа в MAX")
        query = st.text_input("Поиск чата", key=f"chat_search_{account['id']}").strip().casefold()
        filtered = [
            link
            for link in chats
            if not query
            or query
            in " ".join(
                str(link.get(key) or "") for key in ("topic_title", "max_title", "max_chat_id")
            ).casefold()
        ]
        st.caption(f"Найдено: {len(filtered)}")
        # Keep each Elements tree below its 1,000-node limit. All batches stay
        # visible; batching is a rendering detail, not pagination.
        batch = []
        renaming = st.session_state.get("renaming_topic")
        for link in filtered:
            batch.append(link)
            if len(batch) == 50 or renaming == (account["id"], link["id"]):
                chat_batch(client, account, batch, disabled=disabled)
                batch = []
        if batch:
            chat_batch(client, account, batch, disabled=disabled)


def chat_batch(client, account, chats, *, disabled=False):
    actions = []
    with ui.elements(key=f"chat_batch_{account['id']}_{chats[0]['id']}") as el:
        for link in chats:
            link_id = link["id"]
            with el.stack(key=f"chat_{link_id}", gap="sm"):
                with el.grid(columns=2, min_column_width=240, gap="sm"):
                    with el.stack(gap="xs"):
                        el.text(
                            str(
                                link.get("topic_title") or link["max_title"] or link["max_chat_id"]
                            ),
                            variant="muted" if link["muted"] else "label",
                        )
                        kind = {"DIALOG": "Dialog", "CHAT": "Group", "CHANNEL": "Channel"}.get(
                            link["max_chat_type"], link["max_chat_type"]
                        )
                        with el.stack(direction="horizontal", gap="xs", wrap=True):
                            el.badge(kind, variant="secondary")
                            el.badge(
                                f"Topic {link['topic_id']}"
                                if link["topic_id"] is not None
                                else "нет Topic",
                                variant="default" if link["topic_id"] is not None else "outline",
                            )
                            if link["muted"]:
                                el.badge("Muted", variant="secondary")
                    with el.stack(direction="horizontal", gap="sm", align="center", justify="end"):
                        verb = "topic" if link["topic_id"] is None else "rename"
                        primary = el.button(
                            "Создать Topic" if verb == "topic" else "Переименовать",
                            key=f"{verb}_{link_id}",
                            variant="outline",
                            disabled=disabled,
                        )
                        mute = el.button(
                            "Unmute" if link["muted"] else "Mute",
                            key=f"mute_{link_id}",
                            variant="secondary",
                            disabled=disabled,
                        )
                        actions.append((link, verb, primary, mute))
                el.separator()
    for link, verb, primary, mute in actions:
        if primary.clicked and not disabled:
            if verb == "rename":
                st.session_state["renaming_topic"] = (account["id"], link["id"])
                st.rerun()
            elif account["inbox_chat_id"] is None:
                st.error("Сначала подключите Inbox: Owner должен отправить /start боту.")
            else:
                invoke(client, "POST", f"/accounts/{account['id']}/chats/{link['id']}/topic")
        if mute.clicked and not disabled:
            verb = "unmute" if link["muted"] else "mute"
            invoke(client, "POST", f"/accounts/{account['id']}/chats/{link['id']}/{verb}")
    last = chats[-1]
    if last["topic_id"] is not None and st.session_state.get("renaming_topic") == (
        account["id"],
        last["id"],
    ):
        rename_topic_form(client, account, last, disabled=disabled)


def rename_topic_form(client, account, link, *, disabled=False):
    link_id = link["id"]
    key = f"rename_value_{link_id}"
    with st.container(border=True):
        name = field("Новое название Topic", key)
        st.caption("От 1 до 128 символов. Изменения названия в MAX больше не переименуют Topic.")
        with st.container(
            key=f"rename_form_actions_{link_id}",
            horizontal=True,
            horizontal_alignment="right",
            gap="small",
        ):
            save = button(
                "Сохранить", f"rename_save_{link_id}", variant="secondary", disabled=disabled
            )
            cancel = button("Отмена", f"rename_cancel_{link_id}", variant="secondary")
        if cancel:
            st.session_state.pop("renaming_topic", None)
            clear_field(key)
            st.rerun()
        if save:
            name = name.strip()
            if not 1 <= len(name) <= 128:
                st.error("Введите название от 1 до 128 символов.")
                return
            try:
                client.request(
                    "PATCH", f"/accounts/{account['id']}/chats/{link_id}/topic", {"name": name}
                )
            except (ApiUnavailable, ApiRejected) as exc:
                st.error(str(exc))
                return
            st.session_state.pop("renaming_topic", None)
            clear_field(key)
            st.session_state["notice"] = "Название Topic сохранено"
            st.rerun()


def journal(events):
    with st.container(border=True):
        st.subheader("Журнал учетки")
        st.caption("Последние 1000 записей · содержимое сообщений не логируется")
        rows = []
        for event in events:
            stamp = datetime.fromisoformat(event["ts"])
            level = event["level"]
            rows.append(
                f'<div class="event"><div>{stamp:%H:%M:%S}<br><small>{stamp:%d.%m.%Y}</small></div>'
                f"<div>{badge(level, level.lower())}</div><code>{escape(event['message'])}</code></div>"
            )
        if rows:
            markup("".join(rows))
        else:
            st.caption("Событий пока нет")


def account_card(client, account, events, chats, *, disabled=False):
    account_id, state = account["id"], account["state"]
    path = f"/accounts/{account_id}"
    if button("← Все учетки", f"back_{account_id}", variant="ghost"):
        st.session_state.pop("selected_account", None)
        st.rerun()
    with st.container(key="account_heading"):
        left, right = st.columns([3, 2.4])
        with left:
            markup(
                f'<div class="account-title"><h1>{escape(account["name"])}</h1>{badge(STATES[state], state)}</div>'
            )
            st.caption(
                f"Учетка #{account_id} · MAX {account['phone']} · Inbox {account['inbox_chat_id'] or 'ещё не подключён'} · {account['inbox_mode']}"
            )
        with right, st.container(key="actions"):
            actions = st.columns([1.5 if state == "paused" else 1, 1.6, 0.8])
            with actions[0]:
                if state == "paused":
                    if button(
                        "Возобновить",
                        f"resume_{account_id}",
                        disabled=disabled,
                        width="stretch",
                    ):
                        invoke(client, "POST", path + "/resume")
                elif button(
                    "Ⅱ Пауза",
                    f"pause_{account_id}",
                    variant="outline",
                    width="stretch",
                    disabled=disabled or state not in {"active", "logging_in", "password_required"},
                ):
                    invoke(client, "POST", path + "/pause")
            with actions[1]:
                if state in {"active", "paused"}:
                    clicked = button(
                        "Войти заново",
                        f"login_again_{account_id}",
                        variant="outline",
                        width="stretch",
                        disabled=disabled,
                    )
                    confirm_action(client, account_id, "login_again", clicked, disabled=disabled)
            with actions[2]:
                clicked = button(
                    "Logout",
                    f"logout_{account_id}",
                    variant="secondary",
                    width="stretch",
                    disabled=disabled,
                )
                confirm_action(client, account_id, "logout", clicked, disabled=disabled)
    if account.get("state_reason"):
        st.error("Причина состояния: " + account["state_reason"])
    if state == "active" and account["inbox_chat_id"] is None:
        st.info(
            "Inbox ещё не подключён. Owner должен отправить боту /start, чтобы подключить Inbox."
        )
    login_wizard(client, account, events, disabled=disabled)
    tab = ui.tabs(
        options=["Карточка", "События", "Чаты MAX"],
        value="Карточка",
        key=f"tabs_{account_id}",
        width="stretch",
    )
    if tab == "События":
        journal(events)
    elif tab == "Чаты MAX":
        chat_links(client, account, chats, disabled=disabled)
    else:
        account_settings(client, account, disabled=disabled)


def topbar(online):
    with st.container(key="topbar"):
        brand, status, logout = st.columns([7, 2, 1])
        with brand:
            markup('<div class="brand"><span class="logo">↔</span>Max-gate</div>')
        with status:
            markup(
                f'<span class="bridge {"" if online else "offline"}"><span class="dot"></span>'
                f"{'Bridge online' if online else 'Bridge недоступен'}</span>"
            )
        with logout:
            if button("Выйти", "operator_logout", variant="secondary", width="stretch"):
                st.session_state.clear()
                st.rerun()


@st.fragment(run_every=2.0)
def dashboard(client):
    for key in st.session_state.pop("_clear_keys", []):
        clear_field(key)
    if notice := st.session_state.pop("notice", None):
        st.session_state.pop("creating_account", None)
        st.toast(notice)
    disabled = False
    error = None
    accounts = st.session_state.get("accounts_snapshot", [])
    selected = st.session_state.get("selected_account")
    events, chats = st.session_state.get(f"details_snapshot_{selected}", ([], []))
    try:
        client.get("/health")
        accounts = client.get("/accounts")
        st.session_state["accounts_snapshot"] = accounts
        if selected is not None and any(a["id"] == selected for a in accounts):
            events = client.get(f"/accounts/{selected}/events")
            chats = client.get(f"/accounts/{selected}/chats")
            st.session_state[f"details_snapshot_{selected}"] = (events, chats)
    except (ApiUnavailable, ApiRejected) as exc:
        disabled, error = True, str(exc)
    st.session_state["bridge_unavailable"] = disabled
    topbar(not disabled)
    if disabled:
        st.error(f"{error} — Действия временно недоступны. Данные могут быть устаревшими.")
        if button("Обновить", "refresh_unavailable", variant="outline"):
            st.rerun()
    account = next((a for a in accounts if a["id"] == selected), None)
    if account is not None:
        account_card(client, account, events, chats, disabled=disabled)
        return
    st.session_state.pop("selected_account", None)
    title, create = st.columns([4, 1])
    with title:
        st.title("Учетки")
        st.caption("MAX ↔ Telegram · обновляется каждые 2 с")
    with create:
        if button("+ Создать учетку", "create_open", disabled=disabled):
            st.session_state["creating_account"] = True
    if st.session_state.get("creating_account"):
        create_account(client, disabled=disabled)
    columns = st.columns(3)
    metrics = [
        ("Всего", len(accounts)),
        ("Активны", sum(a["state"] == "active" for a in accounts)),
        (
            "Нужно внимание",
            sum(a["state"] in {"error", "session_lost", "password_required"} for a in accounts),
        ),
    ]
    for index, (label, value) in enumerate(metrics):
        with columns[index]:
            ui.metric_card(label, value, key=f"metric_{index}")
    with st.container(key="account_list"):
        markup(
            '<div class="account-list-heading"><h3>Все учетки</h3>'
            '<div class="subtle">Нажмите на карточку, чтобы открыть</div></div>'
        )
        if not accounts:
            st.caption("Учеток пока нет")
            return
        for start in range(0, len(accounts), 2):
            columns = st.columns(2)
            for column, account in zip(columns, accounts[start : start + 2], strict=False):
                account_id, state = account["id"], account["state"]
                inbox = "личный" if account["inbox_mode"] == "private" else "группа"
                if account["inbox_mode"] != "private" and account["inbox_chat_id"] is not None:
                    inbox += f" · {account['inbox_chat_id']}"
                details = (
                    ("Телефон", account["phone"]),
                    ("Inbox", inbox),
                    ("Каналы", "да" if account["relay_channels"] else "нет"),
                    ("Владелец TG", account["owner_tg_user_id"]),
                )
                rows = "".join(
                    f"<dt>{label}</dt><dd>{escape(str(value))}</dd>" for label, value in details
                )
                reason = (
                    f'<div class="account-tile-reason {escape(state)}">'
                    f"{escape(account['state_reason'])}</div>"
                    if account.get("state_reason")
                    else ""
                )
                with column, st.container(border=True, key=f"account_tile_{account_id}"):
                    markup(
                        '<div class="account-tile-heading">'
                        f"<strong>{escape(account['name'])}</strong>{badge(STATES[state], state)}</div>"
                        f'<dl class="account-tile-details">{rows}</dl>{reason}'
                    )
                    if st.button(account["name"], key=f"open_{account_id}", width="stretch"):
                        st.session_state["selected_account"] = account_id
                        st.rerun()


def main():
    st.set_page_config(page_title="Max-gate · Operator", page_icon="↔", layout="wide")
    markup(CSS)
    try:
        settings = UiSettings()
    except Exception:
        st.error("UI не настроен: задайте MAXGATE_UI_PASSWORD и MAXGATE_INTERNAL_TOKEN")
        st.stop()
    if not login(settings):
        st.stop()
    dashboard(InternalApiClient(settings.internal_url, settings.internal_token))


if __name__ == "__main__":
    main()
