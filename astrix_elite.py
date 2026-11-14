
import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

# ASTRIX SHOP — ASTRIX ELITE

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = os.getenv("ADMIN_ID", "")
DB_PATH = os.getenv("DATABASE_PATH", "/data/astrix.db")
STORE_URL = os.getenv(
    "STORE_URL",
    "https://astrix-shop-production.up.railway.app"
)
PORT = int(os.getenv("PORT", "8080"))

WEEK = 7 * 24 * 60 * 60

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("astrix_elite")

dp = Dispatcher()

# ПЕРВАЯ КОЛЛЕКЦИЯ: 10 ПЕРСОНАЖЕЙ

PEOPLE = [
    ("karina", "Карина", "girls",
     "Розовый импульс", "#ff68ac"),

    ("nika", "Ника", "girls",
     "Звёздный шарм", "#c889ff"),

    ("vera", "Вера", "girls",
     "Тихая элегантность", "#91ba83"),

    ("katya", "Катя", "girls",
     "Алый характер", "#ff758c"),

    ("ruzanna", "Рузанна", "girls",
     "Изумрудный ритм", "#9ded94"),

    ("vika", "Вика", "girls",
     "Фиолетовый драйв", "#a98cff"),

    ("birzhan", "Биржан", "boys",
     "Синий разряд", "#6ebaff"),

    ("hakim", "Хаким", "boys",
     "Неоновая энергия", "#73f3b6"),

    ("kirill", "Кирилл", "boys",
     "Лунный стиль", "#b1a1ff"),

    ("denis", "Денис", "boys",
     "Скейт без правил", "#ff8989"),
]

ELITE = {
    "elite_" + row[0]: row
    for row in PEOPLE
}


# БАЗА ДАННЫХ

def connect():
    Path(DB_PATH).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    db = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    db.execute(
        "PRAGMA busy_timeout=30000"
    )

    return db


def setup_db():
    with connect() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price INTEGER NOT NULL,
                preview_id TEXT,
                original_id TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                sku TEXT NOT NULL,
                original_id TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS elite_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        for sku, (_, name, _, signature, _) in ELITE.items():

            db.execute("""
                INSERT OR IGNORE INTO products
                (sku, name, description, price)
                VALUES (?, ?, ?, 150)
            """, (
                sku,
                name,
                signature
            ))


# СКИДКА НА 7 ДНЕЙ

def sale(db):

    row = db.execute("""
        SELECT value
        FROM elite_settings
        WHERE key='sale_start'
    """).fetchone()

    start = int(row[0]) if row else None

    end = (
        start + WEEK
        if start is not None
        else None
    )

    now = int(time.time())

    active = (
        start is not None
        and start <= now < end
    )

    return {
        "started": start is not None,
        "active": active,
        "ends_at": end,
        "remaining": (
            max(0, end - now)
            if end else 0
        ),
        "price": 100 if active else 150,
        "old_price": 150
    }


# ПРОВЕРКА TELEGRAM WEB APP

def telegram_user(init_data):

    if not init_data:
        raise web.HTTPUnauthorized(
            text="Открой магазин через Telegram"
        )

    try:
        parts = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received = parts.pop("hash")

        check = "\n".join(
            f"{k}={v}"
            for k, v in sorted(parts.items())
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        expected = hmac.new(
            secret,
            check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            received,
            expected
        ):
            raise ValueError("bad hash")

        if abs(
            time.time() -
            int(parts["auth_date"])
        ) > 86400:
            raise ValueError("expired")

        return int(
            json.loads(
                parts["user"]
            )["id"]
        )

    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError
    ):
        raise web.HTTPUnauthorized(
            text="Ошибка авторизации Telegram"
        )


def auth(request):
    return telegram_user(
        request.headers.get(
            "X-Telegram-Init-Data",
            ""
        )
    )


# ЗАЩИТА ПЛАТЕЖЕЙ

