
import os
import json
import hmac
import hashlib
import time
import asyncio
import logging

from urllib.parse import parse_qsl
from pathlib import Path

from aiohttp import web, ClientSession
from aiogram import Bot

import bot as astrix_bot


# НАСТРОЙКИ
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.getenv("DATABASE_PATH", "/data/astrix.db")
PORT = int(os.getenv("PORT", "8080"))

BASE_DIR = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)


# БАЗА ДАННЫХ
def database():
    return astrix_bot.database()


# ПРОВЕРКА ПОЛЬЗОВАТЕЛЯ TELEGRAM
def verify_telegram(init_data):
    if not init_data:
        raise web.HTTPUnauthorized(
            text="Откройте магазин через Telegram"
        )

    try:
        values = dict(
            parse_qsl(init_data, keep_blank_values=True)
        )

        received_hash = values.pop("hash", None)

        if not received_hash:
            raise ValueError("Отсутствует подпись")

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(values.items())
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret,
            check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            raise ValueError("Неверная подпись")

        auth_date = int(values["auth_date"])

        if abs(time.time() - auth_date) > 86400:
            raise ValueError("Авторизация устарела")

        user = json.loads(values["user"])
        user_id = int(user["id"])

        if user_id <= 0:
            raise ValueError("Неверный пользователь")

        return user_id

    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError
    ):
        raise web.HTTPUnauthorized(
            text="Ошибка авторизации Telegram"
        )


def authorized_user(request):
    return verify_telegram(
        request.headers.get(
            "X-Telegram-Init-Data", ""
        )
    )


# ГЛАВНАЯ СТРАНИЦА
async def index(request):
    path = BASE_DIR / "index.html"

    if not path.is_file():
        raise web.HTTPNotFound(
            text="Интерфейс магазина не найден"
        )

    return web.FileResponse(path)


# ПРОВЕРКА СЕРВЕРА
async def health(request):
    return web.json_response({
        "status": "ok",
        "app": "Astrix Shop"
    })


# КАТАЛОГ
async def products(request):
    authorized_user(request)

    with database() as con:
        rows = con.execute("""
            SELECT sku, name, description, price
            FROM products
            WHERE preview_id IS NOT NULL
              AND original_id IS NOT NULL
        """).fetchall()

    result = []

    for sku, name, description, price in rows:
        result.append({
            "sku": sku,
            "name": name,
            "description": description,
            "price": price,
            "preview_url": "/api/preview/" + sku
        })

    return web.json_response({
        "products": result
    })


# ЗАПРОСЫ К TELEGRAM
async def telegram_api(method, payload):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:
        async with ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=20
            ) as response:
                data = await response.json()

        if not data.get("ok"):
            logging.error(
                "Telegram API error: %s",
                data.get("description", "Unknown")
            )
            raise web.HTTPBadGateway(
                text="Ошибка Telegram API"
            )

        return data["result"]

    except (OSError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(
            text="Telegram временно недоступен"
        )


# ПРЕВЬЮ ПЕРСОНАЖА
async def preview(request):
    authorized_user(request)

    sku = request.match_info["sku"]

    with database() as con:
        row = con.execute("""
            SELECT preview_id
            FROM products
            WHERE sku = ?
              AND original_id IS NOT NULL
        """, (sku,)).fetchone()

    if not row or not row[0]:
        raise web.HTTPNotFound(
            text="Изображение не найдено"
        )

    file_info = await telegram_api(
        "getFile",
        {"file_id": row[0]}
    )

    file_path = file_info["file_path"]

    file_url = (
        "https://api.telegram.org/file/bot"
        + BOT_TOKEN
        + "/"
        + file_path
    )

    try:
        async with ClientSession() as session:
            async with session.get(
                file_url,
                timeout=30
            ) as response:
                if response.status != 200:
                    raise web.HTTPBadGateway(
                        text="Не удалось получить превью"
                    )

                content = await response.read()

                content_type = response.headers.get(
                    "Content-Type",
                    "image/jpeg"
                )

    except (OSError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(
            text="Ошибка загрузки изображения"
        )

    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    return web.Response(
        body=content,
        content_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300"
        }
    )


# КОЛЛЕКЦИЯ ПОЛЬЗОВАТЕЛЯ
async def purchases(request):
    user_id = authorized_user(request)

    with database() as con:
        rows = con.execute("""
            SELECT DISTINCT
                p.sku,
                p.name,
                p.description
            FROM purchases AS b
            JOIN products AS p
              ON p.sku = b.sku
            WHERE b.user_id = ?
        """, (user_id,)).fetchall()

    result = []

    for sku, name, description in rows:
        result.append({
            "sku": sku,
            "name": name,
            "description": description
        })

    return web.json_response({
        "purchases": result
    })


# СОЗДАНИЕ СЧЁТА TELEGRAM STARS
async def invoice(request):
    authorized_user(request)

    try:
        body = await request.json()
        sku = body["sku"]

        if not isinstance(sku, str):
            raise ValueError("Неверный товар")

    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError
    ):
        raise web.HTTPBadRequest(
            text="Неверный запрос"
        )

    with database() as con:
        row = con.execute("""
            SELECT name, description, price
            FROM products
            WHERE sku = ?
              AND preview_id IS NOT NULL
              AND original_id IS NOT NULL
        """, (sku,)).fetchone()

    if not row:
        raise web.HTTPNotFound(
            text="Товар недоступен"
        )

    name, description, price = row

    if not 1 <= price <= 100000:
        raise web.HTTPBadRequest(
            text="Неверная цена"
        )

    # Совместимо с обработчиком оплаты в bot.py
    payload = f"astrix:{sku}:{price}"

    invoice_url = await telegram_api(
        "createInvoiceLink",
        {
            "title": name[:32],
            "description": description[:255],
            "payload": payload,
            "currency": "XTR",
            "prices": [
                {
                    "label": name[:32],
                    "amount": price
                }
            ]
        }
    )

    return web.json_response({
        "invoice_url": invoice_url
    })


# МАРШРУТЫ ВЕБ-ПРИЛОЖЕНИЯ
app = web.Application(
    client_max_size=1024 * 1024
)

app.router.add_get("/", index)
app.router.add_get("/health", health)
app.router.add_get("/api/products", products)
app.router.add_get(
    "/api/preview/{sku}",
    preview
)
app.router.add_get(
    "/api/purchases",
    purchases
)
app.router.add_post(
    "/api/invoice",
    invoice
)


# ОДНОВРЕМЕННЫЙ ЗАПУСК МАГАЗИНА И БОТА
async def main():
    with database():
        pass

    telegram_bot = Bot(token=BOT_TOKEN)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT
    )

    try:
        await site.start()

        logging.info(
            "Astrix Shop web server started on port %s",
            PORT
        )

        await astrix_bot.dp.start_polling(
            telegram_bot
        )

    finally:
        await runner.cleanup()
        await telegram_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

