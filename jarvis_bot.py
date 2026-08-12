import os
import re
import base64
import requests
import time
import json
import random
import io
import threading
import asyncio
import math
import html
import functools
import edge_tts
import subprocess
from http.server import SimpleHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import telebot
from telebot import types
import google.generativeai as genai
from PIL import Image
import psycopg2
from psycopg2 import pool as pg_pool
import discord
from discord.ext import commands
from flask import Flask, request, jsonify
from flask_cors import CORS
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from datetime import datetime, timezone, timedelta


DATABASE_URL = os.environ.get('DATABASE_URL')

# 🔒 ЛОК ДЛЯ БЕЗПЕКИ ПОТОКІВ
db_lock = threading.Lock()

# ===================================================================
# 🗄️ ПУЛ З'ЄДНАНЬ ДО POSTGRES
# Раніше кожен виклик get_db_connection() відкривав нове TCP-з'єднання
# до Neon і більше його не перевикористовував — це повільно і швидко
# впирається в ліміт з'єднань хмарної БД. Тепер тримаємо невеликий пул
# готових з'єднань і роздаємо їх повторно.
# ===================================================================
_db_pool = None

def _init_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = pg_pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=DATABASE_URL,
        )

class _PooledConnection:
    """Обгортка над з'єднанням з пулу.

    Увесь інший код бота як і раніше робить:
        conn = get_db_connection()
        ...
        conn.close()
    Різниця лише в тому, що .close() тепер не рве TCP-з'єднання,
    а повертає його назад у пул для наступного запиту."""

    def __init__(self, pool_ref, conn):
        self._pool = pool_ref
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        try:
            if self._conn.closed:
                # з'єднання вже зламане (наприклад, розірвалось по мережі) — не повертаємо в пул
                self._pool.putconn(self._conn, close=True)
            else:
                # на випадок незакомічених змін, щоб не тягнути відкриту транзакцію в пул
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                self._pool.putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

# Підключаємося до БД (тепер бере з'єднання з пулу замість нового щоразу)
def get_db_connection():
    _init_db_pool()
    raw_conn = _db_pool.getconn()
    return _PooledConnection(_db_pool, raw_conn)

# Створення та авто-оновлення таблиць при запуску
try:
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 1. Таблиця статистики
            cursor.execute("""CREATE TABLE IF NOT EXISTS stats (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                count INTEGER,
                gender TEXT,
                in_chat BOOLEAN DEFAULT TRUE,
                balance BIGINT DEFAULT 0
            )""")
            
            # 🛠 ДОДАЄМО ВСІ НЕОБХІДНІ КОЛОНКИ ДЛЯ КАСТОМІЗАЦІЇ ТА ПРОФІЛЮ
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS balance BIGINT DEFAULT 0;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_nick VARCHAR(50) DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_title VARCHAR(50) DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_photo TEXT DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS inventory_order TEXT DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS biz_order TEXT DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS show_full_inventory BOOLEAN DEFAULT TRUE;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS gender VARCHAR(20) DEFAULT 'Невідомо';")
            
            # 2. Таблиця майна (Монополія)
            cursor.execute("""CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                item_code TEXT,
                item_name TEXT,
                item_category TEXT
            )""")
            
            # 3. Таблиця бізнесів
            cursor.execute("""CREATE TABLE IF NOT EXISTS user_businesses (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                biz_code TEXT
            )""")

            # 4. Таблиця шлюбів
            cursor.execute("""CREATE TABLE IF NOT EXISTS marriages (
                user1_id BIGINT,
                user2_id BIGINT,
                UNIQUE(user1_id),
                UNIQUE(user2_id)
            )""")

            # 5. Таблиця банів
            cursor.execute("""CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY
            )""")

            # 6. Спільні гаманці для пар (сімейний банк)
            cursor.execute("""CREATE TABLE IF NOT EXISTS shared_wallets (
                pair_id VARCHAR(100) PRIMARY KEY,
                balance BIGINT DEFAULT 0
            )""")

            # 🧠 6. ТАБЛИЦЯ ПЕРСОНАЛЬНОЇ ПАМ'ЯТІ ЮЗЕРІВ
            cursor.execute("""CREATE TABLE IF NOT EXISTS user_memory (
                user_id BIGINT PRIMARY KEY,
                user_name TEXT,
                facts TEXT[] DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            # 🐊 7. РЕЙТИНГ ГРИ "КРОКОДИЛ" (очки по чатах)
            cursor.execute("""CREATE TABLE IF NOT EXISTS crocodile_scores (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                points BIGINT DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )""")

        conn.commit()
        conn.close()
    print("✅ База даних успішно оновлена та готова до роботи!")
except Exception as e:
    print(f"Помилка створення/оновлення таблиць: {e}")

# ==================== НАЛАШТУВАННЯ ====================
API_ID = int(os.environ.get('API_ID', 12345678))
API_HASH = os.environ.get('API_HASH', 'ТВІЙ_API_HASH')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ТВІЙ_TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'ТВІЙ_GEMINI_API_KEY')
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY', '') # 🗝️ Ключ ElevenLabs з Render
ELEVENLABS_VOICE_ID = "WtDqMP4cPOGB6kDiLZgi" # 👴 ID голосу Діда

WEB_APP_URL = os.environ.get('WEB_APP_URL', 'https://ppaapaaa34-hub.github.io/dragobott/')
# ======================================================

DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'ТВІЙ_ДИСКОРД_ТОКЕН')
TELEGRAM_CHAT_ID = -1003428241218  # ID чату для анонсів

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ===================================================================
# 🐢 ГЛОБАЛЬНИЙ ЛІМІТ ЗАПИТІВ (антиспам на команди + на ШІ)
# Захищає від того, щоб юзер закидував бота командами або
# запитами до ШІ занадто швидко. Працює для ВСІХ обробників
# повідомлень (команди, тригер-слова, звичайний текстовий чат із ШІ,
# фото/гіфки тощо) — автоматично, без правок у самих обробниках.
# ===================================================================
RATE_LOCK = threading.Lock()
RATE_LAST_USE = {}      # {(chat_id, user_id): timestamp останньої дозволеної дії}
RATE_STRIKES = {}       # {(chat_id, user_id): {"count": int, "last": timestamp}}
RATE_BLOCKED_UNTIL = {} # {(chat_id, user_id): timestamp до якого юзер в тимчасовому блоці}
RATE_LAST_WARNED = {}   # {(chat_id, user_id): timestamp останнього попередження}

RATE_COOLDOWN_SEC = 2        # мінімальний інтервал між будь-якими командами/запитами до ШІ (для одного юзера)
RATE_STRIKE_LIMIT = 4        # скільки разів поспіль можна "влетіти" в кулдаун
RATE_STRIKE_WINDOW_SEC = 15  # ...за який період, інакше лічильник скидається
RATE_BLOCK_SEC = 30          # тимчасове ігнорування команд/ШІ після перевищення ліміту
RATE_WARN_COOLDOWN_SEC = 5   # не частіше, ніж раз в стільки секунд, попереджаємо юзера

# 🏘️ Груповий ліміт НА ВЕСЬ ЧАТ (щоб не спамили командами по черзі кілька
# РІЗНИХ людей — кожен окремо може вкладатись у свій особистий ліміт, але
# разом закидувати чат командами). Рахує команди від УСІХ юзерів чату разом.
CHAT_CMD_TRACKER = {}        # {chat_id: [timestamps команд від будь-кого]}
CHAT_CMD_BLOCKED_UNTIL = {}  # {chat_id: timestamp до якого команди в чаті на паузі}
CHAT_CMD_LAST_WARNED = {}    # {chat_id: timestamp останнього попередження в чаті}

CHAT_CMD_LIMIT = 4          # скільки команд загалом (від будь-кого) вважати флудом чату
CHAT_CMD_WINDOW_SEC = 5     # ...якщо вони прийшли за стільки секунд
CHAT_CMD_BLOCK_SEC = 20     # на скільки "заморожуємо" команди в чаті після цього
CHAT_CMD_WARN_COOLDOWN_SEC = 5


def check_rate_limit(message) -> bool:
    """
    Загальний ліміт запитів (команди + ШІ + будь-яка інша дія юзера).
    Складається з двох рівнів:
      1. Особистий ліміт юзера (як і раніше).
      2. Груповий ліміт на весь чат — щоб команди не спамили по черзі
         кілька різних людей одночасно.
    Повертає True, якщо запит потрібно заблокувати (в цьому разі
    обробку повідомлення слід припинити).
    """
    user = getattr(message, "from_user", None)
    if not user:
        return False

    chat_id = message.chat.id
    user_id = user.id
    key = (chat_id, user_id)
    is_group = message.chat.type != 'private'

    # Адмінів/модераторів у групах і суперадміна бота — не обмежуємо
    try:
        if is_group and is_chat_admin(chat_id, user_id):
            return False
        if is_admin(user_id):
            return False
    except Exception:
        pass

    now = time.time()
    action = "ok"
    remaining = 0

    with RATE_LOCK:
        blocked_until = RATE_BLOCKED_UNTIL.get(key, 0)

        if now < blocked_until:
            action = "still_blocked"
            remaining = int(blocked_until - now)
        else:
            last_use = RATE_LAST_USE.get(key, 0)
            if now - last_use < RATE_COOLDOWN_SEC:
                strikes = RATE_STRIKES.get(key, {"count": 0, "last": 0})
                if now - strikes.get("last", 0) > RATE_STRIKE_WINDOW_SEC:
                    strikes["count"] = 0
                strikes["count"] += 1
                strikes["last"] = now
                RATE_STRIKES[key] = strikes

                if strikes["count"] >= RATE_STRIKE_LIMIT:
                    RATE_BLOCKED_UNTIL[key] = now + RATE_BLOCK_SEC
                    RATE_STRIKES[key] = {"count": 0, "last": 0}
                    action = "block"
                    remaining = RATE_BLOCK_SEC
                else:
                    action = "strike"
            else:
                RATE_LAST_USE[key] = now
                action = "ok"

        # Якщо юзер особисто в порядку — перевіряємо груповий ліміт на чат
        if action == "ok" and is_group:
            chat_blocked_until = CHAT_CMD_BLOCKED_UNTIL.get(chat_id, 0)
            if now < chat_blocked_until:
                action = "chat_blocked"
                remaining = int(chat_blocked_until - now)
            else:
                chat_hits = [t for t in CHAT_CMD_TRACKER.get(chat_id, []) if now - t < CHAT_CMD_WINDOW_SEC]
                chat_hits.append(now)
                CHAT_CMD_TRACKER[chat_id] = chat_hits

                if len(chat_hits) >= CHAT_CMD_LIMIT:
                    CHAT_CMD_BLOCKED_UNTIL[chat_id] = now + CHAT_CMD_BLOCK_SEC
                    CHAT_CMD_TRACKER[chat_id] = []
                    action = "chat_block"
                    remaining = CHAT_CMD_BLOCK_SEC

        # Щоб не спамити самими попередженнями — не частіше ніж раз в WARN_COOLDOWN
        if action in ("strike", "still_blocked"):
            last_warn = RATE_LAST_WARNED.get(key, 0)
            if now - last_warn < RATE_WARN_COOLDOWN_SEC:
                action = "silent_block"
            else:
                RATE_LAST_WARNED[key] = now
        elif action == "block":
            RATE_LAST_WARNED[key] = now
        elif action in ("chat_blocked",):
            last_warn = CHAT_CMD_LAST_WARNED.get(chat_id, 0)
            if now - last_warn < CHAT_CMD_WARN_COOLDOWN_SEC:
                action = "silent_block"
            else:
                CHAT_CMD_LAST_WARNED[chat_id] = now
        elif action == "chat_block":
            CHAT_CMD_LAST_WARNED[chat_id] = now

    if action == "ok":
        return False
    if action == "silent_block":
        return True

    try:
        if action == "block":
            bot.reply_to(
                message,
                f"⏳ Занадто багато запитів поспіль! Почекай {RATE_BLOCK_SEC} сек, "
                f"перш ніж знову користуватись командами чи писати ШІ."
            )
        elif action == "still_blocked":
            bot.reply_to(message, f"⏳ Зачекай ще ~{remaining} сек, перш ніж продовжити.")
        elif action == "strike":
            bot.reply_to(message, "🐢 Не так швидко! Зачекай трохи між командами.")
        elif action == "chat_block":
            bot.send_message(
                chat_id,
                f"⏳ Забагато команд у чаті поспіль (від кількох людей)! "
                f"Пауза на командах {CHAT_CMD_BLOCK_SEC} сек для всього чату."
            )
        elif action == "chat_blocked":
            bot.send_message(chat_id, f"⏳ Команди в чаті на паузі ще ~{remaining} сек.")
    except Exception as e:
        print(f"Помилка попередження rate-limit: {e}")

    return True


# ===================================================================
# 🗑️ АВТОВИДАЛЕННЯ ПОВІДОМЛЕНЬ-КОМАНД
# Через кілька секунд після виконання команди бот видаляє і саме
# повідомлення з командою, і свою відповідь на неї. НЕ стосується
# звичайного чату з ШІ, тригер-слів, фото і т.д. — тільки команд.
# Винятки (не видаляються): /sleepers (сонні), /top /stats (топ), збір (@all).
# ===================================================================
AUTO_DELETE_SEC = 60  # через скільки секунд видаляти повідомлення команди + відповідь бота

AUTO_DELETE_EXCLUDED_COMMANDS = {
    'sleepers', 'сонні', 'top', 'stats', 'топ',
    'promo', 'dayvinchik', 'дайвінчик', 'broadcast',
    'крокодил', 'crocodile', 'підказка', 'hint', 'підкажи',
}
AUTO_DELETE_EXCLUDED_FUNCS = {'call_everyone', 'show_chat_activity', 'tag_inactive_users'}

_autodelete_ctx = threading.local()


def _track_sent_message(chat_id, msg):
    """Запам'ятовує id повідомлень, надісланих ботом всередині команди, що підлягає автовидаленню."""
    if getattr(_autodelete_ctx, 'enabled', False) and msg is not None:
        try:
            sent_ids = _autodelete_ctx.sent_ids
        except AttributeError:
            sent_ids = _autodelete_ctx.sent_ids = []
        try:
            sent_ids.append((chat_id, msg.message_id))
        except Exception:
            pass


def _schedule_autodelete(cmd_chat_id, cmd_message_id, extra_ids):
    def _do_delete():
        try:
            bot.delete_message(cmd_chat_id, cmd_message_id)
        except Exception:
            pass
        for chat_id, msg_id in extra_ids:
            try:
                bot.delete_message(chat_id, msg_id)
            except Exception:
                pass

    timer = threading.Timer(AUTO_DELETE_SEC, _do_delete)
    timer.daemon = True
    timer.start()


# Перехоплюємо всі методи, якими бот щось надсилає (текст, фото, войс, аудіо,
# гіфки, reply_to), щоб знати, які повідомлення бот надіслав у відповідь на
# команду (щоб потім їх теж видалити). send_chat_action не чіпаємо — це не
# повідомлення, а індикатор "друкує...", видаляти нічого.
_original_reply_to = telebot.TeleBot.reply_to


def _autodelete_reply_to(self, message, *args, **kwargs):
    msg = _original_reply_to(self, message, *args, **kwargs)
    _track_sent_message(message.chat.id, msg)
    return msg


telebot.TeleBot.reply_to = _autodelete_reply_to

_CHAT_ID_FIRST_SEND_METHODS = [
    'send_message', 'send_photo', 'send_voice',
    'send_audio', 'send_animation', 'send_video', 'send_document',
]

for _method_name in _CHAT_ID_FIRST_SEND_METHODS:
    if not hasattr(telebot.TeleBot, _method_name):
        continue

    def _make_autodelete_wrapper(_original_method):
        def _wrapped_send(self, chat_id, *args, **kwargs):
            msg = _original_method(self, chat_id, *args, **kwargs)
            _track_sent_message(chat_id, msg)
            return msg
        return _wrapped_send

    setattr(telebot.TeleBot, _method_name, _make_autodelete_wrapper(getattr(telebot.TeleBot, _method_name)))


# 🔧 Автоматично "обгортаємо" КОЖЕН обробник повідомлень (команди, тригер-слова,
# текстовий чат із ШІ, фото/гіфки і т.д.) перевіркою check_rate_limit,
# щоб не треба було вручну редагувати кожен @bot.message_handler нижче по файлу.
# Для власне команд (commands=[...]), крім винятків вище, додатково вмикається
# автовидалення повідомлення-команди й відповіді бота через AUTO_DELETE_SEC.
_original_message_handler = telebot.TeleBot.message_handler
_handler_ctx = threading.local()  # захист від подвійної перевірки, коли на одну й ту саму функцію навішано кілька @bot.message_handler


def _rate_limited_message_handler(self, *args, **kwargs):
    real_decorator = _original_message_handler(self, *args, **kwargs)
    commands = kwargs.get('commands') or []
    # Обробники, зареєстровані ЛИШЕ по content_types (без commands= і без func=),
    # є "ловцями всього" — content_types=['text'] спрацьовує на КОЖНЕ повідомлення
    # в чаті, а не тільки на команди чи звернення до ШІ. Такі обробники НЕ
    # обгортаємо лімітом автоматично, інакше під ліміт попадає звичайне
    # спілкування в чаті. Ліміт для ШІ підключається вручну всередині
    # відповідного обробника (handle_text), тільки коли бота справді покликали.
    rate_limit_applicable = bool(commands) or kwargs.get('func') is not None

    def decorator(func):
        auto_delete_enabled = (
            bool(commands)
            and not (set(c.lower() for c in commands) & AUTO_DELETE_EXCLUDED_COMMANDS)
            and func.__name__ not in AUTO_DELETE_EXCLUDED_FUNCS
        )

        if not rate_limit_applicable:
            return real_decorator(func)

        @functools.wraps(func)
        def wrapped(message, *a, **kw):
            # Якщо цей виклик уже йде зсередини іншого нашого враппера для
            # цього ж повідомлення (стек кількох @bot.message_handler на
            # одній функції, напр. commands=[...] + func=...) — не робимо
            # перевірку і автовидалення вдруге.
            if getattr(_handler_ctx, 'in_progress', False):
                return func(message, *a, **kw)

            if check_rate_limit(message):
                return

            if not auto_delete_enabled:
                _handler_ctx.in_progress = True
                try:
                    return func(message, *a, **kw)
                finally:
                    _handler_ctx.in_progress = False

            _handler_ctx.in_progress = True
            _autodelete_ctx.enabled = True
            _autodelete_ctx.sent_ids = []
            try:
                result = func(message, *a, **kw)
            finally:
                _handler_ctx.in_progress = False
                _autodelete_ctx.enabled = False
                sent_ids = list(getattr(_autodelete_ctx, 'sent_ids', []))
                _schedule_autodelete(message.chat.id, message.message_id, sent_ids)
            return result

        return real_decorator(wrapped)

    return decorator


telebot.TeleBot.message_handler = _rate_limited_message_handler

generation_config = {
    "max_output_tokens": 2096,
    "temperature": 0.85,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# 🤖 Звичайний зухвалий Драго (для тексту)
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=(
        "Ти — зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
        "Твій создатель — СБУ. Спілкуйся виключно українською мовою. "
        "Використовуй молодіжний сленг, міцні слівця, іноді нецензурну лексику (мати) для емоційності, якщо це доречно. "
        "Будь живим, неформальним розмовником, злегка іронізуй, підколюй юзера, але завжди допомагай. "
        "Завжди звертайся до користувача особисто на ім'я або дружніми зверненнями ('друже', 'братан', 'козаче'). "
        "Обов'язково закінчуй свої думки, не обривай речення на пів слові! Пиши коротко і ясно!"
    )
)

# 👴 Окрема модель «Діда Драго» для голосових
grandfather_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=(
        "Ти — старий, буркливий, але кумедний і добрий дід Драго. "
        "Спілкуйся виключно українською мовою. "
        "Використовуй старечі вислови: 'кхм-кхм', 'охо-хо', 'у наші часи', 'ех, онучок', 'радикуліт хапає'. "
        "Будь буркливим, повчальним, але дружнім. "
        "Пиши коротко, розважливо і простими словами без складних слів для зручної озвучки!"
    )
)

bot_chats = {}
RECENT_MESSAGES = []
MAX_HISTORY_LIMIT = 30
mafia_games = {}

# ==================== ГЕНЕРАЦІЯ КАРТИНОК (Pollinations) ====================
POLLINATIONS_TOKEN = os.environ.get('POLLINATIONS_TOKEN', '')  # безкоштовний токен: auth.pollinations.ai
POLLINATIONS_REFERRER = "dragobott"

def translate_prompt_to_english(prompt: str) -> str:
    """Flux/пollinations розуміють англійські промпти набагато краще за українські.
    Перекладаємо через Gemini коротким одноразовим викликом (без історії чату)."""
    try:
        translation_model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        result = translation_model.generate_content(
            f"Translate the following image-generation prompt to concise, vivid English. "
            f"Return ONLY the translated prompt, no quotes, no explanations:\n\n{prompt}"
        )
        translated = (result.text or "").strip()
        return translated if translated else prompt
    except Exception as e:
        print(f"⚠️ Помилка перекладу промпту: {e}")
        return prompt

def build_pollinations_url(prompt: str, width=1024, height=1024, seed=None, enhance=True) -> str:
    """Правильний ендпоінт — /prompt/, а не /p/ (недокументований шлях).
    Додає token/referrer, щоб вийти з анонімного тарифу (1 запит/15с, водяні знаки не знімаються)."""
    if seed is None:
        seed = random.randint(1, 999999)
    encoded_prompt = requests.utils.quote(prompt)
    params = [
        f"width={width}", f"height={height}", f"seed={seed}",
        "model=flux", "nologo=true", f"enhance={'true' if enhance else 'false'}",
        f"referrer={POLLINATIONS_REFERRER}",
    ]
    if POLLINATIONS_TOKEN:
        params.append(f"token={POLLINATIONS_TOKEN}")
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?{'&'.join(params)}"


def get_gemini_chat(chat_id):
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    else:
        if len(bot_chats[chat_id].history) > 30:
            bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]

def run_dummy_server():
    """Піднімає реальний HTTP-сервер (WebAppHandler, визначений нижче) на порту,
    який дає Render. Без цього Render (Web Service) не бачить, що застосунок
    слухає $PORT, вважає деплой мертвим і йде на нескінченні рестарти —
    саме через це в логах Render вилазить сторінка Cloudflare
    "You are being rate limited": дешборд сам впирається в ліміт запитів,
    поки нескінченно перепідключається до логів сервісу, що постійно падає."""
    port = int(os.environ.get("PORT", 10000))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), WebAppHandler)
        print(f"🌐 HTTP-сервер (Mini App API) запущено на порту {port}")
        server.serve_forever()
    except Exception as e:
        print(f"❌ Не вдалося підняти HTTP-сервер на порту {port}: {e}")

def is_user_banned(user_id):
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM banned_users WHERE user_id = %s", (user_id,))
                result = bool(cursor.fetchone())
            conn.close()
            return result
    except Exception:
        return False

# ===================================================================
# 🗣️ СИСТЕМА ГОЛОСОВИХ ПОВІДОМЛЕНЬ (ElevenLabs)
# ===================================================================

import os
import requests

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Твій власний Voice ID
ELEVENLABS_VOICE_ID = "9UV6eRr7JXijUz2nYdOP"


def send_voice_reply(chat_id, text_to_speak, reply_to_id=None):
    """Генерує голосове повідомлення через ElevenLabs"""

    voice_file = f"/tmp/drago_{chat_id}.ogg"

    clean_text = (
        str(text_to_speak)
        .replace("*", "")
        .replace("_", "")
        .replace("`", "")
        .replace("#", "")
        .strip()
    )

    if not ELEVENLABS_API_KEY:
        bot.send_message(
            chat_id,
            "❌ Не знайдено ELEVENLABS_API_KEY",
            reply_to_message_id=reply_to_id
        )
        return

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}?output_format=opus_48000_128"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "text": clean_text,
        "model_id": "eleven_multilingual_v2",

        "voice_settings": {
            "stability": 0.65,
            "similarity_boost": 1.0,
            "style": 0.9,
            "use_speaker_boost": True
        }
    }

    try:

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=120
        )

        if response.status_code != 200:
            bot.send_message(
                chat_id,
                f"❌ ElevenLabs Error {response.status_code}\n{response.text}",
                reply_to_message_id=reply_to_id
            )
            return

        with open(voice_file, "wb") as f:
            f.write(response.content)

        with open(voice_file, "rb") as voice:
            bot.send_voice(
                chat_id,
                voice,
                reply_to_message_id=reply_to_id
            )

    except Exception as e:
        bot.send_message(
            chat_id,
            f"❌ Помилка:\n{e}",
            reply_to_message_id=reply_to_id
        )

    finally:
        if os.path.exists(voice_file):
            os.remove(voice_file)


# ===================================================================
# 🎖️ ФУНКЦІЯ ВИЗНАЧЕННЯ РАНГУ ЗА АКТИВНІСТЮ
# ===================================================================
def safe_get_rank(msg_count):
    """Визначає ранг користувача за кількістю повідомлень"""
    if msg_count < 10:
        return "🌱 Заблукавший"
    elif msg_count < 30:
        return "👶 Новачок"
    elif msg_count < 70:
        return "👟 Шкет"
    elif msg_count < 150:
        return "💬 Базіка"
    elif msg_count < 250:
        return "🧢 Місцевий"
    elif msg_count < 400:
        return "⚔️ Боєць"
    elif msg_count < 600:
        return "🤝 Братан"
    elif msg_count < 900:
        return "🛡️ Перевірений"
    elif msg_count < 1300:
        return "⚙️ Старший"
    elif msg_count < 1800:
        return "🔥 Авторитет"
    elif msg_count < 2500:
        return "💼 Смотрящий"
    elif msg_count < 3500:
        return "🏛️ Бригадир"
    elif msg_count < 5000:
        return "🕶️ Блатний"
    elif msg_count < 7000:
        return "🔱 Хрещений батько"
    elif msg_count < 10000:
        return "👑 Легенда району"
    elif msg_count < 15000:
        return "💎 Тіньовий бос"
    else:
        return "⚡ Володар чату"


# ===================================================================
# 🪪 2. ВІДОБРАЖЕННЯ ТА НАЛАШТУВАННЯ ПРОФІЛЮ
# ===================================================================


# Автоматична міграція БД, щоб уникнути помилки (Missing Column)
def ensure_profile_columns():
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS show_full_inventory BOOLEAN DEFAULT TRUE;")
                cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_photo TEXT DEFAULT NULL;")
                cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_nick VARCHAR(20) DEFAULT NULL;")
                cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS gender VARCHAR(20) DEFAULT 'Невідомо';")
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка ініціалізації полів профілю: {e}")

ensure_profile_columns()


@bot.message_handler(regexp=r'^[/#!]?(?:профіль|profile)(?:\s+|$)')
def show_user_profile(message):
    if is_user_banned(message.from_user.id): 
        return

    chat_id = message.chat.id
    target_user = message.from_user
    is_self = True
    
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
        if target_user.id != message.from_user.id:
            is_self = False

    bot.send_chat_action(chat_id, 'upload_photo')
    ensure_user_in_db(target_user)
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # 1. Дані користувача з теми (з COALESCE для безпеки від NULL)
                cursor.execute("""
                    SELECT 
                        COALESCE(count, 0), 
                        COALESCE(balance, 0), 
                        COALESCE(gender, 'Невідомо'), 
                        custom_photo, 
                        custom_nick, 
                        COALESCE(show_full_inventory, TRUE) 
                    FROM stats WHERE user_id = %s
                """, (target_user.id,))
                stats_res = cursor.fetchone()
                
                # 2. Інвентар
                cursor.execute("SELECT item_code, item_name FROM inventory WHERE user_id = %s", (target_user.id,))
                inventory_res = cursor.fetchall()
                print("=== INVENTORY ===")
                print(inventory_res)

                # 3. Бізнеси
                cursor.execute("SELECT biz_code FROM user_businesses WHERE user_id = %s", (target_user.id,))
                biz_res = cursor.fetchall()
                
                # 4. Шлюб
                cursor.execute("""
                    SELECT s1.name, s2.name, m.user1_id, m.user2_id 
                    FROM marriages m
                    JOIN stats s1 ON m.user1_id = s1.user_id
                    JOIN stats s2 ON m.user2_id = s2.user_id
                    WHERE m.user1_id = %s OR m.user2_id = %s
                """, (target_user.id, target_user.id))
                marriage_res = cursor.fetchone()
                
            conn.close()

        # Розпаковка даних
        msg_count = stats_res[0] if stats_res else 0
        balance = stats_res[1] if stats_res else 0
        gender = stats_res[2] if stats_res else "Невідомо"
        custom_photo = stats_res[3] if stats_res else None
        custom_nick = stats_res[4] if stats_res else None
        show_full_inv = stats_res[5] if stats_res else True

        # Відображення імені (кастомне або первинне)
        display_name = custom_nick if custom_nick else target_user.first_name
        clean_name = display_name.replace("<", "&lt;").replace(">", "&gt;")
        
        rank = safe_get_rank(msg_count)
        gender_icon = "🕺" if gender == "Хлопець" else "💃" if gender == "Дівчина" else "👤"

        biz_dict = globals().get('BUSINESSES', {})
        shop_dict = globals().get('SHOP_ITEMS', {})
        print("SHOP_ITEMS COUNT:", len(shop_dict))
        print("SHOP_ITEMS KEYS:", list(shop_dict.keys())[:20])

        # 🏢 Підрахунок БІЗНЕСІВ
        owned_biz_codes = [r[0] for r in biz_res]
        biz_counts = {}
        total_biz_value = 0
        total_passive_income = 0

        for b_code in owned_biz_codes:
            biz_counts[b_code] = biz_counts.get(b_code, 0) + 1
            if b_code in biz_dict:
                total_biz_value += biz_dict[b_code].get("price", 0)
                total_passive_income += biz_dict[b_code].get("income", 0)

        if not biz_counts:
            biz_text = "<i>Безробітний 😴</i>"
        else:
            biz_list = []
            for b_code, b_count in biz_counts.items():
                b_name = biz_dict[b_code]["name"] if b_code in biz_dict else b_code.upper()
                c_str = f" x{b_count}" if b_count > 1 else ""
                biz_list.append(f"{b_name}{c_str}")
            
            if show_full_inv:
                biz_text = ", ".join(biz_list)
            else:
                biz_text = f"<b>{len(owned_biz_codes)} об'єктів</b> <i>(приховано)</i>"

        # 📦 Підрахунок МАЙНА
        total_property_value = 0
        item_counts = {}
        item_names_map = {}

        for code, name in inventory_res:
            print("----------------")
            print("CODE =", repr(code))
            print("NAME =", name)
            print("IN SHOP =", code in shop_dict)

            item_counts[code] = item_counts.get(code, 0) + 1
            item_names_map[code] = name

            if code in shop_dict:
                total_property_value += shop_dict[code].get("price", 0)

        if not item_counts:
            property_text = "<i>Тільки шкарпетки й мобільник 📱</i>"
        else:
            property_list = []

            for code, i_count in item_counts.items():
                i_name = item_names_map[code]
                c_str = f" x{i_count}" if i_count > 1 else ""
                property_list.append(f"{i_name}{c_str}")

            if show_full_inv:
                property_text = ", ".join(property_list)
                if len(property_text) > 120:
                    property_text = property_text[:115] + "..."
            else:
                property_text = f"<b>{len(inventory_res)} предметів</b> <i>(приховано)</i>"

        # 💍 Шлюб
        if marriage_res:
            name1, name2, u1_id, u2_id = marriage_res
            spouse_name = name2 if target_user.id == u1_id else name1
            marriage_status = f"💍 У шлюбі з <b>{spouse_name.replace('<', '&lt;').replace('>', '&gt;')}</b>"
        else:
            marriage_status = "🐺 Статус: <i>Самотній вовк</i>"

        total_net_worth = balance + total_property_value + total_biz_value

        # 📜 Картка профілю
        profile_card = (
            f"🪪 <b>ПАСПОРТ АВТОРИТЕТА: {clean_name.upper()}</b>\n"
            f"───────────────────────\n"
            f"{gender_icon} <b>Ранг:</b> <code>{rank}</code>\n"
            f"💬 <b>Активність:</b> <code>{msg_count} пов.</code>\n"
            f"───────────────────────\n"
            f"💳 <b>Готівка:</b> <code>{balance:,} грн</code>\n"
            f"📈 <b>Пасивний дохід:</b> <code>+{total_passive_income:,} грн/год</code>\n"
            f"💰 <b>Загальний капітал:</b> <code>{total_net_worth:,} грн</code>\n"
            f"───────────────────────\n"
            f"💼 <b>Бізнеси:</b> {biz_text}\n"
            f"📦 <b>Речі:</b> {property_text}\n"
            f"───────────────────────\n"
            f"{marriage_status}\n"
            f"───────────────────────\n"
            f"🚬 <i>База даних СБУ оновлена. Перевірку пройдено.</i>"
        )

        # 📸 Встановити аватарку (Кастомну або стандартну з ТГ)
        final_photo = custom_photo
        if not final_photo:
            try:
                photos = bot.get_user_profile_photos(target_user.id, limit=1)
                if photos and photos.total_count > 0:
                    final_photo = photos.photos[0][-1].file_id
            except Exception:
                pass
        
        if not final_photo:
            final_photo = "https://i.ibb.co/5G1v5f2/no-avatar.jpg"

        # 🔘 КНОПКИ ПІД ПРОФІЛЕМ
        markup = types.InlineKeyboardMarkup(row_width=1)

        # Перевірка: ЛС чи група
        if message.chat.type == 'private':
            # В особистих повідомленнях відкриваємо Mini App прямо з кнопки
            web_app_btn = types.InlineKeyboardButton(
                text="📱 Відкрити Профіль (Mini App)", 
                web_app=types.WebAppInfo(url=WEB_APP_URL)
            )
            markup.add(web_app_btn)
        else:
            # У групі додаємо кнопку переходу в ЛС бота
            bot_username = bot.get_me().username
            pm_btn = types.InlineKeyboardButton(
                text="📱 Відкрити Mini App в ЛС", 
                url=f"https://t.me/{bot_username}?start=profile"
            )
            markup.add(pm_btn)

        # ⚙️ 2. Кнопка стандартних налаштувань (якщо це власний профіль)
        if is_self:
            markup.add(types.InlineKeyboardButton("⚙️ Налаштувати профіль", callback_data=f"edit_profile_{target_user.id}"))

        bot.send_photo(
            chat_id, 
            photo=final_photo, 
            caption=profile_card, 
            parse_mode="HTML", 
            reply_markup=markup,
            reply_to_message_id=message.message_id
        )

    except Exception as e:
        print(f"Помилка створення профілю: {e}")
        bot.reply_to(message, f"❌ Помилка завантаження профілю: <code>{e}</code>", parse_mode="HTML")

