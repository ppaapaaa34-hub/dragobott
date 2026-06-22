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
    coins INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS daily_stats (
    user_id INTEGER,
    chat_id INTEGER,
    date TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    chat_id INTEGER,
    remind_at INTEGER,
    text TEXT,
    done INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trivia (
    chat_id INTEGER PRIMARY KEY,
    question TEXT,
    answer TEXT,
    active INTEGER DEFAULT 0
);
""")
conn.commit()

# ===================================================================
# ⚙️ НАЛАШТУВАННЯ
# ===================================================================
TELEGRAM_TOKEN   = '8788139276:AAGKr6sFii4n9B1E5sysHSa-xMTgYsmUZfI'
GEMINI_API_KEY   = 'AIzaSyC_7U44ek_eaN0u6GV4FqL-m1N9OcpvVJM'
WEATHER_API_KEY  = 'ТВІЙ_КЛЮЧ_OPENWEATHER'   # openweathermap.org

FLOOD_LIMIT      = 5
FLOOD_TIME       = 10
MUTE_DURATION    = 300

BAD_WORDS = [
    'хуй', 'піздець', 'пізда', 'єбать', 'їбать',
    'блять', 'сука', 'нігер', 'гандон', 'залупа',
    'мудак', 'пидор', 'пидорас'
]

ZODIAC_SIGNS = {
    'овен': 'Aries', 'телець': 'Taurus', 'близнюки': 'Gemini',
    'рак': 'Cancer', 'лев': 'Leo', 'діва': 'Virgo',
    'терези': 'Libra', 'скорпіон': 'Scorpio', 'стрілець': 'Sagittarius',
    'козоріг': 'Capricorn', 'водолій': 'Aquarius', 'риби': 'Pisces'
}

# ===================================================================
# 🤖 ІНІЦІАЛІЗАЦІЯ
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
        "Ти — зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
        "Спілкуйся виключно українською мовою. Використовуй молодіжний сленг, "
        "іноді нецензурну лексику для емоційності. Будь живим, неформальним, "
        "злегка іронізуй, підколюй юзера, але завжди допомагай. "
        "Завжди закінчуй думки! Пиши коротко і ясно!"
    )
)

bot_chats  = {}
flood_data = defaultdict(list)
game_state = {}

def get_gemini_chat(chat_id):
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]

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
    flood_data[key] = [t for t in flood_data[key] if now - t < FLOOD_TIME]
    flood_data[key].append(now)
    return len(flood_data[key]) > FLOOD_LIMIT

def update_message_count(user_id, name, chat_id):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        cursor.execute(
            "INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)", (user_id, name))
        cursor.execute(
            "UPDATE stats SET count = count + 1, name = ?, coins = coins + 1 WHERE user_id = ?",
            (name, user_id))
        cursor.execute(
            "INSERT INTO daily_stats (user_id, chat_id, date, count) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(user_id, chat_id, date) DO UPDATE SET count = count + 1",
            (user_id, chat_id, today))
        conn.commit()

def get_coins(user_id):
    with db_lock:
        cursor.execute("SELECT coins FROM stats WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

def add_coins(user_id, amount):
    with db_lock:
        cursor.execute(
            "INSERT OR IGNORE INTO stats (user_id, name, coins) VALUES (?, 'Unknown', 100)",
            (user_id,))
        cursor.execute(
            "UPDATE stats SET coins = MAX(0, coins + ?) WHERE user_id = ?",
            (amount, user_id))
        conn.commit()

def run_dummy_server():
    port  = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(("", port), SimpleHTTPRequestHandler)
    httpd.serve_forever()

# ===================================================================
# 🎭 МЕМ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.мем')
def send_meme(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        meme_dir = r"D:\DragoBot\memes"
        if os.path.exists(meme_dir):
            memes = [f for f in os.listdir(meme_dir)
                     if f.endswith(('.png', '.jpg', '.jpeg', '.gif'))]
            if memes:
                with open(os.path.join(meme_dir, random.choice(memes)), 'rb') as ph:
                    bot.send_photo(message.chat.id, ph)
            else:
                bot.send_message(message.chat.id, "Папка з мемами порожня!")
        else:
            bot.send_message(message.chat.id, "Не знайшов папку з мемами.")
    except Exception as e:
        logger.error(f"Мем: {e}")

# ===================================================================
# 🖼️ ГЕНЕРАЦІЯ ЗОБРАЖЕНЬ
# ===================================================================
@bot.message_handler(commands=['generate'])
def generate_image(message):
    prompt = message.text[10:].strip()
    if not prompt:
        bot.reply_to(message, "⚠️ Напиши опис! /generate cyberpunk wolf")
        return
    msg = bot.reply_to(message, "⏳ Малюю... зачекай до 2 хвилин.")
    try:
        url = (f"https://image.pollinations.ai/p/{requests.utils.quote(prompt)}"
               f"?width=1024&height=1024&seed={random.randint(1,999999)}&model=flux&nologo=true")
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and len(r.content) >= 10000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            bio = io.BytesIO(); bio.name = 'art.jpg'
            img.save(bio, 'JPEG', quality=95); bio.seek(0)
            bot.send_photo(message.chat.id, bio,
                           caption=f"🔥 Готово!\n📋 <b>{prompt}</b>",
                           parse_mode="HTML",
                           reply_to_message_id=message.message_id)
            bot.delete_message(message.chat.id, msg.message_id)
        else:
            raise Exception(f"HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Generate: {e}")
        bot.edit_message_text(f"❌ Не зміг. Спробуй пізніше.", message.chat.id, msg.message_id)

# ===================================================================
# 🎙️ ГОЛОСОВІ
# ===================================================================
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
            "Послухай голосове і відповідж як Драго:",
            {"data": data, "mime_type": "audio/ogg"}
        ])
        bot.reply_to(message, resp.text)
    except Exception as e:
        logger.error(f"Voice: {e}")
        bot.reply_to(message, "Не зміг розпізнати голосове.")

# ===================================================================
# 📸 АНАЛІЗ ФОТО (Gemini Vision)
# ===================================================================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    caption = (message.caption or "").lower()
    is_group = message.chat.type in ['group', 'supergroup']
    # В групі — лише при реплаї або підписі з ключовим словом
    if is_group:
        trigger_words = ['драго', 'джарвіс']
        mentioned = any(w in caption for w in trigger_words)
        replied_to_bot = (message.reply_to_message and
                          message.reply_to_message.from_user.id == bot.get_me().id)
        if not mentioned and not replied_to_bot:
            return
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        fi   = bot.get_file(message.photo[-1].file_id)
        data = bot.download_file(fi.file_path)
        prompt = caption if caption else "Опиши що на цьому фото детально і дотепно в стилі Драго."
        resp = model.generate_content([
            prompt,
            {"data": data, "mime_type": "image/jpeg"}
        ])
        bot.reply_to(message, resp.text)
    except Exception as e:
        logger.error(f"Photo: {e}")
        bot.reply_to(message, "Не зміг розглянути фото.")

# ===================================================================
# 👋 ВХІД / ВИХІД
# ===================================================================
@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    new_s = message.new_chat_member.status
    old_s = message.old_chat_member.status
    user  = message.new_chat_member.user

    if new_s in ['member', 'administrator', 'restricted'] and not user.is_bot:
        with db_lock:
            cursor.execute(
                "INSERT OR IGNORE INTO stats (user_id, name) VALUES (?, ?)",
                (user.id, user.first_name))
            conn.commit()
        bot.send_message(
            message.chat.id,
            f"Вітаємо, <b>{user.first_name}</b>! 🤍\n"
            f"Тобі нараховано 100 стартових монет 🪙\n"
            f"Драго хоче знати — ти хлопець чи дівчина? Просто напиши щось!",
            parse_mode="HTML"
        )
    elif old_s in ['member', 'administrator', 'restricted'] and new_s in ['left', 'kicked']:
        name = message.old_chat_member.user.first_name
        byes = [
            f"Ну і пофіг, <b>{name}</b> пішов. 👋",
            f"<b>{name}</b> покинув чат. Менше народу — більше кисню. 🚪",
            f"<b>{name}</b> злиняв. Не витримав нашого інтелекту 🧠",
        ]
        bot.send_message(message.chat.id, random.choice(byes), parse_mode="HTML")

# ===================================================================
# 🎮 ГРА В СЛОВА
# ===================================================================
@bot.message_handler(commands=['game'])
def start_word_game(message):
    game_state[message.chat.id] = {"last_letter": None, "used_words": []}
    bot.reply_to(message, "🎲 Гра в слова! Пиши перше слово.")

@bot.message_handler(commands=['stop'])
def stop_word_game(message):
    if message.chat.id in game_state:
        del game_state[message.chat.id]
        bot.reply_to(message, "Гру зупинено 👋")
    else:
        bot.reply_to(message, "Гра не запущена.")

def handle_word_game(message):
    word  = message.text.lower().strip()
    state = game_state[message.chat.id]
    if not word.replace(" ", "").isalpha() or len(word) < 2:
        return
    if state["last_letter"] and word[0] != state["last_letter"]:
        bot.reply_to(message, f"Не-а! Має починатись на '{state['last_letter'].upper()}'.")
        return
    if word in state["used_words"]:
        bot.reply_to(message, "Слово вже було! 😎")
        return
    state["used_words"].append(word)
    nl = word[-1] if word[-1] not in ['ь', 'и', 'й', 'ї'] else word[-2]
    state["last_letter"] = nl
    bot.reply_to(message, f"✅ Прийнято! Наступне на '{nl.upper()}'.")

# ===================================================================
# 📊 СТАТИСТИКА
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
        bot.reply_to(message, "Сьогодні тиша... ніхто нічого не писав."); return
    medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    text = "📊 <b>Топ балакунів сьогодні:</b>\n\n"
    for i, (name, count) in enumerate(rows):
        text += f"{medals[i]} <b>{name}</b> — {count} повідомлень\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.профіль')
def show_profile(message):
    uid  = message.from_user.id
    name = message.from_user.first_name
    with db_lock:
        cursor.execute("SELECT count, gender, warns, coins FROM stats WHERE user_id=?", (uid,))
        row = cursor.fetchone()
    if not row:
        bot.reply_to(message, "Ти ще не в базі. Пиши більше!"); return
    count, gender, warns, coins = row
    bot.send_message(message.chat.id,
        f"👤 <b>Профіль {name}</b>\n\n"
        f"💬 Повідомлень: <b>{count}</b>\n"
        f"🚻 Стать: <b>{gender}</b>\n"
        f"🪙 Монети: <b>{coins}</b>\n"
        f"⚠️ Варни: <b>{warns}/3</b>",
        parse_mode="HTML")

@bot.message_handler(commands=['stats'])
def show_group_stats(message):
    if not is_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "Тільки для адмінів!"); return
    with db_lock:
        cursor.execute("SELECT COUNT(*), SUM(count), SUM(coins) FROM stats")
        total_users, total_msgs, total_coins = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender='Хлопець'")
        boys  = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM stats WHERE gender='Дівчина'")
        girls = cursor.fetchone()[0]
    bot.send_message(message.chat.id,
        f"📈 <b>Статистика групи</b>\n\n"
        f"👥 Юзерів: <b>{total_users}</b>\n"
        f"💬 Повідомлень: <b>{total_msgs or 0}</b>\n"
        f"🪙 Монет в обігу: <b>{total_coins or 0}</b>\n"
        f"👦 Хлопців: <b>{boys}</b>  👧 Дівчат: <b>{girls}</b>",
        parse_mode="HTML")

# ===================================================================
# 💰 ЕКОНОМІКА (МОНЕТИ)
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.баланс')
def show_balance(message):
    coins = get_coins(message.from_user.id)
    bot.reply_to(message, f"🪙 Твій баланс: <b>{coins} монет</b>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.переказ'))
def transfer_coins(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Зроби реплай на повідомлення отримувача!"); return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "Формат: .переказ [сума] (реплай на юзера)"); return
    amount   = int(parts[1])
    sender   = message.from_user
    receiver = message.reply_to_message.from_user
    if sender.id == receiver.id:
        bot.reply_to(message, "Собі не переказуєш, бро 😄"); return
    if get_coins(sender.id) < amount:
        bot.reply_to(message, "Не вистачає монет!"); return
    add_coins(sender.id,   -amount)
    add_coins(receiver.id,  amount)
    bot.send_message(message.chat.id,
        f"✅ <b>{sender.first_name}</b> переказав <b>{amount} 🪙</b> → <b>{receiver.first_name}</b>",
        parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.казино'))
def casino(message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "Формат: .казино [ставка]"); return
    bet   = int(parts[1])
    uid   = message.from_user.id
    coins = get_coins(uid)
    if coins < bet:
        bot.reply_to(message, f"Недостатньо монет! У тебе {coins} 🪙"); return
    if bet < 1:
        bot.reply_to(message, "Мінімальна ставка — 1 монета!"); return
    result = random.random()
    if result < 0.45:      # 45% — виграш x2
        add_coins(uid, bet)
        bot.send_message(message.chat.id,
            f"🎰 <b>ПЕРЕМОГА!</b> Ставка: {bet} 🪙\nВиграш: +{bet} 🪙 (x2)\n"
            f"Баланс: {get_coins(uid)} 🪙", parse_mode="HTML")
    elif result < 0.5:     # 5% — ДЖЕКПОТ x5
        win = bet * 4
        add_coins(uid, win)
        bot.send_message(message.chat.id,
            f"💥 <b>ДЖЕКПОТ!!!</b> Ставка: {bet} 🪙\nВиграш: +{win} 🪙 (x5)\n"
            f"Баланс: {get_coins(uid)} 🪙", parse_mode="HTML")
    else:                  # 50% — програш
        add_coins(uid, -bet)
        bot.send_message(message.chat.id,
            f"😢 <b>Програв!</b> Ставка: {bet} 🪙\nМінус {bet} 🪙\n"
            f"Баланс: {get_coins(uid)} 🪙", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.магазин')
def show_shop(message):
    bot.send_message(message.chat.id,
        "🏪 <b>Магазин Драго:</b>\n\n"
        "🎭 <b>VIP статус</b> — 500 🪙 (/buy vip)\n"
        "🔇 <b>Мут юзера на 1 год</b> — 200 🪙 (/buy mute @юзер)\n"
        "🎁 <b>Секретний мем</b> — 50 🪙 (/buy meme)\n\n"
        "Монети заробляєш за кожне повідомлення!",
        parse_mode="HTML")

@bot.message_handler(commands=['buy'])
def buy_item(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Що купляємо? /buy vip | /buy meme"); return
    item  = parts[1].lower()
    uid   = message.from_user.id
    coins = get_coins(uid)

    if item == 'vip':
        if coins < 500:
            bot.reply_to(message, f"Не вистачає монет! Треба 500, у тебе {coins} 🪙"); return
        add_coins(uid, -500)
        bot.reply_to(message, "✅ Ти купив VIP статус! Тепер ти крутіший від інших 😎")

    elif item == 'meme':
        if coins < 50:
            bot.reply_to(message, f"Треба 50 🪙, у тебе {coins}"); return
        add_coins(uid, -50)
        try:
            resp = model.generate_content("Придумай смішний унікальний мем-текст про Telegram чат. Коротко, одне речення.")
            bot.reply_to(message, f"🎁 Секретний мем:\n\n{resp.text}")
        except Exception:
            bot.reply_to(message, "🎁 Мем: 'Коли купив VIP але він нічого не дає'")

    elif item == 'mute' and len(parts) > 2:
        if coins < 200:
            bot.reply_to(message, f"Треба 200 🪙, у тебе {coins}"); return
        if not message.reply_to_message:
            bot.reply_to(message, "Зроби реплай на юзера якого хочеш замутити!"); return
        target = message.reply_to_message.from_user
        try:
            add_coins(uid, -200)
            until = int(time.time()) + 3600
            bot.restrict_chat_member(message.chat.id, target.id,
                until_date=until,
                permissions=types.ChatPermissions(can_send_messages=False))
            bot.send_message(message.chat.id,
                f"🔇 <b>{message.from_user.first_name}</b> купив мут для <b>{target.first_name}</b> на 1 годину! 💰",
                parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"Не зміг замутити: {e}")
    else:
        bot.reply_to(message, "Невідомий товар. Дивись /магазин")

# ===================================================================
# 🎰 ІГРИ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.рулетка')
def roulette(message):
    outcomes = [
        ("💀 Мут на 1 хвилину!", "mute", 60),
        ("🎁 +20 монет!", "coins", 20),
        ("💸 -10 монет!", "coins", -10),
        ("🌟 +50 монет — ПОЩАСТИЛО!", "coins", 50),
        ("🤡 Нічого не сталось. Просто лох.", "none", 0),
        ("👑 +100 монет — ДЖЕКПОТ!", "coins", 100),
        ("🔇 Мут на 30 секунд!", "mute", 30),
        ("🎉 +5 монет. Ну, хоч щось.", "coins", 5),
    ]
    outcome = random.choice(outcomes)
    uid  = message.from_user.id
    name = message.from_user.first_name
    bot.send_message(message.chat.id,
        f"🎰 <b>{name}</b> крутить рулетку...\n\nРезультат: {outcome[0]}", parse_mode="HTML")
    if outcome[1] == "coins":
        add_coins(uid, outcome[2])
    elif outcome[1] == "mute":
        try:
            until = int(time.time()) + outcome[2]
            bot.restrict_chat_member(message.chat.id, uid,
                until_date=until,
                permissions=types.ChatPermissions(can_send_messages=False))
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.монетка')
def coin_flip(message):
    result = random.choice(["🦅 Орел!", "🔵 Решка!"])
    bot.reply_to(message, f"Підкидаю монетку...\n\n<b>{result}</b>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and re.match(r'^\.кубик(\s+\d+)?$', m.text.strip().lower()))
def roll_dice(message):
    parts = message.text.strip().split()
    sides = int(parts[1]) if len(parts) > 1 else 6
    sides = max(2, min(sides, 1000))
    result = random.randint(1, sides)
    bot.reply_to(message, f"🎲 Кидаю кубик d{sides}...\n\nВипало: <b>{result}</b>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.правда_чи_діло')
def truth_or_dare(message):
    truths = [
        "Яка найдурніша річ, яку ти коли-небудь робив?",
        "Кого з цього чату ти вважаєш найрозумнішим?",
        "Який твій найбільший страх?",
        "Назви своє найбільше досягнення в житті.",
        "Що найгірше ти коли-небудь казав про когось за спиною?",
    ]
    dares = [
        "Напиши комплімент кожному учаснику чату!",
        "Відправ голосове з піснею (хоча б 10 секунд).",
        "Напиши щось про себе, чого ніхто не знає.",
        "Скажи щось приємне адміну чату.",
        "Зміни своє ім'я в чаті на 'Бот Драго' на 10 хвилин.",
    ]
    if random.random() > 0.5:
        bot.send_message(message.chat.id,
            f"🤔 <b>ПРАВДА:</b>\n\n{random.choice(truths)}", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id,
            f"😈 <b>ДІЛО:</b>\n\n{random.choice(dares)}", parse_mode="HTML")

@bot.message_handler(commands=['вікторина'])
def start_trivia(message):
    try:
        prompt = ("Придумай одне питання для вікторини українською мовою та правильну відповідь. "
                  "Формат відповіді ТІЛЬКИ так:\n"
                  "ПИТАННЯ: [текст питання]\n"
                  "ВІДПОВІДЬ: [правильна відповідь]")
        resp = model.generate_content(prompt)
        lines = resp.text.strip().split('\n')
        question = answer = None
        for line in lines:
            if line.startswith('ПИТАННЯ:'):
                question = line.replace('ПИТАННЯ:', '').strip()
            elif line.startswith('ВІДПОВІДЬ:'):
                answer = line.replace('ВІДПОВІДЬ:', '').strip().lower()
        if not question or not answer:
            raise Exception("Не вдалося розпарсити")
        with db_lock:
            cursor.execute(
                "INSERT OR REPLACE INTO trivia (chat_id, question, answer, active) VALUES (?, ?, ?, 1)",
                (message.chat.id, question, answer))
            conn.commit()
        bot.send_message(message.chat.id,
            f"🧠 <b>ВІКТОРИНА!</b>\n\n{question}\n\n"
            f"Хто відповість правильно — отримає 50 🪙!\n"
            f"(скасувати: /стоп_вікторина)",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Trivia: {e}")
        bot.reply_to(message, "Не зміг придумати питання. Спробуй ще раз.")

@bot.message_handler(commands=['стоп_вікторина'])
def stop_trivia(message):
    with db_lock:
        cursor.execute("UPDATE trivia SET active=0 WHERE chat_id=?", (message.chat.id,))
        conn.commit()
    bot.reply_to(message, "Вікторину скасовано.")

def check_trivia_answer(message):
    """Перевіряємо відповідь на вікторину"""
    with db_lock:
        cursor.execute("SELECT question, answer FROM trivia WHERE chat_id=? AND active=1",
                       (message.chat.id,))
        row = cursor.fetchone()
    if not row:
        return False
    question, answer = row
    user_answer = message.text.lower().strip()
    if answer in user_answer or user_answer in answer:
        with db_lock:
            cursor.execute("UPDATE trivia SET active=0 WHERE chat_id=?", (message.chat.id,))
            conn.commit()
        add_coins(message.from_user.id, 50)
        bot.send_message(message.chat.id,
            f"✅ <b>{message.from_user.first_name}</b> відповів правильно!\n"
            f"Відповідь: <b>{answer}</b>\nНагорода: +50 🪙",
            parse_mode="HTML")
        return True
    return False

# ===================================================================
# ⏰ НАГАДУВАННЯ
# ===================================================================
@bot.message_handler(commands=['нагадай'])
def set_reminder(message):
    # Формат: /нагадай 10хв Зустріч або /нагадай 2год Завдання
    parts = message.text.split(' ', 2)
    if len(parts) < 3:
        bot.reply_to(message, "Формат: /нагадай [час] [текст]\nПриклад: /нагадай 30хв Зустріч з другом"); return
    time_str = parts[1].lower()
    text_r   = parts[2]
    seconds  = 0
    if 'хв' in time_str or 'min' in time_str:
        num = re.sub(r'[^\d]', '', time_str)
        seconds = int(num) * 60 if num else 0
    elif 'год' in time_str or 'h' in time_str:
        num = re.sub(r'[^\d]', '', time_str)
        seconds = int(num) * 3600 if num else 0
    elif 'с' in time_str or 'sec' in time_str:
        num = re.sub(r'[^\d]', '', time_str)
        seconds = int(num) if num else 0
    if seconds < 1:
        bot.reply_to(message, "Не зрозумів час. Приклад: 30хв, 2год, 60с"); return
    remind_at = int(time.time()) + seconds
    with db_lock:
        cursor.execute(
            "INSERT INTO reminders (user_id, chat_id, remind_at, text) VALUES (?, ?, ?, ?)",
            (message.from_user.id, message.chat.id, remind_at, text_r))
        conn.commit()
    human_time = str(timedelta(seconds=seconds))
    bot.reply_to(message, f"✅ Нагадаю через <b>{human_time}</b>:\n<i>{text_r}</i>", parse_mode="HTML")

def reminder_worker():
    """Фоновий потік для перевірки нагадувань"""
    while True:
        now = int(time.time())
        with db_lock:
            cursor.execute(
                "SELECT id, user_id, chat_id, text FROM reminders WHERE remind_at <= ? AND done=0",
                (now,))
            rows = cursor.fetchall()
        for row in rows:
            rid, uid, cid, text_r = row
            try:
                bot.send_message(cid,
                    f"⏰ <b>Нагадування!</b>\n\n<a href='tg://user?id={uid}'>Привіт!</a> Ти просив нагадати:\n<i>{text_r}</i>",
                    parse_mode="HTML")
            except Exception as e:
                logger.error(f"Reminder send: {e}")
            with db_lock:
                cursor.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
                conn.commit()
        time.sleep(10)

# ===================================================================
# 🌤️ ПОГОДА
# ===================================================================
@bot.message_handler(commands=['погода'])
def get_weather(message):
    city = message.text.replace('/погода', '').strip()
    if not city:
        bot.reply_to(message, "Напиши місто! /погода Київ"); return
    try:
        url  = (f"http://api.openweathermap.org/data/2.5/weather"
                f"?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ua")
        data = requests.get(url, timeout=10).json()
        if data.get('cod') != 200:
            bot.reply_to(message, f"Не знайшов '{city}'."); return
        temp    = data['main']['temp']
        feels   = data['main']['feels_like']
        desc    = data['weather'][0]['description']
        humidity= data['main']['humidity']
        wind    = data['wind']['speed']
        bot.send_message(message.chat.id,
            f"🌤️ <b>Погода в {city}</b>\n\n"
            f"🌡 {temp:.1f}°C (відчувається як {feels:.1f}°C)\n"
            f"☁️ {desc}\n💧 Вологість: {humidity}%\n💨 Вітер: {wind} м/с",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Weather: {e}")
        bot.reply_to(message, "Помилка погоди. Перевір API ключ.")

# ===================================================================
# 🌍 ПЕРЕКЛАД
# ===================================================================
@bot.message_handler(commands=['translate'])
def translate_text(message):
    parts = message.text.split(' ', 2)
    if len(parts) < 3 and not message.reply_to_message:
        bot.reply_to(message, "Формат: /translate [мова] [текст] або реплай"); return
    if message.reply_to_message and len(parts) < 3:
        text_t = message.reply_to_message.text or ""
        lang   = parts[1] if len(parts) > 1 else 'англійська'
    else:
        lang   = parts[1]
        text_t = parts[2]
    try:
        resp = model.generate_content(
            f"Переклади на {lang}. ТІЛЬКИ переклад, без пояснень: {text_t}")
        bot.reply_to(message, f"🌍 <b>({lang}):</b>\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Помилка перекладу.")

# ===================================================================
# 📝 СТИСНЕННЯ ТЕКСТУ
# ===================================================================
@bot.message_handler(commands=['summarize'])
def summarize_text(message):
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення!"); return
    text = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not text:
        bot.reply_to(message, "Немає тексту."); return
    try:
        resp = model.generate_content(f"Стисни до 2-3 речень українською: {text}")
        bot.reply_to(message, f"📝 <b>Коротко:</b>\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Помилка стиснення.")

# ===================================================================
# 💡 ФАКТ / АНЕКДОТ / ГОРОСКОП / ДУЕЛЬ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.факт')
def random_fact(message):
    try:
        resp = model.generate_content("Один цікавий несподіваний факт. 2-3 речення, стиль Драго.")
        bot.send_message(message.chat.id, f"💡 {resp.text}")
    except Exception:
        bot.reply_to(message, "Мозок завис.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['.анекдот', '.жарт'])
def tell_joke(message):
    try:
        resp = model.generate_content("Короткий смішний анекдот українською. Тільки анекдот.")
        bot.send_message(message.chat.id, f"🃏 {resp.text}")
    except Exception:
        bot.reply_to(message, "Жарти скінчились 😅")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.астрологія'))
def horoscope(message):
    sign = message.text.lower().split()[1] if len(message.text.split()) > 1 else ""
    if sign not in ZODIAC_SIGNS:
        bot.reply_to(message, f"Знаки: {', '.join(ZODIAC_SIGNS.keys())}"); return
    try:
        resp = model.generate_content(
            f"Гороскоп для {sign} на сьогодні. Стиль Драго — дотепно, 3-4 речення.")
        bot.send_message(message.chat.id,
            f"♈ <b>Гороскоп для {sign.capitalize()}:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Зірки мовчать.")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.дуель'))
def duel(message):
    challenger = message.from_user.first_name
    opponent   = (message.reply_to_message.from_user.first_name
                  if message.reply_to_message else
                  message.text.split()[1].replace('@','') if len(message.text.split()) > 1 else None)
    if not opponent:
        bot.reply_to(message, "Вкажи суперника! .дуель @нік або реплай"); return
    winner = random.choice([challenger, opponent])
    loser  = opponent if winner == challenger else challenger
    phrases = [
        f"⚔️ <b>{winner}</b> переміг! <b>{loser}</b> навіть не встиг дістати зброю 😂",
        f"🔥 <b>{winner}</b> знищив суперника одним поглядом! <b>{loser}</b> в нокауті 💀",
        f"🎯 <b>{winner}</b> — чемпіон! <b>{loser}</b> тікав, але не допомогло 🏃",
    ]
    bot.send_message(message.chat.id, random.choice(phrases), parse_mode="HTML")

# ===================================================================
# 🔗 ІНТЕГРАЦІЇ: WIKIPEDIA / ВАЛЮТА / КРИПТО
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.вікі'))
def wikipedia_search(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "Напиши запит! .вікі Місяць"); return
    try:
        url    = f"https://uk.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(query)}"
        data   = requests.get(url, timeout=10).json()
        if 'extract' not in data:
            bot.reply_to(message, f"Нічого не знайшов про '{query}'."); return
        extract = data['extract'][:800] + ('...' if len(data['extract']) > 800 else '')
        page_url= data.get('content_urls', {}).get('desktop', {}).get('page', '')
        bot.send_message(message.chat.id,
            f"📖 <b>{data.get('title', query)}</b>\n\n{extract}\n\n<a href='{page_url}'>Читати повністю</a>",
            parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Wiki: {e}")
        bot.reply_to(message, "Помилка Wikipedia.")

@bot.message_handler(func=lambda m: m.text and re.match(r'^\.курс\s+\w+$', m.text.strip().lower()))
def currency_rate(message):
    currency = message.text.split()[1].upper()
    try:
        url  = f"https://api.exchangerate-api.com/v4/latest/UAH"
        data = requests.get(url, timeout=10).json()
        if currency not in data.get('rates', {}):
            bot.reply_to(message, f"Не знайшов валюту '{currency}'."); return
        rate = data['rates'][currency]
        uah_per = 1 / rate
        bot.send_message(message.chat.id,
            f"💱 <b>Курс {currency}/UAH</b>\n\n"
            f"1 {currency} = <b>{uah_per:.2f} грн</b>\n"
            f"1 грн = {rate:.6f} {currency}",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Currency: {e}")
        bot.reply_to(message, "Не вдалося отримати курс.")

@bot.message_handler(func=lambda m: m.text and re.match(r'^\.крипто\s+\w+$', m.text.strip().lower()))
def crypto_rate(message):
    coin = message.text.split()[1].upper()
    try:
        ids_map = {
            'BTC': 'bitcoin', 'ETH': 'ethereum', 'BNB': 'binancecoin',
            'SOL': 'solana',  'XRP': 'ripple',   'ADA': 'cardano',
            'DOT': 'polkadot','DOGE':'dogecoin',  'MATIC':'matic-network',
            'TRX': 'tron',    'TON': 'the-open-network'
        }
        coin_id = ids_map.get(coin, coin.lower())
        url  = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd,uah"
        data = requests.get(url, timeout=10).json()
        if coin_id not in data:
            bot.reply_to(message, f"Не знайшов '{coin}'. Спробуй BTC, ETH, SOL, TON..."); return
        usd = data[coin_id].get('usd', 'N/A')
        uah = data[coin_id].get('uah', 'N/A')
        bot.send_message(message.chat.id,
            f"₿ <b>{coin} зараз:</b>\n\n"
            f"💵 ${usd:,.2f}\n"
            f"💴 {uah:,.0f} грн",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Crypto: {e}")
        bot.reply_to(message, "Не вдалося отримати ціну.")

# ===================================================================
# 🧠 ШІ-АНАЛІЗ
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('.аналіз'))
def analyze_user(message):
    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
    else:
        target = message.from_user
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        with db_lock:
            cursor.execute(
                "SELECT ds.count, s.gender, s.warns, s.coins FROM daily_stats ds "
                "JOIN stats s ON s.user_id = ds.user_id "
                "WHERE ds.user_id=? AND ds.date=?", (target.id, today))
            row = cursor.fetchone()
        count  = row[0] if row else 0
        gender = row[1] if row else 'невідомо'
        warns  = row[2] if row else 0
        coins  = row[3] if row else 0
        prompt = (
            f"Проаналізуй юзера за такими даними і дай смішну характеристику в стилі Драго:\n"
            f"Ім'я: {target.first_name}\nСтать: {gender}\n"
            f"Повідомлень сьогодні: {count}\nВарни: {warns}\nМонети: {coins}\n"
            f"Дай влучну, дотепну психологічну характеристику цього юзера. 3-4 речення."
        )
        resp = model.generate_content(prompt)
        bot.send_message(message.chat.id,
            f"🧠 <b>Аналіз {target.first_name}:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Analyze: {e}")
        bot.reply_to(message, "Не зміг проаналізувати.")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.настрій')
def analyze_mood(message):
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        with db_lock:
            cursor.execute("""
                SELECT s.name, ds.count FROM daily_stats ds
                JOIN stats s ON s.user_id = ds.user_id
                WHERE ds.chat_id=? AND ds.date=?
                ORDER BY ds.count DESC LIMIT 5
            """, (message.chat.id, today))
            rows = cursor.fetchall()
        if not rows:
            bot.reply_to(message, "Недостатньо даних для аналізу."); return
        names_str = ", ".join([r[0] for r in rows])
        prompt = (
            f"Оціни загальний настрій чату де топ активних юзерів: {names_str}. "
            f"Визнач який зараз настрій в чаті (весело, серйозно, скандально, тихо і т.д.). "
            f"Дай коротку характеристику. Стиль Драго."
        )
        resp = model.generate_content(prompt)
        bot.send_message(message.chat.id, f"🎭 <b>Настрій чату:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Mood: {e}")

@bot.message_handler(commands=['резюме'])
def chat_summary(message):
    try:
        prompt = (
            "Уяви що ти аналізуєш типовий Telegram чат і маєш зробити смішне резюме "
            "того що там зазвичай відбувається. Напиши в стилі Драго — дотепно і коротко. 3-4 речення."
        )
        resp = model.generate_content(prompt)
        bot.send_message(message.chat.id, f"📋 <b>Резюме чату:</b>\n\n{resp.text}", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Не зміг зробити резюме.")

# ===================================================================
# ⚠️ МОДЕРАЦІЯ
# ===================================================================
@bot.message_handler(commands=['warn'])
def warn_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай на повідомлення юзера."); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns = warns + 1 WHERE user_id=?", (target.id,))
        cursor.execute("SELECT warns FROM stats WHERE user_id=?", (target.id,))
        row = cursor.fetchone(); conn.commit()
    warns = row[0] if row else 1
    if warns >= 3:
        try:
            bot.restrict_chat_member(message.chat.id, target.id,
                until_date=int(time.time()) + 3600,
                permissions=types.ChatPermissions(can_send_messages=False))
            bot.send_message(message.chat.id,
                f"⛔ <b>{target.first_name}</b> — 3 варни → мут на 1 год!", parse_mode="HTML")
        except Exception as e:
            bot.send_message(message.chat.id, f"Не зміг замутити: {e}")
    else:
        bot.send_message(message.chat.id,
            f"⚠️ <b>{target.first_name}</b> — варн {warns}/3. Ще {3-warns} — мут!",
            parse_mode="HTML")

@bot.message_handler(commands=['unwarn'])
def unwarn_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    with db_lock:
        cursor.execute("UPDATE stats SET warns=MAX(0, warns-1) WHERE user_id=?", (target.id,))
        conn.commit()
    bot.send_message(message.chat.id, f"✅ Знято варн з <b>{target.first_name}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['mute'])
def mute_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target  = message.reply_to_message.from_user
    parts   = message.text.split()
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            until_date=int(time.time()) + minutes * 60,
            permissions=types.ChatPermissions(can_send_messages=False))
        bot.send_message(message.chat.id,
            f"🔇 <b>{target.first_name}</b> замучений на {minutes} хв.", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['unmute'])
def unmute_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    try:
        bot.restrict_chat_member(message.chat.id, target.id,
            permissions=types.ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True))
        bot.send_message(message.chat.id,
            f"🔊 <b>{target.first_name}</b> розмучений!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(message.chat.id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Реплай!"); return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        bot.send_message(message.chat.id,
            f"🚫 <b>{target.first_name}</b> забанений!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

@bot.message_handler(commands=['kick'])
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
            f"👟 <b>{target.first_name}</b> вигнаний!", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Помилка: {e}")

# ===================================================================
# ❓ ДОПОМОГА
# ===================================================================
@bot.message_handler(commands=['help', 'start'])
def show_help(message):
    bot.send_message(message.chat.id, """
🤖 <b>ДРАГО — ПОВНИЙ СПИСОК КОМАНД</b>

<b>💬 Спілкування:</b>
• Напиши "Драго" або реплай — відповідь ШІ
• Реплай з фото — аналіз зображення

<b>🎮 Ігри:</b>
• <code>.рулетка</code> — удача чи мут?
• <code>.монетка</code> — орел чи решка
• <code>.кубик [N]</code> — кинути кубик
• <code>.дуель @юзер</code> — бій до смерті
• <code>.правда_чи_діло</code> — правда або діло
• <code>/вікторина</code> — запитання (+50🪙 за відповідь)
• <code>/game</code> / <code>/stop</code> — гра в слова

<b>💰 Економіка:</b>
• <code>.баланс</code> — скільки монет
• <code>.переказ [сума]</code> (реплай) — відправити монети
• <code>.казино [ставка]</code> — поставити монети
• <code>.магазин</code> — купити щось
• <code>/buy [товар]</code> — купівля

<b>📊 Статистика:</b>
• <code>.топ</code> — топ дня
• <code>.профіль</code> — твій профіль
• <code>.аналіз</code> (реплай) — аналіз юзера
• <code>.настрій</code> — настрій чату
• <code>/резюме</code> — резюме чату
• <code>/stats</code> — статистика (адміни)

<b>🛠️ Утиліти:</b>
• <code>.факт</code> — цікавий факт
• <code>.анекдот</code> — жарт
• <code>.астрологія [знак]</code> — гороскоп
• <code>.вікі [запит]</code> — Wikipedia
• <code>.курс [USD/EUR]</code> — курс валюти
• <code>.крипто [BTC/ETH]</code> — ціна крипти
• <code>/погода [місто]</code> — погода
• <code>/translate [мова] [текст]</code> — переклад
• <code>/summarize</code> (реплай) — стиснути текст
• <code>/нагадай [час] [текст]</code> — нагадування
• <code>/generate [опис]</code> — генерація фото
• <code>.мем</code> — рандомний мем

<b>⚠️ Модерація (адміни):</b>
• <code>/warn</code> <code>/unwarn</code> — варни
• <code>/mute [хв]</code> <code>/unmute</code> — мут
• <code>/ban</code> <code>/kick</code> — бан/кік
""", parse_mode="HTML")

# ===================================================================
# 🧠 АНАЛІЗ СТАТІ
# ===================================================================
def analyze_gender(text):
    try:
        resp = model.generate_content(
            f"Визнач стать (Хлопець, Дівчина або Незрозуміло). Тільки одне слово: {text}")
        return resp.text.strip()
    except Exception:
        return "Незрозуміло"

# ===================================================================
# 🎛️ ЦЕНТРАЛЬНИЙ ДИСПЕТЧЕР
# ===================================================================
@bot.message_handler(content_types=['text'])
def main_handler(message):
    text      = message.text
    chat_id   = message.chat.id
    chat_type = message.chat.type
    user_id   = message.from_user.id
    name      = message.from_user.first_name

    update_message_count(user_id, name, chat_id)

    # Антиспам
    if chat_type in ['group', 'supergroup'] and not is_admin(chat_id, user_id):
        if check_flood(user_id, chat_id):
            try:
                bot.restrict_chat_member(chat_id, user_id,
                    until_date=int(time.time()) + MUTE_DURATION,
                    permissions=types.ChatPermissions(can_send_messages=False))
                bot.send_message(chat_id,
                    f"⚡ <b>{name}</b>, флуд виявлено — мут на 5 хвилин!", parse_mode="HTML")
            except Exception as e:
                logger.error(f"Antispam: {e}")
            return

    # Антимат
    if has_bad_words(text) and chat_type in ['group','supergroup']:
        if not is_admin(chat_id, user_id):
            bot.reply_to(message, f"Ей, <b>{name}</b>, стеж за лексикою! Ще раз — варн.",
                         parse_mode="HTML")

    # Вікторина
    if check_trivia_answer(message):
        return

    # Гра в слова
    if chat_id in game_state:
        handle_word_game(message)
        return

    # Аналіз статі
    with db_lock:
        cursor.execute("SELECT gender FROM stats WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    if row and row[0] == 'не вказано':
        g = analyze_gender(text)
        if g in ['Хлопець', 'Дівчина']:
            with db_lock:
                cursor.execute("UPDATE stats SET gender=? WHERE user_id=?", (g, user_id))
                conn.commit()
            bot.send_message(chat_id, f"Драго вирішив що ти — {g.lower()}. Вгадав? 😎")

    # Діалог з Драго
    is_mentioned = False
    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'джарвіс']
        text_lower    = text.lower()
        word_found    = any(w in text_lower for w in trigger_words)
        if (word_found or
            f"@{bot.get_me().username}" in text or
            (message.reply_to_message and
             message.reply_to_message.from_user.id == bot.get_me().id)):
            is_mentioned = True
            for w in trigger_words:
                if text_lower.startswith(w):
                    text = text[len(w):].strip(); break
    else:
        is_mentioned = True

    if not is_mentioned:
        return

    status_msg = None
    try:
        bot.send_chat_action(chat_id, 'typing')
        status_msg = bot.reply_to(message, "Йде відправка даних в СБУ... 👮‍♂️")
        gemini_chat = get_gemini_chat(chat_id)
        response    = gemini_chat.send_message(text)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text=response.text, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text=response.text)
    except genai.types.generation_types.BlockedPromptException:
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                  text="Цей запит заблоковано Google. 🤐")
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
# 🚀 ЗАПУСК
# ===================================================================
threading.Thread(target=run_dummy_server,  daemon=True).start()
threading.Thread(target=reminder_worker,   daemon=True).start()

if __name__ == "__main__":
    logger.info("DRAGO BOT ЗАПУЩЕНИЙ!")
    print("=" * 45)
    print("   DRAGO BOT — МАКСИМАЛЬНА ВЕРСІЯ!   ")
    print("=" * 45)
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
