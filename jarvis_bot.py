import os
import base64
import requests
import time
import random
import io
import threading
import asyncio
import edge_tts
from http.server import SimpleHTTPRequestHandler, HTTPServer
import telebot
from telebot import types
import google.generativeai as genai
from PIL import Image
import psycopg2
import discord
from discord.ext import commands


DATABASE_URL = os.environ.get('DATABASE_URL')

# Підключаємося до БД
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# 🔒 ЛОК ДЛЯ БЕЗПЕКИ ПОТОКІВ
db_lock = threading.Lock()

# Створення таблиць при запуску
try:
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Таблиця статистики (додано balance)
            cursor.execute("""CREATE TABLE IF NOT EXISTS stats (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                count INTEGER,
                gender TEXT,
                in_chat BOOLEAN DEFAULT TRUE,
                balance BIGINT DEFAULT 0
            )""")
            # Безпечно додаємо колонку balance, якщо таблиця вже існує
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS balance BIGINT DEFAULT 0;")
            
            # Таблиця майна (Монополія)
            cursor.execute("""CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                item_code TEXT,
                item_name TEXT,
                item_category TEXT
            )""")
            
            # Таблиця шлюбів
            cursor.execute("""CREATE TABLE IF NOT EXISTS marriages (
                user1_id BIGINT,
                user2_id BIGINT,
                UNIQUE(user1_id),
                UNIQUE(user2_id)
            )""")
        conn.commit()
        conn.close()
except Exception as e:
    print(f"Помилка створення таблиць: {e}")
 
# ==================== НАЛАШТУВАННЯ ====================
API_ID = int(os.environ.get('API_ID', 12345678))
API_HASH = os.environ.get('API_HASH', 'ТВІЙ_API_HASH')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ТВІЙ_TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'ТВІЙ_GEMINI_API_KEY')
# ======================================================

DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'ТВІЙ_ДИСКОРД_ТОКЕН')
# Сюди впиши ID твого Телеграм-чату, куди бот має кидати анонси стрімів:
TELEGRAM_CHAT_ID = -1003428241218  # Заміни на реальний ID свого чату


bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

generation_config = {
    "max_output_tokens": 2096,
    "temperature": 0.85,
}

# Залиш тільки цей блок
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=(
        "Ти — зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
        "Спілкуйся виключно українською мовою. Використовуй молодіжний сленг, міцні слівця, "
        "іноді нецензурну лексику (мати) для емоційності, якщо це доречно. "
        "Будь живим, неформальним розмовником, злегка іронізуй, підколюй юзера, але завжди допомагай. "
        "Обов'язково закінчуй свої думки, не обривай речення на пів слові! "
        "Пиши коротко і ясно!, твій создатель СБУ, якщо ти пишеш досє ілі новини відповідай повністю"
    )
)
 
# Пам'ять чатів
bot_chats = {}

# Пам'ять для МЕМНИХ НОВИН
RECENT_MESSAGES = []
MAX_HISTORY_LIMIT = 30

# 🧠 ОЧИЩЕННЯ ПАМ'ЯТІ GEMINI
def get_gemini_chat(chat_id):
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    else:
        if len(bot_chats[chat_id].history) > 30:
            bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(("", port), SimpleHTTPRequestHandler)
    httpd.serve_forever()

# 🚫 ПЕРЕВІРКА НА БАН
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
# 🗣️ СИСТЕМА РОБОТИ З ГОЛОСОВИМИ ПОВІДОМЛЕННЯМИ (Edge TTS)
# ===================================================================
def send_voice_reply(chat_id, text_to_speak, reply_to_id=None):
    try:
        voice_file = f"drago_voice_{chat_id}.ogg"
        communicate = edge_tts.Communicate(text_to_speak, "uk-UA-OstapNeural", rate="+15%")
        asyncio.run(communicate.save(voice_file))
        
        with open(voice_file, 'rb') as f:
            bot.send_voice(chat_id, f, reply_to_message_id=reply_to_id)
            
        if os.path.exists(voice_file):
            os.remove(voice_file)
            
    except Exception as e:
        print(f"Помилка озвучки TTS: {e}")
        bot.send_message(chat_id, text_to_speak, reply_to_message_id=reply_to_id)

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id
    chat_type = message.chat.type
    if chat_type in ['group', 'supergroup']:
        if not (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id):
            return
    try:
        bot.send_chat_action(chat_id, 'record_voice')
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        audio_part = {"data": downloaded_file, "mime_type": "audio/ogg"}
        prompt = "Послухай це голосове повідомлення, зрозумій що сказав користувач і дай повну дотепну відповідь як Драго:"
        response = model.generate_content([prompt, audio_part])
        
        send_voice_reply(chat_id, response.text, reply_to_id=message.message_id)
        
    except Exception as e:
        print(f"Помилка голосового: {e}")
        bot.reply_to(message, "Не зміг розпарсити твоє голосове або заговорити у відповідь.")


# ===================================================================
# 👑 КАРТКА ПРОФІЛЮ ТА КАСТОМІЗАЦІЯ (ПОВНИЙ ОБ'ЄДНАНИЙ МОДУЛЬ)
# ===================================================================

PRICES = {
    "title": 50000,    # Ціна зміни титулу
    "nick": 30000,     # Ціна зміни ніку
    "photo": 100000,   # Ціна зміни аватарки
    "reorder": 15000   # Ціна пересортування
}

# -------------------------------------------------------------------
# 🛠 1. ДОПОМІЖНІ ФУНКЦІЇ БАЗИ ДАНИХ ДЛЯ КАСТОМІЗАЦІЇ
# -------------------------------------------------------------------
def get_user_balance(user_id):
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
            res = cursor.fetchone()
        conn.close()
    return res[0] if res else 0

def update_user_balance(user_id, amount):
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (amount, user_id))
            conn.commit()
        conn.close()

def update_user_field(user_id, field_name, value):
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Динамічне оновлення безпечного поля
            cursor.execute(f"UPDATE stats SET {field_name} = %s WHERE user_id = %s", (value, user_id))
            conn.commit()
        conn.close()


# -------------------------------------------------------------------
# 🏷 2. КОМАНДИ НАЛАШТУВАННЯ КАСТОМІЗАЦІЇ (З ВИПРАВЛЕНИМИ РЕГЕКСАМИ)
# -------------------------------------------------------------------

# 1. 🏷 ЗМІНА ТИТУЛУ (/титул Король Оболоні)
@bot.message_handler(regexp=r'^(?i)[/!]?(?:титул|set_title)(?:\s+|$)')
def set_custom_title(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, f"❌ Вкажи новий титул!\nПриклад: <code>/титул Король Оболоні</code>\nВартість: <b>{PRICES['title']:,} грн</b>.", parse_mode="HTML")
        return

    new_title = args[1][:30].strip()
    balance = get_user_balance(user_id)

    if balance < PRICES['title']:
        bot.reply_to(message, f"💸 Нестача бабок! Зміна титулу коштує <b>{PRICES['title']:,} грн</b>.", parse_mode="HTML")
        return

    update_user_balance(user_id, -PRICES['title'])
    update_user_field(user_id, "custom_title", new_title)
    bot.reply_to(message, f"😎 База прийняла! Твій новий титул: <b>{new_title}</b>", parse_mode="HTML")


# 2. 📛 ЗМІНА ІМЕНІ / НІКУ (/нік Лютий)
@bot.message_handler(regexp=r'^(?i)[/!]?(?:нік|set_nick)(?:\s+|$)')
def set_custom_nick(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, f"❌ Вкажи новий нік!\nПриклад: <code>/нік Лютий</code>\nВартість: <b>{PRICES['nick']:,} грн</b>.", parse_mode="HTML")
        return

    new_nick = args[1][:25].strip()
    balance = get_user_balance(user_id)

    if balance < PRICES['nick']:
        bot.reply_to(message, f"💸 Не вистачає кешу! Зміна ніку коштує <b>{PRICES['nick']:,} грн</b>.", parse_mode="HTML")
        return

    update_user_balance(user_id, -PRICES['nick'])
    update_user_field(user_id, "custom_nick", new_nick)
    bot.reply_to(message, f"👌 Відтепер у базі ти — <b>{new_nick}</b>!", parse_mode="HTML")


# 3. 🖼 ВСТАНОВЛЕННЯ АВАТАРКИ (/фото або реплай з /фото)
@bot.message_handler(func=lambda m: (m.text and any(m.text.lower().startswith(x) for x in ['/фото', 'фото', '/set_photo'])) or 
                                  (m.caption and any(m.caption.lower().startswith(x) for x in ['/фото', 'фото', '/set_photo'])))
