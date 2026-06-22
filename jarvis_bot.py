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

# Підключаємося до БД (або створюємо її)
conn = sqlite3.connect('drago_bot.db', check_same_thread=False)
cursor = conn.cursor()

# Створюємо таблицю, якщо її немає
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    count INTEGER,
    gender TEXT
)
""")
conn.commit()

# ==================== ТВОЇ ОФІЦІЙНІ НАЛАШТУВАННЯ ====================
API_ID = 29566622
API_HASH = 'd06e98b0540b86be0722e099c4c22355'

TELEGRAM_TOKEN = '8788139276:AAGKr6sFii4n9B1E5sysHSa-xMTgYsmUZfI'
GEMINI_API_KEY = 'AIzaSyC_7U44ek_eaN0u6GV4FqL-m1N9OcpvVJM'
# ===================================================================

# Ініціалізація бота та ШІ
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

# Налаштування конфігурації генерації (максимум токенів для повних відповідей)
generation_config = {
    "max_output_tokens": 2048,  # коротко і ясно
    "temperature": 0.85,
}

# 🛠️ ЗНІМАЄМО ФІЛЬТРИ ЦЕНЗУРИ GOOGLE (Щоб пропускав мати)
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# Налаштування моделі Gemini 2.5 Flash із системним характером Драго
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,  # Тепер фільтри підключені!
    system_instruction=(
        "Ти — зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
        "Спілкуйся виключно українською мовою. Використовуй молодіжний сленг, міцні слівця, "
        "іноді нецензурну лексику (мати) для емоційності, якщо це доречно. "
        "Будь живим, неформальним розмовником, злегка іронізуй, підколюй юзера, але завжди допомагай. "
        "Обов'язково закінчуй свої думки, не обривай речення на пів слові!"
        "Пиши коротко і ясно!"
    )
)

# 🧠 СЛОВНИК ДЛЯ ГЛИБОКОЇ ПАМ'ЯТІ (Тільки для тексту)
bot_chats = {}

def get_gemini_chat(chat_id):
    """Отримує або створює унікальну сесію чату з пам'яттю для конкретного чату"""
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]

# Функція для запуска фейкового веб-сервера
def run_dummy_server():
    # Render автоматично передає порт у змінну оточення PORT
    port = int(os.environ.get("PORT", 8080))
    server_address = ("", port)
    httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    httpd.serve_forever()


# ===================================================================
# 🎭 1. КОМАНДА ДЛЯ ЛОКАЛЬНИХ МЕМІВ (.мем)
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
                bot.send_message(message.chat.id, "Бро, папка з мемами порожня! Закинь туди картинок.")
        else:
            bot.send_message(message.chat.id, "Не знайшов папку з мемами за шляхом D:\\DragoBot\\memes")
    except Exception as e:
        print(f"Помилка мему: {e}")


import io
import random
import requests
from PIL import Image  # Переконайся, що Pillow є в requirements.txt

# ===================================================================
# 🖼️ КОМАНДА /generate (ОЧІКУВАННЯ ГЕНЕРАЦІЇ + ВІДПРАВКА ГОТОВОГО ФОТО)
# ===================================================================
@bot.message_handler(commands=['generate'])
def generate_image_wait_and_send(message):
    # Відрізаємо команду "/generate " (10 символів)
    prompt = message.text[10:].strip()

    if not prompt:
        bot.reply_to(message, "⚠️ Напиши опис картини, бро! Наприклад: /generate cyberpunk warrior wolf")
        return

    # Попереджаємо юзера, що треба почекати, поки ШІ малює
    status_msg = bot.reply_to(message, "⏳ Драго починає малювати... Це може зайняти від 30 секунд до 2 хвилин. Будь ласка, зачекай.")

    try:
        # 1. Кодуємо текст промпту для безпечного URL
        encoded_prompt = requests.utils.quote(prompt)
        
        # 2. Беремо топову модель Flux (вона малює довше, але якість — бомба)
        image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1, 999999)}&model=flux&nologo=true"

        # 3. БОТ ЧЕКАЄ: Ставимо timeout=120 (це рівно 2 хвилини)
        # Бот буде висіти і чекати, поки сервер повністю згенерує і віддасть усі байти
        response = requests.get(image_url, timeout=120)
        
        if response.status_code == 200:
            # Перевіряємо, чи нам не підсунули текст помилки замість картинки
            if "application/json" in response.headers.get("Content-Type", "") or len(response.content) < 10000:
                raise Exception("Сервер ШІ перевантажений або повернув помилку ліміту.")

            # Оновлюємо статус у чаті, що байти отримано і ми їх обробляємо
            try:
                bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    text="⚡ Картинку згенеровано! Обробляю формат для Telegram..."
                )
            except Exception:
                pass

            # 4. ОБРОБКА ЧЕРЕЗ PILLOW (перетворюємо прогресивний JPEG у звичайний, щоб Telegram не падав)
            img = Image.open(io.BytesIO(response.content))
            img = img.convert("RGB")
            
            bio = io.BytesIO()
            bio.name = 'drago_art.jpg'
            img.save(bio, 'JPEG', progressive=False, quality=95)
            bio.seek(0)  # Скидаємо покажчик на початок файлу

            # 5. НАДСИЛАЄМО ГОТОВЕ ФОТО (воно розгорнеться відразу в чаті)
            bot.send_photo(
                chat_id=message.chat.id,
                photo=bio,
                caption=f"🔥 Твоя картинка готова, бро!\n\n📋 <b>Запит:</b> {prompt}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            
            # Видаляємо статус-повідомлення
            try:
                bot.delete_message(message.chat.id, status_msg.message_id)
            except Exception:
                pass
                
        else:
            raise Exception(f"Сервер повернув код помилки: {response.status_code}")

    except Exception as e:
        print(f"Помилка генерації: {e}")
        error_text = str(e)[:100]
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=f"❌ Не зміг отримати готову картинку.\nПомилка: <code>{error_text}</code>\n\nСпробуй повторити запит ще раз за хвилинку, бро.",
                parse_mode="HTML"
            )
        except Exception:
            bot.reply_to(message, "❌ Сервер малювання тимчасово ліг. Спробуй пізніше.")
