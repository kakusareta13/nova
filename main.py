import asyncio
import os
import sys
from loguru import logger
from handlers import *
from config import (BOT_TOKEN, conn, API_ID, API_HASH, user_clients, scheduler, cleanup_processed_callbacks, init_bot)
from utils.database import create_table, delete_table
from telethon import TelegramClient
from telethon.sessions import StringSession

# Настройка loguru для красивого отображения логов
logger.remove()  # Удаляем стандартный обработчик

# Добавляем красивый форматированный лог
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    colorize=True
)

# Для Render используем stdout
logger.add(
    sys.stdout,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
)

# Функция для загрузки сессий из базы данных при запуске бота
async def load_sessions():
    cursor = conn.cursor()
    try:
        # Получаем все сессии из базы данных
        sessions = cursor.execute("SELECT user_id, session_string FROM sessions").fetchall()
        logger.info(f"Загружаю {len(sessions)} сессий из базы данных")
        
        # Создаем директорию для хранения файлов сессий, если её нет
        os.makedirs(".sessions", exist_ok=True)
        
        for user_id, session_string in sessions:
            try:
                # Инициализируем клиент с StringSession
                client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
                await client.connect()
                
                # Проверяем авторизацию
                if await client.is_user_authorized():
                    logger.info(f"Сессия для пользователя {user_id} успешно загружена")
                else:
                    logger.warning(f"Сессия для пользователя {user_id} не авторизована")
                
                # Отключаем клиент
                await client.disconnect()
            except Exception as e:
                logger.error(f"Ошибка при загрузке сессии для пользователя {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке сессий: {e}")
    finally:
        cursor.close()

async def setup_scheduler():
    """Настройка и запуск планировщика после старта бота"""
    scheduler.start()
    scheduler.add_job(
        cleanup_processed_callbacks,
        "interval",
        hours=1,  # Очищаем каждый час
        id="cleanup_callbacks"
    )
    logger.info("📅 Планировщик запущен с задачей очистки callback'ов")

async def main():
    """Главная асинхронная функция бота"""
    logger.info("🤖 Инициализация бота...")
    
    # Инициализируем бота
    from config import bot as bot_instance
    if bot_instance is None:
        bot = await init_bot()
        # Обновляем глобальную переменную в config
        import config
        config.bot = bot
    else:
        bot = bot_instance
    
    # Создаем таблицы в базе данных
    create_table()
    delete_table()
    
    logger.info("📱 Запуск бота...")
    
    # Запускаем бота
    await bot.start(bot_token=BOT_TOKEN)
    
    # Загружаем сессии при запуске бота
    await load_sessions()
    
    # Запускаем планировщик после старта бота
    await setup_scheduler()
    
    # Выводим сообщение о запуске
    logger.info("🚀 Бот успешно запущен!")
    
    # Запускаем бота в режиме ожидания сообщений
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        # Запускаем главную асинхронную функцию
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        # Закрываем соединение с базой данных при выходе
        delete_table()
        conn.close()
        logger.info("🔌 Соединение с базой данных закрыто")