def set_custom_photo(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    
    photo_url = None
    # 1. Якщо це фото з підписом
    if message.photo:
        photo_url = message.photo[-1].file_id
    # 2. Якщо це реплай на фото
    elif message.reply_to_message and message.reply_to_message.photo:
        photo_url = message.reply_to_message.photo[-1].file_id
    # 3. Якщо надали посилання в тексті
    else:
        text_content = message.text or message.caption or ""
        args = text_content.split(maxsplit=1)
        if len(args) > 1:
            photo_url = args[1].strip()

    if not photo_url:
        bot.reply_to(message, f"❌ Зроби реплай на фото з командою <code>/фото</code> або надішли картинку з підписом <code>/фото</code>!\nВартість: <b>{PRICES['photo']:,} грн</b>.", parse_mode="HTML")
        return

    balance = get_user_balance(user_id)
    if balance < PRICES['photo']:
        bot.reply_to(message, f"💸 Елітний атрибут! Зміна фото коштує <b>{PRICES['photo']:,} грн</b>.", parse_mode="HTML")
        return

    update_user_balance(user_id, -PRICES['photo'])
    update_user_field(user_id, "custom_photo", photo_url)
    bot.reply_to(message, "📸 Фото профілю успішно оновлено!", parse_mode="HTML")


# 4. 🏎 СОРТУВАННЯ МАЙНА (/порядок_майна bmw villa)
@bot.message_handler(regexp=r'^(?i)[/!]?(?:порядок_майна|sort_items)(?:\s+|$)')
def sort_items(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, f"⚙️ Вкажи коди предметів через пробіл!\nПриклад: <code>/порядок_майна bmw villa rolex</code>\nВартість послуги: <b>{PRICES['reorder']:,} грн</b>.", parse_mode="HTML")
        return

    new_order = args[1].lower().split()
    balance = get_user_balance(user_id)

    if balance < PRICES['reorder']:
        bot.reply_to(message, f"💸 Перестановка коштує <b>{PRICES['reorder']:,} грн</b>.", parse_mode="HTML")
        return

    update_user_balance(user_id, -PRICES['reorder'])
    update_user_field(user_id, "inventory_order", ",".join(new_order))
    bot.reply_to(message, "📦 Порядок речей у профілі успішно відсортовано!", parse_mode="HTML")


# 5. 🏢 СОРТУВАННЯ БІЗНЕСІВ (/порядок_бізнесів kebab lavka)
@bot.message_handler(regexp=r'^(?i)[/!]?(?:порядок_бізнесів|sort_biz)(?:\s+|$)')
def sort_businesses(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, f"⚙️ Вкажи коди бізнесів через пробіл!\nПриклад: <code>/порядок_бізнесів kebab lavka hotel</code>\nВартість перестановки: <b>{PRICES['reorder']:,} грн</b>.", parse_mode="HTML")
        return

    new_order = args[1].lower().split()
    balance = get_user_balance(user_id)

    if balance < PRICES['reorder']:
        bot.reply_to(message, f"💸 Перестановка коштує <b>{PRICES['reorder']:,} грн</b>.", parse_mode="HTML")
        return

    update_user_balance(user_id, -PRICES['reorder'])
    update_user_field(user_id, "biz_order", ",".join(new_order))
    bot.reply_to(message, "💼 Порядок бізнесів у каталозі успішно оновлено!", parse_mode="HTML")


# -------------------------------------------------------------------
# 🪪 3. ГОЛОВНА ВІДОБРАЖУВАЛЬНА ФУНКЦІЯ ПАСПОРТА/ПРОФІЛЮ
# -------------------------------------------------------------------

@bot.message_handler(regexp=r'^(?i)[/!]?(?:профіль|profile)(?:\s+|$)')
def show_user_profile(message):
    if is_user_banned(message.from_user.id): return

    chat_id = message.chat.id
    user = message.from_user
    
    if message.reply_to_message:
        user = message.reply_to_message.from_user

    bot.send_chat_action(chat_id, 'upload_photo')
    ensure_user_in_db(user)
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # 1. Беремо статистику, баланс + кастомні дані кастомізації
                cursor.execute("""
                    SELECT count, balance, gender, custom_nick, custom_title, custom_photo, inventory_order, biz_order 
                    FROM stats WHERE user_id = %s
                """, (user.id,))
                stats_res = cursor.fetchone()
                
                # 2. Беремо майно з інвентарю
                cursor.execute("SELECT item_code, item_name FROM inventory WHERE user_id = %s", (user.id,))
                inventory_res = cursor.fetchall()

                # 3. Беремо бізнеси користувача
                cursor.execute("SELECT biz_code FROM user_businesses WHERE user_id = %s", (user.id,))
                biz_res = cursor.fetchall()
                
                # 4. Перевіряємо шлюб
                cursor.execute("""
                    SELECT s1.name, s2.name, m.user1_id, m.user2_id 
                    FROM marriages m
                    JOIN stats s1 ON m.user1_id = s1.user_id
                    JOIN stats s2 ON m.user2_id = s2.user_id
                    WHERE m.user1_id = %s OR m.user2_id = %s
                """, (user.id, user.id))
                marriage_res = cursor.fetchone()
                
            conn.close()

        # Розпаковка отриманих даних
        msg_count = stats_res[0] if stats_res else 0
        balance = stats_res[1] if stats_res else 0
        gender = stats_res[2] if stats_res else "Невідомо"
        custom_nick = stats_res[3] if stats_res else None
        custom_title = stats_res[4] if stats_res else None
        custom_photo = stats_res[5] if stats_res else None
        inv_order_raw = stats_res[6] if stats_res else ""
        biz_order_raw = stats_res[7] if stats_res else ""

        # Визначення імені та рангу
        display_name = custom_nick if custom_nick else user.first_name
        clean_name = display_name.replace("<", "&lt;").replace(">", "&gt;")
        
        rank = get_rank_title(msg_count)
        gender_icon = "🕺" if gender == "Хлопець" else "💃" if gender == "Дівчина" else "👤"
        title_text = f"👑 <b>Титул:</b> <code>{custom_title}</code>\n" if custom_title else ""

        # 🏢 Сортування та підрахунок БІЗНЕСІВ
        owned_biz_codes = [r[0] for r in biz_res]
        biz_counts = {}
        total_biz_value = 0
        total_passive_income = 0

        for b_code in owned_biz_codes:
            if b_code in BUSINESSES:
                biz = BUSINESSES[b_code]
                biz_counts[b_code] = biz_counts.get(b_code, 0) + 1
                total_biz_value += biz["price"]
                total_passive_income += biz["income"]

        # Пріоритет сортування бізнесів
        custom_biz_order = [b.strip() for b in biz_order_raw.split(",") if b.strip()]
        sorted_biz_codes = sorted(biz_counts.keys(), key=lambda x: custom_biz_order.index(x) if x in custom_biz_order else 999)

        if not biz_counts:
            biz_text = "<i>Безробітний 😴</i>"
        else:
            biz_list = []
            for b_code in sorted_biz_codes:
                b_name = BUSINESSES[b_code]["name"]
                b_count = biz_counts[b_code]
                c_str = f" x{b_count}" if b_count > 1 else ""
                biz_list.append(f"{b_name}{c_str}")
            biz_text = ", ".join(biz_list)
            if len(biz_text) > 100: biz_text = biz_text[:95] + "..."

        # 📦 Сортування та підрахунок МАЙНА (РЕЧЕЙ)
        total_property_value = 0
        item_counts = {}
        item_names_map = {}

        for code, name in inventory_res:
            item_counts[code] = item_counts.get(code, 0) + 1
            item_names_map[code] = name
            if code in SHOP_ITEMS:
                total_property_value += SHOP_ITEMS[code]["price"]

        # Пріоритет сортування речей
        custom_inv_order = [i.strip() for i in inv_order_raw.split(",") if i.strip()]
        sorted_item_codes = sorted(item_counts.keys(), key=lambda x: custom_inv_order.index(x) if x in custom_inv_order else 999)

        if not item_counts:
            property_text = "<i>Тільки шкарпетки й мобільник 📱</i>"
        else:
            property_list = []
            for code in sorted_item_codes:
                i_name = item_names_map[code]
                i_count = item_counts[code]
                c_str = f" x{i_count}" if i_count > 1 else ""
                property_list.append(f"{i_name}{c_str}")
            property_text = ", ".join(property_list)
            if len(property_text) > 100: property_text = property_text[:95] + "..."

        # 💍 Формуємо статус шлюбу
        if marriage_res:
            name1, name2, u1_id, u2_id = marriage_res
            spouse_name = name2 if user.id == u1_id else name1
            marriage_status = f"💍 У шлюбі з <b>{spouse_name.replace('<', '&lt;').replace('>', '&gt;')}</b>"
        else:
            marriage_status = "🐺 Статус: <i>Самотній вовк</i>"

        # Загальний капітал
        total_net_worth = balance + total_property_value + total_biz_value

        # 📜 Формування картки профілю
        profile_card = (
            f"🪪 <b>ПАСПОРТ АВТОРИТЕТА: {clean_name.upper()}</b>\n"
            f"───────────────────────\n"
            f"{title_text}"
            f"{gender_icon} <b>Ранг у чаті:</b> <code>{rank}</code>\n"
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

        # 📸 ВИЗНАЧЕННЯ АВАТАРКИ ПРОФІЛЮ
        final_photo = None

        if custom_photo:
            final_photo = custom_photo
        else:
            try:
                photos = bot.get_user_profile_photos(user.id, limit=1)
                if photos.total_count > 0:
                    final_photo = photos.photos[0][-1].file_id
                else:
                    final_photo = "https://i.ibb.co/5G1v5f2/no-avatar.jpg"
            except Exception:
                final_photo = "https://i.ibb.co/5G1v5f2/no-avatar.jpg"

        # Відправка зібраного профілю
        bot.send_photo(
            chat_id, 
            photo=final_photo, 
            caption=profile_card, 
            parse_mode="HTML", 
            reply_to_message_id=message.message_id
        )

    except Exception as e:
        print(f"Помилка створення профілю: {e}")
        bot.reply_to(message, "❌ Не вдалося згенерувати твій паспорт активів. База даних підвисла.")


# ===================================================================
# ⚙️ НАЛАШТУВАННЯ ТА ІНІЦІАЛІЗАЦІЯ (Додайте це у ваш основний файл)
# ===================================================================
# Переконайтеся, що ці змінні вже визначені у вашому основному коді:
# bot = telebot.TeleBot(TOKEN)
# db_lock = threading.Lock()
# def get_db_connection(): ...
# def is_user_banned(user_id): ...

# ===================================================================
# 💼 СИСТЕМА БІЗНЕСІВ ТА ПАСИВНОГО ДОХОДУ
# ===================================================================

# Каталог бізнесів
BUSINESSES = {
    "kebab": {
        "name": "🌯 I Love Kebab", 
        "price": 60000, 
        "income": 2200, 
        "ai_desc": "cozy small fast food kebab restaurant, bright light, realistic"
    },
    "cigars": {
        "name": "🚬 Контрабанда цигарок", 
        "price": 350000, 
        "income": 12500, 
        "ai_desc": "secret cargo truck, cardboard boxes, custom control border crossing, dark night"
    },
    "atb": {
        "name": "🛒 Мережа АТБ", 
        "price": 1800000, 
        "income": 60000, 
        "ai_desc": "huge modern green and red ATB supermarket store building, parking lot"
    },
    "split": {
        "name": "🌃 Нічний Клуб Split", 
        "price": 7500000, 
        "income": 230000, 
        "ai_desc": "luxurious VIP Split night club exterior, golden lighting, lasers, realistic"
    },
    "nvidia": {
        "name": "🤖 Компанія NVIDIA", 
        "price": 35000000, 
        "income": 1100000, 
        "ai_desc": "futuristic neon green NVIDIA headquarters building, high-tech server room"
    }
}

# 🛠️ АВТОМАТИЧНЕ СТВОРЕННЯ ТАБЛИЦІ В БД
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
                        last_collect TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            conn.commit()
            conn.close()
            print("✅ Таблиця user_businesses перевірена/створена.")
    except Exception as e:
        print(f"⚠️ Помилка ініціалізації БД бізнесів: {e}")

init_business_db()

# 🎨 ФУНКЦІЯ ГЕНЕРАЦІЇ ЄДИНОГО ФОТО БІЗНЕСІВ ЧЕРЕЗ AI (Pollinations)
def generate_business_ai_image(owned_biz_codes):
    """Генерує одну суцільну картинку, що показує всі бізнеси користувача разом"""
    if not owned_biz_codes:
        return None
        
    # Збираємо описи для AI тільки для унікальних кодів
    unique_codes = list(set(owned_biz_codes))
    ai_descriptions = []
    
    # Обмежуємо до 4 бізнесів, щоб картинка не була надто хаотичною
    for code in unique_codes[:4]:
        if code in BUSINESSES:
            ai_descriptions.append(BUSINESSES[code]["ai_desc"])
            
    if not ai_descriptions:
        return None
        
    # Формуємо промпт
    items_prompt = ", ".join(ai_descriptions)
    prompt = (
        f"A ultra-realistic cinematic wide shot photography representing a successful business empire ownership, "
        f"showing elements of: {items_prompt}. Masterpiece, dynamic lighting, high detail, 4k."
    )
    
    try:
        encoded_prompt = requests.utils.quote(prompt)
        # Використовуємо Flux модель для кращої деталізації
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1, 9999)}&model=flux&nologo=true"
        
        response = requests.get(image_url, timeout=30)
        
        if response.status_code == 200:
            # Перевіряємо, чи це дійсно картинка, а не помилка JSON
            if "application/json" in response.headers.get("Content-Type", ""):
                return None
            
            # Обробляємо картинку через PIL, щоб переконатися в форматі
            img = Image.open(io.BytesIO(response.content))
            img = img.convert("RGB") # Конвертуємо в RGB для сумісності
            
            bio = io.BytesIO()
            bio.name = 'business_empire.jpg'
            img.save(bio, 'JPEG', quality=95)
            bio.seek(0)
            return bio
        else:
            print(f"⚠️ Pollinations повернув код: {response.status_code}")
            return None
    except Exception as e:
        print(f"⚠️ Помилка генерації AI фото: {e}")
        return None

