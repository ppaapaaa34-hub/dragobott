"""Telegram worker kept separate from the Express web service.

Render starts this as a Background Worker, so it never competes with the web
server for $PORT.  The legacy jarvis_bot.py is intentionally retained as the
original bot implementation; this small entry point owns only the Mini App.
"""
import os
import logging
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

logging.basicConfig(level=logging.INFO)
token = os.environ.get('TELEGRAM_TOKEN')
web_app_url = os.environ.get('WEB_APP_URL')
if not token or not web_app_url:
    raise RuntimeError('TELEGRAM_TOKEN and WEB_APP_URL must be configured for the bot worker')

bot = telebot.TeleBot(token)

@bot.message_handler(commands=['start'])
def start(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton('🐉 Відкрити Drago Tap Empire', web_app=WebAppInfo(web_app_url)))
    bot.send_message(message.chat.id, 'Вітаю! Відкривайте Mini App кнопкою нижче.', reply_markup=markup)

@bot.message_handler(content_types=['web_app_data'])
def web_app_data(message):
    logging.info('Mini App data received from %s', message.from_user.id)

if __name__ == '__main__':
    bot.infinity_polling(skip_pending=True, allowed_updates=['message'])
