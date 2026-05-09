"""
Кот-кликер Telegram Mini App с реферальной системой
Стек: aiogram 3.x + Flask + SQLite
"""

import asyncio
import logging
import os
from datetime import datetime
from threading import Thread

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from flask import Flask, request, jsonify, send_from_directory
import aiosqlite

# ---------- Настройки ----------
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"  # Замените на свой токен
WEBAPP_URL = "https://ваш-домен.com"  # URL вашего веб-приложения
DATABASE = "clicker.db"

# ---------- Инициализация ----------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Flask приложение
app = Flask(__name__, static_folder='static')
# Разместите index.html в папке static
os.makedirs('static', exist_ok=True)

# ---------- База данных ----------
async def init_db():
    """Создание таблиц базы данных"""
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                clicks INTEGER DEFAULT 0,
                referrer_id INTEGER,
                referrals_count INTEGER DEFAULT 0,
                referral_bonus_claimed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_user(user_id: int) -> dict | None:
    """Получение данных пользователя"""
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def create_user(user_id: int, username: str, first_name: str, referrer_id: int = None):
    """Создание нового пользователя с реферальным бонусом"""
    async with aiosqlite.connect(DATABASE) as db:
        # Проверяем, существует ли уже пользователь
        existing = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if await existing.fetchone():
            return
        
        # Базовый баланс
        balance = 0
        referral_bonus_claimed = False
        
        # Если пришел по реферальной ссылке
        if referrer_id and referrer_id != user_id:
            referrer = await get_user(referrer_id)
            if referrer:
                # Начисляем бонус пригласившему (5000 рыбок)
                await db.execute(
                    "UPDATE users SET balance = balance + 5000, referrals_count = referrals_count + 1 WHERE user_id = ?",
                    (referrer_id,)
                )
                # Новому игроку даем 2000 рыбок
                balance = 2000
                referral_bonus_claimed = True
        
        # Создаем запись пользователя
        await db.execute("""
            INSERT INTO users (user_id, username, first_name, balance, referrer_id, referral_bonus_claimed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, first_name, balance, referrer_id, referral_bonus_claimed, datetime.now()))
        await db.commit()

async def update_clicks(user_id: int, clicks: int):
    """Обновление кликов и баланса с защитой от дублирования"""
    async with aiosqlite.connect(DATABASE) as db:
        user = await get_user(user_id)
        if not user:
            return None
        
        # Простая защита: проверяем, что клики не уменьшились
        if clicks < user['clicks']:
            return user
        
        # Вычисляем разницу кликов
        new_clicks = clicks - user['clicks']
        if new_clicks <= 0:
            return user
        
        # Начисляем рыбок (1 клик = 1 рыбка + бонус за уровень)
        level_bonus = user['level'] * 0.5
        earned = int(new_clicks * (1 + level_bonus))
        
        # Обновляем данные
        await db.execute("""
            UPDATE users 
            SET clicks = ?, balance = balance + ?, level = CASE 
                WHEN balance + ? >= level * 1000 THEN level + 1 
                ELSE level 
            END
            WHERE user_id = ?
        """, (clicks, earned, earned, user_id))
        await db.commit()
        
        return await get_user(user_id)

# ---------- Обработчики бота ----------
@dp.message(CommandStart(deep_link=True))
async def start_deep_link(message: types.Message):
    """Обработчик старта с реферальной ссылкой"""
    referrer_id = int(message.text.split()[1]) if len(message.text.split()) > 1 else None
    await create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        referrer_id
    )
    
    await show_welcome(message)

@dp.message(CommandStart())
async def start_command(message: types.Message):
    """Обычный старт без реферала"""
    await create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    await show_welcome(message)

async def show_welcome(message: types.Message):
    """Приветственное сообщение с кнопками"""
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Ошибка создания профиля. Попробуйте /start")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🐱 ИГРАТЬ В КЛИКЕР", 
            web_app=WebAppInfo(url=f"{WEBAPP_URL}?user_id={message.from_user.id}")
        )],
        [
            InlineKeyboardButton(text="👥 Друзья", callback_data="friends"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats")
        ]
    ])
    
    bonus_text = ""
    if user['referral_bonus_claimed']:
        bonus_text = "\n🎁 <b>Вы получили реферальный бонус: 2000 рыбок!</b>"
    
    await message.answer(
        f"🐱 <b>Кот-Кликер</b>\n\n"
        f"Привет, {message.from_user.first_name}!\n"
        f"Кликай по коту и зарабатывай рыбок!\n"
        f"Приглашай друзей и получай бонусы.{bonus_text}\n\n"
        f"💰 Баланс: <b>{user['balance']}</b> рыбок\n"
        f"📈 Уровень: <b>{user['level']}</b>",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "friends")
async def show_friends(callback: types.CallbackQuery):
    """Показывает реферальную информацию"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка загрузки данных")
        return
    
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={callback.from_user.id}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Скопировать ссылку", callback_data="copy_link")],
        [InlineKeyboardButton(text="🐱 Играть", web_app=WebAppInfo(url=f"{WEBAPP_URL}?user_id={callback.from_user.id}"))]
    ])
    
    await callback.message.edit_text(
        f"👥 <b>Реферальная система</b>\n\n"
        f"🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"👤 Приглашено друзей: <b>{user['referrals_count']}</b>\n"
        f"💰 Вы получаете <b>5000 рыбок</b> за каждого друга\n"
        f"🎁 Друг получает <b>2000 рыбок</b> при входе\n"
        f"📊 Заработано на рефералах: <b>{user['referrals_count'] * 5000}</b> рыбок",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    """Показывает статистику игрока"""
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка загрузки данных")
        return
    
    next_level = user['level'] * 1000
    progress = (user['balance'] % 1000) / 1000 * 100
    
    await callback.message.edit_text(
        f"📊 <b>Статистика игрока</b>\n\n"
        f"💰 Баланс: <b>{user['balance']}</b> 🐟\n"
        f"📈 Уровень: <b>{user['level']}</b>\n"
        f"📊 Прогресс до {user['level']+1} уровня: <b>{progress:.1f}%</b>\n"
        f"👆 Всего кликов: <b>{user['clicks']}</b>\n"
        f"👥 Приглашено друзей: <b>{user['referrals_count']}</b>\n"
        f"📅 Дата регистрации: <b>{user['created_at'][:10]}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    """Возврат в главное меню"""
    user = await get_user(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐱 Играть", web_app=WebAppInfo(url=f"{WEBAPP_URL}?user_id={callback.from_user.id}"))],
        [
            InlineKeyboardButton(text="👥 Друзья", callback_data="friends"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats")
        ]
    ])
    
    await callback.message.edit_text(
        f"🐱 <b>Кот-Кликер</b>\n\n"
        f"💰 Баланс: <b>{user['balance']}</b> рыбок\n"
        f"📈 Уровень: <b>{user['level']}</b>",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "copy_link")
async def copy_link(callback: types.CallbackQuery):
    """Уведомление о копировании ссылки"""
    await callback.answer("✅ Ссылка скопирована! Отправьте её друзьям", show_alert=True)

# ---------- Flask API ----------
@app.route('/')
def index():
    """Отдача главной страницы"""
    return send_from_directory('static', 'index.html')

@app.route('/api/user/<int:user_id>')
async def api_get_user(user_id: int):
    """API: получение данных пользователя"""
    user = await get_user(user_id)
    if not user:
        return jsonify({"error": "Пользователь не найден"}), 404
    
    return jsonify({
        "user_id": user['user_id'],
        "username": user['username'],
        "balance": user['balance'],
        "level": user['level'],
        "clicks": user['clicks'],
        "referrals_count": user['referrals_count']
    })

@app.route('/api/click', methods=['POST'])
async def api_click():
    """API: обработка кликов с защитой от дублирования"""
    data = request.json
    user_id = data.get('user_id')
    clicks = data.get('clicks', 0)
    
    if not user_id:
        return jsonify({"error": "user_id обязателен"}), 400
    
    updated_user = await update_clicks(user_id, clicks)
    if not updated_user:
        return jsonify({"error": "Ошибка обновления"}), 500
    
    return jsonify({
        "balance": updated_user['balance'],
        "level": updated_user['level'],
        "clicks": updated_user['clicks']
    })

# ---------- Запуск ----------
async def main():
    """Запуск бота и инициализация БД"""
    await init_db()
    await dp.start_polling(bot)

if __name__ == '__main__':
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=5000))
    flask_thread.daemon = True
    flask_thread.start()
    
    # Запускаем бота
    asyncio.run(main())
