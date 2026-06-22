import os
import re
import io
import time
import random
import logging
import requests
import threading
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from http.server import SimpleHTTPRequestHandler, HTTPServer
import telebot
from telebot import types
import google.generativeai as genai
from PIL import Image

# ===================================================================
# 📋 ЛОГУВАННЯ
# ===================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('drago_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ])
logger = logging.getLogger(__name__)

# ===================================================================
# 💾 БАЗА ДАНИХ
# ===================================================================
conn    = sqlite3.connect('drago_bot.db', check_same_thread=False)
cursor  = conn.cursor()
db_lock = threading.Lock()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS stats (
    user_id      INTEGER PRIMARY KEY,
    name         TEXT,
    count        INTEGER DEFAULT 0,
    gender       TEXT    DEFAULT 'не вказано',
    warns        INTEGER DEFAULT 0,
    coins        INTEGER DEFAULT 100,
    rep          INTEGER DEFAULT 0,
    last_bonus   INTEGER DEFAULT 0,
    last_lottery INTEGER DEFAULT 0,
    city         TEXT    DEFAULT '');

CREATE TABLE IF NOT EXISTS daily_stats (
    user_id INTEGER,
    chat_id INTEGER,
    date    TEXT,
    count   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date));

CREATE TABLE IF NOT EXISTS reminders (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER,
    chat_id   INTEGER,
    remind_at INTEGER,
    text      TEXT,
    done      INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS trivia (
    chat_id  INTEGER PRIMARY KEY,
    question TEXT,
    answer   TEXT,
    active   INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS confessions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    text    TEXT,
    created INTEGER);

CREATE TABLE IF NOT EXISTS achievements (
    user_id     INTEGER,
    achievement TEXT,
    earned_at   INTEGER,
    PRIMARY KEY (user_id, achievement));
""")
conn.commit()

# ===================================================================
# ⚙️ НАЛАШТУВАННЯ
# ===================================================================
TELEGRAM_TOKEN  = '8788139276:AAGKr6sFii4n9B1E5sysHSa-xMTgYsmUZfI'
GEMINI_API_KEY  = 'AIzaSyC_7U44ek_eaN0u6GV4FqL-m1N9OcpvVJM'
WEATHER_API_KEY = 'ТВІЙ_КЛЮЧ_OPENWEATHER'

FLOOD_LIMIT   = 5
FLOOD_TIME    = 10
MUTE_DURATION = 300

BAD_WORDS = [
    'хуй','піздець','пізда','єбать','їбать',
    'блять','сука','нігер','гандон','залупа',
    'мудак','пидор','пидорас']

ZODIAC_SIGNS = {
    'овен':'Aries','телець':'Taurus','близнюки':'Gemini',
    'рак':'Cancer','лев':'Leo','діва':'Virgo',
    'терези':'Libra','скорпіон':'Scorpio','стрілець':'Sagittarius',
    'козоріг':'Capricorn','водолій':'Aquarius','риби':'Pisces'}

AUTO_REACTIONS = {
    'пиво':      ["🍺 Налий і мені!", "Пиво — це рідкий хліб. Поживно! 🍺"],
    'їжа':       ["А мені нічого не принесли 😤", "Замовляй, я слідкую 👀"],
    'сон':       ["Сон — це смерть для слабаків 💀", "Поспи, може розумнішим прокинешся 😴"],
    'гроші':     ["Де гроші, Лебовскі?! 💸", "Гроші — зло. Але без зла нудно."],
    'любов':     ["Ніжності?! Від Драго?! Ха! ❤️", "Любов — це дофамін і серотонін. Буквально хімія."],
    'понеділок': ["Понеділок — день важкий. Як і всі інші. 😈"],
    'відпустка': ["Відпустка?! А хто буде страждати на роботі? 🏖️"],
    'гпт':       ["Той ChatGPT — просто мій менший брат. Я крутіший. 😎"],
    'нудно':     ["Нудно? Пограй у !фортуна або !кості. Або просто поговори зі мною 🤖"],
}

ACHIEVEMENTS = {
    'перше_повідомлення': ('🌱', 'Перші кроки', 'Надіслав перше повідомлення'),
    'сто_повідомлень':    ('💬', 'Балакун',      '100 повідомлень у чаті'),
    'тисяча_повідомлень': ('🗣️', 'Легенда чату', '1000 повідомлень!'),
    'мільйонер':          ('💰', 'Мільйонер',    'Накопичив 1000 монет'),
    'везунчик':           ('🍀', 'Везунчик',     'Виграв джекпот у казино'),
    'перший_бонус':       ('🎁', 'Халявник',     'Забрав перший щоденний бонус'),
    'рапер':              ('🎤', 'Рапер',         'Переміг у рап-батлі'),
    'добра_людина':       ('😇', 'Добра людина', 'Дав +репутацію іншому'),
    'токсик':             ('☢️', 'Токсик',        'Отримав 3 варни'),
}

# ===================================================================
# 🤖 ІНІЦІАЛІЗАЦІЯ
# ===================================================================
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

generation_config = {"max_output_tokens": 2048, "temperature": 0.85}
safety_settings = [
    {
safety_settings
"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=(
        
    generation_config=generation_config,

    generation_
"Ти — зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
        "Спілкуйся виключно українською мовою. Використовуй молодіжний сленг, "
        "іноді нецензурну лексику для емоційності. Будь живим, неформальним, "
        "злегка іронізуй, підколюй юзера, але завжди допомагай. "
        "Завжди закінчуй думки! Пиши коротко і ясно!"
    ))

bot_chats   = {}
flood_data  = defaultdict(
    ))

bot_ch

    ))

bot_

    ))

bot
list)
game_state  = {}
rap_pending = {}



game_state
# ✅ ВИПРАВЛЕНО: тепер ключ = (chat_id, user_id) — кожен юзер має свою розмову
def get_gemini_chat(chat_id, user_id):
    key = (chat_id, user_id)
    
    key = (chat_id, user
if key not in bot_chats:
        bot_chats[key] = model.start_chat(history=[])
    return bot_chats[key]


# ===================================================================
# 🛠️ УТИЛІТИ
# ===================================================================
def is_admin(chat_id, user_id):
    try:
        return bot.get_chat_member(chat_id, user_id).status in ['administrator', 'creator']
    except Exception:
        return False

def has_bad_words(text):
    return any(w in text.lower() for w in BAD_WORDS)

def check_flood(user_id, chat_id):
    now  = time.time()
    key  = (user_id, chat_id)
    flood_data[key] = [t 
    now  = time.time()
    key  = (user_id, chat_id)
    flood_data[key] = [

    now  
for t in flood_data[key] if now - t < FLOOD_TIME]
    flood_data[key].append(now)
    return len(flood_data[key]) > FLOOD_LIMIT

def get_coins(user_id):
    with db_lock:
        cursor.execute("SELECT coins FROM stats WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

def add_coins(user_id, amount):
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, 'Unknown')", (user_id,))
        cursor.execute("UPDATE stats SET coins=MAX(0, coins+?) WHERE user_id=?", (amount, user_id))
        conn.commit()

def update_message_count(user_id, name, chat_id):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)", (user_id, name))
        cursor.execute("UPDATE stats SET count=count+1, name=?, coins=coins+1 WHERE user_id=?", (name, user_id))
        cursor.execute(
            
        cursor.execute(
"INSERT INTO daily_stats (user_id,chat_id,date,count) VALUES(?,?,?,1) "
            "ON CONFLICT(user_id,chat_id,date) DO UPDATE SET count=count+1",
            (user_id, chat_id, today))
        conn.commit()
    check_achievements(user_id, chat_id)


            (user_id, chat_id, today))
        conn.commit()
def give_achievement(user_id, key, chat_id):
    with db_lock:
        cursor.execute("SELECT 1 FROM achievements WHERE user_id=? AND achievement=?", (user_id, key))
        if cursor.fetchone():
            return False
        cursor.execute("INSERT INTO achievements (user_id, achievement, earned_at) VALUES(?,?,?)",
                       (user_id, key, 
                       (user_id, key,
int(time.time())))
        conn.commit()
    emoji, title, desc = ACHIEVEMENTS[key]
    try:
        with db_lock:
            cursor.execute("SELECT name FROM stats WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        name = row[
            row = cursor.
0] if row else "Хтось"
        bot.send_message(chat_id,
            f"🏅 <b>ДОСЯГНЕННЯ!</b>\n\n"
            f"{emoji} <b>{title}</b>\n"
            f"<i>{desc}</i>\n\n"
            f"Отримав: <b>{name}</b> 🎉",
            parse_mode="HTML")
    except Exception:
        pass
    return True

def check_achievements(user_id, chat_id):
    with db_lock:
        cursor.execute("SELECT count, coins FROM stats WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    if not row:
        return
    count, coins = row
    
    
if count == 1:
        threading.Thread(target=give_achievement, args=(user_id, 'перше_повідомлення', chat_id), daemon=True).start()
    if count >= 100:
        threading.Thread(target=give_achievement, args=(user_id, 'сто_повідомлень', chat_id), daemon=True).start()
    if count >= 1000:
        threading.Thread(target=give_achievement, args=(user_id, 'тисяча_повідомлень', chat_id), daemon=True).start()
    if coins >= 1000:
        threading.Thread(target=give_achievement, args=(user_id, 'мільйонер', chat_id), daemon=True).start()

def run_dummy_server():
    port  = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(("", port), SimpleHTTPRequestHandler)
    httpd.serve_forever()

def analyze_gender(text):
    try:
        resp = model.generate_content(
            f"Визнач стать (Хлопець/Дівчина/Незрозуміло). Тільки одне слово: {text}")
        return resp.text.strip()
    except Exception:
        return "Незрозуміло"

# ===================================================================
# 👤 СТАТЬ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!стать'))
def set_gender(message):
    parts = message.text.split()
    
    parts = message.text.split()
if len(parts) < 2:
        bot.reply_to(message,
            "Вкажи стать:\n<code>!стать хлопець</code>\n<code>!стать дівчина</code>",
            parse_mode="HTML")
        return
    g_input = parts[1].lower()
    if g_input in ['хлопець', 'хлоп', 'м', 'чол', 'boy', 'male', 'чоловік']:
        gender = 'Хлопець'
    elif g_input in ['дівчина', 'дів', 'ж', 'жін', 'girl', 'female', 'жінка']:
        gender = 'Дівчина'
    else:
        bot.reply_to(message, "Не зрозумів. Напиши: !стать хлопець або !стать дівчина")
        return
    uid = message.from_user.
    uid = message.from_user.
id
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)",
                       (uid, message.from_user.first_name))
        cursor.execute("UPDATE stats SET gender=? WHERE user_id=?", (gender, uid))
        conn.commit()
    bot.reply_to(message, f"✅ Стать збережена: <b>{gender}</b>", parse_mode="HTML")

# ===================================================================
# 🏙️ МІСТО
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!місто'))
def set_city(message):
    parts = message.text.split(' ', 1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Вкажи місто: <code>!місто Київ</code>", parse_mode="HTML")
        return
    city = parts[1].strip()
    uid  = message.from_user.id
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)",
                       (uid, message.from_user.first_name))
        cursor.execute(
                       
"UPDATE stats SET city=? WHERE user_id=?", (city, uid))
        conn.commit()
    bot.reply_to(message,
        f"✅ Місто збережено: <b>{city}</b>\nТепер <code>!погода</code> без параметрів покаже твоє місто!",
        parse_mode="HTML")

# ===================================================================
# 🏅 ДОСЯГНЕННЯ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!нагороди')
def show_achievements(message):
    uid  = message.from_user.id
    name = message.from_user.first_name
    with db_lock:
        cursor.execute("SELECT achievement FROM achievements WHERE user_id=? ORDER BY earned_at", (uid,))
        rows = cursor.fetchall()
    if not rows:
        bot.reply_to(message,
            f"У <b>{name}</b> ще немає досягнень. Пиши більше, грай, будь активним!",
            parse_mode="HTML")
        return
    text = f"🏅 <b>Досягнення {name}:</b>\n\n"
    for (key,) in rows:
        if key in ACHIEVEMENTS:
            emoji, title, desc = ACHIEVEMENTS[key]
            text += f"{emoji} <b>{title}</b> — <i>{desc}</i>\n"
    total = len(ACHIEVEMENTS)
    earned = len(rows)
    text += f"\n<b>{earned}/{total}</b> досягнень отримано"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# ===================================================================
# 🌡️ ПОГОДА
# ===================================================================
@bot.message_handler(func=lambda m: m.text and re.match(r'^!погода(\s+.*)?$', m.text.strip().lower()))
def get_weather(message):
    parts = message.text.split(' ', 1)
    city  = parts[1].strip() if len(parts) > 1 else ''
    if not city:
        with db_lock:
            cursor.execute("SELECT city FROM stats WHERE user_id=?", (message.from_user.id,))
            row = cursor.fetchone()
        city = row[0] if row and row[0] else ''
    if not city:
        bot.reply_to(message,
            "Вкажи місто: <code>!погода Київ</code>\n"
            "Або збережи своє місто: <code>!місто Київ</code>",
            parse_mode="HTML")
        return
    try:
        d = requests.get(
            f"http://api.openweathermap.org/data/2.5/weather"
            f"?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ua",
            timeout=10).json()
        if d.get('cod') != 200:
            bot.reply_to(message, f"Не знайшов місто '{city}'."); return
        temp     = d['main']['temp']
        feels    = d['main']['feels_like']
        desc     = d['weather'][0]['description']
        humidity = d[
        humidity
'main']['humidity']
        wind     = d['wind']['speed']
        w_id = d['weather'][0]['id']
        if w_id < 300:    w_emoji = "⛈️"
        elif w_id < 500:  w_emoji = "🌦️"
        elif w_id < 600:  w_emoji = "🌧️"
        elif w_id < 700:  w_emoji = "❄️"
        elif w_id < 800:  w_emoji = "🌫️"
        elif w_id == 800: w_emoji = "☀️"
        else:              w_emoji = "⛅"
        bot.send_message(message.chat.id,
            f"{w_emoji} <b>Погода в {city}</b>\n\n"
            f"🌡 {temp:.1f}°C (відчув. {feels:.1f}°C)\n"
            f"☁️ {desc}\n"
            f"💧 Вологість: {humidity}%\n"
            f"💨 Вітер: {wind} м/с",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Weather: {e}")
        bot.reply_to(message, "Помилка погоди. Перевір API ключ.")

# ===================================================================
# 🎁 ЩОДЕННИЙ БОНУС
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!бонус')
def daily_bonus(message):
    uid  = message.from_user.id
    now  = int(time.time())
    with db_lock:
        cursor.execute("SELECT last_bonus FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
    last = row[0] if row else 0
    if now - last < 86400:
        remaining = 86400 - (now - last)
        h = remaining // 3600
        m_ = (remaining % 3600) // 60
        bot.reply_to(message,
            f"⏳ Бонус вже забрав! Наступний через <b>{h}год {m_}хв</b>", parse_mode="HTML")
        return
    bonus = random.randint(50, 200)
    add_coins(uid, bonus)
    with db_lock:
        cursor.execute("UPDATE stats SET last_bonus=? WHERE user_id=?", (now, uid))
        conn.commit()
    give_achievement(uid, 'перший_бонус', message.chat.id)
    bot.send_message(message.chat.id,
        f"🎁 <b>{message.from_user.first_name}</b> забрав щоденний бонус!\n"
        f"+<b>{bonus} 🪙</b> | Баланс: {get_coins(uid)} 🪙",
        parse_mode="HTML")

# ===================================================================
# 🎟️ ЛОТЕРЕЯ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!лотерея')
def daily_lottery(message):
    uid = message.from_user.id
    now = int(time.time())
    with db_lock:
        cursor.execute("SELECT last_lottery FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
    last = row[0] if row else 0
    if now - last < 86400:
        remaining = 86400 - (now - last)
        h = remaining // 3600
        bot.reply_to(message,
            f"⏳ Вже брав участь! Наступна через <b>{h} год</b>", parse_mode="HTML")
        return
    with db_lock:
        cursor.execute(
        cursor
"UPDATE stats SET last_lottery=? WHERE user_id=?", (now, uid))
        conn.commit()
    r = random.random()
    if r < 0.05:
        prize = 1000; text = f"💥 <b>ДЖЕКПОТ!!!</b> +{prize} 🪙"
        give_achievement(uid, 'везунчик', message.chat.id)
    elif r < 0.20:
        prize = 300;  text = f"🌟 <b>Великий виграш!</b> +{prize} 🪙"
    elif r < 0.50:
        prize = 100;  text = f"✅ <b>Виграш</b> +{prize} 🪙"
    else:
        prize = 0;    text = f"😢 <b>{message.from_user.first_name}</b> нічого не виграв."
    if prize > 0:
        add_coins(uid, prize)
    bot.send_message(message.chat.id,
        f"🎟️ <b>{message.from_user.first_name}</b> бере участь у лотереї...\n\n{text}",
        parse_mode="HTML")

# ===================================================================
# ⭐ РЕПУТАЦІЯ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip() in ['!+', '!-'])
def change_rep(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера!"); return
    target = message.reply_to_message.from_user
    giver  = message.from_user
    if target.id == giver.id:
        bot.reply_to(message, "Собі репутацію? Нарцис! 😄"); return
    if target.is_bot:
        bot.reply_to(message, "Боту репутацію? 🤖"); return
    change = +1 if message.text.strip() == '!+' else -1
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)",
                       (target.id, target.first_name))
        cursor.execute("UPDATE stats SET rep=rep+? WHERE user_id=?", (change, target.id))
        cursor.execute("SELECT rep FROM stats WHERE user_id=?", (target.id,))
        new_rep = cursor.fetchone()[0]
        conn.commit()
    emoji  = "⬆️" if change > 0 else "⬇️"
    action = "підвищив" if change > 0 else "понизив"
    if change > 0:
        give_achievement(giver.id, 'добра_людина', message.chat.id)
    bot.send_message(message.chat.id,
        f"{emoji} <b>{giver.first_name}</b> {action} репутацію <b>{target.first_name}</b>\n"
        f"Репутація: <b>{new_rep}</b>",
        parse_mode="HTML")

# ===================================================================
# 🎤 РАП-БАТЛ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!батл'))
def rap_battle_challenge(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення суперника!"); return
    challenger = message.from_user
    opponent   = message.reply_to_message.from_user
    if challenger.id == opponent.id:
        bot.reply_to(message, "Сам з собою батл? 😂"); return
    if opponent.is_bot:
        bot.reply_to(message, "Бот не рапує (поки що) 🎤"); return
    rap_pending[message.chat.id] = {
        'challenger_id':   challenger.id,
        'challenger_name': challenger.first_name,
        'opponent_id':     opponent.id,
        'opponent_name':   opponent.first_name,
        'stage':           'waiting_accept'
    }
    bot.send_message(message.chat.
    }
    bot.send_message(message.chat
id,
        f"🎤 <b>{challenger.first_name}</b> кидає виклик → <b>{opponent.first_name}</b>!\n\n"
        f"<code>!прийняти</code> — прийняти\n<code>!відмовити</code> — відмовитись",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!прийняти')
def rap_accept(message):
    cid = message.chat.id
    if cid not in rap_pending:
        bot.reply_to(message, 
        bot.reply_to(message

        
"Немає активного виклику."); return
    battle = rap_pending[cid]
    if message.from_user.id != battle['opponent_id']:
        bot.reply_to(message, "Не тебе викликали! 😏"); return
    rap_pending[cid]['stage'] = 'waiting_rap1'
    bot.send_message(cid,
        f"⚔️ <b>РАП-БАТЛ!</b>\n\n"
        f"<b>{battle['challenger_name']}</b> — напиши свій реп (реплай на це):",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!відмовити')
def rap_decline(message):
    cid = message.chat.id
    if cid not in rap_pending: return
    battle = rap_pending.pop(cid)
    
    battle = rap_pending.pop(cid)
    
if message.from_user.id != battle['opponent_id']: return
    bot.send_message(cid,
        f"🏳️ <b>{battle['opponent_name']}</b> злякався рапу! Боягуз! 😂",
        parse_mode="HTML")

# ===================================================================
# 🤫 КОНФЕСІЯ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!конфесія'))
def confession(message):
    text_ = message.text[9:].strip()
    if not text_:
        bot.reply_to(message, 
        bot
"Напиши текст! !конфесія [текст]"); return
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    with db_lock:
        cursor.execute("INSERT INTO confessions (chat_id, text, created) VALUES (?,?,?)",
                       (message.chat.id, text_, int(time.time())))
        conn.commit()
    bot.send_message(message.chat.
        conn.commit()
    bot.send

        conn.commit()
id,
        f"🤫 <b>АНОНІМНА КОНФЕСІЯ:</b>\n\n<i>{text_}</i>", parse_mode="HTML")

# ===================================================================
# 🔮 НОВІ КРУТІ ФУНКЦІЇ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!прогноз')
def day_forecast(message):
    name = message.from_user.first_name
    try:
        resp = model.generate_content(
            
        resp = model.generate_content(

        resp = model.
f"Персональний прогноз дня для {name} в стилі Драго — іронічний, з підколками. "
            f"Включи: щастя, кохання, гроші, роботу. 4 речення.")
        bot.send_message(message.chat.
        bot.send
id,
            f"🔮 <b>Прогноз для {name}:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Кристальна куля затуманилась.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!роль')
def random_role(message):
    roles = [
        "👑 Король чату", "🤡 Клоун дня", "🧠 Найрозумніший",
        "💀 Зомбі чату", "🦁 Альфа самець", "🐑 Вівця стада",
        "🕵️ Секретний агент", "🌟 Зірка вечора", "🍕 Людина-піца",
        "😴 Соня дня", "🦊 Хитрий лис", "🐢 Черепаха-мудрець",
        "🎸 Рок-зірка", "📚 Ходяча енциклопедія", "🤖 Робот-симулятор",
    ]
    bot.send_message(message.chat.
    ]
    bot.send_message(message.

    
id,
        f"🎭 Сьогодні <b>{message.from_user.first_name}</b> — це...\n\n<b>{random.choice(roles)}</b>!",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!куля'))
def magic_ball(message):
    question = message.text[5:].strip()
    if not question:
        bot.reply_to(message, 
        bot.
"Задай питання! !куля Чи пощастить мені?"); return
    answers = [
        "🟢 Так, однозначно!", "🟢 Все вказує на так!", "🟢 Безперечно!",
        "🟢 Можеш розраховувати на це!", "🟡 Спитай пізніше...",
        "🟡 Важко сказати зараз.", "🟡 Не визначено.",
        "🔴 Не розраховуй на це.", "🔴 Відповідь — ні.",
        "🔴 Навіть не мрій!", "😂 Серйозно?! ТИ ЗАДАЄШ ЦЕ ПИТАННЯ?!",
    ]
    bot.send_message(message.chat.id,
        f"🔮 <b>Питання:</b> {question}\n\n<b>{random.choice(answers)}</b>",
        parse_mode=
        parse_mode
"HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!голос'))
def quick_poll(message):
    text_ = message.text[
    text
6:].strip()
    if not text_:
        bot.reply_to(message, "!голос Хто найкрутіший?"); return
    try:
        bot.send_poll(message.chat.
        bot.send_poll

        bot.send
id, question=text_[:300],
                      options=[
                      options=
"👍 Так", "👎 Ні", "🤷 Все рівно"],
                      is_anonymous=False)
    except Exception as e:
        bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!хто_кращий'))
def who_is_better(message):
    parts = message.text[11:].strip().split(' vs ')
    if len(parts) < 2:
        bot.reply_to(message, "Формат: !хто_кращий Кіт vs Собака"); return
    a, b     = parts[0].strip(), parts[1].strip()
    score_a  = random.randint(0, 100)
    score_b  = 100 - score_a
    winner   = a 
    winner   = a

    winner
if score_a > score_b else b
    bot.send_message(message.chat.id,
        f"⚖️ <b>{a}</b> vs <b>{b}</b>\n\n"
        
        f
f"📊 {a}: {score_a}%\n📊 {b}: {score_b}%\n\n"
        f"🏆 Переможець: <b>{winner}</b>!",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!цитата')
def motivational_quote(message):
    try:
        style = random.choice(['мотиваційна', 'демотиваційна і саркастична', 'філософська', 'смішна'])
        resp  = model.generate_content(
            f"Придумай коротку {style} цитату українською. "
            f"Тільки цитата і вигаданий автор. Без зайвого тексту.")
        bot.send_message(message.chat.id, f"💬 <i>{resp.text}</i>", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, 
        
"Муза покинула Драго.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!токсик')
def toxicity_level(message):
    target_name = (message.reply_to_message.from_user.first_name
                   
    target_name = (message.reply

    target_name = (
if message.reply_to_message else message.from_user.first_name)
    level = random.randint(0, 100)
    if level < 20:    emoji, desc = "😇", "Майже святий. Підозріло."
    elif level < 40:  emoji, desc = "😊", "Нормальний юзер. Рідкість."
    elif level < 60:  emoji, desc = "😏", "Середньо токсичний."
    elif level < 80:  emoji, desc = "😤", "Досить токсичний! Обережно!"
    else:             emoji, desc = "☢️", "НЕБЕЗПЕЧНИЙ РІВЕНЬ ТОКСИЧНОСТІ!!!"
    bar = "█" * (level // 10) + "░" * (10 - level // 10)
    bot.send_message(message.chat.
    bot.send_message
id,
        f"{emoji} <b>Токсичність {target_name}:</b>\n\n"
        f"[{bar}] {level}%\n\n<i>{desc}</i>",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!любов'))
def love_percent(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера! !любов"); return
    name1  = message.from_user.first_name
    name2  = message.reply_to_message.from_user.first_name
    seed   = 
    name1  = message.from_user.first_name
    name2  = message.reply_to

    name1  = message.from_user.first_name
    name2
abs(hash(f"{min(name1, name2)}{max(name1, name2)}")) % 101
    level  = seed
    if level < 20:    emoji, desc = "💔", "Нічого спільного. Забудь."
    elif level < 40:  emoji, desc = "💛", "Дружба — і не більше."
    elif level < 60:  emoji, desc = "🧡", "Симпатія є, але поки нічого серйозного."
    elif level < 80:  emoji, desc = "❤️", "Є іскра! Діяти потрібно!"
    else:             emoji, desc = "💘", "ІДЕАЛЬНА ПАРА!! Одружуйтесь вже!"
    bar = "█" * (level // 10) + "░" * (10 - level // 10)
    bot.send_message(message.chat.id,
        f"💘 <b>{name1}</b> + <b>{name2}</b>\n\n"
        f"[{bar}] {level}%\n\n{emoji} <i>{desc}</i>",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!фортуна')
def roulette(message):
    outcomes = [
        ("💀 Мут на 1 хвилину!", "mute", 60),
        ("🎁 +20 монет!", "coins", 20),
        ("💸 -10 монет!", "coins", -10),
        ("🌟 +50 монет!", "coins", 50),
        ("🤡 Нічого. Просто лох.", "none", 0),
        ("👑 +100 монет!", "coins", 100),
        ("🔇 Мут 30 секунд!", "mute", 30),
        ("🎉 +5 монет. Хоч щось.", "coins", 5),
    ]
    o   = random.choice(outcomes)
    uid = message.from_user.
    ]
    o   = random.choice(outcomes)
    uid

    ]
    o   = random.choice(outcomes)
id
    bot.send_message(message.chat.id,
        f"🎰 <b>{message.from_user.first_name}</b> крутить колесо фортуни...\n\n{o[0]}",
        parse_mode="HTML")
    if o[1] == "coins":
        add_coins(uid, o[
        add
2])
    elif o[1] == "mute":
        try:
            bot.restrict_chat_member(message.chat.
            bot.restrict_chat_member(message.chat.
id, uid,
                until_date=int(time.time()) + o[2],
                permissions=types.ChatPermissions(can_send_messages=False))
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!монетка')
def coin_flip(message):
    bot.reply_to(message,
        
    bot.reply_to(message,

    bot.reply
f"🪙 Підкидаю...\n\n<b>{random.choice(['🦅 Орел!', '🔵 Решка!'])}</b>",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and re.match(r'^!кості(\s+\d+)?$', m.text.strip().lower()))
def roll_dice(message):
    parts = message.text.strip().split()
    sides = 
    parts = message.text.strip().split()
    sides
int(parts[1]) if len(parts) > 1 else 6
    sides = max(2, min(sides, 1000))
    bot.reply_to(message,
        
    bot.reply
f"🎲 Кубик d{sides}...\n\n<b>{random.randint(1, sides)}</b>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!пчд')
def truth_or_dare(message):
    truths = [
        "Яка найдурніша річ, яку ти коли-небудь робив?",
        "Кого з чату вважаєш найрозумнішим?",
        "Який твій найбільший страх?",
        "Що найгірше казав про когось за спиною?",
    ]
    dares = [
        "Напиши комплімент кожному в чаті!",
        "Відправ голосове з піснею (хоча б 10 сек).",
        "Розкажи щось про себе, чого ніхто не знає.",
        "Скажи щось приємне адміну.",
    ]
    if random.random() > 0.5:
        bot.send_message(message.chat.
        bot.send

        
id,
            f"🤔 <b>ПРАВДА:</b>\n\n{random.choice(truths)}", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id,
            f"😈 <b>ДІЛО:</b>\n\n{random.choice(dares)}", parse_mode="HTML")

# ===================================================================
# 📊 СТАТИСТИКА
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!хто')
def show_top(message):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        cursor.execute("""
            SELECT s.name, ds.count FROM daily_stats ds
            JOIN stats s ON s.user_id=ds.user_id
            WHERE ds.chat_id=? AND ds.date=?
            ORDER BY ds.count DESC LIMIT 10
        """, (message.chat.id, today))
        rows = cursor.fetchall()
    if not rows:
        bot.reply_to(message, 
        bot.
"Сьогодні тиша..."); return
    medals = [
    medals
'🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    text   = "📊 <b>Топ балакунів сьогодні:</b>\n\n"
    for i, (name, count) in enumerate(rows):
        text += f"{medals[i]} <b>{name}</b> — {count} повідомлень\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!я')
def show_profile(message):
    uid = message.from_user.
    uid = message.from_user.
id
    with db_lock:
        cursor.execute("SELECT count,gender,warns,coins,rep,city FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
        cursor.execute(
        row = cursor.fetchone()
        

        row = cursor.fetchone()
"SELECT COUNT(*) FROM achievements WHERE user_id=?", (uid,))
        ach_count = cursor.fetchone()[
        ach_count = cursor.fetchone()[
0]
    if not row:
        bot.reply_to(message, "Ти не в базі. Пиши більше!"); return
    count, gender, warns, coins, rep, city = row
    rep_emoji  = 
    count, gender, warns, coins, rep, city = row
    rep_emoji  
"⭐" if rep > 0 else ("💀" if rep < 0 else "😐")
    city_text  = f"🏙️ Місто: <b>{city}</b>\n" if city else ""
    bot.send_message(message.chat.id,
        
        f
f"👤 <b>Профіль {message.from_user.first_name}</b>\n\n"
        f"💬 Повідомлень: <b>{count}</b>\n"
        f"🚻 Стать: <b>{gender}</b>\n"
        
        f
f"{city_text}"
        f"🪙 Монети: <b>{coins}</b>\n"
        f"{rep_emoji} Репутація: <b>{rep}</b>\n"
        f"⚠️ Варни: <b>{warns}/3</b>\n"
        f"🏅 Досягнень: <b>{ach_count}/{len(ACHIEVEMENTS)}</b>",
        parse_mode="HTML")

@bot.message_handler(commands=['д_зведення'])
def show_group_stats(message):
    
    
if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "Тільки для адмінів!"); return
    with db_lock:
        cursor.execute("SELECT COUNT(*),SUM(count),SUM(coins) FROM stats")
        tu, tm, tc = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender='Хлопець'")
        boys  = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender='Дівчина'")
        girls = cursor.fetchone()[
        girls = cursor.fetchone()[
0]
        cursor.execute("SELECT COUNT(*) FROM achievements")
        ach   = cursor.fetchone()[0]
    bot.send_message(message.chat.id,
        f"📈 <b>Статистика групи</b>\n\n"
        f"👥 Юзерів: <b>{tu}</b>\n"
        f"💬 Повідомлень: <b>{tm or 0}</b>\n"
        f"🪙 Монет в обігу: <b>{tc or 0}</b>\n"
        f"👦 Хлопців: <b>{boys}</b>  👧 Дівчат: <b>{girls}</b>\n"
        f"🏅 Досягнень видано: <b>{ach}</b>",
        parse_mode=
        parse_
"HTML")

# ===================================================================
# 💰 ЕКОНОМІКА
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!гаманець')
def show_balance(message):
    coins = get_coins(message.from_user.id)
    bot.reply_to(message, 
    bot.reply
f"🪙 Твій баланс: <b>{coins} монет</b>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!дати'))
def transfer_coins(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай + !дати [сума]"); return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "Формат: !дати [сума] (реплай)"); return
    amount   = int(parts[1])
    sender   = message.from_user
    receiver = message.reply_to_message.from_user
    
    sender   = message.from_user
    receiver
if sender.id == receiver.id:
        bot.reply_to(message, "Собі? 😄"); return
    if get_coins(sender.id) < amount:
        bot.reply_to(message, 
        bot.
"Не вистачає монет!"); return
    add_coins(sender.id, -amount)
    add_coins(receiver.
    add
id,  amount)
    bot.send_message(message.chat.id,
        f"✅ <b>{sender.first_name}</b> → <b>{amount} 🪙</b> → <b>{receiver.first_name}</b>",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!ставка'))
def casino(message):
    parts = message.text.split()
    
    parts = message.text.split()
if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "!ставка [сума]"); return
    bet   = 
    bet   
int(parts[1])
    uid   = message.from_user.id
    coins = get_coins(uid)
    if coins < bet or bet < 1:
        bot.reply_to(message, 
        bot.
f"Недостатньо монет! ({coins} 🪙)"); return
    r = random.random()
    
    r
if r < 0.45:
        add_coins(uid, bet)
        bot.send_message(message.chat.id,
            
            
f"🎰 <b>ВИГРАШ!</b> +{bet} 🪙 | Баланс: {get_coins(uid)} 🪙", parse_mode="HTML")
    elif r < 0.50:
        win = bet * 
        win
4; add_coins(uid, win)
        give_achievement(uid, 
        give_
'везунчик', message.chat.id)
        bot.send_message(message.chat.
        bot.send_message
id,
            f"💥 <b>ДЖЕКПОТ!</b> +{win} 🪙 (x5) | Баланс: {get_coins(uid)} 🪙", parse_mode="HTML")
    else:
        add_coins(uid, -bet)
        bot.send_message(message.chat.
        add_coins(uid, -

        add
id,
            f"😢 Програв {bet} 🪙 | Баланс: {get_coins(uid)} 🪙", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!крамниця')
def show_shop(message):
    bot.send_message(message.chat.
    bot.send_message(message.chat.
id,
        "🏪 <b>Крамниця Драго:</b>\n\n"
        "🎭 VIP — 500 🪙 → /д_купити vip\n"
        "🔇 Мут юзера 1 год — 200 🪙 → /д_купити mute\n"
        "🎁 Секретний мем — 50 🪙 → /д_купити meme\n\n"
        "Монети: +1 за кожне повідомлення + !бонус щодня!",
        parse_mode="HTML")

@bot.message_handler(commands=['д_купити'])
def buy_item(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, 
        bot.reply_to
"Що купляємо? /д_купити vip | meme | mute"); return
    item  = parts[1].lower()
    uid   = message.from_user.id
    coins = get_coins(uid)
    if item == 'vip':
        if coins < 500:
            bot.reply_to(message, f"Треба 500 🪙, у тебе {coins}"); return
        add_coins(uid, -500)
        bot.reply_to(message, "✅ Купив VIP! Тепер ти офіційно крутіший 😎")
    elif item == 'meme':
        if coins < 50:
            bot.reply_to(message, f"Треба 50 🪙, у тебе {coins}"); return
        add_coins(uid, -50)
        try:
            resp = model.generate_content("Смішний мем-текст про Telegram чат. Коротко.")
            bot.reply_to(message, f"🎁 {resp.text}")
        except Exception:
            bot.reply_to(message, "🎁 'Коли купив VIP але нічого не змінилось'")
    elif item == 'mute':
        if coins < 200:
            bot.reply_to(message, f"Треба 200 🪙, у тебе {coins}"); return
        if not message.reply_to_message:
            bot.reply_to(message, "Реплай на юзера!"); return
        target = message.reply_to_message.from_user
        
        target = message.reply_to_

        target
try:
            add_coins(uid, -200)
            bot.restrict_chat_member(message.chat.id, target.id,
                until_date=int(time.time()) + 3600,
                permissions=types.ChatPermissions(can_send_messages=False))
            bot.send_message(message.chat.
            bot.send_message(
id,
                f"🔇 <b>{message.from_user.first_name}</b> купив мут для "
                f"<b>{target.first_name}</b> на 1 год!",
                parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"Не зміг: {e}")

# ===================================================================
# 🧠 ВІКТОРИНА
# ===================================================================
@bot.message_handler(commands=['д_загадка'])
def start_trivia(message):
    try:
        resp  = model.generate_content(
            "Придумай питання вікторини українською.\n"
            "Формат:\nПИТАННЯ: [текст]\nВІДПОВІДЬ: [відповідь]")
        lines = resp.text.strip().split('\n')
        q = a = None
        for line in lines:
            if line.startswith('ПИТАННЯ:'):    q = line.replace('ПИТАННЯ:', '').strip()
            elif line.startswith('ВІДПОВІДЬ:'): a = line.replace('ВІДПОВІДЬ:', '').strip().lower()
        if not q or not a: raise Exception("parse error")
        with db_lock:
            cursor.execute(
                "INSERT OR REPLACE INTO trivia (chat_id,question,answer,active) VALUES(?,?,?,1)",
                (message.chat.id, q, a))
            conn.commit()
        bot.send_message(message.chat.
            
id,
            f"🧠 <b>ВІКТОРИНА!</b>\n\n{q}\n\nПравильна відповідь = +50 🪙!\n"
            f"Скасувати: /д_стоп_загадка", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Trivia: {e}")
        bot.reply_to(message, "Не зміг придумати питання.")

@bot.message_handler(commands=['д_стоп_загадка'])
def stop_trivia(message):
    with db_lock:
        cursor.execute("UPDATE trivia SET active=0 WHERE chat_id=?", (message.chat.id,))
        conn.commit()
    bot.reply_to(message, 
        conn.commit()
"Вікторину скасовано.")

def check_trivia_answer(message):
    with db_lock:
        cursor.execute("SELECT question,answer FROM trivia WHERE chat_id=? AND active=1",
                       (message.chat.id,))
        row = cursor.fetchone()
    if not row: return False
    q, a = row
    ua   = message.text.lower().strip()
    if a in ua or ua in a:
        with db_lock:
            cursor.execute("UPDATE trivia SET active=0 WHERE chat_id=?", (message.chat.id,))
            conn.commit()
        add_coins(message.from_user.id, 50)
        bot.send_message(message.chat.id,
            f"✅ <b>{message.from_user.first_name}</b> відповів правильно!\n"
            f"Відповідь: <b>{a}</b> | +50 🪙", parse_mode="HTML")
        return True
    return False

# ===================================================================
# ⏰ НАГАДУВАННЯ
# ===================================================================
@bot.message_handler(commands=['д_памятка'])
def set_reminder(message):
    parts = message.text.split(' ', 2)
    if len(parts) < 3:
        bot.reply_to(message, 
        
"Формат: /д_памятка 30хв Зустріч"); return
    time_str = parts[1].lower(); text_r = parts[2]
    seconds  = 0
    if 'хв' in time_str or 'min' in time_str:
        seconds = int(re.sub(r'[^\d]', '', time_str) or 0) * 60
    elif 'год' in time_str or 'h' in time_str:
        seconds = int(re.sub(r'[^\d]', '', time_str) or 0) * 3600
    elif 'с' in time_str:
        seconds = int(re.sub(r'[^\d]', '', time_str) or 0)
    
    
if seconds < 1:
        bot.reply_to(message, "Не зрозумів час. Приклад: 30хв, 2год, 60с"); return
    with db_lock:
        cursor.execute(
            "INSERT INTO reminders (user_id,chat_id,remind_at,text) VALUES(?,?,?,?)",
            (message.from_user.id, message.chat.id, int(time.time()) + seconds, text_r))
        conn.commit()
    bot.reply_to(message,
        
        conn.commit()
    bot.reply_to(message,

        conn.commit()
    bot
f"✅ Нагадаю через <b>{str(timedelta(seconds=seconds))}</b>:\n<i>{text_r}</i>",
        parse_mode="HTML")

def reminder_worker():
    while True:
        now = int(time.time())
        with db_lock:
            cursor.execute(
                
            cursor.execute(
"SELECT id,user_id,chat_id,text FROM reminders WHERE remind_at<=? AND done=0",
                (now,))
            rows = cursor.fetchall()
        
                (now,))
            rows = cursor
for rid, uid, cid, text_r in rows:
            try:
                bot.send_message(cid,
                    f"⏰ <b>Нагадування!</b>\n\n"
                    f"<a href='tg://user?id={uid}'>Привіт!</a> Ти просив нагадати:\n<i>{text_r}</i>",
                    parse_mode="HTML")
            except Exception as e:
                logger.error(f"Reminder: {e}")
            
            
with db_lock:
                cursor.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
                conn.commit()
        time.sleep(10)

# ===================================================================
# 🌍 ПЕРЕКЛАД / СТИСНЕННЯ / ВІКІ / ВАЛЮТА / КРИПТО
# ===================================================================
@bot.message_handler(commands=['д_перекласти'])
def translate_text(message):
    parts = message.text.split(' ', 2)
    if len(parts) < 3 and not message.reply_to_message:
        bot.reply_to(message, "/д_перекласти [мова] [текст] або реплай"); return
    if message.reply_to_message and len(parts) < 3:
        lang = parts[1] if len(parts) > 1 else 'англійська'
        text_ = message.reply_to_message.text 
        text_ = message

        text_
or ""
    else:
        lang = parts[1]; text_ = parts[2]
    try:
        resp = model.generate_content(
        resp = model.
f"Переклади на {lang}. ТІЛЬКИ переклад: {text_}")
        bot.reply_to(message, 
        bot.reply_to(message,
f"🌍 <b>({lang}):</b>\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Помилка перекладу.")

@bot.message_handler(commands=['д_стиснути'])
def summarize_text(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення!"); return
    text_ = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not text_:
        bot.reply_to(message, "Немає тексту."); return
    try:
        resp = model.generate_content(
        resp = model.generate_
f"Стисни до 2-3 речень українською: {text_}")
        bot.reply_to(message, f"📝 <b>Коротко:</b>\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Помилка.")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!вікі'))
def wikipedia_search(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "!вікі Місяць"); return
    try:
        data = requests.get(
            f"https://uk.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(query)}",
            timeout=10).json()
        if 'extract' not in data:
            bot.reply_to(message, f"Нічого про '{query}'."); return
        extract  = data[
        extract  
'extract'][:800] + ('...' if len(data['extract']) > 800 else '')
        page_url = data.get('content_urls', {}).get('desktop', {}).get('page', '')
        bot.send_message(message.chat.id,
            f"📖 <b>{data.get('title', query)}</b>\n\n{extract}\n\n<a href='{page_url}'>Повністю</a>",
            parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Wiki: {e}")
        bot.reply_to(message, "Помилка Wikipedia.")

@bot.message_handler(func=lambda m: m.text and re.match(r'^!курс\s+\w+$', m.text.strip().lower()))
def currency_rate(message):
    currency = message.text.split()[1].upper()
    try:
        data = requests.get(
        data
"https://api.exchangerate-api.com/v4/latest/UAH", timeout=10).json()
        if currency not in data.get('rates', {}):
            bot.reply_to(message, f"Не знайшов '{currency}'."); return
        rate = data['rates'][currency]
        bot.send_message(message.chat.
        bot.send
id,
            f"💱 <b>{currency}/UAH</b>\n\n1 {currency} = <b>{1/rate:.2f} грн</b>",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Currency: {e}")
        bot.reply_to(message, "Помилка курсу.")

@bot.message_handler(func=lambda m: m.text and re.match(r'^!монета\s+\w+$', m.text.strip().lower()))
def crypto_rate(message):
    coin    = message.text.split()[1].upper()
    ids_map = {
        
    ids
'BTC':'bitcoin','ETH':'ethereum','BNB':'binancecoin','SOL':'solana',
        'XRP':'ripple','ADA':'cardano','DOGE':'dogecoin','TON':'the-open-network',
        'TRX':'tron','MATIC':'matic-network'
    }
    coin_id = ids_map.get(coin, coin.lower())
    
    }
    coin_

    }
    coin
try:
        data = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd,uah",
            timeout=10).json()
        if coin_id not in data:
            bot.reply_to(message, f"Не знайшов '{coin}'."); return
        usd = data[coin_id].get(
        usd = data[coin_id].
'usd', '?')
        uah = data[coin_id].get('uah', '?')
        bot.send_message(message.chat.id,
            f"₿ <b>{coin}</b>\n💵 ${usd:,.2f}\n💴 {uah:,.0f} грн", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Crypto: {e}")
        bot.reply_to(message, "Помилка крипто.")

# ===================================================================
# 💡 ФАКТ / ЖАРТ / ГОРОСКОП
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!знаєш')
def random_fact(message):
    try:
        resp = model.generate_content("Один цікавий факт. 2-3 речення, стиль Драго.")
        bot.send_message(message.chat.id, f"💡 {resp.text}")
    except Exception:
        bot.reply_to(message, 
        
"Мозок завис.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!смішно', '!жарт'])
def tell_joke(message):
    try:
        resp = model.generate_content("Короткий смішний анекдот українською. Тільки анекдот.")
        bot.send_message(message.chat.id, f"🃏 {resp.text}")
    except Exception:
        bot.reply_to(message, "Жарти скінчились 😅")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!зірки'))
def horoscope(message):
    sign = message.text.lower().split()[
    sign = message.text.
1] if len(message.text.split()) > 1 else ""
    if sign not in ZODIAC_SIGNS:
        bot.reply_to(message, f"Знаки: {', '.join(ZODIAC_SIGNS.keys())}"); return
    try:
        resp = model.generate_content(
            f"Гороскоп для {sign} на сьогодні. Стиль Драго, 3-4 речення.")
        bot.send_message(message.chat.id,
            f"♈ <b>Гороскоп {sign.capitalize()}:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, 
        bot
"Зірки мовчать.")

# ===================================================================
# 🖼️ ГЕНЕРАЦІЯ / ГОЛОСОВІ / ФОТО
# ===================================================================
@bot.message_handler(commands=['д_малюй'])
def generate_image(message):
    prompt = message.text.split(
    prompt
' ', 1)[1].strip() if len(message.text.split()) > 1 else ''
    if not prompt:
        bot.reply_to(message, "⚠️ /д_малюй [опис]"); return
    msg = bot.reply_to(message, 
    msg = bot
"⏳ Малюю... до 2 хвилин.")
    try:
        url = (f"https://image.pollinations.ai/p/{requests.utils.quote(prompt)}"
               f"?width=1024&height=1024&seed={random.randint(1,999999)}&model=flux&nologo=true")
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and len(r.content) >= 10000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            bio = io.BytesIO(); bio.name = 'art.jpg'
            img.save(bio, 
            img.save(
'JPEG', quality=95); bio.seek(0)
            bot.send_photo(message.chat.
            bot.send_photo
id, bio,
                           caption=f"🔥 <b>{prompt}</b>",
                           parse_mode="HTML",
                           reply_to_message_id=message.message_id)
            bot.delete_message(message.chat.
                           reply_to_message_id=message.message_id)
            bot.delete_message(message.chat.

                           reply_to_message_id=message.message_id)
            bot.delete
id, msg.message_id)
        else:
            raise Exception(f"HTTP {r.status_code}")
    except Exception as e:
        logger.error(
        logger.error(f
f"Generate: {e}")
        bot.edit_message_text("❌ Не зміг. Спробуй пізніше.", message.chat.id, msg.message_id)

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    
    
if message.chat.type in ['group', 'supergroup']:
        if not (message.reply_to_message and
                message.reply_to_message.from_user.id == bot.get_me().id):
            return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        fi   = bot.get_file(message.voice.file_id)
        data = bot.download_file(fi.file_path)
        resp = model.generate_content([
            
        fi   = bot.get_file(message.voice

        fi   = bot.get_file
"Послухай голосове і відповідж як Драго:",
            {"data": data, "mime_type": "audio/ogg"}
        ])
        bot.reply_to(message, resp.text)
    
        ])
        bot.reply

        ])
        bot.
except Exception as e:
        logger.error(f"Voice: {e}")
        bot.reply_to(message, "Не зміг розпізнати.")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    caption  = (message.caption or "").lower()
    is_group = message.chat.type in ['group', 'supergroup']
    if is_group:
        triggers = ['драго', 'джарвіс']
        
        
if not (any(w in caption for w in triggers) or
                (message.reply_to_message and
                 message.reply_to_message.from_user.id == bot.get_me().id)):
            return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        fi   = bot.get_file(message.photo[-
        fi   = bot.get_file(message.photo
1].file_id)
        data = bot.download_file(fi.file_path)
        p    = caption 
        data = bot.download_file(fi.file_path)
        p
if caption else "Опиши що на фото детально і дотепно в стилі Драго."
        resp = model.generate_content([p, {"data": data, "mime_type": "image/jpeg"}])
        bot.reply_to(message, resp.text)
    except Exception as e:
        logger.error(f"Photo: {e}")
        bot.reply_to(message, "Не зміг розглянути фото.")

# ===================================================================
# 🎮 ГРА В СЛОВА
# ===================================================================
@bot.message_handler(commands=['д_слова'])
def start_word_game(message):
    game_state[message.chat.
    game_state[
id] = {"last_letter": None, "used_words": []}
    bot.reply_to(message, "🎲 Гра в слова! Пиши перше слово.")

@bot.message_handler(commands=['д_стоп'])
def stop_word_game(message):
    if message.chat.id in game_state:
        del game_state[message.chat.id]
        bot.reply_to(message, 
        
"Гру зупинено 👋")
    else:
        bot.reply_to(message, "Гра не запущена.")

def handle_word_game(message):
    word  = message.text.lower().strip()
    state = game_state[message.chat.
    word  = message.text.lower
id]
    if not word.replace(" ", "").isalpha() or len(word) < 2:
        return
    if state["last_letter"] and word[0] != state["last_letter"]:
        bot.reply_to(message, 
        bot.reply_to(message, f
f"Не-а! Має починатись на '{state['last_letter'].upper()}'."); return
    if word in state["used_words"]:
        bot.reply_to(message, "Слово вже було! 😎"); return
    state["used_words"].append(word)
    nl = word[-
    nl
1] if word[-1] not in ['ь','и','й','ї'] else word[-2]
    state["last_letter"] = nl
    bot.reply_to(message, f"✅ Прийнято! Наступне на '{nl.upper()}'.")

# ===================================================================
# 👋 ВХІД / ВИХІД
# ===================================================================
@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    new_s = message.new_chat_member.status
    old_s = message.old_chat_member.status
    user  = message.new_chat_member.user
    
    new_s = message.new_chat_member.status
    old_s = message.old_chat_member.status
    user  

    new_s = message.new_chat_member.status
    old_s = message.old_chat_member.status
    

    new_s

    new
if new_s in ['member','administrator','restricted'] and not user.is_bot:
        with db_lock:
            cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)",
                           (user.id, user.first_name))
            conn.commit()
        bot.send_message(message.chat.id,
            
            
f"Вітаємо, <b>{user.first_name}</b>! 🤍\n"
            f"Тобі нараховано 100 стартових монет 🪙\n\n"
            f"<code>!стать хлопець</code> або <code>!стать дівчина</code> — вказати стать\n"
            f"<code>!місто Київ</code> — вказати місто для погоди\n"
            f"/допомога — всі команди",
            parse_mode="HTML")
    elif old_s in ['member','administrator','restricted'] and new_s in ['left','kicked']:
        name = message.old_chat_member.user.first_name
        byes = [
            
        name = message.old_chat_member.user.first_name
        byes
f"Ну і пофіг, <b>{name}</b> пішов. 👋",
            f"<b>{name}</b> злиняв. Менше народу — більше кисню. 🚪",
            f"<b>{name}</b> не витримав нашого рівня інтелекту. 🧠",
        ]
        bot.send_message(message.chat.id, random.choice(byes), parse_mode="HTML")

# ===================================================================
# ⚠️ МОДЕРАЦІЯ
# ===================================================================
@bot.message_handler(commands=['д_дд'])
def warn_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, 
        bot.reply_to
"Реплай!"); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute(
        cursor
"UPDATE stats SET warns=warns+1 WHERE user_id=?", (target.id,))
        cursor.execute(
        cursor
"SELECT warns FROM stats WHERE user_id=?", (target.id,))
        row = cursor.fetchone(); conn.commit()
    warns = row[
        row = cursor.fetchone(); conn.commit()
    warns
0] if row else 1
    if warns >= 3:
        give_achievement(target.
        give_achievement(target
id, 'токсик', message.chat.id)
        try:
            bot.restrict_chat_member(message.chat.
            bot.restrict_chat_member(message.

            bot.restrict
id, target.id,
                until_date=int(time.time()) + 3600,
                permissions=types.ChatPermissions(can_send_messages=False))
            bot.send_message(message.chat.
            bot
id,
                f"⛔ <b>{target.first_name}</b> — 3 варни → мут 1 год!", parse_mode="HTML")
        except Exception as e:
            bot.send_message(message.chat.id, f"Не зміг замутити: {e}")
    else:
        bot.send_message(message.chat.id,
            f"⚠️ <b>{target.first_name}</b> — варн {warns}/3", parse_mode="HTML")

@bot.message_handler(commands=['д_пробачаю'])
def unwarn_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns=MAX(0,warns-1) WHERE user_id=?", (target.id,))
        conn.commit()
    bot.send_message(message.chat.id,
        f"✅ Знято варн з <b>{target.first_name}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['д_тиша'])
def mute_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target  = message.reply_to_message.from_user
    parts   = message.text.split()
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            until_date=
            until_
int(time.time()) + minutes * 60,
            permissions=types.ChatPermissions(can_send_messages=False))
        bot.send_message(message.chat.id,
            
            f
f"🔇 <b>{target.first_name}</b> замовкни на {minutes} хв.", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['д_говори'])
def unmute_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            permissions=types.ChatPermissions(
                can_send_messages=
            permissions
True, can_send_media_messages=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True))
        bot.send_message(message.chat.id,
            f"🔊 <b>{target.first_name}</b> тепер може говорити!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['д_вигнати'])
def ban_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        bot.send_message(message.chat.id,
            f"🚫 <b>{target.first_name}</b> забанений! Адьос!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['д_пнути'])
def kick_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        time.sleep(0.5)
        bot.unban_chat_member(message.chat.id, target.id)
        bot.send_message(message.chat.id,
            f"👟 <b>{target.first_name}</b> — пнутий! До побачення!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

# ===================================================================
# 🧠 ШІ-АНАЛІЗ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!хто_це'))
def analyze_user(message):
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    try:
        with db_lock:
            cursor.execute("SELECT count,gender,warns,coins,rep FROM stats WHERE user_id=?",
                           (target.id,))
            row = cursor.fetchone()
        count, gender, warns, coins, rep = row 
            row = cursor.fetchone()
        
if row else (0,'?',0,0,0)
        resp = model.generate_content(
            
        resp = model.generate_content(
f"Дай смішну характеристику в стилі Драго:\n"
            f"Ім'я: {target.first_name}, Стать: {gender}, "
            f"Повідомлень: {count}, Варни: {warns}, Монети: {coins}, Реп: {rep}\n"
            f"3-4 речення, іронічно.")
        bot.send_message(message.chat.
        bot.send_message(message.chat.
id,
            f"🧠 <b>Аналіз {target.first_name}:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Analyze: {e}")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!атмосфера')
def analyze_mood(message):
    try:
        today = datetime.now().strftime(
        today = datetime.now().'%Y-%m-%d')
        with db_lock:
            cursor.execute("""
                SELECT s.name,ds.count FROM daily_stats ds
                JOIN stats s ON s.user_id=ds.user_id
                WHERE ds.chat_id=? AND ds.date=?
                ORDER BY ds.count DESC LIMIT 5
            """, (message.chat.id, today))
            rows = cursor.fetchall()
        if not rows:
            bot.reply_to(message, "Немає даних."); return
        names = ", ".join([r[0] for r in rows])
        resp  = model.generate_content(
            f"Оціни настрій чату де активні: {names}. Коротко, стиль Драго.")
        bot.send_message(message.chat.id,
            f"🎭 <b>Атмосфера чату:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Mood: {e}")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!мем')
def send_meme(message):
    try:
        bot.delete_message(message.chat.
        bot.delete
id, message.message_id)
        meme_dir = r"D:\DragoBot\memes"
        if os.path.exists(meme_dir):
            memes = [f for f in os.listdir(meme_dir)
                     if f.endswith(('.png','.jpg','.jpeg','.gif'))]
            if memes:
                with open(os.path.join(meme_dir, random.choice(memes)), 'rb') as ph:
                    bot.send_photo(message.chat.id, ph)
            else:
                bot.send_message(message.chat.id, "Папка з мемами порожня!")
        else:
            bot.send_message(message.chat.
            bot
id, "Не знайшов папку з мемами.")
    except Exception as e:
        logger.error(f"Мем: {e}")

# ===================================================================
# ❓ ДОПОМОГА
# ===================================================================
@bot.message_handler(commands=['допомога', 'start', 'help'])
def show_help(message):
    bot.send_message(message.chat.id, """🤖 <b>ДРАГО — ВСІ КОМАНДИ</b>

<b>👤 Профіль:</b>
• <code>!стать хлопець/дівчина</code> — вказати стать
• <code>!місто [назва]</code> — зберегти місто для погоди
• <code>!я</code> — мій профіль
• <code>!нагороди</code> — мої досягнення

<b>🤖 Спілкування:</b>
• Напиши "Драго" або реплай — відповідь ШІ
• Реплай фото + "Драго" — аналіз зображення

<b>🎮 Ігри:</b>
• <code>!фортуна</code> — колесо фортуни
• <code>!монетка</code> — орел чи решка
• <code>!кості [N]</code> — кубик
• <code>!батл</code> (реплай) — рап-батл
• <code>!прийняти</code> / <code>!відмовити</code>
• <code>!пчд</code> — правда чи діло
• <code>!куля [питання]</code> — чарівна куля
• <code>!хто_кращий A vs B</code> — порівняння
• <code>!любов</code> (реплай) — відсоток кохання
• <code>/д_загадка</code> — вікторина (+50🪙)
• <code>/д_слова</code> / <code>/д_стоп</code>

<b>💰 Економіка:</b>
• <code>!бонус</code> — щоденний бонус
• <code>!лотерея</code> — щоденна лотерея
• <code>!гаманець</code> — баланс
• <code>!дати [сума]</code> (реплай) — переказ
• <code>!ставка [сума]</code> — казино
• <code>!крамниця</code> / <code>/д_купити</code>

<b>⭐ Репутація:</b>
• <code>!+</code> (реплай) — підвищити репутацію
• <code>!-</code> (реплай) — понизити репутацію

<b>📊 Статистика:</b>
• <code>!хто</code> — топ дня
• <code>!хто_це</code> (реплай) — аналіз юзера
• <code>!атмосфера</code> — настрій чату
• <code>/д_зведення</code> — статистика (адміни)

<b>🛠️ Утиліти:</b>
• <code>!знаєш</code> — цікавий факт
• <code>!смішно</code> — анекдот
• <code>!зірки [знак]</code> — гороскоп
• <code>!цитата</code> — цитата дня
• <code>!токсик</code> (реплай) — рівень токсичності
• <code>!прогноз</code> — прогноз дня від ШІ
• <code>!роль</code> — рандомна роль
• <code>!голос [питання]</code> — голосування
• <code>!конфесія [текст]</code> — анонімно
• <code>!вікі [запит]</code> — Wikipedia
• <code>!курс [USD]</code> — курс валюти
• <code>!монета [BTC]</code> — ціна крипти
• <code>!погода [місто]</code> — погода
• <code>!мем</code> — рандомний мем
• <code>/д_малюй [опис]</code> — генерація фото
• <code>/д_перекласти [мова] [текст]</code>
• <code>/д_стиснути</code> (реплай)
• <code>/д_памятка [час] [текст]</code>

<b>⚠️ Модерація (адміни):</b>
• <code>/д_дд</code> <code>/д_пробачаю</code>
• <code>/д_тиша [хв]</code> <code>/д_говори</code>
• <code>/д_вигнати</code> <code>/д_пнути</code>""", parse_mode="HTML")

# ===================================================================
# 🎛️ ЦЕНТРАЛЬНИЙ ДИСПЕТЧЕР
# ===================================================================
@bot.message_handler(content_types=['text'])
def main_handler(message):
    text      = message.text
    chat_id   = message.chat.
    
id
    chat_type = message.chat.type
    user_id   = message.from_user.
    user
id
    name      = message.from_user.first_name

    update_message_count(user_id, name, chat_id)

    
    name      = message.from_user.first_name

    update_message_count
# Антиспам
    if chat_type in ['group', 'supergroup'] and not is_admin(chat_id, user_id):
        if check_flood(user_id, chat_id):
            try:
                bot.restrict_chat_member(chat_id, user_id,
                    until_date=int(time.time()) + MUTE_DURATION,
                    permissions=types.ChatPermissions(can_send_messages=False))
                bot.send_message(chat_id,
                    f"⚡ <b>{name}</b>, флуд → мут 5 хв!", parse_mode="HTML")
            except Exception as e:
                logger.error(f"Antispam: {e}")
            return

    # Антимат
    if has_bad_words(text) and chat_type in ['group', 'supergroup']:
        if not is_admin(chat_id, user_id):
            bot.reply_to(message, 
            bot.reply
f"Ей, <b>{name}</b>, стеж за лексикою!", parse_mode="HTML")

    # Авто-реакції (30% шанс)
    text_lower = text.lower()
    for keyword, responses in AUTO_REACTIONS.items():
        if keyword in text_lower and random.random() < 0.30:
            bot.send_message(chat_id, random.choice(responses))
            break

    # Рап-батл
    if chat_id in rap_pending:
        battle = rap_pending[chat_id]
        
        battle =

        battle
if battle.get('stage') == 'waiting_rap1' and user_id == battle['challenger_id'] and message.reply_to_message:
            battle['challenger_rap'] = text
            battle['stage'] = 'waiting_rap2'
            bot.send_message(chat_id,
                
            bot.send_message(chat_id,
f"✅ Реп <b>{battle['challenger_name']}</b> прийнято!\n\n"
                f"<b>{battle['opponent_name']}</b> — твоя черга (реплай на це):",
                parse_mode="HTML")
            return
        elif battle.get('stage') == 'waiting_rap2' and user_id == battle['opponent_id'] and message.reply_to_message:
            battle['opponent_rap'] = text
            try:
                prompt = (
                    
                prompt
f"Ти суддя рап-батлу. Оціни:\n\n"
                    f"🎤 {battle['challenger_name']}: {battle.get('challenger_rap','...')}\n\n"
                    f"🎤 {battle['opponent_name']}: {battle['opponent_rap']}\n\n"
                    f"Оцінка кожному (1-10) + переможець. Стиль Драго."
                )
                resp   = model.generate_content(prompt)
                bot.send_message(chat_id,
                    
                )
                resp   = model.generate_content(prompt)
                bot.send_message(chat_id,
f"⚖️ <b>ВЕРДИКТ ДРАГО:</b>\n\n{resp.text}", parse_mode="HTML")
            except Exception:
                winner = random.choice([battle['challenger_name'], battle['opponent_name']])
                bot.send_message(chat_id, 
                bot.send_message(
f"⚖️ Переможець: <b>{winner}</b>!", parse_mode="HTML")
            rap_pending.pop(chat_id)
            
            rap_pending.
return

    # Вікторина
    if check_trivia_answer(message):
        return

    # Гра в слова
    if chat_id in game_state:
        handle_word_game(message)
        return

    # Аналіз статі (якщо ще не вказано)
    with db_lock:
        cursor.execute("SELECT gender FROM stats WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    if row and row[0] == 'не вказано':
        g = analyze_gender(text)
        if g in ['Хлопець', 'Дівчина']:
            with db_lock:
                cursor.execute(
                cursor
"UPDATE stats SET gender=? WHERE user_id=?", (g, user_id))
                conn.commit()
            bot.send_message(chat_id,
                f"Драго вирішив що ти — {g.lower()}. Вгадав? 😎\n"
                f"Якщо ні — напиши <code>!стать хлопець/дівчина</code>",
                parse_mode="HTML")

    # Діалог з Драго
    is_mentioned = False
    if chat_type in ['group', 'supergroup']:
        triggers   = ['драго', 'джарвіс']
        word_found = 
        word_found
any(w in text_lower for w in triggers)
        if (word_found or
            f"@{bot.get_me().username}" in text or
            (message.reply_to_message and
             message.reply_to_message.from_user.id == bot.get_me().id)):
            is_mentioned = True
            for w in triggers:
                if text_lower.startswith(w):
                    text = text[
                    text = text[

                    text
len(w):].strip(); break
    else:
        is_mentioned = True

    if not is_mentioned:
        return

    status_msg = None
    try:
        bot.send_chat_action(chat_id, 
        bot.send_chat_action(chat_id,
'typing')
        status_msg  = bot.reply_to(message, 
        status
"Йде відправка даних в СБУ... 👮‍♂️")
        # ✅ ВИПРАВЛЕНО: передаємо user_id щоб кожен юзер мав свою розмову
        gemini_chat = get_gemini_chat(chat_id, user_id)
        response    = gemini_chat.send_message(text)
        
        gemini_chat = get_gemini_chat(chat_id, user_id)
        response
try:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text=response.text, parse_mode=
            bot.edit_message_text(chat_id=chat_id, message_id=status

            bot.edit_message_text(chat_id=chat_id, message_id
"Markdown")
        except Exception:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text=response.text)
    
            bot
except genai.types.generation_types.BlockedPromptException:
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text="Цей запит заблоковано Google. 🤐")
    except Exception as e:
        logger.error(f"Dialog: {e}")
        err = 
        err =
"Сервери прилягли, спробуй пізніше."
        if "ResourceExhausted" in str(e) or "quota" in str(e).lower():
            err = "Пригальмуй! Google каже почекати хвилину..."
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=err)
        else:
            bot.reply_to(message, err)

# ===================================================================
# 🚀 ЗАПУСК
# ===================================================================
threading.Thread(target=run_dummy_server, daemon=
threading.Thread(target=run
True).start()
threading.Thread(target=reminder_worker,  daemon=
threading.Thread(target=reminder_worker,  

threading.Thread(target=reminder_worker,
True).start()

if __name__ == "__main__":
    logger.info("DRAGO BOT ЗАПУЩЕНИЙ!")
    print("=" * 45)
    print("   DRAGO BOT — ФІНАЛЬНА ВЕРСІЯ v3!   ")
    print("=" * 45)
    bot.infinity_polling(allowed_updates=[
    bot
'message', 'chat_member', 'my_chat_member'])
