import os
import telebot
import gspread
import requests
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_URL = os.environ.get("SHEET_URL")

bot = telebot.TeleBot(BOT_TOKEN)

# Подключение к публичной таблице
gc = gspread.Client()
SHEET = gc.open_by_url(SHEET_URL).sheet1

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
        # 1. Скачиваем фото
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        photo_content = requests.get(file_url).content

        # 2. Отправляем в API для распознавания QR
        api_url = "https://api.qrserver.com/v1/read-qr-code/"
        files = {'file': photo_content}
        response = requests.post(api_url, files=files)
        if response.status_code != 200:
            raise Exception("Сервер распознавания временно недоступен")

        data = response.json()
        if not data or not data[0].get('symbol'):
            raise Exception("QR-код не найден. Попробуйте сфотографировать крупнее и чётче.")

        qr_text = data[0]['symbol'][0]['data']
        if not qr_text:
            raise Exception("Не удалось прочитать QR-код.")

        # 3. Парсим параметры
        params = dict(x.split('=') for x in qr_text.split('&') if '=' in x)
        raw_date = params.get('t', '')
        total_sum = params.get('s', '0')
        account = user_data[chat_id]['account']

        # 4. Форматируем дату
        if raw_date:
            try:
                dt = datetime.strptime(raw_date, '%Y%m%dT%H%M%S')
                date_formatted = dt.strftime('%d.%m.%Y %H:%M')
            except ValueError:
                date_formatted = raw_date
        else:
            date_formatted = "Неизвестно"

        # 5. Сумма
        try:
            total_sum_float = float(total_sum.replace(',', '.'))
        except ValueError:
            total_sum_float = 0.0

        # 6. Запись в таблицу
        SHEET.append_row([date_formatted, account, total_sum_float, qr_text])

        bot.send_message(chat_id, f"✅ Данные внесены!\n📅 Дата: {date_formatted}\n💳 Счет: {account}\n💰 Сумма: {total_sum} руб.")
        del user_data[chat_id]

    except Exception as e:
        bot.send_message(chat_id, f"❌ Ошибка обработки: {str(e)}")

if __name__ == "__main__":
    bot.polling(none_stop=True)
