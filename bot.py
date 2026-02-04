import asyncio
import logging
import os
import re
import sqlite3
from contextlib import closing

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

# ================== НАСТРОЙКИ ==================
# Токен и админы только из переменных окружения (никогда не храните их в коде и в Git).

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_STR = (os.getenv("ADMIN_IDS") or "").strip()
ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()}

# База: на Vercel можно задать DB_PATH (например /tmp/bot.db — данные не сохраняются между вызовами).
DB_PATH = os.getenv("DB_PATH", "bot.db")


def is_admin(user_id: int) -> bool:
    """Если список админов задан — только они могут менять кнопки. Иначе — все."""
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ================== РАБОТА С БАЗОЙ ==================

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        # Таблица ссылок меню: одна запись = одна кнопка
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL
            )
            """
        )

        # Таблица с id последнего меню-сообщения в каждой теме
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_menus (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                menu_message_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, thread_id)
            )
            """
        )

        conn.commit()


def add_link(chat_id: int, thread_id: int, title: str, url: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO topic_links (chat_id, thread_id, title, url)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, thread_id, title, url),
        )
        conn.commit()


def get_links(chat_id: int, thread_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT title, url
            FROM topic_links
            WHERE chat_id = ? AND thread_id = ?
            ORDER BY id ASC
            """,
            (chat_id, thread_id),
        )
        return cur.fetchall()


def clear_links(chat_id: int, thread_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM topic_links
            WHERE chat_id = ? AND thread_id = ?
            """,
            (chat_id, thread_id),
        )
        conn.commit()


def remove_link_at_index(chat_id: int, thread_id: int, index_1based: int) -> bool:
    """Удаляет одну кнопку по номеру (1, 2, 3...). Возвращает True, если удалено."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM topic_links
            WHERE chat_id = ? AND thread_id = ?
            ORDER BY id ASC
            """,
            (chat_id, thread_id),
        )
        ids = [row[0] for row in cur.fetchall()]
        if index_1based < 1 or index_1based > len(ids):
            return False
        cur.execute("DELETE FROM topic_links WHERE id = ?", (ids[index_1based - 1],))
        conn.commit()
        return True


def get_menu_message_id(chat_id: int, thread_id: int) -> int | None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT menu_message_id
            FROM topic_menus
            WHERE chat_id = ? AND thread_id = ?
            """,
            (chat_id, thread_id),
        )
        row = cur.fetchone()
        return row[0] if row else None


def set_menu_message_id(chat_id: int, thread_id: int, message_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO topic_menus (chat_id, thread_id, menu_message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                menu_message_id = excluded.menu_message_id
            """,
            (chat_id, thread_id, message_id),
        )
        conn.commit()


