import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
import aiosqlite
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

API_TOKEN = "API_TOKEN"
ADMIN_IDS = [7241469346, 1204112225, 5228430828]  # my, мама, Кристина

SCHEDULE = [
    ("Вторник", "18:20-19:00", 6),
    ("Вторник", "19:10-19:50", 6),
    ("Среда", "12:00-12:40", 6),
    ("Среда", "17:30-18:10", 6),
    ("Среда", "18:20-19:00", 6),
    ("Среда", "19:10-19:50", 6),
    ("Четверг", "18:20-19:00", 6),
    ("Четверг", "19:10-19:50", 6),
    ("Пятница", "12:00-12:40", 6),
    ("Суббота", "11:20-12:00", 6),
    ("Суббота", "13:00-13:40", 6),
    ("Суббота", "13:50-14:30", 6),
    ("Суббота", "16:30-17:10", 6),
    ("Воскресенье", "17:30-18:10", 6),
    ("Воскресенье", "18:20-19:00", 6),
]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

# FSM
class RegisterStates(StatesGroup):
    choosing_day = State()
    choosing_time = State()
    entering_name = State()

# Клавиатуры
main_kb = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="📅 Расписание"), types.KeyboardButton(text="✏️ Записаться")],
        [types.KeyboardButton(text="❌ Отменить запись")]
    ],
    resize_keyboard=True
)

def get_days_kb():
    days = sorted(
        set([d for d, t, m in SCHEDULE]),
        key=lambda x: ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"].index(x)
    )
    buttons = [[types.InlineKeyboardButton(text=day, callback_data=f"day_{day}")] for day in days]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