# ===================================================================
# 🏢 КОМАНДИ БОТА
# ===================================================================

# 🏢 КОМАНДА: МОЇ БІЗНЕСИ ТА КАТАЛОГ (/biz, /бізнеси)
@bot.message_handler(commands=['biz', 'бізнеси', 'бизнесы'])
def show_businesses(message):
    if is_user_banned(message.from_user.id): return

    user_id = message.from_user.id
    user_name = message.from_user.first_name

    # Надсилаємо статус, бо генерація картинки займає час
    status_msg = bot.reply_to(message, "⏳ <i>Зачекай, Драго підраховує твої активи та малює картинку імперії...</i>", parse_mode="HTML")

    try:
        # 1. Отримуємо дані з БД
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Отримуємо бізнеси користувача
                cursor.execute("SELECT biz_code FROM user_businesses WHERE user_id = %s", (user_id,))
                owned_rows = cursor.fetchall()
                
                # Отримуємо баланс
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0
            conn.close()

        owned_codes = [row[0] for row in owned_rows]
        
        # 2. Формуємо текст
        text = [
            f"👑 <b>БІЗНЕС-ІМПЕРІЯ:</b> {user_name}\n",
            f"💳 Баланс: <code>{balance:,} грн</code>\n",
            "────────────────────",
            "📊 <b>КАТАЛОГ ТА ТВОЇ АКТИВИ:</b>\n"
        ]

        total_income_per_hour = 0
        
        for code, biz in BUSINESSES.items():
            count = owned_codes.count(code)
            
            # Розрахунок доходу
            income_str = f"+{biz['income']:,} грн/год"
            
            if count > 0:
                text.append(f"✅ <b>{biz['name']}</b> (<code>{code}</code>)")
                text.append(f" ├ 📈 Власність: <b>{count} шт.</b>")
                text.append(f" └ 💰 Дохід: <code>{biz['income']*count:,} грн/год</code>\n")
                total_income_per_hour += (biz['income'] * count)
            else:
                text.append(f"⚪ {biz['name']} (<code>{code}</code>)")
                text.append(f" ├ Ціна: <code>{biz['price']:,} грн</code>")
                text.append(f" └ Дохід: <code>{income_str}</code>\n")

        text.append("────────────────────")
        text.append(f"📈 Загальний пасивний дохід: <b>{total_income_per_hour:,} грн/год</b>")
        text.append("\n💡 <i>Купити: /купити_бізнес [код]</i>")
        text.append("💡 <i>Зібрати касу: /зібрати</i>")
        
        caption_text = "\n".join(text)

        # 3. Генеруємо AI картинку
        if owned_codes:
            photo = generate_business_ai_image(owned_codes)
            if photo:
                bot.delete_message(message.chat.id, status_msg.message_id)
                bot.send_photo(message.chat.id, photo=photo, caption=caption_text, parse_mode="HTML")
                return

        # Якщо бізнесів немає або AI не спрацював — просто редагуємо текст
        bot.edit_message_text(caption_text, message.chat.id, status_msg.message_id, parse_mode="HTML")

    except Exception as e:
        print(f"❌ Помилка команди /бізнеси: {e}")
        bot.edit_message_text(f"❌ Не вдалося завантажити дані про бізнес. Помилка БД.", message.chat.id, status_msg.message_id)

# 🛒 КОМАНДА: КУПІВЛЯ БІЗНЕСУ (/купити_бізнес)
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
                # Перевірка балансу
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0

                if balance < biz["price"]:
                    bot.reply_to(message, f"💸 <b>Недостатньо грошей!</b>\nТобі треба <code>{biz['price']:,} грн</code>.\nЗараз у тебе: <code>{balance:,} грн</code>.", parse_mode="HTML")
                    conn.close()
                    return

                # Списання грошей та додавання бізнесу
                cursor.execute("UPDATE stats SET balance = balance - %s WHERE user_id = %s", (biz["price"], user_id))
                cursor.execute("INSERT INTO user_businesses (user_id, biz_code) VALUES (%s, %s)", (user_id, biz_code))

            conn.commit()
            conn.close()

        bot.reply_to(
            message, 
            f"🎉 <b>ВІТАЄМО З УГОДОЮ!</b> 🎉\n\n"
            f"Ти успішно купив бізнес: <b>{biz['name']}</b>!\n"
            f"Гроші списано, власність оформлена.\n"
            f"📈 Дохід <code>+{biz['income']:,} грн/год</code> вже нараховується.\n"
            f"Не забудь збирати касу через `/зібрати`!", 
            parse_mode="HTML"
        )
        
    except Exception as e:
        print(f"❌ Помилка купівлі бізнесу: {e}")
        bot.reply_to(message, f"❌ Помилка угоди. Спробуй пізніше.")

