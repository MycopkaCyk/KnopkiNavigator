# Бот навигации по темам (Telegram)

Бот для групп с темами: держит в каждой теме инлайн-кнопки со ссылками на посты и всегда оставляет меню последним сообщением.

## Секреты (никогда не коммитить)

- **BOT_TOKEN** — токен бота от [@BotFather](https://t.me/BotFather).
- **ADMIN_IDS** — ваш Telegram ID (узнать: напишите боту `/myid`). Можно несколько через запятую: `123,456`.

Локально создайте файл `.env` по образцу `.env.example` и не добавляйте `.env` в Git.

## Деплой на Vercel (через GitHub)

1. **Репо и GitHub**
   - Создайте репозиторий на GitHub.
   - Заливайте только код (без `.env` и без `bot.db` — они в `.gitignore`).

2. **Vercel**
   - [vercel.com](https://vercel.com) → Import проекта из GitHub.
   - В настройках проекта → **Environment Variables** добавьте:
     - `BOT_TOKEN` — токен бота;
     - `ADMIN_IDS` — ваш ID (или несколько через запятую).
   - Сделайте деплой.

3. **Webhook**
   - После деплоя получите URL вида: `https://ваш-проект.vercel.app`.
   - Один раз выполните (подставьте свой URL и токен):
     ```bash
     set WEBHOOK_URL=https://ваш-проект.vercel.app/api/webhook
     set BOT_TOKEN=ваш_токен
     python set_webhook.py
     ```
   - Либо в PowerShell:
     ```powershell
     $env:WEBHOOK_URL="https://ваш-проект.vercel.app/api/webhook"
     $env:BOT_TOKEN="ваш_токен"
     python set_webhook.py
     ```

Важно: на Vercel нет постоянного диска. База SQLite будет в памяти/временной папке и **не сохраняется** между вызовами. Для постоянного хранения кнопок лучше использовать [Railway](https://railway.app) или [Render](https://render.com) и запускать бота в режиме polling (см. ниже).

## Локальный запуск (polling)

```bash
# Windows (PowerShell)
copy .env.example .env
# Отредактируйте .env: BOT_TOKEN и ADMIN_IDS

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Команды в группе

| Действие              | Команда / текст      |
|-----------------------|----------------------|
| Добавить кнопку       | Ответ на пост: `/add Название` |
| Список кнопок в теме  | `/list`              |
| Удалить кнопку №N     | `delete N`           |
| Удалить все кнопки    | `delete all`         |
| Узнать свой ID        | `/myid`              |

Права на команды только у пользователей, чей ID указан в `ADMIN_IDS`.