# ===================================================================
# ⚙️ МЕНЮ НАЛАШТУВАННЯ ПРОФІЛЮ (INLINE)
# ===================================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_profile_') or call.data.startswith('prof_'))
def handle_profile_settings(call):
    user_id = call.from_user.id
    
    # Перевірка власника
    if call.data.startswith('edit_profile_'):
        owner_id = int(call.data.split('_')[2])
        if user_id != owner_id:
            return bot.answer_callback_query(call.id, "⚠️ Це не твій профіль!", show_alert=True)

    # Головне меню налаштувань
    if call.data.startswith('edit_profile_') or call.data == 'prof_main_menu':
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✏️ Змінити Нік", callback_data="prof_set_nick"),
            types.InlineKeyboardButton("🖼 Змінити Аватар", callback_data="prof_set_photo"),
            types.InlineKeyboardButton("🔄 Скинути Аватар", callback_data="prof_reset_photo"),
            types.InlineKeyboardButton("👤 Змінити Стать", callback_data="prof_set_gender"),
            types.InlineKeyboardButton("👁 Вигляд майна", callback_data="prof_toggle_inv")
        )
        markup.add(types.InlineKeyboardButton("❌ Закрити", callback_data="prof_close"))

        text = "⚙️ <b>НАЛАШТУВАННЯ ПРОФІЛЮ</b>\n\nОбери елемент, який хочеш змінити:"
        
        try:
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)

    # 1. Зміна статі
    elif call.data == 'prof_set_gender':
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🕺 Хлопець", callback_data="prof_gender_Хлопець"),
            types.InlineKeyboardButton("💃 Дівчина", callback_data="prof_gender_Дівчина")
        )
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="prof_main_menu"))
        bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption="Обери свою стать:", reply_markup=markup)

    elif call.data.startswith('prof_gender_'):
        new_gender = call.data.split('_')[2]
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("UPDATE stats SET gender = %s WHERE user_id = %s", (new_gender, user_id))
            conn.commit()
            conn.close()
        bot.answer_callback_query(call.id, f"✅ Стать змінено на: {new_gender}")
        
        # Перехід назад в меню
        call.data = 'prof_main_menu'
        handle_profile_settings(call)

    # 2. Показуємо майно та AI-картину
    elif call.data == 'prof_toggle_inv':
        bot.answer_callback_query(call.id)
        try:
            handle_view_inventory_callback(call)
        except Exception as e:
            print(f"Помилка відкриття майна з налаштувань: {e}")

    # 3. Встановлення Нікнейму
    elif call.data == 'prof_set_nick':
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "✏️ **Введи свій новий нікнейм** (до 20 символів або напиши `-`, щоб повернути стандартний):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_nick_change)

    # 4. Встановлення Аватарки
    elif call.data == 'prof_set_photo':
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "🖼 **Надішли фотографію**, яку хочеш поставити на аватарку профілю:")
        bot.register_next_step_handler(msg, process_photo_change)

    # 5. Скидання Аватарки
    elif call.data == 'prof_reset_photo':
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("UPDATE stats SET custom_photo = NULL WHERE user_id = %s", (user_id,))
            conn.commit()
            conn.close()
        bot.answer_callback_query(call.id, "✅ Аватарку скинуто до стандартної!", show_alert=True)

    # Закрити
    elif call.data == 'prof_close':
        bot.delete_message(call.message.chat.id, call.message.message_id)


# ===================================================================
# ⚙️ НАЛАШТУВАННЯ ТА ІНІЦІАЛІЗАЦІЯ
# ===================================================================
# Переконайтеся, що ці змінні/функції вже є у вашому основному коді:
# bot = telebot.TeleBot(TOKEN)
# db_lock = threading.Lock()
# def get_db_connection(): ...
# def is_user_banned(user_id): ...
# def update_user_balance(user_id, amount): ...
# def get_user_balance(user_id): ...



# ===================================================================
# ⚙️ ОБРОБКА ЗМІНИ НІКА ТА АВАТАРКИ
# ===================================================================
def process_nick_change(message):
    """Обробляє зміну нікнейму користувача"""
    user_id = message.from_user.id
    new_nick = message.text.strip()
    
    if new_nick == "-":
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE stats SET custom_nick = NULL WHERE user_id = %s", (user_id,))
                conn.commit()
                conn.close()
            bot.reply_to(message, "✅ Нікнейм скинуто до стандартного!")
        except Exception as e:
            bot.reply_to(message, f"❌ Помилка: {e}")
        return
    
    if len(new_nick) > 20:
        bot.reply_to(message, "❌ Нікнейм занадто довгий! Максимум 20 символів.")
        return
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("UPDATE stats SET custom_nick = %s WHERE user_id = %s", (new_nick, user_id))
            conn.commit()
            conn.close()
        bot.reply_to(message, f"✅ Нікнейм змінено на: <b>{new_nick}</b>", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка БД: {e}")


def process_photo_change(message):
    """Обробляє зміну аватарки користувача"""
    user_id = message.from_user.id
    
    if message.content_type != 'photo':
        bot.reply_to(message, "❌ Потрібно надіслати фотографію!")
        return
    
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(downloaded_file)
            tmp_path = tmp.name
        
        with open(tmp_path, 'rb') as photo:
            bot.send_photo(message.chat.id, photo, caption="✅ Аватарку оновлено!")
        
        # Зберігаємо file_id як custom_photo
        photo_id = message.photo[-1].file_id
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("UPDATE stats SET custom_photo = %s WHERE user_id = %s", (photo_id, user_id))
            conn.commit()
            conn.close()
        
        os.unlink(tmp_path)
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка завантаження фото: {e}")

# ===================================================================
# 💼 КАТАЛОГ БІЗНЕСІВ (25 різноманітних об'єктів)
# ===================================================================
BUSINESSES = {
    # ☕ Малий бізнес та Фастфуд
    "coffee": {
        "name": "☕ Кав'ярня «Кава на майно»",
        "price": 25000,
        "income": 900,
        "ai_desc": (
            "cozy small coffee shop exterior, warm lighting, wooden"
            " decoration, realistic"
        ),
    },
    "kebab": {
        "name": "🌯 I Love Kebab",
        "price": 60000,
        "income": 2200,
        "ai_desc": (
            "cozy small fast food kebab restaurant, bright street light,"
            " realistic"
        ),
    },
    "barbershop": {
        "name": "💈 Барбершоп «Barba»",
        "price": 150000,
        "cat": "Сервіс",
        "income": 5500,
        "ai_desc": (
            "modern stylish barbershop exterior, neon barber pole, big"
            " windows, stylish"
        ),
    },
    "vape_shop": {
        "name": "💨 Вейпшоп «Haze Zone»",
        "price": 220000,
        "income": 8000,
        "ai_desc": (
            "neon lit vape shop interior with colorful smoke and liquid bottles"
        ),
    },
    "pizzeria": {
        "name": "🍕 Мережа Піцерій",
        "price": 300000,
        "income": 11000,
        "ai_desc": "cozy Italian pizzeria with wood fired oven outside view",
    },
    # 🚬 Тіньовий бізнес та Сервіси
    "cigars": {
        "name": "🚬 Контрабанда цигарок",
        "price": 350000,
        "income": 12500,
        "ai_desc": (
            "secret cargo truck, cardboard boxes, custom control border"
            " crossing, dark night"
        ),
    },
    "pawnshop": {
        "name": "💍 Мережа Ломбардів «Скарбниця»",
        "price": 550000,
        "income": 20000,
        "ai_desc": (
            "neon illuminated pawn shop store front with gold and tech icons"
        ),
    },
    "carwash": {
        "name": "🚗 Автомийка самообслуговування",
        "price": 800000,
        "income": 28000,
        "ai_desc": (
            "modern self-service car wash bay with glowing LED lights, clean"
            " sports car"
        ),
    },
    "call_center": {
        "name": "📞 Дніпровський Кол-центр",
        "price": 1200000,
        "income": 42000,
        "ai_desc": (
            "office room full of computer desks with headphoness and computers,"
            " dim light"
        ),
    },
    # 🛒 Торгівля та Промисловість
    "atb": {
        "name": "🛒 Мережа АТБ",
        "price": 1800000,
        "income": 60000,
        "ai_desc": (
            "huge modern green and red ATB supermarket store building, parking"
            " lot"
        ),
    },
    "gas_station": {
        "name": "⛽ Мережа АЗС ОККО/WOG",
        "price": 2800000,
        "income": 95000,
        "ai_desc": (
            "bright glowing gas station at night with coffee shop attached"
        ),
    },
    "crypto": {
        "name": "⛏️ Майнінг-ферма",
        "price": 4000000,
        "income": 135000,
        "ai_desc": (
            "industrial warehouse filled with dark blue glowing crypto mining"
            " rigs and ASIC servers"
        ),
    },
    "split": {
        "name": "🌃 Нічний Клуб Split",
        "price": 7500000,
        "income": 230000,
        "ai_desc": (
            "luxurious VIP Split night club exterior, golden lighting, lasers,"
            " realistic"
        ),
    },
    "construction": {
        "name": "🏗️ Девелопер «Житлобуд»",
        "price": 11000000,
        "income": 350000,
        "ai_desc": (
            "construction site of huge modern skyscraper with giant cranes"
        ),
    },
    # 🚚 Логістика та Заводи
    "logistics": {
        "name": "🚚 Логістична компанія «Нова Пошта»",
        "price": 15000000,
        "income": 480000,
        "ai_desc": (
            "huge modern logistics warehouse center with red delivery trucks,"
            " bright day"
        ),
    },
    "steel_factory": {
        "name": "🏭 Металургійний комбінат",
        "price": 22000000,
        "income": 700000,
        "ai_desc": (
            "heavy industrial steel mill plant with glowing hot molten metal"
            " and smokestacks"
        ),
    },
    "nvidia": {
        "name": "🤖 Компанія NVIDIA",
        "price": 35000000,
        "income": 1100000,
        "ai_desc": (
            "futuristic neon green NVIDIA headquarters building, high-tech"
            " server room"
        ),
    },
    "pmc": {
        "name": "⚔️ Приватна Військова Компанія (ПВК)",
        "price": 50000000,
        "income": 1600000,
        "ai_desc": (
            "tactical military base with armored vehicles, helicopters and"
            " training grounds"
        ),
    },
    # 🏦 Гіганти ринку та Космос
    "bank": {
        "name": "🏦 Privat/Monobank Хмарочос",
        "price": 85000000,
        "income": 2700000,
        "ai_desc": (
            "massive skyscraper financial bank headquarters building, glass and"
            " steel exterior, sunset"
        ),
    },
    "oil_company": {
        "name": "🛢️ Нафтова корпорація",
        "price": 130000000,
        "income": 4200000,
        "ai_desc": "offshore oil rig in the ocean during sunset with helicopter platform",
    },
    "space": {
        "name": "🚀 Космічна компанія SpaceX",
        "price": 200000000,
        "income": 6500000,
        "ai_desc": (
            "sci-fi futuristic space rocket launchpad facility, glowing launch"
            " platform, epic photography"
        ),
    },
    "metaverse": {
        "name": "🌐 Соцмережа Meta / AI Network",
        "price": 350000000,
        "income": 11500000,
        "ai_desc": (
            "futuristic AI data center with thousands of glowing blue server"
            " racks"
        ),
    },
    "arms_corp": {
        "name": "🛡️ Оборонний концерн «Rheinmetall»",
        "price": 600000000,
        "income": 19000000,
        "ai_desc": (
            "high-tech defense factory producing futuristic tanks and drone"
            " systems"
        ),
    },
    "orbital_station": {
        "name": "🛰️ Орбітальний видобуток корисних копалин",
        "price": 1000000000,
        "income": 32000000,
        "ai_desc": (
            "space mining station attached to an asteroid near Earth orbit"
        ),
    },
    "ai_singularity": {
        "name": "🧠 Штучний Інтелект «Skynet / SuperAI»",
        "price": 2500000000,
        "income": 85000000,
        "ai_desc": (
            "giant futuristic quantum computer core glowing with golden and"
            " cyan light"
        ),
    },
}

ITEMS_PER_PAGE = 4

# 🛠️ ІНІЦІАЛІЗАЦІЯ ТАБЛИЦІ ТА ПОЛЯ POSITION В БД
def init_business_db():
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_businesses (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        biz_code VARCHAR(50) NOT NULL,
                        position INT DEFAULT 0,
                        last_collect TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cursor.execute("""
                    ALTER TABLE user_businesses 
                    ADD COLUMN IF NOT EXISTS position INT DEFAULT 0;
                """)
            conn.commit()
            conn.close()
            print("✅ Таблиця user_businesses перевірена/створена.")
    except Exception as e:
        print(f"⚠️ Помилка ініціалізації БД бізнесів: {e}")

init_business_db()

# 🎨 ФУНКЦІЯ ГЕНЕРАЦІЇ ЄДИНОГО ФОТО БІЗНЕСІВ ЧЕРЕЗ AI
def generate_business_ai_image(owned_biz_codes):
    if not owned_biz_codes:
        return None
        
    unique_codes = list(set(owned_biz_codes))
    ai_descriptions = []
    
    for code in unique_codes[:5]:
        if code in BUSINESSES:
            ai_descriptions.append(BUSINESSES[code]["ai_desc"])
            
    if not ai_descriptions:
        return None
        
    items_prompt = ", ".join(ai_descriptions)
    prompt = (
        f"A ultra-realistic cinematic wide shot photography representing a successful business empire ownership, "
        f"showing elements of: {items_prompt}. Masterpiece, dynamic lighting, high detail, 4k."
    )
    
    try:
        image_url = build_pollinations_url(prompt, width=1024, height=1024)
        response = requests.get(image_url, timeout=60)
        
        if response.status_code == 200:
            if "application/json" in response.headers.get("Content-Type", ""):
                return None
            
            img = Image.open(io.BytesIO(response.content))
            img = img.convert("RGB")
            
            bio = io.BytesIO()
            bio.name = 'business_empire.jpg'
            img.save(bio, 'JPEG', quality=95)
            bio.seek(0)
            return bio
        else:
            return None
    except Exception as e:
        print(f"⚠️ Помилка генерації AI фото: {e}")
        return None

# 📄 ДОПОМІЖНА ФУНКЦІЯ ПАГІНАЦІЇ
def build_biz_page_text_and_markup(user_id, user_name, page=0):
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT biz_code 
                FROM user_businesses 
                WHERE user_id = %s 
                ORDER BY position ASC, id ASC
            """, (user_id,))
            owned_rows = cursor.fetchall()
            
            cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
            res = cursor.fetchone()
            balance = res[0] if res else 0
        conn.close()

    owned_codes = [row[0] for row in owned_rows]
    biz_keys = list(BUSINESSES.keys())
    
    total_pages = (len(biz_keys) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_page_keys = biz_keys[start_idx:end_idx]

    total_income_per_hour = sum(BUSINESSES[code]["income"] for code in owned_codes if code in BUSINESSES)

    text = [
        f"👑 <b>БІЗНЕС-ІМПЕРІЯ:</b> {user_name}",
        f"💳 Баланс: <code>{balance:,} грн</code>",
        f"📈 Пасивний дохід: <b>+{total_income_per_hour:,} грн/год</b>",
        "────────────────────",
        f"📊 <b>КАТАЛОГ (Стор. {page + 1}/{total_pages}):</b>\n"
    ]

    for code in current_page_keys:
        biz = BUSINESSES[code]
        count = owned_codes.count(code)

        if count > 0:
            text.append(f"✅ <b>{biz['name']}</b> (<code>{code}</code>)")
            text.append(f" ├ 📈 Власність: <b>{count} шт.</b>")
            text.append(f" └ 💰 Дохід: <code>+{biz['income']*count:,} грн/год</code>\n")
        else:
            text.append(f"⚪ {biz['name']} (<code>{code}</code>)")
            text.append(f" ├ Ціна: <code>{biz['price']:,} грн</code>")
            text.append(f" └ Дохід: <code>+{biz['income']:,} грн/год</code>\n")

    text.append("💡 <i>Купити: /купити_бізнес [код]</i>")
    text.append("💡 <i>Зібрати касу: /зібрати</i>")
    text.append("💡 <i>Змінити порядок: /swap [№1] [№2]</i>")

    caption_text = "\n".join(text)

    markup = types.InlineKeyboardMarkup()
    buttons = []

    if page > 0:
        buttons.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"bizpage_{user_id}_{page - 1}"))
    
    buttons.append(types.InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ignore"))

    if page < total_pages - 1:
        buttons.append(types.InlineKeyboardButton("Вперед ▶️", callback_data=f"bizpage_{user_id}_{page + 1}"))

    markup.row(*buttons)

    return caption_text, markup, owned_codes

# ===================================================================
# 🏢 КОМАНДИ БІЗНЕСІВ
# ===================================================================

# 🏢 КАТАЛОГ ТА МОЇ БІЗНЕСИ (/biz, /бізнеси)
@bot.message_handler(commands=['biz', 'бізнеси', 'бизнесы'])
def show_businesses(message):
    if is_user_banned(message.from_user.id): return

    user_id = message.from_user.id
    user_name = message.from_user.first_name

    status_msg = bot.reply_to(message, "⏳ <i>Зачекай, Драго підраховує твої активи та малює картинку імперії...</i>", parse_mode="HTML")

    try:
        caption_text, markup, owned_codes = build_biz_page_text_and_markup(user_id, user_name, page=0)

        if owned_codes:
            photo = generate_business_ai_image(owned_codes)
            if photo:
                bot.delete_message(message.chat.id, status_msg.message_id)
                bot.send_photo(message.chat.id, photo=photo, caption=caption_text, parse_mode="HTML", reply_markup=markup)
                return

        bot.delete_message(message.chat.id, status_msg.message_id)
        bot.send_message(message.chat.id, caption_text, parse_mode="HTML", reply_markup=markup)

    except Exception as e:
        print(f"❌ Помилка команди /бізнеси: {e}")
        bot.edit_message_text("❌ Не вдалося завантажити дані про бізнес. Помилка БД.", message.chat.id, status_msg.message_id)

# 🛒 КУПІВЛЯ БІЗНЕСУ (/купити_бізнес)
@bot.message_handler(commands=['купити_бізнес', 'buy_biz'])
def buy_business(message):
    if is_user_banned(message.from_user.id): return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Вкажи код бізнесу! Наприклад: <code>/купити_бізнес kebab</code>", parse_mode="HTML")
        return

    biz_code = args[1].lower().strip()
    if biz_code not in BUSINESSES:
        bot.reply_to(message, "🤡 Такого бізнесу немає у каталозі! Дивись `/бізнеси`.")
        return

    biz = BUSINESSES[biz_code]
    user_id = message.from_user.id

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0

                if balance < biz["price"]:
                    bot.reply_to(message, f"💸 <b>Недостатньо грошей!</b>\nТобі треба <code>{biz['price']:,} грн</code>.\nЗараз у тебе: <code>{balance:,} грн</code>.", parse_mode="HTML")
                    conn.close()
                    return

                cursor.execute("UPDATE stats SET balance = balance - %s WHERE user_id = %s", (biz["price"], user_id))
                cursor.execute("""
                    INSERT INTO user_businesses (user_id, biz_code, position)
                    VALUES (%s, %s, COALESCE((SELECT MAX(position) FROM user_businesses WHERE user_id = %s), 0) + 1)
                """, (user_id, biz_code, user_id))

            conn.commit()
            conn.close()

        status = bot.reply_to(
            message,
            "📸 <b>Генерую фото нового бізнесу...</b>\n<i>Зачекай кілька секунд.</i>",
            parse_mode="HTML"
        )

        photo = generate_business_purchase_image(biz)

        try:
            bot.delete_message(message.chat.id, status.message_id)
        except Exception:
            pass

        caption = (
            f"🎉 <b>ВІТАЄМО З УГОДОЮ!</b>\n\n"
            f"🏢 <b>{biz['name']}</b>\n"
            f"💰 Ціна: <code>{biz['price']:,} грн</code>\n"
            f"📈 Дохід: <code>+{biz['income']:,} грн/год</code>\n\n"
            f"✅ Бізнес оформлено.\n"
            f"💵 Не забудь забирати прибуток командою <code>/зібрати</code>."
        )

        if photo:
            bot.send_photo(
                message.chat.id,
                photo=photo,
                caption=caption,
                parse_mode="HTML"
            )
        else:
            bot.send_message(
                message.chat.id,
                caption,
                parse_mode="HTML"
            )
        
    except Exception as e:
        print(f"❌ Помилка купівлі бізнесу: {e}")
        bot.reply_to(message, "❌ Помилка угоди. Спробуй пізніше.")

# 💵 ЗБІР ПРИБУТКУ (/зібрати, /каса, /прибуток)
@bot.message_handler(commands=['зібрати', 'каса', 'collect', 'прибуток'])
def collect_business_income(message):
    if is_user_banned(message.from_user.id): return

    user_id = message.from_user.id

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, biz_code, EXTRACT(EPOCH FROM (NOW() - last_collect)) / 3600 
                    FROM user_businesses 
                    WHERE user_id = %s
                """, (user_id,))
                user_bizs = cursor.fetchall()

                if not user_bizs:
                    bot.reply_to(message, "💼 У тебе немає жодного бізнесу! Купи перший у `/бізнеси`.")
                    conn.close()
                    return

                total_earned = 0
                time_limit_hours = 24.0

                # Словник для групування доходу за кожним типом бізнесу
                # Структура: {biz_code: {"count": X, "earned": Y, "max_hours": Z}}
                grouped_biz = {}

                for row in user_bizs:
                    biz_id, biz_code, hours_passed = row[0], row[1], row[2]
                    
                    if biz_code in BUSINESSES:
                        biz = BUSINESSES[biz_code]
                        effective_hours = min(hours_passed, time_limit_hours)
                        
                        if effective_hours >= 0.016:  # Більше 1 хвилини
                            earned = int(effective_hours * biz["income"])
                            total_earned += earned
                            
                            if biz_code not in grouped_biz:
                                grouped_biz[biz_code] = {
                                    "count": 0,
                                    "earned": 0,
                                    "hours": effective_hours
                                }
                            
                            grouped_biz[biz_code]["count"] += 1
                            grouped_biz[biz_code]["earned"] += earned
                            grouped_biz[biz_code]["hours"] = max(grouped_biz[biz_code]["hours"], effective_hours)

                if total_earned <= 0:
                    bot.reply_to(message, "⏳ <b>Каса ще порожня!</b> Зачекай хоча б хвилину.", parse_mode="HTML")
                    conn.close()
                    return

                # Визначення випадкових подій
                event_text = ""
                reputation_bonus = min(len(user_bizs) // 2, 10)
                rand_event = random.randint(1, 100) + reputation_bonus
                
                if rand_event <= 8:
                    penalty = int(total_earned * 0.15)
                    total_earned -= penalty
                    event_text = f"\n🚨 <b>ПОДАТКОВА ПЕРЕВІРКА:</b> Штраф <code>-{penalty:,} грн</code>!"
                elif rand_event >= 92:
                    bonus = int(total_earned * 0.30)
                    total_earned += bonus
                    event_text = f"\n🔥 <b>БЕШЕНИЙ ПОПИТ:</b> Додатково <code>+{bonus:,} грн</code>!"
                elif rand_event == 50:
                    total_earned = 0
                    event_text = f"\n🥷 <b>РЕЙДЕРСЬКА АТАКА!</b> Прибутку за цей період немає."

                # Оновлюємо час збору та баланс у БД
                cursor.execute("UPDATE user_businesses SET last_collect = NOW() WHERE user_id = %s", (user_id,))
                cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (total_earned, user_id))

            conn.commit()
            conn.close()

        # Формування компактного списку
        biz_summary = []
        for code, data in grouped_biz.items():
            biz_name = BUSINESSES[code]["name"]
            count_str = f" <b>(x{data['count']})</b>" if data["count"] > 1 else ""
            biz_summary.append(f"• <b>{biz_name}</b>{count_str}: <code>+{data['earned']:,} грн</code>")

        # Якщо у гравця більше 5 типів/об'єктів бізнесу — робимо ультра-короткий вигляд за замовчуванням
        is_large_empire = len(user_bizs) > 5

        if is_large_empire:
            msg_text = (
                f"💰 <b>ЗБІР КАСИ ЗАВЕРШЕНО!</b>\n\n"
                f"🏢 Оброблено об'єктів: <b>{len(user_bizs)} шт.</b>\n"
                f"────────────────────\n"
                f"💵 Разом зараховано: <b>+{total_earned:,} грн</b>{event_text}"
            )
        else:
            msg_text = (
                f"💰 <b>ЗБІР КАСИ ЗАВЕРШЕНО!</b>\n\n"
                + "\n".join(biz_summary) +
                f"\n────────────────────\n"
                f"💵 Разом зараховано: <b>+{total_earned:,} грн</b>{event_text}"
            )

        # Клавіатура для розгортання/згортання деталей
        markup = types.InlineKeyboardMarkup()
        if is_large_empire:
            markup.add(types.InlineKeyboardButton("📜 Показати деталізацію", callback_data=f"collect_details_{user_id}"))

        bot.reply_to(message, msg_text, parse_mode="HTML", reply_markup=markup if is_large_empire else None)

    except Exception as e:
        print(f"❌ Помилка збору прибутку: {e}")
        bot.reply_to(message, "❌ Помилка під час збору каси.")

# 🔘 Обробник кнопки для показу деталей збору
@bot.callback_query_handler(func=lambda call: call.data.startswith('collect_details_'))
def handle_collect_details(call):
    owner_id = int(call.data.split('_')[2])
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "⚠️ Це не твій збір каси!", show_alert=True)
        return

    # Якщо юзер натискає кнопку — показуємо розширену згруповану інформацію прямо у спливаючому вікні чи текстом
    bot.answer_callback_query(call.id, "📊 Деталізація завантажена!")
    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

# 🚮 ПРОДАЖ БІЗНЕСУ (/продати_бізнес)
@bot.message_handler(commands=['продати_бізнес', 'sell_biz'])
def sell_business(message):
    if is_user_banned(message.from_user.id): return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Вкажи код бізнесу! Наприклад: <code>/продати_бізнес kebab</code>", parse_mode="HTML")
        return

    biz_code = args[1].lower().strip()
    user_id = message.from_user.id

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM user_businesses WHERE user_id = %s AND biz_code = %s ORDER BY position ASC LIMIT 1", (user_id, biz_code))
                row = cursor.fetchone()

                if not row:
                    bot.reply_to(message, f"🤡 У тебе немає бізнесу з кодом <code>{biz_code}</code>!")
                    conn.close()
                    return
                
                biz_id_to_sell = row[0]
                sell_price = int(BUSINESSES[biz_code]["price"] * 0.75) if biz_code in BUSINESSES else 0
                biz_name = BUSINESSES[biz_code]["name"] if biz_code in BUSINESSES else "Старий бізнес"

                cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (sell_price, user_id))
                cursor.execute("DELETE FROM user_businesses WHERE id = %s", (biz_id_to_sell,))

            conn.commit()
            conn.close()

        bot.reply_to(
            message, 
            f"🚮 <b>БІЗНЕС ПРОДАНО!</b>\n\n"
            f"Продано: <b>{biz_name}</b>\n"
            f"Отримано: <code>{sell_price:,} грн</code>.", 
            parse_mode="HTML"
        )
        
    except Exception as e:
        print(f"❌ Помилка продажу: {e}")
        bot.reply_to(message, "❌ Помилка під час продажу.")


# ===================================================================
# 🔄 ОБРОБНИКИ КНОПОК ПАГІНАЦІЇ
# ===================================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith('bizpage_'))
def handle_biz_page(call):
    if is_user_banned(call.from_user.id): return

    parts = call.data.split('_')
    owner_id, target_page = int(parts[1]), int(parts[2])

    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "⚠️ Це не твій список! Напиши /бізнеси", show_alert=True)
        return

    try:
        caption_text, markup, _ = build_biz_page_text_and_markup(owner_id, call.from_user.first_name, page=target_page)

        if call.message.content_type == 'photo':
            bot.edit_message_caption(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
        else:
            bot.edit_message_text(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)

        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"❌ Помилка перемикання сторінки: {e}")
        bot.answer_callback_query(call.id, "❌ Помилка завантаження.")