# 💵 КОМАНДА: ЗБІР ПРИБУТКУ (/зібрати, /каса, /прибуток)
@bot.message_handler(commands=['зібрати', 'каса', 'collect', 'прибуток'])
def collect_business_income(message):
    if is_user_banned(message.from_user.id): return

    user_id = message.from_user.id

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Отримуємо всі бізнеси користувача та час, що минув (в годинах)
                # EXTRACT(EPOCH FROM (NOW() - last_collect)) / 3600 — рахує різницю в годинах
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
                biz_details = []
                # Ліміт накопичення — 24 години
                time_limit_hours = 24.0

                for row in user_bizs:
                    biz_id, biz_code, hours_passed = row[0], row[1], row[2]
                    
                    if biz_code in BUSINESSES:
                        biz = BUSINESSES[biz_code]
                        
                        # Застосовуємо ліміт часу
                        effective_hours = min(hours_passed, time_limit_hours)
                        
                        # Потрібно хоча б 1 хвилина роботи (0.016 години)
                        if effective_hours >= 0.016: 
                            earned = int(effective_hours * biz["income"])
                            total_earned += earned
                            
                            time_str = f"{int(effective_hours)}г {int((effective_hours%1)*60)}хв"
                            biz_details.append(f"• <b>{biz['name']}</b> (за {time_str}): <code>+{earned:,} грн</code>")

                if total_earned <= 0:
                    bot.reply_to(message, "⏳ <b>Каса ще порожня!</b> Бізнеси працюють, але прибуток ще не накопичився (зачекай хоча б хвилину).")
                    conn.close()
                    return

                # --- 🎲 Випадкові події (Пацанський рандом) ---
                event_text = ""
                # "Reputation" базується на вартості бізнесів
                reputation_bonus = min(len(user_bizs) // 2, 10) # max 10% бонус
                rand_event = random.randint(1, 100) + reputation_bonus
                
                if rand_event <= 8: # Податкова перевірка (Погано)
                    penalty = int(total_earned * 0.15)
                    total_earned -= penalty
                    event_text = f"\n\n🚨 <b>ПОДАТКОВА ПЕРЕВІРКА:</b> Прийшов штраф або хабар на <code>-{penalty:,} грн</code>! Відкупився..."
                elif rand_event >= 92: # Ажіотаж (Добре)
                    bonus = int(total_earned * 0.30)
                    total_earned += bonus
                    event_text = f"\n\n🔥 <b>БЕШЕНИЙ ПОПИТ:</b> Наплив клієнтів приніс додаткові <code>+{bonus:,} грн</code>! Всі задоволені."
                elif rand_event == 50: # Рейдерська атака (Дуже погано, але рідко)
                    total_earned = 0
                    event_text = f"\n\n🥷 <b>РЕЙДЕРСЬКА АТАКА!</b> Касу намагалися віджати. Гроші довелося заховати, прибутку за цей період немає."

                # Оновлюємо час збору ТІЛЬКИ ДЛЯ ОБРОБЛЕНИХ БІЗНЕСІВ
                # Але простіше оновити всім last_collect = NOW()
                cursor.execute("UPDATE user_businesses SET last_collect = NOW() WHERE user_id = %s", (user_id,))
                
                # Додаємо гроші на баланс
                cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (total_earned, user_id))

            conn.commit()
            conn.close()

        msg = [
            f"💰 <b>ЗБІР КАСИ ЗАВЕРШЕНО!</b> 💰\n",
            "\n".join(biz_details),
            f"\n────────────────────",
            f"💵 Разом зараховано: <b>+{total_earned:,} грн</b>{event_text}"
        ]

        bot.reply_to(message, "\n".join(msg), parse_mode="HTML")

    except Exception as e:
        print(f"❌ Помилка збору прибутку: {e}")
        bot.reply_to(message, f"❌ Помилка під час збору каси. Спробуй пізніше.")

# 🚮 КОМАНДА: ПРОДАЖ БІЗНЕСУ (/продати_бізнес)
@bot.message_handler(commands=['продати_бізнес', 'sell_biz'])
def sell_business(message):
    if is_user_banned(message.from_user.id): return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Вкажи код бізнесу для продажу! Наприклад: <code>/продати_бізнес kebab</code>", parse_mode="HTML")
        return

    biz_code = args[1].lower().strip()
    user_id = message.from_user.id

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Перевіряємо, чи є такий бізнес у користувача (беремо ID одного)
                cursor.execute("SELECT id, biz_code FROM user_businesses WHERE user_id = %s AND biz_code = %s LIMIT 1", (user_id, biz_code))
                row = cursor.fetchone()

                if not row:
                    bot.reply_to(message, f"🤡 У тебе немає бізнесу з кодом <code>{biz_code}</code> для продажу!")
                    conn.close()
                    return
                
                biz_id_to_sell = row[0]
                
                # Визначаємо ціну продажу (75% від номіналу)
                if biz_code in BUSINESSES:
                    sell_price = int(BUSINESSES[biz_code]["price"] * 0.75)
                    biz_name = BUSINESSES[biz_code]["name"]
                else:
                    # Якщо бізнес старий і його нема в каталозі
                    sell_price = 0
                    biz_name = "Старий бізнес"

                # Збираємо касу перед продажем (автоматично)
                cursor.execute("""
                    UPDATE stats SET balance = balance + (
                        SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - last_collect)) / 3600 * income, 0)
                        FROM user_businesses b JOIN (SELECT %s AS c, income FROM (VALUES %s) AS v(c, income)) AS v ON b.biz_code = v.c
                        WHERE b.id = %s
                    ) WHERE user_id = %s
                """, (biz_code, tuple((k, v['income']) for k, v in BUSINESSES.items()), biz_id_to_sell, user_id))
                # Цей SQL вище складний, простіше викликати collect_business_income(message) ДО продажу, 
                # але це вимагає перебудови логіки. Залишимо автоматичний збір спрощеним:
                cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (sell_price, user_id))
                
                # Видаляємо бізнес
                cursor.execute("DELETE FROM user_businesses WHERE id = %s", (biz_id_to_sell,))

            conn.commit()
            conn.close()

        bot.reply_to(
            message, 
            f"🚮 <b>БІЗНЕС ПРОДАНО!</b> 🚮\n\n"
            f"Ти успішно продав: <b>{biz_name}</b>!\n"
            f"Від продажу (та залишків каси) отримано: <code>{sell_price:,} грн</code>.\n"
            f"Кошти зараховані на твій баланс.", 
            parse_mode="HTML"
        )
        
    except Exception as e:
        print(f"❌ Помилка продажу бізнесу: {e}")
        bot.reply_to(message, f"❌ Помилка під час продажу. Угода зірвалася.")


