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

DATABASE_URL = os.environ.get('DATABASE_URL')

# Підключаємося до БД
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# 🔒 ЛОК ДЛЯ БЕЗПЕКИ ПОТОКІВ
db_lock = threading.Lock()

# Створення таблиці при запуску (виправлено помилку неіснуючих conn/cursor)
try:
    with db_lock:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS stats (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                count INTEGER,
                gender TEXT
            )""")
        conn.commit()
        conn.close()
except Exception as e:
    print(f"Помилка створення таблиці stats: {e}")
 
# ==================== НАЛАШТУВАННЯ ====================
API_ID = int(os.environ.get('API_ID', 12345678))
API_HASH = os.environ.get('API_HASH', 'ТВІЙ_API_HASH')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ТВІЙ_TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'ТВІЙ_GEMINI_API_KEY')
# ======================================================
 
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

generation_config = {
    "max_output_tokens": 2048,
    "temperature": 0.85,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
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
@bot.message_handler(commands=['top', 'stats'])
def show_chat_activity(message):
    chat_id = message.chat.id
    try:
        bot.send_chat_action(chat_id, 'typing')
        with db_lock:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT name, count, gender FROM stats ORDER BY count DESC LIMIT 10")
                rows = cursor.fetchall()
                cursor.execute("SELECT SUM(count) FROM stats")
                total_messages = cursor.fetchone()[0] or 0
            conn.close()

        if not rows:
            bot.reply_to(message, "📊 Таблиця активності порожня. Ви що, взагалі нічого не пишете? Ну ви й сонні мухи... 🥱")
            return

        response_lines = [
            "🏆 <b>ТОП-10 АКТИВНИХ БАНДИТІВ ЧАТУ</b> 🏆",
            f"📈 <i>Всього в базі зафіксовано повідомлень: <b>{total_messages}</b></i>\n",
        ]

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

        for idx, (name, count, gender) in enumerate(rows):
            medal = medals[idx] if idx < len(medals) else "🔹"
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Анонім"
            suffix = "🕺" if gender == "Хлопець" else "💃" if gender == "Дівчина" else "👤"
            response_lines.append(f"{medal} {clean_name} {suffix} — <b>{count}</b> pov.")

        response_lines.append("\n☠️ <i>Ті, кого немає в списку — підніміть дупи і почніть писати, бо видалю нафіг!</i>")
        full_response = "\n".join(response_lines)
        bot.reply_to(message, full_response, parse_mode="HTML")

    except Exception as e:
        print(f"Помилка виведення топу: {e}")
        bot.reply_to(message, "❌ Не зміг підрахувати ваші звивини (помилка бази даних). Спробуй пізніше.")


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
                cursor.execute("SELECT user_id, name, count FROM stats WHERE count < 5 ORDER BY count ASC LIMIT 15")
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
    if (message.new_chat_member.status in ['member', 'administrator', 'restricted']
            and not message.new_chat_member.user.is_bot):
        user = message.new_chat_member.user
        gender = ensure_user_in_db(user)
        name = user.first_name
        if gender == 'Дівчина':
            greeting = f"Вітаємо в чаті, <b>{name}</b>! 🤍\nРадий бачити тебе тут!"
        elif gender == 'Хлопець':
            greeting = f"Йо, <b>{name}</b>, вітаємо в чаті! 🤝\nРадий бачити тебе тут, бро!"
        else:
            greeting = f"Вітаємо в нашій групі, <b>{name}</b>! 🤍\nРозкажи трохи про себе!"
        bot.send_message(message.chat.id, greeting, parse_mode="HTML")

    elif (message.old_chat_member.status in ['member', 'administrator', 'restricted']
          and message.new_chat_member.status in ['left', 'kicked']):
        name = message.old_chat_member.user.first_name
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

    if gender == 'Never' or gender == 'Невідомо':
        guessed = analyze_gender_from_text(text)
        if guessed in ['Хлопець', 'Дівчина']:
            try:
                with db_lock:
                    conn = get_db_connection()
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE stats SET gender = %s WHERE user_id = %s", (guessed, user.id))
                    conn.commit()
                    conn.close()
                gender = guessed
            except Exception as e:
                print(f"Помилка оновлення гендеру: {e}")

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
        print(f"Помилка оновлення лічильника повідомлень: {e}")

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

    print("🔥 Драго вийшов на полювання і готовий до роботи на Neon DB!")
    bot.infinity_polling(allowed_updates=['message', 'edited_message', 'chat_member', 'callback_query'])
