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
import urllib.request
import urllib.parse
import re



DATABASE_URL = os.environ.get('DATABASE_URL')

# Підключаємося до БД
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# 🔒 ЛОК ДЛЯ БЕЗПЕКИ ПОТОКІВ
db_lock = threading.Lock()

def html_escape(text):
    """Допоміжна функція для безпечного екранування спецсимволів у HTML"""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# Створення та авто-оновлення таблиць при запуску
try:
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 1. Таблиця статистики
            cursor.execute("""CREATE TABLE IF NOT EXISTS stats (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                count INTEGER DEFAULT 0,
                gender TEXT,
                in_chat BOOLEAN DEFAULT TRUE,
                balance BIGINT DEFAULT 0
            )""")
            
            # 🛠 ДОДАЄМО ВСІ НЕОБХІДНІ КОЛОНКИ ДЛЯ КАСТОМІЗАЦІЇ ТА ПРОФІЛЮ
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS balance BIGINT DEFAULT 0;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS username VARCHAR(100) DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_nick VARCHAR(50) DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_title VARCHAR(50) DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS custom_photo TEXT DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS inventory_order TEXT DEFAULT NULL;")
            cursor.execute("ALTER TABLE stats ADD COLUMN IF NOT EXISTS biz_order TEXT DEFAULT NULL;")
            
            # 2. Таблиця майна (Монополія)
            cursor.execute("""CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                item_code TEXT,
                item_name TEXT,
                item_category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            
            # 3. Таблиця бізнесів
            cursor.execute("""CREATE TABLE IF NOT EXISTS user_businesses (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                biz_code VARCHAR(50) NOT NULL,
                last_collect TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

            # 6. Таблиця сімейного банку ( shared_wallets )
            cursor.execute("""CREATE TABLE IF NOT EXISTS shared_wallets (
                pair_id VARCHAR(100) PRIMARY KEY,
                balance BIGINT DEFAULT 0
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
# ======================================================

DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'ТВІЙ_ДИСКОРД_ТОКЕН')
TELEGRAM_CHAT_ID = -1003428241218  # ID вашого Телеграм-чату


bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

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

# Пам'ять для гри Мафія
mafia_games = {}

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


# -------------------------------------------------------------------
# 🛠 1. ДОПОМІЖНІ ФУНКЦІЇ БАЗИ ДАНИХ ТА ПЕРЕВІРОК
# -------------------------------------------------------------------

def get_user_balance(user_id):
    """Отримує поточний баланс користувача з бази"""
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

def update_user_balance(user_id, amount):
    """Змінює баланс користувача на вказану суму (може бути + або -)"""
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

def get_rank_title(count):
    """Визначає ранг користувача за кількістю повідомлень"""
    if count >= 10000: return "Бог Чату ⚡"
    if count >= 5000: return "Легенда СБУ 👑"
    if count >= 2500: return "Авторитет 🚬"
    if count >= 1000: return "Місцева Легенда 👑"
    if count >= 500: return "Завзятий Дописувач 🔥"
    if count >= 100: return "Чатер 💬"
    if count >= 20: return "Місцевий 🚶"
    return "Новачок 🐣"

def safe_get_rank(msg_count):
    return get_rank_title(msg_count)

def ensure_user_in_db(user) -> str:
    """Гарантує, що користувач є в базі stats та заповнює його дані"""
    if not user:
        return 'Невідомо'
    user_id = user.id
    name = user.first_name or "Без імені"
    username = user.username
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender FROM stats WHERE user_id = %s", (user_id,))
                row = cursor.fetchone()
                if row is None:
                    gender = analyze_gender_from_user(user)
                    cursor.execute(
                        "INSERT INTO stats (user_id, name, count, gender, username, balance) VALUES (%s, %s, 0, %s, %s, 0)",
                        (user_id, name, gender, username)
                    )
                    conn.commit()
                    conn.close()
                    return gender
                else:
                    cursor.execute("UPDATE stats SET name = %s, username = %s WHERE user_id = %s", (name, username, user_id))
                    conn.commit()
                    conn.close()
                    return row[0]
    except Exception as e:
        print(f"Помилка ensure_user_in_db: {e}")
        return 'Невідомо'


# -------------------------------------------------------------------
# 🪪 2. ВІДОБРАЖЕННЯ ПРОФІЛЮ
# -------------------------------------------------------------------

@bot.message_handler(regexp=r'^[/#!]?(?:профіль|profile)(?:\s+|$)')
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
                # 1. Дані користувача з теми
                cursor.execute("""
                    SELECT count, balance, gender 
                    FROM stats WHERE user_id = %s
                """, (user.id,))
                stats_res = cursor.fetchone()
                
                # 2. Інвентар
                cursor.execute("SELECT item_code, item_name FROM inventory WHERE user_id = %s", (user.id,))
                inventory_res = cursor.fetchall()

                # 3. Бізнеси
                cursor.execute("SELECT biz_code FROM user_businesses WHERE user_id = %s", (user.id,))
                biz_res = cursor.fetchall()
                
                # 4. Шлюб
                cursor.execute("""
                    SELECT s1.name, s2.name, m.user1_id, m.user2_id 
                    FROM marriages m
                    JOIN stats s1 ON m.user1_id = s1.user_id
                    JOIN stats s2 ON m.user2_id = s2.user_id
                    WHERE m.user1_id = %s OR m.user2_id = %s
                """, (user.id, user.id))
                marriage_res = cursor.fetchone()
                
            conn.close()

        # Розпаковка даних
        msg_count = stats_res[0] if stats_res and stats_res[0] else 0
        balance = stats_res[1] if stats_res and stats_res[1] else 0
        gender = stats_res[2] if stats_res and stats_res[2] else "Невідомо"

        # Ім'я та ранг
        clean_name = html_escape(user.first_name)
        rank = safe_get_rank(msg_count)
        gender_icon = "🕺" if gender == "Хлопець" else "💃" if gender == "Дівчина" else "👤"

        # Словники-заглушки
        biz_dict = globals().get('BUSINESSES', {})
        shop_dict = globals().get('SHOP_ITEMS', {})

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
            biz_text = ", ".join(biz_list)
            if len(biz_text) > 100: biz_text = biz_text[:95] + "..."

        # 📦 Підрахунок МАЙНА
        total_property_value = 0
        item_counts = {}
        item_names_map = {}

        for code, name in inventory_res:
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
            property_text = ", ".join(property_list)
            if len(property_text) > 100: property_text = property_text[:95] + "..."

        # 💍 Шлюб
        if marriage_res:
            name1, name2, u1_id, u2_id = marriage_res
            spouse_name = name2 if user.id == u1_id else name1
            marriage_status = f"💍 У шлюбі з <b>{html_escape(spouse_name)}</b>"
        else:
            marriage_status = "🐺 Статус: <i>Самотній вовк</i>"

        # Загальний капітал
        total_net_worth = balance + total_property_value + total_biz_value

        # 📜 Формування картки
        profile_card = (
            f"🪪 <b>ПАСПОРТ АВТОРИТЕТА: {clean_name.upper()}</b>\n"
            f"───────────────────────\n"
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

        # 📸 Стандартна аватарка з профілю Telegram
        final_photo = None
        try:
            photos = bot.get_user_profile_photos(user.id, limit=1)
            if photos and photos.total_count > 0:
                final_photo = photos.photos[0][-1].file_id
        except Exception:
            pass
        
        if not final_photo:
            final_photo = "https://i.ibb.co/5G1v5f2/no-avatar.jpg"

        # Відправка
        bot.send_photo(
            chat_id, 
            photo=final_photo, 
            caption=profile_card, 
            parse_mode="HTML", 
            reply_to_message_id=message.message_id
        )

    except Exception as e:
        print(f"Помилка створення профілю: {e}")
        bot.reply_to(message, f"❌ Помилка завантаження профілю: <code>{e}</code>", parse_mode="HTML")

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

# 🎨 ФУНКЦІЯ ГЕНЕРАЦІЇ ЄДИНОГО ФОТО БІЗНЕСІВ ЧЕРЕЗ AI (Pollinations)
def generate_business_ai_image(owned_biz_codes):
    """Генерує одну суцільну картинку, що показує всі бізнеси користувача разом"""
    if not owned_biz_codes:
        return None
        
    unique_codes = list(set(owned_biz_codes))
    ai_descriptions = []
    
    for code in unique_codes[:4]:
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
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1, 9999)}&model=flux&nologo=true"
        
        response = requests.get(image_url, timeout=30)
        
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

# ===================================================================
# 🏢 КОМАНДИ БОТА (БІЗНЕСИ)
# ===================================================================

@bot.message_handler(commands=['biz', 'бізнеси', 'бизнесы'])
def show_businesses(message):
    if is_user_banned(message.from_user.id): return

    user_id = message.from_user.id
    user_name = message.from_user.first_name

    status_msg = bot.reply_to(message, "⏳ <i>Зачекай, Драго підраховує твої активи та малює картинку імперії...</i>", parse_mode="HTML")

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT biz_code FROM user_businesses WHERE user_id = %s", (user_id,))
                owned_rows = cursor.fetchall()
                
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0
            conn.close()

        owned_codes = [row[0] for row in owned_rows]
        
        text = [
            f"👑 <b>БІЗНЕС-ІМПЕРІЯ:</b> {html_escape(user_name)}\n",
            f"💳 Баланс: <code>{balance:,} грн</code>\n",
            "────────────────────",
            "📊 <b>КАТАЛОГ ТА ТВОЇ АКТИВИ:</b>\n"
        ]

        total_income_per_hour = 0
        
        for code, biz in BUSINESSES.items():
            count = owned_codes.count(code)
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

        if owned_codes:
            photo = generate_business_ai_image(owned_codes)
            if photo:
                bot.delete_message(message.chat.id, status_msg.message_id)
                bot.send_photo(message.chat.id, photo=photo, caption=caption_text, parse_mode="HTML")
                return

        bot.edit_message_text(caption_text, message.chat.id, status_msg.message_id, parse_mode="HTML")

    except Exception as e:
        print(f"❌ Помилка команди /бізнеси: {e}")
        bot.edit_message_text(f"❌ Не вдалося завантажити дані про бізнес. Помилка БД.", message.chat.id, status_msg.message_id)

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
                biz_details = []
                time_limit_hours = 24.0

                for row in user_bizs:
                    biz_id, biz_code, hours_passed = row[0], row[1], row[2]
                    
                    if biz_code in BUSINESSES:
                        biz = BUSINESSES[biz_code]
                        effective_hours = min(hours_passed or 0, time_limit_hours)
                        
                        if effective_hours >= 0.016: 
                            earned = int(effective_hours * biz["income"])
                            total_earned += earned
                            
                            time_str = f"{int(effective_hours)}г {int((effective_hours%1)*60)}хв"
                            biz_details.append(f"• <b>{biz['name']}</b> (за {time_str}): <code>+{earned:,} грн</code>")

                if total_earned <= 0:
                    bot.reply_to(message, "⏳ <b>Каса ще порожня!</b> Бізнеси працюють, але прибуток ще не накопичився (зачекай хоча б хвилину).")
                    conn.close()
                    return

                event_text = ""
                reputation_bonus = min(len(user_bizs) // 2, 10)
                rand_event = random.randint(1, 100) + reputation_bonus
                
                if rand_event <= 8:
                    penalty = int(total_earned * 0.15)
                    total_earned -= penalty
                    event_text = f"\n\n🚨 <b>ПОДАТКОВА ПЕРЕВІРКА:</b> Прийшов штраф або хабар на <code>-{penalty:,} грн</code>! Відкупився..."
                elif rand_event >= 92:
                    bonus = int(total_earned * 0.30)
                    total_earned += bonus
                    event_text = f"\n\n🔥 <b>БЕШЕНИЙ ПОПИТ:</b> Наплив клієнтів приніс додаткові <code>+{bonus:,} грн</code>! Всі задоволені."
                elif rand_event == 50:
                    total_earned = 0
                    event_text = f"\n\n🥷 <b>РЕЙДЕРСЬКА АТАКА!</b> Касу намагалися віджати. Гроші довелося заховати, прибутку за цей період немає."

                cursor.execute("UPDATE user_businesses SET last_collect = NOW() WHERE user_id = %s", (user_id,))
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
                cursor.execute("SELECT id, biz_code, EXTRACT(EPOCH FROM (NOW() - last_collect)) / 3600 FROM user_businesses WHERE user_id = %s AND biz_code = %s LIMIT 1", (user_id, biz_code))
                row = cursor.fetchone()

                if not row:
                    bot.reply_to(message, f"🤡 У тебе немає бізнесу з кодом <code>{html_escape(biz_code)}</code> для продажу!", parse_mode="HTML")
                    conn.close()
                    return
                
                biz_id_to_sell, b_code, hours_passed = row[0], row[1], row[2]
                
                if biz_code in BUSINESSES:
                    sell_price = int(BUSINESSES[biz_code]["price"] * 0.75)
                    biz_name = BUSINESSES[biz_code]["name"]
                    income_rate = BUSINESSES[biz_code]["income"]
                else:
                    sell_price = 0
                    biz_name = "Старий бізнес"
                    income_rate = 0

                effective_hours = min(max(hours_passed or 0, 0), 24.0)
                uncollected_income = int(effective_hours * income_rate)
                total_payout = sell_price + uncollected_income

                cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (total_payout, user_id))
                cursor.execute("DELETE FROM user_businesses WHERE id = %s", (biz_id_to_sell,))

            conn.commit()
            conn.close()

        bot.reply_to(
            message, 
            f"🚮 <b>БІЗНЕС ПРОДАНО!</b> 🚮\n\n"
            f"Ти успішно продав: <b>{biz_name}</b>!\n"
            f"Від продажу (та залишків каси) отримано: <code>{total_payout:,} грн</code>.\n"
            f"Кошти зараховані на твій баланс.", 
            parse_mode="HTML"
        )
        
    except Exception as e:
        print(f"❌ Помилка продажу бізнесу: {e}")
        bot.reply_to(message, f"❌ Помилка під час продажу. Угода зірвалася.")


# ===================================================================
# 💰 СИСТЕМА «ПАЦАНСЬКА МОНОПОЛІЯ» (Базар, Купівля, Майно, Перекази)
# ===================================================================

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

def generate_inventory_ai_image(bought_codes):
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
        
    items_prompt = ", ".join(ai_descriptions[:5])
    full_prompt = (
        f"A cinematic high quality photo showing a wealthy owner collection in one scene: {items_prompt}. "
        f"4k resolution, ultra detailed, modern luxury style"
    )
    
    try:
        encoded_prompt = requests.utils.quote(full_prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"
        
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
                    (user_id, item_code, item['name'], item['cat'])
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
            bot.reply_to(message, "⚠️ Вкажи суму переказу! Наприклад: <code>/передати 5000</code>", parse_mode="HTML")
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
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id, name FROM stats WHERE LOWER(username) = LOWER(%s)", (username_arg,))
                    res = cursor.fetchone()
                    if res:
                        target_user_id = res[0]
                        target_user_name = res[1] or username_arg
                conn.close()
        except Exception as e:
            print(f"Помилка пошуку юзера: {e}")

        if not target_user_id:
            bot.reply_to(message, f"❌ Не знайшов у базі гравця <code>@{html_escape(username_arg)}</code>. Хай він спочатку напише щось у чат!", parse_mode="HTML")
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
                    conn.close()
                    return

                cursor.execute("UPDATE stats SET balance = balance - %s WHERE user_id = %s", (amount, sender_id))
                cursor.execute("""
                    INSERT INTO stats (user_id, balance) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET balance = stats.balance + EXCLUDED.balance;
                """, (target_user_id, amount))

            conn.commit()
            conn.close()

        clean_sender = html_escape(message.from_user.first_name)
        clean_target = html_escape(target_user_name)

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

@bot.message_handler(commands=['money', 'balance', 'майно', 'гаманець', 'баланс'])
def show_inventory(message):
    if is_user_banned(message.from_user.id): return
    
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "Користувач"
    status_msg = None
    
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                balance = res[0] if res else 0
                
                cursor.execute("SELECT item_code, item_name FROM inventory WHERE user_id = %s", (user_id,))
                items = cursor.fetchall()
            conn.close()
            
        clean_name = html_escape(user_name)
        
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

        status_msg = bot.reply_to(
            message, 
            "🎨 <b>Драго малює твоє майно на єдиній картині...</b>\n<i>Зачекай пару секунд!</i>", 
            parse_mode="HTML"
        )

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
        error_details = html_escape(str(e))
        
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
intents.voice_states = True  
intents.members = True       

discord_client = discord.Client(intents=intents)

@discord_client.event
async def on_ready():
    print(f"🤖 Discord-агент Драго успішно підключився як {discord_client.user}!")

@discord_client.event
async def on_voice_state_update(member, before, after):
    if not before.self_stream and after.self_stream:
        user_name = member.display_name
        channel_name = after.channel.name if after.channel else "Голосовий канал"
        
        guild_id = member.guild.id
        channel_id = after.channel.id if after.channel else 0
        discord_stream_url = f"https://discord.com/channels/{guild_id}/{channel_id}"
        
        announcement = (
            f"🎮 <b>ДРАГО ПАЛИТЬ КОНТОРУ В DISCORD!</b> 🚨\n\n"
            f"Чувак <b>{html_escape(user_name)}</b> не схотів сидіти тихо і запустив <b>живу трансляцію</b> "
            f"у голосовому каналі <i>«{html_escape(channel_name)}»</i>!\n\n"
            f"🚀 <b><a href='{discord_stream_url}'>👉 ЗАЛЕТІТИ НА СТРІМ 👈</a></b>\n\n"
            f"🍿 <i>Шоу почалося, бандити! Тисніть на лінк вище і залітайте!</i>"
        )
        
        try:
            bot.send_message(TELEGRAM_CHAT_ID, announcement, parse_mode="HTML")
        except Exception as e:
            print(f"Помилка відправки анонсу стріму в ТГ: {e}")

def run_discord():
    if DISCORD_TOKEN and DISCORD_TOKEN != 'ТВІЙ_ДИСКОРД_ТОКЕН':
        discord_client.run(DISCORD_TOKEN)
    else:
        print("⚠️ DISCORD_TOKEN не налаштовано. Модуль Discord спить.")

# ===================================================================
# 📋 КОМАНДА /help
# ===================================================================
@bot.message_handler(commands=['start', 'help', 'команди', 'info'])
def show_all_commands(message):
    if is_user_banned(message.from_user.id):
        return

    help_text = """
<b>📁 ОПЕРАТИВНА БАЗА ДАНИХ ДРАГО</b> 📁

Слухай сюди. Ось повний список того, що я вмію:

🗣 <b>Спілкування та ШІ:</b>
• Просто пиши моє ім'я (<b>Драго</b>) або роби реплай — відповім по-пацанськи.
• Надішли мені <b>голосове</b> — я його послухаю і відповім!
• Попроси мене <b>"скажи"</b> або <b>"голосове"</b> в тексті — і я надиктую відповідь голосом.

📊 <b>Розвідка та Статистика:</b>
• /profile або /профіль — Переглянути свій паспорт авторитета.
• /top або /stats — Топ найавторитетніших чатерів.
• /sleepers або /сонні — Викликати на килим тих, хто спить.
• /dossier або /досьє — <i>(реплай)</i> Скласти секретне досьє СБУ на юзера.
• /news або /новини — Гарячий випуск мемних новин з переписок чату.

🎰 <b>АЗАРТНІ ІГРИ ТА РОЗВАГИ:</b>
• /mafia або /мафія — Зібрати братву на гру в Мафію

💍 <b>ШЛЮБИ ТА СІМ'Я:</b>
• /одруження або /шлюб — Зробити пропозицію (у відповідь/реплай)
• /подарувати [сума] — Подарувати гроші партнерці/партнеру
• /спільний_баланс — Переглянути сімейний банк
• /поповнити_банк [сума] — Закинути гроші в сімейний сейф
• /розлучення — Розлучитися та поділити банк 50/50
• /marriages або /пари — Список усіх кримінальних сімей чату.

🏢 <b>Бізнес-Імперія (Пасивний дохід):</b>
• /biz або /бізнеси — Каталог бізнесів та огляд імперії.
• /купити_бізнес [код] — Купити бізнес (наприклад: <i>/купити_бізнес kebab</i>).
• /зібрати або /каса — Зібрати прибуток з усіх бізнесів.
• /продати_бізнес [код] — Продати бізнес за 75% вартості.

💰 <b>Кримінальна Монополія та Базар:</b>
• /магазин або /shop — Чорний ринок тачок, вілл, годинників.
• /купити [код] — Придбати обрану річ (наприклад: <i>/купити bmw</i>).
• /майно, /баланс або /гаманець — Перевірити рахунок та майно.
• /передати [сума] — <i>(реплай або @username)</i> Переказати бабки.

🎵 <b>Музика та Арт:</b>
• /найти [назва] — Знайти і скачати трек.
• /generate [опис англійською] — Намалювати картинку через ШІ.

📢 <b>Інше:</b>
• @all або .збір — Загальний збір! Тегаю всіх живих у чаті.
• /menu або /меню — Меню смаколиків.

<i>Користуйся, поки я добрий. Твій капітан Драго. 🚬</i>
    """
    bot.reply_to(message, help_text, parse_mode="HTML")


# ===================================================================
# 💍 СИСТЕМА ОДРУЖЕННЯ ТА СІМЕЙНОГО БЮДЖЕТУ
# ===================================================================

def get_marriage_pair(user_id):
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user1_id, user2_id FROM marriages WHERE user1_id = %s OR user2_id = %s", (user_id, user_id))
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

    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_yes = types.InlineKeyboardButton("💍 ТАК, Я ЗГОДЕН(НА)", callback_data=f"marry_yes_{user1.id}_{user2.id}")
    btn_no = types.InlineKeyboardButton("❌ НІ, ПІШОВ ТИ", callback_data=f"marry_no_{user1.id}_{user2.id}")
    markup.add(btn_yes, btn_no)
    
    target_name = html_escape(user2.first_name)
    initiator_name = html_escape(user1.first_name)
    
    bot.send_message(
        message.chat.id, 
        f"💒 <b>ОФІЦІЙНА ЗАЯВА В РАЦС СБУ!</b>\n\n"
        f"Громадянин(ка) <b>{initiator_name}</b> робить пропозицію <b>{target_name}</b>!\n"
        f"Що скажеш? Твоя відповідь вирішить вашу долю.", 
        reply_markup=markup, 
        parse_mode="HTML"
    )

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
                    cursor.execute("INSERT INTO marriages (user1_id, user2_id) VALUES (%s, %s)", (user1_id, user2_id))
                    pair_key = f"{min(user1_id, user2_id)}_{max(user1_id, user2_id)}"
                    cursor.execute("INSERT INTO shared_wallets (pair_id, balance) VALUES (%s, 0) ON CONFLICT DO NOTHING", (pair_key,))
                    conn.commit()
                conn.close()
                
            bot.edit_message_text(
                "🎉 <b>НОВИЙ БАНДИТСЬКИЙ СОЮЗ!</b> 🥂\n\n"
                "Драго офіційно оголошує вас сім'єю!\n"
                "Тепер ви — одне кримінальне угруповання. Гірко! 💋\n\n"
                "💡 <i>Вам доступний сімейний банк: <code>/спільний_баланс</code> та <code>/поповнити_банк [сума]</code></i>", 
                call.message.chat.id, 
                call.message.message_id, 
                parse_mode="HTML"
            )
        except Exception as e:
            bot.answer_callback_query(call.id, f"Помилка БД: {e}", show_alert=True)

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
        bot.reply_to(message, "❌ Сума має бути числом!")
        return

    if amount <= 0: return

    if get_user_balance(user_id) < amount:
        bot.reply_to(message, "💸 У тебе немає стільки грошей!")
        return

    update_user_balance(user_id, -amount)
    update_user_balance(spouse_id, amount)

    bot.reply_to(message, f"🎁 <b>Романтика!</b> Ти подарував своїй другій половинці <b>{amount:,} грн</b>! ❤️", parse_mode="HTML")

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
                    bank_bal = res[0]
            conn.close()

        bot.reply_to(
            message, 
            f"👩‍❤️‍👨 <b>СІМЕЙНИЙ СЕЙФ</b>\n"
            f"───────────────────────\n"
            f"💰 У банку пари лежить: <b>{bank_bal:,} грн</b>\n\n"
            f"📥 <i>Закинути гроші: <code>/поповнити_банк [сума]</code></i>", 
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка БД: {e}")

@bot.message_handler(commands=['add_family_bank', 'поповнити_банк'])
def add_family_bank(message):
    if is_user_banned(message.from_user.id): return
    user_id = message.from_user.id
    spouse_id, pair_key = get_marriage_pair(user_id)

    if not spouse_id:
        bot.reply_to(message, "💔 Спочатку знайди собі пару!")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❌ <b>Формат:</b> <code>/поповнити_банк 50000</code>", parse_mode="HTML")
        return

    try:
        amount = int(args[1])
    except ValueError:
        bot.reply_to(message, "❌ Сума має бути числом!")
        return

    if amount <= 0: return

    if get_user_balance(user_id) < amount:
        bot.reply_to(message, "💸 Брак коштів на гаманці!")
        return

    update_user_balance(user_id, -amount)
    
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("UPDATE shared_wallets SET balance = balance + %s WHERE pair_id = %s", (amount, pair_key))
            conn.commit()
        conn.close()

    bot.reply_to(message, f"🏦 Ти закинув <b>{amount:,} грн</b> у сімейний банк!", parse_mode="HTML")

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
                    if res: shared_money = res[0]

                cursor.execute("DELETE FROM marriages WHERE user1_id = %s OR user2_id = %s", (user_id, user_id))
                if pair_key:
                    cursor.execute("DELETE FROM shared_wallets WHERE pair_id = %s", (pair_key,))
                conn.commit()
            conn.close()

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
        bot.reply_to(message, f"❌ Помилка БД: {e}")

@bot.message_handler(commands=['marriages', 'пари', 'шлюби'])
def show_all_marriages(message):
    if is_user_banned(message.from_user.id): return

    chat_id = message.chat.id
    try:
        bot.send_chat_action(chat_id, 'typing')
        
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT s1.name, s2.name 
                    FROM marriages m
                    JOIN stats s1 ON m.user1_id = s1.user_id
                    JOIN stats s2 ON m.user2_id = s2.user_id
                """)
                couples = cursor.fetchall()
            conn.close()

        if not couples:
            bot.reply_to(
                message, 
                "🕸 <b>РАЦС пустує!</b>\nУ цьому чаті лише самотні вовки та незалежні левиці.", 
                parse_mode="HTML"
            )
            return

        response_lines = ["💍 <b>ОФІЦІЙНІ БАНДИТСЬКІ ПАРИ ЧАТУ:</b> 💍\n"]
        
        for idx, (name1, name2) in enumerate(couples, 1):
            clean_name1 = html_escape(name1) if name1 else "Хтось"
            clean_name2 = html_escape(name2) if name2 else "Хтось"
            response_lines.append(f"{idx}. {clean_name1} 💘 {clean_name2}")

        response_lines.append("\n<i>Хто ще без пари? Команда /шлюб чекає на вас!</i>")
        
        bot.reply_to(message, "\n".join(response_lines), parse_mode="HTML")

    except Exception as e:
        print(f"Помилка виведення списку шлюбів: {e}")
        bot.reply_to(message, "❌ Помилка бази даних. Драго не може знайти документи.")


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
    status_msg = bot.reply_to(message, "⏳ Драго починає малювати... Зачекай пару секунд.")
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
                caption=f"🔥 Твоя картинка готова!\n\n📋 <b>Запит:</b> {html_escape(prompt)}",
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
            clean_name = html_escape(name) if name else "Бро"
            mentions.append(f'<a href="tg://user?id={user_id}">{clean_name}</a>')

        if not mentions:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Не знайшов кого кликати, бро.")
            return

        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        if reason:
            reason_text = f"📌 <b>Причина збору:</b> {html_escape(reason)}"
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
# 👑 СУПЕР-АДМІН ПАНЕЛЬ V2.0
# ===================================================================

ADMIN_ID = 5512316636

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)

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
            "Твій бот працює на хмарній архітектурі Neon PostgreSQL. Усі бекапи автоматичні."
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

# --- АДМІН ОБРОБНИКИ (NEXT_STEP_HANDLERS) ---
def process_broadcast(message):
    if not is_admin(message.from_user.id): return
    if message.text and message.text.lower() in ['скасування', 'відміна', 'cancel']: 
        return bot.reply_to(message, "🛑 Скасовано.")
    
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
                bot.send_message(user[0], f"📢 <b>Повідомлення від Творця:</b>\n\n{html_escape(message.text)}", parse_mode="HTML")
                success += 1
                time.sleep(0.05)
            except Exception:
                failed += 1
        bot.send_message(message.chat.id, f"✅ <b>Розсилку завершено!</b>\nУспішно: {success}\nПомилок: {failed}", parse_mode="HTML")

    threading.Thread(target=run_broadcast, daemon=True).start()

def process_dm(message):
    if not is_admin(message.from_user.id): return
    if message.text and message.text.lower() in ['скасування', 'відміна', 'cancel']:
        return bot.reply_to(message, "🛑 Скасовано.")
    
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return bot.reply_to(message, "⚠️ Формат: `ID_користувача Текст`", parse_mode="Markdown")
        
        target_id = int(parts[0])
        dm_text = parts[1]
        
        bot.send_message(target_id, f"✉️ <b>Особисте повідомлення від Адміна:</b>\n\n{html_escape(dm_text)}", parse_mode="HTML")
        bot.reply_to(message, f"✅ Повідомлення успішно надіслано юзеру <code>{target_id}</code>!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка надсилання: {e}")

def process_user_info(message):
    if not is_admin(message.from_user.id): return
    if message.text and message.text.lower() in ['скасування', 'відміна', 'cancel']:
        return bot.reply_to(message, "🛑 Скасовано.")
    
    try:
        target_id = int(message.text.strip())
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT name, count, gender, balance FROM stats WHERE user_id = %s", (target_id,))
                res = cursor.fetchone()
                cursor.execute("SELECT 1 FROM banned_users WHERE user_id = %s", (target_id,))
                is_banned = bool(cursor.fetchone())
            conn.close()

        if not res:
            return bot.reply_to(message, "❌ Користувача з таким ID не знайдено в базі.")

        name, count, gender, balance = res
        status = "🚫 ЗАБАНЕНИЙ" if is_banned else "✅ Активний"

        bot.reply_to(
            message,
            f"👤 <b>ІНФОРМАЦІЯ ПРО ЮЗЕРА:</b>\n"
            f"• ID: <code>{target_id}</code>\n"
            f"• Ім'я: <b>{html_escape(name or 'Невідомо')}</b>\n"
            f"• Стать: {gender or 'Невідомо'}\n"
            f"• Повідомлень: {count or 0}\n"
            f"• Баланс: {balance or 0:,} грн\n"
            f"• Статус: <b>{status}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {e}")

def process_ban_user(message):
    if not is_admin(message.from_user.id): return
    if message.text and message.text.lower() in ['скасування', 'відміна', 'cancel']:
        return bot.reply_to(message, "🛑 Скасовано.")
    
    try:
        target_id = int(message.text.strip())
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (target_id,))
                conn.commit()
            conn.close()
        bot.reply_to(message, f"🚫 Користувача <code>{target_id}</code> успішно забанено!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка бану: {e}")