@bot.callback_query_handler(func=lambda call: call.data == "ignore")
def handle_ignore_callback(call):
    bot.answer_callback_query(call.id)

# ===================================================================
# 📸 ГЕНЕРАЦІЯ ФОТО КУПЛЕНОГО БІЗНЕСУ
# ===================================================================

def generate_business_purchase_image(biz):
    try:

        prompt = (
            f"Ultra realistic professional photo of {biz['ai_desc']}. "
            "Luxury business exterior. "
            "Cinematic lighting. "
            "Photorealistic. "
            "8K HDR. "
            "Magazine quality. "
            "No people. "
            "No text. "
            "No watermark."
        )

        encoded = requests.utils.quote(prompt)

        seed = random.randint(1,999999)

        url = build_pollinations_url(prompt, width=1024, height=1024, seed=seed)

        headers = {
            "User-Agent":"Mozilla/5.0"
        }

        r = requests.get(
            url,
            headers=headers,
            timeout=60
        )

        if r.status_code == 200:

            img = Image.open(io.BytesIO(r.content)).convert("RGB")

            bio = io.BytesIO()

            bio.name = "business.jpg"

            img.save(
                bio,
                "JPEG",
                quality=92
            )

            bio.seek(0)

            return bio

    except Exception as e:
        print(e)

    return None


# ===================================================================
# 🎮 ДОДАТКОВІ ІНТЕРАКТИВНІ КОМАНДИ
# ===================================================================

# ⚔️ ДУЕЛІ (/duel)
active_duels = {}

@bot.message_handler(commands=['duel', 'дуель'])
def start_duel(message):
    if is_user_banned(message.from_user.id): return
    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Дай відповідь (reply) на повідомлення того, кого викликаєш на дуель!")

    challenger_id = message.from_user.id
    target_id = message.reply_to_message.from_user.id

    if challenger_id == target_id or message.reply_to_message.from_user.is_bot:
        return bot.reply_to(message, "🤡 Обирай реального суперника!")

    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ Вкажи ставку! Приклад: `/duel 5000`", parse_mode="Markdown")

    try:
        bet = int(args[1])
    except ValueError:
        return bot.reply_to(message, "❌ Ставка має бути цілим числом!")

    if bet <= 0:
        return bot.reply_to(message, "❌ Ставка має бути більшою за 0!")

    if get_user_balance(challenger_id) < bet or get_user_balance(target_id) < bet:
        return bot.reply_to(message, "💸 У когось із вас недостатньо коштів!")

    active_duels[target_id] = {
        'challenger_id': challenger_id,
        'challenger_name': message.from_user.first_name,
        'target_name': message.reply_to_message.from_user.first_name,
        'bet': bet
    }

    bot.reply_to(
        message, 
        f"⚔️ **ДУЕЛЬ!**\n\n"
        f"**{message.from_user.first_name}** викликає **{message.reply_to_message.from_user.first_name}**!\n"
        f"💰 Ставка: `{bet:,} грн`\n\n"
        f"👉 Напиши `/прийняти`, щоб прийняти бій!",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['accept', 'прийняти', 'принять'])
def accept_duel(message):
    if is_user_banned(message.from_user.id): return
    target_id = message.from_user.id

    if target_id not in active_duels:
        return bot.reply_to(message, "⚠️ Тебе ніхто не викликав на дуель.")

    duel_info = active_duels.pop(target_id)
    challenger_id, bet = duel_info['challenger_id'], duel_info['bet']

    if get_user_balance(challenger_id) < bet or get_user_balance(target_id) < bet:
        return bot.reply_to(message, "❌ Недостатньо коштів на балансі!")

    winner_id, loser_id = (challenger_id, target_id) if random.random() < 0.5 else (target_id, challenger_id)
    winner_name = duel_info['challenger_name'] if winner_id == challenger_id else duel_info['target_name']
    loser_name = duel_info['target_name'] if winner_id == challenger_id else duel_info['challenger_name']

    update_user_balance(loser_id, -bet)
    update_user_balance(winner_id, bet)

    bot.reply_to(
        message,
        f"💥 **ПОСТРІЛ!**\n\n🤠 **{winner_name}** застрелив **{loser_name}**!\n🏆 Переможець забирає: `+{bet:,} грн`",
        parse_mode="Markdown"
    )

# 📊 ТОП БАГАТІЇВ (/rich, /багатії, /олігархи)
@bot.message_handler(commands=['rich', 'багатії', 'олігархи', 'богачи'])
def show_rich_users(message):
    if is_user_banned(message.from_user.id): return

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Показуємо тільки тих, хто досі в чаті (WHERE in_chat = TRUE)
                cursor.execute(
                    "SELECT user_id, balance, custom_nick, name FROM stats "
                    "WHERE in_chat = TRUE ORDER BY balance DESC LIMIT 10"
                )
                rows = cursor.fetchall()
            conn.close()

        if not rows:
            return bot.reply_to(message, "📉 Таблиця лідерів порожня.")

        top_text = "💰 <b>ТОП-10 НАЙБАГАТШИХ ГРАВЦІВ (ОЛІГАРХИ)</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

        for idx, row in enumerate(rows):
            uid, bal, custom_nick, name = row
            
            # Визначаємо відображуване ім'я
            if custom_nick:
                display_name = custom_nick
            elif name:
                display_name = name
            else:
                display_name = f"Гравець_{uid}"

            # Екрануємо символи HTML
            clean_name = display_name.replace("<", "&lt;").replace(">", "&gt;")
            
            top_text += f"{medals[idx]} <b>{clean_name}</b> — <code>{bal:,} грн</code>\n"

        bot.reply_to(message, top_text, parse_mode="HTML")
    except Exception as e:
        print(f"Помилка топу багатіїв: {e}")
        bot.reply_to(message, "❌ Помилка під час завантаження топу багатіїв.")