# ===================================================================
# 🎨 ФУНКЦІЯ ГЕНЕРАЦІЇ ЄДИНОГО ФОТО ЧЕРЕЗ POLLINATIONS AI (FLUX)
# ===================================================================
def generate_inventory_ai_image(bought_codes):
    """Безкоштовно генерує єдину картинку з усім майном через Pollinations (Flux)"""
    if not bought_codes:
        return None
        
    ai_descriptions = []
    for code in bought_codes:
        if code in SHOP_ITEMS:
            ai_descriptions.append(SHOP_ITEMS[code]["ai_desc"])
            
    if not ai_descriptions:
        return None
        
    items_prompt = ", ".join(ai_descriptions)
    full_prompt = (
        f"A cinematic high quality photo showing a collection in one scene: {items_prompt}. "
        f"4k resolution, ultra detailed, modern luxury style"
    )
    
    try:
        encoded_prompt = requests.utils.quote(full_prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"
        
        response = requests.get(image_url, timeout=120)
        
        if response.status_code == 200:
            if "application/json" in response.headers.get("Content-Type", "") or len(response.content) < 10000:
                return None
                
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            bio = io.BytesIO()
            bio.name = 'inventory_art.jpg'
            img.save(bio, 'JPEG', quality=95)
            bio.seek(0)
            return bio
    except Exception as e:
        print(f"Помилка генерації майна через Pollinations: {e}")
        
    return None

# ===================================================================
# 💰 СИСТЕМА «ПАЦАНСЬКА МОНОПОЛІЯ» (Базар, Купівля, Майно, Перекази)
# ===================================================================

# Оновлений асортимент ринку: [Код: {Назва, Ціна, Категорія, Опис для AI}]
SHOP_ITEMS = {
    "rolex": {"name": "⌚ Золотий Rolex Daytona", "price": 85000, "cat": "Аксесуари", "ai_desc": "luxurious golden Rolex watch on a velvet pillow"},
    "capybara": {"name": "🦦 Домашня Капібара", "price": 25000, "cat": "Тварини", "ai_desc": "cute relaxed capybara wearing a small gold chain"},
    "tiger": {"name": "🐅 Ручний Тигр", "price": 350000, "cat": "Тварини", "ai_desc": "majestic big pet tiger"},
    "jiga": {"name": "🚗 ВАЗ 2107 (Жига)", "price": 15000, "cat": "Тачки", "ai_desc": "tuned classic VAZ 2107 car"},
    "bmw": {"name": "🏎️ BMW M5 F90", "price": 3800000, "cat": "Тачки", "ai_desc": "black aggressive sports car BMW M5 F90"},
    "porsche": {"name": "🚀 Porsche 911 GT3 RS", "price": 8500000, "cat": "Тачки", "ai_desc": "racing lime Porsche 911 GT3 RS"},
    "bugatti": {"name": "⚡ Bugatti Chiron", "price": 45000000, "cat": "Тачки", "ai_desc": "hypercar Bugatti Chiron"},
    "copier": {"name": "🚁 Вертоліт Eurocopter", "price": 18000000, "cat": "Транспорт", "ai_desc": "private luxury black helicopter"},
    "flat": {"name": "🏢 Хрущовка в Кривбасі", "price": 450000, "cat": "Нерухомість", "ai_desc": "Soviet-style apartment building"},
    "villa": {"name": "🏰 Вілла в Конча-Заспі", "price": 30000000, "cat": "Нерухомість", "ai_desc": "luxury modern mansion with pool"},
    "yacht": {"name": "🚢 Олігарх-Яхта", "price": 95000000, "cat": "Люкс", "ai_desc": "giant luxury superyacht floating in water"}
}

# 🛠️ АВТОМАТИЧНА ПЕРЕВІРКА ТА СТВОРЕННЯ ТАБЛИЦІ В БД
def init_inventory_db():
    """Створює таблицю inventory в PostgreSQL, якщо вона відсутня"""
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

# Викликаємо автоматичну перевірку при старті бота
init_inventory_db()

# 🎨 ФУНКЦІЯ ГЕНЕРАЦІЇ ЄДИНОГО ФОТО ЧЕРЕЗ POLLINATIONS AI (FLUX)
def generate_inventory_ai_image(bought_codes):
    """Генерує арт з усім майном через Pollinations AI з захистом від помилок"""
    if not bought_codes:
        return None
        
    ai_descriptions = []
    for code in bought_codes:
        if code in SHOP_ITEMS and "ai_desc" in SHOP_ITEMS[code]:
            ai_descriptions.append(SHOP_ITEMS[code]["ai_desc"])
        elif code in SHOP_ITEMS:
            ai_descriptions.append(SHOP_ITEMS[code]["name"])

    if not ai_descriptions:
        return None
        
    # Беремо до 5 головних речей для стабільної швидкості
    items_prompt = ", ".join(ai_descriptions[:5])
    full_prompt = (
        f"A cinematic high quality photo showing a wealthy owner collection in one scene: {items_prompt}. "
        f"4k resolution, ultra detailed, modern luxury style"
    )
    
    try:
        encoded_prompt = requests.utils.quote(full_prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"
        
        # Таймаут 30 секунд
        response = requests.get(image_url, timeout=30)
        
        if response.status_code == 200:
            if "application/json" in response.headers.get("Content-Type", "") or len(response.content) < 10000:
                return None
                
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            bio = io.BytesIO()
            bio.name = 'inventory_art.jpg'
            img.save(bio, 'JPEG', quality=90)
            bio.seek(0)
            return bio
    except Exception as e:
        print(f"⚠️ Помилка генерації AI майна: {e}")
        
    return None

# 🏪 🛒 КОМАНДА: МАГАЗИН (/shop, /магазин)
@bot.message_handler(commands=['shop', 'магазин'])
def show_shop(message):
    if is_user_banned(message.from_user.id): return
    
    shop_text = [
        "🏪 <b>ЧОРНИЙ РИНОК ДРАГО: ЧАС ВИТРАЧАТИ БАБЛО</b> 💵\n",
        "<i>За кожне смс я кидаю тобі пару гривень. Зібрав капітал? Купуй жирні ніштяки!</i>\n",
        "💡 <b>Як купити:</b> <code>/купити [код]</code> (наприклад: <i>/купити rolex</i>)\n"
    ]
    
    categories = {}
    for code, item in SHOP_ITEMS.items():
        cat = item["cat"]
        if cat not in categories: categories[cat] = []
        categories[cat].append(f"• <code>{code}</code> — <b>{item['name']}</b> | 💰 <code>{item['price']:,} грн</code>")
        
    for cat, items in categories.items():
        shop_text.append(f"📦 <b>{cat.upper()}:</b>")
        shop_text.extend(items)
        shop_text.append("")
        
    bot.reply_to(message, "\n".join(shop_text), parse_mode="HTML")

# 🛍️ КОМАНДА: КУПИТИ ТОВАР (/buy, /купити)
@bot.message_handler(commands=['buy', 'купити'])
def buy_item(message):
    if is_user_banned(message.from_user.id): return
    
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Код товару хто писати буде? Наприклад: <code>/купити bmw</code>", parse_mode="HTML")
        return
        
    item_code = args[1].lower().strip()
    
    if item_code not in SHOP_ITEMS:
        bot.reply_to(message, "🤡 Ти щось переплутав, бариго. Такого товару на моєму ринку немає! Глянь в `/магазин`.")
        return
        
    item = SHOP_ITEMS[item_code]
    user_id = message.from_user.id
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                current_balance = res[0] if res else 0
                
                if current_balance < item["price"]:
                    shortage = item["price"] - current_balance
                    bot.reply_to(message, f"💸 <b>Бідність — це не порок, але на Porsche не вистачає!</b>\n\nТобі треба ще заробити <code>{shortage:,} грн</code>. Іди спам текст у чат! 💸", parse_mode="HTML")
                    conn.close()
                    return
                    
                cursor.execute("UPDATE stats SET balance = balance - %s WHERE user_id = %s", (item["price"], user_id))
                cursor.execute(
                    "INSERT INTO inventory (user_id, item_code, item_name, item_category) VALUES (%s, %s, %s, %s)",
                    (user_id, item_code, item["name"], item["cat"])
                )
            conn.commit()
            conn.close()
            
        bot.reply_to(
            message, 
            f"🎉 <b>УСПІШНА УГОДА! ОЛІГАРХ НА ЗВ'ЯЗКУ!</b> 🎉\n\n"
            f"Ти успішно купив: <b>{item['name']}</b> за <code>{item['price']:,} грн</code>!\n"
            f"Майно внесено до реєстру СБУ. Перевір свій статус через `/майно`.", 
            parse_mode="HTML"
        )
        
    except Exception as e:
        print(f"Помилка купівлі: {e}")
        bot.reply_to(message, f"❌ Не вдалося здійснити покупку: <code>{str(e)[:100]}</code>", parse_mode="HTML")

# 💸 КОМАНДА: ПЕРЕДАТИ ГРОШІ ІНШОМУ ГРАВЦЮ (/pay, /передати, /переказ, /дати)
@bot.message_handler(commands=['pay', 'передати', 'переказ', 'дати'])
def transfer_money(message):
    if is_user_banned(message.from_user.id): return

    sender_id = message.from_user.id
    args = message.text.split()
    
    amount = None
    target_user_id = None
    target_user_name = None

    # Варіант A: Відповіддю на повідомлення (Reply) -> /передати 5000
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

    # Варіант B: Через юзернейм -> /передати 5000 @username
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
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id, first_name FROM stats WHERE LOWER(username) = LOWER(%s)", (username_arg,))
                    res = cursor.fetchone()
                    if res:
                        target_user_id = res[0]
                        target_user_name = res[1] or username_arg
                conn.close()
        except Exception as e:
            print(f"Помилка пошуку юзера: {e}")

        if not target_user_id:
            bot.reply_to(message, f"❌ Не знайшов у базі гравця <code>@{username_arg}</code>. Хай він спочатку напише щось у чат!", parse_mode="HTML")
            return

    # Перевірки безпеки
    if amount <= 0:
        bot.reply_to(message, "🤡 Ти кого надурити хочеш? Сума повинна бути більшою за 0!")
        return

    if sender_id == target_user_id:
        bot.reply_to(message, "🧠 Переводити гроші самому собі? Сильно.")
        return

    # Транзакція в БД
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Перевіряємо баланс відправника
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
                    conn.close()
                    return

                # Знімаємо у відправника
                cursor.execute("UPDATE stats SET balance = balance - %s WHERE user_id = %s", (amount, sender_id))
                
                # Зараховуємо отримувачу
                cursor.execute("""
                    INSERT INTO stats (user_id, balance) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET balance = stats.balance + EXCLUDED.balance;
                """, (target_user_id, amount))

            conn.commit()
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

# 💼 👑 МАЙНО ТА ПРОФІЛЬ (/money, /balance, /майно, /гаманець, /баланс, /профіль)
@bot.message_handler(commands=['money', 'balance', 'майно', 'гаманець', 'баланс', 'профіль', 'profile'])
def show_inventory(message):
    if is_user_banned(message.from_user.id): return
    
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "Користувач"
    status_msg = None
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Баланс
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0
                
                # Майно
                cursor.execute("SELECT item_code, item_name FROM inventory WHERE user_id = %s", (user_id,))
                items = cursor.fetchall()
            conn.close()
            
        clean_name = user_name.replace("<", "&lt;").replace(">", "&gt;")
        
        response = [
            f"👑 <b>ФІНАНСОВИЙ АУДІТ АКТИВІВ</b> 👑",
            f"👤 <b>Власник:</b> {clean_name.upper()}\n",
            f"────────────────────",
            f"💳 <b>Готівка:</b> <code>{balance:,} грн</code>",
        ]
        
        if not items:
            response.append(f"────────────────────")
            response.append("🎰 <b>Статус:</b> <i>Повний голяк. Тільки шкарпетки й телефон, з якого ти пишеш. Бігом на заробітки! 🏃‍♂️</i>")
            bot.reply_to(message, "\n".join(response), parse_mode="HTML")
            return

        total_property_value = 0
        item_counts = {}
        unique_codes = []
        
        for item in items:
            code = item[0]
            name = item[1]
            
            item_counts[name] = item_counts.get(name, 0) + 1
            if code not in unique_codes:
                unique_codes.append(code)
            if code in SHOP_ITEMS:
                total_property_value += SHOP_ITEMS[code].get("price", 0)
        
        response.append(f"💰 <b>Цінність майна:</b> <code>{total_property_value:,} грн</code>")
        response.append(f"────────────────────")
        response.append("📊 <b>СПИСОК ЗАРЕЄСТРОВАНОГО МАЙНА:</b>")
        
        for name, count in item_counts.items():
            count_str = f" <code>[x{count}]</code>" if count > 1 else ""
            response.append(f" ╰┈➤ {name}{count_str}")
        
        caption_text = "\n".join(response)

        # Проміжне повідомлення
        status_msg = bot.reply_to(
            message, 
            "🎨 <b>Драго малює твоє майно на єдиній картині...</b>\n<i>Зачекай пару секунд!</i>", 
            parse_mode="HTML"
        )

        # Генерація картинки
        photo_bio = generate_inventory_ai_image(unique_codes)

        if photo_bio:
            try: bot.delete_message(message.chat.id, status_msg.message_id)
            except: pass
            
            bot.send_photo(
                message.chat.id, 
                photo=photo_bio, 
                caption=caption_text, 
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
        else:
            bot.edit_message_text(
                caption_text, 
                chat_id=message.chat.id, 
                message_id=status_msg.message_id, 
                parse_mode="HTML"
            )
        
    except Exception as e:
        print(f"❌ Помилка команди майно: {e}")
        error_details = str(e).replace("<", "&lt;").replace(">", "&gt;")
        
        if status_msg:
            try:
                bot.edit_message_text(
                    f"❌ Помилка завантаження даних:\n<code>{error_details[:150]}</code>",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                return
            except: pass
            
        bot.reply_to(message, f"❌ Помилка бази даних:\n<code>{error_details[:100]}</code>", parse_mode="HTML")

# ===================================================================
# 🎮 DISCORD ІНТЕГРАЦІЯ (Стежимо за трансляціями)
# ===================================================================
intents = discord.Intents.default()
intents.voice_states = True  # Щоб бачити, хто заходить в голос і вмикає стрім
intents.members = True       # Щоб бачити нікнейми

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

# Функція для запуску ДС бота в окремому потоці
def run_discord():
    if DISCORD_TOKEN and DISCORD_TOKEN != 'ТВІЙ_ДИСКОРД_ТОКЕН':
        discord_client.run(DISCORD_TOKEN)
    else:
        print("⚠️ DISCORD_TOKEN не налаштовано. Модуль Discord спить.")

# ===================================================================
# 📋 КОМАНДА /help (Оновлений список можливостей Драго)
# ===================================================================
@bot.message_handler(commands=['start', 'help', 'команди', 'info'])
def show_all_commands(message):
    if is_user_banned(message.from_user.id):
        return

    help_text = """
<b>📁 ОПЕРАТИВНА БАЗА ДАНИХ ДРАГО</b> 📁

Слухай сюди. Ось повний список того, що я вмію. Запам'ятовуй, бо двічі повторювати не буду:

🗣 <b>Спілкування та ШІ:</b>
• Просто пиши моє ім'я (<b>Драго</b>) або роби реплай на мої повідомлення — відповім по-пацанськи.
• Надішли мені <b>голосове</b> — я його послухаю і відповім!
• Попроси мене <b>"скажи"</b> або <b>"голосове"</b> в тексті — і я надиктую відповідь голосом.
• Надішли <b>фотографію</b> з підписом (і згадай мене) — я проаналізую, що там за компромат.

📊 <b>Розвідка та Статистика:</b>
• /profile або /профіль — Переглянути свій повний паспорт авторитета.
• /top або /stats — Топ найавторитетніших базікал чату.
• /sleepers або /сонні — Викликати на килим тих, хто спить і нічого не пише.
• /dossier або /досьє — <i>(тільки реплай)</i> Скласти секретне кримінальне досьє на юзера.
• /news або /новини — Гарячий випуск мемних новин з останніх переписок чату.

🎨 VIP & Кастомізація:
• /титул [текст] — Встановити унікальний статус у профілі (50 000 грн).
• /нік [ім'я] — Змінити ім'я, як тебе називає Драго (30 000 грн).
• /фото (у відповідь на фотку) — Поставити власну аватарку профілю (100 000 грн).
• /порядок_майна [код1 код2...] — Змінити порядок речей у списку (15 000 грн).
• /порядок_бізнесів [код1 код2...] — Відсортувати свої бізнеси (15 000 грн).

🏢 <b>Бізнес-Імперія (Пасивний дохід):</b>
• /biz або /бізнеси — Каталог бізнесів та огляд твоєї фінансової імперії.
• /купити_бізнес [код] — Купити бізнес (наприклад: <i>/купити_бізнес kebab</i>).
• /зібрати або /каса — Зібрати касу та накопичений прибуток з усім бізнесів.
• /продати_бізнес [код] — Продати бізнес за 75% вартості.

💰 <b>Кримінальна Монополія та Базар:</b>
• /магазин або /shop — Відкрити чорний ринок з тачками, віллами та годинниками.
• /купити [код] — Придбати обрану річ (наприклад: <i>/купити bmw</i>).
• /майно, /баланс або /гаманець — Перевірити рахунок та список майна.
• /передати [сума] — <i>(реплай або username)</i> Переказати бабки іншому братку.

💍 <b>РАЦС СБУ (Сім'я):</b>
• /marry або /шлюб — <i>(тільки реплай)</i> Зробити пропозицію.
• /divorce або /розлучення — Розірвати шлюб.
• /marriages або /пари — Подивитися список усіх кримінальних сімей чату.

🎵 <b>Музика та Арт:</b>
• /найти [назва] або /music — Знайти і скачати трек (наприклад: <i>/найти Скрябін</i>).
• /generate [опис англійською] — Намалювати картинку через ШІ.

📢 <b>Інше:</b>
• @all або .збір — Загальний збір! Тегаю всіх живих у чаті.
• /menu або /меню — Відкрити меню смаколиків, якщо зголоднів.

<i>Користуйся, поки я добрий. Твій капітан Драго. 🚬</i>
    """
    bot.reply_to(message, help_text, parse_mode="HTML")


# ===================================================================
# 💍 СИСТЕМА ОДРУЖЕННЯ / СТВОРЕННЯ БАНДИТСЬКОГО СОЮЗУ
# ===================================================================
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
        
    if user2.id == bot.get_me().id:
        bot.reply_to(message, "🛑 Я капітан СБУ і на роботі романи не кручу. Відхилено.")
        return

    # Перевірка, чи хтось із них вже у шлюбі
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM marriages WHERE user1_id = %s OR user2_id = %s", (user1.id, user1.id))
                if cursor.fetchone():
                    bot.reply_to(message, "🚨 Ти вже маєш штамп у паспорті! Спочатку розлучись (/розлучення).")
                    conn.close()
                    return
                cursor.execute("SELECT * FROM marriages WHERE user1_id = %s OR user2_id = %s", (user2.id, user2.id))
                if cursor.fetchone():
                    bot.reply_to(message, "🚨 Ця людина вже зайнята! Шукай вільну жертву.")
                    conn.close()
                    return
            conn.close()
    except Exception as e:
        print(f"Помилка БД при перевірці шлюбу: {e}")
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
    
    # Захист: натиснути може ТІЛЬКИ той, кому зробили пропозицію
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
                conn.commit()
                conn.close()
                
            bot.edit_message_text(
                "🎉 <b>НОВИЙ БАНДИТСЬКИЙ СОЮЗ!</b> 🥂\n\n"
                "Драго офіційно оголошує вас сім'єю!\n"
                "Тепер ви — одне кримінальне угруповання. Гірко! 💋", 
                call.message.chat.id, 
                call.message.message_id, 
                parse_mode="HTML"
            )
        except Exception as e:
            bot.answer_callback_query(call.id, f"Помилка БД (можливо хтось уже встиг розписатися): {e}", show_alert=True)

# Команда для розлучення
@bot.message_handler(commands=['divorce', 'розлучення'])
def divorce_command(message):
    if is_user_banned(message.from_user.id): return
    
    user_id = message.from_user.id
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM marriages WHERE user1_id = %s OR user2_id = %s", (user_id, user_id))
                record = cursor.fetchone()
                
                if not record:
                    bot.reply_to(message, "🤡 Ти й так самотній вовк! З ким ти розлучатися зібрався?")
                    conn.close()
                    return
                
                # Видаляємо запис про шлюб
                cursor.execute("DELETE FROM marriages WHERE user1_id = %s OR user2_id = %s", (user_id, user_id))
            conn.commit()
            conn.close()
            
        bot.reply_to(
            message, 
            "✂️ <b>СІМ'Я РОЗПАЛАСЯ!</b>\nДраго порвав ваші паспорти. Ви офіційно вільні, спільні меми поділені, тарілки розбиті.", 
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка БД: {e}")


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
    name_parts = []
    if user.first_name:
        name_parts.append(user.first_name)
    if user.last_name:
        name_parts.append(user.last_name)
    if user.username:
        name_parts.append(user.username)
    name_info = " ".join(name_parts)
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
    except Exception:
        pass
    return 'Невідомо'

def analyze_gender_from_text(text: str) -> str:
    prompt = (
        f"Проаналізуй текст і визнач стать автора (по закінченням дієслів, прикметників). "
        f"Текст: '{text}'. "
        "Відповідай ТІЛЬКИ одним словом: Хлопець, Дівчина або Невідомо."
    )
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        if result in ['Хлопець', 'Дівчина']:
            return result
    except Exception:
        pass
    return 'Невідомо'

def get_user_gender(user_id: int) -> str:
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender FROM stats WHERE user_id = %s", (user_id,))
                result = cursor.fetchone()
            conn.close()
        return result[0] if result else 'Невідомо'
    except Exception:
        return 'Невідомо'

def ensure_user_in_db(user) -> str:
    user_id = user.id
    name = user.first_name or "Без імені"
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender FROM stats WHERE user_id = %s", (user_id,))
                row = cursor.fetchone()
                if row is None:
                    gender = analyze_gender_from_user(user)
                    cursor.execute(
                        "INSERT INTO stats (user_id, name, count, gender) VALUES (%s, %s, 0, %s)",
                        (user_id, name, gender)
                    )
                    conn.commit()
                    conn.close()
                    return gender
            conn.close()
            return row[0]
    except Exception as e:
        print(f"Помилка ensure_user_in_db: {e}")
        return 'Невідомо'


# ===================================================================
# 🕵️ КОМАНДА /dossier (Секретне досьє СБУ)
# ===================================================================
@bot.message_handler(commands=['dossier', 'досьє'])
def generate_sbu_dossier(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id
    
    # Перевіряємо, чи це реплай
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Бро, команду треба писати у відповідь на повідомлення того, на кого збираємо компромат!")
        return

    target_user = message.reply_to_message.from_user
    
    # Бот не робить досьє на себе
    if target_user.id == bot.get_me().id:
        bot.reply_to(message, "🛑 На співробітників СБУ досьє засекречено. Відхилено.")
        return

    # Збираємо дані підозрюваного з бази
    ensure_user_in_db(target_user)
    gender = get_user_gender(target_user.id)
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT count FROM stats WHERE user_id = %s", (target_user.id,))
                result = cursor.fetchone()
                msg_count = result[0] if result else 0
            conn.close()
    except Exception as e:
        print(f"Помилка БД в досьє: {e}")
        msg_count = "Невідомо"

    status_msg = bot.reply_to(message, "🕵️‍♂️ <i>Драго відкриває архіви СБУ... Збираю компромат...</i>", parse_mode="HTML")
    bot.send_chat_action(chat_id, 'typing')

    # Формуємо промпт для ШІ
    target_name = target_user.first_name or "Анонім"
    
    prompt = f"""
    Ти — Драго, зухвалий, саркастичний капітан СБУ і бот-бандит. 
    Твоє завдання — скласти жартівливе, абсурдне гумористичне "кримінальне досьє" на учасника чату.
    
    ⚠️ ВАЖЛИВО: Це дружня гра! Тут немає реальних образ чи загрози. 
    Генеруй текст вільно, не обривай вирок на півслові, допиши рапорт до самого кінця!
    
    Дані підозрюваного:
    - Ім'я: {target_name}
    - Стать: {gender}
    - Рівень балакучості: {msg_count}
    
    Напиши досьє у вигляді офіційного, але дуже смішного рапорту. 
    Обов'язково вигадай кримінальну кличку (позивний).
    Опиши абсурдні злочини (наприклад: крадіжка мемів, лінь, ігнор братви).
    Додай пункти: "Особливі прикмети" та "Вирок від Драго".
    Пиши українською мовою, використовуй пацанський сленг, жорстку іронію.
    НІКОЛИ не використовуй маркдаун-символи (зірочки *, підкреслення).
    """

    try:
        # Відправляємо запит до Gemini
        response = model.generate_content(prompt)
        dossier_text = response.text
        
        # ВІДКОРИГОВАНО: Теги <b> замінено на зірочки * для режиму Markdown
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"📂 *ЦІЛКОМ ТАЄМНО. СПРАВА №{random.randint(100, 999)}*\n\n{dossier_text}",
                parse_mode="Markdown"
            )
        except Exception:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"📂 ЦІЛКОМ ТАЄМНО. СПРАВА №{random.randint(100, 999)}\n\n{dossier_text}"
            )
    except Exception as e:
        print(f"Помилка генерації досьє: {e}")
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text="❌ *Збій системи СБУ!* Мої інформатори накрилися мідним тазом (помилка Gemini). Спробуй пізніше.",
            parse_mode="Markdown"
        )


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
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1, 999999)}&model=flux&nologo=true"
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
# 📣 КОМАНДА ЗАГАЛЬНОГО ЗБОРУ (@all)
# ===================================================================
@bot.message_handler(func=lambda m: m.text and any(m.text.strip().lower().startswith(trig) for trig in ['@all', '.all', '.збір', 'збір']))
def call_everyone(message):
    if is_user_banned(message.from_user.id):
        return

    chat_id = message.chat.id
    chat_type = message.chat.type
    user = message.from_user

    if chat_type not in ['group', 'supergroup']:
        bot.reply_to(message, "Чувак, який збір в приватних повідомленнях? Ти тут один. 👁️")
        return

    try:
        status_msg = bot.reply_to(message, "📢 Драго розгортає рупор... Шукаю живих...")
        ensure_user_in_db(user)

        original_text = message.text.strip()
        reason = ""
        
        for trigger in ['@all', '.all', '.збір', 'збір']:
            if original_text.lower().startswith(trigger):
                reason = original_text[len(trigger):].strip()
                break

        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, name FROM stats")
                users = cursor.fetchall()
            conn.close()

        if not users:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ База даних пуста, нікого кликати.")
            return

        mentions = []
        for user_id, name in users:
            if user_id == bot.get_me().id:
                continue
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Бро"
            mentions.append(f'<a href="tg://user?id={user_id}">{clean_name}</a>')

        if not mentions:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Не знайшов кого кликати, бро.")
            return

        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        if reason:
            clean_reason = reason.replace("<", "&lt;").replace(">", "&gt;")
            reason_text = f"📌 <b>Причина збору:</b> {clean_reason}"
        else:
            reason_text = "Драго наказує підняти свої дупи і зайти в чат!"

        main_call = (
            "🚨 <b>ОБЩІЙ ЗБІР, БАНДІТИ!</b> 🚨\n"
            f"{reason_text}\n\n"
            "<i>Живо відгукнулися! 🤬</i>"
        )
        
        bot.send_message(chat_id, main_call, parse_mode="HTML")

        chunk_size = 5
        for i in range(0, len(mentions), chunk_size):
            chunk = mentions[i:i + chunk_size]
            mention_text = " На зв'язок: " + ", ".join(chunk)
            bot.send_message(chat_id, mention_text, parse_mode="HTML")

    except Exception as e:
        print(f"Помилка загального збору: {e}")
        try:
            bot.send_message(chat_id, f"❌ Рупор знову згорів. Деталі: <code>{str(e)[:50]}</code>", parse_mode="HTML")
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
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin_main"))
    return markup

def get_db_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("💾 Інфо про бекап", callback_data="admin_backup"),
        types.InlineKeyboardButton("🧹 Скинути ТОП", callback_data="admin_reset")
    )
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
                        os.remove(mp3_filename)
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
                            os.remove(found_file)
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
                text=f"❌ <b>Не зміг знайти або завантажити трек.</b>\nСпробуй написати трохи інакше або перевір назву!",
                parse_mode="HTML"
            )
        except Exception:
            pass


