import asyncio
import os
import sys
from loguru import logger

# ВАЖНО: Сначала импортируем config и database
from config import (BOT_TOKEN, conn, API_ID, API_HASH, user_clients, scheduler, 
                    cleanup_processed_callbacks, init_bot, bot as bot_placeholder)
from utils.database import create_table, delete_table
from telethon import TelegramClient
from telethon.sessions import StringSession

# Настройка логов
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", colorize=True)
logger.add(sys.stdout, level="DEBUG", format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}")

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

async def main():
    logger.info("🤖 Инициализация бота...")
    
    # Инициализируем бота
    bot = await init_bot()
    
    # Устанавливаем бота в глобальную переменную config
    import config
    config.bot = bot
    
    # ТЕПЕРЬ импортируем handlers (после того как bot установлен)
    from handlers import *
    
    create_table()
    delete_table()
    
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
        delete_table()
        conn.close()
        logger.info("🔌 Соединение с базой данных закрыто")
