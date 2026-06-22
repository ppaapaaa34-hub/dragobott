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
# ЛОГУВАННЯ
# ===================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('drago_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ])
logger = logging.getLogger(__name__)

# ==============================================================================
# БАЗА ДАНИХ
# ==============================================================================
conn = sqlite3.connect('drago_bot.db', check_same_thread=False)
cursor = conn.cursor()
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
# НАЛАШТУВАННЯ
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
    'пиво':      ["Налий і мені!", "Пиво - це рідкий хліб. Поживно!"],
    'їжа':       ["А мені нічого не принесли", "Замовляй, я слідкую"],
    'сон':       ["Сон - це смерть для слабаків", "Поспи, може розумнішим прокинешся"],
    'гроші':     ["Де гроші, Лебовскі?!", "Гроші - зло. Але без зла нудно."],
    'любов':     ["Ніжності?! Від Драго?! Ха!", "Любов - це дофамін і серотонін. Буквально хімія."],
    'понеділок': ["Понеділок - день важкий. Як і всі інші."],
    'відпустка': ["Відпустка?! А хто буде страждати на роботі?"],
    'гпт':       ["Той ChatGPT - просто мій менший брат. Я крутіший."],
    'нудно':     ["Нудно? Пограй у !фортуна або !кості. Або просто поговори зі мною"],
}
ACHIEVEMENTS = {
    'перше_повідомлення': ('Перші кроки',  'Надіслав перше повідомлення'),
    'сто_повідомлень':    ('Балакун',       '100 повідомлень у чаті'),
    'тисяча_повідомлень': ('Легенда чату',  '1000 повідомлень!'),
    'мільйонер':          ('Мільйонер',     'Накопичив 1000 монет'),
    'везунчик':           ('Везунчик',      'Виграв джекпот у казино'),
    'перший_бонус':       ('Халявник',      'Забрав перший щоденний бонус'),
    'рапер':              ('Рапер',         'Переміг у рап-батлі'),
    'добра_людина':       ('Добра людина',  'Дав +репутацію іншому'),
    'токсик':             ('Токсик',        'Отримав 3 варни'),
}

# ===================================================================
# ІНІЦІАЛІЗАЦІЯ
# ===================================================================
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {"max_output_tokens": 2048, "temperature": 0.85}
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=(
        "Ти - зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
        "Спілкуйся виключно українською мовою. Використовуй молодіжний сленг, "
        "іноді нецензурну лексику для емоційності. Будь живим, неформальним, "
        "злегка іронізуй, підколюй юзера, але завжди допомагай. "
        "Завжди закінчуй думки! Пиши коротко і ясно!"
    ))
bot_chats = {}
flood_data = defaultdict(list)
game_state = {}
rap_pending = {}

# ===================================================================
# АВТО-ВИДАЛЕННЯ ПОВІДОМЛЕНЬ БОТА
# ===================================================================
def auto_delete(msg, delay=30):
    def _delete():
        time.sleep(delay)
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except Exception:
            pass
    threading.Thread(target=_delete, daemon=True).start()

def get_gemini_chat(chat_id, user_id):
    key = (chat_id, user_id)
    if key not in bot_chats:
        bot_chats[key] = model.start_chat(history=[])
    return bot_chats[key]

def is_admin(chat_id, user_id):
    try:
        return bot.get_chat_member(chat_id, user_id).status in ['administrator', 'creator']
    except Exception:
        return False

def has_bad_words(text):
    return any(w in text.lower() for w in BAD_WORDS)

def check_flood(user_id, chat_id):
    now = time.time()
    key = (user_id, chat_id)
    flood_data[key] = [t for t in flood_data[key] if now - t < FLOOD_TIME]
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
            "INSERT INTO daily_stats (user_id,chat_id,date,count) VALUES(?,?,?,1) "
            "ON CONFLICT(user_id,chat_id,date) DO UPDATE SET count=count+1",
            (user_id, chat_id, today))
        conn.commit()
    check_achievements(user_id, chat_id)

def give_achievement(user_id, key, chat_id):
    with db_lock:
        cursor.execute("SELECT 1 FROM achievements WHERE user_id=? AND achievement=?", (user_id, key))
        if cursor.fetchone():
            return False
        cursor.execute("INSERT INTO achievements (user_id, achievement, earned_at) VALUES(?,?,?)",
                       (user_id, key, int(time.time())))
        conn.commit()
    title, desc = ACHIEVEMENTS[key]
    try:
        with db_lock:
            cursor.execute("SELECT name FROM stats WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        name = row[0] if row else "Хтось"
        bot.send_message(chat_id,
            f"<b>ДОСЯГНЕННЯ!</b>\n\n<b>{title}</b>\n<i>{desc}</i>\n\nОтримав: <b>{name}</b>",
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
        resp = model.generate_content(f"Визнач стать (Хлопець/Дівчина/Незрозуміло). Тільки одне слово: {text}")
        return resp.text.strip()
    except Exception:
        return "Незрозуміло"

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!стать'))
def set_gender(message):
    parts = message.text.split()
    if len(parts) < 2:
        sent = bot.reply_to(message, "Вкажи стать:\n<code>!стать хлопець</code>\n<code>!стать дівчина</code>", parse_mode="HTML")
        auto_delete(sent, 20); return
    g_input = parts[1].lower()
    if g_input in ['хлопець','хлоп','м','чол','boy','male','чоловік']:
        gender = 'Хлопець'
    elif g_input in ['дівчина','дів','ж','жін','girl','female','жінка']:
        gender = 'Дівчина'
    else:
        sent = bot.reply_to(message, "Не зрозумів. Напиши: !стать хлопець або !стать дівчина")
        auto_delete(sent, 20); return
    uid = message.from_user.id
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)", (uid, message.from_user.first_name))
        cursor.execute("UPDATE stats SET gender=? WHERE user_id=?", (gender, uid))
        conn.commit()
    sent = bot.reply_to(message, f"Стать збережена: <b>{gender}</b>", parse_mode="HTML")
    auto_delete(sent, 20)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!місто'))
def set_city(message):
    parts = message.text.split(' ', 1)
    if len(parts) < 2 or not parts[1].strip():
        sent = bot.reply_to(message, "Вкажи місто: <code>!місто Київ</code>", parse_mode="HTML")
        auto_delete(sent, 20); return
    city = parts[1].strip()
    uid  = message.from_user.id
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)", (uid, message.from_user.first_name))
        cursor.execute("UPDATE stats SET city=? WHERE user_id=?", (city, uid))
        conn.commit()
    sent = bot.reply_to(message, f"Місто збережено: <b>{city}</b>", parse_mode="HTML")
    auto_delete(sent, 20)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!нагороди')
