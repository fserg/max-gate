# PyMax 2.4.1: что умеет библиотека

Отчёт собран 13 сентября 2026 по исходникам репозитория MaxApiTeam/PyMax (тег v2.4.1),
документации docs.pymax.org и issues. Ссылки вида `<repo>/...` указывают на файлы в клоне
репозитория. Сохранён как справочник для реализации адаптера `maxgate/max/`.

---


Дата: 2026-09-13.

Источники: локальный клон репозитория `https://github.com/MaxApiTeam/PyMax` (HEAD `53103f0`, тег `v2.4.1`)
(далее `<repo>/`), PyPI JSON API (`https://pypi.org/pypi/maxapi-python/json`), GitHub API и `gh issue view`.
Опубликованные docs `https://docs.pymax.org/` отвечают 200 и собраны из `<repo>/docs/*.rst`, поэтому ниже
цитируются rst-исходники. DeepWiki не использовался (авто-сгенерирован из того же репозитория).

Уровни уверенности: **высокая** = прочитано в коде/документации; **средняя** = вывод из кода или
обсуждения в issues; **низкая** = первоисточником не подтверждено.

---

## 0. Краткая сводка по вопросам (а)–(з)

| Вопрос | Ответ | Где в отчёте |
|---|---|---|
| (а) сигнатуры отправки/скачивания медиа и событий | `send_message(chat_id, text=None, reply_to=None, attachments=None, *, notify=True, send_at=None) -> Message`; вложения `Photo/File/Video/Voice/VideoNote/Poll`; входящие в `Message.attaches: list[Attachment]`; **метода `download()` нет** — только `get_file_by_id()`/`get_video_by_id()` возвращают URL, `PhotoAttachment.base_url`/`StickerAttachment.url`/`AudioAttachment.url` — прямые ссылки | п.5, п.6, п.11 |
| (б) как понять, что событие — моё собственное сообщение | Только по `message.sender == client.me.contact.id`. В библиотеке фильтра нет. Придёт ли вообще NOTIF_MESSAGE о сообщении, отправленном с телефона, первоисточниками не подтверждено | п.3 |
| (в) mark_as_read | `await client.read_message(message_id, chat_id) -> ReadState(unread, mark)` и `await message.read()`; opcode `CHAT_MARK`, тип `READ_MESSAGE`, `mark = now_ms`. Отдельного «прочитать весь чат» нет | п.6 |
| (г) история с пагинацией | `await client.fetch_history(chat_id, forward=0, backward=40, backward_time=0, forward_time=0, from_time=None, item_type=ItemType.REGULAR, get_chat=False, get_messages=True, interactive=False) -> list[Message]`; пагинация по времени (Unix ms), не по id; `messages[0]` — самое старое | п.7 |
| (д) edit/delete | `edit_message(chat_id, message_id, text=None, attachments=None) -> Message`; `delete_message(chat_id, message_ids: list[int], for_me: bool) -> bool`; `message.edit()`, `message.delete(for_me=False)` | п.6 |
| (е) список чатов и тип | `client.chats` (кеш login/sync), `await client.fetch_chats(marker=None) -> list[Chat]`, `await client.get_chat(chat_id) -> Chat`; `Chat.type: ChatType` = `DIALOG` / `CHAT` (группа) / `CHANNEL`; свойства `is_dialog/is_group/is_channel` | п.4 |
| (ж) несколько Client в одном процессе | Да, штатно: у каждого свои App/Connection/SessionStore/Router, глобального состояния нет (кроме `configure_logging`). Обязательно разные `session_name`/`work_dir`: `load_session()` берёт первую строку без фильтра по телефону | п.9 |
| (з) issues про баны | Прямых issue о бане за использование PyMax нет. Есть #22 (2025-11): `error.user.restricted.send` «Возможности профиля ограничены» при первом сообщении незнакомому номеру, автор: «ограничение на создание новых чатов, подождать». README предупреждает о риске блокировки | п.8 |

---

## 1. Метаданные (уверенность: высокая)

| Параметр | Значение | Источник |
|---|---|---|
| Пакет PyPI | `maxapi-python`, import `pymax` | `<repo>/pyproject.toml:2`, README |
| Версия | 2.4.1 (PyPI upload 2026-08-24; GitHub release 2026-09-01; `__version__ = "2.4.1"`) | PyPI JSON, `gh release list`, `<repo>/src/pymax/__init__.py:1` |
| История релизов | 2.0.1 (2026-05-23), 2.1.0 (05-26), 2.1.1, 2.1.2, 2.1.3, 2.2.0 (06-13), 2.3.0 (06-18), 2.3.1 (06-19), 2.4.0 (08-04), 2.4.1 (08-24) | PyPI JSON |
| Python | `>=3.10`, классификаторы 3.10–3.14 | `pyproject.toml:5, 17-21` |
| Зависимости | `aiofiles>=25.1.0`, `aiohttp>=3.13.5`, `aiosqlite>=0.22.1`, `msgpack>=1.1.2`, `pydantic>=2.10.0`, `python-socks[asyncio]>=2.8.1`, `qrcode>=8.2`, `websockets>=16.0`, `zstandard>=0.25.0`; extra `video` = `tinytag>=2.2.1` (автоопределение длительности Voice/VideoNote) | `pyproject.toml:26-36, 100-103` |
| Лицензия | MIT (Copyright (c) 2025 ink-developer) | `<repo>/LICENSE` |
| Активность | последний коммит 2026-08-24 («fix: twine bump»); 259 звёзд, 55 форков, 11 открытых issue; автор `ink-developer` отвечает в issues в течение дней | GitHub API, `git log -1` |
| API | полностью asyncio, sync-варианта нет | весь `src/pymax` |
| Транспорт | `Client` = TCP + TLS к `api2.oneme.ru:443` (сертификат Max вшит в `_data/rootca_ssl_rsa2022.crt`), кадры msgpack (+lz4/zstd), протокол v10; `WebClient` = WebSocket `wss://api.oneme.ru/websocket` с Origin `https://web.max.ru` | `config.py:248-250`, `protocol/tcp/protocol.py`, `transport/tcp.py:21-24`, `transport/websocket.py:17-28` |
| Статус | `Development Status :: 4 - Beta`; README: «неофициальный внутренний API… может нарушать условия сервиса… авторы не несут ответственности за блокировки аккаунтов» | README, `pyproject.toml:12` |
| Планы | `TODO.md`: FSM, DI, улучшенные фильтры — в 2.5.0 | `<repo>/TODO.md` |
| Прочее | `ExtraConfig.telemetry=True` по умолчанию — библиотека шлёт telemetry-события Max (`TelemetryService`) | `config.py:267`, `app.py:62, 217-218` |

Структура пакета: `client.py` (`Client`), `client_web.py` (`WebClient`), `base.py` (`BaseClient` — декораторы, `start/connect/stop/relogin`), `app.py` (`App` — runtime: handshake/login/ping/dispatch), `auth/` (`SmsAuthFlow`, `QrAuthFlow`, провайдеры), `api/` (сервисы `auth/chats/messages/uploads/users/self/session/bots`), `infra/` (миксины с публичными методами клиента), `dispatch/` (роутер/диспетчер/маппинг событий), `types/domain` (Pydantic-модели), `types/events`, `files/` (отправляемые вложения), `session/` (SQLite store), `versions/` (каталог fingerprints APK).

---

## 2. Авторизация

### 2.1 Логин по телефону (высокая)

`Client(phone=...)` использует `SmsAuthFlow` (`<repo>/src/pymax/auth/sms.py:55-111`):

