import os
import random
import io
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
    "max_output_tokens": 2048,  # Дозволяємо писати великі розгорнуті тексти
    "temperature": 0.85,
}

# Налаштування моделі Gemini 2.5 Flash із системним характером Драго
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    system_instruction=(
        "Ти — зухвалий, надзвичайно розумний та дотепний ШІ-помічник на ім'я Драго. "
    "Спілкуйся виключно українською мовою. Використовуй молодіжний сленг, міцні слівця, "
    "іноді нецензурну лексику (мати) для емоційності, якщо це доречно. "
    "Будь живим, неформальним розмовником, злегка іронізуй, підколюй юзера, але завжди допомагай. "
    "Обов'язково закінчуй свої думки, не обривай речення на пів слові!"
    )
)
# 🛠️ ЗНІМАЄМО ФІЛЬТРИ ЦЕНЗУРИ GOOGLE (Щоб пропускав мати)
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]
# 🧠 СЛОВНИК ДЛЯ ГЛИБОКОЇ ПАМ'ЯТІ (Тільки для тексту)
bot_chats = {}

def get_gemini_chat(chat_id):
    """Отримує або створює унікальну сесію чату з пам'яттю для конкретного чату"""
    if chat_id not in bot_chats:
        bot_chats[chat_id] = model.start_chat(history=[])
    return bot_chats[chat_id]


# 🎭 1. КОМАНДА ДЛЯ ЛОКАЛЬНИХ МЕМІВ (.мем)
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


# 🖼️ 2. ЗІР (ОБРОБКА ФОТО)
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    chat_type = message.chat.type
    caption = message.caption or "Уважно подивись на це фото, розпізнай що на ньому зображено і розпиши своїми словами."

    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'джарвіс']
        if not (any(w in caption.lower() for w in trigger_words) or f"@{bot.get_me().username}" in caption):
            return

    try:
        bot.send_chat_action(chat_id, 'typing')
        
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        image_part = {
            "data": downloaded_file,
            "mime_type": "image/jpeg"
        }
        
        response = model.generate_content([caption, image_part])
        
        try:
            bot.reply_to(message, response.text, parse_mode="Markdown")
        except Exception:
            bot.reply_to(message, response.text)  # Беккап без розмітки, якщо Telegram лається
            
    except Exception as e:
        print(f"Помилка обробки фото: {e}")
        bot.reply_to(message, "Не зміг нормально роздивитися фотку, якась лажа з файлом.")


# 🎙️ 3. СЛУХ (ОБРОБКА ГОЛОСОВИХ ПОВІДОМЛЕНЬ)
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

# 💬 4. РОЗУМНІ ТЕКСТОВІ ДІАЛОГИ (ГРУПИ + ПАМ'ЯТЬ)
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text
    chat_id = message.chat.id
    chat_type = message.chat.type

    is_mentioned = False
    if chat_type in ['group', 'supergroup']:
        trigger_words = ['драго', 'драго,', 'джарвіс', 'джарвіс,']
        first_word = text.split()[0].lower() if text.split() else ""
        
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

    # Створюємо змінну для повідомлення-заглушки, щоб потім її редагувати
    status_msg = None
    try:
        # 1. Надсилаємо статус "typing" (двокрапка вгорі чату)
        bot.send_chat_action(chat_id, 'typing')
        
        # 2. Відправляємо проміжний жарт про СБУ і зберігаємо його в змінну
        status_msg = bot.reply_to(message, "Йде відправка даних в СБУ... 👮‍♂️")
        
        # 3. Отримуємо відповідь від Gemini
        gemini_chat = get_gemini_chat(chat_id)
        response = gemini_chat.send_message(text)
        
        # 4. Міняємо текст про СБУ на реальну відповідь від Драго
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=response.text) # Беккап без Markdown
        
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
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
import os

# Функція для запуску фейкового веб-сервера
def run_dummy_server():
    # Render автоматично передає порт у змінну оточення PORT
    port = int(os.environ.get("PORT", 8080))
    server_address = ("", port)
    httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    httpd.serve_forever()
# Хендлер на команду /generate для генерації картинок через твою бібліотеку
@bot.message_handler(commands=['generate'])
def generate_image_gemini(message):
    # Забираємо текст після команди /generate (пропускаємо перші 9 символів)
    prompt = message.text[9:].strip()

    if not prompt:
        bot.reply_to(message, "⚠️ Напиши опис картини! Наприклад: /generate a cyberpunk cat")
        return

    # Надсилаємо повідомлення про старт генерації
    status_msg = bot.reply_to(message, "⏳ Драго запускає Imagen 3... Зачекай трохи.")

    try:
        # Використовуємо твій імпорт 'genai' для виклику Imagen 3
        # Завантажуємо модель для зображень
        imagen = genai.ImageGenerationModel("imagen-3.0-generate-002")
        
        # Генеруємо зображення
        result = imagen.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="1:1" # Квадратна картинка
        )

        # Оскільки результат повертає об'єкт зображення PIL, конвертуємо його в байти для Telegram
        for image in result.images:
            bio = io.BytesIO()
            bio.name = 'image.jpeg'
            image.image.save(bio, 'JPEG')
            bio.seek(0)
            
            # Видаляємо статус-повідомлення "⏳ Драго запускає..."
            bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)

            # Надсилаємо готове фото
            bot.send_photo(
                chat_id=message.chat.id,
                photo=bio,
                caption=f"🔥 Твоя картинка за запитом: {prompt}\n(Згенеровано Драго через Imagen 3)",
                reply_to_message_id=message.message_id
            )

    except Exception as e:
        print(f"Помилка генерації зображення: {e}")
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text="❌ Щось пішло не так при створенні картинки. Зміни опис або спробуй пізніше."
        )
# Запускаємо сервер в окремому потоці, щоб він не заважав боту
threading.Thread(target=run_dummy_server, daemon=True).start()
# Запуск бота
if __name__ == "__main__":
    print("=========================================")
    print(" DRAGO BOT УСПІШНО ЗАПУЩЕНИЙ НА TIER 1! ")
    print(" Код повністю оптимізовано під тексти! ")
    print("=========================================")
    bot.infinity_polling()