def get_times_kb(day):
    buttons = [
        [types.InlineKeyboardButton(text=t, callback_data=f"time_{day}_{t}")]
        for d, t, m in SCHEDULE if d == day
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

# --- База данных ---
async def init_db():
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                user_name TEXT,
                child_name TEXT,
                day TEXT,
                time TEXT,
                notify_sent INTEGER DEFAULT 0
            )
        """)
        await db.commit()

async def get_free_places(day, time):
    max_places = next((m for d, t, m in SCHEDULE if d == day and t == time), 0)
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM records WHERE day=? AND time=?", (day, time))
        count = (await cursor.fetchone())[0]
    return max_places - count

async def get_schedule_text():
    text = ""
    for d, t, m in SCHEDULE:
        free = await get_free_places(d, t)
        text += f"📌 <b>{d}</b>\n🕒 {t}\n🆓 Свободных мест: <b>{free}</b>\n\n"
    return text

async def add_record(user_id, user_name, child_name, day, time):
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute(
            "INSERT INTO records (user_id, user_name, child_name, day, time) VALUES (?, ?, ?, ?, ?)",
            (user_id, user_name, child_name, day, time)
        )
        await db.commit()

async def get_user_records(user_id):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT id, day, time, child_name FROM records WHERE user_id=? ORDER BY day, time", (user_id,)
        )
        return await cursor.fetchall()

async def delete_record(record_id, user_id=None):
    async with aiosqlite.connect("db.sqlite3") as db:
        if user_id:
            await db.execute("DELETE FROM records WHERE id=? AND user_id=?", (record_id, user_id))
        else:
            await db.execute("DELETE FROM records WHERE id=?", (record_id,))
        await db.commit()

async def get_all_records():
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT id, user_name, child_name, day, time FROM records ORDER BY day, time"
        )
        return await cursor.fetchall()

async def get_upcoming_records_for_notify():
    now = datetime.now()
    upcoming = []
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT id, user_id, child_name, day, time FROM records WHERE notify_sent=0"
        )
        rows = await cursor.fetchall()
        for row in rows:
            record_id, user_id, child_name, day, time = row
            dt = get_next_datetime(day, time)
            if dt and 0 <= (dt - now).total_seconds() <= 3600:
                upcoming.append((record_id, user_id, child_name, day, time, dt))
    return upcoming

async def mark_notified(record_id):
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("UPDATE records SET notify_sent=1 WHERE id=?", (record_id,))
        await db.commit()

def get_next_datetime(day, time):
    days_map = {
        "Понедельник": 0, "Вторник": 1, "Среда": 2, "Четверг": 3,
        "Пятница": 4, "Суббота": 5, "Воскресенье": 6
    }
    try:
        weekday = days_map[day]
        start_time = datetime.strptime(time.split('-')[0], "%H:%M").time()
        now = datetime.now()
        days_ahead = (weekday - now.weekday() + 7) % 7
        if days_ahead == 0 and now.time() > start_time:
            days_ahead = 7
        dt = datetime.combine(now.date() + timedelta(days=days_ahead), start_time)
        return dt
    except Exception:
        return None

# --- Хэндлеры ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать!\n\nВыберите действие:",
        reply_markup=main_kb
    )

@dp.message(F.text == "📅 Расписание")
async def show_schedule(message: types.Message):
    text = await get_schedule_text()
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "✏️ Записаться")
async def start_register(message: types.Message, state: FSMContext):
    await state.set_state(RegisterStates.choosing_day)
    await message.answer("Выберите день для записи на 'Нейрогимнастику':", reply_markup=get_days_kb())

@dp.callback_query(F.data.startswith("day_"), RegisterStates.choosing_day)
async def choose_day(callback: types.CallbackQuery, state: FSMContext):
    day = callback.data[4:]
    await state.update_data(day=day)
    await state.set_state(RegisterStates.choosing_time)
    await callback.message.edit_text("Выберите время:", reply_markup=get_times_kb(day))
    await callback.answer()

@dp.callback_query(F.data.startswith("time_"), RegisterStates.choosing_time)
async def choose_time(callback: types.CallbackQuery, state: FSMContext):
    _, day, time = callback.data.split("_", 2)
    free = await get_free_places(day, time)
    if free <= 0:
        await callback.answer("Нет свободных мест!", show_alert=True)
        return
    await state.update_data(time=time)
    await state.set_state(RegisterStates.entering_name)
    await callback.message.edit_text(f"Введите имя и фамилию ребёнка для записи на {day} {time}:")
    await callback.answer()

@dp.message(RegisterStates.entering_name)
async def get_child_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    day = data.get("day")
    time = data.get("time")
    child_name = message.text.strip()
    free = await get_free_places(day, time)
    if free <= 0:
        await message.answer("К сожалению, мест уже нет.")
        await state.clear()
        return
    await add_record(message.from_user.id, message.from_user.full_name, child_name, day, time)
    await message.answer(f"✅ Запись успешно создана!\n{child_name} записан(а) на {day} {time}.", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "❌ Отменить запись")
async def cancel_record(message: types.Message):
    records = await get_user_records(message.from_user.id)
    if not records:
        await message.answer("У вас нет активных записей.")
        return
    buttons = [
        [types.InlineKeyboardButton(text=f"{child_name} — {day} {time}", callback_data=f"del_{rec_id}")]
        for rec_id, day, time, child_name in records
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите запись для отмены:", reply_markup=kb)

@dp.callback_query(F.data.startswith("del_"))
async def delete_user_record(callback: types.CallbackQuery):
    rec_id = int(callback.data[4:])
    await delete_record(rec_id, user_id=callback.from_user.id)
    await callback.message.edit_text("Запись отменена.", reply_markup=main_kb)
    await callback.answer()

# --- Админ-панель ---
@dp.message(F.text.lower() == "админ-панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return
    records = await get_all_records()
    if not records:
        await message.answer("Нет записей.")
        return
    text = "Все записи:\n\n"
    buttons = []
    for rec_id, user_name, child_name, day, time in records:
        text += f"{child_name} ({user_name}) — {day} {time}\n"
        buttons.append([types.InlineKeyboardButton(text=f"Удалить {child_name} {day} {time}", callback_data=f"admindel_{rec_id}")])
    kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("admindel_"))
async def admin_delete_record(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    rec_id = int(callback.data[9:])
    await delete_record(rec_id)
    await callback.message.edit_text("Запись удалена.")
    await callback.answer()

# --- Напоминания ---
async def send_notifications():
    upcoming = await get_upcoming_records_for_notify()
    for rec_id, user_id, child_name, day, time, dt in upcoming:
        try:
            await bot.send_message(
                user_id,
                f"⏰ Напоминаем, что у вас сегодня занятие по нейрогимнастике в {time} ({day}) для {child_name}."
            )
            await mark_notified(rec_id)
        except Exception as e:
            print(f"Ошибка отправки напоминания: {e}")

async def main():
    await init_db()
    scheduler.add_job(send_notifications, "interval", minutes=1)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())