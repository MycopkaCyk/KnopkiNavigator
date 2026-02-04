"""
Один раз после деплоя на Vercel: установить webhook Telegram на ваш URL.
Запуск: задайте BOT_TOKEN и WEBHOOK_URL в окружении, затем:
  python set_webhook.py
Пример:
  set WEBHOOK_URL=https://ваш-проект.vercel.app/api/webhook
  set BOT_TOKEN=ваш_токен
  python set_webhook.py
"""
import asyncio
import os
import sys

try:
    from aiogram import Bot
except ImportError:
    print("Установите aiogram: pip install aiogram")
    sys.exit(1)


async def main():
    token = (os.getenv("BOT_TOKEN") or "").strip()
    url = (os.getenv("WEBHOOK_URL") or "").strip().rstrip("/")
    if not token:
        print("Задайте BOT_TOKEN в окружении.")
        sys.exit(1)
    if not url:
        print("Задайте WEBHOOK_URL в окружении (например https://ваш-проект.vercel.app/api/webhook)")
        sys.exit(1)
    bot = Bot(token)
    await bot.set_webhook(url)
    info = await bot.get_webhook_info()
    print("Webhook установлен:", info.url)
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
