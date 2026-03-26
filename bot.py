import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from decouple import config
from loguru import logger
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ============ КОНФИГУРАЦИЯ ============
API_ID: int = int(config("API_ID"))
API_HASH: str = config("API_HASH")
BOT_TOKEN: str = config("BOT_TOKEN")
ADMIN_ID_LIST: List[int] = list(map(int, map(str.strip, config("ADMIN_ID_LIST").split(","))))

# Инициализация бота
bot = TelegramClient("bot", API_ID, API_HASH)
conn = sqlite3.connect("sessions.db", timeout=30.0)
scheduler = AsyncIOScheduler()

# Глобальные словари для хранения состояний
phone_waiting: Dict[int, bool] = {}
code_waiting: Dict[int, str] = {}
password_waiting: Dict[int, dict] = {}
user_states: Dict[int, str] = {}
broadcast_all_text: Dict[int, str] = {}
broadcast_all_state: Dict[int, dict] = {}
broadcast_solo_state: Dict[int, dict] = {}
broadcast_all_state_account: Dict[int, dict] = {}
user_sessions: Dict[int, dict] = {}
user_sessions_deleting: Dict[int, dict] = {}
user_sessions_phone: Dict[Tuple[int, int], dict] = {}
user_clients: Dict[int, TelegramClient] = {}
processed_callbacks: Dict[str, bool] = {}

# ============ БАЗА ДАННЫХ ============
def create_table():
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            group_username TEXT,
            group_name TEXT,
            message_text TEXT,
            interval_seconds INTEGER,
            is_active BOOLEAN DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES sessions(user_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            group_username TEXT,
            message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cursor.close()

def delete_table():
    cursor = conn.cursor()
    cursor.execute('DELETE FROM sessions WHERE 1=0')
    conn.commit()
    cursor.close()

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============
async def safe_callback_answer(event, text: str = ""):
    try:
        await event.answer(text)
    except Exception as e:
        logger.debug(f"Ошибка при ответе на callback: {e}")

def cleanup_processed_callbacks():
    processed_callbacks.clear()
    logger.debug("Очищены обработанные callback'ы")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_ID_LIST

# ============ ГЛАВНОЕ МЕНЮ ============
async def main_menu(event, user_id: int):
    buttons = [
        [events.InlineQueryResultButton("👤 Добавить аккаунт", b"add_account")],
        [events.InlineQueryResultButton("📱 Мои аккаунты", b"my_accounts")],
        [events.InlineQueryResultButton("➕ Добавить группу", b"add_group")],
        [events.InlineQueryResultButton("📊 История рассылки", b"history")]
    ]
    
    if is_admin(user_id):
        buttons.append([events.InlineQueryResultButton("👑 Админ панель", b"admin_panel")])
    
    await event.respond("🔰 **Главное меню**", buttons=buttons)

# ============ ОБРАБОТЧИКИ КОМАНД ============
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    
    if not is_admin(user_id):
        await event.respond("❌ У вас нет доступа к этому боту!")
        return
    
    await main_menu(event, user_id)

@bot.on(events.NewMessage)
async def message_handler(event):
    user_id = event.sender_id
    
    if not is_admin(user_id):
        return
    
    # Обработка ожидания кода
    if user_id in code_waiting:
        code = event.raw_text
        await process_code(event, user_id, code)
        return
    
    # Обработка ожидания пароля
    if user_id in password_waiting:
        password = event.raw_text
        await process_password(event, user_id, password)
        return
    
    # Обработка ожидания текста рассылки
    if user_id in broadcast_all_text:
        text = event.raw_text
        await process_broadcast_text(event, user_id, text)
        return
    
    # Обработка ожидания интервала
    if user_id in user_states and user_states[user_id] == "waiting_interval":
        interval = event.raw_text
        await process_interval(event, user_id, interval)
        return

# ============ ОБРАБОТЧИКИ CALLBACK ============
@bot.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    data = event.data.decode()
    
    if not is_admin(user_id):
        await event.answer("❌ Нет доступа", alert=True)
        return
    
    if data == "add_account":
        await add_account_handler(event, user_id)
    elif data == "my_accounts":
        await my_accounts_handler(event, user_id)
    elif data == "add_group":
        await add_group_handler(event, user_id)
    elif data == "history":
        await history_handler(event, user_id)
    elif data.startswith("account_"):
        account_id = int(data.split("_")[1])
        await account_detail_handler(event, user_id, account_id)
    elif data.startswith("group_"):
        group_id = int(data.split("_")[1])
        await group_detail_handler(event, user_id, group_id)
    elif data.startswith("delete_account_"):
        account_id = int(data.split("_")[2])
        await confirm_delete_account(event, user_id, account_id)
    elif data.startswith("confirm_delete_"):
        account_id = int(data.split("_")[2])
        await delete_account(event, user_id, account_id)
    elif data.startswith("start_broadcast_"):
        account_id = int(data.split("_")[2])
        await start_broadcast_handler(event, user_id, account_id)
    elif data == "stop_broadcast":
        await stop_broadcast_handler(event, user_id)

# ============ ОСНОВНЫЕ ФУНКЦИИ ============
async def add_account_handler(event, user_id):
    await event.answer()
    phone_waiting[user_id] = True
    await event.edit("📞 **Введите номер телефона**\n\nВ формате: +79001234567")
    
    @bot.on(events.NewMessage)
    async def get_phone(e):
        if e.sender_id == user_id and user_id in phone_waiting:
            phone = e.raw_text
            del phone_waiting[user_id]
            await add_account_step2(e, user_id, phone)

async def add_account_step2(event, user_id, phone):
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        # Отправляем код
        await client.send_code_request(phone)
        code_waiting[user_id] = phone
        
        await event.respond(f"📱 **Код подтверждения**\n\nОтправлен на номер {phone}\n\nВведите код из Telegram:")
    except Exception as e:
        logger.error(f"Ошибка при отправке кода: {e}")
        await event.respond("❌ Ошибка при отправке кода. Проверьте номер телефона.")

async def process_code(event, user_id, code):
    phone = code_waiting.pop(user_id, None)
    if not phone:
        return
    
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        await client.sign_in(phone, code)
        
        # Получаем session string
        session_string = client.session.save()
        
        # Сохраняем в базу
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO sessions (user_id, session_string, phone) VALUES (?, ?, ?)",
            (user_id, session_string, phone)
        )
        conn.commit()
        cursor.close()
        
        await client.disconnect()
        
        await event.respond("✅ **Аккаунт успешно добавлен!**")
        await main_menu(event, user_id)
        
    except Exception as e:
        if "password" in str(e).lower():
            password_waiting[user_id] = {"phone": phone, "client": client}
            await event.respond("🔐 **Введите пароль**\n\nУ вас включена двухфакторная аутентификация:")
        else:
            logger.error(f"Ошибка авторизации: {e}")
            await event.respond(f"❌ Ошибка: {str(e)}")
            await main_menu(event, user_id)

