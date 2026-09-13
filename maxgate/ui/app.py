"""Запуск: streamlit run maxgate/ui/app.py. UI не открывает базу Gate."""

import hashlib
import hmac
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
:root {font-family:Inter,system-ui,sans-serif;color:#09090b}
.stApp,.stApp p,.stApp h1,.stApp h3,.stApp label {font-family:Inter,system-ui,sans-serif!important}
.stApp p {font-size:14px!important}
.stApp [data-testid="stCaptionContainer"] p {font-size:13px!important;color:#71717a}
.stElementContainer:has(style):not(:has(iframe)) {display:none}
[class*="st-key-field_"] {gap:6px!important}
.stApp [data-testid="stHeadingWithActionElements"] a {display:none}
.st-key-actions [data-testid="stHorizontalBlock"] {flex-wrap:nowrap;gap:8px}
.st-key-actions [data-testid="stColumn"] {min-width:0!important}
[class*="st-key-account_row_"] [data-testid="stHorizontalBlock"] {flex-wrap:nowrap;align-items:center;gap:8px}
[class*="st-key-account_row_"] [data-testid="stColumn"] {min-width:0!important}
[class*="st-key-account_row_"] [data-testid="stColumn"]:first-child {flex:1 1 70%!important;width:70%!important}
[class*="st-key-account_row_"] [data-testid="stColumn"]:last-child {flex:0 0 auto!important;width:auto!important}
.st-key-topbar {height:56px}
[data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {position:sticky;top:0;z-index:99;background:white}
/* Radix renders its dialog in a fixed portal, so automatic iframe height is zero. */
/* Move the library's Show Dialog trigger above the clipped layer. Increasing
   the iframe by twice the offset keeps the Radix dialog centered in the viewport. */
[class*="st-key-st-key-dialog_layer_"] {overflow:hidden!important}
[class*="st-key-st-key-dialog_layer_"] iframe {
 height:calc(100vh + 80px)!important;width:100%!important;transform:translateY(-40px);
}
[class*="st-key-st-key-dialog_layer_"] [data-testid="stElementContainer"]:has(iframe) {
 position:absolute;inset:0;width:100%!important;height:100vh!important;
}


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
.st-key-topbar [data-testid="stElementContainer"]:has(iframe),
.st-key-topbar [data-testid="stElementContainer"]:has(iframe)>div {height:36px!important}
.st-key-topbar iframe {height:36px!important;vertical-align:top}
.st-key-topbar [data-testid="stMarkdownContainer"],.st-key-topbar p {margin:0!important}
.brand {display:flex;align-items:center;gap:10px;font-size:18px;font-weight:600;white-space:nowrap}
.logo {display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;background:#18181b;color:white;border-radius:7px;font-size:14px}
.bridge {display:inline-flex;align-items:center;gap:6px;border:1px solid #e4e4e7;border-radius:9999px;padding:3px 9px;font-size:12px;white-space:nowrap}
.dot {width:8px;height:8px;border-radius:100%;background:#16a34a}.offline .dot {background:#dc2626}
.badge {display:inline-block;border-radius:9999px;padding:2px 10px;font-size:12px;font-weight:600;background:#f4f4f5;color:#3f3f46;white-space:nowrap}
.active {background:#dcfce7;color:#166534}.logging_in,.password_required,.warning {background:#fef3c7;color:#92400e}
.session_lost,.error {background:#fee2e2;color:#991b1b}.topic {background:#e0e7ff;color:#3730a3}.muted {background:#18181b;color:white}
.account-title {display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.st-key-danger {border-color:#fecaca!important}
.st-key-login_panel {max-width:380px;margin:18vh auto 0;padding:28px 24px;border-radius:12px!important;background:white}
[data-testid="stAppViewContainer"]:has(.st-key-login_panel) {background:#fafafa}
.countdown {font-size:40px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.2}
/* Keep chat actions together when the row wraps on narrow screens. */
[class*="st-key-chat_row_"] {padding:12px 0;border-bottom:1px solid #f4f4f5}
[data-testid="stLayoutWrapper"]:last-child > [class*="st-key-chat_row_"] {border-bottom:0}
[class*="st-key-chat_row_"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {flex-wrap:wrap;align-items:center;gap:8px}
[class*="st-key-chat_row_"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {flex:1 1 240px!important;min-width:0!important;width:auto!important;overflow-wrap:anywhere}
[class*="st-key-chat_row_"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {flex:0 0 228px!important;min-width:0!important;width:228px!important;margin-left:auto}
[class*="st-key-chat_row_"] [data-testid="stColumn"]:last-child:not(:has([class*="st-key-topic_"])) {flex-basis:86px!important;width:86px!important}
[class*="st-key-chat_actions_"] > [class*="st-key-topic_"] {flex:0 0 134px!important;width:134px!important}
[class*="st-key-chat_actions_"] > [class*="st-key-mute_"] {flex:0 0 86px!important;width:86px!important}
[class*="st-key-chat_actions_"] {gap:8px!important;flex-wrap:nowrap!important;justify-content:flex-end}
.event {display:grid;grid-template-columns:110px 80px minmax(0,1fr);gap:12px;padding:12px 0;border-bottom:1px solid #f4f4f5;font-size:13px}
.event code {white-space:pre-wrap;overflow-wrap:anywhere;color:#3f3f46;font-size:12.5px;background:none}
.event small {color:#a1a1aa}.event .badge {font-size:11px}
@media(max-width:640px) {
 .stMainBlockContainer {padding:0 16px 24px}
 .st-key-topbar [data-testid="stColumn"] {flex:1 1 auto!important;width:auto!important}
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


def button(text, key, *, disabled=False, variant="default", class_name="", **props):
    # Enforce disabled in Python too: custom components can retain a click event.
    event = st.session_state.get(key, {})
    clicked = ui.button(
        text,
        key=key,
        variant=variant,
        disabled=disabled,
        class_name="h-9 rounded-md " + class_name,
        style={"maxWidth": "100%", **props.pop("style", {})},
        **props,
    )
    # The frontend retains the last click across remounts. Library bookkeeping can
    # briefly move back to its initial event_id; never treat that as a new click.
    consumed = st.session_state.setdefault("consumed_button_events", {})
    event_id = event.get("event_id")
    if not clicked or not event.get("value") or consumed.get(key) == event_id:
        return False
    consumed[key] = event_id
    return not disabled


def confirmation_key(action, account_id):
    generation = st.session_state.get(f"{action}_dialog_revision_{account_id}", 0)
    base = f"{action}_{account_id}"
    return f"{base}_{generation}" if generation else base


def field(label, key, default="", *, secret=False, placeholder=None):
    revision = st.session_state.get(f"revision_{key}", 0)
    widget_key = f"{key}_{revision}" if revision else key
    with st.container(key=f"field_{key}"):
        st.markdown(escape(label))
        return ui.input(
            default_value=str(default),
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
        supplied = field("Пароль Operator", "operator_password", secret=True)
        if button("Войти", "operator_login", class_name="w-full"):
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
    st.session_state.pop("creating_account", None)
    clear_field("create_token")


@st.dialog("Создать Account", width="small", on_dismiss=close_create)
def create_account(client, *, disabled=False):
    disabled = disabled or st.session_state.get("bridge_unavailable", False)
    st.caption("Один аккаунт MAX + один Telegram-бот, принадлежащие одному Owner.")
    st.warning("Перед входом включите пароль 2FA в MAX: Профиль → Приватность → Пароль для входа.")
    name = field("Название", "create_name", placeholder="напр. Отдел продаж")
    phone = field("Телефон MAX", "create_phone", placeholder="+7…")
    token = field("Токен Telegram-бота", "create_token", secret=True)
    st.caption("Один бот — один Account. Для private Inbox включите Topics у бота в BotFather.")
    owner = field("Telegram id Owner", "create_owner", "79652610")
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
        if not name.strip() or not phone.strip() or not token.strip():
            st.error("Заполните название, телефон и токен бота")
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
            )


def confirm_action(client, account_id, action, clicked, *, disabled=False):
    titles = {
        "delete": "Удалить Account",
        "logout": "Удалить Session",
        "login_again": "Войти заново",
    }
    descriptions = {
        "delete": "Account и его данные будут удалены из Gate. Topic в Telegram останутся.",
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
        show=clicked,
        title=titles[action],
        description=descriptions[action],
        confirm_label=titles[action],
        cancel_label="Отмена",
        key=key,
    )
    if confirmed or (not clicked and not st.session_state.get(key, {}).get("open", False)):
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
    with st.container(border=True):
        st.subheader("Настройки")
        st.caption(
            "Смена Owner или режима Inbox потребует нового /start. Старые Topic сохранятся в Telegram."
        )
        columns = st.columns(4)
        with columns[0]:
            name = field("Название Account", f"name_{account_id}", account["name"])
        with columns[1]:
            owner = field("Owner · Telegram id", f"owner_{account_id}", account["owner_tg_user_id"])
        with columns[2]:
            mode = inbox_mode(f"mode_{account_id}", account["inbox_mode"])
        with columns[3]:
            st.markdown("Relay Channel")
            channels = ui.switch(
                default_checked=account["relay_channels"],
                label="Переносить Channel",
                key=f"channels_{account_id}",
            )
        if button("Сохранить настройки", f"save_{account_id}", disabled=disabled):
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
            st.subheader("Удаление Account")
            st.caption("Account и его данные будут удалены из Gate. Topic в Telegram останутся.")
        with right:
            clicked = button(
                "Удалить Account",
                confirmation_key("delete", account_id),
                variant="outline",
                class_name="text-red-600 border-red-200",
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
        for link in chats:
            with st.container(key=f"chat_row_{link['id']}"):
                label, actions = st.columns([1, 1])
                with label:
                    title = escape(str(link["max_title"] or link["max_chat_id"]))
                    markup(
                        f'<div style="opacity:{0.55 if link["muted"] else 1};font-weight:500">{title}</div>'
                    )
                    kind = {"DIALOG": "Dialog", "CHAT": "Group", "CHANNEL": "Channel"}.get(
                        link["max_chat_type"], link["max_chat_type"]
                    )
                    markup(
                        badge(kind)
                        + " "
                        + badge(
                            f"Topic {link['topic_id']}"
                            if link["topic_id"] is not None
                            else "нет Topic",
                            "topic" if link["topic_id"] is not None else "",
                        )
                        + (" " + badge("Muted", "muted") if link["muted"] else "")
                    )
                with actions:
                    with st.container(
                        key=f"chat_actions_{link['id']}",
                        horizontal=True,
                        horizontal_alignment="right",
                        gap="small",
                    ):
                        if link["topic_id"] is None and button(
                            "Создать Topic",
                            f"topic_{link['id']}",
                            variant="outline",
                            class_name="w-full",
                            disabled=disabled,
                        ):
                            if account["inbox_chat_id"] is None:
                                st.error(
                                    "Сначала подключите Inbox: Owner должен отправить /start боту."
                                )
                            else:
                                invoke(
                                    client,
                                    "POST",
                                    f"/accounts/{account['id']}/chats/{link['id']}/topic",
                                )
                        if button(
                            "Unmute" if link["muted"] else "Mute",
                            f"mute_{link['id']}",
                            variant="secondary",
                            class_name="w-full",
                            disabled=disabled,
                        ):
                            verb = "unmute" if link["muted"] else "mute"
                            invoke(
                                client,
                                "POST",
                                f"/accounts/{account['id']}/chats/{link['id']}/{verb}",
                            )


def journal(events):
    with st.container(border=True):
        st.subheader("Журнал Account")
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
    if button("← Все Account", f"back_{account_id}", variant="ghost", class_name="text-zinc-500"):
        st.session_state.pop("selected_account", None)
        st.rerun()
    left, right = st.columns([3, 2.4])
    with left:
        markup(
            f'<div class="account-title"><h1>{escape(account["name"])}</h1>{badge(STATES[state], state)}</div>'
        )
        st.caption(
            f"Account #{account_id} · MAX {account['phone']} · Inbox {account['inbox_chat_id'] or 'ещё не подключён'} · {account['inbox_mode']}"
        )
    with right, st.container(key="actions"):
        actions = st.columns([1.5 if state == "paused" else 1, 1.6, 0.8])
        with actions[0]:
            if state == "paused":
                if button("Возобновить", f"resume_{account_id}", disabled=disabled):
                    invoke(client, "POST", path + "/resume")
            elif button(
                "Ⅱ Пауза",
                f"pause_{account_id}",
                variant="outline",
                disabled=disabled or state not in {"active", "logging_in", "password_required"},
            ):
                invoke(client, "POST", path + "/pause")
        with actions[1]:
            if state in {"active", "paused"}:
                clicked = button(
                    "Войти заново",
                    confirmation_key("login_again", account_id),
                    variant="outline",
                    disabled=disabled,
                )
                confirm_action(client, account_id, "login_again", clicked, disabled=disabled)
        with actions[2]:
            clicked = button(
                "Logout",
                confirmation_key("logout", account_id),
                variant="secondary",
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
        default_value="Карточка",
        key=f"tabs_{account_id}",
        className="w-full",
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
            if button("Выйти", "operator_logout", variant="secondary"):
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
        st.title("Account")
        st.caption("MAX ↔ Telegram · обновляется каждые 2 с")
    with create:
        if button("+ Создать Account", "create_open", disabled=disabled):
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
            with ui.element(
                "div",
                key=f"metric_{index}",
                style={
                    "border": "1px solid #e4e4e7",
                    "borderRadius": "8px",
                    "padding": "16px 20px",
                    "boxShadow": "0 1px 2px #0000000d",
                    "fontFamily": "system-ui, sans-serif",
                },
            ):
                ui.element(
                    "div", label, style={"fontSize": "13px", "fontWeight": 500, "color": "#71717a"}
                )
                ui.element(
                    "div",
                    str(value),
                    style={
                        "fontSize": "28px",
                        "fontWeight": 600,
                        "lineHeight": "34px",
                        "color": "#dc2626" if index == 2 and value else "#09090b",
                    },
                )
    with st.container(border=True):
        st.subheader("Все Account")
        st.caption("Нажмите на строку, чтобы открыть карточку")
        for account in accounts:
            account_id = account["id"]
            row_container = st.container(key=f"account_row_{account_id}")
            row, status = row_container.columns([4, 1])
            with row:
                if button(
                    f"{account['name']}  #{account_id}\n{account['phone']} · Inbox {account['inbox_chat_id'] or 'ещё не подключён'}  ›",
                    f"open_{account_id}",
                    variant="ghost",
                    class_name="w-full justify-start text-left h-auto py-3",
                    style={
                        "whiteSpace": "pre-line",
                        "minHeight": "66px",
                        "overflowWrap": "anywhere",
                    },
                ):
                    st.session_state["selected_account"] = account_id
                    st.rerun()
            with status:
                markup(badge(STATES[account["state"]], account["state"]))
        if not accounts:
            st.caption("Account пока нет")


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
