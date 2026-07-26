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
        "Пиши коротко і ясно!, твій создатель СБУ"
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

def ensure_user_in_db(user):
    """Гарантує, що користувач є в базі stats"""
    try:
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO stats (user_id, name, count)
                    VALUES (%s, %s, 0)
                    ON CONFLICT (user_id) DO UPDATE 
                    SET name = EXCLUDED.name;
                """, (user.id, user.first_name))
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Помилка ensure_user_in_db: {e}")

def safe_get_rank(msg_count):
    """Резервний підрахунок рангу, якщо немає зовнішньої get_rank_title"""
    if 'get_rank_title' in globals():
        return get_rank_title(msg_count)
    
    if msg_count > 1000: return "Місцева Легенда 👑"
    if msg_count > 500: return "Завзятий Дописувач 🔥"
    if msg_count > 100: return "Чатер 💬"
    return "Новачок 🐣"


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
        clean_name = user.first_name.replace("<", "&lt;").replace(">", "&gt;")
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
            marriage_status = f"💍 У шлюбі з <b>{spouse_name.replace('<', '&lt;').replace('>', '&gt;')}</b>"
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
def is_chat_admin(chat_id, user_id):
    """
    Перевіряє 3 рівні доступу:
    1. Суперадмін бота (ти через is_admin).
    2. Призначений через /addmod модератор.
    3. Стандартний адмін Telegram-групи.
    """
    # 1. Глобальний адмін бота
    if is_admin(user_id):5512316636
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
    """Парсить час для муту (наприклад: 10m, 2h, 1d) -> повертає секунди"""
    unit = time_str[-1].lower()
    if not time_str[:-1].isdigit():
        return None
    val = int(time_str[:-1])
    if unit == 'm':
        return val * 60
    elif unit == 'h':
        return val * 3600
    elif unit == 'd':
        return val * 86400
    elif unit == 's':
        return val
    return None


# ===================================================================
# 3. КОМАНДИ КЕРУВАННЯ МОДЕРАТОРАМИ (ТІЛЬКИ ДЛЯ ТЕБЕ)
# ===================================================================

# ➕ Додати модератора (/addmod у відповідь або /addmod ID)
@bot.message_handler(commands=['addmod'])
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
        if len(args) > 1 and args[1].isdigit():
            target_id = int(args[1])
            target_name = f"ID: {target_id}"

    if not target_id:
        return bot.reply_to(
            message, 
            "⚠️ **Як використовувати:**\n"
            "1. Відповіж командою `/addmod` на повідомлення користувача.\n"
            "2. Або напиши: `/addmod [ID_користувача]`",
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


# ➖ Забрати права модератора (/delmod у відповідь або /delmod ID)
@bot.message_handler(commands=['delmod', 'rmmod'])
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
        if len(args) > 1 and args[1].isdigit():
            target_id = int(args[1])
            target_name = f"ID: {target_id}"

    if not target_id:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення або вкажи ID!\nПриклад: `/delmod 12345678`", parse_mode="Markdown")

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


# 📋 Список призначених модераторів (/modlist)
@bot.message_handler(commands=['modlist', 'mods'])
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

# 🔇 МУТ
@bot.message_handler(commands=['mute'])
def mute_user(message):
    if message.chat.type == 'private':
        return bot.reply_to(message, "⚠️ Ця команда працює лише в групах!")

    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Відповіж на повідомлення порушника!\nПриклад: `/mute 30m спам`", parse_mode="Markdown")

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
        bot.reply_to(
            message,
            f"🔇 Користувача **{target_user.first_name}** замучено на **{duration_sec // 60} хв.**\n📝 **Причина:** {reason}",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка муту (перевірте права бота): `{e}`", parse_mode="Markdown")


# 🔊 РОЗМУТ
@bot.message_handler(commands=['unmute'])
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


# 🔨 БАН
@bot.message_handler(commands=['ban'])
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


# 🔓 РОЗБАН
@bot.message_handler(commands=['unban'])
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


# 👞 КІК
@bot.message_handler(commands=['kick'])
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


# 🧹 ОЧИЩЕННЯ ПОВІДОМЛЕНЬ (/clear 10)
@bot.message_handler(commands=['clear', 'purge'])
def clear_messages(message):
    if message.chat.type == 'private': return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ У тебе немає прав модератора!")

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return bot.reply_to(message, "⚠️ Вкажи кількість повідомлень.\nПриклад: `/clear 15`", parse_mode="Markdown")

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
        "• /sleepers або /сонні — виклик тих, хто заліг на дно і мовчить.\n"
        "• /dossier або /досьє — <i>(реплай)</i> скласти секретну справу СБУ на юзера.\n"
        "• /news або /новини — гарячий випуск мемних новин з переписок."
    ),
    "page_fun": (
        "🎰 <b>РОЗВАГИ, МУЗИКА ТА АРТ:</b>\n\n"
        "• /mafia або /мафія — зібрати братву на гру в Мафію.\n"
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
        "• /продати_бізнес [код] — продати бізнес за 75% від початкової вартості."
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
        "• /addmod / /delmod / /modlist — керування модераторами бота."
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
# 📣 КОМАНДА ЗАГАЛЬНОГО ЗБОРУ (@all / ЗБІР) — В СТИЛІ БОТА-ЗАЗИВАЛИ
# ===================================================================

import random

# Список крутих фраз для заклику
CALL_HEADERS = [
    "📢 <b>УВАГА, ЗАГОН! ЗАГАЛЬНА МОБІЛІЗАЦІЯ!</b> 🚨",
    "🔔 <b>ТРИВОГА В ЧАТІ! ВСІМ ПІДНЯТИСЯ!</b> 💥",
    "⚡ <b>ДРАГО ЗБИРАЄ БАНДУ! ХТО НЕ З НАМИ — ТОЙ МУСОР!</b> 🔥",
    "🔊 <b>ГОЛОВНИЙ ШУМОВИК ВВІМКНЕНО! ВШЕНТ РОЗБУДИТИ ВСІХ!</b> 💣"
]

CALL_FOOTERS = [
    "<i>⚡ Живо кидайте свої справи і відповідайте!</i>",
    "<i>👀 Хто проігнорить — той закриває банк.</i>",
    "<i>👇 Відмічаємось у коментарях!</i>",
    "<i>🫡 Явка обов'язкова, відмазки не приймаються.</i>"
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
        reason = ""
        
        for trigger in ['@all', '.all', '.збір', 'збір', '/all']:
            if original_text.lower().startswith(trigger):
                reason = original_text[len(trigger):].strip()
                break

        # Беремо з БД тільки тих, хто активний у чаті
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, name FROM stats WHERE in_chat = TRUE")
                users = cursor.fetchall()
            conn.close()

        if not users:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ База даних порожня, нікого кликати.")
            return

        mentions = []
        for u_id, name in users:
            if u_id == bot.get_me().id:
                continue
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Бро"
            mentions.append(f'<a href="tg://user?id={u_id}">{clean_name}</a>')

        if not mentions:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Здається, крім тебе й мене тут нікого немає, бро.")
            return

        # Видаляємо тимчасове повідомлення
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        # Формуємо причину
        if reason:
            clean_reason = reason.replace("<", "&lt;").replace(">", "&gt;")
            reason_text = f"📌 <b>Причина:</b> <i>{clean_reason}</i>\n"
        else:
            reason_text = "📌 <b>Причина:</b> <i>Терміновий збір без пояснень!</i>\n"

        header = random.choice(CALL_HEADERS)
        footer = random.choice(CALL_FOOTERS)

        # 1. Відправляємо головну шапку-зазивалу
        main_call = (
            f"{header}\n\n"
            f"{reason_text}\n"
            f"👥 <b>Учасників до виклику:</b> <code>{len(mentions)}</code>\n"
            f"───────────────────────\n"
            f"{footer}"
        )
        
        bot.send_message(chat_id, main_call, parse_mode="HTML")

        # 2. Розбиваємо теги на акуратні блоки (по 5 осіб), щоб Telegram точно надіслав сповіщення
        chunk_size = 5
        for i in range(0, len(mentions), chunk_size):
            chunk = mentions[i:i + chunk_size]
            
            # Гарне оформлення кожного блоку
            mention_text = (
                f"🎯 <b>На зв'язок:</b>\n"
                f"└ " + ", ".join(chunk)
            )
            
            bot.send_message(chat_id, mention_text, parse_mode="HTML")
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
# 📊 ТОП АКТИВНОСТІ (Стиль бота Соняшник)
# ===================================================================

@bot.message_handler(commands=['top', 'stats', 'топ'])
def show_chat_activity(message):
    if is_user_banned(message.from_user.id): 
        return
    
    chat_id = message.chat.id
    
    try:
        bot.send_chat_action(chat_id, 'typing')
        
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Беремо топ 10 активних у чаті
                cursor.execute("SELECT name, count FROM stats WHERE in_chat = TRUE ORDER BY count DESC LIMIT 10")
                rows = cursor.fetchall()
                
                # Загальна сума повідомлень усього чату
                cursor.execute("SELECT SUM(count) FROM stats WHERE in_chat = TRUE")
                total_messages = cursor.fetchone()[0] or 0
            conn.close()

        if not rows or total_messages == 0:
            bot.reply_to(message, "🕸 <b>У чаті ще немає зафіксованої активності!</b>", parse_mode="HTML")
            return

        # Шапка
        chat_title = message.chat.title or "цьому чаті"
        chat_title_clean = chat_title.replace("<", "&lt;").replace(">", "&gt;")
        
        response = [
            f"📊 <b>Топ найактивніших учасників в {chat_title_clean}:</b>\n"
        ]

        # Іконки топу
        medals = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

        # Формування списку активності з правильними відступами
        for idx, (name, count) in enumerate(rows):
            icon = medals[idx] if idx < len(medals) else "🔹"
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Анонім"
            
            # Вираховуємо відсоток від загальної кількості
            percent = (count / total_messages) * 100 if total_messages > 0 else 0
            
            # Форматований вивід у стилі Соняшника
            response.append(f"{icon} <b>{clean_name}</b> — <code>{count:,}</code> пов. (<i>{percent:.1f}%</i>)")

        # Підвал
        response.append(f"\n💬 Всього повідомлень у чаті: <b>{total_messages:,}</b>")
        
        bot.reply_to(message, "\n".join(response), parse_mode="HTML")

    except Exception as e:
        print(f"Помилка виведення топу: {e}")
        bot.reply_to(message, "❌ Не вдалося завантажити топ активності.", parse_mode="HTML")

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