async def process_password(event, user_id, password):
    data = password_waiting.pop(user_id, None)
    if not data:
        return
    
    phone = data["phone"]
    client = data["client"]
    
    try:
        await client.sign_in(password=password)
        
        session_string = client.session.save()
        
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO sessions (user_id, session_string, phone) VALUES (?, ?, ?)",
            (user_id, session_string, phone)
        )
        conn.commit()
        cursor.close()
        
        await client.disconnect()
        
        await event.respond("✅ **Аккаунт успешно добавлен!**")
        await main_menu(event, user_id)
        
    except Exception as e:
        logger.error(f"Ошибка пароля: {e}")
        await event.respond(f"❌ Неверный пароль. Попробуйте снова.")
        await main_menu(event, user_id)

async def my_accounts_handler(event, user_id):
    await event.answer()
    
    cursor = conn.cursor()
    accounts = cursor.execute("SELECT user_id, phone FROM sessions").fetchall()
    cursor.close()
    
    if not accounts:
        await event.edit("📭 **У вас нет добавленных аккаунтов**\n\nДобавьте аккаунт через главное меню.")
        return
    
    buttons = []
    for acc_user_id, phone in accounts:
        buttons.append([events.InlineQueryResultButton(f"👤 {phone}", f"account_{acc_user_id}")])
    
    buttons.append([events.InlineQueryResultButton("🔙 Назад", b"back_to_menu")])
    
    await event.edit("📱 **Ваши аккаунты**", buttons=buttons)

