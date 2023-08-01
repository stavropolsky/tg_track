import logging
from configparser import ConfigParser
from pyrogram import Client, filters, idle
from pyrogram.types import Message, ChatPreview
import sqlite3
from typing import Union


# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler("telegram_monitor.log")])

# Загрузка конфигурации
config = ConfigParser()
config.read("config.ini")

api_id = config.get("telegram", "api_id")
api_hash = config.get("telegram", "api_hash")
bot_token = config.get("telegram", "bot_token")
user_channel = config.get("telegram", "user_channel")
phone = config.get("telegram", "phone")

# Создание клиентов для бота и пользователя
app = Client("user", api_id, api_hash, phone_number=phone)
bot = Client("bot", api_id, api_hash, bot_token=bot_token)

# Создание и настройка базы данных
conn = sqlite3.connect("monitoring.db", check_same_thread=False)
cursor = conn.cursor()


def create_tables():
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER UNIQUE,
            url TEXT UNIQUE,
            type TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY,
            keyword TEXT UNIQUE
        )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY,
                user_id INTEGER UNIQUE,
                username TEXT UNIQUE
        )
        """)


create_tables()


def generate_message_link(chat_id: int, message_id: int, channel_type: str, username: str = None) -> str:
    if channel_type == "public":
        return f"https://t.me/{username}/{message_id}"
    elif channel_type == "private":
        # Убираем первые 4 символа (-100) из идентификатора приватного канала
        adjusted_chat_id = str(abs(chat_id))[3:]
        return f"https://t.me/c/{adjusted_chat_id}/{message_id}"
    else:
        return None


def convert_channel_url_to_entity(channel_url):
    if channel_url.startswith("https://t.me/"):
        return "@" + channel_url.replace("https://t.me/", "")
    else:
        return channel_url


async def get_channel_info(client, channel_url):
    try:
        if channel_url.startswith("https://t.me/+"):
            chat = await client.get_chat(channel_url)
            channel_type = "private"
        else:
            channel_entity = convert_channel_url_to_entity(channel_url)
            chat = await client.get_chat(channel_entity)
            channel_type = "public"

        return {"chat": chat, "type": channel_type}
    except Exception as error:
        logging.error(f"Ошибка при обработке сообщения: {error}")
        return None


def add_channel(chat_id: int, url: str, channel_type: str):
    try:
        if not url.startswith("https://t.me/"):
            url = "https://t.me/" + url.lstrip("@")

        with conn:
            conn.execute("INSERT INTO channels (chat_id, url, type) VALUES (?, ?, ?)", (chat_id, url, channel_type))
        return True
    except sqlite3.IntegrityError:
        return False


def remove_channel(url: str):
    with conn:
        conn.execute("DELETE FROM channels WHERE url = ?", (url,))
    return conn.total_changes > 0


def add_keyword(keyword: str):
    try:
        with conn:
            conn.execute("INSERT INTO keywords (keyword) VALUES (?)", (keyword,))
        return True
    except sqlite3.IntegrityError:
        return False


def remove_keyword(keyword: str):
    with conn:
        conn.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
    return conn.total_changes > 0


def get_channels():
    with conn:
        channels = conn.execute("SELECT * FROM channels").fetchall()
    return channels


def get_channel_type(chat_id: int) -> str:
    with conn:
        result = conn.execute("SELECT type FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()
    if result:
        return result[0]
    else:
        return None


def get_keywords():
    with conn:
        keywords = conn.execute("SELECT * FROM keywords").fetchall()
    return keywords


# Функции для работы с черным списком
async def add_user_to_blacklist(user_identifier: Union[int, str]) -> bool:
    try:
        user_id = None
        username = None

        if isinstance(user_identifier, int):
            user_id = user_identifier
            user = await app.get_users(user_id)
            if user.username:
                username = user.username.lower()
        elif isinstance(user_identifier, str) and user_identifier.startswith("@"):
            username = user_identifier[1:].lower()
            user = await app.get_users(username)
            if user.id:
                user_id = user.id
        else:
            return False

        with conn:
            user_in_blacklist = conn.execute("SELECT * FROM blacklist WHERE user_id = ?", (user_id,)).fetchone()

            if user_in_blacklist:
                return False  # пользователь уже в черном списке

            conn.execute("""
            INSERT INTO blacklist (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username""",
                         (user_id, username))
        return True  # пользователь успешно добавлен в черный список
    except Exception as error:
        logging.error(f"Ошибка при добавлении пользователя в черный список: {error}")
        return False


def remove_user_from_blacklist(user_identifier: Union[int, str]) -> bool:
    try:
        if isinstance(user_identifier, int):
            with conn:
                user_in_blacklist = conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?",
                                                 (user_identifier,)).fetchone()
                if not user_in_blacklist:
                    return False
                conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_identifier,))
        elif isinstance(user_identifier, str):
            with conn:
                user_in_blacklist = conn.execute("SELECT 1 FROM blacklist WHERE username = ?",
                                                 (user_identifier,)).fetchone()
                if not user_in_blacklist:
                    return False
                conn.execute("DELETE FROM blacklist WHERE username = ?", (user_identifier,))
        else:
            raise ValueError("Неверный тип идентификатора пользователя.")

        return conn.total_changes > 0
    except Exception as error:
        logging.error(f"Ошибка при удалении пользователя из черного списка: {error}")
        return False


def get_blacklist():
    with conn:
        blacklist = conn.execute("SELECT * FROM blacklist").fetchall()
    return blacklist


def is_user_blacklisted(user_id: int) -> bool:
    with conn:
        result = conn.execute("SELECT * FROM blacklist WHERE user_id = ?", (user_id,)).fetchone()
    return result is not None


@bot.on_message(filters.command("start"))
def start_handler(_, message: Message):
    print('вызов команды start')
    message.reply("Привет! Я бот для мониторинга каналов в поисках ключевых слов."
                  " Используйте следующие команды для работы со мной:\n\n"
                  "/add_channel <ссылка на канал> - добавить канал для отслеживания\n"
                  "/remove_channel <ссылка на канал> - удалить канал из отслеживания\n"
                  "/list_channels - список отслеживаемых каналов\n"
                  "/add_keyword <ключевое слово> - добавить ключевое слово для поиска\n"
                  "/remove_keyword <ключевое слово> - удалить ключевое слово\n"
                  "/list_keywords - список ключевых слов\n"
                  "/add_to_blacklist - <имя или id пользователя> - добавить пользователя в чёрный список\n"
                  "/remove_from_blacklist - <имя или id пользователя> - удалить пользователя из чёрного списка\n"
                  "/list_blacklist - список заблокированных пользователей\n")


@app.on_message(filters.command("add_channel"))
async def on_add_channel(_, message: Message):
    text_parts = message.text.split(maxsplit=1)
    if len(text_parts) < 2:
        await message.reply("Не указана ссылка на канал.")
        return

    url = text_parts[1].strip()
    if not url:
        await message.reply("Не указана ссылка на канал.")
        return

    channel_info = await get_channel_info(app, url)
    if channel_info:
        chat = channel_info["chat"]
        if isinstance(chat, ChatPreview):
            await message.reply("Не удалось добавить канал, так как вы не являетесь участником этого приватного"
                                " канала.")
            return

        chat_id = chat.id
        channel_type = channel_info["type"]
        if add_channel(chat_id, url, channel_type):
            await message.reply("Канал успешно добавлен.")
        else:
            await message.reply("Канал уже был добавлен ранее или произошла ошибка при добавлении.")
    else:
        await message.reply("Не удалось добавить канал. Проверьте ссылку и убедитесь, что вы имеете доступ к каналу.")


@bot.on_message(filters.command("remove_channel"))
def on_remove_channel(_, message: Message):
    split_message = message.text.split(maxsplit=1)

    if len(split_message) < 2:
        message.reply("Пожалуйста, предоставьте ссылку или имя канала для удаления.")
        return

    url = split_message[1].strip()

    if remove_channel(url):
        message.reply("Канал успешно удален.")
    else:
        message.reply("Не удалось удалить канал. Возможно, он не был добавлен ранее.")


@bot.on_message(filters.command("add_keyword"))
def on_add_keyword(_, message: Message):
    text_parts = message.text.split(maxsplit=1)
    if len(text_parts) < 2:
        message.reply("Не указано ключевое слово.")
        return

    keyword = text_parts[1].strip()
    if not keyword:
        message.reply("Не указано ключевое слово.")
        return

    if add_keyword(keyword):
        message.reply("Ключевое слово успешно добавлено.")
    else:
        message.reply("Ключевое слово уже было добавлено ранее или произошла ошибка при добавлении.")


@bot.on_message(filters.command("remove_keyword"))
def on_remove_keyword(_, message: Message):
    split_message = message.text.split(maxsplit=1)

    if len(split_message) < 2:
        message.reply("Пожалуйста, предоставьте ключевое слово для удаления.")
        return

    keyword = split_message[1].strip()

    if remove_keyword(keyword):
        message.reply(f"Ключевое слово '{keyword}' успешно удалено.")
    else:
        message.reply(f"Ключевое слово '{keyword}' не найдено в базе данных. Удаление не требуется.")


@bot.on_message(filters.command("list_channels"))
def on_list_channels(_, message: Message):
    channels = get_channels()
    if channels:
        message.reply("\n".join(f"{channel[2]}" for channel in channels))
    else:
        message.reply("Нет добавленных каналов.")


@bot.on_message(filters.command("list_keywords"))
def on_list_keywords(_, message: Message):
    keywords = get_keywords()
    if keywords:
        message.reply("\n".join(f"{keyword[1]}" for keyword in keywords))
    else:
        message.reply("Нет добавленных ключевых слов.")


# Обработчики команд для работы с черным списком
@bot.on_message(filters.command("add_to_blacklist"))
async def on_add_to_blacklist(_, message: Message):
    text_parts = message.text.split(maxsplit=1)
    if len(text_parts) < 2:
        await message.reply("Не указан идентификатор пользователя (ID или имя пользователя).")
        return

    user_identifier = text_parts[1].strip()
    if not user_identifier:
        await message.reply("Не указан идентификатор пользователя (ID или имя пользователя).")
        return

    if user_identifier.startswith("@"):
        result = await add_user_to_blacklist(user_identifier)
    else:
        try:
            user_id = int(user_identifier)
            result = await add_user_to_blacklist(user_id)
        except ValueError:
            await message.reply("Идентификатор пользователя должен быть целым числом или именем пользователя,"
                                " начинающимся с @.")
            return

    if result:
        await message.reply("Пользователь успешно добавлен в черный список.")
    else:
        await message.reply("Пользователь уже находится в черном списке.")


@bot.on_message(filters.command("remove_from_blacklist"))
def on_remove_from_blacklist(_, message: Message):
    text_parts = message.text.split(maxsplit=1)
    if len(text_parts) < 2:
        message.reply("Не указано имя пользователя или его ID.")
        return

    user_info = text_parts[1].strip()
    if not user_info:
        message.reply("Не указано имя пользователя или его ID.")
        return

    if user_info.startswith("@"):
        user_info = user_info.lstrip("@")

    user_id = None

    if user_info.isdigit():
        user_id = int(user_info)
    else:
        with conn:
            result = conn.execute("SELECT user_id FROM blacklist WHERE username = ?", (user_info,)).fetchone()
        if result:
            user_id = result[0]

    if user_id is not None:
        if remove_user_from_blacklist(user_id):
            message.reply("Пользователь успешно удален из черного списка.")
        else:
            message.reply("Не удалось удалить пользователя из черного списка. Возможно, он не был добавлен ранее.")
    else:
        message.reply("Не удалось найти пользователя с указанным именем или ID.")


@bot.on_message(filters.command("list_blacklist"))
def on_list_blacklist(_, message: Message):
    blacklist = get_blacklist()

    if blacklist:
        blacklist_text = "Черный список:\n\n"
        for user in blacklist:
            user_id = user[1]
            username = user[2] or "Неизвестно"
            blacklist_text += f"ID: {user_id}, Имя пользователя: @{username}\n"
    else:
        blacklist_text = "Черный список пуст."

    message.reply(blacklist_text)


@app.on_message()
async def on_message(client: Client, message: Message):
    try:
        if message.from_user:
            user_id = message.from_user.id
            username = message.from_user.username

            if is_user_blacklisted(user_id) or (username and is_user_blacklisted(username)):
                return
        elif message.sender_chat:
            user_id = message.sender_chat.id
            if is_user_blacklisted(user_id):
                return

        channels = get_channels()
        current_channel = message.chat.id

        if any(channel[1] == current_channel for channel in channels):
            keywords = [keyword[1] for keyword in get_keywords()]

            for keyword in keywords:
                text_to_check = message.text or message.caption
                if text_to_check and keyword.lower() in text_to_check.lower():
                    user_channel_info = await get_channel_info(client, user_channel)
                    if user_channel_info:
                        user_channel_id = user_channel_info["chat"].id

                        channel_type = get_channel_type(current_channel)
                        from_chat_id = None
                        if channel_type == "public":
                            from_chat_id = f"@{message.chat.username}"
                        elif channel_type == "private":
                            from_chat_id = message.chat.id

                        message_link = generate_message_link(current_channel, message.id, channel_type,
                                                             message.chat.username)

                        if message.media:
                            caption_with_link = f"{message.caption}\n\n[Ссылка на сообщение]({message_link})"
                            await client.copy_message(chat_id=user_channel_id,
                                                      from_chat_id=from_chat_id,
                                                      message_id=message.id,
                                                      caption=caption_with_link)
                        else:
                            text_with_link = f"{message.text}\n\n[Ссылка на сообщение]({message_link})"
                            await client.send_message(chat_id=user_channel_id,
                                                      text=text_with_link)
                    else:
                        logging.error("Не удалось получить информацию о канале user_channel.")
                    break

    except Exception as error:
        logging.error(f"Ошибка при обработке сообщения: {error}")


# Запуск клиентов и обработчиков
if __name__ == "__main__":
    try:
        bot.start()
        app.start()
        idle()
    except Exception as e:
        logging.error(f"Ошибка при запуске клиентов: {e}")
    finally:
        bot.stop()
        app.stop()
        conn.close()
