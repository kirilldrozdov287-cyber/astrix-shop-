

import os
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    PreCheckoutQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

# Настройки первого товара в Railway
PRODUCT_NAME = os.getenv(
    "PRODUCT_NAME", "Первый персонаж Astrix"
)
PRODUCT_DESCRIPTION = os.getenv(
    "PRODUCT_DESCRIPTION",
    "Коллекционный персонаж Astrix Shop"
)
PRODUCT_FILE_ID = os.getenv("PRODUCT_FILE_ID", "")
PRODUCT_TYPE = os.getenv("PRODUCT_TYPE", "photo")


def product_price():
    try:
        return int(os.getenv("PRODUCT_PRICE", "0"))
    except ValueError:
        return 0


def product_ready():
    return (
        bool(PRODUCT_FILE_ID)
        and product_price() > 0
        and PRODUCT_TYPE in ("photo", "document")
    )


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🛍 Каталог персонажей",
            callback_data="catalog"
        )],
        [InlineKeyboardButton(
            text="🎁 Мои покупки",
            callback_data="purchases"
        )],
        [InlineKeyboardButton(
            text="💎 NFT-персонажи",
            callback_data="nft"
        )],
        [InlineKeyboardButton(
            text="ℹ️ О магазине",
            callback_data="about"
        )],
    ])


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "✨ ASTRIX SHOP ✨\n\n"
        "Добро пожаловать в волшебный магазин!\n\n"
        "💎 Эксклюзивные персонажи\n"
        "🦋 Магические трансформации\n"
        "👑 Лимитированные коллекции\n"
        "⭐ Оплата через Telegram Stars\n\n"
        "Выбери раздел:",
        reply_markup=main_menu()
    )


@dp.callback_query(F.data == "catalog")
async def catalog(callback: CallbackQuery):
    if not product_ready():
        await callback.message.answer(
            "🛍 КАТАЛОГ ASTRIX SHOP\n\n"
            "✨ Первая коллекция готовится!\n"
            "Скоро здесь появятся персонажи."
        )
    else:
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"⭐ Купить за {product_price()} Stars",
                callback_data="buy"
            )],
            [InlineKeyboardButton(
                text="🏠 Главное меню",
                callback_data="home"
            )]
        ])

        caption = (
            f"✨ {PRODUCT_NAME}\n\n"
            f"{PRODUCT_DESCRIPTION}\n\n"
            f"⭐ Цена: {product_price()} Stars"
        )

        if PRODUCT_TYPE == "photo":
            await callback.message.answer_photo(
                PRODUCT_FILE_ID,
                caption=caption,
                reply_markup=buttons
            )
        else:
            await callback.message.answer_document(
                PRODUCT_FILE_ID,
                caption=caption,
                reply_markup=buttons
            )

    await callback.answer()


@dp.callback_query(F.data == "buy")
async def buy(callback: CallbackQuery):
    if not product_ready():
        await callback.answer(
            "Товар пока недоступен.",
            show_alert=True
        )
        return

    await callback.message.answer_invoice(
        title=PRODUCT_NAME[:32],
        description=PRODUCT_DESCRIPTION[:255],
        payload="astrix_product_001",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=PRODUCT_NAME[:32],
                amount=product_price()
            )
        ],
        provider_token=""
    )

    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    valid = (
        query.invoice_payload == "astrix_product_001"
        and query.currency == "XTR"
        and query.total_amount == product_price()
        and product_ready()
    )

    await query.answer(
        ok=valid,
        error_message=(
            None if valid
            else "Товар временно недоступен."
        )
    )


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment

    if payment.invoice_payload != "astrix_product_001":
        logging.warning("Неизвестный платёж")
        return

    await message.answer(
        "✅ Оплата прошла успешно!\n"
        "🎁 Выдаём твоего персонажа."
    )

    if PRODUCT_TYPE == "photo":
        await message.answer_photo(
            PRODUCT_FILE_ID,
            caption=f"💎 Твоя покупка: {PRODUCT_NAME}"
        )
    else:
        await message.answer_document(
            PRODUCT_FILE_ID,
            caption=f"💎 Твоя покупка: {PRODUCT_NAME}"
        )


@dp.callback_query(F.data == "purchases")
async def purchases(callback: CallbackQuery):
    await callback.answer(
        "Личная коллекция появится после "
        "подключения базы покупок.",
        show_alert=True
    )


@dp.callback_query(F.data == "nft")
async def nft(callback: CallbackQuery):
    await callback.answer(
        "💎 NFT-персонажи появятся позже!",
        show_alert=True
    )


@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    await callback.message.answer(
        "✨ ASTRIX SHOP\n\n"
        "Магазин цифровых персонажей "
        "и коллекционных образов.\n\n"
        "⭐ Оплата: Telegram Stars\n"
        "💎 NFT: в разработке"
    )
    await callback.answer()


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=main_menu()
    )
    await callback.answer()


async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