def invoice_signature(data):

    return hmac.new(
        BOT_TOKEN.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()[:24]


def validate_payload(
    payload,
    amount,
    currency,
    checkout=False
):

    if currency != "XTR":
        return None

    parts = payload.split(":")

    if len(parts) == 5 and parts[0] == "elite":

        sku = parts[1]
        price_str = parts[2]
        issued_str = parts[3]
        sig = parts[4]

        data = ":".join(parts[:4])

        if sku not in ELITE:
            return None

        if not hmac.compare_digest(
            sig,
            invoice_signature(data)
        ):
            return None

        try:
            price = int(price_str)
            issued = int(issued_str)

        except ValueError:
            return None

        now = int(time.time())

        if price != amount:
            return None

        if issued > now + 60:
            return None

        if issued < now - 3600:
            return None

        with connect() as db:
            state = sale(db)

            row = db.execute("""
                SELECT original_id
                FROM products
                WHERE sku=?
            """, (sku,)).fetchone()

        if not row or not row[0]:
            return None

        if not state["started"]:
            return None

        if checkout:

            if price != state["price"]:
                return None

            if now - issued > 900:
                return None

        elif price == 100:

            if state["ends_at"] is None:
                return None

            if issued >= state["ends_at"]:
                return None

        return sku

    # Совместимость со старыми покупками

    if (
        len(parts) == 3
        and parts[0] == "astrix"
    ):

        sku = parts[1]

        try:
            price = int(parts[2])

        except ValueError:
            return None

        if sku in ELITE or price != amount:
            return None

        with connect() as db:

            row = db.execute("""
                SELECT price, original_id
                FROM products
                WHERE sku=?
            """, (sku,)).fetchone()

        if row and row[0] == price and row[1]:
            return sku

    return None


def file_id(value):

    if not value:
        return None

    if value.startswith("photo:"):
        return value.split(":", 1)[1]

    if value.startswith("document:"):
        return value.split(":", 1)[1]

    return value


# ДИЗАЙН МАГАЗИНА

PAGE = r"""
<!DOCTYPE html>
<html lang="ru">

<head>
<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1,
viewport-fit=cover">

<title>Astrix Shop — Astrix Elite</title>

<script
src="https://telegram.org/js/telegram-web-app.js">
</script>

<style>

:root {
    --bg: #100d1b;
    --panel: #21172d;
    --ink: #fff7fa;
    --muted: #bcaac7;
    --gold: #f7d79b;
}

* {
    box-sizing: border-box;
}

html,
body {
    overflow-x: hidden;
}

body {
    margin: 0;
    color: var(--ink);
    font-family: system-ui, Arial, sans-serif;

    background:
        radial-gradient(
            ellipse at 50% -5%,
            #502f5b,
            transparent 52%
        ),
        linear-gradient(
            140deg,
            #130f21,
            #0d0b18
        );

    min-height: 100vh;
}

button,
input {
    font: inherit;
}

button {
    cursor: pointer;
}

header {
    padding: 24px 16px 20px;
    text-align: center;
    border-bottom: 1px solid #694c6633;
}

header b {
    display: block;
    font: 700 29px Georgia, serif;
    letter-spacing: 3px;
    color: var(--gold);
    text-shadow: 0 0 24px #d55cbd88;
}

header small {
    display: block;
    margin-top: 7px;
    color: #ddbecc;
    letter-spacing: 2px;
    font-size: 10px;
}

main {
    width: min(100%, 660px);
    margin: auto;
    padding: 16px 13px 115px;
}

section[hidden] {
    display: none !important;
}

h2 {
    font: 700 21px Georgia, serif;
    color: #ffe7cf;
    margin: 22px 2px 14px;
}

.hero {
    position: relative;
    overflow: hidden;
    border-radius: 24px;
    padding: 28px 22px 24px;

    background:
        radial-gradient(
            circle at 86% 12%,
            #e6a4e44a,
            transparent 40%
        ),
        linear-gradient(
            140deg,
            #5e3057,
            #21162d 75%
        );

    border: 1px solid #d9a7b25c;

    box-shadow:
        0 18px 45px #0007,
        inset 0 0 45px #efaef30c;
}

.hero::after {
    content: "✧";
    position: absolute;
    right: 18px;
    top: 18px;
    font-size: 112px;
    color: #f6c8d62b;
    pointer-events: none;
}

.eyebrow {
    font-size: 10px;
    letter-spacing: 2px;
    color: var(--gold);
    font-weight: 700;
}

.hero h1 {
    font: 700 38px Georgia, serif;
    letter-spacing: 2px;
    margin: 8px 0;
    text-shadow: 0 4px 14px #0008;
}

.hero p {
    position: relative;
    z-index: 1;
    line-height: 1.45;
    max-width: 270px;
    color: #efdbe7;
}

.promo {
    margin-top: 17px;
    display: inline-flex;
    gap: 10px;
    align-items: center;

    background: #ffffff18;
    border: 1px solid #ffe8d750;
    border-radius: 50px;
    padding: 9px 13px;
    font-weight: 700;
}

.promo del {
    color: #cdb5c7;
    font-size: 12px;
}

.gold {
    color: #ffe1a0;
}

.timer {
    margin-top: 12px;
    font-size: 12px;
    color: #f4d4eb;
}

.rail {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
}

.rail small {
    font-size: 11px;
    color: var(--muted);
}

.search {
    width: 100%;
    border: 1px solid #735475;
    border-radius: 14px;
    background: #ffffff0b;
    padding: 13px 14px;
    outline: none;
    color: white;
    margin: 10px 0 12px;
}

.search:focus {
    border-color: #e2a6cf;
}

.filters {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    margin-bottom: 16px;
}

.filters button {
    border: 1px solid #684965;
    background: #2c2138;
    border-radius: 30px;
    color: #e8d3e8;
    padding: 8px 13px;
    white-space: nowrap;
}

.filters button.on {
    border-color: #f0bcbe;
    background: #8d4e7c;
    color: white;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(2, minmax(0, 1fr));
    gap: 12px;
    align-items: stretch;
    min-width: 0;
}

.card {
    overflow: hidden;
    min-width: 0;
    border-radius: 19px;

    background:
        linear-gradient(
            180deg,
            #34263d,
            #211725
        );

    border: 1px solid #80607980;
    box-shadow: 0 11px 22px #0004;
}

.art {
    aspect-ratio: 4 / 5;
    position: relative;

    background:
        radial-gradient(
            circle at 50% 40%,
            var(--accent, #8e759f),
            transparent 68%
        ),
        #241a33;

    overflow: hidden;
}

.art img {
    height: 100%;
    width: 100%;
    object-fit: contain;
    display: block;
}

.placeholder {
    position: absolute;
    inset: 0;

    display: grid;
    place-items: center;
    align-content: center;
    text-align: center;

    padding: 8px;
    color: #fff7;
}

.placeholder strong {
    font: 700 56px Georgia, serif;
}

.placeholder small {
    font-size: 10px;
    margin-top: 10px;
}

.card-info {
    padding: 12px 11px;
    min-width: 0;
}

.card-name {
    font-weight: 800;
    font-size: 15px;

    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.tagline {
    color: #c8b4d0;
    font-size: 11px;
    margin-top: 4px;
    min-height: 30px;
}

.price {
    display: flex;
    align-items: baseline;
    gap: 7px;
    flex-wrap: wrap;
    margin: 7px 0 9px;
}

.price b {
    font-size: 20px;
    color: #ffe3ad;
}

.price del {
    font-size: 11px;
    color: #a594a4;
}

.sale {
    font-size: 10px;
    color: #ffdbe9;
}

.buy {
    width: 100%;
    border: none;
    border-radius: 11px;
    padding: 10px 5px;

    color: #281323;
    background:
        linear-gradient(
            110deg,
            #fadba8,
            #f5a6d2
        );

    font-weight: 800;
}

.buy:disabled {
    background: #4b3c54;
    color: #beaab9;
    cursor: default;
}

.notice {
    border: 1px solid #624969;
    background: #251a32;
    padding: 19px;
    border-radius: 16px;
    color: #e4d0dd;
    line-height: 1.5;
}

.item {
    padding: 13px;
    border-bottom: 1px solid #493654;
}

nav {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 5;

    display: flex;
    justify-content: center;

    background: #1b1425f7;
    backdrop-filter: blur(20px);
    border-top: 1px solid #765777;
    box-shadow: 0 -10px 25px #0005;

    padding-bottom:
        env(safe-area-inset-bottom);
}

nav > div {
    width: min(100%, 660px);
    display: grid;
    grid-template-columns:
        repeat(4, minmax(0, 1fr));
}

nav button {
    border: none;
    background: none;
    color: #b5a4bf;
    padding: 10px 1px 11px;
    font-size: 10px;
}

nav button i {
    font-size: 20px;
    display: block;
    font-style: normal;
    margin-bottom: 4px;
}

nav button.on {
    color: #ffe1b1;
}

.overlay {
    position: fixed;
    inset: 0;
    z-index: 10;

    background: #05030bdc;
    display: grid;
    place-items: center;
    padding: 18px;
}

.overlay[hidden] {
    display: none;
}

.dialog {
    position: relative;
    width: min(100%, 420px);
    max-height: 93vh;
    overflow-y: auto;

    border-radius: 22px;
    background: #2b1c37;
    border: 1px solid #d1a9b787;
    padding: 18px;
}

.dialog img {
    width: 100%;
    height: min(45vh, 350px);
    object-fit: contain;
    border-radius: 15px;
    background: #21172e;
}

.x {
    position: absolute;
    right: 15px;
    top: 12px;
    background: #170f21dd;
    border: none;
    border-radius: 50%;
    height: 36px;
    width: 36px;
    color: white;
    font-size: 22px;
}

@media (min-width: 530px) {
    .grid {
        grid-template-columns:
            repeat(3, minmax(0, 1fr));
    }
}

@media (max-width: 340px) {
    main {
        padding: 12px 9px 100px;
    }

    .grid {
        gap: 8px;
    }

    .card-info {
        padding: 9px 8px;
    }

    .card-name {
        font-size: 13px;
    }

    .hero h1 {
        font-size: 32px;
    }
}

</style>
</head>

<body>

<header>
    <b>✦ ASTRIX SHOP ✦</b>
    <small>ORIGINAL 3D COLLECTIONS</small>
</header>

<main>

<section id="home">

    <div class="hero">

        <div class="eyebrow">
            ПЕРВАЯ КОЛЛЕКЦИЯ · 01
        </div>

        <h1>ASTRIX ELITE</h1>

        <p>
            Десять ярких 3D-аватаров.
            Выбери персонажа,
            который тебе близок.
        </p>

        <div class="promo">

            <span id="hero-price" class="gold">
                150 ⭐
            </span>

            <del id="hero-old"></del>

            <span
                class="sale"
                id="hero-sale">
            </span>

        </div>

        <div id="timer" class="timer"></div>

    </div>

    <div class="rail">
        <h2>Вся коллекция</h2>
        <small>10 персонажей</small>
    </div>

    <div id="home-grid" class="grid"></div>

</section>

<section id="catalog" hidden>

    <h2>Каталог Astrix Elite</h2>

    <input
        id="search"
        class="search"
        placeholder="Найти персонажа..."
        aria-label="Поиск персонажей"
    >

    <div class="filters">

        <button class="on" data-filter="all">
            Все
        </button>

        <button data-filter="girls">
            Девушки
        </button>

        <button data-filter="boys">
            Парни
        </button>

    </div>

    <div id="catalog-grid" class="grid"></div>

</section>

<section id="owned" hidden>

    <h2>Моя коллекция</h2>

    <div
        id="owned-list"
        class="notice">
        Загрузка...
    </div>

</section>

<section id="profile" hidden>

    <h2>Мой профиль</h2>

    <div
        id="profile-info"
        class="notice">
    </div>

</section>

</main>

<nav>
    <div>
        <button
            class="on"
            data-page="home">
            <i>⌂</i>Главная
        </button>

        <button data-page="catalog">
            <i>▦</i>Каталог
        </button>

        <button data-page="owned">
            <i>♡</i>Коллекция
        </button>

        <button data-page="profile">
            <i>♙</i>Профиль
        </button>
    </div>
</nav>

<div id="modal" class="overlay" hidden>

    <div class="dialog">

        <button id="close" class="x">
            ×
        </button>

        <div id="modal-body"></div>

    </div>

</div>

<script>

const tg = window.Telegram?.WebApp;

tg?.ready();
tg?.expand();

const initData = tg?.initData || "";

let products = [];
let promo = {};
let filter = "all";
let query = "";

const $ = id => document.getElementById(id);

const tones = {
    karina: "#d34891",
    nika: "#8d48cb",
    vera: "#678c60",
    katya: "#b03c63",
    ruzanna: "#62b772",
    vika: "#8754be",
    birzhan: "#427bba",
    hakim: "#47a879",
    kirill: "#7469be",
    denis: "#c45f5b"
};

function node(tag, cls, content) {

    let el = document.createElement(tag);

    if (cls) {
        el.className = cls;
    }

    if (content !== undefined) {
        el.textContent = content;
    }

    return el;
}

async function api(path, opts = {}) {

    let response = await fetch(path, {
        ...opts,

        headers: {
            "X-Telegram-Init-Data": initData,
            ...opts.headers
        }
    });

    if (!response.ok) {
        throw Error(
            "Ошибка " + response.status
        );
    }

    return response.json();
}

function card(p) {

    let key = p.sku.replace("elite_", "");

    let c = node("article", "card");

    let art = node("div", "art");

    art.style.setProperty(
        "--accent",
        tones[key] || "#8a70a0"
    );

    if (p.preview_url) {

        let img = node("img");

        img.src = p.preview_url;
        img.alt = p.name;
        img.loading = "lazy";

        art.append(img);

    } else {

        let placeholder = node(
            "div",
            "placeholder"
        );

        placeholder.append(
            node("strong", "", p.name.slice(0, 1)),
            node(
                "small",
                "",
                "ИЗОБРАЖЕНИЕ ГОТОВИТСЯ"
            )
        );

        art.append(placeholder);
    }

    let info = node(
        "div",
        "card-info"
    );

    info.append(
        node("div", "card-name", p.name),
        node(
            "div",
            "tagline",
            p.description
        )
    );

    let price = node("div", "price");

    price.append(
        node("b", "", p.price + " ⭐")
    );

    if (p.discount) {

        price.append(
            node("del", "", "150 ⭐"),
            node("span", "sale", "−50 ⭐")
        );

    }

    info.append(price);

    let button = node(
        "button",
        "buy",
        p.available
            ? "Подробнее"
            : "Скоро в продаже"
    );

    button.disabled = !p.available;

    button.onclick = () => details(p);

    info.append(button);

    c.append(art, info);

    return c;
}

function render() {

    let home = $("home-grid");
    let catalog = $("catalog-grid");

    home.replaceChildren();
    catalog.replaceChildren();

    for (const product of products) {

        home.append(card(product));

        let matches = (
            filter === "all" ||
            product.gender === filter
        ) && product.name
            .toLowerCase()
            .includes(query);

        if (matches) {
            catalog.append(card(product));
        }
    }

    if (!catalog.children.length) {
        catalog.append(
            node(
                "div",
                "notice",
                "Ничего не найдено"
            )
        );
    }
}

function refreshPromo() {

    let timer = $("timer");
    let price = $("hero-price");
    let old = $("hero-old");
    let saleLabel = $("hero-sale");

    if (promo.active) {

        price.textContent = "100 ⭐";
        old.textContent = "150 ⭐";
        saleLabel.textContent = "−33%";

        let seconds = Math.max(
            0,
            Math.floor(
                (
                    promo.ends_at * 1000 -
                    Date.now()
                ) / 1000
            )
        );

        let days = Math.floor(
            seconds / 86400
        );

        let hours = Math.floor(
            (seconds % 86400) / 3600
        );

        let minutes = Math.floor(
            (seconds % 3600) / 60
        );

        timer.textContent =
            "Акция завершится через: " +
            days + " д. " +
            hours + " ч. " +
            minutes + " мин.";

        if (seconds === 0) {
            load();
        }

    } else {

        price.textContent = "150 ⭐";
        old.textContent = "";
        saleLabel.textContent = "";

        timer.textContent = promo.started
            ? "Акция завершена"
            : "Первая коллекция · скоро открытие";
    }
}

async function load() {

    try {

        let data = await api("/api/products");

        products = data.products;
        promo = data.promo;

        render();
        refreshPromo();

    } catch (error) {

        $("home-grid").append(
            node(
                "div",
                "notice",
                "Не удалось загрузить каталог"
            )
        );
    }
}

function details(product) {

    let modal = $("modal");
    let body = $("modal-body");

    body.replaceChildren();

    body.append(
        node("h2", "", product.name)
    );

    if (product.preview_url) {

        let img = node("img");

        img.src = product.preview_url;
        img.alt = product.name;

        body.append(img);
    }

    body.append(
        node(
            "p",
            "",
            product.description
        )
    );

    body.append(
        node(
            "h2",
            "",
            product.price + " ⭐"
        )
    );

    if (product.discount) {

        body.append(
            node(
                "p",
                "sale",
                "Цена до скидки: 150 ⭐ · экономия 50 ⭐"
            )
        );
    }

    let button = node(
        "button",
        "buy",
        "Купить за " + product.price + " ⭐"
    );

    button.onclick = () => purchase(
        product.sku,
        button
    );

    body.append(button);

    modal.hidden = false;
}

async function purchase(sku, button) {

    if (!tg?.openInvoice || !initData) {

        alert(
            "Открой магазин через Telegram-бота"
        );

        return;
    }

    button.disabled = true;

    try {

        let data = await api(
            "/api/invoice",
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body: JSON.stringify({
                    sku
                })
            }
        );

        tg.openInvoice(
            data.invoice_url,

            status => {

                button.disabled = false;

                if (status === "paid") {

                    tg.showAlert(
                        "Покупка принята! " +
                        "Изображение придёт в чат с ботом."
                    );

                    $("modal").hidden = true;
                }
            }
        );

    } catch (error) {

        button.disabled = false;

        if (tg?.showAlert) {

            tg.showAlert(
                "Не удалось создать счёт."
            );

        } else {

            alert(error.message);
        }
    }
}

async function owned() {

    let target = $("owned-list");

    if (!initData) {

        target.textContent =
            "Коллекция доступна через Telegram.";

        return;
    }

    try {

        let data = await api(
            "/api/purchases"
        );

        target.replaceChildren();

        if (!data.purchases.length) {

            target.textContent =
                "Ты ещё не купил персонажей.";

            return;
        }

        for (let item of data.purchases) {

            target.append(
                node(
                    "div",
                    "item",
                    "✦ " + item.name
                )
            );
        }

    } catch (error) {

        target.textContent =
            "Не удалось загрузить покупки.";
    }
}

function show(page) {

    document
        .querySelectorAll("main > section")
        .forEach(section => {

            section.hidden =
                section.id !== page;
        });

    document
        .querySelectorAll("nav button")
        .forEach(button => {

            button.classList.toggle(
                "on",
                button.dataset.page === page
            );
        });

    if (page === "owned") {
        owned();
    }
}

document
    .querySelectorAll("nav button")
    .forEach(button => {

        button.onclick = () => show(
            button.dataset.page
        );
    });

document
    .querySelectorAll(".filters button")
    .forEach(button => {

        button.onclick = () => {

            filter = button.dataset.filter;

            document
                .querySelectorAll(".filters button")
                .forEach(item => {

                    item.classList.toggle(
                        "on",
                        item === button
                    );
                });

            render();
        };
    });

$("search").oninput = event => {

    query = event.target.value
        .toLowerCase()
        .trim();

    render();
};

$("close").onclick = () => {

    $("modal").hidden = true;
};

$("modal").onclick = event => {

    if (event.target === $("modal")) {

        $("modal").hidden = true;
    }
};

const person = tg?.initDataUnsafe?.user;

$("profile-info").textContent = person
    ? (
        (person.first_name || "Пользователь") +
        " · Telegram ID " + person.id
    )
    : "Открой приложение через Telegram.";

load();

setInterval(
    refreshPromo,
    30000
);

</script>

</body>
</html>
"""


# ГЛАВНАЯ СТРАНИЦА

async def index(request):

    return web.Response(
        text=PAGE,
        content_type="text/html",
        charset="utf-8"
    )


# ПРОВЕРКА RAILWAY

async def health(request):

    return web.json_response({
        "status": "ok",
        "app": "Astrix Elite"
    })


# КАТАЛОГ

async def products(request):

    with connect() as db:

        state = sale(db)

        rows = db.execute("""
            SELECT
                sku,
                name,
                description,
                preview_id,
                original_id
            FROM products
        """).fetchall()

    records = {
        row[0]: row
        for row in rows
    }

    result = []

    for sku, (_, name, gender, signature, accent) in ELITE.items():

        row = records[sku]

        ready = bool(
            row[3] and
            row[4] and
            state["started"]
        )

        result.append({
            "sku": sku,
            "name": name,
            "description": signature,
            "gender": gender,
            "accent": accent,
            "price": state["price"],
            "discount": state["active"],
            "available": ready,
            "preview_url": (
                f"/image/{sku}"
                if row[3]
                else None
            )
        })

    return web.json_response(
        {
            "products": result,
            "promo": state
        },
        headers={
            "Cache-Control": "no-store"
        }
    )


# ИЗОБРАЖЕНИЯ ПЕРСОНАЖЕЙ

async def preview(request):

    sku = request.match_info["sku"]

    if sku not in ELITE:
        raise web.HTTPNotFound()

    with connect() as db:

        row = db.execute("""
            SELECT preview_id
            FROM products
            WHERE sku=?
        """, (sku,)).fetchone()

    if not row or not row[0]:
        raise web.HTTPNotFound()

    cache = request.app["cache"]

    if (
        sku not in cache or
        cache[sku][0] != row[0]
    ):

        try:

            data = await request.app["bot"].download(
                file_id(row[0])
            )

            payload = data.getvalue()

        except Exception:

            log.exception(
                "Image download failed: %s",
                sku
            )

            raise web.HTTPBadGateway(
                text="Изображение временно недоступно"
            )

        cache[sku] = (
            row[0],
            payload
        )

    data = cache[sku][1]

    if data.startswith(b"\x89PNG"):
        kind = "image/png"

    elif data.startswith(b"RIFF"):
        kind = "image/webp"

    else:
        kind = "image/jpeg"

    return web.Response(
        body=data,
        content_type=kind,
        headers={
            "Cache-Control":
                "public, max-age=300"
        }
    )


# ПОКУПКИ ПОЛЬЗОВАТЕЛЯ

async def purchases(request):

    user_id = auth(request)

    with connect() as db:

        rows = db.execute("""
            SELECT DISTINCT
                p.sku,
                p.name
            FROM purchases b

            JOIN products p
            ON p.sku=b.sku

            WHERE b.user_id=?
        """, (user_id,)).fetchall()

    return web.json_response({
        "purchases": [
            {
                "sku": sku,
                "name": name
            }
            for sku, name in rows
        ]
    })


# СОЗДАНИЕ СЧЁТА TELEGRAM STARS

async def invoice(request):

    auth(request)

    try:

        body = await request.json()
        sku = body["sku"]

        if sku not in ELITE:
            raise ValueError()

    except (
        KeyError,
        TypeError,
        ValueError
    ):

        raise web.HTTPBadRequest(
            text="Неверный товар"
        )

    with connect() as db:

        row = db.execute("""
            SELECT
                name,
                description,
                preview_id,
                original_id
            FROM products
            WHERE sku=?
        """, (sku,)).fetchone()

        state = sale(db)

    if (
        not row or
        not row[2] or
        not row[3] or
        not state["started"]
    ):

        raise web.HTTPConflict(
            text="Персонаж ещё не опубликован"
        )

    price = state["price"]
    issued = int(time.time())

    data = f"elite:{sku}:{price}:{issued}"

    payload = (
        f"{data}:{invoice_signature(data)}"
    )

    try:

        url = await request.app["bot"].create_invoice_link(

            title=row[0][:32],

            description=(
                "Astrix Elite · " + row[1]
            )[:255],

            payload=payload,

            currency="XTR",

            prices=[
                LabeledPrice(
                    label=row[0][:32],
                    amount=price
                )
            ],

            provider_token=""
        )

    except Exception:

        log.exception(
            "Could not create invoice"
        )

        raise web.HTTPBadGateway(
            text="Не удалось создать счёт"
        )

    return web.json_response({
        "invoice_url": url
    })


# ПРОВЕРКА АДМИНИСТРАТОРА

def is_admin(message):

    return (
        ADMIN_ID.isdigit()
        and message.from_user
        and message.from_user.id == int(ADMIN_ID)
    )


# TELEGRAM-БОТ

@dp.message(Command("myid"))
async def myid(message: Message):

    await message.answer(
        f"Telegram ID: {message.from_user.id}"
    )


@dp.message(CommandStart())
@dp.message(Command("catalog"))
async def start(message: Message):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть Astrix Elite",
                    web_app=WebAppInfo(
                        url=STORE_URL
                    )
                )
            ]
        ]
    )

    await message.answer(
        "✦ ASTRIX SHOP\n\n"
        "Первая коллекция Astrix Elite — "
        "10 оригинальных 3D-персонажей.\n\n"
        "Открой каталог и выбери персонажа.",
        reply_markup=keyboard
    )


