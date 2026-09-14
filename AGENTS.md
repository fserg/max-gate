# AGENTS.md

This file provides guidance to LLM-agents when working with code in this repository.

## Что это

Max-gate: шлюз MAX ↔ Telegram. Каждый MaxChat отражается в Topic у Telegram-бота, ответы из
Topic уходят обратно в MAX. Один Gate обслуживает несколько Account (номер MAX + свой бот + Owner).
Общение и документация на русском; термины кода (Account, Owner, Inbox, Topic, ChatLink,
MessageLink, Relay, Echo, Catch-up, Note) на английском. Глоссарий с запрещёнными синонимами:
`CONTEXT.md`. Архитектура, модель данных, потоки и границы v1: `docs/design.md`. Решения: `docs/adr/`.
Проверенные факты о PyMax 2.4.1 и Bot API: `docs/research/`. Хронология работ: `docs/implementation-log.md`. Развёртывание: `docs/deploy.md`.

## Команды

Python 3.12, uv, зависимости закреплены в `uv.lock` (`uv sync`).

```bash
uv run ruff check .                       # линт + isort (E4,E7,E9,F,I), line-length 100
uv run pytest                             # все тесты, asyncio_mode=auto
uv run pytest tests/test_relay.py -k album  # один файл / тест
uv run maxgate gen-key | migrate | seed-account --owner <tg_id> | max-check <account_id>
uv run python -m maxgate.bridge           # процесс bridge (InternalApi + Supervisor)
uv run streamlit run maxgate/ui/app.py --server.address=127.0.0.1 --server.port=8501
docker compose up --build -d              # dev: override монтирует temp/data и публикует API на 8787
docker compose -f docker-compose.yml up --build -d   # запуск без Dokploy, без override
```

Prod: push в `main` → `.gitea/workflows/deploy.yml` (тесты, образ в реестр Gitea, вебхук Dokploy) →
Dokploy поднимает `docker-compose.dokploy.yml`. Остальные ветки проходят только тесты. Настройка,
откат через `MAXGATE_IMAGE_TAG` и резервная копия: `docs/deploy.md`, решение: ADR-0004.

Настройки через `.env` (см. `.env.example`): `MAXGATE_SECRET_KEY` (Fernet), `MAXGATE_INTERNAL_TOKEN`,
`MAXGATE_UI_PASSWORD`, `MAXGATE_INTERNAL_URL`, `MAXGATE_DATA_DIR`. Локальный InternalApi на порту 8787
(8080 занят). Все маршруты API, включая `/health`, требуют `Authorization: Bearer`.

Браузерная проверка UI и скриншоты: `uv run --with playwright python temp/check_ui_shadcn.py`
(Chromium берётся из `~/.cache/ms-playwright/chromium-1181`, пароль читается из `.env` через `UiSettings`).
Папка `temp/` в gitignore: туда всё временное, включая dev-базу `temp/data` и сессию PyMax `temp/pymax/session.db`.

## Архитектура

Два процесса из одного образа (ADR-0001):

- **bridge** (`maxgate/bridge/`): asyncio. `Supervisor` держит по `AccountRunner` на активный Account,
  перезапускает упавшие с паузами 5/30/120 с, после пяти неудач переводит в `error`. `InternalApi`
  (aiohttp) обслуживает только UI. Bridge единственный писатель в SQLite.
- **ui** (`maxgate/ui/`): Streamlit + streamlit-shadcn-ui. Файл базы и ключ Fernet не получает,
  ходит в API через `InternalApiClient` (`urllib`). Дашборд перерисовывается фрагментом каждые 2 с.

`AccountRunner` создаёт `MaxClient` (обёртка над `pymax.Client`, relogin выключен намеренно),
вход в MAX идёт внутри `client.start()`; SMS-код и пароль 2FA Operator кладёт через API в
`asyncio.Queue`-провайдер. После входа запускаются Catch-up и polling aiogram (свой `Bot` и
`Dispatcher` на Account). Состояния Account: `new → logging_in → active ↔ paused`,
`password_required`, `session_lost`, `error` (схема в design.md §3.2).

Слои:

- `domain/`: чистая логика без I/O (RelayMessage, подписи, Note, конвертация форматирования,
  смещения Entity в UTF-16, разбиение текста). Тестируется без адаптеров.
- `max/`, `tg/`: адаптеры PyMax и aiogram за узкими интерфейсами. `max/store.py` реализует
  `StoreProtocol` PyMax поверх таблицы `max_sessions` с шифрованием токена (ADR-0002).
  `max/uploads.py` и `max/plaintext.py` подменяют части PyMax 2.4.1 (TLS для `*.oneme.ru`
  ограниченным контекстом, без изменения системного хранилища сертификатов).
- `relay/`: `RelayEngine` с FIFO-очередью и одним воркером на ChatLink; повторы 1/5/30 с,
  429 Telegram соблюдается по `retry_after`; окончательные ApiError MAX не повторяются
  (классификация в `relay/errors.py`). Реакции идут отдельной очередью. `relay/storage.py`
  всегда ограничивает запросы Account.
- `db/`: модели SQLAlchemy (Account, MaxSession, ChatLink, MessageLink, AccountEvent) и миграции
  Alembic в `db/migrations/versions/`.
- `diagnostics.py`, `relay/errors.py`: тексты ошибок и логи без credentials, payload и
  содержимого сообщений. Секреты (токены, номер, пароль) не выводить ни в ответы API, ни в лог, ни в чат.

Тесты `relay/` и `bridge/` работают на фейках `FakeMax`/`FakeTg` (`tests/test_relay.py`) и фикстуре
`storage` из `tests/conftest.py` (SQLite во временном каталоге с двумя Account). UI тестируется
через `streamlit.testing.v1.AppTest` с `FakeApi` (`tests/test_ui.py`).

## Особенности UI

`maxgate/ui/app.py` стилизуется одним блоком CSS в константе `CSS` под shadcn (Inter, zinc, radius 8).
Контейнеры адресуются через `st.container(key=...)`, что даёт классы `.st-key-<key>`; Streamlit
перебивает размеры и цвета внутри `st.markdown`, поэтому для `dt/dd/h3` внутри разметки задавать
`font-size`/`color` явно. Кликабельные карточки учёток реализованы невидимой native-кнопкой поверх
контейнера. Все динамические значения в HTML экранируются через `escape`.

## Ограничения

- Сертификаты в систему не ставить, проверку TLS не отключать.
- `docs/design.md` и ADR не менять без явной просьбы; новые решения оформлять как ADR.
- `git commit`/`push` только по запросу пользователя.
- Не запускать локальный bridge и Docker-bridge одновременно с одной базой.
- Не запускать локальный bridge с боевыми Account: они работают на проде (бот и сессия MAX одни).
- Push в `main` сразу уходит в прод: доработки вести в ветке и сливать после зелёного CI.
