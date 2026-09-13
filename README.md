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

## UI Operator

В `.env` задайте `MAXGATE_UI_PASSWORD`, `MAXGATE_INTERNAL_TOKEN` и
`MAXGATE_INTERNAL_URL` (локально `http://127.0.0.1:8787`). Запуск отдельно от bridge:

```bash
uv run streamlit run maxgate/ui/app.py --server.address=127.0.0.1 --server.port=8501
```

Откройте [UI](http://127.0.0.1:8501) и введите пароль Operator. UI позволяет создавать
Account, менять Owner/Inbox/Relay Channel, вводить SMS и пароль 2FA, ставить Account
на паузу, возобновлять работу, выполнять повторный вход, logout и удаление.
Карточка обновляется каждые две секунды; во время входа виден отсчёт 60 секунд
от запроса кода/пароля. Вкладки «События» и «Чаты MAX» показывают журнал и ChatLink
с управлением Muted. При недоступном bridge действия скрываются.
UI не открывает SQLite и не получает ключ шифрования базы в Docker.

## Развёртывание в Docker

Нужны Docker Engine и Compose. Один образ содержит Python 3.12, uv и зависимости,
закреплённые в `uv.lock`; процессы bridge и UI работают в разных контейнерах.

1. Скопируйте `.env.example` в `.env`. Заполните `MAXGATE_SECRET_KEY` ключом Fernet
   (`uv run maxgate gen-key`), задайте собственный пароль `MAXGATE_UI_PASSWORD`
   и случайный `MAXGATE_INTERNAL_TOKEN`. Не добавляйте `.env` в git.
2. Запустите production-конфигурацию:

   ```bash
   docker compose -f docker-compose.yml up --build -d
   docker compose -f docker-compose.yml ps
   ```

3. Откройте [127.0.0.1:8501](http://127.0.0.1:8501), войдите по паролю,
   создайте Account. Для private Inbox включите Topics у бота в BotFather.
   До SMS-входа включите пароль 2FA в приложении MAX. После входа Owner пишет `/start` боту.

Compose задаёт `MAXGATE_DATA_DIR=/data` поверх локальной `.env`. SQLite хранится
в именованном томе `maxgate-data`, который доступен только bridge. Миграции
выполняются автоматически перед запуском. InternalApi слушает `bridge:8080`
внутри Docker-сети; production-конфигурация не публикует его порт.
Healthcheck обращается к `/health` с Bearer из окружения, UI ждёт здоровый bridge.
Оба сервиса используют `restart: unless-stopped`. UI опубликован только на loopback;
HTTPS и внешний доступ обеспечивает reverse proxy сервера.

Обновление: повторите `docker compose -f docker-compose.yml up --build -d`.
Остановка: `docker compose -f docker-compose.yml down` (без `-v`, чтобы сохранить базу).
Резервную копию `maxgate.db` делайте при остановленном bridge; сохраните также
соответствующий ключ Fernet отдельно от базы.

### Docker с текущими dev-данными

Для локальной проверки создан **неотслеживаемый** `docker-compose.override.yml`:

```yaml
services:
  bridge:
    volumes:
      - ./temp/data:/data
    ports:
      - "127.0.0.1:8787:8080"
```

Он заменяет именованный том bind-mount существующей dev-базы без изменения
боевого compose. Образ работает с uid 1000; этот пользователь должен иметь доступ
к каталогу dev-данных. Не запускайте одновременно локальный bridge и Docker bridge
с одной базой. В каталоге уже имеется Session Account 1, SMS повторно не требуется.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f --tail=100 bridge
docker compose logs -f --tail=100 ui
```

В этом режиме `.env` должна содержать тот же ключ Fernet, которым зашифрована dev-база.
Override, `.env` и `temp/` исключены из git; `.dockerignore` допускает в контекст сборки
только исходники и файлы зависимостей. Секретов и dev-базы в образе нет.

## Диагностика Relay и загрузок

Каждая выполненная передача пишет INFO с направлением, Account, MaxChat и id сообщений;
содержимое сообщений в лог не попадает. WARNING/ERROR PyMax направляются в тот же лог.
Note и журнал содержат сообщение исключения и цепочку причин с удалёнными секретами,
URL-параметрами, payload и HTTP headers.

В PyMax 2.4.1 HTTP-загрузка File не добавляет корень CA для `fu2.oneme.ru`.
`maxgate/max/uploads.py` заменяет только сервис загрузки File этого клиента: TLS
проверяется с отдельным контекстом для `oneme.ru`, а для иных доменов используется
системное доверие. Перенаправления проверяются по одному; ожидание FILE_READY сохранено.
Системное хранилище сертификатов не изменяется.
