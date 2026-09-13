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

Готовы домен, хранилище и адаптеры. `MaxClient.on(...)` подключает события MAX,
`TgBot.on_message`, `on_edit` и `on_topic_edited` — обработчики будущего AccountRunner.
Методы отправки принимают `RelayMessage`, для отправки медиа используются локальные
файлы; вызывающий конвейер отвечает за их удаление. Telegram передаёт исходное
сообщение вместе с RelayMessage, чтобы конвейер мог собрать альбом по `media_group_id`.
Сборка альбомов, очереди Relay, Catch-up, Supervisor и интерфейс Operator относятся
к следующим частям. Сквозной Relay этим каркасом пока не запускается.