def process_unban_user(message):
    if not is_admin(message.from_user.id): return
    if message.text and message.text.lower() in ['скасування', 'відміна', 'cancel']:
        return bot.reply_to(message, "🛑 Скасовано.")
    
    try:
        target_id = int(message.text.strip())
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM banned_users WHERE user_id = %s", (target_id,))
                conn.commit()
            conn.close()
        bot.reply_to(message, f"✅ Користувача <code>{target_id}</code> успішно розбанено!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка розбану: {e}")

def process_raw_sql(message):
    if not is_admin(message.from_user.id): return
    if message.text and message.text.lower() in ['скасування', 'відміна', 'cancel']:
        return bot.reply_to(message, "🛑 Скасовано.")
    
    sql_query = message.text.strip()
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(sql_query)
                if cursor.description:
                    res = cursor.fetchall()
                    out = f"📊 **Результат:**\n`{res[:10]}`"
                else:
                    out = "✅ **Запит успішно виконано.**"
                conn.commit()
            conn.close()
        bot.reply_to(message, out, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка SQL: `{e}`", parse_mode="Markdown")


# ===================================================================
# 🧠 ГОЛОВНИЙ ОБРОБНИК (ЧАТ, ФАРМ ГРОШЕЙ ТА GEMINI)
# ===================================================================
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'sticker'])
def handle_all_messages(message):
    if is_user_banned(message.from_user.id): return
    
    user = message.from_user
    chat_id = message.chat.id
    text = message.text or message.caption or ""
    
    # 1. Оновлення статистики та нарахування грошей за активність (наприклад, 2 грн за смс)
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO stats (user_id, name, count, balance) 
                    VALUES (%s, %s, 1, 2)
                    ON CONFLICT (user_id) DO UPDATE 
                    SET name = EXCLUDED.name, 
                        count = stats.count + 1,
                        balance = stats.balance + 2;
                """, (user.id, user.first_name))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка оновлення стат: {e}")

    # 2. Збереження історії для новин
    if text:
        RECENT_MESSAGES.append(f"{user.first_name}: {text}")
        if len(RECENT_MESSAGES) > MAX_HISTORY_LIMIT:
            RECENT_MESSAGES.pop(0)

    # 3. Реакція Драго (ШІ)
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id
    bot_name_mentioned = "драго" in text.lower()

    if is_reply_to_bot or bot_name_mentioned or message.chat.type == 'private':
        bot.send_chat_action(chat_id, 'typing')
        try:
            chat = get_gemini_chat(chat_id)
            prompt = f"Користувач {user.first_name} каже: {text}"
            
            # Якщо юзер просить голосове
            if "скажи" in text.lower() or "голосове" in text.lower():
                response = chat.send_message(prompt + " (Дай коротку відповідь для озвучки)")
                send_voice_reply(chat_id, response.text, reply_to_id=message.message_id)
            else:
                response = chat.send_message(prompt)
                bot.reply_to(message, response.text)
        except Exception as e:
            print(f"Помилка Gemini: {e}")


# ===================================================================
# 💵 ФУНКЦІЇ БАЛАНСУ
# ===================================================================
def get_user_balance(user_id):
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance FROM stats WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
            conn.close()
        return res[0] if res else 0
    except Exception as e:
        print(f"Помилка отримання балансу: {e}")
        return 0

def update_user_balance(user_id, amount):
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("UPDATE stats SET balance = balance + %s WHERE user_id = %s", (amount, user_id))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка оновлення балансу: {e}")


# ===================================================================
# 🛠️ ДОПОМІЖНІ ФУНКЦІЇ
# ===================================================================
def html_escape(text):
    """Екранування спецсимволів для безпечного виводу в HTML"""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ===================================================================
# 📊 ДОДАТКОВІ КОМАНДИ (ТОП, СОННІ, ДОСЬЄ, НОВИНИ, МАФІЯ, МЕНЮ, МУЗИКА)
# ===================================================================

@bot.message_handler(commands=['top', 'stats', 'топ', 'статистика'])
def show_top_users(message):
    if is_user_banned(message.from_user.id): return

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Додаємо COALESCE, щоб уникнути проблем з NULL
                cursor.execute("SELECT COALESCE(name, 'Анонім'), COALESCE(count, 0), COALESCE(balance, 0) FROM stats ORDER BY count DESC LIMIT 10")
                top_users = cursor.fetchall()
            conn.close()

        if not top_users:
            bot.reply_to(message, "🕸️ База даних порожня!")
            return

        lines = ["📊 <b>ТОП-10 НАЙАКТИВНІШИХ ГРАВЦІВ:</b>\n"]
        for idx, (name, count, balance) in enumerate(top_users, 1):
            c_name = html_escape(name)
            rank = get_rank_title(count)
            lines.append(f"{idx}. <b>{c_name}</b> — <code>{count} пов.</code> | <code>{balance:,} грн</code> ({rank})")

        lines.append("\n<i>Пиши частіше, щоб піднятися вище в рейтингу! 🚀</i>")
        bot.reply_to(message, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        print(f"Помилка топ-статистики: {e}")
        bot.reply_to(message, f"❌ Помилка топ-статистики: <code>{e}</code>", parse_mode="HTML")

@bot.message_handler(commands=['sleepers', 'сонні', 'сони'])
def show_sleepers(message):
    if is_user_banned(message.from_user.id): return

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT name, count FROM stats WHERE count < 5 ORDER BY count ASC LIMIT 15")
                sleepers = cursor.fetchall()
            conn.close()

        if not sleepers:
            bot.reply_to(message, "🔥 Сонних немає! Усі валять повідомлення на повну!")
            return

        lines = ["💤 <b>СПИСОК СОННИХ МУХ ЧАТУ:</b>\n"]
        for idx, (name, count) in enumerate(sleepers, 1):
            c_name = html_escape(name or "Без імені")
            lines.append(f"{idx}. <b>{c_name}</b> (лише <code>{count} пов.</code>)")

        lines.append("\n<i>Прокинулися і швидко дали про себе знати! 🤬</i>")
        bot.reply_to(message, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка списку сонних: {e}")

@bot.message_handler(commands=['dossier', 'досьє', 'досье'])
def generate_user_dossier(message):
    if is_user_banned(message.from_user.id): return

    target_user = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    ensure_user_in_db(target_user)

    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT count, balance, gender FROM stats WHERE user_id = %s", (target_user.id,))
                res = cursor.fetchone()
            conn.close()

        count = res[0] if res else 0
        balance = res[1] if res else 0
        gender = res[2] if res else "Невідомо"

        bot.send_chat_action(message.chat.id, 'typing')

        prompt = (
            f"Склади секретне, гумористичне кримінальне досьє від імені агента СБУ Драго на користувача {target_user.first_name}. "
            f"Його стать: {gender}, активність: {count} повідомлень, статки: {balance} грн. "
            f"Пиши з пацанським гумором, іронією, сленгом українською мовою."
        )

        response = model.generate_content(prompt)
        dossier_text = f"🕵️‍♂️ <b>ТАЄМНЕ ДОСЬЄ СБУ: {html_escape(target_user.first_name)}</b>\n\n{response.text}"

        bot.reply_to(message, dossier_text, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка генерації досьє: {e}")

@bot.message_handler(commands=['news', 'новини', 'новости'])
def generate_chat_news(message):
    if is_user_banned(message.from_user.id): return

    if not RECENT_MESSAGES:
        bot.reply_to(message, "📰 Ще немає достатньо новин! Напишіть щось у чат, щоб Драго склав дайджест.")
        return

    bot.send_chat_action(message.chat.id, 'typing')

    try:
        history_text = "\n".join(RECENT_MESSAGES[-25:])
        prompt = (
            f"Ти — ведучий пацанських мемних новин Драго. На основі цих останніх повідомлень з чату:\n\n{history_text}\n\n"
            f"Склади короткий, смішний, емоційний та зухвалий випуск гарячих новин чату українською мовою."
        )

        response = model.generate_content(prompt)
        bot.reply_to(message, f"📰 <b>МЕМНІ НОВИНИ ЧАТУ ВІД ДРАГО</b> 🗞️\n\n{response.text}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка формування новин: {e}")

@bot.message_handler(commands=['mafia', 'мафія', 'мафия'])
def start_mafia_game(message):
    if is_user_banned(message.from_user.id): return

    bot.reply_to(
        message, 
        "🕵️‍♂️ <b>ГРА В МАФІЮ ВІД ДРАГО!</b>\n\n"
        "Збір братви оголошено! Щоб запустити повноцінну гру, треба мінімум 4 гравці.\n"
        "Натискайте кнопку нижче, щоб приєднатися!",
        reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎮 Приєднатися до гри", callback_data="mafia_join")),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "mafia_join")
def mafia_join_callback(call):
    bot.answer_callback_query(call.id, text="Ти в грі! Збираємо інших...")
    bot.send_message(
        call.message.chat.id, 
        f"🕵️‍♂️ <b>{html_escape(call.from_user.first_name)}</b> приєднався(-лася) до мафіозного клану!", 
        parse_mode="HTML"
    )

@bot.message_handler(commands=['menu', 'меню'])
def show_menu(message):
    if is_user_banned(message.from_user.id): return

    menu_text = (
        "🍕 <b>МЕНЮ СМАКОЛИКІВ ВІД ДРАГО</b> 🍺\n\n"
        "1. 🌯 Шаурма По-Київськи — 120 грн\n"
        "2. 🍕 Піца З Чотирма Сирами — 250 грн\n"
        "3. 🍔 Подвійний Пацанський Бургер — 180 грн\n"
        "4. 🍺 Холодне Пінне — 60 грн\n"
        "5. ☕ Кава Еспресо — 35 грн\n\n"
        "<i>Замовляй у барі, гроші списуються з твоєї кишені через /купити!</i>"
    )
    bot.reply_to(message, menu_text, parse_mode="HTML")

@bot.message_handler(commands=['найти', 'music', 'музика', 'музыка'])
def search_music(message):
    if is_user_banned(message.from_user.id): return

    query = message.text.split(maxsplit=1)
    if len(query) < 2:
        bot.reply_to(message, "🎧 Напиши назву треку або виконавця! Наприклад: <code>/найти Скрябін Кораблі</code>", parse_mode="HTML")
        return

    track_name = query[1].strip()
    
    msg = bot.reply_to(
        message, 
        f"🔍 <b>Драго шукає трек:</b> <i>{html_escape(track_name)}</i>...\n\n"
        f"🎧 <i>Зачекай пару секунд!</i>", 
        parse_mode="HTML"
    )

    try:
        query_string = urllib.parse.urlencode({"search_query": track_name})
        html_content = urllib.request.urlopen("https://www.youtube.com/results?" + query_string)
        
        search_results = re.findall(r'url\"\:\"\/watch\?v\=(.*?(?=\"))', html_content.read().decode())
        
        if search_results:
            video_url = "https://www.youtube.com/watch?v=" + search_results[0]
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text=f"🎧 <b>Ось твій трек:</b>\n{video_url}",
                parse_mode="HTML"
            )
        else:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text="❌ Драго перерив весь інтернет, але нічого не знайшов."
            )
    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=f"❌ Помилка пошуку: {e}"
        )

# ===================================================================
# 💬 ГОЛОВНИЙ ОБРОБНИК ТЕКСТОВИХ ПОВІДОМЛЕНЬ (ШІ ЧАТ ТА ЛІЧИЛЬНИК)
# ===================================================================

@bot.message_handler(content_types=['text'])
def handle_all_text_messages(message):
    if is_user_banned(message.from_user.id):
        return

    user = message.from_user
    chat_id = message.chat.id
    text = message.text or ""

    # 1. Забезпечуємо користувача у БД
    ensure_user_in_db(user)

    # 2. Нараховуємо 1 повідомлення та капає пацанський бонус (+гроші)
    try:
        cash_reward = random.randint(5, 25)
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE stats 
                    SET count = count + 1, 
                        balance = balance + %s,
                        name = %s,
                        username = %s
                    WHERE user_id = %s
                """, (cash_reward, user.first_name, user.username, user.id))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка оновлення статів: {e}")

    # 3. Зберігаємо в буфер новин
    RECENT_MESSAGES.append(f"{user.first_name}: {text}")
    if len(RECENT_MESSAGES) > MAX_HISTORY_LIMIT:
        RECENT_MESSAGES.pop(0)

    # 4. Перевірка чи звертаються до Драго
    bot_name = bot.get_me().username.lower() if bot.get_me() else ""
    is_private = message.chat.type == "private"
    is_mentioned = "драго" in text.lower() or (bot_name and f"@{bot_name}" in text.lower())
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id

    if is_private or is_mentioned or is_reply_to_bot:
        bot.send_chat_action(chat_id, 'typing')

        wants_voice = any(kw in text.lower() for kw in ["скажи", "голосове", "голосом", "озвуч", "скажи голосом"])

        try:
            chat_genai = get_gemini_chat(chat_id)
            prompt_clean = text.replace("драго", "").replace("Драго", "").replace(f"@{bot_name}", "").strip()
            if not prompt_clean:
                prompt_clean = "Привіт, Драго!"

            response = chat_genai.send_message(prompt_clean)
            reply_text = response.text

            if wants_voice:
                send_voice_reply(chat_id, reply_text, reply_to_id=message.message_id)
            else:
                bot.reply_to(message, reply_text)
        except Exception as e:
            print(f"Помилка Gemini AI: {e}")
            bot.reply_to(message, "Шось у мене мізки закипіли... Повтори пізніше, бро.")


# ===================================================================
# 🚀 ЗАПУСК БОТА ТА ПОТОКІВ
# ===================================================================
if __name__ == '__main__':
    # 1. Запускаємо HTTP-сервер для Keep-Alive (наприклад, для Render/Railway) у фоновому потоці
    threading.Thread(target=run_dummy_server, daemon=True).start()

    # 2. Запускаємо Discord-агента у фоновому потоці
    threading.Thread(target=run_discord, daemon=True).start()

    # 3. Запускаємо основний цикл Telegram бота
    print("🚀 Бот Драго успішно запущений і готовий до роботи!")
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print(f"❌ Помилка в роботі бота: {e}")