# ИНСТРУКЦИЯ ДЛЯ ЗАГРУЗКИ

@dp.message(Command("helpelite"))
async def helpelite(message: Message):

    if not is_admin(message):
        return

    await message.answer(
        "ЗАГРУЗКА ПЕРСОНАЖЕЙ\n\n"
        "Отправь изображение с подписью:\n"
        "#elite karina\n\n"
        "Остальные имена:\n"
        "nika, vera, katya, ruzanna, vika,\n"
        "birzhan, hakim, kirill, denis.\n\n"
        "Для максимального качества "
        "отправляй изображение как файл.\n\n"
        "После загрузки всех десяти "
        "отправь /launch_elite.\n\n"
        "Проверка: /status_elite"
    )


# СОСТОЯНИЕ КОЛЛЕКЦИИ

@dp.message(Command("status_elite"))
async def status_elite(message: Message):

    if not is_admin(message):
        return

    with connect() as db:

        state = sale(db)

        rows = db.execute("""
            SELECT
                sku,
                preview_id,
                original_id
            FROM products
        """).fetchall()

    done = [
        sku
        for sku, preview_id, original_id in rows
        if (
            sku in ELITE and
            preview_id and
            original_id
        )
    ]

    await message.answer(
        f"Astrix Elite: {len(done)}/10.\n"
        f"Акция запущена: "
        f"{'да' if state['started'] else 'нет'}.\n"
        f"Активна: "
        f"{'да' if state['active'] else 'нет'}.\n\n"
        "Не готовы: " +
        ", ".join(
            sku[6:]
            for sku in ELITE
            if sku not in done
        )
    )


