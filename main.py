import os
import json
import base64
import logging
import asyncio
from io import BytesIO

import cv2
import numpy as np
import gspread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message

# ====== Логирование ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== Переменные окружения (настраиваются в Amvera) ======
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")          # токен бота
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")          # ссылка на таблицу
GOOGLE_CREDENTIALS_BASE64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")  # JSON-ключ в Base64

if not TELEGRAM_TOKEN or not GOOGLE_SHEET_URL or not GOOGLE_CREDENTIALS_BASE64:
    raise EnvironmentError(
        "Не заданы TELEGRAM_API_TOKEN, GOOGLE_SHEET_URL или GOOGLE_CREDENTIALS_BASE64"
    )

# ====== Инициализация бота ======
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ====== Настройка Google Sheets ======
def get_gspread_client():
    """Декодирует Base64-ключ и создаёт клиент gspread."""
    try:
        # Декодируем Base64 -> байты -> строка JSON
        decoded_bytes = base64.b64decode(GOOGLE_CREDENTIALS_BASE64)
        creds_json = decoded_bytes.decode("utf-8")
        creds_dict = json.loads(creds_json)
        return gspread.service_account_from_dict(creds_dict)
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Sheets: {e}")
        raise

gc = get_gspread_client()
sheet = gc.open_by_url(GOOGLE_SHEET_URL).sheet1  # первый лист

# ====== Распознавание QR-кода ======
def decode_qr(image_bytes: bytes) -> str | None:
    """
    Декодирует QR-код из изображения (байты).
    Возвращает строку данных или None, если QR не найден.
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        detector = cv2.QRCodeDetector()
        data, bbox, _ = detector.detectAndDecode(img)
        if data:
            return data.strip()
        return None
    except Exception as e:
        logger.error(f"Ошибка при декодировании QR: {e}")
        return None

# ====== Парсинг типового фискального чека ======
def parse_receipt_qr(qr_text: str) -> dict:
    """
    Если QR содержит параметры вида t=...&s=...&fn=...,
    возвращает словарь с полями. Иначе – сырой текст.
    """
    if "=" in qr_text and "&" in qr_text:
        try:
            params = dict(pair.split("=", 1) for pair in qr_text.split("&") if "=" in pair)
            return {
                "datetime": params.get("t", ""),
                "sum": params.get("s", ""),
                "fn": params.get("fn", ""),
                "fd": params.get("i", ""),     # номер фискального документа
                "fp": params.get("fp", ""),    # фискальный признак
                "operation_type": params.get("n", ""),
                "raw": qr_text
            }
        except:
            pass
    return {"raw": qr_text}

# ====== Запись в Google Таблицу ======
def add_row_to_sheet(data: dict):
    """Добавляет строку с данными чека. Если лист пуст, вставляет заголовки."""
    headers = ["Дата/время", "Сумма", "ФН", "ФД", "ФП", "Тип операции", "Сырой QR"]
    existing = sheet.get_all_values()
    if not existing or existing[0] != headers:
        # Если лист не пуст и заголовки не совпадают – очищаем и добавляем правильные
        if existing:
            sheet.clear()
        sheet.append_row(headers)

    row = [
        data.get("datetime", ""),
        data.get("sum", ""),
        data.get("fn", ""),
        data.get("fd", ""),
        data.get("fp", ""),
        data.get("operation_type", ""),
        data.get("raw", "")
    ]
    sheet.append_row(row)
    logger.info("Данные успешно добавлены в таблицу.")

# ====== Обработчики команд ======
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Пришлите фото с QR-кодом чека. "
        "Я расшифрую данные и сохраню их в Google Таблицу."
    )

# ====== Обработчик фото и файлов-изображений ======
@dp.message(F.photo | F.document)
async def handle_qr_image(message: Message):
    # Определяем источник изображения
    if message.photo:
        file_id = message.photo[-1].file_id  # самое высокое разрешение
    elif message.document:
        doc = message.document
        # Если это не изображение – предупреждаем
        if doc.mime_type and not doc.mime_type.startswith("image/"):
            return await message.answer("⚠️ Пожалуйста, отправьте изображение.")
        file_id = doc.file_id
    else:
        return

    # Скачиваем файл из Telegram
    try:
        file = await bot.get_file(file_id)
        file_bytes_io = BytesIO()
        await bot.download_file(file.file_path, file_bytes_io)
        image_bytes = file_bytes_io.getvalue()
    except Exception as e:
        logger.error(f"Ошибка скачивания файла: {e}")
        return await message.answer("❌ Не удалось загрузить файл. Попробуйте ещё раз.")

    # Распознаём QR
    qr_text = decode_qr(image_bytes)
    if not qr_text:
        return await message.answer("❌ QR-код не найден. Убедитесь, что он чётко виден на фото.")

    # Парсим данные
    data = parse_receipt_qr(qr_text)

    # Записываем в таблицу
    try:
        add_row_to_sheet(data)
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {e}")
        return await message.answer(
            "⚠️ QR-код прочитан, но не удалось сохранить в таблицу. "
            "Проверьте доступ и права сервисного аккаунта."
        )

    # Формируем ответ пользователю
    if "datetime" in data:
        answer = (
            f"✅ Чек добавлен!\n"
            f"📅 Дата/время: {data['datetime']}\n"
            f"💰 Сумма: {data['sum']} ₽\n"
            f"🧾 ФН: {data['fn']}\n"
            f"📄 ФД: {data['fd']}\n"
            f"🔐 ФП: {data['fp']}"
        )
    else:
        answer = (
            f"✅ QR-код распознан, содержимое сохранено:\n"
            f"<code>{data['raw']}</code>"
        )

    await message.answer(answer, parse_mode="HTML")

# ====== Запуск ======
async def main():
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
