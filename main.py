import telebot
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from PIL import Image
from pyzbar.pyzbar import decode
import os
import json

# Теперь бот не хранит пароли в коде, а берет их из скрытых настроек сервера Amvera
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_CREDS_TEXT = os.environ.get("GOOGLE_CREDS")

bot = telebot.TeleBot(BOT_TOKEN)

# Настройка Google Таблиц
SCOPE = ["https://google.com", "https://googleapis.com"]
creds_dict = json.loads(GOOGLE_CREDS_TEXT)
CREDS = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
CLIENT = gspread.authorize(CREDS)
SHEET = CLIENT.open("Мои Расходы").sheet1 

user_data = {}

@bot.message_handler(commands=['start'])
def start_message(message):
    bot.send_message(message.chat.id, "Привет! Введите **Наименование счета** (например: Карта Т-Банк, Наличные):")
    bot.register_next_step_handler(message, get_account_name)

def get_account_name(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'account': message.text}
    bot.send_message(chat_id, f"Счет '{message.text}' выбран. Теперь пришлите **фотографию QR-кода** с чека.")

@bot.message_handler(content_types=['photo'])
def handle_qr_photo(message):
    chat_id = message.chat.id
    if chat_id not in user_data:
        bot.send_message(chat_id, "Пожалуйста, начните с команды /start")
        return

    bot.send_message(chat_id, "Загружаю и анализирую фото...")

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        image_path = f"qr_{chat_id}.jpg"
        with open(image_path, 'wb') as f:
            f.write(downloaded_file)

        img = Image.open(image_path)
        decoded_objects = decode(img)

        if not decoded_objects:
            raise Exception("Не удалось обнаружить QR-код. Сделайте фото ближе и четче.")

        qr_text = decoded_objects.data.decode('utf-8')
        params = dict(x.split('=') for x in qr_text.split('&'))
        
        raw_date = params.get('t', 'Неизвестно')
        total_sum = params.get('s', '0')
        account = user_data[chat_id]['account']

        if raw_date != 'Неизвестно':
            date_formatted = f"{raw_date[6:8]}.{raw_date[4:6]}.{raw_date[:4]} {raw_date[9:11]}:{raw_date[11:13]}"
        else:
            date_formatted = raw_date

        SHEET.append_row([date_formatted, account, float(total_sum), qr_text])
        bot.send_message(chat_id, f"✅ Данные внесены!\n📅 Дата: {date_formatted}\n💳 Счет: {account}\n💰 Сумма: {total_sum} руб.")
        
        os.remove(image_path)
        del user_data[chat_id]

    except Exception as e:
        bot.send_message(chat_id, f"❌ Ошибка обработки: {str(e)}")
        if os.path.exists(image_path):
            os.remove(image_path)

if __name__ == "__main__":
    bot.polling(none_stop=True)