# ЗАПУСК НЕДЕЛЬНОЙ АКЦИИ

@dp.message(Command("launch_elite"))
async def launch_elite(message: Message):

    if not is_admin(message):
        return

    with connect() as db:

        ready = db.execute("""
            SELECT COUNT(*)
            FROM products
            WHERE sku LIKE 'elite_%'
            AND preview_id IS NOT NULL
            AND original_id IS NOT NULL
        """).fetchone()[0]

        old = db.execute("""
            SELECT value
            FROM elite_settings
            WHERE key='sale_start'
        """).fetchone()

        if not old and ready == 10:

            db.execute("""
                INSERT INTO elite_settings
                (key, value)
                VALUES ('sale_start', ?)
            """, (
                str(int(time.time())),
            ))

    if old:

        await message.answer(
            "Акция уже была запущена."
        )

    elif ready != 10:

        await message.answer(
            f"Сначала загрузи всех персонажей.\n"
            f"Готово: {ready}/10."
        )

    else:

        await message.answer(
            "ASTRIX ELITE ОПУБЛИКОВАНА!\n\n"
            "Стоимость: 100 ⭐\n"
            "Обычная цена: 150 ⭐\n"
            "Экономия: 50 ⭐\n\n"
            "Акция действует ровно 7 суток."
        )