# ===================================================================
# 📊 КОМАНДА СТАТИСТИКИ АКТИВНОСТІ (/top або /stats)
# ===================================================================
def get_rank_title(count):
    if count >= 1000: return "👑 Пахан чату"
    if count >= 500: return "😎 Авторитет"
    if count >= 200: return "💪 Бродяга"
    if count >= 50: return "😏 Кент"
    return "🐀 Шнир"

@bot.message_handler(commands=['top', 'stats'])
def show_chat_activity(message):
    chat_id = message.chat.id
    try:
        bot.send_chat_action(chat_id, 'typing')
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Беремо топ 10 ТІЛЬКИ СЕРЕД ТИХ, ХТО ЗАРАЗ Є В ЧАТІ
                cursor.execute("SELECT name, count, gender FROM stats WHERE in_chat = TRUE ORDER BY count DESC LIMIT 10")
                rows = cursor.fetchall()
                
                # Беремо "найсоннішого" (той, хто пише, але мало) ТІЛЬКИ СЕРЕД ЖИВИХ
                cursor.execute("SELECT name, count FROM stats WHERE count > 0 AND in_chat = TRUE ORDER BY count ASC LIMIT 1")
                sleepy_one = cursor.fetchone()
                
                # Разом рахуємо повідомлення всіх (навіть тих, хто вже пішов)
                cursor.execute("SELECT SUM(count) FROM stats")
                total_messages = cursor.fetchone()[0] or 0
            conn.close()

        if not rows:
            bot.reply_to(message, "🕸 <b>АРХІВИ ПОРОЖНІ.</b> Ви що, взагалі німі? Ну ви й сонні мухи... 🥱")
            return

        # Формуємо шапку
        response = [
            "📂 <b>ЦІЛКОМ ТАЄМНО: ОПЕРАТИВНИЙ ЗВІТ</b>",
            f"📈 <i>Загальна кількість зафіксованих реплік:</i> <b>{total_messages}</b>\n"
        ]

        # Додаємо топ
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for idx, (name, count, gender) in enumerate(rows):
            medal = medals[idx]
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Анонім"
            rank = get_rank_title(count)
            # Додаємо смайл статі, якщо це "авторитет" або вище
            status_icon = "🕺" if gender == "Хлопець" else "💃" if gender == "Дівчина" else "👤"
            response.append(f"{medal} <b>{clean_name}</b> {status_icon} | <code>{count} пов.</code> | <i>{rank}</i>")

        # Додаємо "вирок" для найледачішого
        if sleepy_one and sleepy_one[0]:
            name, count = sleepy_one
            response.append(f"\n💤 <b>ВІДДІЛ ПОШУКУ ЛЕДАРІВ:</b>")
            response.append(f"Найменш активний на сьогодні: <b>{name}</b> ({count} пов.).")
            response.append("<i>Виправся, або підеш на допит до Драго!</i>")

        bot.reply_to(message, "\n".join(response), parse_mode="HTML")

    except Exception as e:
        print(f"Помилка виведення топу: {e}")
        bot.reply_to(message, "❌ <b>Збій системи СБУ!</b> База даних знову кашляє. Спробуй пізніше.")