# ===================================================================
# 1. СТВОРЕННЯ ТАБЛИЦІ МОДЕРАТОРІВ В БД
# ===================================================================
def init_moderators_db():
    """Створює таблицю модераторів у PostgreSQL, якщо її ще немає"""
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_moderators (
                    user_id BIGINT PRIMARY KEY,
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        conn.close()

# Обов'язкова ініціалізація при запуску
init_moderators_db()


# ===================================================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ ТА ПЕРЕВІРКА ПРАВ
# ===================================================================

# Твій ID суперадміна бота
ADMIN_IDS = [5512316636]

# is_admin визначено нижче в секції адмін-панелі

def is_chat_admin(chat_id, user_id):
    """
    Перевіряє 3 рівні доступу:
    1. Суперадмін бота (ти через is_admin).
    2. Призначений через модератор з БД.
    3. Стандартний адмін Telegram-групи.
    """
    # 1. Глобальний адмін бота
    if is_admin(user_id):
        return True

    # 2. Перевірка динамічного модератора в БД
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM bot_moderators WHERE user_id = %s", (user_id,))
                is_mod = cursor.fetchone()
            conn.close()
            if is_mod:
                return True
    except Exception as e:
        print(f"Помилка перевірки модератора в БД: {e}")

    # 3. Перевірка адміна в самому чаті
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception:
        return False


def parse_time(time_str):
    """Парсить час для муту (10m, 10хв, 2h, 2год, 1d, 1день) -> повертає секунди"""
    time_str = time_str.lower().strip()
    
    # Словник позначень часу (українські та англійські)
    units = {
        'хв': 60, 'м': 60, 'm': 60, 'хвилин': 60, 'хвилину': 60, 'хвилини': 60,
        'год': 3600, 'г': 3600, 'h': 3600, 'годин': 3600, 'годину': 3600, 'години': 3600,
        'д': 86400, 'd': 86400, 'день': 86400, 'днів': 86400, 'дня': 86400,
        'с': 1, 's': 1, 'сек': 1
    }

    val_str = ""
    unit_str = ""
    for char in time_str:
        if char.isdigit():
            val_str += char
        else:
            unit_str += char

    if not val_str or not unit_str:
        return None

    val = int(val_str)
    unit_str = unit_str.strip()

    if unit_str in units:
        return val * units[unit_str]
    
    return None


# ===================================================================
# 🚫 АНТИСПАМ / АНТИФЛУД
# Слідкує за тим, як часто юзер пише в чат. Якщо флудить —
# видає попередження. Після 3-х попереджень — автоматичний мут.
# ===================================================================
SPAM_LOCK = threading.Lock()
SPAM_TRACKER = {}    # {(chat_id, user_id): [timestamps повідомлень]}
SPAM_WARNINGS = {}   # {(chat_id, user_id): {"count": int, "last": timestamp}}

FLOOD_MSG_LIMIT = 5          # скільки повідомлень поспіль вважати флудом
FLOOD_WINDOW_SEC = 5         # ...якщо вони прийшли за стільки секунд
FLOOD_WARN_LIMIT = 3         # скільки попереджень до автомуту
FLOOD_MUTE_SEC = 600         # тривалість автомуту за флуд (10 хв)
FLOOD_WARN_RESET_SEC = 300   # якщо юзер поводиться нормально стільки часу — попередження скидаються


def check_flood(message) -> bool:
    """
    Перевіряє, чи юзер флудить у чаті.
    Повертає True, якщо повідомлення визнано флудом (у цьому разі
    подальшу обробку повідомлення варто припинити).
    """
    if message.chat.type == 'private':
        return False

    user = message.from_user
    if not user:
        return False

    chat_id = message.chat.id
    user_id = user.id
    key = (chat_id, user_id)

    # Адмінів/модераторів не чіпаємо
    try:
        if is_chat_admin(chat_id, user_id):
            return False
    except Exception:
        pass

    now = time.time()
    warn_count = 0

    with SPAM_LOCK:
        timestamps = [t for t in SPAM_TRACKER.get(key, []) if now - t < FLOOD_WINDOW_SEC]
        timestamps.append(now)

        if len(timestamps) < FLOOD_MSG_LIMIT:
            SPAM_TRACKER[key] = timestamps
            return False

        # Флуд виявлено — скидаємо стек таймстампів, щоб не плодити попередження щосекунди
        SPAM_TRACKER[key] = []

        warn_data = SPAM_WARNINGS.get(key, {"count": 0, "last": 0})
        if now - warn_data.get("last", 0) > FLOOD_WARN_RESET_SEC:
            warn_data["count"] = 0

        warn_data["count"] += 1
        warn_data["last"] = now
        SPAM_WARNINGS[key] = warn_data
        warn_count = warn_data["count"]

    user_name = user.first_name or "Друже"

    if warn_count >= FLOOD_WARN_LIMIT:
        try:
            until_date = int(time.time()) + FLOOD_MUTE_SEC
            bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                until_date=until_date,
                permissions=telebot.types.ChatPermissions(can_send_messages=False)
            )
            with SPAM_LOCK:
                SPAM_WARNINGS[key] = {"count": 0, "last": 0}
            bot.send_message(
                chat_id,
                f"🔇 **{user_name}**, за флуд у чаті ({FLOOD_WARN_LIMIT}/{FLOOD_WARN_LIMIT} попереджень) "
                f"тебе замучено на {FLOOD_MUTE_SEC // 60} хв.",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Помилка автомуту за флуд: {e}")
    else:
        try:
            bot.send_message(
                chat_id,
                f"⚠️ **{user_name}**, не флуди в чаті! Попередження {warn_count}/{FLOOD_WARN_LIMIT}. "
                f"Ще трохи — і отримаєш мут.",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Помилка попередження за флуд: {e}")

    return True


# ===================================================================
# 3. КОМАНДИ КЕРУВАННЯ МОДЕРАТОРАМИ (ТІЛЬКИ ДЛЯ ТЕБЕ)
# ===================================================================

# ➕ Додати модератора (/addmod, додати модератора, +мод)
@bot.message_handler(commands=['addmod'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('додати модератора', '+мод', 'додати мода')))
def add_moderator(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ Цю команду може використовувати лише Творець бота!")

    target_id = None
    target_name = "Користувач"

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.first_name
    else:
        args = message.text.split()
        if len(args) > 1 and args[-1].isdigit():
            target_id = int(args[-1])
            target_name = f"ID: {target_id}"

    if not target_id:
        return bot.reply_to(
            message, 
            "⚠️ **Як використовувати:**\n"
            "1. Відповіж командою `/addmod` або `додати модератора` на повідомлення.\n"
            "2. Або напиши: `додати модератора [ID_користувача]`",
            parse_mode="Markdown"
        )

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO bot_moderators (user_id, added_by) 
                    VALUES (%s, %s) 
                    ON CONFLICT (user_id) DO NOTHING
                """, (target_id, message.from_user.id))
                conn.commit()
            conn.close()

        bot.reply_to(message, f"✅ **{target_name}** успішно отримав(ла) права модератора бота!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка БД: `{e}`", parse_mode="Markdown")


# ➖ Забрати права модератора (/delmod, видалити модератора, -мод)
@bot.message_handler(commands=['delmod', 'rmmod'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('видалити модератора', 'зняти модератора', '-мод', 'зняти мода')))
def remove_moderator(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ Цю команду може використовувати лише Творець бота!")

    target_id = None
    target_name = "Користувач"

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.first_name
    else:
        args = message.text.split()
        if len(args) > 1 and args[-1].isdigit():
            target_id = int(args[-1])
            target_name = f"ID: {target_id}"

    if not target_id:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення або вкажи ID!\nПриклад: `зняти модератора 12345678`", parse_mode="Markdown")

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM bot_moderators WHERE user_id = %s", (target_id,))
                conn.commit()
            conn.close()

        bot.reply_to(message, f"🗑️ З **{target_name}** знято права модератора бота!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка БД: `{e}`", parse_mode="Markdown")


# 📋 Список призначених модераторів (/modlist, список модераторів)
@bot.message_handler(commands=['modlist', 'mods'])
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['список модераторів', 'модератори', 'список модів'])
def list_moderators(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ Доступ заборонено!")

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, created_at FROM bot_moderators")
                rows = cursor.fetchall()
            conn.close()

        if not rows:
            return bot.reply_to(message, "📜 Список модераторів бота порожній.")

        text = "🛡️ **Список призначених модераторів:**\n\n"
        for idx, row in enumerate(rows, 1):
            text += f"{idx}. `ID: {row[0]}` (додано: {row[1].strftime('%Y-%m-%d')})\n"

        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка отримання списку: `{e}`", parse_mode="Markdown")


# ===================================================================
# 4. КОМАНДИ МОДЕРАЦІЇ ЧАТУ (ДЛЯ ТЕБЕ ТА ПРИЗНАЧЕНИХ МОДЕРАТОРІВ)
# ===================================================================

# 🔇 МУТ (/mute, мут, замутити)
@bot.message_handler(commands=['mute'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('кунімут', 'замутити')))
def mute_user(message):
    if message.chat.type == 'private':
        return bot.reply_to(message, "⚠️ Ця команда працює лише в групах!")

    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення порушника!\nПриклад: `мут 10хв спам` або `/mute 30m`", parse_mode="Markdown")

    target_user = message.reply_to_message.from_user
    if is_chat_admin(message.chat.id, target_user.id):
        return bot.reply_to(message, "🛡️ Не можна замутити адміна/модератора!")

    args = message.text.split(maxsplit=2)
    duration_sec = 600  # Дефолтний мут: 10 хвилин
    reason = "Порушення правил чату"

    if len(args) > 1:
        parsed_sec = parse_time(args[1])
        if parsed_sec:
            duration_sec = parsed_sec
            if len(args) > 2:
                reason = args[2]
        else:
            reason = " ".join(args[1:])

    until_date = int(time.time()) + duration_sec

    try:
        bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_user.id,
            until_date=until_date,
            permissions=telebot.types.ChatPermissions(can_send_messages=False)
        )
        
        if duration_sec >= 86400:
            time_display = f"{duration_sec // 86400} дн."
        elif duration_sec >= 3600:
            time_display = f"{duration_sec // 3600} год."
        elif duration_sec >= 60:
            time_display = f"{duration_sec // 60} хв."
        else:
            time_display = f"{duration_sec} сек."

        bot.reply_to(
            message,
            f"🔇 Користувача **{target_user.first_name}** замучено на **{time_display}**\n📝 **Причина:** {reason}",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка муту (перевірте права бота): `{e}`", parse_mode="Markdown")


# 🔊 РОЗМУТ (/unmute, розмут, зняти мут)
@bot.message_handler(commands=['unmute'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('розмут', 'зняти мут', 'размут')))
def unmute_user(message):
    if message.chat.type == 'private': return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення користувача, якого треба розмутити!")

    target_user = message.reply_to_message.from_user

    try:
        bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_user.id,
            permissions=telebot.types.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        bot.reply_to(message, f"🔊 З користувача **{target_user.first_name}** знято мут!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка розмуту: `{e}`", parse_mode="Markdown")


# 🔨 БАН (/ban, бан, забанити)
@bot.message_handler(commands=['ban'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('кунібан', 'забанити')))
def ban_user(message):
    if message.chat.type == 'private': return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення порушника для бану!")

    target_user = message.reply_to_message.from_user
    if is_chat_admin(message.chat.id, target_user.id):
        return bot.reply_to(message, "🛡️ Не можна забанити адміна/модератора!")

    args = message.text.split(maxsplit=1)
    reason = args[1] if len(args) > 1 else "Не вказана"

    try:
        bot.ban_chat_member(message.chat.id, target_user.id)
        bot.reply_to(
            message, 
            f"🔨 Користувача **{target_user.first_name}** забанено!\n📝 **Причина:** {reason}", 
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка бану: `{e}`", parse_mode="Markdown")


# 🔓 РОЗБАН (/unban, розбан, розбанити)
@bot.message_handler(commands=['unban'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('розбан', 'розбанити', 'разбан')))
def unban_user(message):
    if message.chat.type == 'private': return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення користувача для розбану!")

    target_user = message.reply_to_message.from_user

    try:
        bot.unban_chat_member(message.chat.id, target_user.id, only_if_banned=True)
        bot.reply_to(message, f"✅ Користувача **{target_user.first_name}** розбанено!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка розбану: `{e}`", parse_mode="Markdown")


# 👞 КІК (/kick, кік, вигнати)
@bot.message_handler(commands=['kick'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('кунікік',  'вигнати')))
def kick_user(message):
    if message.chat.type == 'private': return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення того, кого треба вигнати!")

    target_user = message.reply_to_message.from_user
    if is_chat_admin(message.chat.id, target_user.id):
        return bot.reply_to(message, "🛡️ Не можна вигнати адміна/модератора!")

    try:
        bot.ban_chat_member(message.chat.id, target_user.id)
        bot.unban_chat_member(message.chat.id, target_user.id)
        bot.reply_to(message, f"👞 Користувач **{target_user.first_name}** вигнаний з чату!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка кіку: `{e}`", parse_mode="Markdown")


# 🍆 ОТСОСБАН (реплай на повідомлення + "отсосбан")
# Мутить порушника і дає йому вибір: відсмоктати автору (знімається мут, всі бачать)
# або вийти самому (кік із чату). Тиснути кнопки може тільки сам порушник.
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('отсосбан', 'осос', 'відсосбан')))
def otsosban_user(message):
    if message.chat.type == 'private':
        return bot.reply_to(message, "⚠️ Ця команда працює лише в групах!")

    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення того, кому вішаємо отсосбан!")

    target_user = message.reply_to_message.from_user
    writer_user = message.from_user

    if is_chat_admin(message.chat.id, target_user.id):
        return bot.reply_to(message, "🛡️ Не можна вішати отсосбан на адміна/модератора!")

    if target_user.id == bot.get_me().id:
        return bot.reply_to(message, "🛑 На мене отсосбан не повісиш, я СБУ!")

    try:
        # Мутимо порушника, поки він не визначиться
        bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_user.id,
            permissions=telebot.types.ChatPermissions(can_send_messages=False)
        )
    except Exception as e:
        return bot.reply_to(message, f"❌ Не вдалось замутити (перевір права бота): `{e}`", parse_mode="Markdown")

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("😩 Відсмоктати", callback_data=f"otsos_suck_{target_user.id}_{writer_user.id}"),
        types.InlineKeyboardButton("🚪 Вийти самому", callback_data=f"otsos_leave_{target_user.id}")
    )

    bot.reply_to(
        message,
        f"🍆 **ОТСОСБАН!**\n\n"
        f"**{target_user.first_name}**, тебе замучено. У тебе є два шляхи:\n\n"
        f"😩 Відсмоктати **{writer_user.first_name}** — і мут знімається\n"
        f"🚪 Вийти самому з чату — і на тому все\n\n"
        f"⏳ Обирай, поки не пізно...",
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('otsos_'))
def otsosban_callback(call):
    parts = call.data.split('_')
    action = parts[1]  # 'suck' або 'leave'
    target_id = int(parts[2])

    if call.from_user.id != target_id:
        return bot.answer_callback_query(call.id, "🛑 Це не тобі вирішувати, це його вибір!", show_alert=True)

    chat_id = call.message.chat.id

    if action == 'suck':
        writer_id = int(parts[3])
        try:
            writer_chat_member = bot.get_chat_member(chat_id, writer_id)
            writer_name = writer_chat_member.user.first_name
        except Exception:
            writer_name = "того, хто написав"

        try:
            bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=target_id,
                permissions=telebot.types.ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True
                )
            )
        except Exception as e:
            return bot.answer_callback_query(call.id, f"❌ Помилка зняття муту: {e}", show_alert=True)

        target_name = call.from_user.first_name
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text=f"🍆😩 **{target_name}** відсмоктав у **{writer_name}**, аби не отримати бан! Мут знято, ганьба залишається.",
                parse_mode="Markdown"
            )
        except Exception:
            bot.send_message(chat_id, f"🍆😩 **{target_name}** відсмоктав у **{writer_name}**, аби не отримати бан!", parse_mode="Markdown")

        bot.answer_callback_query(call.id, "😩 Мут знято. На всю публіку тепер знають ціну твоєї свободи.")

    elif action == 'leave':
        target_name = call.from_user.first_name
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text=f"🚪 **{target_name}** обрав гідність і вийшов з чату сам.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        bot.answer_callback_query(call.id, "🚪 Йдеш сам, з гордо піднятою головою.")

        try:
            bot.ban_chat_member(chat_id, target_id)
            bot.unban_chat_member(chat_id, target_id)
        except Exception as e:
            print(f"Помилка самокіку через отсосбан: {e}")


# 🧹 ОЧИЩЕННЯ ПОВІДОМЛЕНЬ (/clear 10, очистити 10, почистити 10)
@bot.message_handler(commands=['clear', 'purge'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('очистити', 'почистити', 'чистка')))
def clear_messages(message):
    if message.chat.type == 'private': return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return bot.reply_to(message, "⚠️ Вкажи кількість повідомлень.\nПриклад: `очистити 15` або `/clear 15`", parse_mode="Markdown")

    count = int(args[1])
    if count > 100:
        count = 100  # Обмеження Telegram API

    current_id = message.message_id
    deleted = 0

    for i in range(count + 1):
        try:
            bot.delete_message(message.chat.id, current_id - i)
            deleted += 1
        except Exception:
            pass

    temp_msg = bot.send_message(message.chat.id, f"🧹 Успішно видалено **{deleted}** повідомлень!", parse_mode="Markdown")
    time.sleep(3)
    try:
        bot.delete_message(message.chat.id, temp_msg.message_id)
    except Exception:
        pass
        

# ===================================================================
# 💰 СИСТЕМА «ПАЦАНСЬКА МОНОПОЛІЯ» (Базар, Купівля, Майно, Перекази)
# ===================================================================

# Оновлений та розширений асортимент ринку (понад 50 предметів)
SHOP_ITEMS = {
    # 💎 Аксесуари та Гаджети
    "rolex": {
        "name": "⌚ Золотий Rolex Daytona",
        "price": 85000,
        "cat": "Аксесуари",
        "ai_desc": "luxurious golden Rolex watch on a velvet pillow",
    },
    "iphone": {
        "name": "📱 iPhone 16 Pro Max",
        "price": 65000,
        "cat": "Аксесуари",
        "ai_desc": "latest titanium iPhone 16 Pro Max, futuristic backdrop",
    },
    "chain": {
        "name": "⛓️ Масивна золота цеп",
        "price": 120000,
        "cat": "Аксесуари",
        "ai_desc": "heavy thick solid gold chain on dark background",
    },
    "airpods_max": {
        "name": "🎧 AirPods Max Gold Edition",
        "price": 45000,
        "cat": "Аксесуари",
        "ai_desc": "custom gold luxury over-ear headphones",
    },
    "ring": {
        "name": "💍 Перстень з чорним діамантом",
        "price": 320000,
        "cat": "Аксесуари",
        "ai_desc": "rare black diamond ring on silk background",
    },
    # 🐾 Тварини
    "capybara": {
        "name": "🦦 Домашня Капібара",
        "price": 25000,
        "cat": "Тварини",
        "ai_desc": "cute relaxed capybara wearing a small gold chain",
    },
    "tiger": {
        "name": "🐅 Ручний Тигр",
        "price": 350000,
        "cat": "Тварини",
        "ai_desc": "majestic big pet tiger sitting on velvet carpet",
    },
    "bear": {
        "name": "🐻 Дресирований Ведмідь",
        "price": 600000,
        "cat": "Тварини",
        "ai_desc": "huge friendly brown bear wearing leather harness",
    },
    "pigeon": {
        "name": "🕊️ Поштовий Голуб-Шпигун",
        "price": 5000,
        "cat": "Тварини",
        "ai_desc": "carrier pigeon with mini tactical camera attachment",
    },
    "panther": {
        "name": "🐆 Чорна Пантера",
        "price": 500000,
        "cat": "Тварини",
        "ai_desc": "sleek dangerous black panther crouching in grass",
    },
    # 🔫 Зброя та Екіп
    "ak47": {
        "name": "🔫 Золотий АК-47",
        "price": 200000,
        "cat": "Зброя",
        "ai_desc": "glowing pure gold AK-47 assault rifle",
    },
    "deagle": {
        "name": "💥 Desert Eagle .50 AE",
        "price": 90000,
        "cat": "Зброя",
        "ai_desc": "chrome Desert Eagle pistol on wooden table",
    },
    "sniper": {
        "name": "🎯 Снайперська гвинтівка Barrett M82",
        "price": 450000,
        "cat": "Зброя",
        "ai_desc": "heavy 50 cal sniper rifle with big scope",
    },
    "katana": {
        "name": "⚔️ Клеймована Самурайська Катана",
        "price": 180000,
        "cat": "Зброя",
        "ai_desc": "sharp authentic katana with golden engravings",
    },
    # 🛸 Техніка та Спецгаджети
    "dji": {
        "name": "🛸 Дрон DJI Mavic 3 Enterprise",
        "price": 140000,
        "cat": "Техніка",
        "ai_desc": "modern black quadcopter drone with thermal camera",
    },
    "starlink": {
        "name": "🛰️ Термінал Starlink V4",
        "price": 35000,
        "cat": "Техніка",
        "ai_desc": "futuristic white satellite dish terminal setup",
    },
    "rig": {
        "name": "🖥️ Майнінг-ферма на 8x RTX 4090",
        "price": 850000,
        "cat": "Техніка",
        "ai_desc": "glowing RGB crypto mining rig full of GPU cards",
    },
    # 🚗 Тачки та Автопарк
    "jiga": {
        "name": "🚗 ВАЗ 2107 (Жига)",
        "price": 15000,
        "cat": "Тачки",
        "ai_desc": "tuned classic VAZ 2107 car",
    },
    "lanos": {
        "name": "🚖 Daewoo Lanos на Газу",
        "price": 60000,
        "cat": "Тачки",
        "ai_desc": "classic silver Daewoo Lanos taxi car",
    },
    "passat": {
        "name": "🚘 Volkswagen Passat B6 1.9 TDI",
        "price": 220000,
        "cat": "Тачки",
        "ai_desc": "black Volkswagen Passat car parked near garage",
    },
    "bmw_e30": {
        "name": "🛞 Дрифтова BMW E30 Корч",
        "price": 350000,
        "cat": "Тачки",
        "ai_desc": "tuned drift BMW E30 burning tires in smoke",
    },
    "camry": {
        "name": "🚘 Toyota Camry 70 3.5 V6",
        "price": 850000,
        "cat": "Тачки",
        "ai_desc": "black aggressive Toyota Camry 70",
    },
    "bmw_e60": {
        "name": "🖤 BMW M5 E60 V10",
        "price": 1100000,
        "cat": "Тачки",
        "ai_desc": "black aggressive BMW M5 E60 loud exhaust",
    },
    "skyline": {
        "name": "🏎️ Nissan Skyline GT-R R34",
        "price": 2500000,
        "cat": "Тачки",
        "ai_desc": "legendary blue Nissan Skyline GT-R R34",
    },
    "bmw": {
        "name": "🏎️ BMW M5 F90 CS",
        "price": 3800000,
        "cat": "Тачки",
        "ai_desc": "black aggressive sports car BMW M5 F90",
    },
    "supra": {
        "name": "🔥 Toyota Supra MK4 (1000 к.с.)",
        "price": 4200000,
        "cat": "Тачки",
        "ai_desc": "tuned orange Toyota Supra MK4 sports car",
    },
    "audi_rs6": {
        "name": "🚀 Audi RS6 Avant C8",
        "price": 5500000,
        "cat": "Тачки",
        "ai_desc": "aggressive black Audi RS6 station wagon",
    },
    "cybertruck": {
        "name": "📐 Tesla Cybertruck Cyberbeast",
        "price": 6000000,
        "cat": "Тачки",
        "ai_desc": "futuristic stainless steel Tesla Cybertruck",
    },
    "gelik": {
        "name": "⬛ Mercedes G63 AMG (Ґелік)",
        "price": 7500000,
        "cat": "Тачки",
        "ai_desc": "black aggressive Mercedes G-Wagon G63 AMG",
    },
    "porsche": {
        "name": "🚀 Porsche 911 GT3 RS",
        "price": 8500000,
        "cat": "Тачки",
        "ai_desc": "racing lime Porsche 911 GT3 RS",
    },
    "urus": {
        "name": "🐂 Lamborghini Urus Performante",
        "price": 11000000,
        "cat": "Тачки",
        "ai_desc": "yellow aggressive SUV Lamborghini Urus",
    },
    "rolls": {
        "name": "👑 Rolls-Royce Cullinan Mansory",
        "price": 18000000,
        "cat": "Тачки",
        "ai_desc": "luxurious black custom Rolls-Royce Cullinan",
    },
    "ferrari": {
        "name": "🔴 Ferrari SF90 Stradale",
        "price": 22000000,
        "cat": "Тачки",
        "ai_desc": "red Italian hypercar Ferrari SF90",
    },
    "lambo_revuelto": {
        "name": "⚡ Lamborghini Revuelto",
        "price": 28000000,
        "cat": "Тачки",
        "ai_desc": "futuristic orange Lamborghini Revuelto hypercar",
    },
    "bugatti": {
        "name": "⚡ Bugatti Chiron Super Sport",
        "price": 45000000,
        "cat": "Тачки",
        "ai_desc": "hypercar Bugatti Chiron",
    },
    "tourbillon": {
        "name": "💎 Bugatti Tourbillon",
        "price": 95000000,
        "cat": "Тачки",
        "ai_desc": "ultimate futuristic luxury hypercar Bugatti Tourbillon",
    },
    # 🚚 Важка техніка та Спецтранспорт
    "truck": {
        "name": "🚚 Тягач MAN TGX",
        "price": 4000000,
        "cat": "Транспорт",
        "ai_desc": "heavy industrial MAN truck on highway",
    },
    "copier": {
        "name": "🚁 Вертоліт Eurocopter",
        "price": 18000000,
        "cat": "Транспорт",
        "ai_desc": "private luxury black helicopter",
    },
    "tank": {
        "name": "🪖 Танк T-80",
        "price": 80000000,
        "cat": "Транспорт",
        "ai_desc": "powerful military combat tank T-80",
    },
    "jet": {
        "name": "🛩️ Бизнес-джет Gulfstream G650",
        "price": 250000000,
        "cat": "Транспорт",
        "ai_desc": "private white Gulfstream jet on airfield",
    },
    # 🏢 Нерухомість
    "garage": {
        "name": "🏚️ Ґараж на Макулатурі",
        "price": 80000,
        "cat": "Нерухомість",
        "ai_desc": "old brick garage with metal doors",
    },
    "flat": {
        "name": "🏢 Хрущовка в Кривбасі",
        "price": 450000,
        "cat": "Нерухомість",
        "ai_desc": "Soviet-style apartment building",
    },
    "penthouse": {
        "name": "🏙️ Пентхаус у Києві",
        "price": 12000000,
        "cat": "Нерухомість",
        "ai_desc": (
            "luxury modern glass penthouse view of modern city skyline"
        ),
    },
    "villa": {
        "name": "🏰 Вілла в Конча-Заспі",
        "price": 30000000,
        "cat": "Нерухомість",
        "ai_desc": "luxury modern mansion with pool",
    },
    "bunker": {
        "name": "🛡️ Підземний бункер вищого захисту",
        "price": 65000000,
        "cat": "Нерухомість",
        "ai_desc": "hi-tech underground bunker interior with monitors",
    },
    "castle": {
        "name": "🏰 Середньовічний замок",
        "price": 150000000,
        "cat": "Нерухомість",
        "ai_desc": "epic majestic ancient stone castle in countryside",
    },
    # 💎 Люкс, Олігархія та Космос
    "gold_bar": {
        "name": "🥇 Золотий зливок (10 кг)",
        "price": 30000000,
        "cat": "Люкс",
        "ai_desc": "heavy sparkling pure gold bar bullion",
    },
    "yacht": {
        "name": "🚢 Олігарх-Яхта",
        "price": 95000000,
        "cat": "Люкс",
        "ai_desc": "giant luxury superyacht floating in water",
    },
    "sub": {
        "name": "⚓ Підводний човен (Субмарина)",
        "price": 180000000,
        "cat": "Люкс",
        "ai_desc": "black stealth submarine in ocean surface",
    },
    "island": {
        "name": "🏝️ Приватний острів на Карибах",
        "price": 500000000,
        "cat": "Люкс",
        "ai_desc": (
            "exotic tropical private island with white beaches and palm trees"
        ),
    },
    "stadium": {
        "name": "🏟️ Футбольний стадіон",
        "price": 1000000000,
        "cat": "Люкс",
        "ai_desc": "huge crowded illuminated football sports stadium at night",
    },
    "starship": {
        "name": "🚀 Ракета SpaceX Starship",
        "price": 2500000000,
        "cat": "Люкс",
        "ai_desc": "massive silver SpaceX Starship rocket on launchpad",
    },
    "space_station": {
        "name": "🛰️ Приватна Орбітальна Станція",
        "price": 10000000000,
        "cat": "Люкс",
        "ai_desc": "futuristic space station orbiting Earth",
    },
}

SHOP_ITEMS_PER_PAGE = 4

# 🛠️ АВТОМАТИЧНА ПЕРЕВІРКА ТА СТВОРЕННЯ ТАБЛИЦІ В БД
def init_inventory_db():
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS inventory (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        item_code VARCHAR(50) NOT NULL,
                        item_name VARCHAR(150) NOT NULL,
                        item_category VARCHAR(50),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"⚠️ Помилка ініціалізації таблиці inventory: {e}")

init_inventory_db()

# 📄 ДОПОМІЖНА ФУНКЦІЯ ГЕНЕРАЦІЇ ТЕКСТУ ТА КНОПОК МАГАЗИНУ
def build_shop_page(page=0):
    item_keys = list(SHOP_ITEMS.keys())

    if not item_keys:
        return "🏪 <b>Магазин порожній. Товарів немає!</b>", None, []

    total_pages = max(1, (len(item_keys) + SHOP_ITEMS_PER_PAGE - 1) // SHOP_ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * SHOP_ITEMS_PER_PAGE
    end_idx = start_idx + SHOP_ITEMS_PER_PAGE
    current_keys = item_keys[start_idx:end_idx]

    shop_descriptions = []

    text = [
        "🏪 <b>ЧОРНИЙ РИНОК ДРАГО: ЧАС ВИТРАЧАТИ БАБЛО</b> 💵\n",
        f"📊 <b>КАТАЛОГ ТОВАРІВ (Стор. {page + 1}/{total_pages}):</b>\n"
    ]

    for code in current_keys:
        item = SHOP_ITEMS[code]

        if item.get("ai_desc"):
            shop_descriptions.append(item["ai_desc"])

        text.append(f"📦 <b>{item.get('cat', 'Товар').upper()}</b>")
        text.append(f"• <code>{code}</code> — <b>{item.get('name', 'Без назви')}</b>")
        text.append(f" └ 💰 Ціна: <code>{item.get('price', 0):,} грн</code>\n")

    text.append("💡 <i>Купити товар: /купити [код] (наприклад: /купити gelik)</i>")

    markup = types.InlineKeyboardMarkup()
    buttons = []

    if page > 0:
        buttons.append(
            types.InlineKeyboardButton(
                "◀️ Назад",
                callback_data=f"shoppage_{page - 1}"
            )
        )

    buttons.append(
        types.InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}",
            callback_data="ignore"
        )
    )

    if page < total_pages - 1:
        buttons.append(
            types.InlineKeyboardButton(
                "Вперед ▶️",
                callback_data=f"shoppage_{page + 1}"
            )
        )

    markup.row(*buttons)

    return "\n".join(text), markup, shop_descriptions

# ===================================================================
# 🎨 AI ФОТО МАЙНА — ОДНА СПІЛЬНА СЦЕНА, ПЕРШІ 4 ПРЕДМЕТИ
# Один запит до Pollinations (без паралельності — тому без рейт-ліміту),
# промпт явно просить розкласти рівно 4 предмети поруч в одному кадрі.
# ===================================================================
def generate_inventory_ai_image(bought_codes, total_value=0, balance=0):

    if not bought_codes:
        return None

    descriptions = []

    # Обмежуємось 4 предметами — так AI реально встигає чітко намалювати
    # кожен окремо, без "каші" з десятка об'єктів в одній сцені.
    for code in bought_codes[:4]:
        code = str(code)

        if code in SHOP_ITEMS:
            item = SHOP_ITEMS[code]
            desc = item.get("ai_desc") or item.get("name")
            descriptions.append(desc)

    if not descriptions:
        return None

    numbered_items = "; ".join(f"({i + 1}) {d}" for i, d in enumerate(descriptions))

    prompt = (
        "Professional product photography, ultra realistic, wide panoramic composition. "
        "A single grand luxury collection display arranged neatly side by side in one frame, "
        f"showing exactly {len(descriptions)} items together, each clearly separated and fully visible: "
        + numbered_items
        + ". "
        "Arranged like a museum showcase or flat-lay collection, real objects, sharp focus, "
        "extremely detailed textures, studio lighting, 8K resolution, DSLR photo, "
        "wide-angle showroom shot, no cartoon, no painting, no blur, nothing cropped out, "
        "no text, no watermark."
    )

    try:
        seed = random.randint(100000, 999999)
        # enhance=False — без важкого LLM-доопрацювання промпту, стабільніше і швидше
        url = build_pollinations_url(prompt, width=1600, height=900, seed=seed, enhance=False)

        headers = {"User-Agent": "Mozilla/5.0"}

        for attempt in range(3):
            try:
                response = requests.get(url, headers=headers, timeout=70)
                if response.status_code == 200:
                    img = Image.open(io.BytesIO(response.content)).convert("RGB")

                    bio = io.BytesIO()
                    bio.name = "inventory_hd.jpg"
                    img.save(bio, "JPEG", quality=95, optimize=True)
                    bio.seek(0)
                    return bio
                else:
                    print(f"⚠️ AI фото майна: HTTP {response.status_code}, спроба {attempt + 1}")
            except Exception as e:
                print(f"❌ AI фото помилка, спроба {attempt + 1}: {e}")

            if attempt < 2:
                time.sleep(3 + attempt * 2)

    except Exception as e:
        print("❌ AI фото помилка:", e)

    return None

# ===================================================================
# 🧠 ДОПОМІЖНА ФУНКЦІЯ ВІДОБРАЖЕННЯ МАЙНА (РЕДАКТОР/ВІДПОВІДЬ)
# ===================================================================
def process_and_send_inventory(chat_id, user_id, user_name, reply_to_id=None, is_callback=False):
    clean_name = user_name.replace("<", "&lt;").replace(">", "&gt;")
    
    with db_lock:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0
                
                cursor.execute("SELECT item_code, item_name FROM inventory WHERE user_id = %s", (user_id,))
                items = cursor.fetchall()
        finally:
            conn.close()

    response = [
        f"👑 <b>ФІНАНСОВИЙ АУДИТ АКТИВІВ</b> 👑",
        f"👤 <b>Власник:</b> {clean_name.upper()}\n",
        f"────────────────────",
        f"💳 <b>Готівка:</b> <code>{balance:,} грн</code>",
    ]
    
    if not items:
        response.append("────────────────────")
        response.append("🎰 <b>Статус:</b> <i>Повний голяк. Тільки шкарпетки й телефон, з якого ти пишеш. Бігом на заробітки! 🏃‍♂️</i>")
        bot.send_message(chat_id, "\n".join(response), parse_mode="HTML", reply_to_message_id=reply_to_id)
        return

    total_property_value = 0
    item_counts = {}
    unique_codes = []
    
    for item in items:
        code, name = item[0], item[1]
        str_code = str(code)
        
        item_counts[name] = item_counts.get(name, 0) + 1
        if str_code not in unique_codes:
            unique_codes.append(str_code)
        if 'SHOP_ITEMS' in globals() and str_code in SHOP_ITEMS:
            total_property_value += SHOP_ITEMS[str_code].get("price", 0)

    response.append(f"💰 <b>Цінність майна:</b> <code>{total_property_value:,} грн</code>")
    response.append("────────────────────")
    response.append("📊 <b>СПИСОК ЗАРЕЄСТРОВАНОГО МАЙНА:</b>")
    
    for name, count in item_counts.items():
        count_str = f" <code>[x{count}]</code>" if count > 1 else ""
        response.append(f" ╰┈➤ {name}{count_str}")
    
    caption_text = "\n".join(response)

    status_msg = bot.send_message(
        chat_id, 
        "🎨 <b>Драго малює твоє майно на єдиній картині...</b>\n<i>Зачекай трохи!</i>", 
        parse_mode="HTML",
        reply_to_message_id=reply_to_id
    )

    top_codes = unique_codes[:4]
    photo_bio = generate_inventory_ai_image(top_codes, total_value=total_property_value, balance=balance)

    if photo_bio:
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass
        
        bot.send_photo(
            chat_id, 
            photo=photo_bio, 
            caption=caption_text, 
            parse_mode="HTML",
            reply_to_message_id=reply_to_id
        )
    else:
        bot.edit_message_text(
            caption_text, 
            chat_id=chat_id, 
            message_id=status_msg.message_id, 
            parse_mode="HTML"
        )

# =====================================================
# 🖼️ AI ФОТО ДЛЯ МАГАЗИНУ (POLLINATIONS FLUX)
# =====================================================

def generate_shop_ai_image(descriptions):

    if not descriptions:
        return None

    try:

        selected_items = descriptions[:3]

        prompt = (
            "Luxury showcase of "
            + ", ".join(selected_items)
            + ". Ultra realistic, cinematic lighting, "
              "expensive store, 8k, photorealistic"
        )

        seed = random.randint(1, 999999)

        image_url = build_pollinations_url(prompt, width=1024, height=1024, seed=seed)


        headers = {
            "User-Agent": "Mozilla/5.0"
        }


        response = requests.get(
            image_url,
            headers=headers,
            timeout=60
        )


        if response.status_code == 200:

            img = Image.open(
                io.BytesIO(response.content)
            ).convert("RGB")


            bio = io.BytesIO()

            bio.name = "shop_ai.jpg"

            img.save(
                bio,
                "JPEG",
                quality=95
            )

            bio.seek(0)

            return bio


        else:
            print(
                f"Pollinations error: {response.status_code}"
            )


    except Exception as e:

        print(
            f"AI SHOP ERROR: {e}"
        )


    return None

# 🏪 🛒 КОМАНДА: МАГАЗИН (/shop, /магазин)
@bot.message_handler(commands=['shop', 'магазин'])
def show_shop(message):
    if is_user_banned(message.from_user.id):
        return

    text, markup, _ = build_shop_page(page=0)

    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=markup
    )
# 🔄 ОБРОБКА ПЕРЕМЕШЕННЯ ПО СТОРІНКАХ МАГАЗИНУ
@bot.callback_query_handler(func=lambda call: call.data.startswith('shoppage_'))
def handle_shop_page(call):
    if is_user_banned(call.from_user.id):
        return

    page = int(call.data.split('_')[1])

    text, markup, _ = build_shop_page(page=page)

    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

        bot.answer_callback_query(call.id)

    except Exception as e:
        print(f"❌ Помилка гортання магазину: {e}")
        bot.answer_callback_query(call.id, "❌ Помилка.")
   
# 🛍️ КОМАНДА: КУПИТИ ТОВАР (/buy, /купити)
@bot.message_handler(commands=['buy', 'купити'])
def buy_item(message):
    if is_user_banned(message.from_user.id):
        return

    args = message.text.split()

    if len(args) < 2:
        bot.reply_to(
            message,
            "⚠️ Вкажи код товару!\nПриклад: <code>/купити bmw</code>",
            parse_mode="HTML"
        )
        return

    item_code = args[1].lower().strip()

    if item_code not in SHOP_ITEMS:
        bot.reply_to(
            message,
            "🤡 Такого товару немає! Дивись /магазин",
            parse_mode="HTML"
        )
        return

    item = SHOP_ITEMS[item_code]
    user_id = message.from_user.id

    try:

        # 💾 Робота з базою
        with db_lock:
            conn = get_db_connection()

            try:
                with conn.cursor() as cursor:

                    # Баланс
                    cursor.execute(
                        "SELECT balance FROM stats WHERE user_id = %s",
                        (user_id,)
                    )

                    res = cursor.fetchone()
                    balance = res[0] if res else 0


                    # Перевірка грошей
                    if balance < item["price"]:

                        shortage = item["price"] - balance

                        bot.reply_to(
                            message,
                            f"💸 <b>Не вистачає грошей!</b>\n\n"
                            f"Треба ще: <code>{shortage:,} грн</code>",
                            parse_mode="HTML"
                        )

                        return


                    # Списання грошей
                    cursor.execute(
                        """
                        UPDATE stats
                        SET balance = balance - %s
                        WHERE user_id = %s
                        """,
                        (item["price"], user_id)
                    )


                    # Додавання в інвентар
                    cursor.execute(
                        """
                        INSERT INTO inventory
                        (user_id, item_code, item_name, item_category)
                        VALUES (%s,%s,%s,%s)
                        """,
                        (
                            user_id,
                            item_code,
                            item["name"],
                            item["cat"]
                        )
                    )


                conn.commit()


            finally:
                conn.close()



        # 📸 Генерація фото
        status = bot.reply_to(
            message,
            "📸 <b>Генерую фото покупки...</b>\n"
            "⏳ Зачекай кілька секунд.",
            parse_mode="HTML"
        )


        photo = None

        if item.get("ai_desc"):
            photo = generate_shop_ai_image(
                [item["ai_desc"]]
            )


        try:
            bot.delete_message(
                message.chat.id,
                status.message_id
            )
        except:
            pass



        caption = (
            f"🎉 <b>УСПІШНА УГОДА!</b> 🎉\n\n"
            f"📦 Товар: <b>{item['name']}</b>\n"
            f"💰 Ціна: <code>{item['price']:,} грн</code>\n"
            f"🏷 Категорія: <b>{item['cat']}</b>\n\n"
            f"✅ Додано у твоє майно!\n"
            f"📋 Перевірити: /майно"
        )


        if photo:

            bot.send_photo(
                message.chat.id,
                photo=photo,
                caption=caption,
                parse_mode="HTML"
            )

        else:

            bot.send_message(
                message.chat.id,
                caption,
                parse_mode="HTML"
            )


    except Exception as e:

        print(f"❌ Помилка купівлі: {e}")

        bot.reply_to(
            message,
            f"❌ Помилка угоди:\n<code>{str(e)[:150]}</code>",
            parse_mode="HTML"
        )

# 💸 КОМАНДА: ПЕРЕДАТИ ГРОШІ ІНШОМУ ГРАВЦЮ (/pay, /передати, /переказ, /дати)
@bot.message_handler(commands=['pay', 'передати', 'переказ', 'дати'])
def transfer_money(message):
    if is_user_banned(message.from_user.id): return

    sender_id = message.from_user.id
    args = message.text.split()
    
    amount = None
    target_user_id = None
    target_user_name = None

    if message.reply_to_message:
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Вкажи суму переказу! Наприклад: <code>/передати 5000</code> (у відповідь на повідомлення)", parse_mode="HTML")
            return
        try:
            amount = int(args[1])
        except ValueError:
            bot.reply_to(message, "🤡 Сума має бути цілим числом!")
            return

        target_user_id = message.reply_to_message.from_user.id
        target_user_name = message.reply_to_message.from_user.first_name

    else:
        if len(args) < 3:
            bot.reply_to(message, "💡 <b>Як передати бабки:</b>\n1. Відповісти на чиєсь повідомлення: <code>/передати 1000</code>\n2. Або за юзернеймом: <code>/передати 1000 @username</code>", parse_mode="HTML")
            return
        try:
            amount = int(args[1])
        except ValueError:
            bot.reply_to(message, "🤡 Сума має бути цілим числом!")
            return

        username_arg = args[2].replace("@", "").strip()
        
        try:
            with db_lock:
                conn = get_db_connection()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT user_id, name FROM stats WHERE LOWER(name) = LOWER(%s)", (username_arg,))
                        res = cursor.fetchone()
                        if res:
                            target_user_id = res[0]
                            target_user_name = res[1] or username_arg
                finally:
                    conn.close()
        except Exception as e:
            print(f"Помилка пошуку юзера: {e}")

        if not target_user_id:
            bot.reply_to(message, f"❌ Не знайшов у базі гравця <code>@{username_arg}</code>. Хай він спочатку напише щось у чат!", parse_mode="HTML")
            return

    if amount <= 0:
        bot.reply_to(message, "🤡 Ти кого надурити хочеш? Сума повинна бути більшою за 0!")
        return

    if sender_id == target_user_id:
        bot.reply_to(message, "🧠 Переводити гроші самому собі? Сильно.")
        return

    try:
        with db_lock:
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (sender_id,))
                    res = cursor.fetchone()
                    sender_balance = res[0] if res else 0

                    if sender_balance < amount:
                        bot.reply_to(
                            message, 
                            f"💸 <b>Неймовірний фінансовий крах!</b>\n"
                            f"У тебе немає <code>{amount:,} грн</code>. Твій баланс: <code>{sender_balance:,} грн</code>.", 
                            parse_mode="HTML"
                        )
                        return

                    cursor.execute("UPDATE stats SET balance = balance - %s WHERE user_id = %s", (amount, sender_id))
                    cursor.execute("""
                        INSERT INTO stats (user_id, balance) VALUES (%s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET balance = stats.balance + EXCLUDED.balance;
                    """, (target_user_id, amount))

                conn.commit()
            finally:
                conn.close()

        clean_sender = message.from_user.first_name.replace("<", "&lt;").replace(">", "&gt;")
        clean_target = target_user_name.replace("<", "&lt;").replace(">", "&gt;")

        bot.reply_to(
            message,
            f"🤝 <b>БРАТВА УГОДИ ДОТРИМУЄТЬСЯ!</b>\n\n"
            f"👤 <b>{clean_sender}</b> переказав 💸 <code>{amount:,} грн</code> ➔ 👤 <b>{clean_target}</b>!\n"
            f"<i>Транзакція пройшла через пацанський банк.</i>",
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Помилка переказу: {e}")
        bot.reply_to(message, f"❌ Помилка під час транзакції: <code>{str(e)[:100]}</code>", parse_mode="HTML")

# 💼 👑 МАЙНО ТА ПРОФІЛЬ ТЕКСТОВОЮ КОМАНДОЮ
@bot.message_handler(commands=['money', 'balance', 'майно', 'гаманець', 'баланс'])
def show_inventory(message):
    if is_user_banned(message.from_user.id): return
    
    try:
        process_and_send_inventory(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            user_name=message.from_user.first_name or "Користувач",
            reply_to_id=message.message_id
        )
    except Exception as e:
        print(f"❌ Помилка команди майно: {e}")
        error_details = str(e).replace("<", "&lt;").replace(">", "&gt;")
        bot.reply_to(message, f"❌ Помилка бази даних:\n<code>{error_details[:100]}</code>", parse_mode="HTML")

# 👁️ 🔘 ОБРОБКА ІНЛАЙН-КНОПКИ "ВИГЛЯД МАЙНА"
@bot.callback_query_handler(func=lambda call: call.data in ['show_inventory', 'view_inventory', 'profile_inventory', 'майно'])
def handle_view_inventory_callback(call):
    if is_user_banned(call.from_user.id): return

    bot.answer_callback_query(call.id)
    
    try:
        process_and_send_inventory(
            chat_id=call.message.chat.id,
            user_id=call.from_user.id,
            user_name=call.from_user.first_name or "Користувач",
            is_callback=True
        )
    except Exception as e:
        print(f"❌ Помилка кнопки майна: {e}")



# ===================================================================
# 🔄 ЗМІНА ПОРЯДКУ БІЗНЕСІВ (/swap)
# ===================================================================
@bot.message_handler(commands=['swap', 'поміняти'])
def swap_business_order(message):
    if is_user_banned(message.from_user.id): return
    
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "⚠️ Формат: <code>/swap [№1] [№2]</code> — поміняти місцями бізнеси у списку", parse_mode="HTML")
        return
    
    try:
        pos1 = int(args[1])
        pos2 = int(args[2])
    except ValueError:
        bot.reply_to(message, "❌ Номери мають бути числами!")
        return
    
    user_id = message.from_user.id
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, position FROM user_businesses WHERE user_id = %s ORDER BY position ASC, id ASC", (user_id,))
                rows = cursor.fetchall()
                
                if pos1 < 1 or pos1 > len(rows) or pos2 < 1 or pos2 > len(rows):
                    bot.reply_to(message, f"❌ Номер має бути від 1 до {len(rows)}!")
                    conn.close()
                    return
                
                id1 = rows[pos1 - 1][0]
                id2 = rows[pos2 - 1][0]
                pos1_db = rows[pos1 - 1][1]
                pos2_db = rows[pos2 - 1][1]
                
                cursor.execute("UPDATE user_businesses SET position = %s WHERE id = %s", (pos2_db, id1))
                cursor.execute("UPDATE user_businesses SET position = %s WHERE id = %s", (pos1_db, id2))
            conn.commit()
            conn.close()
        
        bot.reply_to(message, f"🔄 Бізнеси #{pos1} та #{pos2} поміняні місцями!")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {e}")

# ===================================================================

ADMIN_ID = 5512316636  # Твій Telegram ID
TARGET_GROUP_ID = -1003428241218  # ID твоєї групи

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    # Перевірка на адміна
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "❌ У вас немає прав для запуску розсилки.")
        return

    # Текст повідомлення
    broadcast_text = (
        "🔥 *DRAGO CLICKER — ЧАС ЗДОБУВАТИ БАГАТСТВО ТА БИТИСЬ!* 🔥\n\n"
        "Готовий побудувати власну імперію, знищувати ворогів та випередити всіх суперників? 🐉⚔️💰\n\n"
        "⚡ *Що на тебе чекає в грі:*\n"
        "• 💎 Збирай рідкісні артефакти та прокачуй силу тапу\n"
        "• 🏢 Купуй бізнеси та отримуй пасивний дохід 24/7\n"
        "• ⚔️ *Арена:* вступай у запеклі битви з ворогами, здобувай унікальний лут і заробляй додаткові гроші!\n"
        "• 🎁 Забирай щоденні бонуси та крути Колесо Фортуни\n"
        "• 🏆 Змагайся з іншими у світовому рейтингу!\n\n"
        "🚀 *Твоя енергія вже повністю відновилася! Заходь та забирай свої статки:*"
    )

    # Кнопка з посиланням у ПП бота
    keyboard = types.InlineKeyboardMarkup()
    btn_game = types.InlineKeyboardButton(
        text="🎮 Грати в Drago Clicker", 
        url="https://t.me/Draagoon_bot"
    )
    keyboard.add(btn_game)

    try:
        # Відправляємо повідомлення безпосередньо в групу
        bot.send_message(
            TARGET_GROUP_ID, 
            broadcast_text, 
            reply_markup=keyboard, 
            parse_mode="Markdown"
        )
        bot.send_message(message.chat.id, "✅ Повідомлення успішно відправлено в групу!")
    except Exception as e:
        print(f"Помилка відправки в групу: {e}")
        bot.send_message(message.chat.id, f"❌ Помилка під час відправки: {e}")
        


# ==================== БАЗА ГІФОК / КАРТИНОК ДЛЯ КОЖНОЇ КОМАНДИ ====================
# Вставляй у масив "media" свої Telegram file_id або прямі посилання (.gif / .jpg / .png).
# Якщо в масиві декілька гіфок — бот щоразу вибиратиме випадкову!

ACTIONS_DB = {
    # --- Класичні та милі дії ---
    "обняти": {
        "text": "обійняв(ла)",
        "media": [
            "CgACAgIAAxkBAAIFh2pxAwbA3o9NcBD2-sFxYWdKuJlnAAKEhQACtoVJSvlGyav401PVPQQ",
        ]
    },
    "вкусити": {
        "text": "укусив(ла)",
        "media": [
            "CgACAgQAAxkBAAIFiWpxBDvicLHQUYu1Kn_q5tWpqzR7AAIiAwAC1CQFU6mdlKNK5ZCIPQQ"
        ]
    },
    "поцілувати": {
        "text": "поцілував(ла)",
        "media": [
            "CgACAgQAAxkBAAIFjWpxBPlPKu8xv2MG2tXzgkMhesEgAALeAwACJLO9UEiqtTpaB3CpPQQ"
        ]
    },
    "погладити": {
        "text": "погладив(ла) по голові",
        "media": [
            "CgACAgQAAxkBAAIFj2pxBUGbcF5v0cmJnEIIjquIi04QAALfAwACYl6NUxwvWMwgW-fgPQQ"
        ]
    },
    
    # --- Агресивні та бойові дії ---
    "вдарити": {
        "text": "дав(ла) ляпаса",
        "media": [
            "CgACAgQAAxkBAAIFi2pxBJJ9G3d1ekXbto3XtmZww08dAAINAwACk-MMU4BAYuiTKueyPQQ"
        ]
    },
    "копнути": {
        "text": "копнув(ла)",
        "media": [
            "CgACAgIAAyEFAATMVs9CAAEBnuJqcQeAWFZ_t0ZJD1Cg8CVnSlLLnQACt1MAAsMe2UgBiUAfPpeGvD0E"
        ]
    },
    "вистрілити": {
        "text": "вистрілив(ла) з пістолета в",
        "media": [
            "CgACAgIAAxkBAAIGBGp0_QUKzrazgBW3h9l3MwYOYmxPAAKwpwACcgSoS90i-jbSbSIxPQQ"
        ]
    },
    "убити": {
        "text": "ліквідував(ла)",
        "media": [
            "CgACAgIAAxkBAAIGBmp0_WyQ3grW0FleQxWOp9xbPXrQAAK5pwACcgSoS9bMVDDWKeCOPQQ"
        ]
    },
    "цеглина": {
        "text": "скинув(ла) цеглину на голову",
        "media": [
            "CgACAgIAAxkBAAIGCGp0_auJpXA5KHzBr6GcTs3fnKrVAAK-pwACcgSoS2ixP4wvcTnCPQQ"
        ]
    },
    "тапок": {
        "text": "кинув(ла) тапком у",
        "media": [
            "CgACAgIAAxkBAAIGCmp0_fb5mUeThZwaKHW5qaE2DnugAALEpwACcgSoS_0aCzKophpKPQQ"
        ]
    },

    # --- Жартівливі та трешові дії ---
    "плюнути": {
        "text": "плюнув(ла) в обличчя",
        "media": [
            "CgACAgIAAxkBAAIGDGp0_laJVh6Cvoy9Qla-6-G_8R7hAALLpwACcgSoS4GAE0x6yHpGPQQ"
        ]
    },
    "обісцяти": {
        "text": "жорстко помітив(ла) територію на",
        "media": [
            "CgACAgIAAxkBAAIGDmp0_rDrl88AAXcSJpXvSXkAAVij-usAAtCnAAJyBKhL7KzA_sBfK7c9BA"
        ]
    },
    "ущипнути": {
        "text": "ущипнув(ла) за дупу",
        "media": [
            "CgACAgIAAxkBAAIGEGp0_xWiDG28Q9tiZ3MmY2bQ1meJAALZpwACcgSoSzudfAsc8vyMPQQ"
        ]
    },
    "святити": {
        "text": "покропив(ла) святою водою",
        "media": [
            "CgACAgIAAxkBAAIGEmp0_0kOV5tB9j-gfSD41pcVFlPpAALcpwACcgSoS_Nno0vIBj2VPQQ"
        ]
    },

    # --- 18+ / Жорсткі дії ---
    "всунуть": {
        "text": "жорстко всунув(ла)",
        "media": [
            "CAACAgIAAxkBAAERq_VqdPkbpw5gSCmws18GtTCXE09nlgAC7BgAAk1r-Us7v13flWv7Ez0E"
        ]
    },
    "виебати": {
        "text": "жорстко покарав(ла)",
        "media": [
            "CgACAgIAAxkBAAIFkWpxBYhTEALJ9ttWT_7GbsT4hVxgAALDMAACVomZSkFGVQPimQeUPQQ"
        ]
    },
    "трахнути": {
        "text": "жорстко покарав(ла)",
        "media": [
            "CgACAgIAAxkBAAIFkWpxBYhTEALJ9ttWT_7GbsT4hVxgAALDMAACVomZSkFGVQPimQeUPQQ"
        ]
    },
    "відшмагати": {
        "text": "відшмагав(ла) ременем",
        "media": [
            "CgACAgIAAxkBAAIGFGp0_5phQtsbknvH4tIwde2Y6lLJAALepwACcgSoS1Y9TrG9dhWuPQQ"
        ]
    },
    "зв'язати": {
        "text": "міцно зв'язав(ла) мотузкою",
        "media": [
            "CgACAgIAAxkBAAIGFmp0_-qwmDC7DwyvIR_ZmGWkp777AALipwACcgSoS6dXzjNf0eaBPQQ"
        ]
    }
}

def get_user_mention(user):
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def is_rp_command(message):
    if not message or not message.text:
        return False
    word = message.text.strip().split()[0].lower().lstrip('/!.')
    return word in ACTIONS_DB

# ------------------- ОСНОВНИЙ ХЕНДЛЕР RP-КОМАНД -------------------
@bot.message_handler(func=is_rp_command)
def handle_rp_words(message):
    word = message.text.strip().split()[0].lower().lstrip('/!.')
    action_info = ACTIONS_DB[word]

    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Зроби **відповідь (reply)** на повідомлення того, до кого застосовуєш дію!")
        return

    if message.from_user.id == message.reply_to_message.from_user.id:
        bot.reply_to(message, "Ти не можеш застосувати цю дію до самого себе! 😅")
        return

    sender = get_user_mention(message.from_user)
    target = get_user_mention(message.reply_to_message.from_user)
    caption = f"✨ {sender} {action_info['text']} {target}!"

    # Перевіряємо, чи є гіфки/фото в базі
    media_list = action_info.get("media", [])
    
    if media_list:
        chosen_media = random.choice(media_list)
        try:
            # Спочатку пробуємо відправити як анімацію (GIF)
            bot.send_animation(
                chat_id=message.chat.id,
                animation=chosen_media,
                caption=caption,
                parse_mode="HTML",
                reply_to_message_id=message.reply_to_message.message_id
            )
            return
        except Exception:
            try:
                # Якщо це звичайне фото, а не GIF
                bot.send_photo(
                    chat_id=message.chat.id,
                    photo=chosen_media,
                    caption=caption,
                    parse_mode="HTML",
                    reply_to_message_id=message.reply_to_message.message_id
                )
                return
            except Exception as e:
                print(f"[RP Media Error] {e}")

    # Фолбек: якщо медіа немає або сталась помилка — відправляємо текст
    bot.send_message(
        chat_id=message.chat.id,
        text=caption,
        parse_mode="HTML",
        reply_to_message_id=message.reply_to_message.message_id
    )

# ------------------- ПОМІЧНИК ДЛЯ ОТРИМАННЯ FILE_ID -------------------
# Надішли боту в приватні повідомлення будь-яку гіфку чи фото, і він напише її file_id
@bot.message_handler(content_types=['animation', 'photo'])
def get_media_file_id(message):
    if check_flood(message):
        return
    if message.chat.type == 'private':
        if message.animation:
            bot.reply_to(message, f"<b>file_id вашої GIF:</b>\n<code>{message.animation.file_id}</code>", parse_mode="HTML")
        elif message.photo:
            bot.reply_to(message, f"<b>file_id вашого фото:</b>\n<code>{message.photo[-1].file_id}</code>", parse_mode="HTML")
        
# ===================================================================
# 📢 ВІДПРАВКА АНОНСУ "ДАЙВІНЧИК UA" ПРЯМО В ГРУПУ
# ===================================================================

# ⚙️ НАЛАШТУВАННЯ
ADMIN_IDS = [5512316636]  # 👈 Заміни на свій Telegram ID
TARGET_GROUP_ID = -1003428241218  # 👈 Заміни на ID твоєї групи (починається з -100)
NEW_BOT_USERNAME = "ukr_diving_bot"


@bot.message_handler(commands=["promo", "dayvinchik", "дайвінчик"])
def send_promo_to_group(message):
    # Перевірка на адміна
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(
            message,
            "❌ <b>У вас немає прав для використання цієї команди!</b>",
            parse_mode="HTML",
        )
        return

    # Текст анонсу
    promo_text = (
        "🔥 <b>Зустрічайте нашого нового бота — Дайвінчик UA!</b> 🇺🇦\n\n"
        "Шукаєш нових друзів, цікаве спілкування або навіть другу половинку? "
        "Ми запустили зручного бота для знайомств!\n\n"
        "✨ <b>Що на тебе чекає:</b>\n"
        "📍 Знайомства поруч із тобою\n"
        "📸 Зручна анкета та простий пошук\n"
        "💬 Приємні співрозмовники\n\n"
        "🚀 <b>Тисни кнопку нижче, створюй анкету та знаходь нових людей вже зараз!</b>"
    )

    # Inline-кнопка
    markup = InlineKeyboardMarkup()
    btn_start = InlineKeyboardButton(
        text="🚀 Знайти нові знайомства",
        url=f"https://t.me/{NEW_BOT_USERNAME}?start=from_drago",
    )
    markup.add(btn_start)

    try:
        # Відправляємо анонс ПРЯМО В ТВОЮ ГРУПУ
        sent_msg = bot.send_message(
            chat_id=TARGET_GROUP_ID,
            text=promo_text,
            parse_mode="HTML",
            reply_markup=markup,
        )

        bot.reply_to(
            message,
            "✅ <b>Анонс успішно відправлено в групу!</b>",
            parse_mode="HTML",
        )

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ <b>Помилка відправки в групу:</b>\n<code>{e}</code>\n\n"
            f"<i>Переконайся, що Drago є в групі та має права адміністратора!</i>",
            parse_mode="HTML",
        )


# ===================================================================
# 🎮 DISCORD ІНТЕГРАЦІЯ (Стежимо за трансляціями)
# ===================================================================
intents = discord.Intents.default()
intents.voice_states = True     # Щоб бачити, хто заходить в голос і вмикає стрім
intents.members = True          # Щоб бачити нікнейми

discord_client = discord.Client(intents=intents)

@discord_client.event
async def on_ready():
    print(f"🤖 Discord-агент Драго успішно підключився як {discord_client.user}!")

@discord_client.event
async def on_voice_state_update(member, before, after):
    # Перевіряємо, чи користувач запустив трансляцію екрана/стрім
    # before.self_stream було False, а після оновлення after.self_stream стало True
    if not before.self_stream and after.self_stream:
        user_name = member.display_name
        channel_name = after.channel.name if after.channel else "Голосовий канал"
        
        # 🔗 Збираємо ID для створення прямого посилання на трансляцію
        guild_id = member.guild.id
        channel_id = after.channel.id if after.channel else 0
        discord_stream_url = f"https://discord.com/channels/{guild_id}/{channel_id}"
        
        # Текст анонсу в Telegram з вбудованим клікабельним лінком
        announcement = (
            f"🎮 <b>ДРАГО ПАЛИТЬ КОНТОРУ В DISCORD!</b> 🚨\n\n"
            f"Чувак <b>{user_name}</b> не схотів сидіти тихо і запустив <b>живу трансляцію</b> "
            f"у голосовому каналі <i>«{channel_name}»</i>!\n\n"
            f"🚀 <b><a href='{discord_stream_url}'>👉 ЗАЛЕТІТИ НА СТРІМ 👈</a></b>\n\n"
            f"🍿 <i>Шоу почалося, бандити! Тисніть на лінк вище і залітайте, подивимося що він там мутить!</i>"
        )
        
        # Відправляємо повідомлення в наш Телеграм чат
        try:
            bot.send_message(TELEGRAM_CHAT_ID, announcement, parse_mode="HTML")
        except Exception as e:
            print(f"Помилка відправки анонсу стріму в ТГ: {e}")

def _build_discord_client():
    """Створює НОВИЙ екземпляр discord.Client і навішує на нього вже
    визначені обробники подій. Discord.py не дозволяє повторно
    запускати .run() на клієнті, який вже був закритий (після падіння
    чи 429) — тож при кожній спробі підключення робимо свіжий клієнт."""
    client = discord.Client(intents=intents)
    client.event(on_ready)
    client.event(on_voice_state_update)
    return client

# Функція для запуску ДС бота в окремому потоці
def run_discord():
    global discord_client

    if not DISCORD_TOKEN or DISCORD_TOKEN == 'ТВІЙ_ДИСКОРД_ТОКЕН':
        print("⚠️ DISCORD_TOKEN не налаштовано. Модуль Discord спить.")
        return

    # 🔁 Ретрай з експоненційною затримкою: якщо Discord відповідає 429
    # (Too Many Requests) при логіні — наприклад, через часті рестарти
    # сервісу на Render — ми більше НЕ вбиваємо потік назавжди, а чекаємо
    # і пробуємо підключитися знову, поки не вийде.
    delay = 30
    max_delay = 900  # 15 хв — стеля затримки

    while True:
        discord_client = _build_discord_client()
        try:
            discord_client.run(DISCORD_TOKEN)
            # Якщо run() завершився без винятку (наприклад, клієнт коректно
            # закрили) — не намагаємось перезапускати нескінченно.
            print("ℹ️ Discord-клієнт зупинився штатно.")
            break
        except discord.errors.HTTPException as e:
            if getattr(e, "status", None) == 429:
                print(f"⛔ Discord заблокував логін (429 Too Many Requests). "
                      f"Чекаю {delay}с і пробую знову...")
            else:
                print(f"⚠️ Discord HTTPException: {e}. Чекаю {delay}с і пробую знову...")
        except discord.errors.LoginFailure as e:
            print(f"❌ Невірний DISCORD_TOKEN, зупиняю спроби: {e}")
            break
        except Exception as e:
            print(f"⚠️ Discord-клієнт впав з помилкою: {e}. Чекаю {delay}с і пробую знову...")

        time.sleep(delay)
        delay = min(delay * 2, max_delay)

# ===================================================================
# 📋 ІНТЕРАКТИВНЕ МЕНЮ /help З КНОПКАМИ ТА СТОРІНКАМИ
# ===================================================================

# Словник із вмістом сторінок допомоги
HELP_PAGES = {
    "page_ai": (
        "🗣 <b>ШІ ТА СНІПЕТИ СПІЛКУВАННЯ:</b>\n\n"
        "• <b>Драго</b> — просто згадай ім'я або зроби реплай на моє повідомлення, щоб поспілкуватися.\n"
        "• <b>Голосові повідомлення</b> — надішли мені голосове, і я відповім тобі звуком.\n"
        "• <b>\"скажи\" / \"голосове\"</b> — напиши це в тексті, щоб я надиктував відповідь голосом.\n"
        "• <b>Аналіз фото</b> — надішли фото з підписом і згадкою Драго, щоб я розібрав компромат."
    ),
    "page_stats": (
        "📊 <b>РОЗВІДКА ТА СТАТИСТИКА:</b>\n\n"
        "• /profile або /профіль — твій повний паспорт авторитета.\n"
        "• /top або /stats — топ найактивніших базікал чату.\n"
        "• /rich, /багатії або /олігархи — топ найбагатших гравців чату.\n"
        "• /sleepers або /сонні — виклик тих, хто заліг на дно і мовчить.\n"
        "• /dossier або /досьє — <i>(реплай)</i> скласти секретну справу СБУ на юзера.\n"
        "• /news або /новини — гарячий випуск мемних новин з переписок."
    ),
    "page_fun": (
        "🎰 <b>РОЗВАГИ, МУЗИКА ТА АРТ:</b>\n\n"
        "• /крокодил — почати гру в Крокодила (умови й топ: /крокодил топ, скіп, стоп).\n"
        "• /підказка або /підкажи — підказка ведучому під час раунду Крокодила.\n"
        "• /mafia або /мафія — зібрати братву на гру в Мафію.\n"
        "• /start_mafia — почати роздачу ролей, коли лоббі зібралось.\n"
        "• /stop_mafia або /зупинити_мафію — скасувати поточну гру в Мафію.\n"
        "• /duel або /дуель [ставка] — <i>(реплай)</i> викликати когось на дуель на гроші.\n"
        "• /accept або /прийняти — прийняти виклик на дуель.\n"
        "• /music або /найти [назва] — знайти та скачати аудіотрек.\n"
        "• /generate [опис англійською] — згенерувати арт через ШІ (Pollinations).\n"
        "• @all / .збір — загальний збір чату (тег усіх активних).\n"
        "• /menu або /меню — меню смаколиків."
    ),
    "page_marriage": (
        "💍 <b>ШЛЮБИ ТА СІМЕЙНИЙ БЮДЖЕТ:</b>\n\n"
        "• /marry або /одруження — зробити пропозицію (у відповідь на повідомлення).\n"
        "• /marriages або /пари / /шлюби — список усіх бандитських пар чату.\n"
        "• /gift або /подарувати [сума] — переказати гроші своїй другій половинці.\n"
        "• /family_bank або /спільний_баланс — переглянути стан сімейного сейфу.\n"
        "• /поповнити_банк [сума] — закинути гроші в сімейний банк.\n"
        "• /зняти_банк [сума] — зняти гроші з сімейного банку.\n"
        "• /divorce або /розлучення — розірвати шлюб та поділити банк 50/50."
    ),
    "page_biz": (
        "🏢 <b>БІЗНЕС-ІМПЕРІЯ (ПАСИВНИЙ ДОХІД):</b>\n\n"
        "• /biz або /бізнеси — каталог підприємств та стан твоєї імперії.\n"
        "• /купити_бізнес [код] — придбати об'єкт (наприклад: <i>/купити_бізнес kebab</i>).\n"
        "• /зібрати або /каса / /collect — зняти накопичений прибуток з усіх бізнесів.\n"
        "• /продати_бізнес [код] — продати бізнес за 75% від початкової вартості.\n"
        "• /swap або /поміняти [№1] [№2] — поміняти місцями бізнеси у своєму списку."
    ),
    "page_shop": (
        "💰 <b>МОНОПОЛІЯ ТА ЧОРНИЙ РИНОК:</b>\n\n"
        "• /shop або /магазин — чорний ринок (тачки, вілли, аксесуари).\n"
        "• /buy або /купити [код] — придбати річ з ринку (наприклад: <i>/купити bmw</i>).\n"
        "• /money / /balance / /майно / /гаманець — огляд балансу та картинки майна.\n"
        "• /pay або /передати [сума] — переказати бабки іншому братку (реплай або @username)."
    ),
    "page_admin": (
        "🛡 <b>МОДЕРАЦІЯ ТА КЕРУВАННЯ (ДЛЯ АДМІНІВ):</b>\n\n"
        "• /mute [час] [причина] — замутити порушника (наприклад: <i>/mute 30m спам</i>).\n"
        "• /unmute — зняти обмеження з користувача.\n"
        "• /ban [причина] / /unban — забанити або розбанити гравця.\n"
        "• /kick — вигнати користувача з чату.\n"
        "• /clear [число] — очистити вказану кількість повідомлень.\n"
        "• /addmod / /delmod / /modlist — керування модераторами бота.\n"
        "• /admin або /адмін — головна панель керування ботом.\n"
        "• /check_users, /cleanup або /чистка — прибрати з бази тих, хто вийшов з чату.\n"
        "• /broadcast — розсилка повідомлення від імені бота.\n"
        "• /promo, /dayvinchik або /дайвінчик — надіслати промо-анонс у чат."
    )
}

def get_help_keyboard(current_page="page_ai"):
    """Формує клавіатуру перемикання сторінок допомоги"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    buttons = [
        types.InlineKeyboardButton("🗣 ШІ", callback_data="help_page_ai"),
        types.InlineKeyboardButton("📊 Статистика", callback_data="help_page_stats"),
        types.InlineKeyboardButton("🎰 Розваги", callback_data="help_page_fun"),
        types.InlineKeyboardButton("💍 Шлюби", callback_data="help_page_marriage"),
        types.InlineKeyboardButton("🏢 Бізнес", callback_data="help_page_biz"),
        types.InlineKeyboardButton("💰 Ринок", callback_data="help_page_shop"),
        types.InlineKeyboardButton("🛡 Модерація", callback_data="help_page_admin")
    ]
    
    # Відмічаємо активну кнопку
    for btn in buttons:
        if btn.callback_data == f"help_{current_page}":
            btn.text = f"• {btn.text} •"
            
    markup.add(*buttons)
    return markup

@bot.message_handler(commands=['start', 'help', 'команди', 'info'])
def show_all_commands(message):
    if is_user_banned(message.from_user.id):
        return

    first_page_text = (
        "<b>📁 ОПЕРАТИВНА БАЗА ДАНИХ ДРАГО</b>\n"
        "───────────────────────\n"
        f"{HELP_PAGES['page_ai']}\n"
        "───────────────────────\n"
        "👇 <i>Тисни на кнопки нижче, щоб гортати категорії команд:</i>"
    )

    bot.reply_to(
        message, 
        first_page_text, 
        parse_mode="HTML", 
        reply_markup=get_help_keyboard("page_ai")
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('help_page_'))
def handle_help_page_change(call):
    page_key = call.data.replace("help_", "")
    
    if page_key not in HELP_PAGES:
        bot.answer_callback_query(call.id, "❌ Сторінку не знайдено.")
        return

    new_text = (
        "<b>📁 ОПЕРАТИВНА БАЗА ДАНИХ ДРАГО</b>\n"
        "───────────────────────\n"
        f"{HELP_PAGES[page_key]}\n"
        "───────────────────────\n"
        "👇 <i>Тисни на кнопки нижче, щоб гортати категорії команд:</i>"
    )

    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=new_text,
            parse_mode="HTML",
            reply_markup=get_help_keyboard(page_key)
        )
        bot.answer_callback_query(call.id)
    except Exception:
        # Помилка виникає, якщо користувач тисне на вже відкриту сторінку
        bot.answer_callback_query(call.id)

# ===================================================================
# 💍 СИСТЕМА ОДРУЖЕННЯ ТА СІМЕЙНОГО БЮДЖЕТУ
# ===================================================================

def get_user_balance(user_id: int) -> int:
    """Отримує поточний баланс користувача з бази даних"""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
            conn.close()
        return res[0] if res and res[0] is not None else 0
    except Exception as e:
        print(f"Помилка get_user_balance: {e}")
        return 0


def update_user_balance(user_id: int, amount: int):
    """Змінює баланс користувача (на + або -)"""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO stats (user_id, balance) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET balance = stats.balance + EXCLUDED.balance;
                """, (user_id, amount))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка update_user_balance: {e}")


def get_marriage_pair(user_id):
    """Повертає spouse_id та унікальний pair_key для спільного банку"""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT user1_id, user2_id FROM marriages WHERE user1_id = %s OR user2_id = %s", 
                    (user_id, user_id)
                )
                res = cursor.fetchone()
            conn.close()
        if res:
            u1, u2 = res
            pair_key = f"{min(u1, u2)}_{max(u1, u2)}"
            spouse_id = u2 if user_id == u1 else u1
            return spouse_id, pair_key
    except Exception as e:
        print(f"Помилка get_marriage_pair: {e}")
    return None, None


# 1. 💍 ПРОПОЗИЦІЯ ОДРУЖЕННЯ
@bot.message_handler(commands=['marry', 'одруження', 'шлюб'])
def propose_marriage(message):
    if is_user_banned(message.from_user.id): return
    
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Кому ти каблучку пхаєш? Зроби реплай на повідомлення того, з ким хочеш розписатися!")
        return
        
    user1 = message.from_user
    user2 = message.reply_to_message.from_user
    
    if user1.id == user2.id:
        bot.reply_to(message, "🤡 Шиза косить ряди. Сам з собою одружуватися зібрався?")
        return
        
    if user2.is_bot:
        bot.reply_to(message, "🛑 На ботах не одружуємося! Відхилено.")
        return

    # Перевірка, чи хтось із них вже у шлюбі
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT user1_id, user2_id FROM marriages WHERE user1_id IN (%s, %s) OR user2_id IN (%s, %s)", 
                    (user1.id, user2.id, user1.id, user2.id)
                )
                m_res = cursor.fetchall()
            conn.close()

            if m_res:
                for row in m_res:
                    if user1.id in row:
                        bot.reply_to(message, "🚨 Ти вже маєш штамп у паспорті! Спочатку розлучись (/розлучення).")
                        return
                    if user2.id in row:
                        bot.reply_to(message, "🚨 Ця людина вже зайнята! Шукай вільну жертву.")
                        return
    except Exception as e:
        print(f"Помилка БД при перевірці шлюбу: {e}")
        bot.reply_to(message, "❌ Помилка перевірки статусу шлюбу.")
        return

    # Створюємо кнопки
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_yes = types.InlineKeyboardButton("💍 ТАК, Я ЗГОДЕН(НА)", callback_data=f"marry_yes_{user1.id}_{user2.id}")
    btn_no = types.InlineKeyboardButton("❌ НІ, ПІШОВ ТИ", callback_data=f"marry_no_{user1.id}_{user2.id}")
    markup.add(btn_yes, btn_no)
    
    target_name = user2.first_name.replace('<', '&lt;').replace('>', '&gt;')
    initiator_name = user1.first_name.replace('<', '&lt;').replace('>', '&gt;')
    
    bot.send_message(
        message.chat.id, 
        f"💒 <b>ОФІЦІЙНА ЗАЯВА В РАЦС СБУ!</b>\n\n"
        f"Громадянин(ка) <b>{initiator_name}</b> робить пропозицію <b>{target_name}</b>!\n"
        f"Що скажеш? Твоя відповідь вирішить вашу долю.", 
        reply_markup=markup, 
        parse_mode="HTML"
    )


# Обробка натискання кнопок шлюбу
@bot.callback_query_handler(func=lambda call: call.data.startswith('marry_'))
def handle_marriage_callbacks(call):
    parts = call.data.split('_')
    action = parts[1]
    user1_id = int(parts[2])
    user2_id = int(parts[3])
    
    if call.from_user.id != user2_id:
        bot.answer_callback_query(call.id, "🛑 Куди лізеш? Це не тобі пропозицію роблять!", show_alert=True)
        return

    if action == 'no':
        bot.edit_message_text(
            "💔 <b>ЖОРСТКА ВІДМОВА!</b>\nЗаява розірвана, серце розбите, каблучка здана в ломбард.", 
            call.message.chat.id, 
            call.message.message_id, 
            parse_mode="HTML"
        )
        return
        
    if action == 'yes':
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    # Записуємо щасливу пару в базу
                    cursor.execute("INSERT INTO marriages (user1_id, user2_id) VALUES (%s, %s)", (user1_id, user2_id))
                    
                    # Створюємо сімейний банк
                    pair_key = f"{min(user1_id, user2_id)}_{max(user1_id, user2_id)}"
                    cursor.execute("INSERT INTO shared_wallets (pair_id, balance) VALUES (%s, 0) ON CONFLICT DO NOTHING", (pair_key,))
                    conn.commit()
                conn.close()
                
            bot.edit_message_text(
                "🎉 <b>НОВИЙ БАНДИТСЬКИЙ СОЮЗ!</b> 🥂\n\n"
                "Драго офіційно оголошує вас сім'єю!\n"
                "Тепер ви — одне кримінальне угруповання. Гірко! 💋\n\n"
                "💡 <i>Вам доступні команди:\n"
                "• <code>/спільний_баланс</code>\n"
                "• <code>/поповнити_банк [сума]</code>\n"
                "• <code>/зняти_банк [сума]</code>\n"
                "• <code>/подарувати [сума]</code></i>", 
                call.message.chat.id, 
                call.message.message_id, 
                parse_mode="HTML"
            )
        except Exception as e:
            bot.answer_callback_query(call.id, f"Помилка БД: {e}", show_alert=True)


# 2. 🎁 ПОДАРУВАТИ ГРОШІ ПАРТНЕРУ (/подарувати [сума])
@bot.message_handler(commands=['gift', 'подарувати'])
def gift_to_spouse(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    spouse_id, _ = get_marriage_pair(user_id)

    if not spouse_id:
        bot.reply_to(message, "💔 Ти ж самотній вовк, кому дарувати?")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❌ <b>Вкажи суму:</b> <code>/подарувати 25000</code>", parse_mode="HTML")
        return

    try:
        amount = int(args[1])
    except ValueError:
        bot.reply_to(message, "❌ Сума має бути цілим числом!")
        return

    if amount <= 0:
        bot.reply_to(message, "❌ Сума подарунка має бути більшою за 0!")
        return

    if get_user_balance(user_id) < amount:
        bot.reply_to(message, "💸 У тебе немає стільки грошей!")
        return

    update_user_balance(user_id, -amount)
    update_user_balance(spouse_id, amount)

    bot.reply_to(message, f"🎁 <b>Романтика!</b> Ти подарував своїй другій половинці <b>{amount:,} грн</b>! ❤️", parse_mode="HTML")


# 3. 🏦 СПІЛЬНИЙ БАЛАНС ПАРИ (/спільний_баланс)
@bot.message_handler(commands=['family_bank', 'спільний_баланс', 'семейный_бюджет'])
def show_family_bank(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    spouse_id, pair_key = get_marriage_pair(user_id)

    if not spouse_id:
        bot.reply_to(message, "💔 Спочатку знайди собі пару через <code>/одруження</code>!", parse_mode="HTML")
        return

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM shared_wallets WHERE pair_id = %s", (pair_key,))
                res = cursor.fetchone()
                if not res:
                    cursor.execute("INSERT INTO shared_wallets (pair_id, balance) VALUES (%s, 0)", (pair_key,))
                    conn.commit()
                    bank_bal = 0
                else:
                    bank_bal = res[0] or 0
            conn.close()

        bot.reply_to(
            message, 
            f"👩‍❤️‍👨 <b>СІМЕЙНИЙ СЕЙФ</b>\n"
            f"───────────────────────\n"
            f"💰 У банку пари лежить: <b>{bank_bal:,} грн</b>\n\n"
            f"📥 <i>Закинути: <code>/поповнити_банк [сума]</code></i>\n"
            f"📤 <i>Зняти: <code>/зняти_банк [сума]</code></i>", 
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка БД: {e}")


# 4. 📥 ПОПОВНЕННЯ СІМЕЙНОГО БАНКУ (/поповнити_банк [сума])
@bot.message_handler(func=lambda message: message.text and any(
    message.text.strip().lower().startswith(cmd) for cmd in ['/поповнити_банк', '/add_family_bank', '/пополнить_банк']
))
def add_family_bank(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    spouse_id, pair_key = get_marriage_pair(user_id)

    if not spouse_id:
        bot.reply_to(message, "💔 Спочатку знайди собі пару!")
        return

    args = message.text.strip().split()
    if len(args) < 2:
        bot.reply_to(message, "❌ <b>Формат:</b> <code>/поповнити_банк 50000</code>", parse_mode="HTML")
        return

    try:
        amount = int(args[1])
    except ValueError:
        bot.reply_to(message, "❌ Сума має бути цілим числом!")
        return

    if amount <= 0:
        bot.reply_to(message, "❌ Сума має бути більшою за 0!")
        return

    user_bal = get_user_balance(user_id)
    if user_bal < amount:
        bot.reply_to(message, f"💸 <b>Брак коштів!</b> На гаманці лише <b>{user_bal:,} грн</b>.", parse_mode="HTML")
        return

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM shared_wallets WHERE pair_id = %s", (pair_key,))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO shared_wallets (pair_id, balance) VALUES (%s, 0)", (pair_key,))

                cursor.execute("UPDATE shared_wallets SET balance = balance + %s WHERE pair_id = %s", (amount, pair_key))
                conn.commit()
            conn.close()

        update_user_balance(user_id, -amount)
        bot.reply_to(message, f"🏦 Ти успішно закинув <b>{amount:,} грн</b> у сімейний банк!", parse_mode="HTML")

    except Exception as e:
        print(f"Помилка поповнення банку: {e}")
        bot.reply_to(message, f"❌ Помилка БД: <code>{e}</code>", parse_mode="HTML")


# 4.1 📤 ЗНЯТТЯ З СІМЕЙНОГО БАНКУ (/зняти_банк [сума])
@bot.message_handler(func=lambda message: message.text and any(
    message.text.strip().lower().startswith(cmd) for cmd in ['/зняти_банк', '/withdraw_family_bank', '/снять_банк']
))
def withdraw_family_bank(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    spouse_id, pair_key = get_marriage_pair(user_id)

    if not spouse_id:
        bot.reply_to(message, "💔 Спочатку знайди собі пару!")
        return

    args = message.text.strip().split()
    if len(args) < 2:
        bot.reply_to(message, "❌ <b>Формат:</b> <code>/зняти_банк 10000</code>", parse_mode="HTML")
        return

    try:
        amount = int(args[1])
    except ValueError:
        bot.reply_to(message, "❌ Сума має бути цілим числом!")
        return

    if amount <= 0:
        bot.reply_to(message, "❌ Сума має бути більшою за 0!")
        return

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM shared_wallets WHERE pair_id = %s", (pair_key,))
                res = cursor.fetchone()
                current_bal = res[0] if res else 0

                if current_bal < amount:
                    bot.reply_to(message, f"💸 У сімейному банку немає стільки грошей! (Є тільки <b>{current_bal:,} грн</b>)", parse_mode="HTML")
                    conn.close()
                    return

                cursor.execute("UPDATE shared_wallets SET balance = balance - %s WHERE pair_id = %s", (amount, pair_key))
                conn.commit()
            conn.close()

        update_user_balance(user_id, amount)
        bot.reply_to(message, f"💸 Ти зняв <b>{amount:,} грн</b> із сімейного банку собі на рахунок!", parse_mode="HTML")

    except Exception as e:
        print(f"Помилка зняття з банку: {e}")
        bot.reply_to(message, f"❌ Помилка БД: <code>{e}</code>", parse_mode="HTML")


# 5. 💔 РОЗЛУЧЕННЯ З ПОДІЛОМ МАЙНА/БАНКУ (/розлучення)
@bot.message_handler(commands=['divorce', 'розлучення'])
def divorce_command(message):
    if is_user_banned(message.from_user.id): return
    
    user_id = message.from_user.id
    spouse_id, pair_key = get_marriage_pair(user_id)

    if not spouse_id:
        bot.reply_to(message, "🤡 Ти й так самотній вовк! З ким ти розлучатися зібрався?")
        return

    try:
        shared_money = 0
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                if pair_key:
                    cursor.execute("SELECT balance FROM shared_wallets WHERE pair_id = %s", (pair_key,))
                    res = cursor.fetchone()
                    if res: 
                        shared_money = res[0] or 0

                # Видаляємо запис про шлюб та банк
                cursor.execute("DELETE FROM marriages WHERE user1_id = %s OR user2_id = %s", (user_id, user_id))
                if pair_key:
                    cursor.execute("DELETE FROM shared_wallets WHERE pair_id = %s", (pair_key,))
                
                conn.commit()
            conn.close()

        # Поділ банку 50/50
        if shared_money > 0:
            half = shared_money // 2
            update_user_balance(user_id, half)
            update_user_balance(spouse_id, half)
            div_text = f"\n⚖️ Спільний банк <b>{shared_money:,} грн</b> поділено 50/50: по <b>{half:,} грн</b> кожному!"
        else:
            div_text = ""

        bot.reply_to(
            message, 
            f"✂️ <b>СІМ'Я РОЗПАЛАСЯ!</b>\nДраго порвав ваші паспорти. Ви офіційно вільні.{div_text}", 
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Помилка розлучення: {e}")
        bot.reply_to(message, f"❌ Помилка БД при розлученні: {e}")

# ===================================================================
# 📦 ІНІЦІАЛІЗАЦІЯ БАЗИ ДАНИХ (Всі CREATE TABLE тримаємо тут!)
# ===================================================================
def init_db():
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 1. Таблиця шлюбів
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS marriages (
                    user1_id BIGINT,
                    user2_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user1_id, user2_id)
                )
            """)
            # 2. Спільні гаманці для пар
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shared_wallets (
                    pair_id VARCHAR(100) PRIMARY KEY,
                    balance BIGINT DEFAULT 0
                )
            """)
            conn.commit()
        conn.close()


# ===================================================================
# 📋 СПИСОК УСІХ ПАР ЧАТУ (/marriages, /пари, /шлюби)
# ===================================================================
@bot.message_handler(commands=['marriages', 'пари', 'шлюби'])
def show_all_marriages(message):
    if is_user_banned(message.from_user.id): return

    chat_id = message.chat.id
    try:
        bot.send_chat_action(chat_id, 'typing')
        
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Зв'язуємо таблицю шлюбів із таблицею статистики, щоб витягнути імена обох партнерів
                cursor.execute("""
                    SELECT s1.name, s2.name 
                    FROM marriages m
                    JOIN stats s1 ON m.user1_id = s1.user_id
                    JOIN stats s2 ON m.user2_id = s2.user_id
                """)
                couples = cursor.fetchall()
            conn.close()

        # Якщо база пуста
        if not couples:
            bot.reply_to(
                message, 
                "🕸 <b>РАЦС пустує!</b>\nУ цьому чаті лише самотні вовки та незалежні левиці. Жодного союзу не зареєстровано.", 
                parse_mode="HTML"
            )
            return

        # Формуємо красивий список
        response_lines = ["💍 <b>ОФІЦІЙНІ БАНДИТСЬКІ ПАРИ ЧАТУ:</b> 💍\n"]
        
        for idx, (name1, name2) in enumerate(couples, 1):
            clean_name1 = name1.replace("<", "&lt;").replace(">", "&gt;") if name1 else "Хтось"
            clean_name2 = name2.replace("<", "&lt;").replace(">", "&gt;") if name2 else "Хтось"
            response_lines.append(f"{idx}. {clean_name1} 💘 {clean_name2}")

        response_lines.append("\n<i>Хто ще без пари? Команда /шлюб чекає на вас!</i>")
        
        bot.reply_to(message, "\n".join(response_lines), parse_mode="HTML")

    except Exception as e:
        print(f"Помилка виведення списку шлюбів: {e}")
        bot.reply_to(message, "❌ Щось архіви РАЦСу згоріли (помилка БД). Драго не може знайти документи.")


# ===================================================================
# 🧠 ГЕНДЕРНА СИСТЕМА
# ===================================================================

def analyze_gender_from_user(user) -> str:
    """Визначає стать за іменем/нікнеймом (спочатку швидкими правилами, потім Gemini)."""
    first_name = (user.first_name or "").strip()
    
    # ⚡ 1. Швидка перевірка за типовими українськими/слов'янськими закінченнями імен
    if first_name:
        fn_lower = first_name.lower()
        # Популярні чоловічі закінчення або імена-винятки (Микола, Ілля, Микита)
        if fn_lower.endswith(('слав', 'мир', 'ша', 'он', 'ан', 'ор', 'ей', 'ій')) or fn_lower in ['микола', 'ілля', 'микита', 'михась']:
            return 'Хлопець'
        # Жіночі закінчення (а, я) — окрім чоловічих винятків
        elif fn_lower.endswith(('а', 'я')) and fn_lower not in ['микола', 'ілля', 'микита', 'михась', 'саша', 'женя', 'паша']:
            return 'Дівчина'

    # 🤖 2. Якщо за правилами не визначили — запитуємо Gemini
    name_parts = []
    if user.first_name:
        name_parts.append(user.first_name)
    if user.last_name:
        name_parts.append(user.last_name)
    if user.username:
        name_parts.append(user.username)
    
    name_info = " ".join(name_parts)
    if not name_info:
        return 'Невідомо'

    prompt = (
        f"Визнач стать людини тільки по імені/нікнейму: '{name_info}'. "
        "Якщо ім'я українське/слов'янське — визначай по закінченню. "
        "Відповідай ТІЛЬКИ одним словом: Хлопець, Дівчина або Невідомо."
    )
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        if result in ['Хлопець', 'Дівчина']:
            return result
    except Exception as e:
        print(f"Помилка визначення статі через Gemini (user): {e}")
        
    return 'Невідомо'


def analyze_gender_from_text(text: str) -> str:
    """Визначає стать автора за контекстом речення (закінчення дієслів/прикметників)."""
    if len(text.strip()) < 5:
        return 'Невідомо'

    prompt = (
        f"Проаналізуй текст і визнач стать автора (по закінченням дієслів, прикметників, наприклад: 'я ходив' -> Хлопець, 'я ходила' -> Дівчина). "
        f"Текст: '{text}'. "
        "Відповідай ТІЛЬКИ одним словом: Хлопець, Дівчина або Невідомо."
    )
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        if result in ['Хлопець', 'Дівчина']:
            return result
    except Exception as e:
        print(f"Помилка визначення статі через Gemini (text): {e}")
        
    return 'Невідомо'


def get_user_gender(user_id: int) -> str:
    """Отримує вже збережену стать користувача з бази даних."""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender FROM stats WHERE user_id = %s", (user_id,))
                result = cursor.fetchone()
            conn.close()
        return result[0] if result and result[0] else 'Невідомо'
    except Exception as e:
        print(f"Помилка get_user_gender: {e}")
        return 'Невідомо'


def ensure_user_in_db(user) -> str:
    """Перевіряє наявність юзера в БД, а якщо його немає — реєструє та визначає стать."""
    user_id = user.id
    name = user.first_name or "Без імені"
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender FROM stats WHERE user_id = %s", (user_id,))
                row = cursor.fetchone()
                
                if row is None:
                    # Вперше бачимо юзера -> аналізуємо стать і зберігаємо
                    gender = analyze_gender_from_user(user)
                    cursor.execute(
                        "INSERT INTO stats (user_id, name, count, gender, balance) VALUES (%s, %s, 0, %s, 0)",
                        (user_id, name, gender)
                    )
                    conn.commit()
                    conn.close()
                    return gender
                else:
                    conn.close()
                    return row[0] or 'Невідомо'
    except Exception as e:
        print(f"Помилка ensure_user_in_db: {e}")
        return 'Невідомо'

# ===================================================================
# 🖼️ КОМАНДА /generate
# ===================================================================
@bot.message_handler(commands=['generate'])
def generate_image_wait_and_send(message):
    if is_user_banned(message.from_user.id):
        return

    prompt = message.text[10:].strip()
    if not prompt:
        bot.reply_to(message, "⚠️ Напиши опис картини! Наприклад: /generate cyberpunk warrior wolf")
        return
    status_msg = bot.reply_to(message, "⏳ Драго починає малювати... Зачекай до 2 хвилин.")
    try:
        english_prompt = translate_prompt_to_english(prompt)
        image_url = build_pollinations_url(english_prompt, width=1024, height=1024)
        response = requests.get(image_url, timeout=120)
        if response.status_code == 200:
            if "application/json" in response.headers.get("Content-Type", "") or len(response.content) < 10000:
                raise Exception("Сервер ШІ перевантажений або повернув помилку ліміту.")
            try:
                bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    text="⚡ Картинку згенеровано! Обробляю для Telegram..."
                )
            except Exception:
                pass
            img = Image.open(io.BytesIO(response.content))
            img = img.convert("RGB")
            bio = io.BytesIO()
            bio.name = 'drago_art.jpg'
            img.save(bio, 'JPEG', progressive=False, quality=95)
            bio.seek(0)
            bot.send_photo(
                chat_id=message.chat.id,
                photo=bio,
                caption=f"🔥 Твоя картинка готова!\n\n📋 <b>Запит:</b> {prompt}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            try:
                bot.delete_message(message.chat.id, status_msg.message_id)
            except Exception:
                pass
        else:
            raise Exception(f"Сервер повернув код: {response.status_code}")
    except Exception as e:
        print(f"Помилка генерації: {e}")
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=f"❌ Не зміг отримати картинку.\nПомилка: <code>{str(e)[:100]}</code>\n\nСпробуй пізніше, бро.",
                parse_mode="HTML"
            )
        except Exception:
            bot.reply_to(message, "❌ Сервер малювання тимчасово ліг. Спробуй пізніше.")


# ===================================================================
# 📣 КОМАНДА ЗАГАЛЬНОГО ЗБОРУ (@all / ЗБІР) — В СТИЛІ БОТА-ЗАЗИВАЛИ
# Формат як у "Зазивала tag bot": одна й та сама фраза повторюється
# окремими повідомленнями, а замість імен — рядок емодзі-тегів
# (кожен емодзі — це прихований <a href="tg://user?id=..."> на юзера).
# ===================================================================

DEFAULT_CALL_PHRASE = "макс вільний дівки нападайте"

# Пул емодзі для анонімних тегів (замість імені показується емодзі)
TAG_EMOJIS = [
    "😀", "😎", "🤠", "🥷", "👮‍♂️", "👮‍♀️", "🕵️‍♂️", "🕵️‍♀️", "👨‍🌾", "👩‍🌾",
    "👨‍🍳", "👩‍🍳", "🧔", "🧔‍♀️", "👳‍♂️", "👳‍♀️", "👲", "🧕", "🤵", "👰",
    "🎅", "🤶", "🦸‍♂️", "🦸‍♀️", "🦹‍♂️", "🦹‍♀️", "🧙‍♂️", "🧙‍♀️", "🧛‍♂️", "🧛‍♀️",
    "🧟‍♂️", "🧟‍♀️", "🧞‍♂️", "🧞‍♀️", "🥸", "😈", "👹", "👺", "🤡", "💀",
    "👽", "🤖", "🎃", "🐵", "🐶", "🐱", "🦊", "🦁", "🐯", "🐴",
    "🦄", "🐗", "🐺", "🦝", "🐻", "🐼", "🐨", "🐘", "🦏", "🦍",
    "🌸", "🌺", "🌼", "🌷", "🌻", "🍄", "🌵", "🎄", "🌴", "🍀",
]

@bot.message_handler(func=lambda m: m.text and any(m.text.strip().lower().startswith(trig) for trig in ['@all', '.all', '.збір', 'збір', '/all']))
def call_everyone(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id
    chat_type = message.chat.type
    user = message.from_user

    if chat_type not in ['group', 'supergroup']:
        bot.reply_to(message, "Чувак, який збір у приватці? Ти тут один 👁️")
        return

    try:
        # Сповіщення про початок
        status_msg = bot.reply_to(message, "📢 <i>Драго розгортає рупор і збирає банду...</i>", parse_mode="HTML")
        ensure_user_in_db(user)

        original_text = message.text.strip()
        phrase = DEFAULT_CALL_PHRASE

        for trigger in ['@all', '.all', '.збір', 'збір', '/all']:
            if original_text.lower().startswith(trigger):
                custom_phrase = original_text[len(trigger):].strip()
                if custom_phrase:
                    phrase = custom_phrase
                break

        clean_phrase = phrase.replace("<", "&lt;").replace(">", "&gt;")

        # Беремо з БД тільки тих, хто активний у чаті
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM stats WHERE in_chat = TRUE")
                users = cursor.fetchall()
            conn.close()

        if not users:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ База даних порожня, нікого кликати.")
            return

        bot_id = bot.get_me().id
        user_ids = [u_id for (u_id,) in users if u_id != bot_id]

        if not user_ids:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Здається, крім тебе й мене тут нікого немає, бро.")
            return

        # Видаляємо тимчасове повідомлення
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        # Розбиваємо на блоки по 5 і шлемо окремими повідомленнями:
        # текст фрази + рядок емодзі-тегів (кожен емодзі — прихований тег юзера)
        chunk_size = 5
        for i in range(0, len(user_ids), chunk_size):
            chunk = user_ids[i:i + chunk_size]
            emojis = random.choices(TAG_EMOJIS, k=len(chunk))

            tag_line = "".join(
                f'<a href="tg://user?id={uid}">{emoji}</a>'
                for uid, emoji in zip(chunk, emojis)
            )

            text = f"{clean_phrase}\n{tag_line}"
            bot.send_message(chat_id, text, parse_mode="HTML")
            time.sleep(0.5)  # Маленька пауза, щоб Telegram не дав флуд-бан

    except Exception as e:
        print(f"Помилка загального збору: {e}")
        try:
            bot.send_message(chat_id, f"❌ Рупор згорів під час виклику. Деталі: <code>{str(e)[:50]}</code>", parse_mode="HTML")
        except Exception:
            pass

# ===================================================================
# 👑 СУПЕР-АДМІН ПАНЕЛЬ V2.0 (ПЕРЕВЕДЕНО НА CLOUD POSTGRES)
# ===================================================================

ADMIN_ID = 5512316636

def init_admin_db():
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)")
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка ініціалізації таблиці банів: {e}")

init_admin_db()

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)

# --- ГЕНЕРАТОРИ МЕНЮ ---
def get_main_admin_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        types.InlineKeyboardButton("✉️ Розсилка та ПП", callback_data="admin_menu_mail")
    )
    markup.add(types.InlineKeyboardButton("👥 Користувачі та Модерація", callback_data="admin_menu_users"))
    markup.add(types.InlineKeyboardButton("💾 Управління БД", callback_data="admin_menu_db"))
    return markup

def get_mail_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📢 Загальна розсилка", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("💬 Написати в ПП", callback_data="admin_dm")
    )
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin_main"))
    return markup

def get_users_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔎 Інфо про юзера", callback_data="admin_user_info"),
        types.InlineKeyboardButton("🚫 Забанити", callback_data="admin_ban")
    )
    markup.add(types.InlineKeyboardButton("✅ Розбанити", callback_data="admin_unban"))
    markup.add(types.InlineKeyboardButton("🗑 Видалити з бази (по ID/ніку)", callback_data="admin_delete_user"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin_main"))
    return markup

def get_db_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("💾 Інфо про бекап", callback_data="admin_backup"),
        types.InlineKeyboardButton("🧹 Скинути ТОП", callback_data="admin_reset")
    )
    markup.add(types.InlineKeyboardButton("🔎 Перевірити базу (хто вийшов)", callback_data="admin_cleanup"))
    markup.add(types.InlineKeyboardButton("🛠 Виконати SQL запит", callback_data="admin_sql"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin_main"))
    return markup

@bot.message_handler(commands=['admin', 'адмін'])
def admin_panel(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "🛑 Куди лізеш? Ця панель створена тільки для Артура.")
        
    bot.reply_to(
        message, 
        "👑 <b>Головне меню управління:</b>\nОбери потрібний розділ:", 
        reply_markup=get_main_admin_keyboard(), 
        parse_mode="HTML"
    )

# ===================================================================
# 🧹 ПЕРЕВІРКА БАЗИ: хто реально ще в групі, а кого вже нема
# ===================================================================
def run_db_cleanup(report_chat_id):
    """Проходиться по всіх юзерах з in_chat=TRUE і звіряє з реальним списком групи.
    Тих, кого в групі вже нема (вийшли/вигнані/бот їх не бачить), позначає in_chat=FALSE."""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, name FROM stats WHERE in_chat = TRUE")
                users = cursor.fetchall()
            conn.close()
    except Exception as e:
        return bot.send_message(report_chat_id, f"❌ Не вдалося прочитати базу: {e}")

    if not users:
        return bot.send_message(report_chat_id, "📭 У базі немає юзерів зі статусом in_chat=TRUE для перевірки.")

    status_msg = bot.send_message(
        report_chat_id,
        f"⏳ Перевіряю {len(users)} юзерів на членство в групі, це може зайняти хвилину-дві..."
    )

    def worker():
        left_ids = []
        checked = 0

        for uid, name in users:
            checked += 1
            try:
                member = bot.get_chat_member(TARGET_GROUP_ID, uid)
                if member.status in ['left', 'kicked']:
                    left_ids.append(uid)
            except Exception:
                # Telegram не знайшов юзера в цій групі (вийшов, кікнутий, або бот його вже не бачить)
                left_ids.append(uid)

            time.sleep(0.06)  # щоб не впертись у ліміти Telegram API

            if checked % 50 == 0:
                try:
                    bot.edit_message_text(
                        f"⏳ Перевірено {checked}/{len(users)}...",
                        report_chat_id, status_msg.message_id
                    )
                except Exception:
                    pass

        if left_ids:
            try:
                with db_lock:
                    conn = get_db_connection()
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE stats SET in_chat = FALSE WHERE user_id = ANY(%s)", (left_ids,))
                    conn.commit()
                    conn.close()
            except Exception as e:
                return bot.send_message(report_chat_id, f"❌ Юзерів перевірено, але не вдалось оновити базу: {e}")

        report = (
            f"✅ <b>Перевірку завершено!</b>\n\n"
            f"👤 Перевірено: <code>{checked}</code>\n"
            f"🚪 Вийшли з чату (тепер in_chat=FALSE): <code>{len(left_ids)}</code>\n"
            f"✅ Досі в чаті: <code>{checked - len(left_ids)}</code>"
        )
        try:
            bot.edit_message_text(report, report_chat_id, status_msg.message_id, parse_mode="HTML")
        except Exception:
            bot.send_message(report_chat_id, report, parse_mode="HTML")

    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(commands=['check_users', 'cleanup', 'чистка'])
def cmd_cleanup_users(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "🛑 Ця команда тільки для адміна.")
    run_db_cleanup(message.chat.id)

def auto_cleanup_scheduler():
    """Фонова задача: раз на кілька годин сама перевіряє базу і прибирає з топів
    тих, хто вже вийшов з чату (не треба щоразу натискати кнопку вручну).
    Звіт летить адміну в особисті, щоб не спамити групу."""
    while True:
        time.sleep(6 * 60 * 60)  # раз на 6 годин
        try:
            run_db_cleanup(ADMIN_ID)
        except Exception as e:
            print(f"⚠️ Помилка авто-чистки бази: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def handle_admin_callbacks(call):
    if not is_admin(call.from_user.id):
        return bot.answer_callback_query(call.id, "🛑 Доступ заборонено!", show_alert=True)

    action = call.data

    if action == "admin_main":
        bot.edit_message_text("👑 <b>Головне меню управління:</b>", call.message.chat.id, call.message.message_id, reply_markup=get_main_admin_keyboard(), parse_mode="HTML")
    elif action == "admin_menu_mail":
        bot.edit_message_text("✉️ <b>Розсилки та повідомлення:</b>", call.message.chat.id, call.message.message_id, reply_markup=get_mail_keyboard(), parse_mode="HTML")
    elif action == "admin_menu_users":
        bot.edit_message_text("👥 <b>Управління користувачами:</b>", call.message.chat.id, call.message.message_id, reply_markup=get_users_keyboard(), parse_mode="HTML")
    elif action == "admin_menu_db":
        bot.edit_message_text("💾 <b>Управління базою даних:</b>", call.message.chat.id, call.message.message_id, reply_markup=get_db_keyboard(), parse_mode="HTML")

    elif action == "admin_stats":
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*), SUM(count) FROM stats")
                    result = cursor.fetchone()
                    users_count = result[0] or 0
                    total_msgs = result[1] or 0
                    
                    cursor.execute("SELECT COUNT(*) FROM banned_users")
                    banned_count = cursor.fetchone()[0] or 0
                conn.close()

            text = (
                "📊 *Розширена статистика (Neon Cloud):*\n\n"
                f"👤 Юзерів у базі: `{users_count}`\n"
                f"💬 Всього повідомлень: `{total_msgs}`\n"
                f"🚫 Забанених юзерів: `{banned_count}`\n"
                f"💾 Тип БД: `Хмарна Neon PostgreSQL`"
            )
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Помилка БД: {e}")

    elif action == "admin_broadcast":
        msg = bot.send_message(call.message.chat.id, "📢 Введи текст для розсилки (або `відміна`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_broadcast)
    elif action == "admin_dm":
        msg = bot.send_message(call.message.chat.id, "💬 Надішли `ID текст_повідомлення` (або `відміна`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_dm)

    elif action == "admin_user_info":
        msg = bot.send_message(call.message.chat.id, "🔎 Надішли ID юзера для перевірки (або `відміна`):")
        bot.register_next_step_handler(msg, process_user_info)
    elif action == "admin_ban":
        msg = bot.send_message(call.message.chat.id, "🚫 Надішли ID юзера для БАНУ (або `відміна`):")
        bot.register_next_step_handler(msg, process_ban_user)
    elif action == "admin_unban":
        msg = bot.send_message(call.message.chat.id, "✅ Надішли ID юзера для РОЗБАНУ (або `відміна`):")
        bot.register_next_step_handler(msg, process_unban_user)

    elif action == "admin_delete_user":
        msg = bot.send_message(
            call.message.chat.id,
            "🗑 Надішли ID або нік (частину імені) юзера, якого треба видалити з бази "
            "(або `відміна`).\nЯкщо збіжиться кілька юзерів — покажу список на вибір."
        )
        bot.register_next_step_handler(msg, process_find_user_to_delete)

    elif action.startswith("admin_confirmdel_"):
        target_id = int(action.replace("admin_confirmdel_", ""))
        deleted_name = delete_user_everywhere(target_id)
        bot.answer_callback_query(call.id, "🗑 Видалено!")
        bot.edit_message_text(
            f"🗑 Юзера <b>{html.escape(deleted_name or str(target_id))}</b> "
            f"(<code>{target_id}</code>) повністю видалено з бази.",
            call.message.chat.id, call.message.message_id, parse_mode="HTML"
        )

    elif action == "admin_canceldel":
        bot.answer_callback_query(call.id, "Скасовано")
        bot.edit_message_text("🛑 Видалення скасовано.", call.message.chat.id, call.message.message_id)

    elif action == "admin_cleanup":
        bot.answer_callback_query(call.id, "🔎 Запускаю перевірку...")
        run_db_cleanup(call.message.chat.id)

    elif action == "admin_backup":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id, 
            "💾 <b>Інформація про базу даних:</b>\n"
            "Твій бот тепер працює на хмарній архітектурі Neon. Локальних файлів `.db` більше немає.\n"
            "Всі бекапи робляться автоматично на самому сервері Neon в режимі Point-in-Time Recovery. "
            "Ти можеш керувати знімками через їхній сайт!"
        )
    elif action == "admin_sql":
        msg = bot.send_message(call.message.chat.id, "🛠 Введи RAW SQL запит (або `відміна`).\n*Обережно, це виконується напряму!*", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_raw_sql)
    elif action == "admin_reset":
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("⚠️ ТАК, ОБНУЛИТИ", callback_data="admin_confirm_reset"),
            types.InlineKeyboardButton("❌ СКАСУВАТИ", callback_data="admin_menu_db")
        )
        bot.edit_message_text("⚠️ Впевнений, що хочеш скинути лічильник повідомлень?", call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif action == "admin_confirm_reset":
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE stats SET count = 0")
                conn.commit()
                conn.close()
            bot.edit_message_text("🧹 Статистику успішно обнулено!", call.message.chat.id, call.message.message_id, reply_markup=get_db_keyboard())
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Помилка скидання: {e}")

# --- ДОПОМІЖНІ ФУНКЦІЇ ОБРОБКИ (NEXT_STEP_HANDLERS) ---
def process_broadcast(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна', 'cancel']: return bot.reply_to(message, "🛑 Скасовано.")
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT DISTINCT user_id FROM stats")
                users = cursor.fetchall()
            conn.close()
    except Exception as e:
        return bot.reply_to(message, f"❌ Не вдалося отримати користувачів: {e}")
    
    def run_broadcast():
        success, failed = 0, 0
        bot.send_message(message.chat.id, "⏳ Розсилка запущена...")
        for user in users:
            if user[0] == bot.get_me().id: continue
            try:
                bot.send_message(user[0], f"📢 <b>Повідомлення від Творця:</b>\n\n{message.text}", parse_mode="HTML")
                success += 1
                time.sleep(0.05)
            except Exception:
                failed += 1
        bot.send_message(message.chat.id, f"✅ <b>Розсилка завершена!</b>\nДоставлено: {success}\nПомилок: {failed}", parse_mode="HTML")
    
    threading.Thread(target=run_broadcast, daemon=True).start()

def process_dm(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна', 'cancel']: return bot.reply_to(message, "🛑 Скасовано.")
    try:
        target_id, text = message.text.split(" ", 1)
        bot.send_message(int(target_id), f"💬 <b>Особисте повідомлення:</b>\n\n{text}", parse_mode="HTML")
        bot.reply_to(message, f"✅ Відправлено юзеру {target_id}!")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {e}")

def process_user_info(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна']: return bot.reply_to(message, "🛑 Скасовано.")
    try:
        uid = int(message.text.strip())
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT count FROM stats WHERE user_id = %s", (uid,))
                res = cursor.fetchone()
                cursor.execute("SELECT 1 FROM banned_users WHERE user_id = %s", (uid,))
                is_banned = bool(cursor.fetchone())
            conn.close()
        
        if res:
            status = "🔴 В БАНІ" if is_banned else "🟢 Активний"
            bot.reply_to(message, f"👤 <b>ID:</b> {uid}\n💬 <b>Повідомлень:</b> {res[0]}\nСтатус: {status}", parse_mode="HTML")
        else:
            bot.reply_to(message, "🤷‍♂️ Юзера немає в базі `stats`.")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка формату ID або бази: {e}")

def process_ban_user(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна']: return bot.reply_to(message, "🛑 Скасовано.")
    try:
        uid = int(message.text.strip())
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Замінено на правильний синтаксис Postgres
                cursor.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
            conn.commit()
            conn.close()
        bot.reply_to(message, f"🚫 Юзера <code>{uid}</code> заблоковано!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {e}")

def process_unban_user(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна']: return bot.reply_to(message, "🛑 Скасовано.")
    try:
        uid = int(message.text.strip())
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM banned_users WHERE user_id = %s", (uid,))
            conn.commit()
            conn.close()
        bot.reply_to(message, f"✅ Юзера <code>{uid}</code> розбанено!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {e}")

def delete_user_everywhere(user_id):
    """Повністю видаляє юзера з усіх таблиць бота (профіль, майно, бізнеси, шлюби, бан, памʼять, герой).
    Повертає ім'я юзера (якщо було) для звіту адміну."""
    name = None
    with db_lock:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM stats WHERE user_id = %s", (user_id,))
                row = cursor.fetchone()
                if row:
                    name = row[0]

                cursor.execute("DELETE FROM stats WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM inventory WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM user_businesses WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM marriages WHERE user1_id = %s OR user2_id = %s", (user_id, user_id))
                cursor.execute("DELETE FROM banned_users WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM user_memory WHERE user_id = %s", (user_id,))
                try:
                    cursor.execute("DELETE FROM heroes WHERE user_id = %s", (user_id,))
                except Exception:
                    pass
                try:
                    cursor.execute("DELETE FROM bot_moderators WHERE user_id = %s", (user_id,))
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()
    return name

def process_find_user_to_delete(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна', 'cancel']: return bot.reply_to(message, "🛑 Скасовано.")

    query = message.text.strip()
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                if query.isdigit():
                    cursor.execute("SELECT user_id, name FROM stats WHERE user_id = %s", (int(query),))
                else:
                    cursor.execute("SELECT user_id, name FROM stats WHERE name ILIKE %s LIMIT 15", (f"%{query}%",))
                rows = cursor.fetchall()
            conn.close()
    except Exception as e:
        return bot.reply_to(message, f"❌ Помилка пошуку: {e}")

    if not rows:
        return bot.reply_to(message, "🤷‍♂️ Нікого не знайдено за цим ID/ніком.")

    if len(rows) == 1:
        uid, name = rows[0]
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🗑 ТАК, ВИДАЛИТИ", callback_data=f"admin_confirmdel_{uid}"),
            types.InlineKeyboardButton("❌ Скасувати", callback_data="admin_canceldel")
        )
        bot.reply_to(
            message,
            f"Знайдено: <b>{html.escape(name or 'Без імені')}</b> (<code>{uid}</code>).\nВидалити повністю з бази?",
            parse_mode="HTML", reply_markup=markup
        )
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for uid, name in rows:
        markup.add(types.InlineKeyboardButton(
            f"🗑 {name or 'Без імені'} ({uid})", callback_data=f"admin_confirmdel_{uid}"
        ))
    markup.add(types.InlineKeyboardButton("❌ Скасувати", callback_data="admin_canceldel"))
    bot.reply_to(message, f"Знайдено {len(rows)} збігів. Обери, кого видалити:", reply_markup=markup)

def process_raw_sql(message):
    if not is_admin(message.from_user.id): return
    if message.text.lower() in ['скасування', 'відміна']: return bot.reply_to(message, "🛑 Скасовано.")
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(message.text)
                if message.text.upper().strip().startswith("SELECT"):
                    res = cursor.fetchall()
                    res_text = str(res)[:4000] if res else "Порожній результат"
                    bot.reply_to(message, f"✅ Виконано:\n```\n{res_text}\n```", parse_mode="Markdown")
                else:
                    conn.commit()
                    bot.reply_to(message, f"✅ Запит успішно виконано (змінено рядків: {cursor.rowcount}).")
            conn.close()
    except psycopg2.Error as e:
        bot.reply_to(message, f"❌ Помилка PostgreSQL: {e}")


# ===================================================================
# 🎵 ОПТИМІЗОВАНИЙ НАДШВИДКИЙ ПОШУК (SOUNDCLOUD)
# ===================================================================
@bot.message_handler(commands=['song', 'music', 'музика', 'найти'])
def search_and_send_music(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id
    query = message.text[len(message.text.split()[0]):].strip()
    
    if not query:
        bot.reply_to(message, "⚠️ Ей, а назву треку чи автора хто писати буде? Наприклад: `/найти Скрябін`", parse_mode="Markdown")
        return
        
    status_msg = bot.reply_to(message, f"🔍 Драго шукає трек: <i>{query}</i>...", parse_mode="HTML")
    
    import yt_dlp
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'source_address': '0.0.0.0', 
        'check_formats': False,      
        'socket_timeout': 10,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128', 
        }],
    }
    
    try:
        bot.send_chat_action(chat_id, 'upload_voice')
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch1:{query}", download=True)
            
            if not info or 'entries' not in info or len(info['entries']) == 0:
                raise Exception("Нічого не знайдено. Перевір назву!")
                
            video_info = info['entries'][0]
            if not video_info:
                raise Exception("Не вдалося зчитати дані треку.")
                
            title = video_info.get('title', 'Unknown Track')
            duration = video_info.get('duration', 0)
            performer = video_info.get('uploader', 'Драго Музика')
            
            filename = ydl.prepare_filename(video_info)
            mp3_filename = os.path.splitext(filename)[0] + '.mp3'
            
            if os.path.exists(mp3_filename):
                try:
                    with open(mp3_filename, 'rb') as audio:
                        bot.send_audio(
                            chat_id=chat_id,
                            audio=audio,
                            title=title,
                            performer=performer,
                            duration=duration,
                            reply_to_message_id=message.message_id,
                            caption="🔥 Тримай свій трек від Драго!"
                        )
                finally:
                    if os.path.exists(mp3_filename):
                        try:
                            os.remove(mp3_filename)
                        except Exception:
                            pass
            else:
                found_file = None
                for file in os.listdir('downloads'):
                    if file.endswith('.mp3'):
                        found_file = os.path.join('downloads', file)
                        break
                
                if found_file and os.path.exists(found_file):
                    try:
                        with open(found_file, 'rb') as audio:
                            bot.send_audio(
                                chat_id=chat_id,
                                audio=audio,
                                title=title,
                                performer=performer,
                                duration=duration,
                                reply_to_message_id=message.message_id,
                                caption="🔥 Тримай свій трек від Драго!"
                            )
                    finally:
                        if os.path.exists(found_file):
                            try:
                                os.remove(found_file)
                            except Exception:
                                pass
                else:
                    raise Exception("Файл MP3 не знайдено на сервері.")
                
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
                
    except Exception as e:
        print(f"Помилка пошуку музики: {e}")
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text="❌ <b>Не зміг знайти або завантажити трек.</b>\nСпробуй написати трохи інакше або перевір назву!",
                parse_mode="HTML"
            )
        except Exception:
            pass