def clear_menu_message_id(chat_id: int, thread_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM topic_menus WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        conn.commit()


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def build_tg_link(chat: types.Chat, message_id: int) -> str:
    """
    Строим ссылку на сообщение.
    Если у чата есть username -> https://t.me/username/message_id
    Иначе -> https://t.me/c/<internal_id>/<message_id>
    """
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"

    # Для супергрупп без username
    chat_id = chat.id  # обычно отрицательное число вида -1001234567890
    internal_id = abs(chat_id)

    # Часто реальный id канала = internal_id без первых трёх цифр "100"
    s = str(internal_id)
    if s.startswith("100"):
        s = s[3:]

    return f"https://t.me/c/{s}/{message_id}"


def build_keyboard_for_topic(chat_id: int, thread_id: int) -> InlineKeyboardMarkup | None:
    links = get_links(chat_id, thread_id)
    if not links:
        return None

    # Делаем по одной кнопке в строке (можно сгруппировать по 2 и т.д.)
    rows: list[list[InlineKeyboardButton]] = []
    for title, url in links:
        rows.append([InlineKeyboardButton(text=title, url=url)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def recreate_menu_in_topic(message: types.Message, bot: Bot):
    """
    Удаляет старое меню в теме и создаёт новое как последнее сообщение.
    Вызывается при каждом новом сообщении в теме,
    если для темы уже есть хотя бы одна ссылка.
    """
    if not message.is_topic_message:
        return

    chat = message.chat
    chat_id = chat.id
    thread_id = message.message_thread_id

    # Если для темы нет ссылок — меню не создаём.
    kb = build_keyboard_for_topic(chat_id, thread_id)
    if kb is None:
        return

    # Удаляем старое меню (если есть)
    old_menu_id = get_menu_message_id(chat_id, thread_id)
    if old_menu_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_menu_id)
        except TelegramBadRequest:
            # Сообщение могли уже удалить/истечь срок — игнорируем
            pass

    # Создаём новое меню внизу темы
    sent = await message.answer("⬇️ Быстрая навигация по теме", reply_markup=kb)
    set_menu_message_id(chat_id, thread_id, sent.message_id)


# ================== НАСТРОЙКА БОТА ==================

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ================== ХЕНДЛЕРЫ ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "Привет! Я бот-навигация по темам.\n\n"
            "1. Добавьте меня в супергруппу с темами.\n"
            "2. Дайте права: писать сообщения и удалять сообщения.\n"
            "3. Отключите приватность бота (в BotFather: /setprivacy -> Disable).\n\n"
            "В группе:\n"
            "- В теме ответьте на пост: <code>/add Название_кнопки</code>\n"
            "- Список кнопок: <code>/list</code>\n"
            "- Удалить кнопку: <code>delete 1</code> или <code>delete all</code>"
        )
    else:
        await message.answer(
            "Бот активирован.\n"
            " — <code>/add Название</code> — ответом на пост: добавить кнопку\n"
            " — <code>/list</code> — список кнопок (номера для delete)\n"
            " — <code>delete 1</code> — удалить кнопку №1\n"
            " — <code>delete all</code> — удалить все кнопки в теме\n\n"
            "Свой ID для прав админа: /myid"
        )


@dp.message(Command("myid"))
async def cmd_myid(message: types.Message):
    """Показывает ваш Telegram ID — его нужно прописать в ADMIN_IDS."""
    uid = message.from_user.id if message.from_user else 0
    await message.reply(
        f"Ваш ID: <code>{uid}</code>\n\n"
        "Пропишите его в настройках бота (ADMIN_IDS в коде или переменная окружения ADMIN_IDS), "
        "чтобы только вы могли добавлять и удалять кнопки."
    )


@dp.message(Command("add"))
async def cmd_add(message: types.Message, command: CommandObject):
    """Ответом на пост в теме: /add Название — добавить кнопку."""
    if message.chat.type not in {ChatType.SUPERGROUP, ChatType.GROUP}:
        await message.answer("Эта команда предназначена для групп/супергрупп.")
        return

    if message.from_user and not is_admin(message.from_user.id):
        await message.reply("Только администратор бота может добавлять кнопки.")
        return

    if not message.is_topic_message or message.message_thread_id is None:
        await message.reply("Эту команду нужно вызывать внутри темы (форум-топика).")
        return

    if not message.reply_to_message:
        await message.reply(
            "Ответьте на пост, который хотите добавить в меню, и напишите: <code>/add Название</code>"
        )
        return

    thread_id = message.message_thread_id
    chat = message.chat
    chat_id = chat.id

    # Текст кнопки
    title = (command.args or "").strip()
    if not title:
        title = "Ссылка"

    # Строим URL на сообщение, на которое ответили
    target_msg = message.reply_to_message
    url = build_tg_link(chat, target_msg.message_id)

    add_link(chat_id, thread_id, title, url)

    # Сразу обновляем меню с кнопками
    await recreate_menu_in_topic(message, bot)

    # Удаляем сообщение пользователя с командой (чистота чата)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if message.chat.type not in {ChatType.SUPERGROUP, ChatType.GROUP}:
        await message.answer("Эта команда предназначена для групп/супергрупп.")
        return

    if message.from_user and not is_admin(message.from_user.id):
        await message.reply("Только администратор бота может просматривать список кнопок.")
        return

    if not message.is_topic_message or message.message_thread_id is None:
        await message.reply("Эту команду нужно вызывать внутри темы (форум-топика).")
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id

    links = get_links(chat_id, thread_id)
    if not links:
        await message.reply("В этой теме ещё нет кнопок.")
        return

    lines = ["Кнопки в этой теме (для удаления: delete 1, delete 2, … или delete all):"]
    for idx, (title, url) in enumerate(links, start=1):
        lines.append(f"{idx}. {title} — {url}")

    await message.reply("\n".join(lines))


def _match_delete(text: str) -> str | None:
    """Возвращает 'all' или номер '1','2',… или None."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip().lower()
    if t == "delete all":
        return "all"
    m = re.match(r"^delete\s+(\d+)$", t)
    if m:
        return m.group(1)
    return None


@dp.message(
    F.chat.type.in_({ChatType.SUPERGROUP, ChatType.GROUP}),
    F.text,
    F.text.func(lambda t: _match_delete(t) is not None),
)
async def cmd_delete(message: types.Message):
    """delete 1 / delete 2 / … или delete all — только в теме, только админ."""
    if not message.is_topic_message or message.message_thread_id is None:
        return

    if message.from_user and not is_admin(message.from_user.id):
        await message.reply("Только администратор бота может удалять кнопки.")
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id
    arg = _match_delete(message.text)

    if arg == "all":
        clear_links(chat_id, thread_id)
        old_menu_id = get_menu_message_id(chat_id, thread_id)
        if old_menu_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=old_menu_id)
            except TelegramBadRequest:
                pass
            clear_menu_message_id(chat_id, thread_id)
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        return

    num = int(arg)
    if remove_link_at_index(chat_id, thread_id, num):
        await recreate_menu_in_topic(message, bot)
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
    else:
        await message.reply(f"Кнопки с номером {num} нет. Номера смотрите в <code>/list</code>.")


@dp.message(F.chat.type.in_({ChatType.SUPERGROUP, ChatType.GROUP}))
async def on_any_group_message(message: types.Message):
    """
    Любое сообщение в группе/супергруппе.
    Если это сообщение в теме, для которой есть ссылки,
    бот удаляет старое меню и создаёт новое, чтобы оно было последним.
    """
    # Не реагируем на собственные сообщения бота
    if message.from_user and message.from_user.is_bot:
        return

    if not message.is_topic_message or message.message_thread_id is None:
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id

    # Проверяем, есть ли вообще ссылки для этой темы
    if not get_links(chat_id, thread_id):
        return

    await recreate_menu_in_topic(message, bot)


# ================== ЗАПУСК ==================

async def process_update(update_dict: dict):
    """Обработка одного апдейта (для webhook на Vercel)."""
    init_db()
    from aiogram.types import Update
    update = Update.model_validate(update_dict)
    await dp.feed_update(bot, update)


async def main_polling():
    """Локальный запуск через long polling."""
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN (или создайте файл .env).")
    init_db()
    asyncio.run(main_polling())

