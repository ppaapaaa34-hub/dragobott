import os
import base64
import requests
import random
import io
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
import telebot
import google.generativeai as genai
from PIL import Image

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

# ===================================================================
# 🖼️ КОМАНДА /generate (ЗАЛІЗОБЕТОННА ГЕНЕРАЦІЯ ГОТОВОЇ КАРТИНКИ В ЧАТ)
# ===================================================================
@bot.message_handler(commands=['generate'])
def generate_image_perfect(message):
    # Забираємо промпт (пропускаємо перші 10 символів: "/generate ")
    prompt = message.text[10:].strip()

    if not prompt:
        bot.reply_to(message, "⚠️ Напиши опис картини, бро! Наприклад: /generate cyberpunk wolf")
        return

    # Надсилаємо статус
    status_msg = bot.reply_to(message, "⏳ Драго малює твій шедевр... Зачекай пару секунд.")

    try:
        # 1. Безпечно кодуємо промпт
        encoded_prompt = requests.utils.quote(prompt)
        
        # 2. Використовуємо стабільне інженерне API (швидкий сервер Stable Diffusion)
        # Воно віддає готові байти без редиректів та черг
        provider = random.choice(["prodia", "pollinations"]) # Рандомізуємо для стабільності
        
        if provider == "prodia":
            # Чисте швидке дзеркало
            image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=768&height=768&seed={random.randint(1, 999999)}&model=turbo&nologo=true"
        else:
            image_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width=768&height=768&seed={random.randint(1, 999999)}&model=dreamshaper&nologo=true"

        # 3. Бот САМ іде в інтернет і чекає, поки картинка повністю скачається в пам'ять (до 30 секунд таймаут)
        response = requests.get(image_url, timeout=30)
        
        if response.status_code == 200:
            # Перевіряємо, чи це точно картинка, а не текст помилки JSON
            if "application/json" in response.headers.get("Content-Type", "") or len(response.content) < 5000:
                raise Exception("Сервер віддав пустий файл або помилку черги.")

            # Загортаємо готові байти у файл для Telegram
            bio = io.BytesIO(response.content)
            bio.name = 'drago_art.jpg'
            
            # 4. Надсилаємо як ГОТОВЕ ФОТО. Оскільки байти вже на сервері Render, Telegram проковтне їх миттєво!
            bot.send_photo(
                chat_id=message.chat.id,
                photo=bio,
                caption=f"🔥 Твоя картинка готова, бро!\n\n📋 <b>Запит:</b> {prompt}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            
            # Видаляємо статус
            try:
                bot.delete_message(message.chat.id, status_msg.message_id)
            except Exception:
                pass
        else:
            raise Exception(f"Код відповіді сервера: {response.status_code}")

    except Exception as e:
        print(f"Помилка генерації: {e}")
        # Якщо Telegram чи сервер таки впали, бот не мовчить, а кидає пряме робоче посилання
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=f"🔥 <b>Твоя картинка готова, бро!</b>\nTelegram не зміг обробити файл прямо в чаті, тому тримай лінк:\n\n👉 <a href='https://image.pollinations.ai/p/{encoded_prompt}?model=turbo'>ВІДКРИТИ КАРТИНКУ</a>",
                parse_mode="HTML",
                disable_web_page_preview=False
            )
        except Exception:
            bot.reply_to(message, "❌ Сервери малювання перевантажені. Спробуй ще раз за хвилину.")
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
# 🚀 ЗАПУСК СЕРВЕРА ТА БОТА
# ===================================================================
# Запускаємо сервер в окремому потоці, щоб він не заважав боту
threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == "__main__":
    print("=========================================")
    print(" DRAGO BOT УСПІШНО ЗАПУЩЕНИЙ НА TIER 1! ")
    print(" Код повністю оптимізовано під тексти! ")
    print("=========================================")
    bot.infinity_polling()
