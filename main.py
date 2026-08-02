import os
import telebot
import gspread
import requests

# Теперь и токен бота, и ссылка на таблицу полностью скрыты в секретах Amvera
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_URL = os.environ.get("SHEET_URL")

bot = telebot.TeleBot(BOT_TOKEN)

# Подключаемся к таблице напрямую по публичной ссылке из переменной окружения
try:
    gc = gspread.public()
    SHEET = gc.open_by_url(SHEET_URL).sheet1
except Exception as e:
    print(f"Ошибка подключения к таблице: {e}")

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
        # Получаем прямую ссылку на фото из Telegram (Строка 42-43)
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org{BOT_TOKEN}/{file_info.file_path}"
        
        # Отправляем фото в бесплатное онлайн-API для чтения QR (Строка 45-47)
        api_url = f"https://qrserver.com{file_url}"
        response = requests.get(api_url).json()
        
        # Защита от сбоя ответа сервера (Строка 49-50)
        if not response or not isinstance(response, list):
            raise Exception("Не удалось связаться с сервером распознавания.")
            
        # Читаем текст из правильной структуры ответа API (Строка 52-53)
        qr_text = response[0]['symbol'][0]['data']

        
        if not qr_text:
            raise Exception("Не удалось обнаружить QR-код на фото. Пожалуйста, сделайте более четкий снимок крупным планом.")

        # Разбираем параметры строки чека
        params = dict(x.split('=') for x in qr_text.split('&'))
        raw_date = params.get('t', 'Неизвестно')
        total_sum = params.get('s', '0')
        account = user_data[chat_id]['account']

        if raw_date != 'Неизвестно':
            date_formatted = f"{raw_date[6:8]}.{raw_date[4:6]}.{raw_date[:4]} {raw_date[9:11]}:{raw_date[11:13]}"
        else:
            date_formatted = raw_date

        # Записываем данные в Google Таблицу по ссылке
        SHEET.append_row([date_formatted, account, float(total_sum), qr_text])
        bot.send_message(chat_id, f"✅ Данные внесены!\n📅 Дата: {date_formatted}\n💳 Счет: {account}\n💰 Сумма: {total_sum} руб.")
        
        del user_data[chat_id]

    except Exception as e:
        bot.send_message(chat_id, f"❌ Ошибка обработки: {str(e)}")

if __name__ == "__main__":
    bot.polling(none_stop=True)