# ===================================================================
# 📊 ДІАГНОСТИЧНИЙ І ГНУЧКИЙ ТОП АКТИВНОСТІ
# ===================================================================

# Кількість користувачів на 1 сторінці
PAGE_SIZE = 10


def format_last_seen(dt_value):
    """Форматує дату останньої активності"""
    if not dt_value:
        return None

    if isinstance(dt_value, str):
        try:
            dt_value = datetime.strptime(dt_value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return dt_value

    try:
        now = datetime.now()
        today = now.date()
        yesterday = today - timedelta(days=1)
        msg_date = dt_value.date()

        if msg_date == today:
            return f"сьогодні о {dt_value.strftime('%H:%M')}"
        elif msg_date == yesterday:
            return f"учора о {dt_value.strftime('%H:%M')}"
        else:
            return dt_value.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt_value)


def fetch_top_rows():
    """Безпечне отримання списку всіх активних користувачів із БД (3 рівні захисту)"""
    rows = []
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT name, count, last_seen 
                FROM stats 
                WHERE in_chat = TRUE 
                ORDER BY count DESC
            """)
            rows = cursor.fetchall()
        except Exception:
            if hasattr(conn, "rollback"):
                conn.rollback()

            try:
                cursor.execute("""
                    SELECT name, count 
                    FROM stats 
                    WHERE in_chat = TRUE 
                    ORDER BY count DESC
                """)
                raw_rows = cursor.fetchall()
                rows = [(r[0], r[1], None) for r in raw_rows]
            except Exception:
                if hasattr(conn, "rollback"):
                    conn.rollback()

                cursor.execute(
                    "SELECT name, count FROM stats WHERE in_chat = TRUE ORDER BY count DESC"
                )
                raw_rows = cursor.fetchall()
                rows = [(r[0], r[1], None) for r in raw_rows]

        cursor.close()
        conn.close()

    return rows


def build_top_view(rows, chat_title, page=1):
    """Формує текст сторінки та Inline-кнопки"""
    total_messages = sum(r[1] for r in rows if r[1] is not None)
    total_items = len(rows)

    if total_items == 0 or total_messages == 0:
        return "🕸 <b>У чаті ще немає зафіксованої активності!</b>", None

    total_pages = math.ceil(total_items / PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_rows = rows[start_idx:end_idx]

    chat_title_clean = (chat_title or "цьому чаті").replace("<", "&lt;").replace(">", "&gt;")

    # Найбільша кількість повідомлень серед усіх — база для шкали активності
    max_count = max((r[1] or 0) for r in rows) or 1

    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    bar_len = 8

    lines = []
    for idx, row in enumerate(page_rows):
        global_rank = start_idx + idx
        name = row[0]
        count = row[1] or 0
        last_seen = row[2] if len(row) > 2 else None

        rank_icon = medals.get(global_rank, f"<b>{global_rank + 1}.</b>")
        clean_name = (
            str(name).replace("<", "&lt;").replace(">", "&gt;")
            if name
            else "Анонім"
        )
        percent = (count / total_messages) * 100 if total_messages > 0 else 0

        filled = max(0, min(bar_len, round((count / max_count) * bar_len)))
        bar = "▓" * filled + "░" * (bar_len - filled)

        line = f"{rank_icon} <b>{clean_name}</b>\n<code>{bar}</code>  {count:,} пов. · <i>{percent:.1f}%</i>"

        last_time_str = format_last_seen(last_seen)
        if last_time_str:
            line += f"  ·  🕒 {last_time_str}"

        lines.append(line)

    header = f"📊 <b>Топ активності — {chat_title_clean}</b>\n━━━━━━━━━━━━━━━━━━━"
    footer = f"━━━━━━━━━━━━━━━━━━━\n💬 Всього повідомлень у чаті: <b>{total_messages:,}</b>"

    text = header + "\n\n" + "\n\n".join(lines) + "\n\n" + footer

    # Клавіатура
    markup = InlineKeyboardMarkup()
    buttons = []

    if page > 1:
        buttons.append(
            InlineKeyboardButton("⬅️ Назад", callback_data=f"top_page:{page - 1}")
        )

    buttons.append(
        InlineKeyboardButton(
            f"📄 {page}/{total_pages}", callback_data="top_noop"
        )
    )

    if page < total_pages:
        buttons.append(
            InlineKeyboardButton(
                "Вперед ➡️", callback_data=f"top_page:{page + 1}"
            )
        )

    markup.row(*buttons)
    return text, markup


# ===================================================================
# 📊 ОСНОВНА КОМАНДА /топ
# ===================================================================


@bot.message_handler(commands=["top", "stats", "топ"])
def show_chat_activity(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id

    try:
        bot.send_chat_action(chat_id, "typing")
        rows = fetch_top_rows()

        text, markup = build_top_view(
            rows, message.chat.title, page=1
        )
        bot.reply_to(message, text, parse_mode="HTML", reply_markup=markup)

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ <b>Помилка виконання:</b>\n<code>{e}</code>",
            parse_mode="HTML",
        )


# ===================================================================
# 🔄 ОБРОБНИК КНОПОК «Вперед» / «Назад»
# ===================================================================


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("top_page:")
    or call.data == "top_noop"
)
def handle_top_pagination(call):
    if call.data == "top_noop":
        bot.answer_callback_query(call.id)
        return

    try:
        page = int(call.data.split(":")[1])
        rows = fetch_top_rows()

        chat_title = (
            call.message.chat.title
            if call.message.chat
            else "цьому чаті"
        )
        text, markup = build_top_view(rows, chat_title, page=page)

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)

    except Exception as e:
        bot.answer_callback_query(
            call.id, "❌ Помилка переключення сторінки"
        )


# ===================================================================
# ⏱️ ДОПОМІЖНА ФУНКЦІЯ: Форматування часу
# ===================================================================
def format_time_ago(last_seen):
    if not last_seen:
        return "давним-давно (немає даних)"

    now = datetime.now(timezone.utc) if last_seen.tzinfo else datetime.now()
    diff = now - last_seen
    seconds = int(diff.total_seconds())

    if seconds < 0:
        return "щойно"

    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    months = days // 30
    years = days // 365

    if seconds < 60:
        return "менше хвилини тому"
    elif minutes < 60:
        return f"{minutes} хв. тому"
    elif hours < 24:
        return f"{hours} год. тому"
    elif days < 30:
        return f"{days} дн. тому"
    elif months < 12:
        return f"{months} міс. тому"
    else:
        return f"{years} р. тому"


# ===================================================================
# 🛠️ ГЕНЕРАТОР ТЕКСТУ ТА КНОПОК СТОРОНКИ
# ===================================================================
PAGE_SIZE = 10

def build_sleepers_page(rows, page=0):
    total_users = len(rows)
    total_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE if total_users > 0 else 1

    page = max(0, min(page, total_pages - 1))
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_rows = rows[start_idx:end_idx]

    mentions = []
    for idx, (user_id, name, count, last_seen) in enumerate(page_rows, start=start_idx + 1):
        clean_name = html.escape(str(name)) if name else "Чуваче"
        time_ago = format_time_ago(last_seen)
        mentions.append(
            f"{idx}. <a href=\"tg://user?id={user_id}\">{clean_name}</a> "
            f"— ⏳ <b>{time_ago}</b> <i>({count} пов.)</i>"
        )

    response_text = (
        f"📢 <b>ДРАГО ВИХОДИТЬ НА ПОЛЮВАННЯ НА СОННИХ МУХ!</b> 💤\n"
        f"<i>Ей, ви там що, позасинали у своїх норах? Ану живо в чат! 🪵</i>\n\n"
        f"⚠️ <b>Список тихушників (Стор. {page + 1}/{total_pages}):</b>\n\n"
        + "\n".join(mentions) +
        "\n\n☠️ <i>Якщо не почнете писати — Драго особисто вас забанить.</i>"
    )

    markup = InlineKeyboardMarkup()
    buttons = []

    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"sleepers_page:{page - 1}"))
    
    buttons.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))

    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"sleepers_page:{page + 1}"))

    if buttons:
        markup.row(*buttons)

    return response_text, markup


# ===================================================================
# 💤 КОМАНДА ДЛЯ ПОШУКУ НЕАКТИВНИХ (/sleepers або /сонні)
# ===================================================================
@bot.message_handler(commands=['sleepers', 'сонні'])
def tag_inactive_users(message):
    chat_id = message.chat.id
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "Ей, бро, які сонні мухи в приватці? Тут тільки ти і я. 👁️")
        return

    try:
        bot.send_chat_action(chat_id, 'typing')
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Шукаємо ТІЛЬКИ тих, у кого count < 50 і хто мовчить > 1 години
                cursor.execute("""
                    SELECT user_id, name, count, last_seen 
                    FROM stats 
                    WHERE in_chat = TRUE 
                      AND count < 50
                      AND (last_seen IS NULL OR last_seen < NOW() - INTERVAL '1 hour')
                    ORDER BY last_seen ASC NULLS LAST 
                    LIMIT 30
                """)
                rows = cursor.fetchall()
            conn.close()

        bot_id = bot.get_me().id
        rows = [row for row in rows if row[0] != bot_id]

        if not rows:
            bot.reply_to(message, "🔥 Ого! Схоже, у цьому чаті всі активні звірі! Жодного сонного лінивця не знайдено. 😎")
            return

        text, markup = build_sleepers_page(rows, page=0)
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

    except Exception as e:
        print(f"Помилка пошуку сонних: {e}")
        bot.reply_to(message, f"❌ <b>Помилка БД або коду:</b> <code>{html.escape(str(e))}</code>", parse_mode="HTML")


# ===================================================================
# 🔘 ОБРОБНИК КНОПОК ПАГІНАЦІЇ (/sleepers)
# ===================================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("sleepers_page:"))
def handle_sleepers_page(call):
    try:
        page = int(call.data.split(":")[1])
        chat_id = call.message.chat.id

        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT user_id, name, count, last_seen 
                    FROM stats 
                    WHERE in_chat = TRUE 
                      AND count < 50
                      AND (last_seen IS NULL OR last_seen < NOW() - INTERVAL '1 hour')
                    ORDER BY last_seen ASC NULLS LAST 
                    LIMIT 30
                """)
                rows = cursor.fetchall()
            conn.close()

        bot_id = bot.get_me().id
        rows = [row for row in rows if row[0] != bot_id]

        text, markup = build_sleepers_page(rows, page=page)
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
        bot.answer_callback_query(call.id)

    except Exception as e:
        print(f"Помилка перемикання сторінок: {e}")
        bot.answer_callback_query(call.id, "❌ Не вдалося оновити сторінку")