# ЗАГРУЗКА 3D-ПЕРСОНАЖЕЙ

@dp.message(F.photo | F.document)
async def upload(message: Message):

    if not is_admin(message):
        return

    caption = (
        message.caption or ""
    ).lower().strip().split()

    if (
        len(caption) != 2 or
        caption[0] != "#elite"
    ):
        return

    sku = "elite_" + caption[1]

    if sku not in ELITE:

        await message.answer(
            "Неизвестный персонаж.\n"
            "Напиши /helpelite"
        )

        return

    if message.photo:

        media = (
            "photo:" +
            message.photo[-1].file_id
        )

    elif (
        message.document and
        (
            message.document.mime_type or ""
        ).startswith("image/")
    ):

        media = (
            "document:" +
            message.document.file_id
        )

    else:

        await message.answer(
            "Пришли изображение PNG/JPG."
        )

        return

    with connect() as db:

        state = sale(db)

        if state["started"]:

            await message.answer(
                "Коллекция уже запущена. "
                "Изменение опубликованного товара "
                "потребует отдельной настройки."
            )

            return

        db.execute("""
            UPDATE products
            SET preview_id=?,
                original_id=?
            WHERE sku=?
        """, (
            media,
            media,
            sku
        ))

    await message.answer(
        "Сохранено: " +
        ELITE[sku][1]
    )