async def account_detail_handler(event, user_id, account_id):
    await event.answer()
    
    cursor = conn.cursor()
    account = cursor.execute("SELECT user_id, phone FROM sessions WHERE user_id = ?", (account_id,)).fetchone()
    
    groups = cursor.execute(
        "SELECT id, group_username, group_name, is_active FROM groups WHERE user_id = ?",
        (account_id,)
    ).fetchall()
    cursor.close()
    
    if not account:
        await event.edit("❌ Аккаунт не найден")
        return
    
    text = f"**👤 Аккаунт:** {account[1]}\n"
    text += f"**📊 Групп:** {len(groups)}\n"
    
    buttons = [
        [events.InlineQueryResultButton("➕ Добавить все группы", f"add_all_groups_{account_id}")],
        [events.InlineQueryResultButton("📋 Список групп", f"groups_list_{account_id}")],
        [events.InlineQueryResultButton("🚀 Начать рассылку во все группы", f"start_broadcast_{account_id}")],
        [events.InlineQueryResultButton("❌ Удалить аккаунт", f"delete_account_{account_id}")],
        [events.InlineQueryResultButton("🔙 Назад", b"my_accounts")]
    ]
    
    await event.edit(text, buttons=buttons)

async def add_group_handler(event, user_id):
    await event.answer()
    user_states[user_id] = "waiting_group_username"
    await event.edit("🔤 **Введите username группы**\n\nПример: @mygroup")

async def history_handler(event, user_id):
    await event.answer()
    
    cursor = conn.cursor()
    history = cursor.execute(
        "SELECT group_username, message, sent_at FROM history ORDER BY sent_at DESC LIMIT 10"
    ).fetchall()
    cursor.close()
    
    if not history:
        await event.edit("📭 **История рассылок пуста**")
        return
    
    text = "📊 **Последние 10 рассылок:**\n\n"
    for group, message, sent_at in history:
        text += f"**📍 Группа:** {group}\n"
        text += f"**⏰ Время:** {sent_at}\n"
        text += f"**💬 Сообщение:** {message[:50]}...\n\n"
    
    await event.edit(text, buttons=[[events.InlineQueryResultButton("🔙 Назад", b"back_to_menu")]])

async def group_detail_handler(event, user_id, group_id):
    await event.answer()
    
    cursor = conn.cursor()
    group = cursor.execute(
        "SELECT id, group_username, group_name, message_text, interval_seconds, is_active FROM groups WHERE id = ?",
        (group_id,)
    ).fetchone()
    cursor.close()
    
    if not group:
        await event.edit("❌ Группа не найдена")
        return
    
    status = "🟢 Активна" if group[5] else "🔴 Неактивна"
    
    text = f"**📋 Группа:** {group[2] or group[1]}\n"
    text += f"**👥 Username:** {group[1]}\n"
    text += f"**📝 Текст:** {group[3] or 'Не установлен'}\n"
    text += f"**⏰ Интервал:** {group[4] or 'Не установлен'} сек\n"
    text += f"**📊 Статус:** {status}\n"
    
    buttons = [
        [events.InlineQueryResultButton("📝 Текст и интервал рассылки", f"edit_group_{group_id}")],
        [events.InlineQueryResultButton("▶️ Начать/возобновить рассылку", f"start_group_broadcast_{group_id}")],
        [events.InlineQueryResultButton("⏹️ Остановить рассылку", f"stop_group_broadcast_{group_id}")],
        [events.InlineQueryResultButton("❌ Удалить группу", f"delete_group_{group_id}")],
        [events.InlineQueryResultButton("🔙 Назад", f"account_{user_id}")]
    ]
    
    await event.edit(text, buttons=buttons)

async def confirm_delete_account(event, user_id, account_id):
    await event.answer()
    buttons = [
        [events.InlineQueryResultButton("✅ Да, удалить", f"confirm_delete_{account_id}")],
        [events.InlineQueryResultButton("❌ Нет, отмена", f"account_{account_id}")]
    ]
    await event.edit("⚠️ **Вы уверены, что хотите удалить аккаунт?**\n\nЭто действие нельзя отменить.", buttons=buttons)

async def delete_account(event, user_id, account_id):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ?", (account_id,))
    cursor.execute("DELETE FROM groups WHERE user_id = ?", (account_id,))
    conn.commit()
    cursor.close()
    
    await event.answer("✅ Аккаунт удален")
    await my_accounts_handler(event, user_id)

