# Telegram Bot API: факты, на которые опирается дизайн

Проверено 13 сентября 2026 по [документации Bot API](https://core.telegram.org/bots/api),
[changelog](https://core.telegram.org/bots/api-changelog), описанию
[форумов](https://core.telegram.org/api/forum) и живым вызовам к боту `@fsmax_bot`.
Актуальная версия Bot API на дату проверки: 10.3 (24 августа 2026).

## Топики в личном чате с ботом

- Bot API 9.3 (31.12.2025): поля `message_thread_id` и `is_topic_message` у сообщений
  в личных чатах, поле `has_topics_enabled` у `User` (возвращается в `getMe`).
- Bot API 9.4 (09.02.2026): `createForumTopic` работает «in a forum supergroup chat or a private
  chat with a user». Так же `editForumTopic` и `deleteForumTopic`. `closeForumTopic` описан
  только для супергрупп.
- Режим включается в BotFather («Threaded mode»). Флаг `allows_users_to_create_topics`
  говорит, что пользователь может сам создавать, переименовывать и удалять топики; управляется
  опцией «Disallow users to create new threads» в BotFather.
- Известный баг [tdlib/telegram-bot-api#847](https://github.com/tdlib/telegram-bot-api/issues/847)
  (май 2026, после Bot API 10.0): у части ботов `sendMessage` с `message_thread_id` в личном
  топике падает с «message thread not found».

Живой тест 13.09.2026 на `@fsmax_bot` (`has_topics_enabled: true`,
`allows_users_to_create_topics: true`):

| Вызов | Результат |
|---|---|
| `createForumTopic` в личном чате, `icon_color=0x6FB9F0` | ok, вернул `message_thread_id` |
| `sendMessage` с `message_thread_id` | ok, `is_topic_message: true` |
| `editForumTopic` (переименование) | ok |
| `sendDocument` multipart в топик | ok |
| `sendPhoto` multipart в топик | ok |
| `sendDocument` по внешнему URL | «failed to get HTTP URL content» (ссылка была нерабочей; в дизайне медиа всегда загружаются с диска) |
| `deleteForumTopic` в личном чате | ok |

Баг #847 этого бота не касается.

## Супергруппа-форум (запасной режим Inbox)

`createForumTopic`, `editForumTopic`, `closeForumTopic`, `deleteForumTopic` требуют, чтобы бот
был администратором с правом `can_manage_topics` (для удаления `can_delete_messages`).
Имя топика 1–128 символов. Допустимые цвета иконки: `0x6FB9F0`, `0xFFD67E`, `0xCB86DB`,
`0x8EEE98`, `0xFF93B2`, `0xFB6F5F`. Служебные сообщения `forum_topic_created`,
`forum_topic_edited`, `forum_topic_closed`, `forum_topic_reopened` приходят как обычные
апдейты `message`.

## Лимиты

| Что | Лимит |
|---|---|
| Отправка файла ботом (`sendDocument`, `sendVideo`, `sendVoice` и др.) | 50 МБ |
| Скачивание через `getFile` | 20 МБ; ссылка живёт не меньше часа |
| Локальный Bot API server | скачивание без лимита, отправка до 2000 МБ; нужны `api_id`/`api_hash` |
| Текст сообщения | 4096 символов |
| Подпись к медиа | 1024 символа |
| `sendMediaGroup` | 2–10 элементов; фото и видео вместе, документы и аудио только со своими |
| `sendVoice` | только OGG/Opus, MP3 или M4A; иное отправлять как аудио или документ |
| Смещения сущностей форматирования | в UTF-16 code units (как и `elements` в MAX) |

## Чего бот не получает

- Удаление сообщений пользователем: апдейта нет (есть только `deleted_business_messages`
  для бизнес-аккаунтов). Отсюда в дизайне нет удалений Telegram → MAX.
- Факт прочтения сообщений: механизма нет.
- Реакции: апдейт `message_reaction` приходит только при явной подписке в `allowed_updates`
  (в группах бот должен быть администратором).

## BotFather и ограничение по пользователям

Настройки BotFather, ограничивающей список пользователей, которым бот отвечает, в документации
не найдено; ограничение делается в коде бота по `from.id`
([Bots FAQ](https://core.telegram.org/bots/faq)). В дизайне Owner задаётся Operator
в UI как Telegram id.