# ВЫДАЧА ПЕРСОНАЖА

async def deliver(
    message,
    sku,
    original
):

    name = (
        ELITE[sku][1]
        if sku in ELITE
        else sku
    )

    if original.startswith("photo:"):

        await message.answer_photo(
            file_id(original),
            caption="Покупка: " + name
        )

    else:

        await message.answer_document(
            file_id(original),
            caption="Покупка: " + name
        )


# МОИ ПОКУПКИ

@dp.message(Command("orders"))
async def orders(message: Message):

    with connect() as db:

        rows = db.execute("""
            SELECT DISTINCT
                p.sku,
                b.original_id

            FROM purchases b

            JOIN products p
            ON p.sku=b.sku

            WHERE b.user_id=?
        """, (
            message.from_user.id,
        )).fetchall()

    if not rows:

        await message.answer(
            "Пока нет покупок."
        )

        return

    for sku, original in rows:

        try:

            await deliver(
                message,
                sku,
                original
            )

        except Exception:

            log.exception(
                "Delivery failed"
            )

            await message.answer(
                "Не удалось отправить " +
                sku +
                ". Обратись к администратору."
            )


# ПОДТВЕРЖДЕНИЕ ОПЛАТЫ

@dp.pre_checkout_query()
async def precheckout(query: PreCheckoutQuery):

    sku = validate_payload(
        query.invoice_payload,
        query.total_amount,
        query.currency,
        checkout=True
    )

    await query.answer(
        ok=bool(sku),

        error_message=(
            None if sku
            else "Счёт недействителен "
                 "или срок акции истёк"
        )
    )


