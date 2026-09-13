# Max-gate

Двусторонняя пересылка сообщений из MAX в Telegram и обратно через телеграм-бота с топиками.

## Документы

- [CONTEXT.md](CONTEXT.md): глоссарий проекта.
- [docs/design.md](docs/design.md): архитектура, модель данных, потоки, границы v1,
  порядок реализации (начиная с проверки PyMax на живом аккаунте).
- [docs/adr/](docs/adr/): решения с компромиссами.
- [docs/research/](docs/research/): проверенные факты о PyMax и Telegram Bot API.

## Локальная разработка — часть 1

Нужны Python 3.12 и uv. `uv sync` устанавливает закреплённые зависимости из `uv.lock`.
В `.env` задайте `MAXGATE_SECRET_KEY` (результат `uv run maxgate gen-key`),
`MAXGATE_DATA_DIR=temp/data`, `TEL`, `TG_BOT_TOKEN` и при необходимости `MAX_PASS`.
Ключ храните вместе с резервной копией базы, отдельно от исходного кода.

```bash
uv run maxgate migrate
uv run maxgate seed-account --owner 79652610
uv run maxgate max-check 1
uv run ruff check .
uv run pytest
```

`seed-account` импортирует Session из `temp/pymax/session.db`; другой путь задаётся
через `--session`. Команда выводит id Account. Повторный запуск сохраняет актуальную
Session в базе. `max-check` использует только сохранённую Session, не запрашивает SMS
и выводит id и виды первых MaxChat без содержимого сообщений.

## Локальный запуск bridge — часть 2

```bash
uv run python -m maxgate.bridge
```

При старте Supervisor запускает Account с сохранённой Session, кроме состояний
`paused`, `session_lost` и `error`. После входа MAX запускаются Catch-up и Telegram
polling. Owner подключает Inbox командой `/start`, после чего сообщения MAX отражаются
в Topic. Доступны `/status`, `/mute`, `/unmute`, `/history N` и `/info`.

Настройки InternalApi: `MAXGATE_API_HOST` (по умолчанию `127.0.0.1`),
`MAXGATE_API_PORT` (по умолчанию `8080`) и обязательный `MAXGATE_INTERNAL_TOKEN`.
В текущей локальной `.env` выбран свободный порт **8787**, поскольку 8080 занят.
Все маршруты, включая `/health`, требуют заголовок
`Authorization: Bearer <MAXGATE_INTERNAL_TOKEN>`.
Таблица маршрутов находится в [дизайне, раздел 5](docs/design.md#5-внутренний-api-bridge).
Создание Account принимает `name`, `phone`, `tg_bot_token`, `owner_tg_user_id`,
необязательные `inbox_mode` и `relay_channels`. Ввод кода: JSON `{"code": "..."}`,
ввод 2FA: `{"password": "..."}`. Секреты в ответы API не включаются.

Лог: `MAXGATE_DATA_DIR/bridge.log`, локально `temp/data/bridge.log`; также stdout.
Журнал каждого Account доступен через `/accounts/{id}/events` и ограничен 1000 записями.
Для остановки нажмите **Ctrl+C**: приём заданий прекращается, очереди дренируются
до `MAXGATE_SHUTDOWN_TIMEOUT` секунд (по умолчанию 30), оставшиеся задачи отменяются,
соединения закрываются. Состояние active сохраняется для следующего запуска.
Одновременный запуск второго bridge с тем же каталогом данных блокируется.

Временные медиа удаляются после обработки задания. Очереди и незавершённые альбомы
находятся в памяти; после перезапуска входящие MAX восстанавливаются через Catch-up.
MessageLink старше 90 дней удаляются при старте и далее раз в сутки.
UI, Docker и конфигурация развёртывания остаются для части 3.