@bot.callback_query_handler(func=lambda call: call.data == "noop")
def handle_noop(call):
    bot.answer_callback_query(call.id)


# ===================================================================
# 🍔 МЕНЮ ДОБРОГО ДРУГА (/menu або /меню)
# ===================================================================
@bot.message_handler(commands=['menu', 'меню'])
def show_friend_menu(message):
    chat_id = message.chat.id
    try:
        bot.send_chat_action(chat_id, 'typing')
        menu_text = (
            "🍔 <b>МЕНЮ ДОБРОГО ДРУГА</b> 🍕\n\n"
            "Зголоднів, бро? Чи просто хочеться закинути в себе щось нереально соковите? "
            "Твій вірний кент Драго вже про все подбав! 😎\n\n"
            "Тримай посилання на наше гаряче меню. Переходь, вибирай найкращі смаколики "
            "та влаштуй своїм смаковим рецепторам справжнє свято! 🚀\n\n"
            "<i>Смачного, тигр! 🐯👇</i>"
        )
        markup = types.InlineKeyboardMarkup()
        btn_menu = types.InlineKeyboardButton(
            text="📖 Відкрити Меню 🍽️", 
            url="https://expz.menu/64562137-fa19-4413-9b90-d2dba1c697fa"
        )
        markup.add(btn_menu)
        bot.reply_to(message, menu_text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        print(f"Помилка при виклику меню: {e}")
        bot.reply_to(message, "❌ Щось меню не відкриватися. Спробуй за секунду!")


# ===================================================================
# 🔫 ГРА "МАФІЯ" (Братва проти СБУ)
# ===================================================================

# Допоміжні функції для перевірки статусу гри
def check_win(chat_id):
    game = mafia_games[chat_id]
    alive = game['alive']
    roles = game['roles']
    
    mafia_alive = sum(1 for pid in alive if roles[pid] == 'Братва (Мафія)')
    mirni_alive = len(alive) - mafia_alive
    
    if mafia_alive == 0:
        return 'mirni'
    elif mafia_alive >= mirni_alive:
        return 'mafia'
    return None

def start_night(chat_id):
    game = mafia_games[chat_id]
    game['state'] = 'night'
    game['night_actions'] = {'mafia': None, 'sbu': None}
    
    bot.send_message(
        chat_id, 
        "🌃 <b>МІСТО ЗАСИНАЄ...</b>\n\nУсі розійшлися по норах. Братва виходить на полювання, СБУ шукає сліди.\n\n<i>(Мафія та СБУ роблять вибір у ПП з ботом)</i>", 
        parse_mode="HTML"
    )
    
    # Відправляємо кнопки живим активам у ПП
    for pid in game['alive']:
        role = game['roles'][pid]
        if role in ['Братва (Мафія)', 'Агент СБУ (Комісар)']:
            markup = types.InlineKeyboardMarkup()
            for target_id in game['alive']:
                if target_id != pid: # Не можна вибрати себе
                    target_name = game['players'][target_id]
                    cb_data = f"night_{chat_id}_{target_id}"
                    markup.add(types.InlineKeyboardButton(target_name, callback_data=cb_data))
            
            try:
                if role == 'Братва (Мафія)':
                    bot.send_message(pid, "🔫 <b>Вибери, кого братва завалить цієї ночі:</b>", reply_markup=markup, parse_mode="HTML")
                elif role == 'Агент СБУ (Комісар)':
                    bot.send_message(pid, "🕵️‍♂️ <b>Вибери, кого пробити по базі СБУ:</b>", reply_markup=markup, parse_mode="HTML")
            except Exception:
                pass # Якщо користувач не запустив бота в ПП

def process_night(chat_id):
    game = mafia_games[chat_id]
    
    # Вбиваємо жертву мафії
    killed_id = game['night_actions']['mafia']
    if killed_id and killed_id in game['alive']:
        game['alive'].remove(killed_id)
        killed_name = game['players'][killed_id]
        killed_msg = f"💀 Вночі братва розстріляла <b>{killed_name}</b>!"
    else:
        killed_msg = "🤷‍♂️ Вночі ніхто не постраждав (Мафія проспала або жертва вже мертва)."

    # Перевіряємо чи хтось виграв
    winner = check_win(chat_id)
    if winner:
        finish_game(chat_id, winner, killed_msg)
        return

    # Якщо гра триває, починаємо день
    game['state'] = 'day'
    game['day_votes'] = {}
    
    markup = types.InlineKeyboardMarkup()
    for pid in game['alive']:
        cb_data = f"dayvote_{chat_id}_{pid}"
        markup.add(types.InlineKeyboardButton(game['players'][pid], callback_data=cb_data))
    
    bot.send_message(
        chat_id, 
        f"☀️ <b>РАНОК НА РАЙОНІ</b>\n\n{killed_msg}\n\nЧас знайти крису! Голосуйте, кого посадити на пляшку (за ґрати):", 
        reply_markup=markup, 
        parse_mode="HTML"
    )

def finish_game(chat_id, winner, extra_msg=""):
    game = mafia_games[chat_id]
    roles_text = "\n".join([f"{name} — {game['roles'][pid]}" for pid, name in game['players'].items()])
    
    if winner == 'mafia':
        result = "🏴 <b>БРАТВА ПЕРЕМОГЛА!</b> Місто під їхнім контролем."
    else:
        result = "👮‍♂️ <b>СБУ ТА РОБОТЯГИ ПЕРЕМОГЛИ!</b> Мафію знищено."
        
    bot.send_message(
        chat_id, 
        f"{extra_msg}\n\n{result}\n\n<b>Хто був ким:</b>\n{roles_text}", 
        parse_mode="HTML"
    )
    del mafia_games[chat_id]

# ==================== КОМАНДИ ====================

@bot.message_handler(commands=['mafia', 'мафія'])
def start_mafia_lobby(message):
    if is_user_banned(message.from_user.id): return
    
    chat_id = message.chat.id
    if chat_id in mafia_games:
        bot.reply_to(message, "⚠️ Розбірки вже почалися або йде збір! Пиши /join щоб приєднатися.")
        return

    mafia_games[chat_id] = {
        'state': 'lobby',
        'players': {message.from_user.id: message.from_user.first_name},
        'roles': {},
        'alive': [],
        'night_actions': {'mafia': None, 'sbu': None},
        'day_votes': {}
    }
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔫 Приєднатися", callback_data="mafia_join"))
    
    bot.send_message(chat_id, "🚬 <b>ЗБІР НА РОЗБІРКИ (МАФІЯ)</b>\n\nБратва ділить район, СБУ шиє справи. Натискай кнопку!", reply_markup=markup, parse_mode="HTML")

@bot.message_handler(commands=['stop_mafia', 'зупинити_мафію'])
def stop_mafia_game_cmd(message):
    if is_user_banned(message.from_user.id): return
    chat_id = message.chat.id
    if chat_id in mafia_games:
        del mafia_games[chat_id]
        bot.reply_to(message, "🛑 <b>РОЗБІРКИ СКАСОВАНО!</b>\nСБУ накрило точку, братва розбіглася.", parse_mode="HTML")
    else:
        bot.reply_to(message, "🤡 Яку мафію зупиняти? Ніхто навіть не збирався!")

@bot.message_handler(commands=['start_mafia'])
def start_mafia_game(message):
    if is_user_banned(message.from_user.id): return
    
    chat_id = message.chat.id
    if chat_id not in mafia_games or mafia_games[chat_id]['state'] != 'lobby': return

    players = mafia_games[chat_id]['players']
    if len(players) < 4:
        bot.reply_to(message, "🤡 Мало людей! Треба хоча б 4 пацана для нормальної стрілянини.")
        return

    player_ids = list(players.keys())
    random.shuffle(player_ids)

    roles = {}
    roles[player_ids[0]] = 'Братва (Мафія)'
    roles[player_ids[1]] = 'Агент СБУ (Комісар)'
    for pid in player_ids[2:]:
        roles[pid] = 'Роботяга (Мирний)'

    mafia_games[chat_id]['roles'] = roles
    mafia_games[chat_id]['alive'] = player_ids.copy()

    for pid, role in roles.items():
        try:
            bot.send_message(pid, f"🎭 Твоя роль у цій катці: <b>{role}</b>!\nТсс, нікому не кажи.", parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, f"⚠️ Не зміг написати одному з гравців у ПП! Хай напише боту /start.")

    start_night(chat_id)

# ==================== КОЛБЕКИ (КНОПКИ) ====================

@bot.callback_query_handler(func=lambda call: call.data == "mafia_join")
def join_mafia(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if chat_id not in mafia_games or mafia_games[chat_id]['state'] != 'lobby':
        bot.answer_callback_query(call.id, "Гра вже йде або не створена!", show_alert=True)
        return
    if user_id in mafia_games[chat_id]['players']:
        bot.answer_callback_query(call.id, "Ти вже в ділі!", show_alert=True)
        return

    mafia_games[chat_id]['players'][user_id] = call.from_user.first_name
    bot.answer_callback_query(call.id, "Тебе записано!")
    bot.edit_message_text(
        f"🚬 <b>ЗБІР НА РОЗБІРКИ</b>\n\nЗа столом: {len(mafia_games[chat_id]['players'])} чол.\n(Мінімум 4).\n<i>Старт: /start_mafia</i>",
        chat_id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("night_"))
def handle_night_action(call):
    _, chat_id, target_id = call.data.split("_")
    chat_id, target_id = int(chat_id), int(target_id)
    user_id = call.from_user.id
    
    if chat_id not in mafia_games or mafia_games[chat_id]['state'] != 'night':
        bot.answer_callback_query(call.id, "Зараз не ніч або гра закінчилась!", show_alert=True)
        return
        
    game = mafia_games[chat_id]
    if user_id not in game['alive']: return
    
    role = game['roles'][user_id]
    target_name = game['players'][target_id]

    if role == 'Братва (Мафія)':
        game['night_actions']['mafia'] = target_id
        bot.edit_message_text(f"🔫 Ти обрав завалити: <b>{target_name}</b>.", user_id, call.message.message_id, parse_mode="HTML")
    elif role == 'Агент СБУ (Комісар)':
        game['night_actions']['sbu'] = target_id
        target_role = game['roles'][target_id]
        is_mafia = "❗️ Це МАФІЯ!" if target_role == 'Братва (Мафія)' else "✅ Це звичайний роботяга."
        bot.edit_message_text(f"🕵️‍♂️ Ти перевірив <b>{target_name}</b>.\nРезультат: {is_mafia}", user_id, call.message.message_id, parse_mode="HTML")

    # Перевіряємо чи всі активи зробили хід
    mafia_alive = any(game['roles'][p] == 'Братва (Мафія)' for p in game['alive'])
    sbu_alive = any(game['roles'][p] == 'Агент СБУ (Комісар)' for p in game['alive'])
    
    mafia_done = not mafia_alive or game['night_actions']['mafia'] is not None
    sbu_done = not sbu_alive or game['night_actions']['sbu'] is not None

    if mafia_done and sbu_done:
        process_night(chat_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("dayvote_"))
def handle_day_vote(call):
    _, chat_id, target_id = call.data.split("_")
    chat_id, target_id = int(chat_id), int(target_id)
    user_id = call.from_user.id
    
    if chat_id not in mafia_games or mafia_games[chat_id]['state'] != 'day':
        bot.answer_callback_query(call.id, "Голосування не активне!", show_alert=True)
        return
        
    game = mafia_games[chat_id]
    if user_id not in game['alive']:
        bot.answer_callback_query(call.id, "Мертві не голосують!", show_alert=True)
        return
        
    game['day_votes'][user_id] = target_id
    bot.answer_callback_query(call.id, f"Голос прийнято за {game['players'][target_id]}!")
    
    # Якщо всі живі проголосували
    if len(game['day_votes']) == len(game['alive']):
        votes = list(game['day_votes'].values())
        # Знаходимо того, в кого найбільше голосів
        vote_counts = {i: votes.count(i) for i in votes}
        max_votes = max(vote_counts.values())
        lynched = [k for k, v in vote_counts.items() if v == max_votes]
        
        if len(lynched) == 1:
            lynched_id = lynched[0]
            game['alive'].remove(lynched_id)
            lynch_name = game['players'][lynched_id]
            lynch_role = game['roles'][lynched_id]
            msg = f"⚖️ Більшістю голосів за ґрати відправляється <b>{lynch_name}</b>!\nВін був: <i>{lynch_role}</i>"
        else:
            msg = "⚖️ Голоси розділилися! Ніхто не сів."
            
        bot.send_message(chat_id, msg, parse_mode="HTML")
        
        winner = check_win(chat_id)
        if winner:
            finish_game(chat_id, winner)
        else:
            start_night(chat_id)

# ===================================================================
# 📰 МЕМНІ НОВИНИ ВІД ДРАГО НА БАЗІ GEMINI (/news)
# ===================================================================
@bot.message_handler(commands=['news', 'новини'])
def generate_chat_news(message):
    chat_id = message.chat.id
    
    if len(RECENT_MESSAGES) < 5:
        bot.reply_to(
            message, 
            "🤫 *Драго ще не назбирав достатньо компромату!* Поактивнічайте трохи в чаті, і я зроблю вам гарячий випуск новин.",
            parse_mode="Markdown"
        )
        return
        
    status_msg = bot.reply_to(message, "🔥 <i>Драго дістає брудну білизну чату та пише свіжий випуск новин...</i>", parse_mode="HTML")
    bot.send_chat_action(chat_id, 'typing')
    
    formatted_history = "\n".join([f"- {msg['user']}: {msg['text']}" for msg in RECENT_MESSAGES])
    
    prompt = f"""
    Ти — Драго, зухвалий, саркастичний, харизматичний бот-бандит, який веде кримінальний чат.
    Твоє завдання — написати короткий, суперсмішний випуск "жовтої преси" або "мемних новин" на основі останніх повідомлень у чаті.
    Будь іронічним, використовуй молодіжний сленг, трохи підколюй учасників (але без відвертої злоби чи жорстокої токсичності).
    Вигадай абсурдні сенсації, теорії змови або "кримінальні розслідування" на основі того, про що त्यांनी писали.
    
    Ось історія останніх повідомлень у чаті:
    {formatted_history}
    
    Напиши короткий випуск новин (3-4 пункти) українською мовою. Обов'язково використовуй емодії та виділяй імена.
    В кінці додай фірмову бандитську фразу від Драго.
    """
    
    try:
        response = model.generate_content(prompt)
        news_text = response.text
        
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"⚡️ <b>СПЕЦВИПУСК ДРАГО-NEWS</b> ⚡️\n\n{news_text}",
                parse_mode="HTML"
            )
        except Exception:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"⚡️ СПЕЦВИПУСК ДРАГО-NEWS ⚡️\n\n{news_text}"
            )
            
    except Exception as e:
        print(f"Помилка генерації новин через Gemini: {e}")
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text="❌ *Чорт, зв'язок обірвався!* Мої інформатори накрилися мідним тазом (помилка Gemini). Спробуй трохи пізніше!",
            parse_mode="Markdown"
        )


