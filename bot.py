
import os
import asyncio
import logging
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
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

# Настройки
DB_PATH = os.getenv("DATABASE_PATH", "/tmp/astrix.db")
ADMIN_ID = os.getenv("ADMIN_ID", "")
SKU = "nika_star_edition"


def database():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB_PATH)

    con.execute("""
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            price INTEGER NOT NULL,
            preview_id TEXT,
            original_id TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            charge_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            sku TEXT NOT NULL,
            original_id TEXT NOT NULL
        )
    """)

    con.execute("""
        INSERT OR IGNORE INTO products
        (sku, name, description, price)
        VALUES (?, ?, ?, ?)
    """, (
        SKU,
        "Ника — Star Edition",
        "Эксклюзивная коллекционная иллюстрация "
        "персонажа Ники из Astrix Shop.",
        150
    ))

    con.commit()
    return con


def is_admin(user):
    return (
        user is not None
        and ADMIN_ID.isdigit()
        and user.id == int(ADMIN_ID)
    )


def get_product():
    with database() as con:
        return con.execute("""
            SELECT sku, name, description, price,
                   preview_id, original_id
            FROM products WHERE sku = ?
        """, (SKU,)).fetchone()


def keyboard(*buttons):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=text,
                callback_data=data
            )]
            for text, data in buttons
        ]
    )


def main_menu():
    return keyboard(
        ("🛍 Каталог персонажей", "catalog"),
        ("🎁 Мои покупки", "purchases"),
        ("✨ О магазине", "about"),
    )


# Получение Telegram ID
@dp.message(Command("myid"))
async def my_id(message: Message):
    await message.answer(
        f"Твой Telegram ID: {message.from_user.id}"
    )


# Главное меню
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "✨ ASTRIX SHOP ✨\n\n"
        "Добро пожаловать в магазин "
        "коллекционных персонажей!\n\n"
        "🛍 Эксклюзивные образы\n"
        "⭐ Оплата Telegram Stars\n"
        "🎁 Цифровые коллекции\n\n"
        "Выбери раздел:",
        reply_markup=main_menu()
    )


# Инструкция администратору
@dp.message(Command("addnika"))
async def add_nika(message: Message):
    if not is_admin(message.from_user):
        await message.answer(
            "⛔ Только для администратора."
        )
        return

    await message.answer(
        "✨ ДОБАВЛЕНИЕ НИКИ\n\n"
        "1. Отправь превью как фото "
        "с подписью:\n"
        "#nika_preview\n\n"
        "2. Отправь оригинал как файл "
        "(документ) с подписью:\n"
        "#nika_original\n\n"
        "После загрузки обоих файлов "
        "Ника появится в каталоге."
    )


# Загрузка изображений
@dp.message(F.photo | F.document)
async def upload_image(message: Message):
    if not is_admin(message.from_user):
        return

    caption = (message.caption or "").strip().lower()

    if caption == "#nika_preview":
        if not message.photo:
            await message.answer(
                "Отправь превью именно как фото."
            )
            return

        file_id = message.photo[-1].file_id

        with database() as con:
            con.execute("""
                UPDATE products
                SET preview_id = ?
                WHERE sku = ?
            """, (file_id, SKU))

        await message.answer(
            "✅ Превью Ники сохранено!"
        )
        return

    if caption == "#nika_original":
        if not message.document:
            await message.answer(
                "Отправь оригинал как файл."
            )
            return

        mime = message.document.mime_type or ""

        if not mime.startswith("image/"):
            await message.answer(
                "Нужен файл изображения."
            )
            return

        file_id = message.document.file_id

        with database() as con:
            con.execute("""
                UPDATE products
                SET original_id = ?
                WHERE sku = ?
            """, (file_id, SKU))

        await message.answer(
            "✅ Оригинал Ники сохранён!\n"
            "Проверь раздел «Каталог»."
        )


