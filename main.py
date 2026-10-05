import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, Message

import db
from config import BOT_TOKEN, MESSAGE_TTL_DAYS
from handlers import admin, business, user


class UserMiddleware(BaseMiddleware):
    """Обновляет пользователя в базе и не пускает забаненных."""

    async def __call__(self, handler, event, data):
        u = event.from_user
        if u and not u.is_bot:
            db.touch_user(u)
            if db.is_banned(u.id) and not db.is_admin(u.id):
                if isinstance(event, CallbackQuery):
                    await event.answer("🚫 Вы заблокированы", show_alert=True)
                elif isinstance(event, Message) and event.chat.type == "private":
                    await event.answer("🚫 Вы заблокированы в этом боте.")
                return None
        return await handler(event, data)


async def cleanup_loop():
    while True:
        try:
            n = db.cleanup_messages(MESSAGE_TTL_DAYS)
            if n:
                logging.info("cache cleanup: removed %s messages", n)
        except Exception:
            logging.exception("cleanup failed")
        await asyncio.sleep(6 * 3600)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан (файл .env или переменная окружения)")
    db.init()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())
    dp.include_routers(admin.router, business.router, user.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Меню и настройки"),
        BotCommand(command="help", description="Как подключить"),
    ])
    asyncio.create_task(cleanup_loop())
    me = await bot.get_me()
    logging.info("Бот @%s запущен", me.username)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