# УСПЕШНЫЙ ПЛАТЁЖ

@dp.message(F.successful_payment)
async def success(message: Message):

    payment = message.successful_payment

    sku = validate_payload(
        payment.invoice_payload,
        payment.total_amount,
        payment.currency
    )

    if not sku:

        await message.answer(
            "Платёж требует проверки. "
            "Обратись к администратору."
        )

        return

    with connect() as db:

        row = db.execute("""
            SELECT original_id
            FROM products
            WHERE sku=?
        """, (sku,)).fetchone()

        if not row or not row[0]:

            await message.answer(
                "Оплата принята. "
                "Файл временно недоступен."
            )

            return

        db.execute("""
            INSERT OR IGNORE INTO purchases
            (charge_id, user_id, sku, original_id)
            VALUES (?, ?, ?, ?)
        """, (
            payment.telegram_payment_charge_id,
            message.from_user.id,
            sku,
            row[0]
        ))

    try:

        await deliver(
            message,
            sku,
            row[0]
        )

    except Exception:

        log.exception(
            "Paid delivery failed"
        )

        await message.answer(
            "Оплата записана.\n"
            "Попробуй /orders "
            "или обратись к администратору."
        )


# ЗАПУСК САЙТА И TELEGRAM-БОТА

async def main():

    setup_db()

    bot = Bot(BOT_TOKEN)

    app = web.Application()

    app["bot"] = bot
    app["cache"] = {}

    app.router.add_get(
        "/",
        index
    )

    app.router.add_get(
        "/health",
        health
    )

    app.router.add_get(
        "/api/products",
        products
    )

    app.router.add_get(
        "/image/{sku}",
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

    runner = web.AppRunner(app)

    await runner.setup()

    try:

        site = web.TCPSite(
            runner,
            "0.0.0.0",
            PORT
        )

        await site.start()

        log.info(
            "Astrix Elite запущена на порту %s",
            PORT
        )

        await dp.start_polling(bot)

    finally:

        await runner.cleanup()

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
