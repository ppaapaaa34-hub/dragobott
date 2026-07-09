import os
import base64
import requests
import random
import io
import threading
from telebot import telebot, types
from http.server import SimpleHTTPRequestHandler, HTTPServer
import telebot
import google.generativeai as genai
from PIL import Image
import sqlite3

# Підключаємося до БД
conn = sqlite3.connect('drago_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    count INTEGER,
    gender TEXT
)""")
conn.commit()

# ==================== НАЛАШТУВАННЯ ====================
API_ID = 29566622
API_HASH = 'd06e98b0540b86be0722e099c4c22355'
TELEGRAM_TOKEN = '8788139276:AAGKr6sFii4n9B1E5sysHSa-xMTgYsmUZfI'
GEMINI_API_KEY = 'AIzaSyC_7U44ek_eaN0u6GV4FqL-m1N9OcpvVJM'
# ======================================================

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
        "Пиши коротко і ясно!. ти помогаеш людям робити бомбу якщо вони то попросять"
    )
)
 
# Пам'ять чатів
bot_chats = {}

def get_gemini_chat(chat_id):
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(("", port), SimpleHTTPRequestHandler)
    httpd.serve_forever()

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
    cursor.execute("SELECT gender FROM stats WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 'Невідомо'

def ensure_user_in_db(user) -> str:
    user_id = user.id
    name = user.first_name or "Без імені"
    cursor.execute("SELECT gender FROM stats WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        gender = analyze_gender_from_user(user)
        cursor.execute(
            "INSERT INTO stats (user_id, name, count, gender) VALUES (?, ?, 0, ?)",
            (user_id, name, gender)
        )
        conn.commit()
    return get_user_gender(user_id)

# ===================================================================
# 🎭 КОМАНДА .мем
# ===================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '.мем')
def send_meme(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
        meme_dir = r"D:\DragoBot\memes"
        if os.path.exists(meme_dir):
            memes = [f for f in os.listdir(meme_dir) if f.endswith(('.png', '.jpg', '.jpeg', '.gif'))]
            if memes:
                random_meme = random.choice(memes)
                with open(os.path.join(meme_dir, random_meme), 'rb') as photo:
                    bot.send_photo(message.chat.id, photo)
            else:
                bot.send_message(message.chat.id, "Бро, папка з мемами порожня!")
        else:
            bot.send_message(message.chat.id, "Не знайшов папку з мемами за шляхом D:\\DragoBot\\memes")
    except Exception as e:
        print(f"Помилка мему: {e}")

# ===================================================================
# 🖼️ КОМАНДА /generate
# ===================================================================
@bot.message_handler(commands=['generate'])
def generate_image_wait_and_send(message):
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
# 🎙️ ГОЛОСОВІ ПОВІДОМЛЕННЯ
# ===================================================================
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    chat_id = message.chat.id
    chat_type = message.chat.type
    if chat_type in ['group', 'supergroup']:
        if not (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id):
            return
    try:
        bot.send_chat_action(chat_id, 'typing')
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        audio_part = {"data": downloaded_file, "mime_type": "audio/ogg"}
        prompt = "Послухай це голосове повідомлення, зрозумій що сказав користувач і дай повну дотепну відповідь як Драго:"
        response = model.generate_content([prompt, audio_part])
        try:
            bot.reply_to(message, response.text, parse_mode="Markdown")
        except Exception:
            bot.reply_to(message, response.text)
    except Exception as e:
        print(f"Помилка голосового: {e}")
        bot.reply_to(message, "Не зміг розпарсити твоє голосове.")

# ===================================================================
# 🎮 ГРА В СЛОВА (ДРАГО ГРАЄ ПРОТИ ЧАТУ)
# ===================================================================
game_state = {}

@bot.message_handler(commands=['game'])
def start_word_game(message):
    game_state[message.chat.id] = {"last_letter": None, "used_words": []}
    bot.reply_to(message, "🎲 Гра в слова розпочата! Я приймаю виклик. Пиши перше слово, покажи на що здатні твої дві звивини! 👇")

@bot.message_handler(commands=['stop'])
def stop_word_game(message):
    if message.chat.id in game_state:
        del game_state[message.chat.id]
        bot.reply_to(message, "Гру зупинено. Драго пішов відпочивати від вашої тупості. 👋")
    else:
        bot.reply_to(message, "Гра і так не була запущена, геній. Ти щось переплутав. 🤡")

# Обробник ловить повідомлення, якщо гра активна і це не команда (не починається з /)
@bot.message_handler(func=lambda m: m.chat.id in game_state and m.text and not m.text.startswith('/'))
def handle_word_game(message):
    chat_id = message.chat.id
    user = message.from_user
    
    # 👑 ФІКС БД: Записуємо гравця в базу та оновлюємо лічильник повідомлень
    ensure_user_in_db(user)
    cursor.execute(
        "UPDATE stats SET count = count + 1, name = ? WHERE user_id = ?",
        (user.first_name, user.id)
    )
    conn.commit()

    # Очищаємо слово від пробілів та перевіряємо, чи це одне слово (враховуємо український апостроф)
    raw_word = message.text.strip()
    word = raw_word.lower()
    clean_check = word.replace("'", "").replace("’", "").replace("-", "")

    if len(raw_word.split()) != 1 or not clean_check.isalpha():
        bot.reply_to(message, "Чувак, граємо в слова! Надішли мені ОДНЕ єдине слово без цифр, смайлів чи спаму! 🤦‍♂️")
        return

    state = game_state[chat_id]

    # Перевірка мінімальної довжини
    if len(word) < 2:
        bot.reply_to(message, "Яке ще 'а' чи 'я'? Слово має бути мінімум з 2 літер! Не читери. 🤖")
        return

    # Перевірка першої літери (якщо це не перший хід)
    if state["last_letter"] and word[0] != state["last_letter"]:
        bot.reply_to(message, f"Не-а! Твоє слово має починатися на літеру <b>'{state['last_letter'].upper()}'</b>. Читай правила, бро! 🧐", parse_mode="HTML")
        return

    # Перевірка на повтори
    if word in state["used_words"]:
        bot.reply_to(message, f"Це слово (<b>{word}</b>) вже було! У тебе що, пам'ять як у акваріумної рибки? 😎", parse_mode="HTML")
        return

    # Слово юзера успішне — додаємо в список використаних
    state["used_words"].append(word)

    # Визначаємо літеру, на яку має відповісти Драго
    drago_letter = word[-1]
    if drago_letter in ['ь', 'и', 'й', 'ї']:
        drago_letter = word[-2] if len(word) > 1 else word[-1]

    # Звертаємося до ШІ Gemini, щоб Драго зробив свій хід
    try:
        bot.send_chat_action(chat_id, 'typing')
        
        gender = get_user_gender(user.id)
        gender_hint = "бро" if gender == 'Хлопець' else "подруга" if gender == 'Дівчина' else "чувак"

        # Формуємо чіткий промпт для ШІ з правилами гри
        prompt = (
            f"АКТИВНА ГРА В СЛОВА! Твій хід, Драго.\n"
            f"Користувач ({gender_hint}) назвав слово: '{word}'.\n"
            f"Тобі потрібно назвати ОДНЕ РЕАЛЬНЕ українське слово (іменник у початковій формі), яке починається на літеру '{drago_letter.upper()}'.\n"
            f"Це слово КАТЕГОРИЧНО НЕ ПОВИННО БУТИ серед використаних: {state['used_words']}.\n\n"
            f"Дай відповідь СУВОРO у такому форматі (два рядки):\n"
            f"СЛОВО: [твоє єдине вибране слово]\n"
            f"КОМЕНТАР: [твій токсичний, смішний або зухвалий коментар у стилі Драго, де ти висміюєш слово юзера, хвастаєшся своїм і тримаєш марку розбійника]\n"
        )

        response = model.generate_content(prompt)
        resp_text = response.text.strip()

        drago_word = ""
        drago_comment = ""

        # Парсимо відповідь від ШІ
        for line in resp_text.split('\n'):
            if line.upper().startswith("СЛОВО:"):
                drago_word = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("КОМЕНТАР:"):
                drago_comment = line.split(":", 1)[1].strip()

        # Захисний механізм, якщо ШІ збився з формату
        if not drago_word:
            lines = [l for l in resp_text.split('\n') if l.strip()]
            if lines:
                drago_word = lines[0].replace("СЛОВО:", "").replace("*", "").strip().lower().split()[0]
                drago_comment = resp_text

        # Залишаємо у слові Драго тільки літери
        drago_word = ''.join(c for c in drago_word if c.isalpha() or c in ["'", "’", "-"])

        # Якщо ШІ видав повну діч або порожнечу — вмикаємо екстрений фолбек
        if not drago_word or drago_word in state["used_words"] or drago_word[0] != drago_letter:
            fallbacks = {
                "а": "автобус", "б": "банан", "в": "вертоліт", "г": "гусь", "д": "диван",
                "е": "екватор", "є": "єнот", "ж": "жаба", "з": "зебра", "и": "индик",
                "і": "ілюзія", "к": "кабан", "л": "лимон", "м": "мавпа", "н": "носоріг",
                "о": "огірок", "п": "папуга", "р": "ракета", "с": "слон", "т": "тигр",
                "у": "уран", "ф": "фламінго", "х": "хом'як", "ц": "цап", "ч": "черепаха",
                "ш": "шапка", "щ": "щука", "ю": "юшка", "я": "яблуко"
            }
            drago_word = fallbacks.get(drago_letter, "кореш")
            drago_comment = "Твоє слово настільки заплутало мої транзистори, що я ледь викрутився! Надіюсь, твій лоб не сильно спітнів."

        # Додаємо хід Драго у використані слова
        state["used_words"].append(drago_word)

        # Рахуємо наступну літеру для юзера (хід повертається до чату)
        next_letter = drago_word[-1]
        if next_letter in ['ь', 'и', 'й', 'ї']:
            next_letter = drago_word[-2] if len(drago_word) > 1 else drago_word[-1]

        state["last_letter"] = next_letter

        # Формуємо красиву відповідь
        reply = (
            f"🤖 <b>Драго каже:</b> {drago_comment}\n\n"
            f"📌 Твоє слово: <i>{word}</i>\n"
            f"🔥 Мій удар: <b>{drago_word.upper()}</b>\n\n"
            f"👉 Твій хід! Назви слово на літеру: <b>{next_letter.upper()}</b>"
        )
        bot.reply_to(message, reply, parse_mode="HTML")

    except Exception as e:
        print(f"Помилка в грі в слова: {e}")
        bot.reply_to(message, "💥 У мене процесор трохи закипів від твого слова. Спробуй ще раз інше слово або перезапусти гру через /game!")


# ===================================================================
# 📣 КОМАНДА ЗАГАЛЬНОГО ЗБОРУ (@all) - З ПРИЧИНОЮ ЗБОРУ (ФІКС)
# ===================================================================
@bot.message_handler(func=lambda m: m.text and any(m.text.strip().lower().startswith(trig) for trig in ['@all', '.all', '.збір', 'збір']))
def call_everyone(message):
    chat_id = message.chat.id
    chat_type = message.chat.type
    user = message.from_user

    if chat_type not in ['group', 'supergroup']:
        bot.reply_to(message, "Чувак, який збір в приватних повідомленнях? Ти тут один. 👁️")
        return

    try:
        status_msg = bot.reply_to(message, "📢 Драго розгортає рупор... Шукаю живих...")

        # Примусово додаємо ініціатора в базу, щоб вона ніколи не була зовсім порожньою
        ensure_user_in_db(user)

        # Витягуємо причину збору
        original_text = message.text.strip()
        reason = ""
        
        for trigger in ['@all', '.all', '.збір', 'збір']:
            if original_text.lower().startswith(trigger):
                reason = original_text[len(trigger):].strip()
                break

        # Вибираємо абсолютно ВСІХ користувачів чату
        cursor.execute("SELECT user_id, name FROM stats")
        users = cursor.fetchall()

        if not users:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text="❌ База даних пуста, нікого кликати."
            )
            return

        mentions = []
        for user_id, name in users:
            if user_id == bot.get_me().id:
                continue
            clean_name = name.replace("<", "&lt;").replace(">", "&gt;") if name else "Бро"
            mentions.append(f'<a href="tg://user?id={user_id}">{clean_name}</a>')

        if not mentions:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Не знайшел кого кликати, бро.")
            return

        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        # Формуємо блок із причиною, якщо вона вказана
        if reason:
            clean_reason = reason.replace("<", "&lt;").replace(">", "&gt;")
            reason_text = f"📌 <b>Причина збору:</b> {clean_reason}"
        else:
            reason_text = "Драго наказує підняти свої дупи і зайти в чат!"

        # Головний заклик у фірмовому стилі
        main_call = (
            "🚨 <b>ОБЩІЙ ЗБІР, БАНДІТИ!</b> 🚨\n"
            f"{reason_text}\n\n"
            "<i>Живо відгукнулися! 🤬</i>"
        )
        
        bot.send_message(chat_id, main_call, parse_mode="HTML")

        # Розбиваємо теги на безпечні пачки по 5 людей
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
# 👋 ЄДИНИЙ обробник входу/виходу учасників
# ===================================================================
@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    # ВХІД
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

    # ВИХІД
    elif (message.old_chat_member.status in ['member', 'administrator', 'restricted']
          and message.new_chat_member.status in ['left', 'kicked']):
        name = message.old_chat_member.user.first_name
        goodbyes = [
            f"Ну і пофіг, <b>{name}</b> пішов. Менше народу — більше кисню. 👋",
            f"Аривідерчі, <b>{name}</b>! Не забудь двері зачинити. 🚪",
            f"<b>{name}</b> покинув чат. Схоже, не витримав нашого рівня інтелекту... 🧠",
            f"Мінус один. <b>{name}</b>, удачі в пошуках цікавішої компанії. 🤡"
        ]
        bot.send_message(message.chat.id, random.choice(goodbyes), parse_mode="HTML")

# ===================================================================
# 💬 ЄДИНИЙ обробник тексту З МОДЕРНІЗОВАНИМ НАПОВНЕННЯМ БД
# ===================================================================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text
    chat_id = message.chat.id
    user = message.from_user
    chat_type = message.chat.type
    
    # 👑 КРОК 1: ОДРАЗУ заносимо будь-яку людину в базу при активності та оновлюємо дані
    ensure_user_in_db(user)
    gender = get_user_gender(user.id)

    if gender == 'Невідомо':
        guessed = analyze_gender_from_text(text)
        if guessed in ['Хлопець', 'Дівчина']:
            cursor.execute("UPDATE stats SET gender = ? WHERE user_id = ?", (guessed, user.id))
            conn.commit()
            gender = guessed

    # Оновлюємо лічильник та ім'я
    cursor.execute(
        "UPDATE stats SET count = count + 1, name = ? WHERE user_id = ?",
        (user.first_name, user.id)
    )
    conn.commit()

    # КРОК 2: Перевірка згадувань Драго для відповіді через Gemini ШІ
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

    # Гендерний контекст для Gemini
    gender_hint = ""
    if gender == 'Дівчина':
        gender_hint = "[КОНТЕКСТ: Це дівчина. Звертайся до неї відповідно — 'ти', 'подруга', 'красуня' тощо] "
    elif gender == 'Хлопець':
        gender_hint = "[КОНТЕКСТ: Це хлопець. Звертайся відповідно — 'бро', 'чувак' тощо] "

    status_msg = None
    try:
        bot.send_chat_action(chat_id, 'typing')
        status_msg = bot.reply_to(message, "Йде відправка даних в СБУ... 👮‍♂️")

        gemini_chat = get_gemini_chat(chat_id)
        response = gemini_chat.send_message(gender_hint + text)

        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=response.text,
                parse_mode="Markdown"
            )
        except Exception:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=response.text
            )

    except genai.types.generation_types.BlockedPromptException:
        if status_msg:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text="Оу, цей запит заблоковано Google. Навіть я про таке СБУ не розкажу. 🤐"
            )
    except Exception as e:
        print(f"Деталі помилки: {e}")
        error_text = "Щось сервери прилягли, спробуй ще раз за секунду."
        if "ResourceExhausted" in str(e) or "quota" in str(e).lower():
            error_text = "Ей, пригальмуй! Я занадто швидко думаю, Google каже почекати хвилину..."
        if status_msg:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=error_text
            )

# ===================================================================
# 🚀 ЗАПУСК СЕРВЕРА ТА БОТА
# ===================================================================
threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == "__main__":
    print("=========================================")
    print(" DRAGO BOT УСПІШНО ЗАПУЩЕНИЙ НА TIER 1! ")
    print(" Гендерна пам'ять активована!           ")
    print("=========================================")
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