def show_achievements(message):
    uid = message.from_user.id
    with db_lock:
        cursor.execute("SELECT achievement FROM achievements WHERE user_id=? ORDER BY earned_at", (uid,))
        rows = cursor.fetchall()
    if not rows:
        sent = bot.reply_to(message, f"У <b>{message.from_user.first_name}</b> ще немає досягнень!", parse_mode="HTML")
        auto_delete(sent, 30); return
    text = f"<b>Досягнення {message.from_user.first_name}:</b>\n\n"
    for (key,) in rows:
        if key in ACHIEVEMENTS:
            title, desc = ACHIEVEMENTS[key]
            text += f"<b>{title}</b> - <i>{desc}</i>\n"
    text += f"\n<b>{len(rows)}/{len(ACHIEVEMENTS)}</b> досягнень отримано"
    sent = bot.send_message(message.chat.id, text, parse_mode="HTML")
    auto_delete(sent, 60)

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
        sent = bot.reply_to(message, "Вкажи місто: <code>!погода Київ</code>", parse_mode="HTML")
        auto_delete(sent, 20); return
    try:
        d = requests.get(
            f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ua",
            timeout=10).json()
        if d.get('cod') != 200:
            sent = bot.reply_to(message, f"Не знайшов місто '{city}'.")
            auto_delete(sent, 20); return
        temp=d['main']['temp']; feels=d['main']['feels_like']
        desc=d['weather'][0]['description']; humidity=d['main']['humidity']; wind=d['wind']['speed']
        w_id = d['weather'][0]['id']
        if w_id < 300: w_emoji="Storm"
        elif w_id < 500: w_emoji="Drizzle"
        elif w_id < 600: w_emoji="Rain"
        elif w_id < 700: w_emoji="Snow"
        elif w_id < 800: w_emoji="Fog"
        elif w_id == 800: w_emoji="Clear"
        else: w_emoji="Cloudy"
        sent = bot.send_message(message.chat.id,
            f"<b>Погода в {city} ({w_emoji})</b>\n\n"
            f"Температура: {temp:.1f}C (відчув. {feels:.1f}C)\n"
            f"Опис: {desc}\nВологість: {humidity}%\nВітер: {wind} м/с",
            parse_mode="HTML")
        auto_delete(sent, 60)
    except Exception as e:
        logger.error(f"Weather: {e}")
        bot.reply_to(message, "Помилка погоди.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!бонус')
def daily_bonus(message):
    uid=message.from_user.id; now=int(time.time())
    with db_lock:
        cursor.execute("SELECT last_bonus FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
    last = row[0] if row else 0
    if now - last < 86400:
        remaining = 86400-(now-last); h=remaining//3600; m_=(remaining%3600)//60
        sent = bot.reply_to(message, f"Бонус вже забрав! Наступний через <b>{h}год {m_}хв</b>", parse_mode="HTML")
        auto_delete(sent, 20); return
    bonus = random.randint(50, 200)
    add_coins(uid, bonus)
    with db_lock:
        cursor.execute("UPDATE stats SET last_bonus=? WHERE user_id=?", (now, uid))
        conn.commit()
    give_achievement(uid, 'перший_бонус', message.chat.id)
    sent = bot.send_message(message.chat.id,
        f"<b>{message.from_user.first_name}</b> забрав бонус! +<b>{bonus} монет</b> | Баланс: {get_coins(uid)} монет",
        parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!лотерея')
def daily_lottery(message):
    uid=message.from_user.id; now=int(time.time())
    with db_lock:
        cursor.execute("SELECT last_lottery FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
    last = row[0] if row else 0
    if now - last < 86400:
        h=(86400-(now-last))//3600
        sent = bot.reply_to(message, f"Вже брав участь! Наступна через <b>{h} год</b>", parse_mode="HTML")
        auto_delete(sent, 20); return
    with db_lock:
        cursor.execute("UPDATE stats SET last_lottery=? WHERE user_id=?", (now, uid)); conn.commit()
    r = random.random()
    if r < 0.05:
        prize=1000; text=f"<b>ДЖЕКПОТ!!!</b> +{prize} монет"
        give_achievement(uid, 'везунчик', message.chat.id)
    elif r < 0.20: prize=300; text=f"<b>Великий виграш!</b> +{prize} монет"
    elif r < 0.50: prize=100; text=f"<b>Виграш</b> +{prize} монет"
    else: prize=0; text=f"<b>{message.from_user.first_name}</b> нічого не виграв."
    if prize > 0: add_coins(uid, prize)
    sent = bot.send_message(message.chat.id,
        f"<b>{message.from_user.first_name}</b> бере участь у лотереї...\n\n{text}", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ['!+', '!-'])
def change_rep(message):
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target=message.reply_to_message.from_user; giver=message.from_user
    if target.id == giver.id:
        sent = bot.reply_to(message, "Собі репутацію? Нарцис!"); auto_delete(sent, 15); return
    if target.is_bot:
        sent = bot.reply_to(message, "Боту репутацію?"); auto_delete(sent, 15); return
    change = 1 if message.text.strip() == '!+' else -1
    with db_lock:
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)", (target.id, target.first_name))
        cursor.execute("UPDATE stats SET rep=rep+? WHERE user_id=?", (change, target.id))
        cursor.execute("SELECT rep FROM stats WHERE user_id=?", (target.id,))
        new_rep = cursor.fetchone()[0]
        conn.commit()
    if change > 0: give_achievement(giver.id, 'добра_людина', message.chat.id)
    action = "підвищив" if change > 0 else "понизив"
    sent = bot.send_message(message.chat.id,
        f"<b>{giver.first_name}</b> {action} репутацію <b>{target.first_name}</b>\nРепутація: <b>{new_rep}</b>",
        parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!батл'))
def rap_battle_challenge(message):
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай на суперника!"); auto_delete(sent, 15); return
    challenger=message.from_user; opponent=message.reply_to_message.from_user
    if challenger.id == opponent.id or opponent.is_bot:
        sent = bot.reply_to(message, "Нема сенсу."); auto_delete(sent, 15); return
    rap_pending[message.chat.id] = {
        'challenger_id': challenger.id, 'challenger_name': challenger.first_name,
        'opponent_id': opponent.id, 'opponent_name': opponent.first_name, 'stage': 'waiting_accept'}
    bot.send_message(message.chat.id,
        f"<b>{challenger.first_name}</b> кидає виклик -> <b>{opponent.first_name}</b>!\n\n"
        f"<code>!прийняти</code> або <code>!відмовити</code>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!прийняти')
def rap_accept(message):
    cid = message.chat.id
    if cid not in rap_pending:
        sent = bot.reply_to(message, "Немає виклику."); auto_delete(sent, 15); return
    battle = rap_pending[cid]
    if message.from_user.id != battle['opponent_id']:
        sent = bot.reply_to(message, "Не тебе викликали!"); auto_delete(sent, 15); return
    rap_pending[cid]['stage'] = 'waiting_rap1'
    bot.send_message(cid,
        f"<b>РАП-БАТЛ!</b>\n\n<b>{battle['challenger_name']}</b> - напиши свій реп (реплай):",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!відмовити')
def rap_decline(message):
    cid = message.chat.id
    if cid not in rap_pending: return
    battle = rap_pending.pop(cid)
    if message.from_user.id != battle['opponent_id']: return
    sent = bot.send_message(cid, f"<b>{battle['opponent_name']}</b> злякався! Боягуз!", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!конфесія'))
def confession(message):
    text_ = message.text[9:].strip()
    if not text_:
        sent = bot.reply_to(message, "!конфесія [текст]"); auto_delete(sent, 15); return
    try: bot.delete_message(message.chat.id, message.message_id)
    except: pass
    with db_lock:
        cursor.execute("INSERT INTO confessions (chat_id, text, created) VALUES (?,?,?)",
                       (message.chat.id, text_, int(time.time())))
        conn.commit()
    bot.send_message(message.chat.id, f"<b>АНОНІМНА КОНФЕСІЯ:</b>\n\n<i>{text_}</i>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!прогноз')
def day_forecast(message):
    name = message.from_user.first_name
    try:
        resp = model.generate_content(
            f"Персональний прогноз дня для {name} в стилі Драго. Включи: щастя, кохання, гроші, роботу. 4 речення.")
        sent = bot.send_message(message.chat.id, f"<b>Прогноз для {name}:</b>\n\n{resp.text}", parse_mode="HTML")
        auto_delete(sent, 60)
    except:
        sent = bot.reply_to(message, "Кристальна куля затуманилась."); auto_delete(sent, 15)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!роль')
def random_role(message):
    roles = ["Король чату","Клоун дня","Найрозумніший","Зомбі чату","Альфа самець","Вівця стада",
             "Секретний агент","Зірка вечора","Людина-піца","Соня дня","Хитрий лис","Черепаха-мудрець"]
    sent = bot.send_message(message.chat.id,
        f"Сьогодні <b>{message.from_user.first_name}</b> - це...\n\n<b>{random.choice(roles)}</b>!",
        parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!куля'))
def magic_ball(message):
    question = message.text[5:].strip()
    if not question:
        sent = bot.reply_to(message, "!куля Чи пощастить мені?"); auto_delete(sent, 15); return
    answers = ["Так, однозначно!","Все вказує на так!","Безперечно!","Можеш розраховувати!",
               "Спитай пізніше...","Важко сказати.","Не визначено.","Не розраховуй на це.",
               "Відповідь - ні.","Навіть не мрій!","Серйозно?! ТИ ЗАДАЄШ ЦЕ ПИТАННЯ?!"]
    sent = bot.send_message(message.chat.id,
        f"<b>Питання:</b> {question}\n\n<b>{random.choice(answers)}</b>", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!голос'))
def quick_poll(message):
    text_ = message.text[6:].strip()
    if not text_:
        sent = bot.reply_to(message, "!голос Питання?"); auto_delete(sent, 15); return
    try: bot.send_poll(message.chat.id, question=text_[:300], options=["Так","Ні","Все рівно"], is_anonymous=False)
    except Exception as e: bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!хто_кращий'))
def who_is_better(message):
    parts = message.text[11:].strip().split(' vs ')
    if len(parts) < 2:
        sent = bot.reply_to(message, "Формат: !хто_кращий Кіт vs Собака"); auto_delete(sent, 15); return
    a,b = parts[0].strip(),parts[1].strip()
    score_a = random.randint(0,100); score_b = 100-score_a
    winner = a if score_a > score_b else b
    sent = bot.send_message(message.chat.id,
        f"<b>{a}</b> vs <b>{b}</b>\n\n{a}: {score_a}%\n{b}: {score_b}%\n\nПереможець: <b>{winner}</b>!",
        parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!цитата')
def motivational_quote(message):
    try:
        style = random.choice(['мотиваційна','демотиваційна і саркастична','філософська','смішна'])
        resp = model.generate_content(f"Коротка {style} цитата українською. Тільки цитата і автор.")
        sent = bot.send_message(message.chat.id, f"<i>{resp.text}</i>", parse_mode="HTML")
        auto_delete(sent, 30)
    except:
        sent = bot.reply_to(message, "Муза покинула Драго."); auto_delete(sent, 15)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!токсик')
def toxicity_level(message):
    target_name = (message.reply_to_message.from_user.first_name
                   if message.reply_to_message else message.from_user.first_name)
    level = random.randint(0,100)
    if level < 20: desc="Майже святий. Підозріло."
    elif level < 40: desc="Нормальний юзер. Рідкість."
    elif level < 60: desc="Середньо токсичний."
    elif level < 80: desc="Досить токсичний!"
    else: desc="НЕБЕЗПЕЧНИЙ РІВЕНЬ ТОКСИЧНОСТІ!!!"
    bar = "#"*(level//10)+"."*(10-level//10)
    sent = bot.send_message(message.chat.id,
        f"<b>Токсичність {target_name}:</b>\n\n[{bar}] {level}%\n\n<i>{desc}</i>", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!любов'))
def love_percent(message):
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    name1=message.from_user.first_name; name2=message.reply_to_message.from_user.first_name
    level = abs(hash(f"{min(name1,name2)}{max(name1,name2)}")) % 101
    if level < 20: desc="Нічого спільного."
    elif level < 40: desc="Дружба - і не більше."
    elif level < 60: desc="Симпатія є!"
    elif level < 80: desc="Є іскра! Діяти!"
    else: desc="ІДЕАЛЬНА ПАРА!!"
    bar = "#"*(level//10)+"."*(10-level//10)
    sent = bot.send_message(message.chat.id,
        f"<b>{name1}</b> + <b>{name2}</b>\n\n[{bar}] {level}%\n\n<i>{desc}</i>", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!фортуна')
def roulette(message):
    outcomes = [("Мут 1 хв!","mute",60),("+20 монет!","coins",20),("-10 монет!","coins",-10),
                ("+50 монет!","coins",50),("Нічого. Просто лох.","none",0),("+100 монет!","coins",100),
                ("Мут 30 сек!","mute",30),("+5 монет.","coins",5)]
    o=random.choice(outcomes); uid=message.from_user.id
    sent = bot.send_message(message.chat.id,
        f"<b>{message.from_user.first_name}</b> крутить колесо...\n\n{o[0]}", parse_mode="HTML")
    auto_delete(sent, 30)
    if o[1] == "coins": add_coins(uid, o[2])
    elif o[1] == "mute":
        try:
            bot.restrict_chat_member(message.chat.id, uid,
                until_date=int(time.time())+o[2],
                permissions=types.ChatPermissions(can_send_messages=False))
        except: pass

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!монетка')
def coin_flip(message):
    sent = bot.reply_to(message,
        f"Підкидаю...\n\n<b>{random.choice(['Орел!','Решка!'])}</b>", parse_mode="HTML")
    auto_delete(sent, 20)

@bot.message_handler(func=lambda m: m.text and re.match(r'^!кості(\s+\d+)?$', m.text.strip().lower()))
def roll_dice(message):
    parts = message.text.strip().split()
    sides = max(2, min(int(parts[1]) if len(parts) > 1 else 6, 1000))
    sent  = bot.reply_to(message,
        f"Кубик d{sides}...\n\n<b>{random.randint(1,sides)}</b>", parse_mode="HTML")
    auto_delete(sent, 20)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!пчд')
def truth_or_dare(message):
    truths = ["Яка найдурніша річ, яку ти коли-небудь робив?","Кого з чату вважаєш найрозумнішим?",
              "Який твій найбільший страх?","Що найгірше казав про когось за спиною?"]
    dares  = ["Напиши комплімент кожному в чаті!","Відправ голосове з піснею.",
              "Розкажи щось про себе, чого ніхто не знає.","Скажи щось приємне адміну."]
    if random.random() > 0.5:
        sent = bot.send_message(message.chat.id, f"<b>ПРАВДА:</b>\n\n{random.choice(truths)}", parse_mode="HTML")
    else:
        sent = bot.send_message(message.chat.id, f"<b>ДІЛО:</b>\n\n{random.choice(dares)}", parse_mode="HTML")
    auto_delete(sent, 60)

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
        sent = bot.reply_to(message, "Сьогодні тиша..."); auto_delete(sent, 15); return
    text = "<b>Топ балакунів сьогодні:</b>\n\n"
    for i,(name,count) in enumerate(rows):
        text += f"{i+1}. <b>{name}</b> - {count} повідомлень\n"
    sent = bot.send_message(message.chat.id, text, parse_mode="HTML")
    auto_delete(sent, 60)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!я')
def show_profile(message):
    uid = message.from_user.id
    with db_lock:
        cursor.execute("SELECT count,gender,warns,coins,rep,city FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM achievements WHERE user_id=?", (uid,))
        ach_count = cursor.fetchone()[0]
    if not row:
        sent = bot.reply_to(message, "Ти не в базі. Пиши більше!"); auto_delete(sent, 15); return
    count,gender,warns,coins,rep,city = row
    city_text = f"Місто: <b>{city}</b>\n" if city else ""
    username  = message.from_user.username
    nick_text = f"Нікнейм: <b>@{username}</b>\n" if username else "Нікнейм: <b>не встановлено</b>\n"
    sent = bot.send_message(message.chat.id,
        f"<b>Профіль {message.from_user.first_name}</b>\n\n"
        f"{nick_text}Повідомлень: <b>{count}</b>\nСтать: <b>{gender}</b>\n"
        f"{city_text}Монети: <b>{coins}</b>\nРепутація: <b>{rep}</b>\n"
        f"Варни: <b>{warns}/3</b>\nДосягнень: <b>{ach_count}/{len(ACHIEVEMENTS)}</b>",
        parse_mode="HTML")
    auto_delete(sent, 60)

@bot.message_handler(commands=['д_зведення'])
def show_group_stats(message):
    if not is_admin(message.chat.id, message.from_user.id):
        sent = bot.reply_to(message, "Тільки для адмінів!"); auto_delete(sent, 15); return
    with db_lock:
        cursor.execute("SELECT COUNT(*),SUM(count),SUM(coins) FROM stats")
        tu,tm,tc = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender='Хлопець'"); boys=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender='Дівчина'"); girls=cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM achievements"); ach=cursor.fetchone()[0]
    sent = bot.send_message(message.chat.id,
        f"<b>Статистика групи</b>\n\nЮзерів: <b>{tu}</b>\nПовідомлень: <b>{tm or 0}</b>\n"
        f"Монет в обігу: <b>{tc or 0}</b>\nХлопців: <b>{boys}</b>  Дівчат: <b>{girls}</b>\n"
        f"Досягнень видано: <b>{ach}</b>", parse_mode="HTML")
    auto_delete(sent, 60)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!гаманець')
def show_balance(message):
    sent = bot.reply_to(message, f"Твій баланс: <b>{get_coins(message.from_user.id)} монет</b>", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!дати'))
def transfer_coins(message):
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай + !дати [сума]"); auto_delete(sent, 15); return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        sent = bot.reply_to(message, "Формат: !дати [сума] (реплай)"); auto_delete(sent, 15); return
    amount=int(parts[1]); sender=message.from_user; receiver=message.reply_to_message.from_user
    if sender.id == receiver.id:
        sent = bot.reply_to(message, "Собі?"); auto_delete(sent, 15); return
    if get_coins(sender.id) < amount:
        sent = bot.reply_to(message, "Не вистачає монет!"); auto_delete(sent, 15); return
    add_coins(sender.id, -amount); add_coins(receiver.id, amount)
    sent = bot.send_message(message.chat.id,
        f"<b>{sender.first_name}</b> -> <b>{amount} монет</b> -> <b>{receiver.first_name}</b>",
        parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!ставка'))
def casino(message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        sent = bot.reply_to(message, "!ставка [сума]"); auto_delete(sent, 15); return
    bet=int(parts[1]); uid=message.from_user.id; coins=get_coins(uid)
    if coins < bet or bet < 1:
        sent = bot.reply_to(message, f"Недостатньо монет! ({coins})"); auto_delete(sent, 15); return
    r = random.random()
    if r < 0.45:
        add_coins(uid, bet)
        sent = bot.send_message(message.chat.id, f"<b>ВИГРАШ!</b> +{bet} монет | Баланс: {get_coins(uid)}", parse_mode="HTML")
    elif r < 0.50:
        win=bet*4; add_coins(uid, win); give_achievement(uid,'везунчик',message.chat.id)
        sent = bot.send_message(message.chat.id, f"<b>ДЖЕКПОТ!</b> +{win} монет | Баланс: {get_coins(uid)}", parse_mode="HTML")
    else:
        add_coins(uid, -bet)
        sent = bot.send_message(message.chat.id, f"Програв {bet} монет | Баланс: {get_coins(uid)}", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!крамниця')
def show_shop(message):
    sent = bot.send_message(message.chat.id,
        "<b>Крамниця Драго:</b>\n\nVIP - 500 монет -> /д_купити vip\n"
        "Мут юзера 1 год - 200 монет -> /д_купити mute\n"
        "Мем - 50 монет -> /д_купити meme", parse_mode="HTML")
    auto_delete(sent, 60)

@bot.message_handler(commands=['д_купити'])
def buy_item(message):
    parts = message.text.split()
    if len(parts) < 2:
        sent = bot.reply_to(message, "/д_купити vip | meme | mute"); auto_delete(sent, 15); return
    item=parts[1].lower(); uid=message.from_user.id; coins=get_coins(uid)
    if item == 'vip':
        if coins < 500:
            sent = bot.reply_to(message, f"Треба 500, у тебе {coins}"); auto_delete(sent, 15); return
        add_coins(uid, -500)
        sent = bot.reply_to(message, "Купив VIP!"); auto_delete(sent, 30)
    elif item == 'meme':
        if coins < 50:
            sent = bot.reply_to(message, f"Треба 50, у тебе {coins}"); auto_delete(sent, 15); return
        add_coins(uid, -50)
        try:
            resp = model.generate_content("Смішний мем-текст про Telegram чат.")
            sent = bot.reply_to(message, resp.text)
        except:
            sent = bot.reply_to(message, "'Коли купив VIP але нічого не змінилось'")
        auto_delete(sent, 30)
    elif item == 'mute':
        if coins < 200:
            sent = bot.reply_to(message, f"Треба 200, у тебе {coins}"); auto_delete(sent, 15); return
        if not message.reply_to_message:
            sent = bot.reply_to(message, "Реплай на юзера!"); auto_delete(sent, 15); return
        target = message.reply_to_message.from_user
        try:
            add_coins(uid, -200)
            bot.restrict_chat_member(message.chat.id, target.id,
                until_date=int(time.time())+3600,
                permissions=types.ChatPermissions(can_send_messages=False))
            sent = bot.send_message(message.chat.id,
                f"<b>{message.from_user.first_name}</b> купив мут для <b>{target.first_name}</b> на 1 год!",
                parse_mode="HTML")
            auto_delete(sent, 30)
        except Exception as e: bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(commands=['д_загадка'])
def start_trivia(message):
    try:
        resp  = model.generate_content(
            "Придумай питання вікторини українською.\nФормат:\nПИТАННЯ: [текст]\nВІДПОВІДЬ: [відповідь]")
        lines = resp.text.strip().split('\n')
        q = a = None
        for line in lines:
            if line.startswith('ПИТАННЯ:'): q = line.replace('ПИТАННЯ:','').strip()
            elif line.startswith('ВІДПОВІДЬ:'): a = line.replace('ВІДПОВІДЬ:','').strip().lower()
        if not q or not a: raise Exception("parse error")
        with db_lock:
            cursor.execute("INSERT OR REPLACE INTO trivia (chat_id,question,answer,active) VALUES(?,?,?,1)",
                           (message.chat.id,q,a)); conn.commit()
        bot.send_message(message.chat.id,
            f"<b>ВІКТОРИНА!</b>\n\n{q}\n\nПравильна відповідь = +50 монет!\nСкасувати: /д_стоп_загадка",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Trivia: {e}")
        sent = bot.reply_to(message, "Не зміг придумати."); auto_delete(sent, 15)

@bot.message_handler(commands=['д_стоп_загадка'])
def stop_trivia(message):
    with db_lock:
        cursor.execute("UPDATE trivia SET active=0 WHERE chat_id=?", (message.chat.id,)); conn.commit()
    sent = bot.reply_to(message, "Вікторину скасовано."); auto_delete(sent, 15)

def check_trivia_answer(message):
    with db_lock:
        cursor.execute("SELECT question,answer FROM trivia WHERE chat_id=? AND active=1", (message.chat.id,))
        row = cursor.fetchone()
    if not row: return False
    q,a = row; ua = message.text.lower().strip()
    if a in ua or ua in a:
        with db_lock:
            cursor.execute("UPDATE trivia SET active=0 WHERE chat_id=?", (message.chat.id,)); conn.commit()
        add_coins(message.from_user.id, 50)
        bot.send_message(message.chat.id,
            f"<b>{message.from_user.first_name}</b> відповів правильно!\nВідповідь: <b>{a}</b> | +50 монет",
            parse_mode="HTML")
        return True
    return False

@bot.message_handler(commands=['д_памятка'])
def set_reminder(message):
    parts = message.text.split(' ', 2)
    if len(parts) < 3:
        sent = bot.reply_to(message, "Формат: /д_памятка 30хв Зустріч"); auto_delete(sent, 15); return
    time_str=parts[1].lower(); text_r=parts[2]; seconds=0
    if 'хв' in time_str or 'min' in time_str:
        seconds = int(re.sub(r'[^\d]','',time_str) or 0)*60
    elif 'год' in time_str or 'h' in time_str:
        seconds = int(re.sub(r'[^\d]','',time_str) or 0)*3600
    elif 'с' in time_str:
        seconds = int(re.sub(r'[^\d]','',time_str) or 0)
    if seconds < 1:
        sent = bot.reply_to(message, "Не зрозумів час. Приклад: 30хв, 2год, 60с"); auto_delete(sent, 15); return
    with db_lock:
        cursor.execute("INSERT INTO reminders (user_id,chat_id,remind_at,text) VALUES(?,?,?,?)",
                       (message.from_user.id, message.chat.id, int(time.time())+seconds, text_r)); conn.commit()
    sent = bot.reply_to(message,
        f"Нагадаю через <b>{str(timedelta(seconds=seconds))}</b>:\n<i>{text_r}</i>", parse_mode="HTML")
    auto_delete(sent, 30)

def reminder_worker():
    while True:
        now = int(time.time())
        with db_lock:
            cursor.execute("SELECT id,user_id,chat_id,text FROM reminders WHERE remind_at<=? AND done=0", (now,))
            rows = cursor.fetchall()
        for rid,uid,cid,text_r in rows:
            try:
                bot.send_message(cid,
                    f"<b>Нагадування!</b>\n\n<a href='tg://user?id={uid}'>Привіт!</a> Ти просив нагадати:\n<i>{text_r}</i>",
                    parse_mode="HTML")
            except Exception as e: logger.error(f"Reminder: {e}")
            with db_lock:
                cursor.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,)); conn.commit()
        time.sleep(10)

@bot.message_handler(commands=['д_перекласти'])
def translate_text(message):
    parts = message.text.split(' ', 2)
    if len(parts) < 3 and not message.reply_to_message:
        sent = bot.reply_to(message, "/д_перекласти [мова] [текст] або реплай"); auto_delete(sent, 15); return
    if message.reply_to_message and len(parts) < 3:
        lang=parts[1] if len(parts)>1 else 'англійська'; text_=message.reply_to_message.text or ""
    else:
        lang=parts[1]; text_=parts[2]
    try:
        resp = model.generate_content(f"Переклади на {lang}. ТІЛЬКИ переклад: {text_}")
        sent = bot.reply_to(message, f"<b>({lang}):</b>\n{resp.text}", parse_mode="HTML")
        auto_delete(sent, 60)
    except: bot.reply_to(message, "Помилка перекладу.")

@bot.message_handler(commands=['д_стиснути'])
def summarize_text(message):
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    text_ = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not text_:
        sent = bot.reply_to(message, "Немає тексту."); auto_delete(sent, 15); return
    try:
        resp = model.generate_content(f"Стисни до 2-3 речень українською: {text_}")
        sent = bot.reply_to(message, f"<b>Коротко:</b>\n{resp.text}", parse_mode="HTML")
        auto_delete(sent, 60)
    except: bot.reply_to(message, "Помилка.")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!вікі'))
def wikipedia_search(message):
    query = message.text[5:].strip()
    if not query:
        sent = bot.reply_to(message, "!вікі Місяць"); auto_delete(sent, 15); return
    try:
        data = requests.get(
            f"https://uk.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(query)}",
            timeout=10).json()
        if 'extract' not in data:
            sent = bot.reply_to(message, f"Нічого про '{query}'."); auto_delete(sent, 15); return
        extract  = data['extract'][:800]+('...' if len(data['extract'])>800 else '')
        page_url = data.get('content_urls',{}).get('desktop',{}).get('page','')
        sent = bot.send_message(message.chat.id,
            f"<b>{data.get('title',query)}</b>\n\n{extract}\n\n<a href='{page_url}'>Повністю</a>",
            parse_mode="HTML", disable_web_page_preview=True)
        auto_delete(sent, 90)
    except Exception as e:
        logger.error(f"Wiki: {e}"); bot.reply_to(message, "Помилка Wikipedia.")

@bot.message_handler(func=lambda m: m.text and re.match(r'^!курс\s+\w+$', m.text.strip().lower()))
def currency_rate(message):
    currency = message.text.split()[1].upper()
    try:
        data = requests.get("https://api.exchangerate-api.com/v4/latest/UAH", timeout=10).json()
        if currency not in data.get('rates',{}):
            sent = bot.reply_to(message, f"Не знайшов '{currency}'."); auto_delete(sent, 15); return
        rate = data['rates'][currency]
        sent = bot.send_message(message.chat.id,
            f"<b>{currency}/UAH</b>\n\n1 {currency} = <b>{1/rate:.2f} грн</b>", parse_mode="HTML")
        auto_delete(sent, 60)
    except Exception as e:
        logger.error(f"Currency: {e}"); bot.reply_to(message, "Помилка курсу.")

@bot.message_handler(func=lambda m: m.text and re.match(r'^!монета\s+\w+$', m.text.strip().lower()))
def crypto_rate(message):
    coin = message.text.split()[1].upper()
    ids_map = {'BTC':'bitcoin','ETH':'ethereum','BNB':'binancecoin','SOL':'solana',
               'XRP':'ripple','ADA':'cardano','DOGE':'dogecoin','TON':'the-open-network',
               'TRX':'tron','MATIC':'matic-network'}
    coin_id = ids_map.get(coin, coin.lower())
    try:
        data = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd,uah",
            timeout=10).json()
        if coin_id not in data:
            sent = bot.reply_to(message, f"Не знайшов '{coin}'."); auto_delete(sent, 15); return
        usd=data[coin_id].get('usd',0); uah=data[coin_id].get('uah',0)
        sent = bot.send_message(message.chat.id,
            f"<b>{coin}</b>\n${usd:,.2f}\n{uah:,.0f} грн", parse_mode="HTML")
        auto_delete(sent, 60)
    except Exception as e:
        logger.error(f"Crypto: {e}"); bot.reply_to(message, "Помилка крипто.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!знаєш')
def random_fact(message):
    try:
        resp = model.generate_content("Один цікавий факт. 2-3 речення, стиль Драго.")
        sent = bot.send_message(message.chat.id, resp.text); auto_delete(sent, 30)
    except:
        sent = bot.reply_to(message, "Мозок завис."); auto_delete(sent, 15)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!смішно','!жарт'])
def tell_joke(message):
    try:
        resp = model.generate_content("Короткий смішний анекдот українською.")
        sent = bot.send_message(message.chat.id, resp.text); auto_delete(sent, 30)
    except:
        sent = bot.reply_to(message, "Жарти скінчились"); auto_delete(sent, 15)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!зірки'))
def horoscope(message):
    sign = message.text.lower().split()[1] if len(message.text.split()) > 1 else ""
    if sign not in ZODIAC_SIGNS:
        sent = bot.reply_to(message, f"Знаки: {', '.join(ZODIAC_SIGNS.keys())}"); auto_delete(sent, 20); return
    try:
        resp = model.generate_content(f"Гороскоп для {sign} на сьогодні. Стиль Драго, 3-4 речення.")
        sent = bot.send_message(message.chat.id,
            f"<b>Гороскоп {sign.capitalize()}:</b>\n\n{resp.text}", parse_mode="HTML")
        auto_delete(sent, 60)
    except: bot.reply_to(message, "Зірки мовчать.")

@bot.message_handler(commands=['д_малюй'])
def generate_image(message):
    prompt = message.text.split(' ', 1)[1].strip() if len(message.text.split()) > 1 else ''
    if not prompt:
        sent = bot.reply_to(message, "/д_малюй [опис]"); auto_delete(sent, 15); return
    msg = bot.reply_to(message, "Малюю... до 2 хвилин.")
    try:
        url = (f"https://image.pollinations.ai/p/{requests.utils.quote(prompt)}"
               f"?width=1024&height=1024&seed={random.randint(1,999999)}&model=flux&nologo=true")
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and len(r.content) >= 10000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            bio = io.BytesIO(); bio.name = 'art.jpg'
            img.save(bio, 'JPEG', quality=95); bio.seek(0)
            bot.send_photo(message.chat.id, bio,
                caption=f"<b>{prompt}</b>", parse_mode="HTML",
                reply_to_message_id=message.message_id)
            bot.delete_message(message.chat.id, msg.message_id)
        else:
            raise Exception(f"HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Generate: {e}")
        bot.edit_message_text("Не зміг. Спробуй пізніше.", message.chat.id, msg.message_id)

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    if message.chat.type in ['group','supergroup']:
        if not (message.reply_to_message and
                message.reply_to_message.from_user.id == bot.get_me().id): return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        fi = bot.get_file(message.voice.file_id)
        data = bot.download_file(fi.file_path)
        resp = model.generate_content([
            "Послухай голосове і відповідж як Драго:",
            {"data": data, "mime_type": "audio/ogg"}])
        bot.reply_to(message, resp.text)
    except Exception as e:
        logger.error(f"Voice: {e}"); bot.reply_to(message, "Не зміг розпізнати.")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    caption = (message.caption or "").lower()
    is_group = message.chat.type in ['group','supergroup']
    if is_group:
        triggers = ['драго','джарвіс']
        if not (any(w in caption for w in triggers) or
                (message.reply_to_message and
                 message.reply_to_message.from_user.id == bot.get_me().id)): return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        fi = bot.get_file(message.photo[-1].file_id)
        data = bot.download_file(fi.file_path)
        p = caption if caption else "Опиши що на фото детально і дотепно в стилі Драго."
        resp = model.generate_content([p, {"data": data, "mime_type": "image/jpeg"}])
        bot.reply_to(message, resp.text)
    except Exception as e:
        logger.error(f"Photo: {e}"); bot.reply_to(message, "Не зміг розглянути фото.")

@bot.message_handler(commands=['д_слова'])
def start_word_game(message):
    game_state[message.chat.id] = {"last_letter": None, "used_words": []}
    sent = bot.reply_to(message, "Гра в слова! Пиши перше слово."); auto_delete(sent, 20)

@bot.message_handler(commands=['д_стоп'])
def stop_word_game(message):
    if message.chat.id in game_state:
        del game_state[message.chat.id]
        sent = bot.reply_to(message, "Гру зупинено"); auto_delete(sent, 15)
    else:
        sent = bot.reply_to(message, "Гра не запущена."); auto_delete(sent, 15)

def handle_word_game(message):
    word = message.text.lower().strip()
    state = game_state[message.chat.id]
    if not word.replace(" ","").isalpha() or len(word) < 2: return
    if state["last_letter"] and word[0] != state["last_letter"]:
        sent = bot.reply_to(message, f"Не-а! Має починатись на '{state['last_letter'].upper()}'.")
        auto_delete(sent, 15); return
    if word in state["used_words"]:
        sent = bot.reply_to(message, "Слово вже було!"); auto_delete(sent, 15); return
    state["used_words"].append(word)
    nl = word[-1] if word[-1] not in ['ь','и','й','ї'] else word[-2]
    state["last_letter"] = nl
    sent = bot.reply_to(message, f"Прийнято! Наступне на '{nl.upper()}'."); auto_delete(sent, 20)

@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    new_s=message.new_chat_member.status; old_s=message.old_chat_member.status
    user=message.new_chat_member.user
    if new_s in ['member','administrator','restricted'] and not user.is_bot:
        with db_lock:
            cursor.execute("INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)",
                           (user.id, user.first_name)); conn.commit()
        bot.send_message(message.chat.id,
            f"Вітаємо, <b>{user.first_name}</b>!\nТобі нараховано 100 стартових монет\n\n"
            f"<code>!стать хлопець/дівчина</code> | <code>!місто Київ</code> | /допомога",
            parse_mode="HTML")
    elif old_s in ['member','administrator','restricted'] and new_s in ['left','kicked']:
        byes=[f"Ну і пофіг, <b>{message.old_chat_member.user.first_name}</b> пішов.",
              f"<b>{message.old_chat_member.user.first_name}</b> злиняв. Менше народу - більше кисню.",
              f"<b>{message.old_chat_member.user.first_name}</b> не витримав нашого рівня інтелекту."]
        bot.send_message(message.chat.id, random.choice(byes), parse_mode="HTML")

@bot.message_handler(commands=['д_дд'])
def warn_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns=warns+1 WHERE user_id=?", (target.id,))
        cursor.execute("SELECT warns FROM stats WHERE user_id=?", (target.id,))
        row = cursor.fetchone(); conn.commit()
    warns = row[0] if row else 1
    if warns >= 3:
        give_achievement(target.id,'токсик',message.chat.id)
        try:
            bot.restrict_chat_member(message.chat.id, target.id,
                until_date=int(time.time())+3600,
                permissions=types.ChatPermissions(can_send_messages=False))
            bot.send_message(message.chat.id,
                f"<b>{target.first_name}</b> - 3 варни! Мут на 1 год!", parse_mode="HTML")
        except Exception as e: bot.reply_to(message, f"Не зміг: {e}")
    else:
        sent = bot.send_message(message.chat.id,
            f"<b>{target.first_name}</b> - варн {warns}/3", parse_mode="HTML")
        auto_delete(sent, 30)

@bot.message_handler(commands=['д_знятидд'])
def remove_warn(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns=MAX(0,warns-1) WHERE user_id=?", (target.id,))
        cursor.execute("SELECT warns FROM stats WHERE user_id=?", (target.id,))
        row = cursor.fetchone(); conn.commit()
    warns = row[0] if row else 0
    sent = bot.send_message(message.chat.id,
        f"<b>{target.first_name}</b> - знято варн. Зараз {warns}/3", parse_mode="HTML")
    auto_delete(sent, 30)

@bot.message_handler(commands=['д_мут'])
def mute_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    parts = message.text.split()
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            until_date=int(time.time())+minutes*60,
            permissions=types.ChatPermissions(can_send_messages=False))
        sent = bot.send_message(message.chat.id,
            f"<b>{target.first_name}</b> отримав мут на <b>{minutes} хв</b>!", parse_mode="HTML")
        auto_delete(sent, 30)
    except Exception as e: bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(commands=['д_знятимут'])
def unmute_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            permissions=types.ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True, can_change_info=False,
                can_invite_users=True, can_pin_messages=False))
        sent = bot.send_message(message.chat.id,
            f"<b>{target.first_name}</b> розмутований!", parse_mode="HTML")
        auto_delete(sent, 30)
    except Exception as e: bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(commands=['д_бан'])
def ban_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        sent = bot.send_message(message.chat.id,
            f"<b>{target.first_name}</b> заблокований!", parse_mode="HTML")
        auto_delete(sent, 30)
    except Exception as e: bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(commands=['д_кік'])
def kick_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        time.sleep(0.5)
        bot.unban_chat_member(message.chat.id, target.id)
        sent = bot.send_message(message.chat.id,
            f"<b>{target.first_name}</b> вилетів з чату!", parse_mode="HTML")
        auto_delete(sent, 30)
    except Exception as e: bot.reply_to(message, f"Не зміг: {e}")

@bot.message_handler(commands=['д_аналіз'])
def analyze_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        sent = bot.reply_to(message, "Реплай!"); auto_delete(sent, 15); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("SELECT count,gender,warns,coins,rep,city FROM stats WHERE user_id=?", (target.id,))
        row = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM achievements WHERE user_id=?", (target.id,))
        ach_count = cursor.fetchone()[0]
    if not row:
        sent = bot.reply_to(message, "Немає даних."); auto_delete(sent, 15); return
    count,gender,warns,coins,rep,city = row
    try:
        resp = model.generate_content(
            f"Коротка характеристика юзера Telegram: "
            f"Повідомлень={count}, Стать={gender}, Варни={warns}, Монети={coins}, "
            f"Репутація={rep}, Місто={city or 'невідомо'}, Досягнень={ach_count}. "
            f"Стиль Драго, 3-4 речення.")
        text = resp.text
    except:
        text = "Аналіз недоступний."
    sent = bot.send_message(message.chat.id,
        f"<b>Аналіз {target.first_name}:</b>\n\n{text}", parse_mode="HTML")
    auto_delete(sent, 60)

@bot.message_handler(commands=['д_настрій'])
def analyze_mood(message):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        cursor.execute("""
            SELECT s.name, ds.count FROM daily_stats ds
            JOIN stats s ON s.user_id=ds.user_id
            WHERE ds.chat_id=? AND ds.date=?
            ORDER BY ds.count DESC LIMIT 5
        """, (message.chat.id, today))
        rows = cursor.fetchall()
    if not rows:
        sent = bot.reply_to(message, "Немає даних за сьогодні."); auto_delete(sent, 15); return
    top_text = "\n".join(f"{name}: {cnt} повідомлень" for name,cnt in rows)
    try:
        resp = model.generate_content(
            f"Визнач настрій чату за активністю:\n{top_text}\nВідповідь: 1 речення в стилі Драго.")
        sent = bot.send_message(message.chat.id,
            f"<b>Настрій чату:</b>\n\n{resp.text}", parse_mode="HTML")
        auto_delete(sent, 30)
    except:
        sent = bot.reply_to(message, "Не зміг проаналізувати."); auto_delete(sent, 15)

@bot.message_handler(commands=['допомога', 'д_допомога', 'help'])
def show_help(message):
    sent = bot.send_message(message.chat.id,
        "<b>КОМАНДИ ДРАГО:</b>\n\n"
        "<b>Активність:</b>\n"
        "!хто - топ активних | !я - профіль | !нагороди - досягнення\n\n"
        "<b>Економіка:</b>\n"
        "!бонус - щоденний бонус | !лотерея | !гаманець\n"
        "!ставка [N] - казино | !фортуна | !дати [N] (реплай)\n"
        "!крамниця | /д_купити vip/meme/mute\n\n"
        "<b>Розваги:</b>\n"
        "!монетка | !кості [N] | !пчд | !куля [питання]\n"
        "!роль | !токсик | !любов (реплай) | !токсик (реплай)\n"
        "!хто_кращий A vs B | !голос [питання] | !цитата\n"
        "!батл (реплай) | !прийняти | !відмовити\n"
        "!конфесія [текст] | !прогноз\n\n"
        "<b>Інфо:</b>\n"
        "!погода [місто] | !вікі [запит] | !знаєш | !жарт\n"
        "!зірки [знак] | !курс [валюта] | !монета [крипто]\n\n"
        "<b>Налаштування:</b>\n"
        "!стать хлопець/дівчина | !місто [назва]\n\n"
        "<b>Ігри:</b>\n"
        "/д_загадка | /д_стоп_загадка\n"
        "/д_слова | /д_стоп\n"
        "/д_памятка [час] [текст]\n\n"
        "<b>Адмін:</b>\n"
        "/д_дд /д_знятидд /д_мут [хв] /д_знятимут\n"
        "/д_бан /д_кік /д_аналіз /д_настрій /д_зведення\n\n"
        "<b>ШІ:</b>\n"
        "Напиши 'Драго' або реплай на бота\n"
        "/д_малюй [опис] | /д_перекласти [мова] [текст]\n"
        "/д_стиснути (реплай)",
        parse_mode="HTML")
    auto_delete(sent, 120)

@bot.message_handler(func=lambda m: m.text)
def main_handler(message):
    if message.chat.type not in ['group','supergroup','private']:
        return
    user_id   = message.from_user.id
    chat_id   = message.chat.id
    chat_type = message.chat.type
    name      = message.from_user.first_name
    text      = message.text or ""
    if not text.strip():
        return
    update_message_count(user_id, name, chat_id)
    # Антифлуд
    if chat_type in ['group','supergroup']:
        if check_flood(user_id, chat_id):
            try:
                bot.restrict_chat_member(chat_id, user_id,
                    until_date=int(time.time())+MUTE_DURATION,
                    permissions=types.ChatPermissions(can_send_messages=False))
                bot.send_message(chat_id, f"<b>{name}</b>, флуд -> мут 5 хв!", parse_mode="HTML")
            except Exception as e: logger.error(f"Antispam: {e}")
            return
    # Антимат
    if has_bad_words(text) and chat_type in ['group','supergroup']:
        if not is_admin(chat_id, user_id):
            bot.reply_to(message, f"Ей, <b>{name}</b>, стеж за лексикою!", parse_mode="HTML")
    # Авто-реакції
    text_lower = text.lower()
    for keyword, responses in AUTO_REACTIONS.items():
        if keyword in text_lower and random.random() < 0.30:
            bot.send_message(chat_id, random.choice(responses)); break
    # Рап-батл
    if chat_id in rap_pending:
        battle = rap_pending[chat_id]
        if (battle.get('stage') == 'waiting_rap1' and
                user_id == battle['challenger_id'] and message.reply_to_message):
            battle['challenger_rap'] = text; battle['stage'] = 'waiting_rap2'
            bot.send_message(chat_id,
                f"Реп <b>{battle['challenger_name']}</b> прийнято!\n\n"
                f"<b>{battle['opponent_name']}</b> - твоя черга (реплай):", parse_mode="HTML")
            return
        elif (battle.get('stage') == 'waiting_rap2' and
              user_id == battle['opponent_id'] and message.reply_to_message):
            battle['opponent_rap'] = text
            try:
                prompt = (f"Ти суддя рап-батлу.\n\n"
                          f"{battle['challenger_name']}: {battle.get('challenger_rap','...')}\n\n"
                          f"{battle['opponent_name']}: {battle['opponent_rap']}\n\n"
                          f"Оцінка кожному (1-10) + переможець. Стиль Драго.")
                resp = model.generate_content(prompt)
                bot.send_message(chat_id, f"<b>ВЕРДИКТ ДРАГО:</b>\n\n{resp.text}", parse_mode="HTML")
            except:
                winner = random.choice([battle['challenger_name'], battle['opponent_name']])
                bot.send_message(chat_id, f"Переможець: <b>{winner}</b>!", parse_mode="HTML")
            rap_pending.pop(chat_id); return
    # Вікторина
    if check_trivia_answer(message): return
    # Гра в слова
    if chat_id in game_state:
        handle_word_game(message); return
    # Аналіз статі
    with db_lock:
        cursor.execute("SELECT gender FROM stats WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    if row and row[0] == 'не вказано':
        g = analyze_gender(text)
        if g in ['Хлопець','Дівчина']:
            with db_lock:
                cursor.execute("UPDATE stats SET gender=? WHERE user_id=?", (g, user_id)); conn.commit()
            bot.send_message(chat_id,
                f"Драго вирішив що ти - {g.lower()}. Вгадав?\n"
                f"Якщо ні - <code>!стать хлопець/дівчина</code>", parse_mode="HTML")
    # Діалог з Драго
    is_mentioned = False
    if chat_type in ['group','supergroup']:
        triggers = ['драго','джарвіс']
        if (any(w in text_lower for w in triggers) or
                f"@{bot.get_me().username}" in text or
                (message.reply_to_message and
                 message.reply_to_message.from_user.id == bot.get_me().id)):
            is_mentioned = True
            for w in triggers:
                if text_lower.startswith(w): text = text[len(w):].strip(); break
    else:
        is_mentioned = True
    if not is_mentioned: return
    status_msg = None
    try:
        bot.send_chat_action(chat_id, 'typing')
        status_msg  = bot.reply_to(message, "Йде відправка даних в СБУ...")
        gemini_chat = get_gemini_chat(chat_id, user_id)
        response    = gemini_chat.send_message(text)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text=response.text, parse_mode="Markdown")
        except:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text)
    except genai.types.generation_types.BlockedPromptException:
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text="Цей запит заблоковано Google.")
    except Exception as e:
        logger.error(f"Dialog: {e}")
        err = "Сервери прилягли, спробуй пізніше."
        if "ResourceExhausted" in str(e) or "quota" in str(e).lower():
            err = "Пригальмуй! Google каже почекати хвилину..."
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=err)
        else:
            bot.reply_to(message, err)

# ===================================================================
# ЗАПУСК
# ===================================================================
threading.Thread(target=run_dummy_server, daemon=True).start()
threading.Thread(target=reminder_worker,  daemon=True).start()

if __name__ == "__main__":
    logger.info("DRAGO BOT ЗАПУЩЕНИЙ!")
    print("=" * 45)
    print("   DRAGO BOT - ФІНАЛЬНА ВЕРСІЯ v3!   ")
    print("=" * 45)
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
