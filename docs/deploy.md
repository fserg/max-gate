# Развёртывание в Dokploy

Решение и альтернативы описаны в [ADR-0004](adr/0004-image-built-in-ci-dokploy-only-runs.md).

```text
push в main ─► Gitea Actions: ruff, pytest ─► docker build ─► реестр Gitea (:<sha12>, :latest)
            ─► вебхук Compose в Dokploy ─► git clone main, .env, docker compose up -d
```

Любая другая ветка и pull request проходят только `test`: образ не собирается, прод не трогается.

## Настройка

Dokploy, Compose:

- источник Gitea, репозиторий `max-gate`, ветка `main`, Compose Path `./docker-compose.dokploy.yml`;
- Auto Deploy включён, иначе вебхук отвечает 400. Вебхук Gitea → Dokploy в настройках
  репозитория не заводить: деплой запускает CI, когда образ уже в реестре;
- Environment: `MAXGATE_IMAGE` (`<реестр>/<владелец>/max-gate`, без тега), `MAXGATE_SECRET_KEY`,
  `MAXGATE_INTERNAL_TOKEN`, `MAXGATE_UI_PASSWORD`; по желанию `MAXGATE_IMAGE_TAG`;
- домен: сервис `ui`, порт 8501, HTTPS Let's Encrypt. Портов наружу compose не публикует.

Сервер развёртывания должен быть залогинен в реестр под root (`docker login`): Dokploy
запускает compose с `DOCKER_CONFIG=/root/.docker`.

Секреты репозитория в Gitea (Settings → Actions → Secrets):

- `REGISTRY_TOKEN`: личный токен владельца со scope `package: Read and Write`. Встроенный
  токен Actions в Gitea 1.26 не может пушить в реестр пользователя;
- `DOKPLOY_WEBHOOK_URL`: `https://<dokploy>/api/deploy/compose/<refreshToken>` из вкладки
  Deployments. Без него CI собирает образ и пропускает деплой.

## Доработки без поломки прода

- Работать в отдельной ветке и пушить её: CI прогоняет тесты. В `main` сливать после зелёного CI.
- Если тесты в `main` упали, образ не собирается и прод остаётся на прошлой сборке.
- Не запускать локальный bridge с боевыми Account: Telegram отдаёт обновления одному
  получателю (второй получает 409 Conflict), а сессия MAX у Account одна. Для живых проверок
  нужен свой тестовый бот и Account в dev-базе `temp/data`.
- Миграции Alembic применяются при старте bridge (`maxgate migrate`). Перед слиянием миграции
  в `main` сделать резервную копию базы.

## Откат

В Environment задать `MAXGATE_IMAGE_TAG=<sha12>` нужной сборки (теги видны в пакетах Gitea
и в заголовках деплоев Dokploy) и нажать Deploy. Пока тег закреплён, новые push деплоят его же.
Удалить переменную, чтобы вернуться на `latest`. Схему базы откат образа не возвращает.

## Резервная копия и перенос базы

Данные лежат в томе с фиксированным именем `maxgate-data`. Копия без остановки через online
backup SQLite:

```bash
c=$(docker ps -qf label=com.docker.compose.service=bridge -f volume=maxgate-data)
docker exec "$c" python -c "import sqlite3; sqlite3.connect('/data/maxgate.db').backup(sqlite3.connect('/data/backup.db'))"
docker cp "$c:/data/backup.db" ./maxgate-$(date +%F).db && docker exec "$c" rm /data/backup.db
```

Хранить копию вместе с `MAXGATE_SECRET_KEY`: без ключа токены в базе не расшифровать.
Для переноса на другой сервер остановить Compose, положить `maxgate.db` в том
(`/var/lib/docker/volumes/maxgate-data/_data`, владелец uid 1000) и перенести ключ в Environment.