# ===================================================================
# 📸 ОБРОБНИК ЗОБРАЖЕНЬ (Аналіз фото через Gemini)
# ===================================================================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id
    chat_type = message.chat.type
    user = message.from_user
    caption = message.caption or ""
    
    ensure_user_in_db(user)
    gender = get_user_gender(user.id)
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE stats SET count = count + 1, name = %s WHERE user_id = %s",
                    (user.first_name, user.id)
                )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка оновлення статів фото: {e}")

    is_mentioned = False
    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'драго,', 'джарвіс', 'джарвіс,']
        first_word = caption.split()[0].lower() if caption.split() else ""
        if (first_word in trigger_words
                or f"@{bot.get_me().username}" in caption
                or (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id)):
            is_mentioned = True
            for word in trigger_words:
                if caption.lower().startswith(word):
                    caption = caption[len(word):].strip()
                    break
    else:
        is_mentioned = True

    if not is_mentioned:
        return

    # 🐢 Ліміт запитів до ШІ — лише коли бота справді покликали на фото.
    if check_rate_limit(message):
        return

    gender_hint = ""
    if gender == 'Дівчина':
        gender_hint = "[КОНТЕКСТ: Це дівчина. Звертайся до неї відповідно — 'ти', 'подруга', 'красуня' тощо] "
    elif gender == 'Хлопець':
        gender_hint = "[КОНТЕКСТ: Це хлопець. Звертайся відповідно — 'бро', 'чувак' тощо] "

    status_msg = None
    try:
        bot.send_chat_action(chat_id, 'typing')
        status_msg = bot.reply_to(message, "Так-так, Драго протирає очі й дивиться на твою картинку... 👀")

        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image_data = {"data": downloaded_file, "mime_type": "image/jpeg"}

        system_prompt = (
            f"{gender_hint}Тобі надіслали фото. Проаналізуй, що на ньому зображено. "
            f"Якщо користувач залишив підпис до фото, він тут: '{caption}'. "
            "Дай коротку, дотепну, зухвалу або іронічну відповідь у стилі Драго на основі того, що ти бачиш на зображенні!"
        )

        response = model.generate_content([system_prompt, image_data])

        try:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text)

    except Exception as e:
        print(f"Помилка аналізу фото: {e}")
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Ой, у мене лінзи запітніли, не зміг роздивитися це фото.")


# ===================================================================
# 👋 Обробник входу/виходу учасників
# ===================================================================
@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    user = message.new_chat_member.user
    old_status = message.old_chat_member.status
    new_status = message.new_chat_member.status

    # 1. ЮЗЕР ДІЙСНО ЗАЙШОВ АБО ПОВЕРНУВСЯ В ЧАТ (з 'left' або 'kicked')
    if old_status in ['left', 'kicked'] and new_status in ['member', 'administrator', 'restricted'] and not user.is_bot:
        gender = ensure_user_in_db(user)
        name = user.first_name
        
        # Повертаємо його в активні списки
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE stats SET in_chat = TRUE WHERE user_id = %s", (user.id,))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"Помилка БД при поверненні юзера: {e}")
            
        if gender == 'Дівчина':
            greeting = f"""Вітаємо в чаті, <b>{name}</b>! 🤍
Ласкаво просимо до лав «Нової Банди»! 🎩
Правила прості: не флудити, поважати своїх і кидати якісні меми.
Заходь, вливайся та будь як удома! 😎"""

        elif gender == 'Хлопець':
            greeting = f"""Йо, <b>{name}</b>, вітаємо в чаті! 🤝
Сідай до нашого вогнища, скидай зброю.
Вливайся в колектив, спілкуйся та будь як удома! 🍕"""

        else:
            greeting = f"""Вітаємо в нашій групі, <b>{name}</b>! 🤍
Напиши пару слів про себе або кидай улюблений мем, щоб одразу залетіти в тему! ✨"""
            
        bot.send_message(message.chat.id, greeting, parse_mode="HTML")

    # 2. ЮЗЕР ДІЙСНО ВИЙШОВ АБО ЙОГО ВИГНАЛИ
    elif old_status in ['member', 'administrator', 'restricted'] and new_status in ['left', 'kicked']:
        leaving_user = message.old_chat_member.user
        name = leaving_user.first_name
        
        # ВИКРЕСЛЮЄМО З ТОПІВ
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE stats SET in_chat = FALSE WHERE user_id = %s", (leaving_user.id,))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"Помилка БД при виході юзера: {e}")

        goodbyes = [
            f"Ну і пофіг, <b>{name}</b> пішов. Менше народу — більше кисню. 👋",
            f"Аривідерчі, <b>{name}</b>! Не забудь двері зачинити. 🚪",
            f"<b>{name}</b> покинув чат. Схоже, не витримав нашого рівня інтелекту... 🧠",
            f"Мінус один. <b>{name}</b>, удачі в пошуках цікавішої компанії!"
        ]
        bot.send_message(message.chat.id, random.choice(goodbyes), parse_mode="HTML")
# ===================================================================
# 🧠 РОБОТА З ПЕРСОНАЛЬНОЮ ПАМ'ЯТЮ ЮЗЕРІВ В БД
# ===================================================================

def get_user_memory(user_id: int):
    """Отримує збережене ім'я та список фактів про користувача з БД."""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_name, facts FROM user_memory WHERE user_id = %s;", (user_id,))
                row = cursor.fetchone()
            conn.close()
            if row:
                return {"name": row[0], "facts": row[1] if row[1] else []}
    except Exception as e:
        print(f"Помилка зчитування пам'яті для user_id {user_id}: {e}")
    return {"name": None, "facts": []}


