import os
import base64
import requests
import random
import io
import threading
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from telebot import telebot, types
from http.server import SimpleHTTPRequestHandler, HTTPServer
import telebot
import google.generativeai as genai
from PIL import Image
import sqlite3

# ===================================================================
# 📋 ЛОГУВАННЯ
# ===================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('drago_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===================================================================
# 💾 БАЗА ДАНИХ
# ===================================================================
conn = sqlite3.connect('drago_bot.db', check_same_thread=False)
cursor = conn.cursor()
db_lock = threading.Lock()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    count INTEGER DEFAULT 0,
    gender TEXT DEFAULT 'не вказано',
    warns INTEGER DEFAULT 0,
    is_muted INTEGER DEFAULT 0,
    mute_until INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_stats (
    user_id INTEGER,
    chat_id INTEGER,
    date TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);
""")
conn.commit()

# ===================================================================
# ⚙️ НАЛАШТУВАННЯ
# ===================================================================
API_ID = 29566622
API_HASH = 'd06e98b0540b86be0722e099c4c22355'
TELEGRAM_TOKEN = '8788139276:AAGKr6sFii4n9B1E5sysHSa-xMTgYsmUZfI'
GEMINI_API_KEY = 'AIzaSyC_7U44ek_eaN0u6GV4FqL-m1N9OcpvVJM'
WEATHER_API_KEY = 'ТВІЙ_КЛЮЧ_OPENWEATHER'  # 👈 Замінити на реальний ключ з openweathermap.org
ADMIN_IDS = []  # 👈 Можна додати ID супер-адмінів

# ===================================================================
# 🤖 ІНІЦІАЛІЗАЦІЯ БОТА ТА ШІ
# ===================================================================
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

generation_config = {
    "max_output_tokens": 2048,
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
        "Пиши коротко і ясно!"
    )
)

bot_chats = {}

def get_gemini_chat(chat_id):
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]

# ===================================================================
# 🛡️ АНТИСПАМ (FLOOD CONTROL)
# ===================================================================
flood_data = defaultdict(list)
FLOOD_LIMIT = 5       # максимум повідомлень
FLOOD_TIME = 10       # за скільки секунд
MUTE_DURATION = 300   # мут у секундах (5 хвилин)

def check_flood(user_id, chat_id):
    now = time.time()
    key = (user_id, chat_id)
    flood_data[key] = [t for t in flood_data[key] if now - t < FLOOD_TIME]
    flood_data[key].append(now)
    return len(flood_data[key]) > FLOOD_LIMIT

# ===================================================================
# 🔍 АНТИМАТ
# ===================================================================
BAD_WORDS = [
    'хуй', 'піздець', 'пізда', 'єбать', 'їбать', 'блять', 'сука',
    'нігер', 'гандон', 'залупа', 'мудак', 'пидор', 'пидорас'
]

def has_bad_words(text):
    text_lower = text.lower()
    return any(word in text_lower for word in BAD_WORDS)

# ===================================================================
# 🛠️ ДОПОМІЖНІ ФУНКЦІЇ
# ===================================================================
def is_admin(bot, chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception:
        return False

def update_message_count(user_id, name, chat_id):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        cursor.execute(
            "INSERT OR IGNORE INTO stats (user_id, name, count, gender) VALUES (?, ?, 0, 'не вказано')",
            (user_id, name)
        )
        cursor.execute("UPDATE stats SET count = count + 1, name = ? WHERE user_id = ?", (name, user_id))
        cursor.execute(
            "INSERT INTO daily_stats (user_id, chat_id, date, count) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(user_id, chat_id, date) DO UPDATE SET count = count + 1",
            (user_id, chat_id, today)
        )
        conn.commit()

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(("", port), SimpleHTTPRequestHandler)
    httpd.serve_forever()

# ===================================================================
# 🎭 1. МЕМ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.мем')
def send_meme(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        meme_dir = r"D:\DragoBot\memes"
        if os.path.exists(meme_dir):
            memes = [f for f in os.listdir(meme_dir) if f.endswith(('.png', '.jpg', '.jpeg', '.gif'))]
            if memes:
                with open(os.path.join(meme_dir, random.choice(memes)), 'rb') as photo:
                    bot.send_photo(message.chat.id, photo)
            else:
                bot.send_message(message.chat.id, "Бро, папка з мемами порожня!")
        else:
            bot.send_message(message.chat.id, "Не знайшов папку з мемами.")
    except Exception as e:
        logger.error(f"Помилка мему: {e}")

# ===================================================================
# 🖼️ 2. ГЕНЕРАЦІЯ ЗОБРАЖЕНЬ
# ===================================================================
@bot.message_handler(commands=['generate'])
def generate_image_wait_and_send(message):
    prompt = message.text[10:].strip()
    if not prompt:
        bot.reply_to(message, "⚠️ Напиши опис! Наприклад: /generate cyberpunk wolf warrior")
        return
    status_msg = bot.reply_to(message, "⏳ Драго малює... Зачекай 30-120 секунд.")
    try:
        encoded_prompt = requests.utils.quote(prompt)
        url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1, 999999)}&model=flux&nologo=true"
        response = requests.get(url, timeout=120)
        if response.status_code == 200 and len(response.content) >= 10000:
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            bio = io.BytesIO()
            bio.name = 'drago_art.jpg'
            img.save(bio, 'JPEG', quality=95)
            bio.seek(0)
            bot.send_photo(
                message.chat.id, bio,
                caption=f"🔥 Готово!\n\n📋 <b>Запит:</b> {prompt}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
            raise Exception(f"Код відповіді: {response.status_code}")
    except Exception as e:
        logger.error(f"Помилка генерації: {e}")
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=f"❌ Не зміг намалювати. Спробуй пізніше.\n<code>{str(e)[:80]}</code>",
            parse_mode="HTML"
        )

# ===================================================================
# 🎙️ 3. ГОЛОСОВІ ПОВІДОМЛЕННЯ
# ===================================================================
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    chat_id = message.chat.id
    if message.chat.type in ['group', 'supergroup']:
        if not (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id):
            return
    try:
        bot.send_chat_action(chat_id, 'typing')
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        audio_part = {"data": downloaded_file, "mime_type": "audio/ogg"}
        prompt = "Послухай це голосове та дай повну дотепну відповідь як Драго:"
        response = model.generate_content([prompt, audio_part])
        bot.reply_to(message, response.text)
    except Exception as e:
        logger.error(f"Помилка голосового: {e}")
        bot.reply_to(message, "Не зміг розпізнати голосове.")

# ===================================================================
# 👋 4. ВХІД/ВИХІД
# ===================================================================
@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    if message.new_chat_member.status in ['member', 'administrator', 'restricted'] and not message.new_chat_member.user.is_bot:
        user_id = message.new_chat_member.user.id
        name = message.new_chat_member.user.first_name
        with db_lock:
            cursor.execute(
                "INSERT OR IGNORE INTO stats (user_id, name, count, gender) VALUES (?, ?, 0, 'не вказано')",
                (user_id, name)
            )
            conn.commit()
        bot.send_message(
            message.chat.id,
            f"Вітаємо в групі, <b>{name}</b>! 🤍\nДраго цікавиться — хлопець чи дівчина? Просто напиши щось!",
            parse_mode="HTML"
        )
    elif message.old_chat_member.status in ['member', 'administrator', 'restricted'] and message.new_chat_member.status in ['left', 'kicked']:
        name = message.old_chat_member.user.first_name
        goodbyes = [
            f"Ну і пофіг, <b>{name}</b> пішов. 👋",
            f"<b>{name}</b> покинув чат. Менше народу — більше кисню. 🚪",
            f"<b>{name}</b> злиняв. Схоже, не витримав нашого рівня інтелекту 🧠",
        ]
        bot.send_message(message.chat.id, random.choice(goodbyes), parse_mode="HTML")

# ===================================================================
# 🎮 5. ГРА В СЛОВА
# ===================================================================
game_state = {}

@bot.message_handler(commands=['game'])
def start_word_game(message):
    game_state[message.chat.id] = {"last_letter": None, "used_words": []}
    bot.reply_to(message, "🎲 Гра в слова розпочата! Пиши перше слово.")

@bot.message_handler(commands=['stop'])
def stop_word_game(message):
    if message.chat.id in game_state:
        del game_state[message.chat.id]
        bot.reply_to(message, "Гру зупинено. Драго відпочиває 👋")
    else:
        bot.reply_to(message, "Гра і так не запущена.")

def handle_word_game(message):
    chat_id = message.chat.id
    word = message.text.lower().strip()
    state = game_state[chat_id]
    if not message.text.replace(" ", "").isalpha() or len(word) < 2:
        return
    if state["last_letter"] and word[0] != state["last_letter"]:
        bot.reply_to(message, f"Не-а! Слово має починатися на '{state['last_letter'].upper()}'.")
        return
    if word in state["used_words"]:
        bot.reply_to(message, "Це слово вже було! 😎")
        return
    state["used_words"].append(word)
    next_letter = word[-1] if word[-1] not in ['ь', 'и', 'й', 'ї'] else word[-2]
    state["last_letter"] = next_letter
    bot.reply_to(message, f"✅ Прийнято! Наступне слово на '{next_letter.upper()}'.")

# ===================================================================
# 📊 6. СТАТИСТИКА (ТОП / ПРОФІЛЬ / STATS)
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.топ')
def show_top(message):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        cursor.execute("""
            SELECT s.name, ds.count FROM daily_stats ds
            JOIN stats s ON s.user_id = ds.user_id
            WHERE ds.chat_id = ? AND ds.date = ?
            ORDER BY ds.count DESC LIMIT 10
        """, (message.chat.id, today))
        rows = cursor.fetchall()
    if not rows:
        bot.reply_to(message, "Сьогодні ще ніхто нічого не писав. Мовчанка якась...")
        return
    medals = ['🥇', '🥈', '🥉'] + ['4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    text = "📊 <b>Топ балакунів сьогодні:</b>\n\n"
    for i, (name, count) in enumerate(rows):
        text += f"{medals[i]} <b>{name}</b> — {count} повідомлень\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.профіль')
def show_profile(message):
    user_id = message.from_user.id
    name = message.from_user.first_name
    with db_lock:
        cursor.execute("SELECT count, gender, warns FROM stats WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
    if not row:
        bot.reply_to(message, "Ти ще не зареєстрований в базі. Пиши більше!")
        return
    count, gender, warns = row
    text = (
        f"👤 <b>Профіль {name}</b>\n\n"
        f"📨 Повідомлень всього: <b>{count}</b>\n"
        f"🚻 Стать: <b>{gender}</b>\n"
        f"⚠️ Варнінги: <b>{warns}/3</b>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(commands=['stats'])
def show_group_stats(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        bot.reply_to(message, "Тільки для адмінів, бро!")
        return
    with db_lock:
        cursor.execute("SELECT COUNT(*) FROM stats")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(count) FROM stats")
        total_msgs = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender = 'Хлопець'")
        boys = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender = 'Дівчина'")
        girls = cursor.fetchone()[0]
    text = (
        f"📈 <b>Статистика групи</b>\n\n"
        f"👥 Всього юзерів: <b>{total_users}</b>\n"
        f"💬 Всього повідомлень: <b>{total_msgs}</b>\n"
        f"👦 Хлопців: <b>{boys}</b>\n"
        f"👧 Дівчат: <b>{girls}</b>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# ===================================================================
# 🌤️ 7. ПОГОДА
# ===================================================================
@bot.message_handler(commands=['погода'])
def get_weather(message):
    city = message.text.replace('/погода', '').strip()
    if not city:
        bot.reply_to(message, "Напиши місто! Наприклад: /погода Київ")
        return
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ua"
        data = requests.get(url, timeout=10).json()
        if data.get('cod') != 200:
            bot.reply_to(message, f"Не знайшов місто '{city}'. Перевір написання.")
            return
        temp = data['main']['temp']
        feels = data['main']['feels_like']
        desc = data['weather'][0]['description']
        humidity = data['main']['humidity']
        wind = data['wind']['speed']
        text = (
            f"🌤️ <b>Погода в {city}</b>\n\n"
            f"🌡 Температура: <b>{temp:.1f}°C</b> (відчувається як {feels:.1f}°C)\n"
            f"☁️ Опис: <b>{desc}</b>\n"
            f"💧 Вологість: <b>{humidity}%</b>\n"
            f"💨 Вітер: <b>{wind} м/с</b>"
        )
        bot.send_message(message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Помилка погоди: {e}")
        bot.reply_to(message, "Не зміг отримати погоду. Спробуй пізніше.")

# ===================================================================
# 🌍 8. ПЕРЕКЛАД
# ===================================================================
@bot.message_handler(commands=['translate'])
def translate_text(message):
    parts = message.text.split(' ', 2)
    if len(parts) < 3:
        # перекласти реплай
        if message.reply_to_message and message.reply_to_message.text:
            text_to_translate = message.reply_to_message.text
            lang = parts[1] if len(parts) > 1 else 'англійська'
        else:
            bot.reply_to(message, "Формат: /translate [мова] [текст] або реплай на повідомлення")
            return
    else:
        lang = parts[1]
        text_to_translate = parts[2]
    try:
        prompt = f"Переклади цей текст на {lang} мову. Поверни ТІЛЬКИ переклад без пояснень: {text_to_translate}"
        response = model.generate_content(prompt)
        bot.reply_to(message, f"🌍 <b>Переклад ({lang}):</b>\n{response.text}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, "Не зміг перекласти.")

# ===================================================================
# 📝 9. СТИСНЕННЯ ТЕКСТУ
# ===================================================================
@bot.message_handler(commands=['summarize'])
def summarize_text(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Зроби реплай на повідомлення, яке треба стиснути!")
        return
    text = message.reply_to_message.text or message.reply_to_message.caption
    if not text:
        bot.reply_to(message, "Немає тексту для стиснення.")
        return
    try:
        prompt = f"Стисни цей текст до 2-3 ключових речень українською мовою: {text}"
        response = model.generate_content(prompt)
        bot.reply_to(message, f"📝 <b>Коротко:</b>\n{response.text}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, "Не зміг стиснути текст.")

# ===================================================================
# 🎯 10. ДУЕЛЬ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.дуель'))
def duel(message):
    parts = message.text.split()
    challenger = message.from_user.first_name
    if message.reply_to_message:
        opponent = message.reply_to_message.from_user.first_name
    elif len(parts) > 1:
        opponent = parts[1].replace('@', '')
    else:
        bot.reply_to(message, "Вкажи суперника! Реплай або .дуель @нікнейм")
        return

    winner = random.choice([challenger, opponent])
    loser = opponent if winner == challenger else challenger
    results = [
        f"⚔️ Дуель між <b>{challenger}</b> та <b>{opponent}</b>!\n\n🏆 Переміг <b>{winner}</b>! {loser} навіть не встиг дістати зброю 😂",
        f"🔥 <b>{challenger}</b> vs <b>{opponent}</b>!\n\n<b>{winner}</b> знищив суперника одним поглядом. {loser} в нокауті 💀",
        f"🎯 Дуель!\n\n<b>{winner}</b> — чемпіон! <b>{loser}</b> тікав, але не допомогло 🏃",
    ]
    bot.send_message(message.chat.id, random.choice(results), parse_mode="HTML")

# ===================================================================
# 💡 11. ЦІКАВИЙ ФАКТ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.факт')
def random_fact(message):
    try:
        prompt = "Розкажи один цікавий і несподіваний факт про світ. Коротко, 2-3 речення, в стилі Драго."
        response = model.generate_content(prompt)
        bot.send_message(message.chat.id, f"💡 {response.text}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, "Мозок завис, спробуй пізніше.")

# ===================================================================
# ♈ 12. ГОРОСКОП
# ===================================================================
ZODIAC_SIGNS = {
    'овен': 'Aries', 'телець': 'Taurus', 'близнюки': 'Gemini',
    'рак': 'Cancer', 'лев': 'Leo', 'діва': 'Virgo',
    'терези': 'Libra', 'скорпіон': 'Scorpio', 'стрілець': 'Sagittarius',
    'козоріг': 'Capricorn', 'водолій': 'Aquarius', 'риби': 'Pisces'
}

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.астрологія'))
def horoscope(message):
    parts = message.text.lower().split()
    if len(parts) < 2:
        bot.reply_to(message, f"Вкажи знак! Наприклад: .астрологія овен\nДоступні: {', '.join(ZODIAC_SIGNS.keys())}")
        return
    sign = parts[1]
    if sign not in ZODIAC_SIGNS:
        bot.reply_to(message, f"Не знаю такого знаку. Спробуй: {', '.join(ZODIAC_SIGNS.keys())}")
        return
    try:
        prompt = f"Напиши короткий гороскоп на сьогодні для знаку зодіаку {sign} в стилі Драго — дотепно, з підколками, але корисно. 3-4 речення."
        response = model.generate_content(prompt)
        bot.send_message(message.chat.id, f"♈ <b>Гороскоп для {sign.capitalize()}:</b>\n\n{response.text}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, "Зірки мовчать сьогодні.")

# ===================================================================
# 🃏 13. АНЕКДОТ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['.анекдот', '.жарт'])
def tell_joke(message):
    try:
        prompt = "Розкажи короткий смішний анекдот українською мовою. Тільки анекдот, без вступів."
        response = model.generate_content(prompt)
        bot.send_message(message.chat.id, f"🃏 {response.text}")
    except Exception:
        bot.reply_to(message, "Жарти скінчилися, поповнення завтра 😅")

# ===================================================================
# ⚠️ 14. МОДЕРАЦІЯ (WARN / MUTE / UNMUTE / BAN / KICK)
# ===================================================================
@bot.message_handler(commands=['warn'])
def warn_user(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        bot.reply_to(message, "Тільки для адмінів!")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера.")
        return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns = warns + 1 WHERE user_id = ?", (target.id,))
        cursor.execute("SELECT warns FROM stats WHERE user_id = ?", (target.id,))
        warns = cursor.fetchone()
        conn.commit()
    warns_count = warns[0] if warns else 1
    if warns_count >= 3:
        try:
            until = int(time.time()) + 3600  # мут на 1 год
            bot.restrict_chat_member(message.chat.id, target.id,
                until_date=until,
                permissions=types.ChatPermissions(can_send_messages=False))
            bot.send_message(message.chat.id,
                f"⛔ <b>{target.first_name}</b> отримав 3 варни і замучений на 1 годину!",
                parse_mode="HTML")
        except Exception as e:
            bot.send_message(message.chat.id, f"Не зміг замутити: {e}")
    else:
        bot.send_message(message.chat.id,
            f"⚠️ <b>{target.first_name}</b> отримав варнінг ({warns_count}/3). Ще {3 - warns_count} — і мут!",
            parse_mode="HTML")

@bot.message_handler(commands=['unwarn'])
def unwarn_user(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера.")
        return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns = MAX(0, warns - 1) WHERE user_id = ?", (target.id,))
        conn.commit()
    bot.send_message(message.chat.id, f"✅ З <b>{target.first_name}</b> знято один варн.", parse_mode="HTML")

@bot.message_handler(commands=['mute'])
def mute_user(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера.")
        return
    target = message.reply_to_message.from_user
    parts = message.text.split()
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    until = int(time.time()) + minutes * 60
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            until_date=until,
            permissions=types.ChatPermissions(can_send_messages=False))
        bot.send_message(message.chat.id,
            f"🔇 <b>{target.first_name}</b> замучений на {minutes} хвилин.",
            parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Не зміг замутити: {e}")

@bot.message_handler(commands=['unmute'])
def unmute_user(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера.")
        return
    target = message.reply_to_message.from_user
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            permissions=types.ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True
            ))
        bot.send_message(message.chat.id,
            f"🔊 <b>{target.first_name}</b> розмучений. Можеш говорити, бро!",
            parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера.")
        return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        bot.send_message(message.chat.id,
            f"🚫 <b>{target.first_name}</b> забанений. Адьос!",
            parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['kick'])
def kick_user(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера.")
        return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        time.sleep(0.5)
        bot.unban_chat_member(message.chat.id, target.id)
        bot.send_message(message.chat.id,
            f"👟 <b>{target.first_name}</b> вилетів з групи. До побачення!",
            parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

# ===================================================================
# 🧠 15. АНАЛІЗ СТАТІ
# ===================================================================
def analyze_gender(text):
    try:
        prompt = f"Проаналізуй цей текст і визнач стать (Хлопець, Дівчина або Незрозуміло). Відповідай ТІЛЬКИ одним словом: {text}"
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception:
        return "Незрозуміло"

# ===================================================================
# ❓ 16. ДОПОМОГА
# ===================================================================
@bot.message_handler(commands=['help', 'start'])
def show_help(message):
    text = """