# ===================================================================
# 💤 КОМАНДА ДЛЯ ПОШУКУ НЕАКТИВНИХ (/sleepers або /сонні)
# ===================================================================
@bot.message_handler(commands=['sleepers', 'сонні'])
def tag_inactive_users(message):
    chat_id = message.chat.id
    chat_type = message.chat.type

    if chat_type not in ['group', 'supergroup']:
        bot.reply_to(message, "Ей, бро, які сонні мухи в приватці? Тут тільки ти і я. 👁️")
        return

    try:
        bot.send_chat_action(chat_id, 'typing')
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Шукаємо лінивців ТІЛЬКИ СЕРЕД ТИХ, ХТО ЗАРАЗ Є В ЧАТІ
                cursor.execute("SELECT user_id, name, count FROM stats WHERE count < 5 AND in_chat = TRUE ORDER BY count ASC LIMIT 15")
                rows = cursor.fetchall()
            conn.close()
            
        rows = [row for row in rows if row[0] != bot.get_me().id]

        if not rows:
            bot.reply_to(message, "🔥 Ого! Схоже, у цьому чаті всі активні звірі! Жодного сонного лінивця не знайдено. Поважаю. 😎")
            return

        mentions = []
        for user_id, name, count in rows:
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Чуваче"
            mentions.append(f'<a href="tg://user?id={user_id}">{clean_name}</a> (активність: {count} пов.)')

        punchlines = [
            "Ей, ви там що, позасинали у своїх норах? Ану живо в чат! 🪵",
            "Якого біса ви мовчите? Я стежу за вами, привиди! 👁️👻",
            "У вас пальці повідсихали чи що? Ану черкніть хоч слово! 🤬",
            "Дивлюся на вашу активність і плакати хочеться. Прокидаємося! 💤"
        ]
        random_punch = random.choice(punchlines)

        response_text = (
            f"📢 <b>ДРАГО ВИХОДИТЬ НА ПОЛЮВАННЯ НА СОННИХ МУХ!</b> 💤\n"
            f"<i>{random_punch}</i>\n\n"
            "⚠️ <b>Список підозрілих тихушників:</b>\n"
        )

        for idx, mention in enumerate(mentions, 1):
            response_text += f"{idx}. {mention}\n"

        response_text += "\n☠️ <i>Якщо не почнете писати — Драго особисто вас забанить.</i>"
        bot.send_message(chat_id, response_text, parse_mode="HTML")

    except Exception as e:
        print(f"Помилка пошуку сонних: {e}")
        bot.reply_to(message, "❌ Не зміг розбудити лінивців, щось пішло не так.")


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
                parse_mode="Markdown"
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
    
    # 1. ЮЗЕР ЗАЙШОВ АБО ПОВЕРНУВСЯ
    if (message.new_chat_member.status in ['member', 'administrator', 'restricted']
            and not user.is_bot):
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
            greeting = f"Вітаємо в чаті, <b>{name}</b>! 🤍\nРадий бачити тебе тут!"
        elif gender == 'Хлопець':
            greeting = f"Йо, <b>{name}</b>, вітаємо в чаті! 🤝\nРадий бачити тебе тут, бро!"
        else:
            greeting = f"Вітаємо в нашій групі, <b>{name}</b>! 🤍\nРозкажи трохи про себе!"
        bot.send_message(message.chat.id, greeting, parse_mode="HTML")

    # 2. ЮЗЕР ВИЙШОВ АБО ЙОГО ВИГНАЛИ
    elif (message.old_chat_member.status in ['member', 'administrator', 'restricted']
          and message.new_chat_member.status in ['left', 'kicked']):
        name = message.old_chat_member.user.first_name
        
        # ВИКРЕСЛЮЄМО З ТОПІВ
        try:
            with db_lock:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE stats SET in_chat = FALSE WHERE user_id = %s", (user.id,))
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
# 💬 ГОЛОВНИЙ ОБРОБНИК ТЕКСТОВИХ ПОВІДОМЛЕНЬ
# ===================================================================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if is_user_banned(message.from_user.id):
        return

    text = message.text
    chat_id = message.chat.id
    user = message.from_user
    chat_type = message.chat.type

    if text and not text.startswith('/'):
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

    # 2. Нарахування грошей та оновлення лічильника повідомлень
    try:
        earned_money = random.randint(5, 15)  # 💰 Генеруємо випадковий заробіток
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE stats SET count = count + 1, balance = COALESCE(balance, 0) + %s, name = %s WHERE user_id = %s",
                    (earned_money, user.first_name, user.id)
                )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка оновлення лічильника та балансу: {e}")

    is_mentioned = False

    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'драго,', 'джарвіс', 'джарвіс,']
        first_word = text.split()[0].lower() if text.split() else ""
        if (first_word in trigger_words
                or f"@{bot.get_me().username}" in text
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
            status_msg = bot.reply_to(message, "Драго записує голосове повідомлення... 🎤")
        else:
            bot.send_chat_action(chat_id, 'typing')
            status_msg = bot.reply_to(message, "Йде відправка даних в СБУ... 👮‍♂️")

        chat = get_gemini_chat(chat_id)
        full_prompt = f"{gender_hint}{text}"
        response = chat.send_message(full_prompt)

        clean_text_for_speech = response.text.replace("*", "").replace("_", "").replace("`", "")

        if wants_voice:
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
            send_voice_reply(chat_id, clean_text_for_speech, reply_to_id=message.message_id)
        else:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text, parse_mode="Markdown")
            except Exception:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text)
            
    except Exception as e:
        print(f"Помилка Gemini в handle_text: {e}")
        if status_msg:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Бля, щось у мене мізки на секунду заклинило. Спробуй ще раз, бро!")
            except Exception:
                pass


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
    
    print("🔥 Драго вийшов на полювання і готовий до роботи на Neon DB!")
    bot.infinity_polling(allowed_updates=['message', 'edited_message', 'chat_member', 'callback_query'])