1. `app.api.auth.request_code(phone)` → opcode `AUTH_REQUEST`(17), payload `{phone, type: "START_AUTH", mode: <fingerprint bytes>}` → `StartAuthResponse(token, code_length, request_max_duration, request_count_left, alt_action_duration)`.
2. `code = await self.code_provider.get_code(phone)` — ожидание кода от `SmsCodeProvider`.
3. `app.api.auth.send_code(start.token, code)` → opcode `AUTH`(18), payload `{token, verifyCode, authTokenType: "CHECK_CODE"}` → `CheckCodeResponse` с `login_token` **или** `password_challenge(track_id, hint)` **или** `register_token`.
4. При `password_challenge` → `PasswordProvider.get_password(hint)` → `AUTH_LOGIN_CHECK_PASSWORD`(115); попытки ограничиваются `ExtraConfig(password_max_attempts=...)`, иначе бесконечно; исчерпание → `PasswordAttemptsExceededError`.
5. При `register_token` (номер не зарегистрирован) → `confirm_registration(first_name, last_name, token)` из `ExtraConfig(registration_config=RegistrationConfig(...))`, иначе `RuntimeError`.
6. Токен сохраняется в store.

Код доставляется **по SMS**; звонок/альтернативный канал в библиотеке **не реализован**. В `AuthType` есть `RESEND` (`api/auth/enums.py:8`), но публичного метода повторной отправки нет. `alt_action_duration` в ответе намекает на альтернативное действие на сервере, но PyMax его не использует.

Источники: `src/pymax/api/auth/service.py:61-118`, `src/pymax/types/domain/auth.py:9-98`, `src/pymax/api/auth/payloads.py:10-21`, `docs/auth.rst:12-21`.

Консольные провайдеры по умолчанию: `ConsoleSmsCodeProvider` (`input()` в `asyncio.to_thread`), `ConsolePasswordProvider` (`getpass`). Протоколы: `SmsCodeProvider.get_code(phone) -> str`, `PasswordProvider.get_password(hint=None) -> str`, `QrHandler.show_qr(qr_url)`, `EmailCodeProvider.get_code(email)` (`src/pymax/auth/providers.py`).

### 2.2 Сохранение сессии (высокая)

- По умолчанию SQLite-файл `work_dir/session_name` (`Client(session_name="session.db", work_dir=".")`; README использует `work_dir="cache", session_name="main.db"`). Директория создаётся автоматически.
- Таблица `sessions(token PK, device_id, phone, mt_instance_id, chats_sync, contacts_sync, drafts_sync, presence_sync, config_hash, user_agent)`. Хранит login-token, device_id, телефон, mt_instance_id, JSON user-agent (модель устройства), sync-маркеры. Схема мигрирует автоматически (`_ensure_column`).
- Повторный запуск не требует SMS: `App.start()` вызывает `store.load_session()`, если есть — сразу handshake + `LOGIN`(19) с токеном.
- **`load_session()` делает `SELECT ... LIMIT 1` без фильтра по телефону** → один файл = одна сессия/один аккаунт.
- `ExtraConfig(persist_session=False)` → `InMemoryStore` (сессия живёт только в текущем runtime; reconnect без `token` снова запустит SMS-авторизацию).
- `ExtraConfig(store=<StoreProtocol>)` — своё хранилище (`save_session`, `update_token`, `load_session`, `load_session_by_device_id`, `load_session_by_phone`, `delete_session`, `close`).
- Токен может обновляться сервером при login (`login_response.token`) и при `close_all_sessions()` — PyMax сам вызывает `store.update_token`.

Источники: `src/pymax/session/store.py:71-311`, `src/pymax/session/models.py:7-26`, `src/pymax/session/protocol.py`, `src/pymax/app.py:73-215`, `docs/client.rst:270-330`, `docs/getting-started.rst:77-86`.

### 2.3 Токен явно в конструктор (высокая)

`Client(phone=..., extra_config=ExtraConfig(token="TOKEN"))`: если сохранённой сессии нет, PyMax создаёт `SessionInfo(token=config.token, device_id=config.device.device_id, ...)`, сохраняет её и логинится. Если сессия в файле уже есть — параметр игнорируется (используется файл). `relogin(drop_config_token=True)` сбрасывает и его.

