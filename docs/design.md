# Max-gate: проектное описание

Дата: 13 сентября 2026. Термины из [CONTEXT.md](../CONTEXT.md) используются без пояснений.
Решения с нетривиальными компромиссами вынесены в [docs/adr](./adr/).

## 1. Назначение

Gate переносит переписку между MAX и Telegram в обе стороны. Каждый MaxChat одного Account
отражается в отдельный Topic у Telegram-бота этого Account. Один экземпляр Gate обслуживает
несколько Account (у каждого свой номер MAX и свой бот), управляет ими Operator через
веб-интерфейс.

Сторона MAX работает через неофициальный клиент [PyMax](https://github.com/MaxApiTeam/PyMax)
(`maxapi-python`), сторона Telegram через Bot API (`aiogram` 3). Подробные факты об обеих
библиотеках: [research/pymax-2.4.1.md](./research/pymax-2.4.1.md),
[research/telegram-bot-api.md](./research/telegram-bot-api.md).

## 2. Границы v1

Входит:

- Relay текста, фото, видео, голосовых, аудио, файлов и альбомов в обе стороны.
- Ответы (reply) и правки в обе стороны; удаления только MAX → Telegram.
- Форматирование в обе стороны: `elements` MAX ↔ сущности Telegram (смещения UTF-16 у обеих
  сторон). Telegram → MAX добавлено 13.09.2026; неподдерживаемые в MAX сущности отбрасываются,
  текст сохраняется.
- Fallback для содержимого без аналога (раздел 7).
- Catch-up после простоя, `/history N` по запросу.
- Несколько Account, вход в MAX по SMS с 2FA-паролем, веб-интерфейс Operator.
- Два режима Inbox: личный чат с ботом (по умолчанию) и супергруппа-форум.
- Реакции Telegram → MAX (добавлено 13.09.2026 по итогам живой проверки).
- Topic по запросу Operator из UI, без ожидания первого сообщения (добавлено 13.09.2026).

Не входит (v2): реакции MAX → Telegram (сервер не присылает событие о реакции собеседника,
PyMax issue #97; проверяется зондом), начало разговора из Telegram
(`/chats`, `/find`), локальный Bot API server для больших файлов, вход по QR через Web-клиент,
отметка «прочитано» в MAX, typing и presence.

## 3. Архитектура

Один Docker-образ, два процесса.

```
┌──────────────────────────────┐        ┌──────────────────────────┐
│ ui  (Streamlit)              │ HTTP   │ bridge (asyncio)         │
│  страницы Operator           │ ─────► │  InternalApi (aiohttp)   │
│  пароль из env               │ token  │  Supervisor              │
└──────────────────────────────┘        │   ├─ AccountRunner #1    │
                                        │   │   ├─ MaxClient (PyMax)
                                        │   │   ├─ TgBot (aiogram) │
                                        │   │   └─ RelayEngine     │
                                        │   └─ AccountRunner #2 …  │
                                        │  Storage (SQLite, Fernet)│
                                        └──────────────────────────┘
                                                    │ /data
```

- **bridge** владеет базой данных и единственный пишет в неё. Держит по одному `AccountRunner`
  на активный Account. Внутренний HTTP API служит только для `ui` (ADR-0001).
- **ui** не имеет доступа к файлу базы, ходит в API с токеном `MAXGATE_INTERNAL_TOKEN`.
  Если bridge недоступен, показывает это и ничего не делает.
- Команды Operator (запустить вход, ввести код, пауза, удалить) исполняются Supervisor
  прямо в процессе, без опроса базы.

### 3.1 AccountRunner

Жизненный цикл одного Account:

1. Создаёт `MaxClient` (обёртка над `pymax.Client`) с собственным `StoreProtocol`
   (ADR-0002), провайдером SMS-кода на `asyncio.Queue` и `ExtraConfig(relogin=False)`.
   Автоматический relogin PyMax выключен намеренно: иначе при отзыве Session библиотека сама
   запустит SMS-вход и повиснет в ожидании кода.
2. Запускает `client.start()` задачей. Вход в MAX происходит внутри неё: библиотека запрашивает
   код и ждёт его из очереди. Operator через API кладёт код (и при необходимости пароль 2FA)
   в очередь. На ввод кода сервер MAX даёт около 60 секунд; UI показывает обратный отсчёт с этим
   допущением.
3. После `on_start` (первый вход и каждое переподключение) запускает Catch-up (раздел 8) и,
   если ещё не запущен, polling Telegram-бота: свой `Bot` и `Dispatcher` на Account,
   `dp.start_polling(bot)` отдельной задачей.
4. События MAX и апдейты Telegram превращаются в задания `RelayEngine`.
5. Остановка: `dp.stop_polling()`, `client.stop()`, дожидание очередей.

Падение задачи runner'а перезапускается Supervisor с растущей паузой (5, 30, 120 секунд),
после пяти подряд неудач Account переводится в `error`.

### 3.2 Состояния Account

```
new ──login──► logging_in ──код принят──► active ◄──resume── paused
                   │  │                      │  ▲              ▲
                   │  └─2FA─► password_required │              │
                   ▼                            │          pause
                 error ◄────────────────────────┤
                                                ▼
                                          session_lost ──login──► logging_in
```

- `session_lost`: PyMax сообщил `FAIL_LOGIN_TOKEN` или `FAIL_LOGOUT_ALL`. Owner получает Note
  в Inbox, Operator проводит вход заново.
- `error` хранит причину текстом, видна в UI.
- Удаление Account стирает его строки в базе; Topic в Telegram не трогаются.

### 3.3 RelayEngine

- На каждый ChatLink своя FIFO-очередь и один воркер: порядок сообщений внутри чата сохраняется,
  чаты друг друга не тормозят.
- Задание выполняется до трёх раз с паузами 1, 5 и 30 секунд. Ответ Telegram 429 соблюдается
  по `retry_after` и в счётчик попыток не входит.
- После третьей неудачи: для MAX → Telegram Note в Topic с причиной, для Telegram → MAX ответ
  «❌ причина» на сообщение Owner.
- Медиа проходят через временный файл в `/data/tmp`, который удаляется сразу после задания.

## 4. Модель данных

SQLite в `/data/maxgate.db`, SQLAlchemy 2 (async, `aiosqlite`), миграции Alembic. Поля
с суффиксом `_enc` зашифрованы Fernet ключом `MAXGATE_SECRET_KEY`.

| Таблица | Поля | Назначение |
|---|---|---|
| `accounts` | `id`, `name`, `phone`, `tg_bot_token_enc`, `owner_tg_user_id`, `inbox_mode` (`private`/`supergroup`), `inbox_chat_id` (null до `/start`), `relay_channels`, `state`, `state_reason`, `created_at`, `updated_at` | Account |
| `max_sessions` | `account_id` (PK), `token_enc`, `device_id`, `phone`, `mt_instance_id`, `user_agent_json`, `sync_json`, `updated_at` | Session MAX, читается и пишется через `StoreProtocol` |
| `chat_links` | `id`, `account_id`, `max_chat_id`, `max_chat_type`, `max_title`, `topic_id` (null, пока Topic не создан), `renamed_by_owner`, `muted`, `last_relayed_time` (мс), `created_at`; уникально `(account_id, max_chat_id)`, индекс `(account_id, topic_id)` | ChatLink |
| `message_links` | `id`, `account_id`, `chat_link_id`, `max_message_id`, `tg_message_id`, `part`, `direction`, `created_at`; индексы по `(account_id, tg_message_id)` и `(chat_link_id, max_message_id)` | MessageLink; одно сообщение MAX может дать несколько сообщений Telegram (альбом, длинный текст), отсюда `part` |
| `max_users` | `account_id`, `user_id`, `display_name`, `phone`, `updated_at` | Кеш имён для подписей в Group и названий Topic |
| `account_events` | `id`, `account_id`, `ts`, `level`, `message` | Журнал для страницы Account в UI; хранится 1000 последних записей |

MessageLink старше 90 дней удаляются ежесуточно. Ответ на сообщение старше этого срока уходит
без reply.

## 5. Внутренний API bridge

`aiohttp` (уже зависимость PyMax), слушает только внутри docker-сети, заголовок
`Authorization: Bearer <MAXGATE_INTERNAL_TOKEN>`.

| Метод и путь | Действие |
|---|---|
| `GET /health` | Живость процесса |
| `GET /accounts`, `POST /accounts` | Список, создание. При создании проверяется токен бота через `getMe`; для режима `private` требуется `has_topics_enabled` |
| `GET /accounts/{id}`, `PATCH /accounts/{id}`, `DELETE /accounts/{id}` | Карточка, правка настроек (`owner_tg_user_id`, `inbox_mode`, `relay_channels`, `name`), удаление |
| `POST /accounts/{id}/login` | Запустить вход: запрос SMS-кода |
| `POST /accounts/{id}/login/code`, `POST /accounts/{id}/login/password` | Передать код, передать пароль 2FA |
| `POST /accounts/{id}/pause`, `POST /accounts/{id}/resume` | Пауза и возобновление |
| `POST /accounts/{id}/logout` | Удалить Session (следующий запуск потребует SMS) |
| `GET /accounts/{id}/events` | Последние записи журнала |
| `GET /accounts/{id}/chats`, `POST /accounts/{id}/chats/{link_id}/mute`, `.../unmute` | ChatLink и их состояние |
| `POST /accounts/{id}/chats/{link_id}/topic` | Создать Topic для ChatLink заранее (имя и цвет как при автоматическом создании); если Topic уже есть, вернуть его |

## 6. Потоки

### 6.1 Заведение Account

0. Предварительно у аккаунта MAX должен быть включён пароль для входа (2FA): в приложении
   MAX «Профиль → Приватность → Пароль для входа». Без него сервер отвечает на SMS-код ошибкой
   `error.profile.active.session.no2fa` «Логин ограничен. Требуется установить 2FA»
   (проверено 13.09.2026 на живом аккаунте). UI показывает это требование до запроса кода.
1. Operator в UI вводит имя, телефон, токен бота, Telegram id Owner, режим Inbox.
2. Operator нажимает «Войти в MAX», на телефон приходит SMS, Operator вводит код (и пароль 2FA,
   если MAX его попросит) в течение минуты. Account переходит в `active`.
3. Owner пишет боту `/start`. Бот сверяет `from.id` с `owner_tg_user_id`; чужим отвечает отказом
   и больше не реагирует. В режиме `private` `inbox_chat_id` становится id этого чата.
   В режиме `supergroup` Owner пишет `/start` внутри форума, бот проверяет `is_forum` и свои
   права `can_manage_topics`, запоминает id группы.
4. До `/start` сообщения MAX копятся в очередях не дольше часа, потом отбрасываются с записью
   в журнал.

### 6.2 MAX → Telegram

1. Событие `on_message` проходит фильтры: тип чата (Channel только при `relay_channels`),
   ChatLink не Muted, не Echo.
2. Echo: событие с `sender == me` откладывается на 2 секунды, затем ищется в MessageLink
   по `max_message_id`. Найдено, значит это наше отправление из Topic, событие отбрасывается.
   Не найдено, значит Owner писал с телефона, сообщение уходит в Topic с подписью «Вы».
3. Находится или создаётся ChatLink. Topic создаётся при первом сообщении: имя из `Chat.title`
   для Group и Channel, из имени собеседника для Dialog (при пустом имени телефон); цвет иконки
   по виду MaxChat: Dialog светло-зелёный `0x8EEE98`, Group светло-синий `0x6FB9F0`,
   Channel оранжевый `0xFB6F5F`.
4. Собирается `RelayMessage` (раздел 7): текст с сущностями форматирования, вложения, ссылка
   на ответ через MessageLink, подпись отправителя в Group.
5. Воркер ChatLink скачивает вложения (фото по `base_url`, файлы и видео по временной ссылке
   из `get_file_by_id` / `get_video_by_id`), отправляет в Telegram с `message_thread_id`,
   пишет MessageLink, обновляет `last_relayed_time`.
6. Ошибка «message thread not found» означает, что Owner удалил Topic: `topic_id` обнуляется,
   Topic создаётся заново, задание повторяется один раз.

### 6.3 Telegram → MAX

1. Апдейт `message` из Inbox. `from.id` не Owner: игнор. Сообщение вне Topic: ответ-подсказка
   со списком команд, дальше ничего.
2. ChatLink ищется по `message_thread_id`. Нет ChatLink (Topic создан Owner вручную): Note
   «этот топик не связан с чатом MAX».
3. Текст уходит в MAX как есть, минуя markdown-разбор PyMax; сущности Telegram (bold, italic,
   underline, strikethrough, code, pre, text_link, url, blockquote) переводятся в `elements` MAX,
   остальные отбрасываются. Вложения скачиваются через `getFile` (до 20 МБ, иначе Note).
   Альбом (`media_group_id`) копится 1,5 секунды и уходит одним сообщением MAX.
4. `reply_to` берётся из MessageLink по `reply_to_message.message_id`; если связи нет,
   сообщение уходит без ответа.
5. Результат `send_message` даёт `Message.id`, пишется MessageLink с направлением `tg_to_max`.
   Тишина означает доставку; неудача после повторов даёт ответ «❌ причина».

### 6.4 Правки, удаления, переименования

- MAX `on_message_edit` → MessageLink → `editMessageText` или `editMessageCaption`. Смена
  вложений в правке даёт Note «сообщение изменено, вложение обновить нельзя».
- Telegram `edited_message` → MessageLink → `edit_message` в MAX.
- MAX `on_message_delete` → MessageLink → в Topic уходит Note «🗑️ удалено!» (эмодзи U+1F5D1 с селектором U+FE0F, затем пробел U+0020) ответом на
  исходное сообщение; само сообщение не меняется (решение 13.09.2026: Bot API не даёт
  прочитать текст сообщения, а хранить его не хотим). Telegram об обычном удалении копии Owner
  не сообщает, поэтому автоматического обратного пути нет.
- `/delete` ответом на сообщение в Topic удаляет связанное через MessageLink сообщение в MAX
  для всех участников (`delete_message(chat_id, [message_id], for_me=False)`). После подтверждения
  MAX Gate удаляет все связанные части сообщения в Telegram и команду, затем удаляет MessageLink.
  При отказе MAX копии остаются, Owner получает Note «❌ безопасная причина» ответом на команду.
  Без reply или без MessageLink в этом Topic — подсказка; чужие Topic/Account не затрагиваются.
  Удаление выполняется в очереди ChatLink, собственный Echo удаления не создаёт Note
  «🗑️ удалено!». Если очистка Telegram временно не удалась, повторяются только незавершённые
  шаги: подтверждённое удаление в MAX и уже удалённые части не повторяются. Отсутствующая
  копия Telegram считается уже удалённой; остальные ошибки дают Note после повторов.
- MAX `on_chat_update` с новым `title` → если `renamed_by_owner` ложь, `editForumTopic`.
- Telegram служебное `forum_topic_edited` → `renamed_by_owner = true`, если это не наше
  собственное переименование (runner помнит свои ожидающие правки имени).
- Telegram `message_reaction` в Topic (подписка через `allowed_updates`) → MessageLink →
  `add_reaction` или `remove_reaction` в MAX с тем же эмодзи; реакция на сообщение без
  MessageLink игнорируется. Обратное направление ждёт события от сервера MAX (раздел 2).

## 7. Содержимое и Fallback

Единая внутренняя модель `RelayMessage`: `text`, `entities` (тип, смещение, длина, url;
смещения в UTF-16, как и у обеих сторон), `attachments` (вид, источник, имя, размер, mime,
длительность, размеры), `reply_to`, `sender`, `forwarded_from`, `notes`.

### 7.1 MAX → Telegram

| Содержимое MAX | В Telegram |
|---|---|
| Текст с `elements` | Текст с сущностями (STRONG→bold, EMPHASIZED→italic, UNDERLINE, STRIKETHROUGH, MONOSPACED→code, CODE→pre, LINK→text_link, HEADING→bold, QUOTE→blockquote); длиннее 4096 режется по абзацам на несколько сообщений |
| Фото | `sendPhoto`; альбом через `sendMediaGroup` |
| Видео, кружок (`video_type == 1`) | `sendVideo`, `sendVideoNote` |
| Голосовое (`AudioAttachment`) | Формат определяется по содержимому: OGG/MP3/M4A через `sendVoice`, иначе `sendAudio` |
| Файл | `sendDocument`, имя сохраняется |
| Стикер | Статичный как фото по `url`, анимированный как документ |
| Контакт | `sendContact` |
| Опрос | Note с вопросом и вариантами |
| Геолокация (не типизирована в PyMax) | `sendLocation`, если в сыром вложении есть координаты, иначе Note |
| Звонок | Note «📞 пропущенный/отклонённый звонок, длительность» |
| Пересланное (`ForwardLink`) | Префикс «↪️ Переслано от X» перед содержимым |
| Системное событие (`ControlAttachment`) | Note |
| Неизвестное вложение | Note с типом; если есть URL, документ |

Подпись к медиа длиннее 1024 символов уходит отдельным сообщением после медиа. В Group
каждое сообщение начинается с жирного имени отправителя; в Dialog подписи нет; сообщения Owner
с телефона подписываются «Вы». Файл больше 50 МБ заменяется Note с именем и размером.

### 7.2 Telegram → MAX

| Содержимое Telegram | В MAX |
|---|---|
| Текст | Текст без изменений, сущности Telegram → `elements` MAX |
| Фото, видео, документ, аудио | `Photo`, `Video`, `File`, `File` |
| Голосовое | `Voice` (Telegram отдаёт OGG/Opus, MAX принимает только OGG); при ошибке отправки тот же файл как `File` с пометкой |
| Альбом | Одно сообщение с несколькими вложениями |
| Контакт | Текст «Контакт: имя, телефон» |
| Геолокация | Текст со ссылкой на карту |
| Пересланное | Префикс «↪️ Переслано от X» и содержимое |
| Стикер, кружок, опрос | Не переносятся; Owner получает ответ «⛔ в MAX не переносится» |
| Файл больше 20 МБ | Ответ «⛔ Telegram не отдаёт ботам файлы больше 20 МБ» |

## 8. Catch-up и история

- При каждом `on_start` runner вызывает `fetch_chats()` и для каждого чата с
  `last_event_time` больше `last_relayed_time` его ChatLink запрашивает
  `fetch_history(from_time=last_relayed_time, forward=101, backward=0)`. Переносятся первые
  100, при остатке в Topic уходит Note «пропущено ещё N». Сообщения Catch-up получают префикс
  с временем отправки.
- Чаты без ChatLink при первом запуске Account получают `last_relayed_time = now` и историю
  не подтягивают.
- `/history N` в Topic: `fetch_history(backward=N)`, N обязателен (без N ответ «укажите число
  сообщений»), не больше 100; сообщения уходят с временем и с записью MessageLink, чтобы
  на них можно было отвечать и видеть правки и удаления. Уже отражённые сообщения (есть
  MessageLink) повторно не отправляются.
- `/reload N` (решение 13.09.2026): тот же запрос `fetch_history(backward=N)` и обязательное
  положительное N с ограничением 100, но уже отражённые сообщения отправляются заново.
  Сценарий — Owner удалил копии в Telegram и хочет получить последние N снова. После успешной
  отправки каждого сообщения его старые MessageLink атомарно заменяются новыми. Правки и
  удаления из MAX, ответы из Telegram используют свежую копию; старые оставшиеся в Telegram
  копии больше не связаны с MAX. При неудаче отправки старые MessageLink сохраняются.
- `/history N` и `/reload N` добавляют префикс времени и идут по `(time, id)`. Каждое сообщение
  имеет собственное исполнение, повторы и Note при окончательном сбое; остальные продолжают
  доставляться. Вся история исполняется внутри одного задания очереди ChatLink, поэтому живые
  события её не обгоняют. Недоступное вложение заменяется пометкой в тексте, как в обычном Relay.

## 9. Команды бота

| Команда | Где | Действие |
|---|---|---|
| `/start` | Inbox | Привязка Inbox, приветствие; чужим отказ |
| `/status` | Inbox вне Topic | Состояние Session, число ChatLink, длина очередей, время последнего события MAX |
| `/mute`, `/unmute` | Topic | Переключить Muted. Mute односторонний: сообщения Owner из Topic в MAX по-прежнему идут |
| `/history N` | Topic | Подгрузить последние N сообщений без повторной отправки отражённых, N обязателен, максимум 100 |
| `/reload N` | Topic | Заново отправить последние N сообщений и заменить их MessageLink, N обязателен, максимум 100 |
| `/delete` | Topic, ответ на сообщение | Удалить соответствующее сообщение в MAX для всех участников, затем копии в Telegram и команду |
| `/info` | Topic | Вид MaxChat, название в MAX, число участников, id |

## 10. Конфигурация и деплой

`.env` (см. `.env.example`):

| Переменная | Назначение |
|---|---|
| `MAXGATE_SECRET_KEY` | Ключ Fernet; создаётся командой `maxgate gen-key` |
| `MAXGATE_UI_PASSWORD` | Пароль Operator в Streamlit |
| `MAXGATE_INTERNAL_TOKEN` | Токен UI ↔ bridge |
| `MAXGATE_DATA_DIR` | По умолчанию `/data` |
| `MAXGATE_MAX_APP_VERSION` | Версия приложения MAX для PyMax; каталог версий подтягивается удалённо |
| `TEL`, `TG_BOT_TOKEN` | Только для разработки: `maxgate seed-account` создаёт из них Account |

`docker-compose.yml`: сервисы `bridge` и `ui` из одного образа (`python:3.12-slim`, `uv`),
том `maxgate-data:/data`, `restart: unless-stopped`, healthcheck bridge по `/health`,
порт UI `127.0.0.1:8501`. HTTPS и внешний доступ обеспечивает reverse proxy сервера.
Профиль `bigfiles` с локальным Bot API server заложен на v2.

Telegram получает апдейты long polling: публичный адрес не нужен.

## 11. Раскладка кода

```
maxgate/
  config.py          настройки из env (pydantic-settings)
  crypto.py          Fernet
  db/                модели SQLAlchemy, фабрика сессий, миграции Alembic
  domain/            чистая логика без I/O: RelayMessage, рендер подписей и Note,
                     конвертация форматирования, разбиение текста, политика повторов
  max/               адаптер PyMax: клиент, StoreProtocol, провайдер SMS-кода, скачивание
  tg/                адаптер aiogram: обработчики, Topic, отправка медиа, лимиты
  relay/             очереди по ChatLink, конвейеры max_to_tg и tg_to_max, catch-up
  bridge/            Supervisor, AccountRunner, InternalApi, точка входа
  ui/                Streamlit: страницы Operator, клиент внутреннего API
  cli.py             gen-key, migrate, seed-account
tests/               юнит-тесты domain и relay на фейковых адаптерах
```

Адаптеры `max/` и `tg/` скрывают библиотеки за узкими интерфейсами, чтобы `relay/` и
`domain/` тестировались без сети.

## 12. Риски

| Риск | Что делаем |
|---|---|
| MAX API неофициальный: возможны ограничения и блокировка аккаунта | Осознанно принято. Настройки PyMax оставлены штатными (эмуляция Android, telemetry). Ошибка «возможности профиля ограничены» уходит Note в Topic |
| Сервер MAX отвергает старые версии клиента | Версия и удалённый каталог настраиваются через env; PyMax закреплён точной версией и обновляется осознанно |
| SMS-код не приходит части пользователей (PyMax issue #99) | В v2 вход по QR через Web-клиент |
| Топики в личном чате свежая функция Telegram (issue #847) | Живой тест 13.09.2026 прошёл; режим `supergroup` как запасной |
| Отправка голосовых в MAX ломается в PyMax 2.4.x | Fallback файлом |
| Неизвестно, приходят ли события о сообщениях Owner с телефона | Проверяется на первой живой сессии; при отсутствии история в Topic будет без них |
| Реакции из MAX сервер не присылает | Telegram → MAX в v1; MAX → Telegram после появления события |

## 13. Порядок реализации

### Шаг 0. Проверка PyMax на живом аккаунте

Вся конструкция держится на неофициальной библиотеке, поэтому до написания приложения
проверяется, что она вообще работает. Одноразовый скрипт `spikes/pymax_smoke.py`, без базы,
без Telegram, сессия в gitignored-папке `temp/`. Проверяется по порядку:

| # | Проверка | Критерий |
|---|---|---|
| 1 | Вход по SMS с номером из `TEL` | `client.me` заполнен, Session сохранена, повторный запуск входит без SMS |
| 2 | `fetch_chats()` | Список чатов с `type`, `title`, участниками; Dialog и Group различимы |
| 3 | Входящее сообщение от контакта | `on_message` срабатывает, есть `chat_id`, `sender`, `text` |
| 4 | Сообщение Owner с телефона | Фиксируется, приходит ли событие с `sender == me` (открытый вопрос из раздела 12) |
| 5 | Входящее фото и файл | Из `attaches` получается рабочий URL, файл скачивается |
| 6 | Отправка текста и фото из скрипта | Собеседник видит сообщение; событие о нём в `on_message` либо не приходит, либо приходит с тем же `id` |
| 7 | `fetch_history(backward=10)` | Возвращает последние сообщения чата |
| 8 | `read_message`, `edit_message`, `delete_message` | Работают на своём сообщении (нужны для правок и удалений в v1) |

Идём дальше только при успехе проверок 1, 2, 3, 5, 6 и 7. Провал 4 или 8 сужает v1
(история без сообщений с телефона, правки без правок), но не останавливает. Провал входа
или приёма сообщений останавливает проект до выяснения причин (PyMax issue #99, версия
клиента, ограничения аккаунта).

Telegram-сторона уже проверена 13.09.2026 на боте `@fsmax_bot`: создание Topic в личном чате,
текст, документ и фото в Topic, переименование и удаление Topic.

**Результат шага 0 (13.09.2026): все восемь проверок пройдены**, плюс распознавание reply
и события правки и удаления от собеседника. Подробности и найденные особенности в
[research/pymax-2.4.1.md](./research/pymax-2.4.1.md), разделы 13–16. Главное для реализации:

- Сообщения Owner с телефона приходят в `on_message` с `sender == me`; отправки из той же
  сессии эхом не возвращаются. Echo сводится к проверке MessageLink без задержки.
- Голосовые MAX это OGG/Opus, фото отдаются как WEBP, файлы качаются с хоста с сертификатом
  Минцифры (нужен ограниченный SSL-контекст, раздел 14 отчёта).
- У сообщений из `fetch_history` поле `chat_id` пустое, у элементов форматирования может
  не быть смещения: оба случая обрабатываются в адаптере.
- Клиента останавливать отменой задачи `start()`, а не `stop()`.
- Для входа нужен включённый пароль 2FA у аккаунта MAX.

### Шаги 1–7. Приложение

1. Каркас: `pyproject`, настройки, база и миграции, crypto, CLI `gen-key` и `migrate`.
2. `max/`: клиент, StoreProtocol, вход по SMS через очередь. Код из шага 0 переезжает сюда.
3. `tg/`: бот, привязка Owner, создание Topic, отправка текста и медиа.
4. `relay/`: MAX → Telegram текст, затем медиа, затем обратное направление.
5. Ответы, правки, удаления, переименования, Catch-up, команды.
6. `bridge/`: Supervisor, API; `ui/`: страницы Operator.
7. Docker, compose, `.env.example`, README по развёртыванию.

После шагов 2 и 4 делается живая проверка на dev-аккаунте, а не только юнит-тесты.
