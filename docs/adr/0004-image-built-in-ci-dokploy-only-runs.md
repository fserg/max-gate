---
status: accepted
date: 2026-09-14
---

# Образ собирает Gitea Actions, Dokploy только запускает его

Прод работает в Dokploy на отдельном сервере. Push в `main` запускает workflow
`.gitea/workflows/deploy.yml`: `ruff` и `pytest`, затем `docker build` и push образа в реестр
Gitea с тегами `:<sha12>` и `:latest`, затем вызов вебхука Compose в Dokploy. Dokploy клонирует
`main`, пишет `.env` из своей вкладки Environment и поднимает `docker-compose.dokploy.yml`;
`pull_policy: always` подтягивает свежий образ. На прод-сервере ничего не собирается, а красные
тесты не дают дойти до деплоя. Workflow лежит в `.gitea/workflows`: GitHub Actions этот каталог
не читает, поэтому публичное зеркало на GitHub ничего не собирает.

## Considered Options

- Dokploy собирает сам из git по вебхуку Gitea: сборка грузит прод-сервер и идёт без тестов.
- Два Application типа Docker (bridge и ui): общий том и сеть настраиваются вручную,
  вебхука два, а описание сервисов уходит из репозитория.
- Compose с источником raw: compose-файл живёт только в Dokploy и расходится с репозиторием.

## Consequences

- Вебхук на Dokploy вызывает CI после push образа, а не сама Gitea при push; иначе Dokploy
  успел бы задеплоить старый образ до сборки нового.
- CI видит только, что Dokploy принял вебхук. Итог деплоя смотрят в Dokploy.
- Откат образа: `MAXGATE_IMAGE_TAG=<sha12>` в Environment и Deploy. Миграции базы откат образа
  не отменяет.
- Секреты (`MAXGATE_SECRET_KEY`, токены) живут в Environment Dokploy и секретах Gitea, в git
  их нет. Адреса реестра и сервера тоже приходят из переменных, а не из файлов репозитория.