🤖 <b>Драго — список команд:</b>

<b>💬 Спілкування:</b>
• Напиши "Драго" або реплай — Драго відповість

<b>🎮 Розваги:</b>
• <code>.мем</code> — рандомний мем
• <code>.дуель @юзер</code> — дуель з кимось
• <code>.факт</code> — цікавий факт
• <code>.анекдот</code> / <code>.жарт</code> — смішний жарт
• <code>.астрологія [знак]</code> — гороскоп
• <code>/game</code> — гра в слова
• <code>/stop</code> — зупинити гру

<b>📊 Статистика:</b>
• <code>.топ</code> — топ балакунів сьогодні
• <code>.профіль</code> — твій профіль
• <code>/stats</code> — статистика групи (адміни)

<b>🛠️ Утиліти:</b>
• <code>/generate [опис]</code> — генерація зображення
• <code>/погода [місто]</code> — погода
• <code>/translate [мова] [текст]</code> — переклад
• <code>/summarize</code> (реплай) — стиснути текст

<b>⚠️ Модерація (адміни):</b>
• <code>/warn</code> — варнінг (3 = мут 1 год)
• <code>/unwarn</code> — зняти варн
• <code>/mute [хвилини]</code> — замутити
• <code>/unmute</code> — розмутити
• <code>/ban</code> — забанити
• <code>/kick</code> — вигнати
"""
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# ===================================================================
# 🎛️ 17. ЦЕНТРАЛЬНИЙ ДИСПЕТЧЕР
# ===================================================================
@bot.message_handler(content_types=['text'])
def main_handler(message):
    text = message.text
    chat_id = message.chat.id
    chat_type = message.chat.type
    user_id = message.from_user.id
    name = message.from_user.first_name

    # Оновлення статистики
    update_message_count(user_id, name, chat_id)

    # Антиспам
    if chat_type in ['group', 'supergroup'] and not is_admin(bot, chat_id, user_id):
        if check_flood(user_id, chat_id):
            try:
                until = int(time.time()) + MUTE_DURATION
                bot.restrict_chat_member(chat_id, user_id,
                    until_date=until,
                    permissions=types.ChatPermissions(can_send_messages=False))
                bot.send_message(chat_id,
                    f"⚡ <b>{name}</b>, стоп-стоп-стоп! Флуд виявлено — мут на 5 хвилин.",
                    parse_mode="HTML")
            except Exception as e:
                logger.error(f"Антиспам помилка: {e}")
            return

    # Антимат (тільки попередження, не видаляємо)
    if has_bad_words(text) and chat_type in ['group', 'supergroup']:
        if not is_admin(bot, chat_id, user_id):
            bot.reply_to(message, f"Ей, <b>{name}</b>, полегше з лексикою! Ще раз — варн.", parse_mode="HTML")

    # Гра в слова — пріоритет
    if chat_id in game_state:
        handle_word_game(message)
        return

    # Аналіз статі (якщо ще не вказано)
    with db_lock:
        cursor.execute("SELECT gender FROM stats WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
    if result and result[0] == 'не вказано':
        gender_guess = analyze_gender(text)
        if gender_guess in ['Хлопець', 'Дівчина']:
            with db_lock:
                cursor.execute("UPDATE stats SET gender = ? WHERE user_id = ?", (gender_guess, user_id))
                conn.commit()
            bot.send_message(chat_id, f"Драго вирішив, що ти — {gender_guess.lower()}. Вгадав? 😎")

    # Стандартний діалог
    is_mentioned = False
    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'драго,', 'джарвіс', 'джарвіс,']
        first_word = text.split()[0].lower() if text.split() else ""
        if (first_word in trigger_words or
            f"@{bot.get_me().username}" in text or
            (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id)):
            is_mentioned = True
            for word in trigger_words:
                if text.lower().startswith(word):
                    text = text[len(word):].strip()
                    break
    else:
        is_mentioned = True

    if not is_mentioned:
        return

    status_msg = None
    try:
        bot.send_chat_action(chat_id, 'typing')
        status_msg = bot.reply_to(message, "Йде відправка даних в СБУ... 👮‍♂️")
        gemini_chat = get_gemini_chat(chat_id)
        response = gemini_chat.send_message(text)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                text=response.text, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text)
    except genai.types.generation_types.BlockedPromptException:
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                text="Оу, цей запит заблоковано Google. Навіть я про таке не скажу. 🤐")
    except Exception as e:
        logger.error(f"Помилка діалогу: {e}")
        error_text = "Щось сервери прилягли, спробуй ще раз."
        if "ResourceExhausted" in str(e) or "quota" in str(e).lower():
            error_text = "Пригальмуй! Google каже почекати хвилину..."
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=error_text)
        else:
            bot.reply_to(message, error_text)

# ===================================================================
# 🚀 ЗАПУСК
# ===================================================================
threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == "__main__":
    logger.info("DRAGO BOT ЗАПУЩЕНИЙ!")
    print("=========================================")
    print("   DRAGO BOT — ПОВНА ВЕРСІЯ ЗАПУЩЕНА!   ")
    print("=========================================")
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