# Каталог
@dp.callback_query(F.data == "catalog")
async def catalog(callback: CallbackQuery):
    sku, name, description, price, preview, original = (
        get_product()
    )

    if not preview or not original:
        await callback.message.answer(
            "🛍 ASTRIX SHOP\n\n"
            "Ника — Star Edition\n"
            "⭐ 150 Stars\n\n"
            "Товар готовится к публикации."
        )
        await callback.answer()
        return

    buttons = keyboard(
        (f"⭐ Купить за {price} Stars", "buy_nika"),
        ("🏠 Главное меню", "home")
    )

    await callback.message.answer_photo(
        preview,
        caption=(
            f"✨ {name}\n\n"
            f"{description}\n\n"
            f"⭐ Цена: {price} Stars\n"
            "🎁 Оригинал после оплаты"
        ),
        reply_markup=buttons
    )

    await callback.answer()


# Покупка
@dp.callback_query(F.data == "buy_nika")
async def buy_nika(callback: CallbackQuery):
    sku, name, description, price, preview, original = (
        get_product()
    )

    if not preview or not original:
        await callback.answer(
            "Товар пока недоступен.",
            show_alert=True
        )
        return

    await callback.message.answer_invoice(
        title=name[:32],
        description=description[:255],
        payload=f"astrix:{sku}:{price}",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=name[:32],
                amount=price
            )
        ],
        provider_token=""
    )

    await callback.answer()


# Проверка платежа
@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    sku, name, description, price, preview, original = (
        get_product()
    )

    valid = (
        bool(preview)
        and bool(original)
        and query.invoice_payload == f"astrix:{sku}:{price}"
        and query.currency == "XTR"
        and query.total_amount == price
    )

    await query.answer(
        ok=valid,
        error_message=(
            None if valid
            else "Товар временно недоступен."
        )
    )


# Успешная оплата
@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment

    sku, name, description, price, preview, original = (
        get_product()
    )

    if (
        payment.invoice_payload != f"astrix:{sku}:{price}"
        or payment.currency != "XTR"
        or payment.total_amount != price
    ):
        logging.error(
            "Неизвестный платёж: %s",
            payment.telegram_payment_charge_id
        )
        await message.answer(
            "Платёж требует проверки. "
            "Обратись к администратору."
        )
        return

    if not original:
        await message.answer(
            "Оплата получена, но файл временно "
            "недоступен. Обратись к администратору."
        )
        return

    with database() as con:
        con.execute("""
            INSERT OR IGNORE INTO purchases
            (charge_id, user_id, sku, original_id)
            VALUES (?, ?, ?, ?)
        """, (
            payment.telegram_payment_charge_id,
            message.from_user.id,
            sku,
            original
        ))

    await message.answer_document(
        original,
        caption=(
            "🎁 Спасибо за покупку!\n\n"
            f"✨ {name}\n"
            "Твой персонаж из Astrix Shop."
        )
    )


# Мои покупки
@dp.callback_query(F.data == "purchases")
async def purchases(callback: CallbackQuery):
    with database() as con:
        row = con.execute("""
            SELECT original_id
            FROM purchases
            WHERE user_id = ? AND sku = ?
            ORDER BY rowid DESC
            LIMIT 1
        """, (
            callback.from_user.id,
            SKU
        )).fetchone()

    if row:
        await callback.message.answer_document(
            row[0],
            caption="🎁 Ника — Star Edition"
        )
    else:
        await callback.message.answer(
            "🎁 У тебя пока нет покупок."
        )

    await callback.answer()


# О магазине
@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    await callback.message.answer(
        "✨ ASTRIX SHOP\n\n"
        "Магазин цифровых коллекционных "
        "персонажей.\n\n"
        "⭐ Оплата через Telegram Stars\n"
        "🎁 Автоматическая выдача файлов"
    )
    await callback.answer()


# Главное меню
@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=main_menu()
    )
    await callback.answer()


async def main():
    with database():
        pass

    bot = Bot(token=os.environ["BOT_TOKEN"])

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