async def start_broadcast_handler(event, user_id, account_id):
    await event.answer()
    broadcast_all_text[user_id] = account_id
    await event.edit("📝 **Введите текст для массовой рассылки**")

async def process_broadcast_text(event, user_id, text):
    account_id = broadcast_all_text.pop(user_id, None)
    if not account_id:
        return
    
    broadcast_all_state[user_id] = {"account_id": account_id, "text": text}
    user_states[user_id] = "waiting_interval"
    
    await event.respond("⏰ **Введите интервал между сообщениями**\n\nВ секундах (например: 60)")

async def process_interval(event, user_id, interval):
    try:
        interval_seconds = int(interval)
        if interval_seconds < 5:
            await event.respond("❌ Интервал должен быть не менее 5 секунд")
            return
        
        data = broadcast_all_state.pop(user_id, None)
        if not data:
            return
        
        account_id = data["account_id"]
        text = data["text"]
        
        cursor = conn.cursor()
        groups = cursor.execute(
            "SELECT id, group_username FROM groups WHERE user_id = ?",
            (account_id,)
        ).fetchall()
        cursor.close()
        
        if not groups:
            await event.respond("❌ Нет групп для рассылки")
            await main_menu(event, user_id)
            return
        
        # Запускаем рассылку
        await event.respond(f"🚀 **Рассылка запущена!**\n\nГрупп: {len(groups)}\nИнтервал: {interval_seconds} сек\n\nНачинаем отправку...")
        
        for group_id, group_username in groups:
            try:
                # Получаем клиент для аккаунта
                cursor = conn.cursor()
                session = cursor.execute("SELECT session_string FROM sessions WHERE user_id = ?", (account_id,)).fetchone()
                cursor.close()
                
                if session:
                    client = TelegramClient(StringSession(session[0]), API_ID, API_HASH)
                    await client.connect()
                    
                    # Отправляем сообщение
                    await client.send_message(group_username, text)
                    
                    # Сохраняем в историю
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO history (user_id, group_username, message) VALUES (?, ?, ?)",
                        (account_id, group_username, text)
                    )
                    conn.commit()
                    cursor.close()
                    
                    await client.disconnect()
                    
                    await asyncio.sleep(interval_seconds)
                    
            except Exception as e:
                logger.error(f"Ошибка отправки в {group_username}: {e}")
        
        await event.respond("✅ **Рассылка завершена!**")
        await main_menu(event, user_id)
        
    except ValueError:
        await event.respond("❌ Введите число (количество секунд)")

async def stop_broadcast_handler(event, user_id):
    await event.answer()
    # Здесь логика остановки рассылки
    await event.edit("⏹️ **Рассылка остановлена**")

# ============ ЗАГРУЗКА СЕССИЙ ============
async def load_sessions():
    cursor = conn.cursor()
    try:
        sessions = cursor.execute("SELECT user_id, session_string FROM sessions").fetchall()
        logger.info(f"Загружаю {len(sessions)} сессий из базы данных")
        
        os.makedirs(".sessions", exist_ok=True)
        
        for user_id, session_string in sessions:
            try:
                client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
                await client.connect()
                
                if await client.is_user_authorized():
                    logger.info(f"Сессия для пользователя {user_id} успешно загружена")
                    user_clients[user_id] = client
                else:
                    logger.warning(f"Сессия для пользователя {user_id} не авторизована")
                    await client.disconnect()
            except Exception as e:
                logger.error(f"Ошибка при загрузке сессии для пользователя {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке сессий: {e}")
    finally:
        cursor.close()

async def setup_scheduler():
    scheduler.start()
    scheduler.add_job(cleanup_processed_callbacks, "interval", hours=1, id="cleanup_callbacks")
    logger.info("📅 Планировщик запущен")

# ============ ЗАПУСК БОТА ============
async def main():
    logger.info("🤖 Инициализация бота...")
    
    create_table()
    
    logger.info("📱 Запуск бота...")
    await bot.start(bot_token=BOT_TOKEN)
    
    await load_sessions()
    await setup_scheduler()
    
    logger.info("🚀 Бот успешно запущен!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        conn.close()
        logger.info("🔌 Соединение с базой данных закрыто")
