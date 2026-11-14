
import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "✨ Добро пожаловать в ASTRIX SHOP!\n\n"
        "💎 Магазин эксклюзивных персонажей\n"
        "⭐ Скоро здесь появятся покупки за Telegram Stars!"
    )

async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