# ===================================================================
# 🎙️ 4. СЛУХ (ОБРОБКА ГОЛОСОВИХ ПОВІДОМЛЕНЬ)
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
        
        audio_part = {
            "data": downloaded_file,
            "mime_type": "audio/ogg"
        }
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
# 💬 5. РОЗУМНІ ТЕКСТОВІ ДІАЛОГИ (Повинні бути в самому кінці!)
# ===================================================================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text
    chat_id = message.chat.id
    chat_type = message.chat.type

    is_mentioned = False
    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'драго,', 'джарвіс', 'джарвіс,']
        if text.split():
            first_word = text.split()[0].lower()
        else:
            first_word = ""
        
        if first_word in trigger_words or f"@{bot.get_me().username}" in text or (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id):
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
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text)
        
    except genai.types.generation_types.BlockedPromptException:
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="Оу, цей запит заблоковано безпекою Google. Навіть я про таке СБУ не розкажу. 🤐")
    except Exception as e:
        print(f"Деталі помилки в консолі: {e}")
        error_text = "Щось сервери прилягли, спробуй ще раз за секунду."
        if "ResourceExhausted" in str(e) or "quota" in str(e).lower():
            error_text = "Ей, пригальмуй! Я занадто швидко думаю, Google каже почекати хвилину..."
            
        if status_msg:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=error_text)
        else:
            bot.reply_to(message, error_text)


# ===================================================================
# 👋 ПРИВІТАННЯ ТА ВИБІР СТАТІ (ОНОВЛЕНИЙ І СТАБІЛЬНИЙ)
# ===================================================================
@bot.chat_member_handler()
def handle_member_updates(message: types.ChatMemberUpdated):
    global cursor, conn
    
    # 1. ОБРОБКА ВХОДУ
    if message.new_chat_member.status in ['member', 'administrator', 'restricted'] and not message.new_chat_member.user.is_bot:
        user_id = message.new_chat_member.user.id
        name = message.new_chat_member.user.first_name
        
        cursor.execute("INSERT OR IGNORE INTO stats (user_id, name, count, gender) VALUES (?, ?, 0, 'не вказано')", (user_id, name))
        conn.commit()

        # Привітання
        welcome_text = (
            f"Вітаємо в нашій групі, <b>{name}</b>! 🤍\n\n"
            "Розкажи трохи про себе, будемо раді познайомитись! "
        )
        bot.send_message(message.chat.id, welcome_text, parse_mode="HTML")
        
        # Кнопки (окреме повідомлення для стабільності)
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True, selective=True)
        markup.add('Хлопець 🧔', 'Дівчина 👩', 'Інше 👽')
        
        bot.send_message(message.chat.id, "Обери свою стать, щоб Драго знав, як до тебе звертатися:", reply_markup=markup)

    # 2. ОБРОБКА ВИХОДУ
    elif message.old_chat_member.status in ['member', 'administrator', 'restricted'] and message.new_chat_member.status in ['left', 'kicked']:
        name = message.old_chat_member.user.first_name
        
        goodbye_texts = [
            f"Ну і пофіг, <b>{name}</b> пішов. Менше народу — більше кисню. 👋",
            f"Аривідерчі, <b>{name}</b>! Не забудь двері зачинити. 🚪",
            f"<b>{name}</b> покинув чат. Схоже, він не витримав нашого рівня інтелекту... 🧠",
            f"Мінус один. <b>{name}</b>, удачі в пошуках цікавішої компанії. 🤡"
        ]
        
        bot.send_message(message.chat.id, random.choice(goodbye_texts), parse_mode="HTML")

   
# ===================================================================
# 💾 ОБРОБКА ВИБОРУ СТАТІ (ЩОБ БОТ ЗАПАМ'ЯТАВ ТЕБЕ)
# ===================================================================
@bot.message_handler(func=lambda m: m.text in ['Хлопець 🧔', 'Дівчина 👩', 'Інше 👽'])
def save_gender_auto(message):
    global cursor, conn
    
    # Витягуємо тільки слово (без емодзі)
    gender = message.text.split()[0]
    
    # Оновлюємо базу даних
    cursor.execute("UPDATE stats SET gender = ? WHERE user_id = ?", (gender, message.from_user.id))
    conn.commit()
    
    # Видаляємо кнопки після вибору
    remove_markup = types.ReplyKeyboardRemove(selective=True)
    
    bot.reply_to(message, f"Записав! Тепер ти — {gender.lower()}. Драго все знає. 😎", 
                 reply_markup=remove_markup)
    
# ===================================================================
# 🚀 ЗАПУСК СЕРВЕРА ТА БОТА
# ===================================================================
# Запускаємо сервер в окремому потоці, щоб він не заважав боту
threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == "__main__":
    print("=========================================")
    print(" DRAGO BOT УСПІШНО ЗАПУЩЕНИЙ НА TIER 1! ")
    print(" Код повністю оптимізовано під тексти! ")
    print("=========================================")
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
