import os
import json
import base64
import logging
import asyncio
from io import BytesIO
from datetime import datetime

import gspread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
GOOGLE_CREDENTIALS_BASE64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")

if not TELEGRAM_TOKEN or not GOOGLE_SHEET_URL or not GOOGLE_CREDENTIALS_BASE64:
    raise EnvironmentError("Не заданы TELEGRAM_API_TOKEN, GOOGLE_SHEET_URL или GOOGLE_CREDENTIALS_BASE64")

# --- Инициализация бота ---
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Google Sheets ---
def get_gspread_client():
    decoded_bytes = base64.b64decode(GOOGLE_CREDENTIALS_BASE64)
    creds_dict = json.loads(decoded_bytes.decode("utf-8"))
    return gspread.service_account_from_dict(creds_dict)

gc = get_gspread_client()
sh = gc.open_by_url(GOOGLE_SHEET_URL)

# Открываем или создаём лист "Позиции"
try:
    ws_items = sh.worksheet("Позиции")
except:
    ws_items = sh.add_worksheet(title="Позиции", rows="1000", cols="10")
    ws_items.append_row(["Дата", "Счёт", "Товар", "Цена", "Количество", "Сумма"])

# --- Категории (счета) ---
ACCOUNTS = [
    "🥦 Продукты", "🚌 Транспорт", "🍔 Кафе", "🎉 Развлечения",
    "🏠 ЖКХ", "💊 Здоровье", "📚 Образование", "🛒 Прочее"
]

accounts_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=acc)] for acc in ACCOUNTS],
    resize_keyboard=True,
    one_time_keyboard=True
)

# --- Состояния ---
class Receipt(StatesGroup):
    waiting_for_account = State()

# --- Вспомогательные функции ---
def parse_receipt_json(data: dict) -> list[dict]:
    """
    Из JSON чека извлекает список позиций.
    Ожидаемая структура: { "items": [ { "name": ..., "price": ..., "quantity": ..., "sum": ... }, ... ] }
    Возвращает список словарей с ключами name, price, quantity, sum.
    """
    items = data.get("items", [])
    if not items:
        return []
    result = []
    for item in items:
        result.append({
            "name": item.get("name", "").strip(),
            "price": float(item.get("price", 0)),
            "quantity": float(item.get("quantity", 1)),
            "sum": float(item.get("sum", 0))
        })
    return result

def write_items_to_sheet(date_str: str, account: str, items: list[dict]):
    """Записывает позиции в лист Позиции."""
    for item in items:
        ws_items.append_row([
            date_str,
            account,
            item["name"],
            item["price"],
            item["quantity"],
            item["sum"]
        ])
    logger.info(f"Добавлено {len(items)} позиций в категорию '{account}'")

# --- Обработчики ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот для учёта чеков.\n"
        "Отправьте мне JSON-файл из приложения «Проверка чека» (или другого), "
        "и я разложу все позиции по вашей Google Таблице.\n"
        "Перед сохранением спрошу категорию (счёт)."
    )

@dp.message(F.document)
async def handle_json_file(message: Message, state: FSMContext):
    doc = message.document
    # Проверяем, что это JSON
    if not (doc.file_name and doc.file_name.lower().endswith(".json")) and \
       not (doc.mime_type and "json" in doc.mime_type):
        return await message.answer("⚠️ Я принимаю только JSON-файлы с чеками.")

    # Скачиваем и парсим
    try:
        file = await bot.get_file(doc.file_id)
        file_bytes = BytesIO()
        await bot.download_file(file.file_path, file_bytes)
        content = file_bytes.getvalue().decode("utf-8")
        data = json.loads(content)
    except Exception as e:
        logger.error(f"Ошибка чтения JSON: {e}")
        return await message.answer("❌ Не удалось прочитать JSON-файл. Проверьте формат.")

    # Извлекаем позиции
    items = parse_receipt_json(data)
    if not items:
        return await message.answer("❌ В чеке не найдено позиций (поле 'items').")

    # Дата (из JSON или сегодня)
    date_str = data.get("dateTime") or data.get("date") or datetime.now().strftime("%Y-%m-%d")

    # Сохраняем в состоянии
    await state.update_data(items=items, date_str=date_str)
    await message.answer(
        f"📋 Чек от {date_str}, позиций: {len(items)}.\nВыберите категорию:",
        reply_markup=accounts_keyboard
    )
    await state.set_state(Receipt.waiting_for_account)

@dp.message(Receipt.waiting_for_account)
async def process_account(message: Message, state: FSMContext):
    account = message.text
    # Убираем emoji для чистоты названия счёта (опционально)
    for emoji in ["🥦 ", "🚌 ", "🍔 ", "🎉 ", "🏠 ", "💊 ", "📚 ", "🛒 "]:
        account = account.replace(emoji, "")

    data = await state.get_data()
    items = data.get("items", [])
    date_str = data.get("date_str", datetime.now().strftime("%Y-%m-%d"))

    try:
        write_items_to_sheet(date_str, account, items)
        await message.answer(
            f"✅ {len(items)} позиций сохранено в категорию «{account}».",
            reply_markup=types.ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {e}")
        await message.answer("⚠️ Не удалось записать в таблицу. Попробуйте позже.")

    await state.clear()

# --- Запуск ---
async def main():
    logger.info("Бот запущен (JSON-режим)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