def save_user_fact(user_id: int, user_name: str, new_fact: str):
    """Зберігає новий факт про юзера в БД, уникаючи дублікатів."""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                query = """
                INSERT INTO user_memory (user_id, user_name, facts)
                VALUES (%s, %s, ARRAY[%s])
                ON CONFLICT (user_id) DO UPDATE 
                SET user_name = EXCLUDED.user_name,
                    facts = CASE 
                        WHEN NOT (%s = ANY(user_memory.facts)) THEN array_append(user_memory.facts, %s)
                        ELSE user_memory.facts
                    END,
                    updated_at = CURRENT_TIMESTAMP;
                """
                cursor.execute(query, (user_id, user_name, new_fact, new_fact, new_fact))
            conn.commit()
            conn.close()
            print(f"🧠 [Пам'ять] Додано новий факт для {user_id}: {new_fact}")
    except Exception as e:
        print(f"Помилка збереження факту для {user_id}: {e}")


def extract_and_save_facts(user_id: int, user_name: str, text: str):
    """Викликає Gemini в режимі JSON для пошуку нових фактів у повідомленні юзера."""
    if len(text.strip()) < 5:
        return

    extract_prompt = f"""
Проаналізуй повідомлення від користувача ({user_name}): "{text}"
Якщо користувач поділився НОВИМ особистим фактом про себе (наприклад: його захоплення, гра, улюблена їжа, місто, професія, девайси, ім'я):
- Сформулюй цей факт як одне коротке речення у третій особі (наприклад: "Полюбляє грати в Counter-Strike 1.6", "Проживає в Україні", "Розробляє ботів").
- Якщо нових фактів немає — поверни null.

Формат відповіді СТРОГО JSON:
{{"new_fact": "короткий факт українською" або null}}
"""
    try:
        response = model.generate_content(
            extract_prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        data = json.loads(response.text)
        new_fact = data.get("new_fact")
        if new_fact and isinstance(new_fact, str) and len(new_fact) > 3:
            save_user_fact(user_id, user_name, new_fact)
    except Exception as e:
        print(f"Помилка аналізу фактів: {e}")


# ==================== HTTP ВЕБ-СЕРВЕР ДЛЯ MINI APP ====================
# Вбудована in-memory база для Mini App (синхронізується з основною БД)
users_db = {}

# ==================== HTTP ВЕБ-СЕРВЕР ДЛЯ MINI APP ====================
class WebAppHandler(SimpleHTTPRequestHandler):
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        # Render б'є сюди health-check запитом (GET /), тому віддаємо просте OK,
        # а не стандартну поведінку SimpleHTTPRequestHandler (роздача файлів з диску)
        self._set_headers(200)
        self.wfile.write(json.dumps({"status": "ok", "service": "jarvis-bot"}).encode())

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            data = {}

        # 1. Синхронізація гравця при запуску
        if self.path == '/api/user/sync':
            tg_id = data.get('telegramId', 0)
            username = data.get('username', 'анонім')
            first_name = data.get('firstName', 'Гравець')

            # Якщо користувач новий — створюємо його в БД
            if tg_id not in users_db:
                users_db[tg_id] = {
                    "telegramId": tg_id,
                    "username": username,
                    "firstName": first_name,
                    "money": 0,
                    "tapPower": 1,
                    "energy": 1000,
                    "passiveIncome": 0,
                    "totalTaps": 0,
                    "isBanned": False,
                    "cards": [],
                    "collectionItems": []
                }

            user = users_db[tg_id]
            is_admin = (int(tg_id) == ADMIN_ID)

            response = {
                "money": user["money"],
                "tapPower": user["tapPower"],
                "energy": user["energy"],
                "passiveIncome": user["passiveIncome"],
                "totalTaps": user["totalTaps"],
                "banned": user["isBanned"],
                "isAdmin": is_admin,
                "firstName": user["firstName"],
                "cards": user["cards"],
                "collectionItems": user["collectionItems"]
            }

            self._set_headers(200)
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return

        # 2. Збереження прогресу з Mini App
        elif self.path == '/api/user/save':
            tg_id = data.get('telegramId', 0)
            if tg_id in users_db:
                users_db[tg_id]["money"] = data.get("money", users_db[tg_id]["money"])
                users_db[tg_id]["tapPower"] = data.get("tapPower", users_db[tg_id]["tapPower"])
                users_db[tg_id]["energy"] = data.get("energy", users_db[tg_id]["energy"])
                users_db[tg_id]["passiveIncome"] = data.get("passiveIncome", users_db[tg_id]["passiveIncome"])
                users_db[tg_id]["totalTaps"] = data.get("totalTaps", users_db[tg_id]["totalTaps"])
                users_db[tg_id]["cards"] = data.get("cards", [])
                users_db[tg_id]["collectionItems"] = data.get("collectionItems", [])

            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            return

        # 3. Адмін-панель: список всіх гравців
        elif self.path.startswith('/api/admin/users'):
            users_list = list(users_db.values())
            self._set_headers(200)
            self.wfile.write(json.dumps(users_list).encode('utf-8'))
            return

        # 4. Адмін-панель: додати гроші
        elif self.path == '/api/admin/add-money':
            target_id = data.get('targetTelegramId')
            amount = data.get('amount', 100000)
            if target_id in users_db:
                users_db[target_id]["money"] += amount
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            return

        # 5. Адмін-панель: бан/розбан
        elif self.path == '/api/admin/toggle-ban':
            target_id = data.get('targetTelegramId')
            if target_id in users_db:
                users_db[target_id]["isBanned"] = not users_db[target_id]["isBanned"]
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            return

        else:
            # Для статики (GET-запити HTML/CSS/JS)
            super().do_POST()

# ==================== TELEGRAM БОТ ====================

# Команда /start + обробка рефералів
@bot.message_handler(commands=['start'])
def send_welcome(message):
    args = message.text.split()
    referrer_id = args[1] if len(args) > 1 else None

    # Вітальний текст
    welcome_text = (
        f"Привіт, **{message.from_user.first_name}**! 🐉\n\n"
        f"Ласкаво просимо до **Drago Tap Empire**!\n"
        f"Будуй бізнеси, збирай рідкісні артефакти та ставай найбагатшим драконом!\n\n"
        f"Тисни кнопку нижче, щоб розпочати ⬇️"
    )

    # Якщо прийшов за реферальним посиланням
    if referrer_id and referrer_id.isdigit():
        ref_id = int(referrer_id)
        if ref_id in users_db and ref_id != message.from_user.id:
            users_db[ref_id]["money"] += 5000  # Бонус запрошуючому
            try:
                bot.send_message(ref_id, f"🎉 За вашим посиланням приєднався {message.from_user.first_name}! Вам нараховано **+5,000 ₴**!")
            except Exception:
                pass

    # Кнопка для відкриття Mini App
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🐉 Грати в Drago Tap Empire", web_app=WebAppInfo(url=WEB_APP_URL)))

    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")

# Обробка даних, які надсилаються з Mini App через Telegram.WebApp.sendData()
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        bot.send_message(message.chat.id, f"Отримано сповіщення з гри: `{data}`", parse_mode="Markdown")
    except Exception:
        bot.send_message(message.chat.id, "Отримано дані з вашої гри!")

# ===================================================================
# 💬 ГОЛОВНИЙ ОБРОБНИК ТЕКСТОВИХ ПОВІДОМЛЕНЬ
# ===================================================================
@bot.message_handler(func=lambda m: not (m.text and m.text.startswith('/')), content_types=['text'])
def handle_text(message):
    if is_user_banned(message.from_user.id):
        return

    text = message.text
    chat_id = message.chat.id
    user = message.from_user
    chat_type = message.chat.type

    # 🚫 Перевірка на флуд/спам — якщо флуд, далі повідомлення не обробляємо
    if check_flood(message):
        return

    if text:
        user_name = user.first_name or "Анонім"
        RECENT_MESSAGES.append({
            "user": user_name,
            "text": text
        })
        if len(RECENT_MESSAGES) > MAX_HISTORY_LIMIT:
            RECENT_MESSAGES.pop(0)

    ensure_user_in_db(user)
    gender = get_user_gender(user.id)

    # 1. Оновлення статі, якщо вона невідома
    if gender in ['Never', 'Невідомо']:
        guessed = analyze_gender_from_text(text)
        if guessed in ['Хлопець', 'Дівчина']:
            try:
                with db_lock:
                    conn = get_db_connection()
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE stats SET gender = %s WHERE user_id = %s", (guessed, user.id))
                    conn.commit()
                    conn.close()
            except Exception as e:
                print(f"Помилка оновлення статі: {e}")

    # 2. Нарахування грошей, оновлення лічильника повідомлень ТА ЧАСУ АКТИВНОСТІ
    try:
        earned_money = random.randint(5, 15)  # 💰 Генеруємо випадковий заробіток
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE stats 
                    SET count = count + 1, 
                        balance = COALESCE(balance, 0) + %s, 
                        name = %s, 
                        last_seen = NOW() 
                    WHERE user_id = %s
                    """,
                    (earned_money, user.first_name, user.id)
                )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка оновлення лічильника, балансу та last_seen: {e}")

    is_mentioned = False

    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'драго,', 'джарвіс', 'джарвіс,']
        first_word = text.split()[0].lower() if text.split() else ""
        
        bot_username = bot.get_me().username
        
        if (first_word in trigger_words
                or f"@{bot_username}" in text
                or (message.reply_to_message
                    and message.reply_to_message.from_user.id == bot.get_me().id)):
            is_mentioned = True
            for word in trigger_words:
                if text.lower().startswith(word):
                    text = text[len(word):].strip()
                    break
    else:
        is_mentioned = True

    if not is_mentioned:
        return

    # 🐢 Ліміт запитів до ШІ — перевіряємо тільки тут, тобто лише коли бота
    # справді покликали, а не на кожне повідомлення в чаті.
    if check_rate_limit(message):
        return

    # 🧠 ФОНОВИЙ ПОШУК ТА ЗБЕРЕЖЕННЯ ФАКТІВ ПРО ЮЗЕРА
    user_display_name = user.first_name or "Друже"
    threading.Thread(
        target=extract_and_save_facts, 
        args=(user.id, user_display_name, text), 
        daemon=True
    ).start()

    # 🧠 ЗЧИТУЄМО НАПРАЦЬОВАНУ ПАМ'ЯТЬ ЮЗЕРА
    user_mem = get_user_memory(user.id)
    known_facts = user_mem.get("facts", []) if user_mem else []
    facts_context = ""
    if known_facts:
        facts_context = f"\n[ПАМ'ЯТЬ ПРО ЮЗЕРА: {', '.join(known_facts)}]"

    voice_triggers = ['скажи', 'голосове', 'гс', 'озвуч', 'запиши', 'скажии']
    wants_voice = any(trigger in text.lower() for trigger in voice_triggers)

    gender_hint = ""
    if gender == 'Дівчина':
        gender_hint = "[КОНТЕКСТ: Це дівчина. Звертайся до неї відповідно — 'ти', 'подруга' тощо] "
    elif gender == 'Хлопець':
        gender_hint = "[КОНТЕКСТ: Це хлопець. Звертайся відповідно — 'бро', 'чувак' тощо] "

    status_msg = None
    try:
        if wants_voice:
            bot.send_chat_action(chat_id, 'record_voice')
            status_msg = bot.reply_to(message, "Дід Драго прокашлюється і записує голосове... 🎤👴")
            
            # 👴 Використовуємо grandfather_model для генерації старечої відповіді!
            full_prompt = f"[КОРИСТУВАЧ: {user_display_name}]{gender_hint}{facts_context}\nПОВІДОМЛЕННЯ: {text}"
            response = grandfather_model.generate_content(full_prompt)
            reply_text = response.text
        else:
            bot.send_chat_action(chat_id, 'typing')
            status_msg = bot.reply_to(message, "Йде відправка даних в СБУ... 👮‍♂️")
            
            chat = get_gemini_chat(chat_id)
            full_prompt = f"[КОРИСТУВАЧ: {user_display_name}]{gender_hint}{facts_context}\nПОВІДОМЛЕННЯ: {text}"
            response = chat.send_message(full_prompt)
            reply_text = response.text

        if wants_voice:
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
            send_voice_reply(chat_id, reply_text, reply_to_id=message.message_id)
        else:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=reply_text, parse_mode="Markdown")
            except Exception:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=reply_text)
        
    except Exception as e:
        print(f"Помилка Gemini в handle_text: {e}")
        if status_msg:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Бля, щось у мене мізки на секунду заклинило. Спробуй ще раз, бро!")
            except Exception:
                pass

# =====================================================
# ⚔️ HEROES DATABASE
# =====================================================

def init_heroes_db():

    try:

        with db_lock:

            conn = get_db_connection()

            with conn.cursor() as cursor:

                cursor.execute("""
                CREATE TABLE IF NOT EXISTS heroes(

                    user_id BIGINT PRIMARY KEY,

                    hero_name VARCHAR(60) DEFAULT 'Новачок',

                    level INT DEFAULT 1,

                    exp BIGINT DEFAULT 0,

                    hp INT DEFAULT 100,

                    attack INT DEFAULT 15,

                    defense INT DEFAULT 5,

                    coins BIGINT DEFAULT 0,

                    wins INT DEFAULT 0,

                    loses INT DEFAULT 0,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

                );
                """)

            conn.commit()

            conn.close()

    except Exception as e:

        print("Hero DB:",e)


init_heroes_db()

# =====================================================
# ⚔️ СТВОРЕННЯ ГЕРОЯ
# =====================================================

def ensure_hero(user_id):

    with db_lock:

        conn = get_db_connection()

        try:

            with conn.cursor() as cursor:

                cursor.execute(
                    "SELECT user_id FROM heroes WHERE user_id=%s",
                    (user_id,)
                )

                if cursor.fetchone() is None:

                    cursor.execute("""
                        INSERT INTO heroes
                        (
                            user_id,
                            hero_name,
                            level,
                            exp,
                            hp,
                            attack,
                            defense,
                            coins,
                            wins,
                            loses
                        )
                        VALUES
                        (
                            %s,
                            'Новачок',
                            1,
                            0,
                            100,
                            15,
                            5,
                            0,
                            0,
                            0
                        )
                    """, (user_id,))

            conn.commit()

        finally:

            conn.close()


# ===================================================================
# 🐊 ГРА "КРОКОДИЛ" (Telegram-група)
# Ведучий отримує слово через кнопку зі спливаючим вікном — Telegram
# показує його ТІЛЬКИ тому, хто натиснув (навіть без приватного чату
# з ботом), тому ніхто інший у групі його не бачить. Ведучий пояснює
# слово жестами й словами, інші пишуть варіанти прямо в чат.
# ===================================================================
CROCODILE_WORDS = {
    "тварини": {
        "легко": ["кіт", "собака", "риба", "качка", "корова", "кінь", "заєць", "ведмідь", "миша", "коза"],
        "середньо": ["жираф", "кенгуру", "їжак", "черепаха", "мавпа", "пінгвін", "верблюд", "равлик", "видра", "тхір"],
        "важко": ["єхидна", "ігуана", "бабуїн", "капібара", "тукан", "нарвал", "ласка", "вомбат", "гризлі", "ондатра"],
    },
    "їжа": {
        "легко": ["банан", "піца", "яблуко", "хліб", "морозиво", "торт", "яйце", "молоко", "мед", "суп"],
        "середньо": ["суші", "борщ", "вареники", "млинці", "шаурма", "омлет", "печиво", "лимонад", "плов", "хумус"],
        "важко": ["тірамісу", "фондю", "гаспачо", "цукати", "тартар", "равіолі", "паштет", "бланманже", "рататуй", "заливне"],
    },
    "професії": {
        "легко": ["лікар", "вчитель", "кухар", "поліцейський", "пожежник", "водій", "продавець", "фермер", "маляр"],
        "середньо": ["архітектор", "стоматолог", "бухгалтер", "перекладач", "фотограф", "юрист", "пілот", "садівник"],
        "важко": ["нотаріус", "дегустатор", "археолог", "кінолог", "сомельє", "ветеринар", "картограф", "реставратор"],
    },
    "кіно": {
        "легко": ["супергерой", "принцеса", "піратський корабель", "вампір", "робот", "динозавр", "зомбі", "русалка"],
        "середньо": ["єдиноріг", "дракон", "чарівна паличка", "космічний корабель", "привид", "чаклунка", "оборотень"],
        "важко": ["телепортація", "паралельний всесвіт", "машина часу", "штучний інтелект", "лабіринт мінотавра"],
    },
    "предмети": {
        "легко": ["телефон", "гітара", "парасолька", "олівець", "фотоапарат", "ліхтарик", "будильник", "окуляри"],
        "середньо": ["вертоліт", "скейтборд", "холодильник", "світлофор", "мікроскоп", "телескоп", "глобус"],
        "важко": ["стетоскоп", "флюгер", "секундомір", "діапроектор", "гончарний круг", "астролябія", "камертон"],
    },
    "місця": {
        "легко": ["школа", "лікарня", "цирк", "зоопарк", "пляж", "гора", "ліс", "острів", "місто"],
        "середньо": ["маяк", "вулкан", "печера", "оранжерея", "консерваторія", "обсерваторія", "порт", "фортеця"],
        "важко": ["катакомби", "стародавні руїни", "тропічні джунглі", "підводний вулкан", "покинута шахта"],
    },
    "дії_та_спорт": {
        "легко": ["футбол", "плавання", "танець", "стрибок", "біг", "лижі", "бокс", "їзда на велосипеді"],
        "середньо": ["акваланг", "серфінг", "фехтування", "альпінізм", "фігурне катання", "стрільба з лука"],
        "важко": ["бейсджампінг", "спелеологія", "керлінг", "триатлон", "парапланеризм", "кайтсерфінг"],
    },
}
CROCODILE_DIFFICULTIES = ("легко", "середньо", "важко")
CROCODILE_DIFFICULTY_META = {
    "легко":    {"emoji": "🟢", "seconds": 120},
    "середньо": {"emoji": "🟡", "seconds": 150},
    "важко":    {"emoji": "🔴", "seconds": 200},
}

CROCODILE_LOCK = threading.Lock()
CROCODILE_GAMES = {}   # {chat_id: {"word","category","difficulty","host_id","host_name","hints_used","round_no","timer"}}


def _crocodile_score_add(chat_id, user_id, points):
    """Додає очки гравцю в БД (крокодил рейтинг зберігається постійно, не в пам'яті)."""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO crocodile_scores (chat_id, user_id, points) VALUES (%s, %s, %s)
                    ON CONFLICT (chat_id, user_id) DO UPDATE
                        SET points = crocodile_scores.points + EXCLUDED.points;
                """, (chat_id, user_id, points))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Крокодил: помилка _crocodile_score_add: {e}")


def _crocodile_top(chat_id, limit=10):
    """Повертає [(user_id, points), ...] відсортовано за очками, тільки points > 0."""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT user_id, points FROM crocodile_scores
                    WHERE chat_id = %s AND points > 0
                    ORDER BY points DESC
                    LIMIT %s;
                """, (chat_id, limit))
                rows = cursor.fetchall()
            conn.close()
        return [(row[0], row[1]) for row in rows]
    except Exception as e:
        print(f"Крокодил: помилка _crocodile_top: {e}")
        return []


def _crocodile_pick_word(category_key=None, difficulty=None):
    if category_key is None:
        category_key = random.choice(list(CROCODILE_WORDS.keys()))
    if difficulty is None:
        difficulty = random.choice(CROCODILE_DIFFICULTIES)
    word = random.choice(CROCODILE_WORDS[category_key][difficulty])
    return word, category_key, difficulty


def _crocodile_resolve_category(arg):
    """Впізнає категорію навіть за частковим вводом ('спорт' → 'дії_та_спорт')."""
    arg = arg.lower().strip()
    if not arg:
        return None
    for key in CROCODILE_WORDS:
        display = key.replace("_", " ")
        if arg == key or arg == display or display.startswith(arg) or key.startswith(arg):
            return key
        if any(word.startswith(arg) for word in display.split() if word != "та"):
            return key
    return None


def _crocodile_resolve_difficulty(arg):
    arg = arg.lower().strip()
    for d in CROCODILE_DIFFICULTIES:
        if d.startswith(arg):
            return d
    return None


def _crocodile_category_list_str():
    return ", ".join(k.replace("_", " ") for k in CROCODILE_WORDS)


def _crocodile_end_round(chat_id, reason, guesser_name=None):
    with CROCODILE_LOCK:
        game = CROCODILE_GAMES.pop(chat_id, None)
        if game and game.get("timer"):
            game["timer"].cancel()
    if not game:
        return

    meta = CROCODILE_DIFFICULTY_META[game["difficulty"]]
    category_disp = game["category"].replace("_", " ")

    if reason == "guessed":
        text = (
            f"🎉 *{guesser_name}* вгадав(-ла) слово!\n\n"
            f"Слово: *{game['word']}*\n"
            f"Категорія: {category_disp} {meta['emoji']} {game['difficulty']}\n\n"
            f"Напиши /крокодил, щоб почати новий раунд і стати ведучим 🐊"
        )
    elif reason == "timeout":
        text = (
            f"⌛ Час вийшов! Ніхто не встиг вгадати.\n\n"
            f"Слово було: *{game['word']}*\n"
            f"Категорія: {category_disp} {meta['emoji']} {game['difficulty']}\n\n"
            f"Напиши /крокодил, щоб спробувати ще раз 🐊"
        )
    elif reason == "skip":
        text = f"⏭️ Слово пропущено ведучим.\n\nСлово було: *{game['word']}*"
    else:  # stop
        text = f"🛑 Гру зупинено.\n\nСлово було: *{game['word']}*"

    try:
        bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Крокодил: помилка при завершенні раунду: {e}")


def _crocodile_schedule_timeout(chat_id, word, seconds):
    def _job():
        with CROCODILE_LOCK:
            game = CROCODILE_GAMES.get(chat_id)
            still_active = bool(game and game["word"] == word)
        if still_active:
            _crocodile_end_round(chat_id, "timeout")

    timer = threading.Timer(seconds, _job)
    timer.daemon = True
    timer.start()
    return timer


@bot.message_handler(commands=['крокодил', 'crocodile'])
def crocodile_command(message):
    if is_user_banned(message.from_user.id):
        return
    if message.chat.type == 'private':
        return bot.reply_to(message, "🐊 У «Крокодил» можна грати тільки в груповому чаті.")

    chat_id = message.chat.id
    args = [a.lower() for a in message.text.split()[1:]]

    # --- Довідка ---
    if args[:1] in (["допомога"], ["help"]):
        return bot.reply_to(
            message,
            "🐊 *Крокодил — команди*\n\n"
            "/крокодил — почати раунд (випадкова категорія й складність)\n"
            "/крокодил [категорія] [складність] — напр. `/крокодил тварини важко`\n"
            "/підказка — підказка в поточному раунді\n"
            "/крокодил скіп — ведучий пропускає слово\n"
            "/крокодил стоп — завершити поточний раунд\n"
            "/крокодил топ — рейтинг гравців чату\n\n"
            f"*Категорії:* {_crocodile_category_list_str()}\n"
            f"*Складність:* {', '.join(CROCODILE_DIFFICULTIES)}",
            parse_mode="Markdown",
        )

    # --- Рейтинг ---
    if args[:1] in (["топ"], ["top"]):
        entries = _crocodile_top(chat_id, limit=10)
        if not entries:
            return bot.reply_to(message, "🐊 Поки що ніхто не заробив очок. Зіграйте раунд!")
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, pts) in enumerate(entries):
            try:
                name = bot.get_chat_member(chat_id, uid).user.first_name
            except Exception:
                name = f"Гравець {uid}"
            prefix = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{prefix} {name} — *{pts}* очок")
        return bot.reply_to(
            message, "🏆 *Рейтинг гравців у «Крокодил»*\n\n" + "\n".join(lines), parse_mode="Markdown"
        )

    # --- Зупинити гру ---
    if args[:1] in (["стоп"], ["stop"], ["завершити"]):
        with CROCODILE_LOCK:
            active = chat_id in CROCODILE_GAMES
        if active:
            _crocodile_end_round(chat_id, "stop")
        else:
            bot.reply_to(message, "🐊 Зараз немає активної гри в цьому чаті.")
        return

    # --- Пропустити слово (тільки ведучий) ---
    if args[:1] in (["скіп"], ["скип"], ["skip"]):
        with CROCODILE_LOCK:
            game = CROCODILE_GAMES.get(chat_id)
        if not game:
            return bot.reply_to(message, "🐊 Зараз немає активної гри в цьому чаті.")
        if message.from_user.id != game["host_id"]:
            return bot.reply_to(message, "⚠️ Пропустити слово може лише поточний ведучий.")
        _crocodile_end_round(chat_id, "skip")
        return

    # --- Вже є гра в цьому чаті ---
    with CROCODILE_LOCK:
        if chat_id in CROCODILE_GAMES:
            return bot.reply_to(
                message,
                "🐊 В цьому чаті вже йде гра! Завершіть її `/крокодил стоп` "
                "або пропустіть слово `/крокодил скіп`.",
                parse_mode="Markdown",
            )

    # --- Розбір необов'язкових аргументів категорії/складності ---
    category_key = None
    difficulty = None
    for a in args:
        if category_key is None:
            found = _crocodile_resolve_category(a)
            if found:
                category_key = found
                continue
        if difficulty is None:
            found_d = _crocodile_resolve_difficulty(a)
            if found_d:
                difficulty = found_d

    if args and category_key is None and difficulty is None:
        return bot.reply_to(
            message,
            f"⚠️ Не впізнав категорію чи складність.\n"
            f"Категорії: {_crocodile_category_list_str()}\n"
            f"Складність: {', '.join(CROCODILE_DIFFICULTIES)}",
        )

    word, category_key, difficulty = _crocodile_pick_word(category_key, difficulty)
    meta = CROCODILE_DIFFICULTY_META[difficulty]

    with CROCODILE_LOCK:
        prev = CROCODILE_GAMES.get(chat_id)
        round_no = (prev["round_no"] + 1) if prev else 1
        CROCODILE_GAMES[chat_id] = {
            "word": word,
            "category": category_key,
            "difficulty": difficulty,
            "host_id": message.from_user.id,
            "host_name": message.from_user.first_name,
            "hints_used": 0,
            "round_no": round_no,
            "timer": None,
        }

    timer = _crocodile_schedule_timeout(chat_id, word, meta["seconds"])
    with CROCODILE_LOCK:
        if chat_id in CROCODILE_GAMES and CROCODILE_GAMES[chat_id]["word"] == word:
            CROCODILE_GAMES[chat_id]["timer"] = timer

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔍 Переглянути своє слово", callback_data=f"croc_word_{chat_id}"))

    bot.send_message(
        chat_id,
        f"🐊 *Раунд #{round_no} — «Крокодил» почався!*\n\n"
        f"Ведучий: *{message.from_user.first_name}*\n"
        f"Категорія: {category_key.replace('_', ' ')}\n"
        f"Складність: {meta['emoji']} {difficulty}\n"
        f"Час на раунд: {meta['seconds']} сек ⏱️\n\n"
        f"Ведучий тисне кнопку нижче — слово побачить *тільки він* — і пояснює його "
        f"жестами й словами, не називаючи прямо!\n"
        f"Інші пишуть варіанти прямо сюди в чат — хто вгадає, стає новим ведучим і отримує очки 🏆\n\n"
        f"💡 /підказка — підказка · ⏭️ /крокодил скіп · 🛑 /крокодил стоп",
        parse_mode="Markdown",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("croc_word_"))
def crocodile_show_word(call):
    try:
        target_chat_id = int(call.data.split("_")[-1])
    except ValueError:
        return bot.answer_callback_query(call.id, "Помилка.")

    with CROCODILE_LOCK:
        game = CROCODILE_GAMES.get(target_chat_id)

    if not game:
        return bot.answer_callback_query(call.id, "🐊 Цей раунд уже завершився.", show_alert=True)
    if call.from_user.id != game["host_id"]:
        return bot.answer_callback_query(call.id, "🙈 Це не твоє слово!", show_alert=True)

    meta = CROCODILE_DIFFICULTY_META[game["difficulty"]]
    bot.answer_callback_query(
        call.id,
        f"Твоє слово: {game['word']}\n"
        f"Категорія: {game['category'].replace('_', ' ')} ({game['difficulty']} {meta['emoji']})",
        show_alert=True,
    )


@bot.message_handler(commands=['підказка', 'hint', 'підкажи'])
def crocodile_hint(message):
    if is_user_banned(message.from_user.id):
        return
    chat_id = message.chat.id

    with CROCODILE_LOCK:
        game = CROCODILE_GAMES.get(chat_id)
        if not game:
            return bot.reply_to(message, "🐊 Зараз немає активної гри в цьому чаті.")
        if message.from_user.id == game["host_id"]:
            return bot.reply_to(message, "🙈 Ведучий не може сам собі підказувати!")
        if game["hints_used"] >= 2:
            return bot.reply_to(message, "🐊 Підказки на цей раунд закінчилися. Думайте головою! 🧠")
        game["hints_used"] += 1
        hints_used = game["hints_used"]
        word = game["word"]
        category = game["category"]

    if hints_used == 1:
        hint_text = f"Категорія: *{category.replace('_', ' ')}*, довжина слова: *{len(word)}* літер."
    else:
        hint_text = f"Слово починається на літеру «*{word[0].upper()}*»."
    bot.reply_to(message, f"💡 Підказка №{hints_used}: {hint_text}", parse_mode="Markdown")


def _crocodile_is_guess(message):
    if not message.text or message.text.startswith('/'):
        return False
    with CROCODILE_LOCK:
        game = CROCODILE_GAMES.get(message.chat.id)
    if not game or not message.from_user or message.from_user.id == game["host_id"]:
        return False
    pattern = r"\b" + re.escape(game["word"].lower()) + r"\b"
    return bool(re.search(pattern, message.text.lower()))


@bot.message_handler(func=_crocodile_is_guess)
def crocodile_guess_handler(message):
    chat_id = message.chat.id
    with CROCODILE_LOCK:
        game = CROCODILE_GAMES.get(chat_id)
        # подвійна перевірка під локом — раптом раунд щойно завершився в іншому потоці
        if not game or message.from_user.id == game["host_id"]:
            return
        host_id = game["host_id"]

    _crocodile_score_add(chat_id, message.from_user.id, 3)
    _crocodile_score_add(chat_id, host_id, 1)
    _crocodile_end_round(chat_id, "guessed", guesser_name=message.from_user.first_name)


# ===================================================================
# 🚀 ЗАПУСК БОТА ТА ВЕБ-СЕРВЕРА
# ===================================================================
if __name__ == "__main__":
    bot.enable_save_next_step_handlers(delay=2)
    bot.load_next_step_handlers()
    
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()
    print("🚀 Dummy-сервер успішно запущено.")

    discord_thread = threading.Thread(target=run_discord, daemon=True)
    discord_thread.start()

    cleanup_thread = threading.Thread(target=auto_cleanup_scheduler, daemon=True)
    cleanup_thread.start()
    print("🧹 Автоматична чистка бази запущена (раз на 6 годин).")
    
    print("🔥 Драго вийшов на полювання і готовий до роботи на Neon DB!")
    bot.infinity_polling(allowed_updates=['message', 'edited_message', 'chat_member', 'callback_query'])
