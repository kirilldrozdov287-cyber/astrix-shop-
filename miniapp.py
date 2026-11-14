
import os
import json
import hmac
import hashlib
import sqlite3
import time
from urllib.parse import parse_qsl

from aiohttp import web, ClientSession

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.getenv("DATABASE_PATH", "/data/astrix.db")
PORT = int(os.getenv("PORT", "8080"))

BASE = os.path.dirname(os.path.abspath(__file__))


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def verify_telegram(init_data):
    if not init_data:
        raise web.HTTPUnauthorized(text="Откройте магазин через Telegram")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)

    if not received_hash:
        raise web.HTTPUnauthorized(text="Нет подписи Telegram")

    check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(values.items())
    )

    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated = hmac.new(
        secret,
        check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise web.HTTPUnauthorized(text="Неверная подпись")

    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise web.HTTPUnauthorized(text="Ошибка авторизации")

    if abs(time.time() - auth_date) > 86400:
        raise web.HTTPUnauthorized(text="Авторизация устарела")

    return user_id


async def index(request):
    path = os.path.join(BASE, "index.html")
    return web.FileResponse(path)


async def health(request):
    return web.json_response({"status": "ok", "app": "Astrix Shop"})


async def products(request):
    verify_telegram(request.headers.get("X-Telegram-Init-Data", ""))

    with db() as con:
        rows = con.execute(
            """
            SELECT sku, name, description, price, preview_id
            FROM products
            WHERE preview_id IS NOT NULL
              AND original_id IS NOT NULL
            """
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["preview_url"] = "/api/preview/" + item["sku"]
        item.pop("preview_id", None)
        result.append(item)

    return web.json_response({"products": result})


async def telegram_api(method, payload):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    async with ClientSession() as session:
        async with session.post(url, json=payload) as response:
            data = await response.json()

    if not data.get("ok"):
        raise web.HTTPBadGateway(text="Ошибка Telegram API")

    return data["result"]


async def preview(request):
    sku = request.match_info["sku"]

    with db() as con:
        row = con.execute(
            "SELECT preview_id FROM products WHERE sku = ?",
            (sku,)
        ).fetchone()

    if not row or not row["preview_id"]:
        raise web.HTTPNotFound()

    file_info = await telegram_api(
        "getFile", {"file_id": row["preview_id"]}
    )

    file_path = file_info["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

    async with ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise web.HTTPBadGateway(text="Не удалось загрузить фото")
            content = await response.read()
            content_type = response.headers.get(
                "Content-Type", "image/jpeg"
            )

    return web.Response(
        body=content,
        content_type=content_type,
        headers={"Cache-Control": "public, max-age=300"}
    )


async def purchases(request):
    user_id = verify_telegram(
        request.headers.get("X-Telegram-Init-Data", "")
    )

    with db() as con:
        rows = con.execute(
            """
            SELECT DISTINCT p.sku, p.name, p.description
            FROM purchases AS b
            JOIN products AS p ON p.sku = b.sku
            WHERE b.user_id = ?
            """,
            (user_id,)
        ).fetchall()

    return web.json_response({
        "purchases": [dict(row) for row in rows]
    })


async def invoice(request):
    user_id = verify_telegram(
        request.headers.get("X-Telegram-Init-Data", "")
    )

    try:
        body = await request.json()
        sku = body["sku"]
    except (ValueError, KeyError, TypeError):
        raise web.HTTPBadRequest(text="Неверный запрос")

    with db() as con:
        product = con.execute(
            """
            SELECT name, description, price
            FROM products
            WHERE sku = ?
              AND preview_id IS NOT NULL
              AND original_id IS NOT NULL
            """,
            (sku,)
        ).fetchone()

    if not product:
        raise web.HTTPNotFound(text="Товар недоступен")

    if not 1 <= product["price"] <= 100000:
        raise web.HTTPBadRequest(text="Неверная цена")

    # Формат payload совместим с обработчиком оплаты в bot.py




payload = f"astrix:{sku}:{product['price']}"
    result = await telegram_api("createInvoiceLink", {
        "title": product["name"][:32],
        "description": product["description"][:255],
        "payload": payload,
        "currency": "XTR",
        "prices": [{
            "label": product["name"][:32],
            "amount": product["price"]
        }]
    })

    return web.json_response({"invoice_url": result})


app = web.Application(client_max_size=1024 * 1024)

app.router.add_get("/", index)
app.router.add_get("/health", health)
app.router.add_get("/api/products", products)
app.router.add_get("/api/preview/{sku}", preview)
app.router.add_get("/api/purchases", purchases)
app.router.add_post("/api/invoice", invoice)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