Токен TCP-клиента (`Client`) и Web-клиента **не взаимозаменяемы** (issue #44, ответ автора: «Токен полученный в сокете не будет работать в вебсокет, и наоборот»).

Источники: `src/pymax/app.py:115-127`, `docs/client.rst:215-227`, `docs/auth.rst:197-198`.

### 2.4 device_id и привязка к устройству

- `device_id` генерируется `secrets.token_hex(8)` (16 hex-символов); можно задать `ExtraConfig(device_id=...)`. Также `mt_instance_id` (тот же генератор). Оба сохраняются в сессии и переиспользуются при handshake (`SESSION_INIT`(6)); при `Client` в handshake также идёт user-agent с моделью Android-устройства (случайно выбирается из списка Samsung/Xiaomi/Pixel…, сохраняется в сессии и восстанавливается). `app_version` (default `26.25.0`) и build number/fingerprint берутся из `VersionCatalog`. (высокая) Источники: `src/pymax/config.py:59-124, 260-263, 273-299`, `src/pymax/app.py:79-105`, `docs/client.rst:140-193, 289-296`.
- Привязан ли **серверный** login-token к `device_id` — **не подтверждено** (средняя). Косвенно: при `ExtraConfig(token=...)` PyMax сохраняет токен с новым случайным `device_id`, то есть автор считает это рабочим сценарием. Безопаснее переносить вместе с токеном и `device_id`.
- `DeviceType`: `ANDROID` (default), `IOS`, `DESKTOP` для `Client`; `WEB` для `WebClient`. Автор в issue #99 не рекомендует `DESKTOP` («могут быть различия в апи, лучше ANDROID»). `authorize_qr_login()` работает только из `ANDROID/IOS`.

### 2.5 Истечение / отзыв сессии (высокая)

- `App._is_invalid_login_token_error()`: `ApiError` с `opcode == LOGIN` и `error`/`message` ∈ {`FAIL_LOGIN_TOKEN`, `FAIL_LOGOUT_ALL`}.
- В `start()`: при такой ошибке пишется warning, при `ExtraConfig(relogin=True)` (default) вызывается `relogin(start=False)` → сессия удаляется из store, runtime пересоздаётся, цикл `start()` заходит на новую авторизацию (снова будет вызван `SmsCodeProvider`). При `relogin=False` автоматического сброса нет.
- В `connect()` (одноразовое подключение) `ApiError` пробрасывается наружу после `close()` и `on_disconnect`.
- Пример из документации с ручной обработкой:

```python
@client.on_error(scope=ErrorScope.GLOBAL)
async def on_err(e: Exception, ctx: ErrorContext[Client]) -> None:
    if isinstance(e, ApiError) and e.message == "FAIL_LOGIN_TOKEN":
        await ctx.client.relogin()
```

- Issue #76 (closed, fix в 2.4.0): при удалении сессии с телефона библиотека раньше зависала.

Источники: `src/pymax/app.py:220-228`, `src/pymax/base.py:193-248, 346-369`, `docs/router.rst:167-196`, `docs/client.rst:354-382`.

### 2.6 Разделение «запросить код» и «ввести код» (высокая по коду, средняя по применимости)

- Публичных методов вида `request_code()` / `sign_in(code)` на `Client` **нет** (в 1.x были `client.request_code(phone)` и `client.login_with_code(token, code)`, issue #23; в 2.x удалены).
- **Штатный путь для веб-формы** — кастомный `SmsCodeProvider`, у которого `get_code()` ждёт `asyncio.Queue`/`Future`, а веб-обработчик кладёт код в очередь. `connect()`/`start()` при этом блокируются внутри auth-flow до получения кода. Пример из `docs/auth.rst:36-59`:

```python
import asyncio
from pymax import Client

class MemorySmsCodeProvider:
    def __init__(self) -> None:
        self._queue = asyncio.Queue[str]()

    async def set_code(self, code: str) -> None:
        await self._queue.put(code)

    async def get_code(self, phone: str) -> str:
        return await self._queue.get()

sms_provider = MemorySmsCodeProvider()
client = Client(phone="+79990000000", work_dir="cache", sms_code_provider=sms_provider)
```

  Ограничение: серверный `request_max_duration` (в issue #99 — 60 с) — код нужно ввести за это время; документация: «Если [get_code] зависнет, зависнет и первичная авторизация».
- **Полное разделение на два независимых вызова** возможно только через кастомный `AuthFlow` с `authenticate(app) -> AuthResult(token=...)`, внутри которого доступны `app.api.auth.request_code(phone) -> StartAuthResponse` и `app.api.auth.send_code(token, code) -> CheckCodeResponse` (`.login_token`, `.password_challenge`, `.register_token`). Документация предупреждает: «advanced API… полный flow использует внутренний App… может ломаться после обновления PyMax» (`docs/auth.rst:164-198, 301-303`). Работает ли `send_code` на другом соединении, чем `request_code`, — не выяснено; в коде оба шага идут по одному открытому TCP-соединению после handshake.
- Альтернатива: `StaticTokenFlow` / `ExtraConfig(token=...)`, если токен получен внешней системой.

### 2.7 QR-авторизация (`WebClient`) (высокая)

`QrAuthFlow`: `GET_QR`(288) → `QrHandler.show_qr(qr_link)` → polling `GET_QR_STATUS`(289) до `login_available` или `expires_at` → `LOGIN_BY_QR`(291) → токен (возможен 2FA). Подтвердить QR можно программно уже авторизованным mobile-`Client`: `await client.authorize_qr_login(qr_link)` (`AUTH_QR_APPROVE`(290)). Источники: `src/pymax/auth/qr.py`, `src/pymax/api/auth/service.py:253-270, 421-428`, `docs/auth.rst:115-162`.

### 2.8 Известные проблемы авторизации

- Issue #99 (open, 2026-09-01, 3 комментария, ещё двое подтверждают): после успешного `AUTH_REQUEST` SMS не приходит на российский номер, QR-вход через `WebClient` работает. Автор: «в большинстве случаев проблема в номере/прокси… не ставить `DeviceType.DESKTOP`».
- Issue #90 (closed): вход зависал на 2.4.1.
- Issue #86 (closed): `client.unsupported-version` — сервер отклоняет устаревшие версии клиента; в 2.4.1 введён `app_version` + `VersionCatalog`.
- Issue #55 (closed, 2.1.2): при повторном логине сервер не возвращает `token` — исправлено.

---

## 3. Получение событий (высокая)

### 3.1 Механика

- `Client.start()` — long-running: открывает TCP, handshake, login, вызывает `on_start`, затем `connection.wait_closed()`; приём кадров в `ConnectionManager._recv_loop`, каждый входящий кадр отдаётся `App.on_event` → `Dispatcher.dispatch(frame)` в отдельной asyncio-задаче.
- Это **не** long polling и не WebSocket для `Client` — постоянное TCP-соединение с push-уведомлениями (`cmd == REQUEST` от сервера); `WebClient` — то же по WebSocket.
- Регистрация через декораторы на клиенте (`client.on_message()`) или на `Router`/`ClientRouter`/`WebRouter` + `client.include_router(router)`; вложенные роутеры `router.include_router(child)`. Handler всегда `async def h(event, client)`. Фильтры — sync/async функции `(event) -> bool`, все должны вернуть `True`.
- Ошибки: `on_error(scope=ErrorScope.GLOBAL|LOCAL)` с `(exc, ErrorContext)`; `on_disconnect()` с `(exc, reconnect, delay)`.

Источники: `src/pymax/base.py:193-248, 267-344`, `src/pymax/connection/connection.py:169-238`, `src/pymax/dispatch/dispatcher.py:236-302`, `docs/router.rst`.

### 3.2 Таблица событий

| Декоратор | Событие | Opcode(s) | Модель события |
|---|---|---|---|
| `on_message(*filters)` | новое сообщение | `NOTIF_MESSAGE`=128 (без `status`) | `Message` |
| `on_message_edit(*filters)` | редактирование | 128 или `MSG_EDIT`=67 со `status == "EDITED"` | `Message` |
| `on_message_delete(*filters)` | удаление | `NOTIF_MSG_DELETE`=142, либо 128 со `status == "REMOVED"` (web) | `MessageDeleteEvent(message_ids: list[int], chat_id: int, chat: Chat\|None, message: Message\|None, ttl: bool)` |
| `on_message_read(*filters)` | отметка прочтения | `NOTIF_MARK`=130 | `MessageReadEvent(set_as_unread: bool, chat_id: int, user_id: int, mark: int)` |
| `on_typing(*filters)` | печатает | `NOTIF_TYPING`=129 | `TypingEvent(chat_id: int, user_id: int)` |
| `on_presence(*filters)` | онлайн/офлайн | `NOTIF_PRESENCE`=132 | `PresenceEvent(user_id: int, presence: Presence(seen: int\|None, status: int\|None))` |
| `on_reaction_update(*filters)` | реакции | `NOTIF_MSG_REACTIONS_CHANGED`=155 | `ReactionUpdateEvent(message_id: str, chat_id: int, counters: list[ReactionCounter]\|None, total_count: int)` |
| `on_chat_update(*filters)` | изменение чата (создание, участники, настройки, добавление в чат) | `NOTIF_CHAT`=135 (payload `{"chat": {...}}`) | `Chat` |
| `on_raw(*filters)` | любой кадр | все | `InboundFrame(opcode, cmd, seq, payload, raw)` |
| `on_start()` | после login (и после каждого reconnect) | — | `(client)` |
| `on_disconnect()` | перед reconnect / перед пробросом ошибки | — | `(exc, reconnect: bool, delay: float)` |
| `on_error(scope)` | ошибки фильтров/handler-ов/on_start/login | — | `(exc, ErrorContext)` |

Внутренние (не публичные) события `FILE_READY`/`VIDEO_READY`/`VOICE_READY` — `NOTIF_ATTACH`=136, используются upload-сервисом.

Источники: `src/pymax/dispatch/mapping.py:37-105`, `src/pymax/dispatch/resolvers.py:15-77`, `src/pymax/dispatch/enums.py`, `src/pymax/types/events/*.py`, `src/pymax/protocol/enums.py`.

Не распознаваемые библиотекой уведомления (только через `on_raw`): `NOTIF_CONTACT`=131, `NOTIF_CONFIG`=134, `NOTIF_CALL_START`=137, `NOTIF_MSG_DELETE_RANGE`=140, `NOTIF_LOCATION`=147, `NOTIF_DRAFT`=152, `NOTIF_MSG_DELAYED`=154, `NOTIF_MSG_YOU_REACTED`=156, `NOTIF_PROFILE`=159, `NOTIF_FOLDERS`=277, `NOTIF_STORIES_UPDATE`=216 и др.

### 3.3 Оговорки по событиям

- Issue #97 (open, 2026-08-29): и на TCP (`Client`), и на Web-сессии сервер **не присылает opcode 155** при реакции собеседника → `on_reaction_update` фактически не срабатывает; наблюдались только кадры 128 (`chatId, mark, message, prevMessageId, ttl, unread`), 129, 130, 132, 135.
- Issue #96 (open): на `WebClient` `messageId` в событии реакции приходит `int`, `ReactionUpdateEvent` ждёт `str` → `ValidationError`.
- Сообщения-события с неполным payload: `chat_id`, `sender`, `attaches` могут быть `None`/пустыми (`docs/messages.rst:264-282`).
- Отдельного события «меня добавили в чат» нет; ожидается `NOTIF_CHAT` (`on_chat_update`) и/или `Message` с `ControlAttachment(event="new", title)` (средняя).

### 3.4 Собственные исходящие сообщения (низкая — не подтверждено)

- В диспетчере **нет фильтра по отправителю**: любой `NOTIF_MESSAGE` попадает в `on_message` (`src/pymax/dispatch/dispatcher.py:236-251`).
- Единственный способ проверки — `message.sender == client.me.contact.id` (`client.me: Profile`, `Profile.contact: User`, `User.id: int`; `Message.sender: int | None`).
- Косвенно: README-пример отвечает `message.answer()` на каждое `on_message` и не зацикливается → сообщения, отправленные **этой же сессией**, сервер обратно не эхо-ит. Про сообщения, отправленные с телефона (другой сессии того же аккаунта), в коде/docs/issues данных нет. Аргумент в пользу «приходят»: сессия получает `NOTIF_MARK`/`NOTIF_CHAT` о чужих действиях и login-sync отдаёт `messages` по чатам, то есть сервер синхронизирует состояние на все устройства; но это предположение.

---

## 4. Модель чатов (высокая)

- `ChatType` (`src/pymax/types/domain/enums.py:4-9`): `DIALOG` (1:1), `CHAT` (группа), `CHANNEL`. `Chat.type: ChatType | str`; свойства `chat.is_dialog`, `chat.is_group`, `chat.is_channel`. Сервер может прислать неизвестный тип → строка.
- `AccessType`: `PUBLIC`, `PRIVATE`, `SECRET`.
- Поля `Chat` (`chat.py:101-130`): `id: int`, `type`, `status: str` (напр. `"ACTIVE"`), `owner: int`, `participants: dict[int, int]` (user_id → время?), `title: str|None`, `base_raw_icon_url`, `base_icon_url` (аватар), `last_message: Message|None`, `last_event_time: int`, `created`, `new_messages: int` (непрочитанные), `link: str|None` (invite), `access`, `restrictions: int|None`, `pinned_message`, `participants_count: int`, `description`, `options: dict[str,bool]|int|None`, `join_time`, `invited_by`, `modified`, `messages_count`, `has_bots`, `prev_message_id`, `admin_participants: dict[int, dict]`, `admins: list[int]`, `cid`.
- Идентификаторы: `chat_id: int` (группы/каналы — отрицательные, напр. `-69759875973346` из issue #86; диалоги — положительные, вычисляются как `user_id_a ^ user_id_b`, см. `client.get_chat_id(first_user_id, second_user_id)` / `api/users/service.py:138-139`), `user_id: int`, `message_id: int` (`Message.id`), но в ряде мест сервер оперирует строками: `ReactionUpdateEvent.message_id: str`, ключи `get_reactions()`, `read_message` для WS — `str`.
- Методы:
  - `client.chats -> list[Chat] | None` — кеш из login/sync (неполный; на повторных запусках сервер может вернуть только изменения по `chats_sync`; для полного — `ExtraConfig(sync=SyncOverrides(chats_sync=-1))`).
  - `await client.fetch_chats(marker: int | None = None) -> list[Chat]` — `CHATS_LIST`(53), `marker` default = now_ms; обновляет кеш.
  - `await client.get_chat(chat_id: int) -> Chat` (кеш → `CHAT_INFO`(48)); `await client.get_chats(chat_ids)`.
  - `await client.get_chat_members(chat_id, marker=None, count=50) -> tuple[list[Member], int]` — страница + маркер; `Member(contact: User, presence: Presence)`.
  - Управление: `create_group(name, participant_ids, notify)`, `invite_users_to_group/channel`, `remove_users_from_group`, `change_group_settings`, `change_group_profile(name, description, photo: Photo)`, `add_admin(chat_id, user_id, permissions: list[ChannelPermissions])`, `join_group(link)`, `join_channel(link)`, `resolve_group_by_link(link)`, `rework_invite_link`, `delete_chat`, `get_join_requests`, `confirm/decline_join_request(s)`; на `Chat`: `answer`, `history`, `get_message(s)`, `leave`, `delete`, `invite`, `remove_users`, `pin_message`, `update_settings`, `rework_invite_link`.
- Пользователи: `client.contacts` (кеш), `get_cached_user(id)`, `await get_user(id)`, `await get_users(ids)`, `await fetch_users(ids)` (`CONTACT_INFO`(32)), `await search_by_phone(phone) -> User` (`CONTACT_INFO_BY_PHONE`(46)), `add_contact`, `remove_contact`, `import_contacts`. `User`: `id`, `names: list[Name(name, first_name, last_name, type)]`, `base_url`/`base_raw_url` (аватар), `phone: int|None`, `link`, `description`, `gender`, `account_status`, `options`.
- У `DIALOG` `title` может быть `None`; имя собеседника берётся из `User` (в библиотеке helper'а нет — средняя).

Источники: `src/pymax/types/domain/chat.py`, `src/pymax/api/chats/service.py:264-320`, `src/pymax/infra/chat.py`, `src/pymax/infra/user.py`, `src/pymax/types/domain/user.py:61-77`, `docs/chats.rst`, `docs/users.rst`.

---

## 5. Модель сообщения (высокая)

`Message` (`src/pymax/types/domain/message.py:155-241`), Pydantic `CamelModel` (алиасы camelCase: `chatId`, `reactionInfo`…):

| Поле | Тип | Комментарий |
|---|---|---|
| `id` | `int` | ID сообщения |
| `chat_id` | `int \| None` | может отсутствовать в некоторых событиях |
| `sender` | `int \| None` | user_id отправителя |
| `text` | `str` | по умолчанию `""` |
| `time` | `int` | Unix time в **миллисекундах** |
| `type` | `str` | напр. `"USER"`, `"CHANNEL"` |
| `cid` | `int \| None` | клиентский id |
| `attaches` | `list[Attachment]` | вложения (см. ниже) |
| `status` | `MessageStatus \| None` | `EDITED` / `REMOVED` |
| `reaction_info` | `ReactionInfo \| None` | `total_count`, `counters: list[ReactionCounter(count, reaction)]`, `your_reaction` |
| `elements` | `list[Element]` | форматирование/сущности: `Element(type: str, from_: int, length: int, attributes: ElementAttributes(url))`; типы `STRONG, EMPHASIZED, UNDERLINE, STRIKETHROUGH, MONOSPACED, LINK, HEADING, QUOTE, CODE` |
| `link` | `ReplyLink \| ForwardLink \| None` | `ReplyLink(type=REPLY, message: Message, chat_id)`; `ForwardLink(type=FORWARD, message, chat_id, chat_name, chat_link, chat_access_type, chat_icon_url)` |
| `delayed_attributes` | `DelayedAttributes \| None` | `time_to_fire`, `notify_sender`, `notify_opponents` — отложенные |
| `prev_message_id`, `unread`, `mark`, `ttl`, `options`, `stats` | | служебные |

Отдельного поля «упоминания» нет; `Element.attributes` содержит только `url` (средняя: упоминания, если сервер их шлёт, попадают в `elements` с иным `type` или теряются).

Методы bound-объекта: `reply(text, attachments, *, notify, send_at)`, `answer(text, reply_to, attachments, *, notify, send_at)`, `forward(chat_id, *, notify)`, `edit(text, attachments)`, `pin(notify_pin)`, `delete(for_me=False)`, `read()`, `react(reaction)`, `unreact()`, `get_reactions()`. Требуют `chat_id` и привязку к клиенту (`RuntimeError("Message is not bound to a client.")`).

### 5.1 Типы вложений (`src/pymax/types/domain/attachments/`)

`Attachment = KnownAttachment | UnknownAttachment`, discriminator по полю `_type` (`AttachmentType`):

| Класс | `_type` | Поля | Как скачать |
|---|---|---|---|
| `PhotoAttachment` | `PHOTO` | `base_url: str`, `width`, `height`, `photo_id: int`, `photo_token: str`, `preview_data: bytes\|None` | прямой `base_url` |
| `VideoAttachment` | `VIDEO` | `video_id: int`, `token`, `width`, `height`, `duration: int\|None`, `thumbnail: str`, `video_type: int` (1 = кружок, средняя), `preview_data` | `await client.get_video_by_id(chat_id, message_id, video_id) -> VideoRequest \| None` → `.url` (выбирается максимальное `MP4_*`), `.external` |
| `FileAttachment` | `FILE` | `file_id: int`, `name: str`, `size: int`, `token` | `await client.get_file_by_id(chat_id, message_id, file_id) -> FileRequest \| None` → `.url` (временный), `.unsafe` |
| `AudioAttachment` (голосовое) | `AUDIO` | `audio_id: int\|None`, `duration: int\|None`, `wave: str\|None`, `url: str\|None`, `token`, `transcription_status: FAILED\|MEDIA_NOT_READY\|NOT_SUPPORTED\|PROCESSING\|SUCCESS\|UNKNOWN` | поле `url`; формат входящих байтов в коде не описан (для отправки требуется OGG) |
| `StickerAttachment` | `STICKER` | `sticker_id`, `url`, `lottie_url`, `set_id`, `width`, `height`, `sticker_type`, `audio: bool`, `tags`, `time`, `author_type` | `url` / `lottie_url` |
| `ContactAttachment` | `CONTACT` | `contact_id`, `first_name`, `last_name`, `name`, `photo_url` | — |
| `PollAttachment(Poll)` | `POLL` | `poll_id`, `version`, `title`, `answers: list[PollAnswer(text, answer_id)]`, `settings: PollFlags`, `state: PollState(total, result, voter_preview_ids)` | — |
| `ShareAttachment` | `SHARE` | `url`, `title`, `description`, `image: dict` (preview ссылки) | — |
| `CallAttachment` | `CALL` | `duration`, `conversation_id`, `contact_ids`, `call_type: AUDIO\|VIDEO`, `hangup_type: MISSED\|REJECTED\|CANCELED\|HUNGUP` | — |
| `ControlAttachment` | `CONTROL` | `event: str`, `title: str\|None` (системные сообщения) | — |
| `InlineKeyboardAttachment` | `INLINE_KEYBOARD` | `keyboard: dict` | — |
| `UnknownAttachment` | любой другой | `type: str` + `model_extra` | — |

- **Локация**: модели нет (есть opcodes `LOCATION_SEND/STOP/REQUEST`, `NOTIF_LOCATION`, но они не реализованы) → придёт как `UnknownAttachment` или только в `on_raw`.
- **Метода `download()` нет.** Библиотека даёт только URL; скачивать нужно самостоятельно (`aiohttp`). Для `PhotoAttachment` — `base_url` сразу, для `FILE`/`VIDEO` — через запрос временной ссылки (`FILE_DOWNLOAD`(88) / `VIDEO_PLAY`(83)).
- **Альбомы**: `attaches` — список, несколько фото в одном сообщении представимы; при отправке `attachments=[Photo(...), Photo(...)]` уходят одним сообщением (`docs/examples.rst:59-83`).
- Голосовые входящие: `AudioAttachment`; формат по коду не определить. Отправка: **только OGG**, без перекодирования (`docs/files.rst:19-21, 91-96`). Issues #102/#103 (open): в 2.4.0/2.4.1 отправка голосовых падает (`NOTIF_ATTACH` не приходит / `errors.process.attachment.video.not.ready`).

Пример разбора вложений из `docs/messages.rst:296-317`:

```python
from pymax.types.domain import FileAttachment, PhotoAttachment, UnknownAttachment

@client.on_message()
async def on_message(message: Message, client: Client) -> None:
    if message.chat_id is None:
        return
    for attach in message.attaches:
        if isinstance(attach, PhotoAttachment):
            print("photo:", attach.photo_id, attach.base_url)
        elif isinstance(attach, FileAttachment):
            file_info = await client.get_file_by_id(
                chat_id=message.chat_id, message_id=message.id, file_id=attach.file_id,
            )
            print(file_info.url if file_info else "no url")
        elif isinstance(attach, UnknownAttachment):
            print("unknown:", attach.type, attach.model_extra)
```

---

## 6. Отправка (высокая)

Методы клиента (`src/pymax/infra/message.py`, реализация `src/pymax/api/messages/service.py`):

```python
async def send_message(self, chat_id: int, text: str | None = None, reply_to: int | None = None,
                       attachments: SendAttachments = None, *, notify: bool = True,
                       send_at: DateTimeUnion | None = None) -> Message
async def forward_message(self, chat_id: int, message_id: int | str, source_chat_id: int | None = None,
                          *, notify: bool = True) -> Message
async def edit_message(self, chat_id: int, message_id: int, text: str | None = None,
                       attachments: SendAttachments = None) -> Message
async def delete_message(self, chat_id: int, message_ids: list[int], for_me: bool) -> bool
async def pin_message(self, chat_id: int, message_id: int, notify_pin: bool) -> bool
async def read_message(self, message_id: int | str, chat_id: int) -> ReadState
async def add_reaction(self, chat_id: int, message_id: int, reaction: str) -> ReactionInfo | None
async def remove_reaction(self, chat_id: int, message_id: int) -> ReactionInfo | None
async def get_reactions(self, chat_id: int, message_ids: list[int]) -> dict[str, ReactionInfo] | None
async def get_message(self, chat_id: int, message_id: int) -> Message | None
async def get_messages(self, chat_id: int, message_ids: list[int]) -> list[Message]
async def get_file_by_id(self, chat_id: int, message_id: int | str, file_id: int) -> FileRequest | None
async def get_video_by_id(self, chat_id: int, message_id: int | str, video_id: int) -> VideoRequest | None
async def vote_poll(self, chat_id: int, message_id: int, poll_id: int, answer_ids: list[int]) -> PollState
```

`SendAttachment = Photo | File | Video | Poll | Voice | VideoNote`; `SendAttachments = Sequence[SendAttachment] | None`; `DateTimeUnion = datetime | timedelta | int` (Unix секунды).

- **Текст**: `send_message(chat_id, text=...)`, `message.answer(...)`, `chat.answer(...)`. Markdown-подобная разметка (`**жирный**`, `_курсив_`, `__подч__`, `~~зач~~`, `` `код` ``, ```` ```блок``` ````, `[текст](url)`, `# заголовок`, `> цитата`) конвертируется в `elements` (`src/pymax/formatting/markdown.py`, `docs/formatting.rst`). Issue #100 (open): `_`/`*` внутри слов вырезаются. HTML не поддерживается (issue #91).
- **Reply**: `reply_to=<message_id>` или `message.reply(text)`. `notify=False` — без push. `send_at` — отложенная отправка.
- **Фото**: `Photo(raw=None, *, url=None, path=None, name=None)`; расширения `.jpg .jpeg .png .gif .webp .bmp`; загружается multipart через `PHOTO_UPLOAD`(80).
- **Файл**: `File(raw=None, *, url=None, path=None, name=None)` — `FILE_UPLOAD`(87), потоковая загрузка чанками 1 МБ, ожидание `NOTIF_ATTACH` до 60 с.
- **Видео**: `Video(raw=None, *, url=None, path=None, name=None)` — `VIDEO_UPLOAD`(82), ожидание готовности до 60 с.
- **Голосовое**: `Voice(raw=None, *, path=None, url=None, name=None, duration: int | None = None)` — только OGG, `duration` в мс (или extra `video` для автоопределения).
- **Кружок**: `VideoNote(raw=None, *, url=None, path=None, name=None, duration=None)` — MP4 H.264 480x480 30 fps AAC, до 60 с.
- **Опрос**: `Poll(title, answers=[PollAnswer(text=...)], settings=PollFlags.FLAG_SETTINGS_ANONYMOUS | ...)`.
- **Альбом**: несколько элементов в `attachments`.
- **Стикеры** отправлять нельзя (issue #95, open).
- **Ограничения размеров**: в коде проверок нет; таймауты: `ExtraConfig(upload_timeout=900)` (общий HTTP), захардкоженные 60 с `sock_read` и 60 с ожидания события готовности. Issue #80: файл 3.2 ГБ загрузился после увеличения таймаута; `upload_timeout` добавлен в 2.4.1, 60-секундный лимит остался захардкожен.
- **Редактирование/удаление своих сообщений**: да (`MSG_EDIT`(67), `MSG_DELETE`(66)); `for_me=True` — удалить только у себя.
- **Отметить прочитанным**: `read_message(message_id, chat_id)` → `CHAT_MARK`(50) с `{type: "READ_MESSAGE", chatId, messageId, mark: now_ms}` → `ReadState(unread, mark)`. Особенность: TCP-клиент ждёт `message_id: int`, WS-клиент — `str`. Отдельного «прочитать весь чат» нет; отметка по последнему сообщению (средняя: сервер трактует mark как «прочитано до»).
- **Реакции**: `add_reaction` (`MSG_REACTION`(178), `reactionType: "EMOJI"`, `id: "👍"`), `remove_reaction` (179), `get_reactions` (180).
- **Typing: не реализовано.** `MSG_TYPING=65` есть только в `Opcode`; ни в сервисах, ни в миксинах нет метода отправки (`grep -rn MSG_TYPING src/pymax` → только `protocol/enums.py`).
- **Presence**: `client.set_presence(online=True)` — только меняет флаг `interactive` в следующих `PING`/`LOGIN`.

Источники: `src/pymax/api/messages/service.py:193-583`, `src/pymax/api/messages/payloads.py`, `src/pymax/api/uploads/service.py`, `src/pymax/files/*.py`, `docs/messages.rst`, `docs/files.rst`.

---

## 7. История (высокая)

```python
async def fetch_history(self, chat_id: int, forward: int = 0, backward: int = 40,
                        backward_time: int = 0, forward_time: int = 0,
                        from_time: int | None = None, item_type: ItemType = ItemType.REGULAR,
                        get_chat: bool = False, get_messages: bool = True,
                        interactive: bool = False) -> list[Message]
```

- Opcode `CHAT_HISTORY`(49), payload `{chatId, from, forward, backward, backwardTime, forwardTime, getChat, getMessages, itemType, interactive}`.
- `from_time` — точка отсчёта, Unix **миллисекунды**; `None` → «сейчас». `backward`/`forward` — количество сообщений назад/вперёд от точки; `backward_time`/`forward_time` — временные окна в мс.
- Пагинация **по времени, не по id**: для выгрузки старее — `from_time = history[0].time` (по ответу автора в issue #24: сервер возвращает `messages[0]` самое старое, `messages[-1]` самое новое). Для догоняния после простоя — `from_time=<time последнего известного>, forward=N, backward=0`.
- `ItemType.DELAYED` — отложенные сообщения.
- Также `chat.history(forward, backward, backward_time, forward_time, from_time, item_type, ...)`, `get_messages(chat_id, message_ids)` (`MSG_GET`(71)).
- При login приходит `client.messages: dict[int, list[Message]]` (chat_id → сообщения из sync).
- Всегда возвращает `list` (пустой, если нет), с 2.4.0.

Источники: `src/pymax/api/messages/service.py:357-391`, `src/pymax/api/messages/payloads.py:76-86`, `src/pymax/infra/message.py:146-188`, `docs/chats.rst:160-175`, `docs/messages.rst:251-262`, issue #24.

---

## 8. Надёжность

### 8.1 Reconnect (высокая)

- `start()` — цикл `while True`: `_ensure_runtime()` → `App.start()` (open TCP → handshake `SESSION_INIT` → ping-task → load/auth session → `LOGIN`/`LOGIN2`) → `emit_start` → `connection.wait_closed()`.
- При `ConnectionError | EOFError | OSError | TimeoutError`: `close()`, `on_disconnect(exc, reconnect, delay)`, если `ExtraConfig(reconnect=True)` (default) → `asyncio.sleep(reconnect_delay)` (default **1.0 с, без экспоненциального backoff**) → `_reset_runtime()` (новые `ConnectionManager` и `App`, тот же root `Router`) → снова handshake/login → `on_start` вызывается повторно.
- Ping: каждые 30 с `PING`(1) с `{"interactive": bool}`; ошибка ping → `connection.fail()` → reconnect. Таймаут запросов `ExtraConfig(request_timeout=30.0)`.
- `connect()` — одноразовое подключение без цикла; `stop()`/`close()` — штатное завершение; `client.is_connected`.
- WebSocket-транспорт: `websockets.asyncio.client.connect(url, origin="https://web.max.ru", max_size=10 MB)`; reconnect тот же.
- После reconnect события за время простоя **не воспроизводятся**; при login сервер по sync-маркерам может вернуть изменения чатов/сообщений (`login_response.messages`), но для надёжного догоняния нужен `fetch_history`.

Источники: `src/pymax/base.py:165-248`, `src/pymax/app.py:73-215, 317-344`, `src/pymax/connection/connection.py`, `docs/client.rst:25-66, 369-401`.

### 8.2 Отзыв сессии / версия клиента (высокая)

- `FAIL_LOGIN_TOKEN` / `FAIL_LOGOUT_ALL` → авто-relogin (см. п.2.5). Issue #76: зависание при удалении сессии с телефона исправлено в 2.4.0.
- Сервер отклоняет устаревшие версии клиента: `client.unsupported-version` «Приложение устарело» (issue #86). В 2.4.1 — `Client(app_version="26.28.0", catalog=VersionCatalog(remote=True))`, каталог fingerprints APK обновляется с `https://hashes.pymax.org/versions.json`; неизвестная версия → `VersionNotFoundError`. Практически: библиотеку/каталог придётся обновлять вслед за Max.
- SSL: issues #92 (closed, у пользователя проблема локального trust store/маршрутизации), #93 (open, «обновите сертификат»). В 2.4.1 сертификат Max вшит в пакет и добавлен в trust store.

### 8.3 Rate limits (средняя)

- В коде нет ни retry, ни обработки ошибок лимитов; строк `rate limit/flood/throttle` нет.
- `StartAuthResponse.request_count_left` и `request_max_duration` — серверные лимиты на запрос SMS-кода (значения не документированы; в issue #99 `request_max_duration=60`).
- В issues упоминаний rate limit нет.

### 8.4 Баны / ограничения аккаунтов (средняя)

- `gh search issues --repo MaxApiTeam/PyMax "бан OR ban OR заблокировали OR блокировка OR restricted"` → единственный результат issue #22 (2025-11-25, closed): при `send_message` незнакомому номеру сервер вернул `error.user.restricted.send` «Начать диалог не получится. Возможности профиля ограничены». Ответ автора: «это ограничение аккаунта на создание новых чатов… подождать, возможно через время спадёт».
- Ни одного issue о полной блокировке аккаунта за использование PyMax не найдено (проверен полный список из 50 issues). README и `docs/index.rst` содержат предупреждение об ответственности за блокировки.
- Косвенные факторы риска, видимые в коде: эмуляция Android-устройства с fingerprint APK (`FingerprintGenerator`), случайные модели устройств, `telemetry=True` по умолчанию.

---

## 9. Несколько аккаунтов в одном процессе (средняя)

- Каждый `Client`/`WebClient` создаёт собственные `Router`, `ConnectionManager`, `App`, `ApiFacade`, `SessionStore`, кеши (`me`, `chats`, `users`, `contacts`, `messages`). Модульного мутабельного состояния в `src/pymax` не найдено (`grep` по module-level `dict/list/None`).
- Единственная глобальная вещь — `configure_logging(level)` в конструкторе каждого клиента (последний `log_level` побеждает; логгер `pymax` общий).
- Следовательно, `await asyncio.gather(client_a.start(), client_b.start())` в одном loop — штатно. Обязательно **разные `session_name` или `work_dir`**, так как `SessionStore.load_session()` берёт первую строку таблицы без учёта телефона.
- Ни тестов, ни документации специально про многоаккаунтность нет.

Источники: `src/pymax/client.py:54-96`, `src/pymax/base.py:135-159`, `src/pymax/app.py:29-71`, `src/pymax/session/store.py:187-208`, `src/pymax/logging.py:55`.

---

## 10. Примеры кода из репозитория

### 10.1 Логин + обработчик входящих (`<repo>/README.md`, «Быстрый старт»)

```python
import asyncio

from pymax import Client, Message

client = Client(
    phone="+79990000000",
    work_dir="cache",
    session_name="main.db",
)


@client.on_start()
async def on_start(client: Client) -> None:
    print("Клиент запущен")
    print("Ваш ID:", client.me.contact.id if client.me else "unknown")


@client.on_message()
async def on_message(message: Message, client: Client) -> None:
    print(message.chat_id, message.sender, message.text)

    if message.chat_id is not None and message.text:
        await message.answer("Привет от PyMax")


async def main() -> None:
    await client.start()


if __name__ == "__main__":
    asyncio.run(main())
```

### 10.2 SMS-код из очереди для веб-формы (`<repo>/docs/auth.rst:36-59`)

```python
import asyncio

from pymax import Client


class MemorySmsCodeProvider:
    def __init__(self) -> None:
        self._queue = asyncio.Queue[str]()

    async def set_code(self, code: str) -> None:
        await self._queue.put(code)

    async def get_code(self, phone: str) -> str:
        return await self._queue.get()


sms_provider = MemorySmsCodeProvider()
client = Client(
    phone="+79990000000",
    work_dir="cache",
    sms_code_provider=sms_provider,
)
```

### 10.3 Роутер и фильтры (`<repo>/README.md`, «Роутеры»)

```python
from pymax import Client, ClientRouter, Message

router = ClientRouter()


def is_start(message: Message) -> bool:
    return message.text == "/start"


@router.on_message(is_start)
async def start(message: Message, client: Client) -> None:
    await message.answer("Готово")


client = Client(phone="+79990000000", work_dir="cache")
client.include_router(router)
```

### 10.4 Отправка файлов (`<repo>/docs/files.rst:30-64`)

```python
import asyncio

from pymax import Client, File, Photo, Video, VideoNote, Voice

client = Client(phone="+79990000000", work_dir="cache")


@client.on_start()
async def send_files(client: Client) -> None:
    chat = await client.get_chat(123456)

    await chat.answer(text="Фото", attachments=[Photo(path="image.jpg")])
    await chat.answer(text="Документ", attachments=[File(path="report.pdf")])
    await chat.answer(text="Видео", attachments=[Video(path="clip.mp4")])
    await chat.answer(attachments=[Voice(path="voice.ogg")])
    await chat.answer(attachments=[VideoNote(path="circle.mp4", duration=4200)])


asyncio.run(client.start())
```

### 10.5 Скачивание входящего файла (`<repo>/docs/files.rst:146-163`)

```python
from pymax import Client, Message
from pymax.types.domain import FileAttachment

@client.on_message()
async def on_message(message: Message, client: Client) -> None:
    if message.chat_id is None:
        return

    for attach in message.attaches:
        if isinstance(attach, FileAttachment):
            info = await client.get_file_by_id(
                chat_id=message.chat_id,
                message_id=message.id,
                file_id=attach.file_id,
            )
            print(info.url if info else "URL не получен")
```

### 10.6 Служебные события (`<repo>/docs/messages.rst:225-249`)

```python
from pymax import Client, MessageReadEvent, PresenceEvent, ReactionUpdateEvent, TypingEvent

@client.on_typing()
async def typing(event: TypingEvent, client: Client) -> None:
    print(event.chat_id, event.user_id)

@client.on_presence()
async def presence(event: PresenceEvent, client: Client) -> None:
    print(event.user_id, event.presence.status)

@client.on_message_read()
async def read(event: MessageReadEvent, client: Client) -> None:
    print(event.chat_id, event.mark)

@client.on_reaction_update()
async def reactions(event: ReactionUpdateEvent, client: Client) -> None:
    print(event.message_id, event.total_count)
```

### 10.7 История при старте (`<repo>/docs/examples.rst:124-138`)

```python
@client.on_start()
async def load_history(client: Client) -> None:
    messages = await client.fetch_history(chat_id=123456, backward=20)
    for message in messages or []:
        print(message.id, message.text)
```

---

## 11. Конфигурация `ExtraConfig` (справочно, высокая)

`src/pymax/config.py:180-271`:

`token`, `registration_config`, `host="api2.oneme.ru"`, `port=443`, `url="wss://api.oneme.ru/websocket"`, `use_ssl=True`, `proxy` (socks/http URL для TCP и WS), `reconnect=True`, `reconnect_delay=1.0`, `upload_timeout=900`, `relogin=True`, `password_max_attempts=None`, `persist_session=True`, `device_id=None` (генерируется), `device_type=DeviceType.ANDROID`, `user_agent=None` (генерируется), `mt_instance_id` (генерируется), `request_timeout=30.0`, `log_level="INFO"`, `telemetry=True`, `store=None`, `sync=SyncOverrides()`.

`Client.__init__(phone, session_name="session.db", work_dir=".", extra_config=None, auth_flow=None, sms_code_provider=None, password_provider=None, app_version="26.25.0", catalog=None)`.

`WebClient.__init__(session_name="session.db", work_dir=".", extra_config=None, auth_flow=None, qr_provider=None)`.

---

## 12. Что НЕ удалось выяснить

1. **Собственные сообщения с другого устройства.** Приходят ли в `on_message` сообщения, отправленные тем же аккаунтом с телефона. В коде фильтра нет, в docs/issues тема не поднималась. Проверка на практике: `message.sender == client.me.contact.id`.
2. **Привязка токена к `device_id`.** Можно ли использовать `ExtraConfig(token=...)` с другим `device_id`/user-agent, чем при получении токена.
3. **`send_code` на другом соединении.** Работает ли шаг ввода кода на новом TCP-соединении (после нового handshake), если `request_code` был на предыдущем — нужно для полного разнесения шагов на два HTTP-запроса без удержания соединения.
4. **Формат входящих голосовых** (`AudioAttachment.url`): в коде не описан; для отправки — OGG.
5. **Серверные лимиты**: размеры файлов, частота запросов, число SMS-запросов — в библиотеке не закодированы; известно только, что 3.2 ГБ загрузился после увеличения таймаута (issue #80).
6. **Реальные баны** за использование PyMax: подтверждений/опровержений в репозитории нет, только ограничение `error.user.restricted.send` на новые диалоги (issue #22).
7. **Событие «меня добавили в чат»**: предположительно `on_chat_update` (`NOTIF_CHAT`) и/или `ControlAttachment`, явно не документировано.
8. **Упоминания (mentions)**: отдельной модели нет; в `Element.attributes` только `url`.
9. Telegram-канал `t.me/pymax_news` и DeepWiki не проверялись.
10. Сведения о текущем состоянии `docs.pymax.org` — только факт доступности (200, `<title>PyMax</title>`); содержимое сверялось по rst-исходникам.

---

## 13. Дополнение по живой проверке 13.09.2026 (spike, `spikes/pymax_smoke.py`)

- PyMax 2.4.1, `app_version=26.31.0` (последняя в удалённом каталоге `hashes.pymax.org`,
  всего 50 версий), TCP-клиент: handshake прошёл, SMS-код запрошен и доставлен за секунды.
- После отправки кода сервер ответил `opcode=18` ошибкой
  `error.profile.active.session.no2fa` «Логин ограничен. Требуется установить 2FA»
  (`Login restricted`). Трактовка: у аккаунта есть активная сессия (телефон), а пароль для
  входа не задан, и MAX не пускает новое устройство. Лечится включением пароля в приложении:
  «Профиль → Приватность → Пароль для входа». После этого вход идёт по цепочке
  SMS-код → `password_challenge` → пароль (PyMax это поддерживает через `PasswordProvider`).
- Следствие для дизайна: включённый 2FA-пароль обязателен для каждого Account, UI предупреждает
  об этом до запроса кода.

## 14. Сертификаты хостов MAX (живая проверка 13.09.2026)

Что наблюдалось:

| Хост | Что отдаёт | Кем подписан | Скачивание из Python |
|---|---|---|---|
| `api2.oneme.ru` | API (TCP+TLS) | Russian Trusted Sub CA → Russian Trusted Root CA (Минцифры) | работает: PyMax возит корень `pymax/_data/rootca_ssl_rsa2022.crt` и подключает его к **своему** SSL-контексту |
| `i.oneme.ru` | фото (`PhotoAttachment.base_url`) | Let's Encrypt | работает с системным хранилищем |
| `a.oneme.ru` | голосовые (`AudioAttachment.url`) | Let's Encrypt (по факту скачивания) | работает с системным хранилищем |
| `fd2.oneme.ru` | файлы (`get_file_by_id().url`) | Russian Trusted Sub CA → Russian Trusted Root CA | `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` |

Почему фото качается, а документ нет: фото и голосовые лежат на CDN с сертификатом Let's
Encrypt, корень которого (ISRG) есть в любой системе. Файлы лежат на хосте с сертификатом
от российского удостоверяющего центра Минцифры, корня которого в Ubuntu, Debian, macOS
и Windows по умолчанию нет.

Что сделано: в скрипте проверки создан отдельный `ssl.SSLContext` (системные корни плюс
тот же файл `rootca_ssl_rsa2022.crt` из пакета PyMax), и он передаётся в `aiohttp` **только**
для URL с хостом `*.oneme.ru`. Системное хранилище сертификатов не менялось, другие программы
и другие хосты это не затрагивает, проверка сертификатов не отключалась.

Чем это опасно и чем нет. Доверие корню означает, что этот удостоверяющий центр может
выпустить сертификат на любое имя, и наш клиент его примет. Если бы корень был добавлен
в систему, это распространилось бы на браузер и все программы для всех сайтов. В нашем
варианте доверие ограничено одним процессом и одним доменом `oneme.ru`, который и так
принадлежит MAX и уже обслуживается этим же центром для API: ровно то же доверие PyMax
уже оказывает при каждом соединении с `api2.oneme.ru`. Новых рисков относительно самого
факта использования MAX это не добавляет. Альтернатива без этого доверия: файлы из MAX
не переносить, только Note с именем и размером.

Для приложения: тот же ограниченный контекст в `maxgate/max/media.py`, без глобальных
изменений `ssl`.

## 15. Остановка клиента

`client.stop()` вызывает `close()`; соединение закрывается, но `start()` в проверке
не вернулся и процесс остался жив (потребовался SIGTERM). В цикле `start()` есть явная
обработка `asyncio.CancelledError` с `close()`. Для `AccountRunner` останавливать клиента
отменой задачи, в которой выполняется `start()`, а не вызовом `stop()`.

## 16. Итог живой проверки 13.09.2026 (все пункты плана шага 0)

| Проверка | Результат | Особенности |
|---|---|---|
| Вход по SMS + 2FA | ок | цепочка `AUTH_REQUEST` → код → `password_challenge` (hint приходит) → пароль; `app_version=26.31.0` сервер принял |
| Повторный вход по сессии | ок, без SMS | сессия в SQLite PyMax, при логине `session token updated` |
| `fetch_chats()` | ок, 50 чатов | у Dialog `title=None`, `participants_count=0`, имя берётся из `get_user`; id групп отрицательные |
| Входящий текст | ок | `chat_id`, `sender`, `text`, `time` (мс) заполнены |
| Своё сообщение с телефона | **приходит** в `on_message` с `sender == me` | отправки из той же сессии эхом не приходят |
| Фото | ок | `base_url` отдаёт WEBP (`image/webp`), ссылка живёт около суток |
| Голосовое | ок | `AudioAttachment.url`, `audio/ogg`, кодек Opus 48 kHz mono, `duration` в мс |
| Файл | ок после SSL-контекста | `get_file_by_id().url` на `fd2.oneme.ru`, `application/octet-stream`, размер совпал |
| Отправка текста | ок | `send_message` вернул `Message` с `id` |
| Отправка фото (PNG) | ок | загрузка заняла ~11 с |
| `fetch_history(backward=3)` | ок | у сообщений `chat_id=None`; у `Element` типа LINK `from_=None` |
| `edit_message` | ок | вернул `status=EDITED` |
| `read_message` | ок | `ReadState(unread=0, mark=...)` |
| `delete_message` | ок | `True` |
| Reply от собеседника | ок | `Message.link = ReplyLink(message=Message(id=...))` |
| Правка собеседника | ок | `on_message_edit`, тот же `id`, `status=EDITED` |
| Удаление собеседником | ок | `on_message_delete`: `MessageDeleteEvent(chat_id, message_ids=[...], ttl=False)` |
| События прочтения | приходят | `on_message_read` с `user_id` собеседника |
| Служебные сообщения MAX | приходят как обычные | отправитель «MAX», `InlineKeyboardAttachment` с кнопками |

Идентификаторы сообщений: 18-значные `int` (например `117263822972066856`).
