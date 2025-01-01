import json
import math
import openai
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, filters, CommandHandler, ContextTypes, CallbackQueryHandler
)
import asyncio
import datetime
import pytz
from datetime import datetime, timedelta
from fpdf import FPDF
import io
from dotenv import load_dotenv
import os
from yookassa import Configuration, Payment
import uuid
import asyncpg

load_dotenv()

DB_CONFIG = {
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
}
db_pool = None
Configuration.account_id = os.getenv("account_id")
Configuration.secret_key = os.getenv("secret_key")
openai_api_key = os.getenv("OPENAI_API_KEY")
telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

# переменые для управление из админ панели
ADMINS = list(map(int, os.getenv("ADMINS", "").split(',')))
subscription_chat_with_ai_is_true = True
subscription_search_book_is_true = True
count_limit_chat_with_ai = 10
count_limit_book_in_subscribe_day = 10
limit_page_book = 20
count_limit_book_day = 1
wait_hour = 1
count_search_book = 0
count_chat_ai = 0

# Хранилище подписок (для админов)
subscriptions = []
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
# Установите API-ключ OpenAI
openai.api_key = openai_api_key

# Получение пользователей без подписок
async def get_users_without_subscriptions():
    conn = await connect_db()
    try:
        no_subscription_users = await conn.fetch("""
            SELECT u.user_id
            FROM users u
            LEFT JOIN user_subscriptions us ON u.user_id = us.user_id
            WHERE us.user_id IS NULL OR us.end_date < $1
        """, datetime.now().date())
        return no_subscription_users
    finally:
        await close_db(conn)

# Получение пользователей с активными подписками
async def get_users_with_active_subscriptions():
    conn = await connect_db()
    try:
        active_users = await conn.fetch("""
            SELECT u.user_id
            FROM users u
            JOIN user_subscriptions us ON u.user_id = us.user_id
            WHERE us.end_date > $1
        """, datetime.now().date())
        return active_users
    finally:
        await close_db(conn)

# Получение всех пользователей
async def get_all_users():
    conn = await connect_db()
    try:
        users = await conn.fetch("SELECT user_id FROM users")  # Здесь предполагается, что таблица users имеет поле user_id
        return users
    finally:
        await close_db(conn)

# Добавление новой подписки
async def add_subscription_db(user_id, subscription_name, subscription_price, end_date):
    conn = await connect_db()
    await conn.execute("""
        INSERT INTO user_subscriptions (user_id, subscription_name, subscription_price, end_date)
        VALUES ($1, $2, $3, $4)
    """, user_id, subscription_name, subscription_price, end_date)
    await close_db(conn)

async def delete_subscription(subscription_id):
    conn = await connect_db()
    await conn.execute("""
        DELETE FROM user_subscriptions
        WHERE id = $1
    """, subscription_id)
    await close_db(conn)

async def get_user_subscriptions(user_id):
    conn = await connect_db()
    try:
        # Получение данных из таблицы
        user_subscriptions = await conn.fetch("""
            SELECT * FROM user_subscriptions WHERE user_id = $1
        """, user_id)
    finally:
        await close_db(conn)
    return user_subscriptions

async def update_reset_time(user_id, reset_time):
    conn = await connect_db()
    await conn.execute("""
        UPDATE users
        SET reset_time = $1
        WHERE user_id = $2
    """, reset_time, user_id)
    await close_db(conn)

async def update_count_words(user_id, new_count):
    """
    Обновляет count_words для пользователя до указанного значения.
    """
    conn = await connect_db()
    await conn.execute("""
        UPDATE users
        SET count_words = $1
        WHERE user_id = $2
    """, new_count, user_id)
    await close_db(conn)

async def increment_count_words(user_id):
    """
    Увеличивает count_words на 1 для пользователя.
    """
    conn = await connect_db()
    await conn.execute("""
        UPDATE users
        SET count_words = count_words + 1
        WHERE user_id = $1
    """, user_id)
    await close_db(conn)

async def update_user_library_dict(user_id: int, library_json: str):
    conn = await connect_db()
    await conn.execute(
        "UPDATE users SET library = $1 WHERE user_id = $2", 
        library_json,  # Передаем сериализованный JSON
        user_id
    )
    await close_db(conn)

# Обновление is_process_book пользователя в базе данных
async def update_user_process_book(user_id, is_processing):
    conn = await connect_db()
    # Выполняем обновление в таблице users
    await conn.execute("""
        UPDATE users
        SET is_process_book = $1
        WHERE user_id = $2
    """, is_processing, user_id)
    await close_db(conn)

async def update_user_library(user_id: int):
    conn = await connect_db()
    await conn.execute(
        "UPDATE users SET library = $1 WHERE user_id = $2", 
        [],  # Пустой список (можно использовать JSON формат или другое представление)
        user_id
    )
    await close_db(conn)

async def update_user_daily_book_count(user_id, new_count):
    conn = await connect_db()
    # Выполняем обновление в таблице users
    await conn.execute("""
        UPDATE users
        SET daily_book_count = $1
        WHERE user_id = $2
    """, new_count, user_id)
    await close_db(conn)

# Создаем асинхронное подключение
async def connect_db():
    conn = await asyncpg.connect(**DB_CONFIG)
    return conn

# Обновление данных пользователя
async def update_user_last_book_date(user_id, today_date):
    conn = await connect_db()
    # Выполняем обновление в таблице users
    await conn.execute("""
        UPDATE users
        SET last_book_date = $1
        WHERE user_id = $2
    """, today_date, user_id)
    await close_db(conn)

# Закрытие соединения с базой данных
async def close_db(conn):
    await conn.close()

# Добавление нового пользователя в базу данных
async def add_user(user_id, username):
    conn = await connect_db()
    await conn.execute("""
        INSERT INTO users (user_id, username, daily_book_count, last_book_date, is_process_book, count_words, reset_time)
        VALUES ($1, $2, 0, NULL, FALSE, 0, NULL)
        ON CONFLICT (user_id) DO NOTHING
    """, user_id, username)
    await close_db(conn)

async def update_user_daily_book_count(user_id, new_count):
    conn = await connect_db()
    # Выполняем обновление в таблице users
    await conn.execute("""
        UPDATE users
        SET daily_book_count = $1
        WHERE user_id = $2
    """, new_count, user_id)
    await close_db(conn)

# Проверка существования пользователя в базе данных
async def user_exists(user_id):
    conn = await connect_db()
    row = await conn.fetchrow("""
        SELECT 1 FROM users WHERE user_id = $1
    """, user_id)
    await close_db(conn)
    return row is not None

# Функция для получения пользователя из базы данных
async def get_user(user_id):
    conn = await connect_db()
    user = await conn.fetchrow("""
        SELECT * FROM users WHERE user_id = $1
    """, user_id)
    await close_db(conn)
    return user

async def get_user_for_username(username):
    conn = await connect_db()
    user = await conn.fetchrow("""
        SELECT * FROM users WHERE username = $1
    """, username)
    await close_db(conn)
    return user

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['chat_context'] = []
    context.user_data['current_mode'] = None
    context.user_data['book_title'] = None
    context.user_data['exact_title'] = None
    context.user_data['chapters'] = None
    context.user_data['awaiting_pages'] = False

    user_id = update.message.from_user.id
    username = update.message.from_user.username

    # Проверяем, существует ли пользователь в базе данных
    if not await user_exists(user_id):
        # Если пользователя нет в базе данных, добавляем его
        print(f'создаем нового пользователя в базе даных {user_id}')
        await add_user(user_id, username)

    # Создаем меню
    await handle_menu(update, context)

# Обработка кнопки "Меню"
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['chat_context'] = []
    context.user_data['current_mode'] = None
    context.user_data['book_title'] = None
    context.user_data['exact_title'] = None
    context.user_data['awaiting_pages'] = False
    query = update.callback_query if update.callback_query else None

    user_id = query.from_user.id if query else update.message.from_user.id

    # Получаем пользователя из базы данных
    user = await get_user(user_id)

    if user is None:
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Формируем текст приветствия
    greeting_message = f"🌟 Здравствуйте, {user['username']}! 👋\n\nМы рады видеть вас в нашем боте! 😊\nВыберите одну из опций ниже:👇"

    # Создаем кнопки для меню
    keyboard = [
        [InlineKeyboardButton("📚 Поиск книг", callback_data="search_books"),
         InlineKeyboardButton("🤖 Чат с ИИ", callback_data="chat_with_ai")],
        [InlineKeyboardButton("💳 Подписки", callback_data="subscriptions_menu"),
         InlineKeyboardButton("🎮 Игры", callback_data="game")],
        [InlineKeyboardButton("📚 Моя библиотека", callback_data="my_library")],
    ]

    # Добавляем кнопку "Админ панель" для администраторов
    if user_id in ADMINS:
        keyboard.append([InlineKeyboardButton("🔒 Админ панель", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Если вызвано из кнопки (callback), редактируем сообщение
    if query:
        await query.edit_message_text(greeting_message, reply_markup=reply_markup)
    else:
        # Иначе создаём новое сообщение
        await update.message.reply_text(greeting_message, reply_markup=reply_markup)

# Функция для асинхронной проверки статуса платежа
async def check_payment_status(payment_id, user_id, subscription_name, subscription_price, query):
    while True:
        updated_payment = Payment.find_one(payment_id)
        if updated_payment.status == "succeeded":
            # Оплата успешна, активируем подписку
            end_date = datetime.now() + timedelta(days=30)

            await add_subscription_db(user_id, subscription_name, float(subscription_price), end_date)
            #user_subscriptions.append({
            #    "user_id": user_id,
            #    "subscription_name": subscription_name,
            #    "price": subscription_price,
            #    "end_date": end_date
            #})


            await query.edit_message_text(
                f"✅ Подписка '{subscription_name}' успешно активирована!\n\n"
                f"📅 Действует до {end_date.strftime('%d.%m.%Y')}.",
                reply_markup=InlineKeyboardMarkup([ 
                    [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
                ])
            )
            break
        elif updated_payment.status == "canceled":
            # Оплата отменена
            await query.edit_message_text(
                f"⚠️ Оплата подписки '{subscription_name}' была отменена.\nПопробуйте снова.",
                reply_markup=InlineKeyboardMarkup([ 
                    [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
                ])
            )
            break
        else:
            # Ждем некоторое время перед следующей проверкой
            await asyncio.sleep(10)

async def generate_options_menu(options, context):
    # Тексты кнопок в зависимости от языка
    option_texts = {
        "russian": ["Ключ.идеи,анализ", "Цитаты из книги", "Биография автора", "Критика книги"],
        "english": ["Key Ideas, Analysis", "Quotes in the book", "Author's Biography", "Book Critique"]
    }

    action_texts = {
        "russian": {
            "next": "➡️ Далее",
            "skip": "⏩ Пропустить",
            "select_all": "✅ Выбрать все",
            "remove_all": "❌ Убрать все",
            "search_books": "🔙 Назад"
        },
        "english": {
            "next": "➡️ Next",
            "skip": "⏩ Skip",
            "select_all": "✅ Select All",
            "remove_all": "❌ Remove All",
            "search_books": "🔙Back"
        }
    }
    
    # Определяем, какие тексты использовать в зависимости от языка
    selected_language = context.user_data.get('book_language', 'russian')
    option_labels = option_texts[selected_language]
    action_labels = action_texts[selected_language]

    # Проверяем, есть ли активные опции, если есть, кнопка "Далее", иначе "Пропустить"
    action_button_text = action_labels["next"] if any(options.values()) else action_labels["skip"]
    remove_all_button = action_labels["remove_all"] if all(options.values()) else None
    select_all_button = action_labels["select_all"] if not all(options.values()) else None

    buttons = [
        [
            InlineKeyboardButton(
                f"{option_labels[0]} {'✅' if options['option_1'] else '❌'}",
                callback_data="toggle_option_option_1"
            )
        ],
        [
            InlineKeyboardButton(
                f"{option_labels[1]} {'✅' if options['option_2'] else '❌'}",
                callback_data="toggle_option_option_2"
            )
        ],
        [
            InlineKeyboardButton(
                f"{option_labels[2]} {'✅' if options['option_3'] else '❌'}",
                callback_data="toggle_option_option_3"
            )
        ],
        [
            InlineKeyboardButton(
                f"{option_labels[3]} {'✅' if options['option_4'] else '❌'}",
                callback_data="toggle_option_option_4"
            )
        ],
        [
            InlineKeyboardButton(action_button_text, callback_data="skip_options")
        ],
        [
            InlineKeyboardButton(remove_all_button if remove_all_button else select_all_button, callback_data="select_all_options" if not remove_all_button else "remove_all_options")
        ],
        [
            InlineKeyboardButton(action_labels["search_books"], callback_data="search_books")
        ]
    ]

    return InlineKeyboardMarkup(buttons)

# Обработка кнопок внутри меню
async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "my_library":
        user_id = query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору")
            return

        # Формируем список книг
        if user.get('library'):
            books_list = "\n".join(
                [f"{idx + 1}. {book['title']}" for idx, book in enumerate(user['library'])]
            )
            library_text = f"📚 Ваши книги\n\nВыберите книгу, чтобы выполнить действия с ней"
            keyboard = [
                [InlineKeyboardButton(book['title'], callback_data=f"book_options_{idx}")]
                for idx, book in enumerate(user['library'])
            ]
            keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")])
        else:
            library_text = "📚 Ваша библиотека пуста. Добавьте книги через поиск!"
            keyboard = [
                [InlineKeyboardButton("📚 Поиск книг", callback_data="search_books")],
                [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(library_text, reply_markup=reply_markup)

    elif query.data.startswith("book_options_"):
        user_id = query.from_user.id
        book_index = int(query.data.split("_")[2])  # Получаем индекс книги
        user = await get_user(user_id)

        if not user or 'library' not in user or book_index >= len(user['library']):
            await query.edit_message_text("⚠️ Книга не найдена или удалена")
            return

        selected_book = user['library'][book_index]
        book_title = selected_book['title']

        # Текст и кнопки для выбранной книги
        options_text = f"📘 Вы выбрали книгу: {book_title}\n\nВыберите действие"
        keyboard = [
            [InlineKeyboardButton("📤 Прислать книгу в чат", callback_data=f"send_book_{book_index}")],
            [InlineKeyboardButton("🗑️ Удалить книгу", callback_data=f"delete_book_{book_index}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="my_library")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(options_text, reply_markup=reply_markup)

    elif query.data.startswith("delete_book_"):
        user_id = query.from_user.id
        book_index = int(query.data.split("_")[2])  # Получаем индекс книги
        user = await get_user(user_id)


        if not user or 'library' not in user or book_index >= len(user['library']):
            await query.edit_message_text("⚠️ Книга не найдена или уже удалена")
            return

        # Удаляем файл книги
        selected_book = user['library'][book_index]
        file_path = selected_book['file_path']
        book_title = selected_book['title']

        try:
            os.remove(file_path)  # Удаляем файл из папки media
        except FileNotFoundError:
            pass  # Если файл уже отсутствует, продолжаем

        # Удаляем книгу из библиотеки пользователя
        user['library'].pop(book_index)

        # Обновляем сообщение о библиотеке
        if user['library']:
            books_list = "\n".join(
                [f"{idx + 1}. {book['title']}" for idx, book in enumerate(user['library'])]
            )
            library_text = f"📚 Ваши книги\n\nВыберите книгу, чтобы выполнить действия с ней"
            keyboard = [
                [InlineKeyboardButton(book['title'], callback_data=f"book_options_{idx}")]
                for idx, book in enumerate(user['library'])
            ]
            keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")])
        else:
            library_text = "📚 Ваша библиотека пуста. Добавьте книги через поиск!"
            keyboard = [
                [InlineKeyboardButton("📚 Поиск книг", callback_data="search_books")],
                [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"🗑️ Книга '{book_title}' была успешно удалена из вашей библиотеки",
            reply_markup=reply_markup
        )

    elif query.data.startswith("send_book_"):
        user_id = query.from_user.id
        book_index = int(query.data.split("_")[2])  # Получаем индекс книги
        user = await get_user(user_id)


        if not user or 'library' not in user or book_index >= len(user['library']):
            await query.edit_message_text("⚠️ Книга не найдена или удалена")
            return

        selected_book = user['library'][book_index]
        file_path = selected_book['file_path']
        book_title = selected_book['title']

        # Отправляем книгу пользователю
        try:
            await query.message.reply_document(document=open(file_path, 'rb'), filename=f"{book_title}.pdf")
        except FileNotFoundError:
            await query.edit_message_text("⚠️ Файл книги не найден. Обратитесь к администратору")
            return

        # Сообщение об успешной отправке
        await query.edit_message_text(
            f"📤 Книга {book_title} успешно отправлена в чат!\n\n",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="my_library")]])
        )

    elif query.data == "menu":
        user_id = query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Возврат в меню
        await handle_menu(update, context)
    
    elif query.data == "subscriptions_menu":
        user_id = query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        # Получаем подписки пользователя
        user_subscriptions = await get_user_subscriptions(user_id)
        if not user_subscriptions:
            # Если подписок нет
            subscription_status = "⚪️ Нет подписки"
            subscription_text = "❌ У вас нет подписки.\n💸 Оформите подписку, чтобы получить доступ к функциям."
        else:
            # Проверяем активные и истекшие подписки
            active_subscription = next(
                (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
                None
            )
            expired_subscription = next(
                (sub for sub in user_subscriptions if sub["end_date"] < datetime.now().date()), 
                None
            )

            if active_subscription:
                # Если есть активная подписка
                subscription_status = "🟢 Подписка активна"
                subscription_text = f"✅ Ваша подписка '{active_subscription['subscription_name']}' активна до {active_subscription['end_date'].strftime('%d.%m.%Y')}."
            elif expired_subscription:
                # Если есть истекшая подписка
                subscription_status = "🔴 Подписка истекла"
                subscription_text = f"❌ Ваша подписка '{expired_subscription['subscription_name']}' истекла {expired_subscription['end_date'].strftime('%d.%m.%Y')}.\n💸 Оформите новую подписку."
            else:
                # Если подписок нет
                subscription_status = "⚪️ Нет подписки"
                subscription_text = "❌ У вас нет подписки.\n💸 Оформите подписку, чтобы получить доступ к функциям."

        # Генерация клавиатуры с обновленным текстом
        subscriptions_keyboard = [
            [InlineKeyboardButton(subscription_status, callback_data="active_subscription")],
            [InlineKeyboardButton("📚 Все подписки", callback_data="subscriptions")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(subscriptions_keyboard)

        # Отображение сообщения с выбором
        await query.edit_message_text(
            f"{subscription_text}\n\n✨ Выберите действие:",
            reply_markup=reply_markup
        )

    elif query.data == "active_subscription":
        # Идентификатор пользователя
        user_id = query.from_user.id

        # Получаем данные пользователя
        user = await get_user(user_id)
        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        # Получаем подписки пользователя из базы данных
        user_subscriptions = await get_user_subscriptions(user_id)

        if not user_subscriptions:
            # Если подписок нет
            message = "⚠️ У вас пока нет подписки."
        else:
            # Ищем активную подписку
            active_subscription = next(
                (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
                None
            )

            if active_subscription:
                # Если подписка активна
                end_date_str = active_subscription["end_date"].strftime('%d.%m.%Y')
                message = (
                    f"🟢 У вас активная подписка: {active_subscription['subscription_name']}\n"
                    f"💰 Цена: {active_subscription['subscription_price']} руб.\n"
                    f"📅 Действует до: {end_date_str}"
                )
            else:
                # Если активной подписки нет (все истекли)
                expired_subscription = max(
                    user_subscriptions, 
                    key=lambda sub: sub["end_date"]
                )  # Берем последнюю истекшую подписку
                message = (
                    f"❌ Ваша подписка '{expired_subscription['subscription_name']}' истекла.\n"
                    f"💰 Цена была: {expired_subscription['subscription_price']} руб.\n"
                    f"📅 Срок действия истек: {expired_subscription['end_date'].strftime('%d.%m.%Y')}\n"
                    "💸 Оформите новую подписку, чтобы продолжить пользоваться сервисом."
                )

        # Кнопка "Назад"
        back_button = [[InlineKeyboardButton("🔙 Назад", callback_data="subscriptions_menu")]]
        reply_markup = InlineKeyboardMarkup(back_button)

        # Отправляем сообщение
        await query.edit_message_text(message, reply_markup=reply_markup)

    elif query.data == "subscriptions":
        user_id = query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверяем, есть ли подписки
        if not subscriptions:
            # Если подписок нет
            no_subscriptions_keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data="subscriptions_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(no_subscriptions_keyboard)
            await query.edit_message_text("⚠️ Подписок пока нет", reply_markup=reply_markup)
        else:
            # Генерация клавиатуры с подписками
            subscriptions_keyboard = [
                [InlineKeyboardButton(sub["name"], callback_data=f"view_{sub['name']}")] for sub in subscriptions
            ]
            # Добавляем кнопку "Обратно"
            subscriptions_keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="subscriptions_menu")])
            reply_markup = InlineKeyboardMarkup(subscriptions_keyboard)
            await query.edit_message_text("✨ Выберите подписку", reply_markup=reply_markup)
    
    elif query.data.startswith("view_"):
        user_id = query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Просмотр информации о выбранной подписке
        subscription_name = query.data[5:]  # Получаем название подписки
        
        # Поиск подписки в списке
        selected_subscription = next(
            (sub for sub in subscriptions if sub["name"] == subscription_name), None
        )
        
        if selected_subscription:
            price = selected_subscription["price"]
            duration_days = 30  # Срок подписки в днях
            end_date = datetime.now() + timedelta(days=duration_days)
            # Формирование красивого сообщения
            message = (
                f"📝 Оформление Подписки\n\n"
                f"✨ Подписка: {subscription_name}\n"
                f"⏳ Срок действия: {duration_days} дней\n"
                f"💰 Цена: {price} руб.\n"
                f"📅 Закончится: {end_date.strftime('%d.%m.%Y')}\n\n"
                "🔑 Оформите подписку, чтобы получить доступ ко всем возможностям!"
            )
            
            # Клавиатура
            keyboard = [
                [InlineKeyboardButton("💸 Оформить подписку", callback_data=f"buy_{subscription_name}")],
                [InlineKeyboardButton("🔙 Назад к подпискам", callback_data="subscriptions")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Отправляем сообщение
            await query.edit_message_text(message, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.edit_message_text("❌ Подписка не найдена.")
    
    # Покупка подписки
    elif query.data.startswith("buy_"):
        user_id = query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Извлекаем название подписки
        subscription_name = query.data.replace("buy_", "")
        
        # Проверяем, есть ли активная подписка
        user_subscriptions = await get_user_subscriptions(user_id)
        # Проверяем активные и истекшие подписки
        active_subscription = next(
            (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
            None
        )
        if active_subscription:
            # Если есть активная подписка, выводим сообщение и выходим
            await query.edit_message_text(
                f"⚠️ У вас уже есть активная подписка: {active_subscription['subscription_name']}.\n"
                f"📅 Действующая до {active_subscription['end_date'].strftime('%d.%m.%Y')}.\n\n"
                "Вы не можете купить новую подписку, пока не истечёт текущая.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
                ])
            )
            return
        else:
            # Удаляем подписку, если она истекла
            expired_subscription = next(
                (sub for sub in user_subscriptions if sub["end_date"] < datetime.now().date()), 
                None
            )
            if expired_subscription:
                # Удаляем подписку из списка
                await delete_subscription(user_id)
                print('удаляем истекшую подписку чтоб добавить новую')

        # Поиск подписки в списке
        selected_subscription = next(
            (sub for sub in subscriptions if sub["name"] == subscription_name), None
        )
        
        if selected_subscription:
            subscription_price = selected_subscription["price"]

            # Генерация ссылки на оплату через Юкассу
            payment = Payment.create({
                "amount": {
                    "value": f"{subscription_price:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://t.me/FastPage_Bot"  # Укажите реальный URL возврата
                },
                "capture": True,
                "description": f"Оплата подписки: {subscription_name}"
            }, uuid.uuid4())

            # Ссылка на оплату
            payment_url = payment.confirmation.confirmation_url

            await query.edit_message_text(
                f"💡 **Для активации подписки '{subscription_name}' выполните следующие шаги:**\n\n"
                f"1️⃣ Нажмите на кнопку 💳 **Оплатить** ниже и перейдите на сайт оплаты.\n"
                f"3️⃣ После успешной оплаты подписка будет активна!\n\n"
                f"⏳ *Ожидается подтверждение оплаты...*\n"
                f"Если вы передумали, **🔙 Назад в меню**.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Оплатить", url=payment_url)],  # Кнопка для перехода на оплату
                    [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]  # Кнопка возврата в меню
                ])
            )
            # Асинхронная проверка статуса платежа
            payment_id = payment.id
            asyncio.create_task(check_payment_status(payment_id, user_id, subscription_name, subscription_price, query))
    
        else:
            await query.edit_message_text("Подписка не найдена")

    elif query.data == "admin_panel":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору")
            return
        # Админ панель
        # Проверка на админа
        user_id = update.callback_query.from_user.id  # Получаем ID пользователя
        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели")
            return
        admin_keyboard = [
            [InlineKeyboardButton("👥 Управление пользователями", callback_data="users_admin")],
            [InlineKeyboardButton("💳 Управление подписками", callback_data="manage_subscriptions")],
            [InlineKeyboardButton("🔔 Уведомления", callback_data="notifications")],
            [InlineKeyboardButton("📈 Cтатистика ", callback_data="statistic")],
            [InlineKeyboardButton("⚙️ Режимы", callback_data="modes_admin")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_keyboard)
        await query.edit_message_text("Админ панель", reply_markup=reply_markup)

    elif query.data == "statistic":
        user_id = update.callback_query.from_user.id
        context.user_data['current_mode'] = None
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору")
            return

        # Проверка на админа
        if user_id not in ADMINS:
            await query.edit_message_text("У вас нет прав для доступа к админ панели")
            return

        # Клавиатура для управления пользователями
        admin_user_management_keyboard = [
            [InlineKeyboardButton("👥 Пользователей всего", callback_data="all_users")],
            [InlineKeyboardButton("🔑 Пользователи с подписками", callback_data="subscribed_users")],
            [InlineKeyboardButton("🚫 Пользователи без подписками", callback_data="unsubscribed_users")],
            [InlineKeyboardButton("🤖 Сколько раз использовали: Чат с ИИ", callback_data="static_chat_ai")],
            [InlineKeyboardButton("📚 Сколько раз использовали: Поиск книг", callback_data="static_search_book")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        await query.edit_message_text("Выберите действие", reply_markup=reply_markup)

    elif query.data == "static_search_book":
        # Получаем количество раз, когда использовался поиск книг
       
        # Формируем сообщение с количеством использований поиска книг
        text = f"📚 Поиск книг использовался {count_search_book} раз(а)."

        # Клавиатура с кнопкой "Назад"
        admin_user_management_keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="statistic")]
        ]
        
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        
        # Отправляем сообщение
        await query.edit_message_text(text, reply_markup=reply_markup)

    elif query.data == "static_chat_ai":
        # Получаем количество раз, когда использовался чат с ИИ
        
        # Формируем сообщение с количеством использований чата с ИИ
        text = f"🤖 Чат с ИИ использовался {count_chat_ai} раз(а)."

        # Клавиатура с кнопкой "Назад"
        admin_user_management_keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="statistic")]
        ]
        
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        
        # Отправляем сообщение
        await query.edit_message_text(text, reply_markup=reply_markup)

    elif query.data == "all_users":
        # Получаем общее количество пользователей
        total_users = len(await get_all_users())

        # Формируем сообщение и клавиатуру с кнопками
        text = f"👥 Всего пользователей в боте: {total_users}"
        
        admin_user_management_keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="statistic")]
        ]
        
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        
        # Отправляем сообщение с количеством пользователей и клавиатурой
        await query.edit_message_text(text, reply_markup=reply_markup)

    elif query.data == "subscribed_users":
        # Получаем общее количество пользователей с подписками
        total_subscribed_users = len(await get_users_with_active_subscriptions())

        # Формируем сообщение и клавиатуру с кнопками
        text = f"🔑 Пользователи с подписками: {total_subscribed_users}"
        
        # Клавиатура с кнопкой "Назад"
        admin_user_management_keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="statistic")]
        ]
        
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        
        # Отправляем сообщение с количеством пользователей и клавиатурой
        await query.edit_message_text(text, reply_markup=reply_markup)

    elif query.data == "unsubscribed_users":
        # Находим пользователей без подписки (вычитаем из списка всех пользователей тех, кто есть в user_subscriptions)
        unsubscribed_users = await get_users_without_subscriptions()

        # Получаем общее количество пользователей без подписок
        total_unsubscribed_users = len(unsubscribed_users)

        # Формируем сообщение и клавиатуру с кнопками
        text = f"🚫 Пользователи без подписок: {total_unsubscribed_users}"

        # Клавиатура с кнопкой "Назад"
        admin_user_management_keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="statistic")]
        ]
        
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        
        # Отправляем сообщение с количеством пользователей и клавиатурой
        await query.edit_message_text(text, reply_markup=reply_markup)

    elif query.data == "users_admin":
        user_id = update.callback_query.from_user.id
        context.user_data['current_mode'] = None
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору")
            return

        # Проверка на админа
        if user_id not in ADMINS:
            await query.edit_message_text("У вас нет прав для доступа к админ панели")
            return

        # Клавиатура для управления пользователями
        admin_user_management_keyboard = [
            [InlineKeyboardButton("🔍 Найти пользователя", callback_data="search_user")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_user_management_keyboard)
        await query.edit_message_text("Выберите действие с пользователями", reply_markup=reply_markup)

    elif query.data == "search_user":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору")
            return
        # Управление подписками (для админа)
        # Проверка на админа

        if user_id not in ADMINS:
            await query.edit_message_text("У вас нет прав для доступа к админ панели")
            return
        admin_subscriptions_keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="users_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)
        context.user_data['current_mode'] = 'search_user'
        await query.edit_message_text(
            "🔍 Пожалуйста, укажите **user_id** или **username** пользователя, которого хотите найти.\n"
            "Пример: \n"
            "- Для поиска по **user_id**: просто введите его число.\n"
            "- Для поиска по **username**: введите имя_пользователя.\n"
            "🔎 Мы постараемся найти этого пользователя для вас.",
            reply_markup=reply_markup
        )

    elif query.data == "notifications":
        user_id = update.callback_query.from_user.id
        context.user_data['current_mode'] = None
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору")
            return
        # Управление подписками (для админа)
        # Проверка на админа

        if user_id not in ADMINS:
            await query.edit_message_text("У вас нет прав для доступа к админ панели")
            return
        admin_subscriptions_keyboard = [
            [InlineKeyboardButton("📢 Для всех", callback_data="notify_all")],
            [InlineKeyboardButton("📢 Для тех кто подписан", callback_data="notify_subscribed")],
            [InlineKeyboardButton("📢 для не подписан", callback_data="notify_unsubscribed")],
            [InlineKeyboardButton("📢 для отдельного пользователя", callback_data="notify_single_user")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)
        await query.edit_message_text("Выберите аудиторию для уведомления", reply_markup=reply_markup)

    elif query.data == "notify_single_user":
        user_id = update.callback_query.from_user.id

        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        # Проверка на админа
        if user_id not in ADMINS:
            await query.edit_message_text("У вас нет прав для отправки уведомлений.")
            return

        # Переход в режим отправки уведомления для конкретного пользователя
        context.user_data['current_mode'] = 'notify_single_user'
        
        # Запросить ID пользователя для отправки уведомления
        await query.edit_message_text("🔍 Пожалуйста, введите ID пользователя, которому хотите отправить уведомление:")
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="notifications")]  # Добавляем кнопку "Назад"
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("🔍 Пожалуйста, введите ID пользователя, которому хотите отправить уведомление:", reply_markup=reply_markup)
        # Переход в async функцию для обработки уведомления
        return

    elif query.data.startswith("notify_"):
        user_id = update.callback_query.from_user.id
        
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        # Проверка, что пользователь администратор
        if user_id not in ADMINS:
            await query.edit_message_text("У вас нет прав для отправки уведомлений.")
            return

        # Определяем аудиторию из callback_data
        target_group = query.data.split("_")[1]  # "all", "subscribed" или "unsubscribed"

        # Проверяем корректность данных
        if target_group not in ["all", "subscribed", "unsubscribed"]:
            await query.edit_message_text("⚠️ Неверный выбор аудитории. Попробуйте снова.")
            return

        # Сохраняем выбор аудитории в user_data
        context.user_data['target_group'] = target_group
        context.user_data['current_mode'] = 'process_notification'

        # Отправляем инструкцию для создания уведомления
        instructions = (
            "✏️ Напишите текст уведомления, который будет отправлен вашим пользователям.\n\n"
            "Вы можете добавить кнопки в уведомление. Для этого используйте следующий формат:\n"
            "`Текст кнопки|Ссылка`\n\n"
            "Пример:\n"
            "🎉 Новое обновление! 🎉\n"
            "Подробнее|https://example.com\n\n"
            "🌟 Для того, чтобы ваше уведомление было красивым и привлекательным, не забудьте добавить смайлики! 🌈😊\n"
            "Они помогут сделать ваше сообщение более ярким и выразительным. Например, используйте смайлики для подчеркивания важной информации или для создания нужной атмосферы.\n\n"
            "Если хотите добавить дополнительные кнопки, укажите их по аналогии с примером выше.\n\n"
            "📌 Не забывайте, что кнопки могут вести на страницы, ссылки или команды бота.\n\n"
            "🔙 Для отмены вернитесь назад, выбрав кнопку ниже."
        )
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="notifications")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(instructions, reply_markup=reply_markup, parse_mode="Markdown")

    elif query.data == "modes_admin":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Управление подписками (для админа)
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        admin_subscriptions_keyboard = [
            [InlineKeyboardButton("📚 Поиск книг", callback_data="search_books_admin")],
            [InlineKeyboardButton("🤖 Чат с ИИ", callback_data="chat_with_ai_admin")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)
        await query.edit_message_text("Выберите действие:", reply_markup=reply_markup)

    elif query.data == "search_books_admin":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Управление подписками (для админа)
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        admin_subscriptions_keyboard = [
            [InlineKeyboardButton("✏️ Ограничение на макс. кол-во стрн. (без подписки)", callback_data="limit_page_book")],
            [InlineKeyboardButton("✏️ Лимит книг в день (без подписки):", callback_data="Limit_books_day")],
            [InlineKeyboardButton("✏️ Лимит книг в день (с подпиской)", callback_data="Limit_books_day_subscribe")],
            [InlineKeyboardButton("🔒 Проверка подписки: Вкл/Выкл", callback_data="off_on_subscription_search_books")],
            [InlineKeyboardButton("📜 Информация о режиме", callback_data="info_search_books")],
            [InlineKeyboardButton("🔙 Назад", callback_data="modes_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)
        await query.edit_message_text("Выберите действие:", reply_markup=reply_markup)

    elif query.data == "off_on_subscription_search_books":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return

        # Работа с глобальной переменной
        global subscription_search_book_is_true
        if subscription_search_book_is_true:
            subscription_search_book_is_true = False
            status_text = "❌ Проверка подписки выключена."
        else:
            subscription_search_book_is_true = True
            status_text = "✅ Проверка подписки включена."

        # Кнопки меню
        menu_buttons = [
            [InlineKeyboardButton("🔙 Назад", callback_data="search_books_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(menu_buttons)

        # Обновление текста с меню
        await query.edit_message_text(
            text=status_text,
            reply_markup=reply_markup
        )
    
    elif query.data == "limit_page_book":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        context.user_data['current_mode'] = 'limit_page_book'
        await query.edit_message_text("Укажите макс. кол-во стрн. (без подписки)")

    elif query.data == "Limit_books_day_subscribe":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        context.user_data['current_mode'] = 'Limit_books_day_subscribe'
        await query.edit_message_text("Укажите лимит книг в день")
    
    elif query.data == "Limit_books_day":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        context.user_data['current_mode'] = 'Limit_books_day'
        await query.edit_message_text("Укажите лимит книг в день")
    
    elif query.data == "info_search_books":
        # Проверка на админа
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return

        # Формирование информации
        subscription_status = "✅ Включена" if subscription_search_book_is_true else "❌ Выключена"
        info_text = (
            "ℹ️ <b>Информация о режиме \"Поиск книг\"</b>\n\n"
            f"💬 <b>Проверка подписки:</b> {subscription_status}\n"
            f"💬 <b>Лимит книг в день (без подписки):</b> {count_limit_book_day}\n"
            f"💬 <b>Лимит книг в день (с подпиской):</b> {count_limit_book_in_subscribe_day}\n"
            f"💬 <b>Ограничение на макс. кол-во стрн. (без подписки):</b> {limit_page_book}\n"
        )

        # Кнопка назад
        back_button = InlineKeyboardButton("🔙 Назад", callback_data="search_books_admin")
        reply_markup = InlineKeyboardMarkup([[back_button]])

        # Отправка сообщения
        await query.edit_message_text(
            text=info_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    elif query.data == "chat_with_ai_admin":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        admin_subscriptions_keyboard = [
            [InlineKeyboardButton("⏳ Изменить лимит часов", callback_data="edit_hour_in_chat_with_ai")],
            [InlineKeyboardButton("✏️ Лимит сообщений (без подписки)", callback_data="edit_count_in_chat_with_ai")],
            [InlineKeyboardButton("🔒 Проверка подписки: Вкл/Выкл", callback_data="off_on_subscription_verification_chat_with")],
            [InlineKeyboardButton("📜 Информация о режиме", callback_data="Info_chat_with_ai")],
            [InlineKeyboardButton("🔙 Назад", callback_data="modes_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)
        await query.edit_message_text("Выберите действие:", reply_markup=reply_markup)

    elif query.data == "off_on_subscription_verification_chat_with":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return

        # Работа с глобальной переменной
        global subscription_chat_with_ai_is_true
        if subscription_chat_with_ai_is_true:
            subscription_chat_with_ai_is_true = False
            status_text = "❌ Проверка подписки выключена."
        else:
            subscription_chat_with_ai_is_true = True
            status_text = "✅ Проверка подписки включена."

        # Кнопки меню
        menu_buttons = [
            [InlineKeyboardButton("🔙 Назад", callback_data="chat_with_ai_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(menu_buttons)

        # Обновление текста с меню
        await query.edit_message_text(
            text=status_text,
            reply_markup=reply_markup
        )
    
    elif query.data == "Info_chat_with_ai":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return

        # Формирование информации
        subscription_status = "✅ Включена" if subscription_chat_with_ai_is_true else "❌ Выключена"
        info_text = (
            "ℹ️ <b>Информация о режиме \"Чат с ИИ\"</b>\n\n"
            f"📜 <b>Проверка подписки:</b> {subscription_status}\n"
            f"💬 <b>Лимит сообщений без подписки:</b> {count_limit_chat_with_ai}\n"
            f"⏳ <b>Время ожидания после исчерпания лимита:</b> {wait_hour} часов\n"
        )

        # Кнопка назад
        back_button = InlineKeyboardButton("🔙 Назад", callback_data="chat_with_ai_admin")
        reply_markup = InlineKeyboardMarkup([[back_button]])

        # Отправка сообщения
        await query.edit_message_text(
            text=info_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    elif query.data == "edit_count_in_chat_with_ai":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        context.user_data['current_mode'] = 'edit_count_in_chat_with_ai'
        await query.edit_message_text("Укажите лимит сообщений (без подписки)")

    elif query.data == "edit_hour_in_chat_with_ai":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        context.user_data['current_mode'] = 'edit_hour_in_chat_with_ai'
        await query.edit_message_text("Укажите кол-во часов для лимита")

    elif query.data == "manage_subscriptions":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Управление подписками (для админа)
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        admin_subscriptions_keyboard = [
            [InlineKeyboardButton("➕ Добавить подписку", callback_data="add_subscription")],
            [InlineKeyboardButton("❌ Удалить подписку", callback_data="remove_subscription")],
            [InlineKeyboardButton("🎁 Подарить подписку", callback_data="gift_subscription")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)
        await query.edit_message_text("Выберите действие:", reply_markup=reply_markup)

    elif query.data == "gift_subscription":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return

        # Если нет доступных подписок для подарка
        if not subscriptions:
            keyboard = [
                [InlineKeyboardButton("🔙 Отмена", callback_data="manage_subscriptions")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Нет доступных подписок для подарка. 😞", reply_markup=reply_markup)
            return
        
        # Список подписок для подарка
        gift_subscription_keyboard = [
            [InlineKeyboardButton(sub['name'], callback_data=f"gift_{sub['name']}") for sub in subscriptions]
        ]
        gift_subscription_keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data="manage_subscriptions")])
        reply_markup = InlineKeyboardMarkup(gift_subscription_keyboard)
        await query.edit_message_text("Выберите подписку, которую хотите подарить:", reply_markup=reply_markup)
    
    elif query.data.startswith("gift_"):
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        # Подарить подписку
        selected_subscription = query.data.replace("gift_", "")
        context.user_data['current_mode'] = 'gift_subscription'
        context.user_data['selected_subscription'] = selected_subscription  # Сохраняем выбранную подписку
        await query.edit_message_text("Введите ID пользователя, которому хотите подарить подписку:")

    elif query.data == "add_subscription":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        # Добавление подписки
        context.user_data['current_mode'] = 'add_subscription'
        await query.edit_message_text("Введите название подписки 📛✨.\nУкажите его с подходящим смайликом, который будет характеризовать эту подписку! 🌟\nПример: 📚 Подписка_книги")

    # Обработчик для удаления подписок
    elif query.data == "remove_subscription":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        # Проверка на админа

        if user_id not in ADMINS:
            await query.answer()  # Отвечаем на запрос, чтобы пользователь не ждал
            await query.edit_message_text("У вас нет прав для доступа к админ панели.")
            return
        
        # Если нет подписок для удаления
        if not subscriptions:
            keyboard = [
                [InlineKeyboardButton("🔙 Отмена", callback_data="manage_subscriptions")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Нет доступных подписок для удаления.", reply_markup=reply_markup)
            return
        
        # Генерация кнопок для подписок
        keyboard = [
            [InlineKeyboardButton(sub['name'], callback_data=f"delete_{sub['name']}") for sub in subscriptions]  # Используем 'name' из словаря
        ]
        keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data="manage_subscriptions")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправка сообщения с кнопками
        await query.edit_message_text("Выберите подписку для удаления:", reply_markup=reply_markup)
    
    # Обработчик для удаления конкретной подписки
    elif query.data.startswith("delete_"):
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        subscription_name = query.data.replace("delete_", "")
        
        # Поиск подписки по имени в списке
        subscription = next((sub for sub in subscriptions if sub['name'] == subscription_name), None)
        
        if subscription:
            subscriptions.remove(subscription)  # Удаляем подписку из списка

            # Создаем кнопку "Назад"
            back_button = InlineKeyboardButton("🔙 Назад", callback_data="manage_subscriptions")

            if subscriptions:
                # Если остались подписки, обновляем список кнопок для удаления
                keyboard = [
                    [InlineKeyboardButton(sub['name'], callback_data=f"delete_{sub['name']}") for sub in subscriptions]
                ]
                keyboard.append([back_button])  # Добавляем кнопку "Назад"
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Отправляем сообщение о том, что подписка удалена и показываем оставшиеся
                await query.edit_message_text(
                    f"Подписка '{subscription_name}' была удалена.\nВыберите следующую для удаления:",
                    reply_markup=reply_markup,
                )
            else:
                # Если подписки больше нет
                await query.edit_message_text(
                    f"Подписка '{subscription_name}' была удалена.\nВсе подписки удалены.",
                    reply_markup=InlineKeyboardMarkup([[back_button]])  # Только кнопка "Назад"
                )
        else:
            # Если подписка не найдена
            await query.edit_message_text(f"Подписка '{subscription_name}' не найдена.")
    
    # Обработка кнопки "Игры"
    elif query.data == "game":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:####################################################################################################################################
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        game_text = (
            "🎮 Раздел 'Игры' скоро будет доступен! 🔜\n"
            "Следите за обновлениями, чтобы первыми узнать о новых играх. 🚀"
        )
        # Клавиатура с кнопкой "Назад в меню"
        game_keyboard = [
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(game_keyboard)

        # Отправка сообщения с кнопкой
        await query.edit_message_text(game_text, reply_markup=reply_markup)

    elif query.data == "search_books":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        # Проверяем, есть ли активная подписка
        user_subscriptions = await get_user_subscriptions(user_id)
        # Проверяем активные и истекшие подписки
        active_subscription = next(
            (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
            None
        )
        print(user_subscriptions)
        if subscription_search_book_is_true:
            # Сообщение об ограничениях для пользователей без подписки
            if not active_subscription:
                await query.edit_message_text(
                    "🔒 У вас нет активной подписки, поэтому функции поиска книг будут ограничены:\n\n"
                    f"1️⃣ Максимальное количество страниц: от 5 до {limit_page_book}.\n"
                    f"2️⃣ Кол-во книг в день ограничено до {count_limit_book_day}\n"
                    "🌐 Выберите язык книги, чтобы продолжить поиск:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🇷🇺 Русский", callback_data="language_russian")],
                        [InlineKeyboardButton("🇬🇧 Английский", callback_data="language_english")],
                        [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
                    ])
                )
                return

        # Клавиатура для выбора языка
        keyboard = [
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="language_russian")],
            [InlineKeyboardButton("🇬🇧 Английский", callback_data="language_english")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "🌐 Выберите язык книги:",
            reply_markup=reply_markup
        )

    elif query.data in ["language_russian", "language_english"]:
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        # Определяем язык
        if query.data == "language_russian":
            context.user_data['book_language'] = "russian"
            prompt_text = "✅ Вы выбрали язык - 🇷🇺 Русский.\nТеперь выберите опции:"
        else:
            context.user_data['book_language'] = "english"
            prompt_text = "✅ You have selected the language - 🇬🇧 English.\nNow select the options:"

        # Инициализация состояния опций
        context.user_data['options'] = {
            "option_1": False,
            "option_2": False,
            "option_3": False,
            "option_4": False,
        }

        # Отправляем сообщение с кнопками опций
        await query.edit_message_text(
            prompt_text,
            reply_markup=await generate_options_menu(context.user_data['options'], context)
        )

    elif query.data == "toggle_option_option_1":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        # Изменяем состояние опции 1
        current_state = context.user_data['options']['option_1']
        context.user_data['options']['option_1'] = not current_state

        if context.user_data.get('book_language') == 'russian':
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Теперь выберите опции:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
        else:
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Now select options:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )

    elif query.data == "toggle_option_option_2":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        # Изменяем состояние опции 2
        current_state = context.user_data['options']['option_2']
        context.user_data['options']['option_2'] = not current_state

        if context.user_data.get('book_language') == 'russian':
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Теперь выберите опции:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
        else:
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Now select options:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )

    elif query.data == "toggle_option_option_3":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        # Изменяем состояние опции 3
        current_state = context.user_data['options']['option_3']
        context.user_data['options']['option_3'] = not current_state

        if context.user_data.get('book_language') == 'russian':
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Теперь выберите опции:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
        else:
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Now select options:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
    
    elif query.data == "toggle_option_option_4":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        # Изменяем состояние опции 4
        current_state = context.user_data['options']['option_4']
        context.user_data['options']['option_4'] = not current_state

        if context.user_data.get('book_language') == 'russian':
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Теперь выберите опции:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
        else:
            # Обновляем сообщение с кнопками
            await query.edit_message_text(
                "✏️ Now select options:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )

    elif query.data == "skip_options":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        # Если все опции не выбраны, пропускаем
        if context.user_data.get('book_language') == 'russian':
            if all(not option for option in context.user_data['options'].values()):
                context.user_data['current_mode'] = "search_books"
                await query.edit_message_text("✏️ Какую книгу вы хотите разобрать?\nНапишите название")
            else:
                # Если хоть одна опция выбрана, показываем кнопку "Далее"
                context.user_data['current_mode'] = "search_books"
                await query.edit_message_text("✏️ Какую книгу вы хотите разобрать?\nНапишите название")
        else:
            if all(not option for option in context.user_data['options'].values()):
                context.user_data['current_mode'] = "search_books"
                await query.edit_message_text("✏️ Which book do you want to review?\nWrite the name")
            else:
                # Если хоть одна опция выбрана, показываем кнопку "Далее"
                context.user_data['current_mode'] = "search_books"
                await query.edit_message_text("✏️ Which book do you want to review?\nWrite the name")

    elif query.data == "select_all_options":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        context.user_data['options'] = {key: True for key in context.user_data['options']}
        if context.user_data.get('book_language') == 'russian':
            await query.edit_message_text(
                "✅ Все опции выбраны. Вы можете убрать все:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
        else:
            await query.edit_message_text(
                "✅ All options are selected. You can remove everything:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )

    elif query.data == "remove_all_options":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return
        
        context.user_data['options'] = {key: False for key in context.user_data['options']}
        if context.user_data.get('book_language') == 'russian':
            await query.edit_message_text(
                "✅ Все опции убраны. Выберите снова:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )
        else:
            await query.edit_message_text(
                "✅ All options have been removed. Select again:",
                reply_markup=await generate_options_menu(context.user_data['options'], context)
            )

    elif query.data == "chat_with_ai":
        user_id = update.callback_query.from_user.id
        # Ищем пользователя
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
            return

        # Проверяем наличие подписки у пользователя
        user_subscriptions = await get_user_subscriptions(user_id)
        active_subscription = next(
            (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
            None
        )
        # Инициализируем поле count_words, если его еще нет
        if 'count_words' not in user:
            user['count_words'] = 0

        if not active_subscription:
            # Для пользователей без подписки
            sms_limit = user['count_words']  # Количество использованных сообщений
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
            ])
            message = (
                f"📉 **У вас нет активной подписки**, и доступ к чату с ИИ ограничен.\n\n"
                f"📱 Ваш текущий лимит на отправку сообщений: {sms_limit}/{count_limit_chat_with_ai}.\n\n"
                f"💬 Вы можете продолжать использовать чат, пока не превысите лимит сообщений.\n"
                f"Как только лимит будет исчерпан, доступ к Чату с ИИ будет ограничен, и начнётся отсчёт времени до снятия лимита.\n\n"
                f"💡 Чтобы получить полный доступ и не ограничиваться лимитом, оформите подписку!\n💬 Задавайте ваши вопросы! Я всегда готов помочь вам. 😊"
            )
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
            context.user_data['current_mode'] = "chat_with_ai"
        else:
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
            ])

            await update.callback_query.message.reply_text(
                "💬 Задавайте ваши вопросы! Я всегда готов вам помочь. 😊",
                reply_markup=reply_markup
            )
            context.user_data['current_mode'] = "chat_with_ai"

# Функция добавления подписки
async def add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Если имя подписки ещё не задано
    if not context.user_data.get('subscription_name'):
        context.user_data['subscription_name'] = text

        # Проверка на существующую подписку с таким же названием
        if any(sub["name"] == text for sub in subscriptions):
            context.user_data['subscription_name'] = None
            await update.message.reply_text(
                f"Подписка с именем '{text}' уже существует. Пожалуйста, введите другое название."
            )
            return
        else:
            await update.message.reply_text("Введите цену подписки в месяц (больше 0):")
            context.user_data['action'] = 'set_price'

    # Обработка введённой цены
    elif context.user_data.get('action') == 'set_price':
        try:
            price = int(text)
            if price <= 0:
                raise ValueError("Цена должна быть больше 0.")
            
            subscription_name = context.user_data.get('subscription_name')

            # Добавляем подписку в список
            subscriptions.append({
                "name": subscription_name,
                "price": price
            })

            await update.message.reply_text(
                f"Подписка '{subscription_name}' добавлена с ценой {price} руб."
            )
            await handle_menu(update, context)
            # Сброс состояния пользователя
            context.user_data['current_mode'] = None
            context.user_data['subscription_name'] = None
            context.user_data['action'] = None
        except ValueError:
            await update.message.reply_text("Введите корректную цену подписки (число больше 0).")

# Функция для обработки ввода ID и подарка подписки
async def gift_subscription(update, context):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['selected_subscription'] = None
        return
    entered_id = update.message.text.strip()  # Ввод пользователя, предполагается ID пользователя

    if context.user_data.get('current_mode') != 'gift_subscription':
        return  # Выход из функции, если не в режиме подарка подписки

    try:
        # Преобразуем ID в целое число
        recipient_id = int(entered_id)

        # Проверяем, существует ли пользователь с таким ID в списке users
        recipient = await get_user(recipient_id)

        if not recipient:
            await update.message.reply_text(f"Пользователь с ID {entered_id} не найден.")
            return

        # Запрашиваем количество дней подписки
        await update.message.reply_text("Введите количество дней подписки:")

        # Сохраняем ID получателя в пользовательских данных для дальнейшего использования
        context.user_data['recipient_id'] = recipient_id
        context.user_data['current_mode'] = 'set_subscription_days'

    except ValueError:
        await update.message.reply_text("Введите корректный ID пользователя.")

# Функция для обработки ввода количества дней подписки
async def set_subscription_days(update, context):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['recipient_id'] = None
        context.user_data['selected_subscription'] = None
        return
    entered_days = update.message.text.strip()

    if context.user_data.get('current_mode') != 'set_subscription_days':
        return  # Выход из функции, если не в режиме установки дней подписки

    try:
        # Проверяем, что введенное количество дней — это целое число
        days = int(entered_days)

        if days <= 0:
            await update.message.reply_text("Количество дней должно быть больше нуля.")
            return

        recipient_id = context.user_data['recipient_id']
        selected_subscription = context.user_data['selected_subscription']

        # Проверяем, есть ли активная подписка
        user_subscriptions = await get_user_subscriptions(recipient_id)
        # Проверяем активные и истекшие подписки
        active_subscription = next(
            (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
            None
        )
        if active_subscription:
            await update.message.reply_text(
                f"⚠️ У Пользователя уже есть активная подписка: {active_subscription['subscription_name']}.\n"
                f"📅 Действующая до {active_subscription['end_date'].strftime('%d.%m.%Y')}.\n\n"
                "Вы не можете подарить новую подписку, пока не истечёт текущая.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
                ])
            )
            # Сброс текущего состояния
            context.user_data['current_mode'] = None
            context.user_data['recipient_id'] = None
            context.user_data['selected_subscription'] = None
            return
        # Рассчитываем дату окончания подписки
        end_date = datetime.now() + timedelta(days=days)

        await add_subscription_db(recipient_id, selected_subscription, 0.0, end_date)

        # Оповещаем админа о подарке
        await update.message.reply_text(f"Подписка '{selected_subscription}' подарена пользователю с ID {recipient_id}. Подписка активна до {end_date.strftime('%d.%m.%Y')}.")
        await handle_menu(update, context)
        # Сброс текущего состояния
        context.user_data['current_mode'] = None
        context.user_data['recipient_id'] = None
        context.user_data['selected_subscription'] = None

    except ValueError:
        await update.message.reply_text("Введите корректное количество дней для подписки.")

# Функция для изменения кол-во часов для лимита
async def edit_hour_in_chat_with_ai(update, context):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    text = update.message.text.strip()
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['selected_subscription'] = None
        return

    if context.user_data.get('current_mode') != 'edit_hour_in_chat_with_ai':
        return  # Выход из функции, если не в режиме изменения кол-во часов для лимита

    if not text:
        await update.message.reply_text(f"Число не найдено")
        return
    try:
        # Преобразуем ID в целое число
        number = int(text)
        
        if number < 1:
            await update.message.reply_text(f"Введите число больше 0")
            return
        global wait_hour
        wait_hour = number
        await update.message.reply_text(f"Кол-во часов для лимита изменено на {wait_hour}")
        await handle_menu(update, context)
        context.user_data['current_mode'] = None
    except ValueError:
        await update.message.reply_text("Введите коректное число больше 0")

# Функция для лимита сообщений (без подписки)
async def edit_count_in_chat_with_ai(update, context):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    text = update.message.text.strip()
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['selected_subscription'] = None
        return

    if context.user_data.get('current_mode') != 'edit_count_in_chat_with_ai':
        return  # Выход из функции, если не в режиме изменения кол-во часов для лимита

    if not text:
        await update.message.reply_text(f"Число не найдено")
        return
    try:
        # Преобразуем ID в целое число
        number = int(text)
        
        if number < 1:
            await update.message.reply_text(f"Введите число больше 0")
            return

        global count_limit_chat_with_ai
        count_limit_chat_with_ai = number
        await update.message.reply_text(f"Лимит сообщений изменён на {count_limit_chat_with_ai}")
        await handle_menu(update, context)
        context.user_data['current_mode'] = None
    except ValueError:
        await update.message.reply_text("Введите коректное число больше 0")

# Функция для лимита книг в день
async def Limit_books_day(update, context):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    text = update.message.text.strip()
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['selected_subscription'] = None
        return

    if context.user_data.get('current_mode') != 'Limit_books_day':
        return  # Выход из функции, если не в режиме изменения кол-во книг для лимита

    if not text:
        await update.message.reply_text(f"Число не найдено")
        return
    try:
        # Преобразуем ID в целое число
        number = int(text)
        
        if number < 1:
            await update.message.reply_text(f"Введите число больше 0")
            return

        global count_limit_book_day
        count_limit_book_day = number
        await update.message.reply_text(f"Лимит на кол-во книг в день изменён на {count_limit_book_day}")
        await handle_menu(update, context)
        context.user_data['current_mode'] = None
    except ValueError:
        await update.message.reply_text("Введите коректное число больше 0")

# Функция для лимита книг в день
async def Limit_books_day_subscribe(update, context):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    text = update.message.text.strip()
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['selected_subscription'] = None
        return

    if context.user_data.get('current_mode') != 'Limit_books_day_subscribe':
        return  # Выход из функции, если не в режиме изменения кол-во книг для лимита

    if not text:
        await update.message.reply_text(f"Число не найдено")
        return
    try:
        # Преобразуем ID в целое число
        number = int(text)
        
        if number < 1:
            await update.message.reply_text(f"Введите число больше 0")
            return

        global count_limit_book_in_subscribe_day
        count_limit_book_in_subscribe_day = number
        await update.message.reply_text(f"Лимит на кол-во книг в день изменён на {count_limit_book_in_subscribe_day}")
        await handle_menu(update, context)
        context.user_data['current_mode'] = None
    except ValueError:
        await update.message.reply_text("Введите коректное число больше 0")

async def send_notification_to_users(update: Update, context: ContextTypes.DEFAULT_TYPE, notification_text, reply_markup, target_group):
    user_id = update.message.from_user.id

    # Получаем данные о пользователе
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Проверка, что пользователь администратор
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для отправки уведомлений.")
        return

    # Фильтруем пользователей по указанной аудитории
    if target_group == "all":
        # Получаем всех пользователей из базы данных
        users = await get_all_users()
        target_users = users
    elif target_group == "subscribed":
        # Получаем пользователей с активными подписками
        target_users = await get_users_with_active_subscriptions()
    elif target_group == "unsubscribed":
        # Получаем пользователей без подписок
        target_users = await get_users_without_subscriptions()
    else:
        await update.message.reply_text("Некорректная группа целевых пользователей.")
        return

    # Отправляем уведомления
    for user in target_users:
        try:
            await context.bot.send_message(
                chat_id=user['user_id'],
                text=notification_text,
                reply_markup=reply_markup,
            )
        except Exception as e:
            print(f"Не удалось отправить сообщение пользователю {user['user_id']}: {e}")
            pass

async def search_user(update, context):
    user_id = update.message.from_user.id

    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Проверка, что пользователь администратор
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для отправки уведомлений.")
        return

    # Получаем данные от пользователя: user_id или username
    user_input = update.message.text.strip()  # Получаем текст, который ввел пользователь (например, user_id или username)

    # Ищем пользователя по user_id или username
    user = None
    if user_input.isdigit():  # Если это user_id (цифры), ищем по ID
        user = await get_user(user_id)
    else:  # Если это username, ищем по username
        user = await get_user_for_username(user_input)

    # Если пользователь не найден
    if not user:
        await update.message.reply_text("⚠️ Пользователь которого ищите не найден, укажите верные данные")
        return

    # Проверяем, есть ли активная подписка
    user_subscriptions = await get_user_subscriptions(user_id)
    # Проверяем активные и истекшие подписки
    active_subscription = next(
        (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
        None
    )

    if active_subscription:
        subscription_name = active_subscription["subscription_name"]
        end_date = active_subscription["end_date"]

        subscription_status = (
            f"Активна до {end_date.strftime('%d.%m.%Y')}"
        )
    elif not user_subscriptions:
        subscription_name = "Нет"
        subscription_status = "Нет активной подписки"
        
    elif not active_subscription or active_subscription['end_date'].date() <= datetime.now().date():
        subscription_name = "Нет"
        subscription_status = "Истекла"

    # Подсчёт количества книг в библиотеке
    books_count = len(user.get('library', []))

    # Если пользователь найден, выводим его информацию
    user_info = (
        f"Информация о пользователе:\n\n"
        f"🆔 ID: {user['user_id']}\n"
        f"📚 Создано книг: {books_count}\n"
        f"👤 Имя пользователя: @{user['username']}\n"
        f"📜 Подписка: {subscription_name} ({subscription_status})\n"
    )

    # Если администратор, отображаем соответствующую клавиатуру для дальнейших действий
    admin_subscriptions_keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="users_admin")]
    ]
    reply_markup = InlineKeyboardMarkup(admin_subscriptions_keyboard)

    # Отправляем информацию о пользователе и клавиатуру
    context.user_data['current_mode'] = None
    await update.message.reply_text(user_info, reply_markup=reply_markup)

async def process_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Проверка, что пользователь администратор
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для отправки уведомлений.")
        return

    # Получаем текст уведомления
    notification_text = update.message.text.strip()
    if not notification_text:
        await update.message.reply_text("⚠️ Текст уведомления не может быть пустым.")
        return

    # Проверяем формат уведомления с кнопками
    buttons = []
    if "|" in notification_text:
        lines = notification_text.split("\n")
        text_lines = []
        for line in lines:
            if "|" in line:
                try:
                    button_text, button_link = line.split("|", 1)
                    # Проверка правильности ссылки (должна быть либо внешняя ссылка, либо команда бота)
                    if not (button_link.startswith("http")):
                        await update.message.reply_text(f"⚠️ Неправильная ссылка или команда в строке:\n{line}\nУбедитесь, что ссылка начинается с 'http'")
                        return
                    buttons.append([InlineKeyboardButton(button_text.strip(), url=button_link.strip() if button_link.startswith("http") else None, callback_data=button_link.strip() if not button_link.startswith("http") else None)])
                except ValueError:
                    await update.message.reply_text(f"⚠️ Ошибка в формате кнопки: {line}\nПроверьте, что формат правильный (Текст|Ссылка).")
                    return
            else:
                text_lines.append(line)

        # Соединяем строки текста обратно
        notification_text = "\n".join(text_lines)

    # Проверка текста уведомления (например, текст должен быть обязательно)
    if not notification_text.strip():
        await update.message.reply_text("⚠️ Текст уведомления не может быть пустым! Пожалуйста, добавьте текст.")
        return

    # Клавиатура с кнопками (если есть)
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    # Отправляем уведомление указанной аудитории
    target_group = context.user_data.get("target_group", "all")
    await send_notification_to_users(update, context, notification_text, reply_markup, target_group)

    await update.message.reply_text("✅ Уведомления отправлены!")

    # Сброс режима
    context.user_data['current_mode'] = None

async def process_single_user_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Проверка на админа
    if user_id not in ADMINS:
        await update.message.reply_text("⚠️ У вас нет прав для отправки уведомлений.")
        return

    # Проверяем, что режим переключен на 'notify_single_user'
    if context.user_data.get('current_mode') != 'notify_single_user':
        await update.message.reply_text("⚠️ Ошибка: Неверный режим. Попробуйте снова.")
        return

    # Получаем ID пользователя
    target_user_id = update.message.text.strip()

    # Проверка, что введен ID
    if not target_user_id.isdigit():
        await update.message.reply_text("⚠️ Неверный ID пользователя. Введите числовой ID.")
        return

    target_user_id = int(target_user_id)

    target_user = await get_user(target_user_id)
    
    if not target_user:
        await update.message.reply_text("⚠️ Пользователь с таким ID не найден.")
        return

    # Сохранение ID пользователя для дальнейшей отправки уведомления
    context.user_data['target_user_id'] = target_user_id
    instructions = (
        "✏️ Напишите текст уведомления, который будет отправлен вашим пользователям.\n\n"
        "Вы можете добавить кнопки в уведомление. Для этого используйте следующий формат:\n"
        "`Текст кнопки|Ссылка`\n\n"
        "Пример:\n"
        "🎉 Новое обновление! 🎉\n"
        "Подробнее|https://example.com\n\n"
        "🌟 Для того, чтобы ваше уведомление было красивым и привлекательным, не забудьте добавить смайлики! 🌈😊\n"
        "Они помогут сделать ваше сообщение более ярким и выразительным. Например, используйте смайлики для подчеркивания важной информации или для создания нужной атмосферы.\n\n"
        "Если хотите добавить дополнительные кнопки, укажите их по аналогии с примером выше.\n\n"
        "📌 Не забывайте, что кнопки могут вести на страницы, ссылки или команды бота.\n\n"
        "🔙 Для отмены вернитесь назад, выбрав кнопку ниже."
    )
    keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="notifications")],
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        # Если это callback запрос, редактируем сообщение
        await update.callback_query.edit_message_text(instructions, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        # Если это обычное сообщение, отправляем новое сообщение
        await update.message.reply_text(instructions, reply_markup=reply_markup, parse_mode="Markdown")

    # Переход в режим написания уведомления
    context.user_data['current_mode'] = 'process_single_notification'

async def limit_page_in_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    # Ищем пользователя
    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return
    text = update.message.text.strip()
    # Проверка на администратора
    if user_id not in ADMINS:
        await update.message.reply_text("У вас нет прав для доступа к админ панели.")
        context.user_data['current_mode'] = None
        context.user_data['selected_subscription'] = None
        return

    if context.user_data.get('current_mode') != 'limit_page_book':
        return  # Выход из функции, если не в режиме изменения кол-во книг для лимита

    if not text:
        await update.message.reply_text(f"Число не найдено")
        return
    try:
        # Преобразуем ID в целое число
        number = int(text)
        
        if number < 5:
            await update.message.reply_text(f"Введите число больше 4")
            return

        global limit_page_book
        limit_page_book = number
        await update.message.reply_text(f"Ограничение на макс. кол-во стрн. (без подписки) изменёно на {limit_page_book}")
        await handle_menu(update, context)
        context.user_data['current_mode'] = None
    except ValueError:
        await update.message.reply_text("Введите коректное число больше 4")

async def process_single_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    user = await get_user(user_id)

    if not user:####################################################################################################################################
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Проверка на админа
    if user_id not in ADMINS:
        await update.message.reply_text("⚠️ У вас нет прав для отправки уведомлений.")
        return

    # Проверяем, что режим переключен на 'process_single_notification'
    if context.user_data.get('current_mode') != 'process_single_notification':
        await update.message.reply_text("⚠️ Ошибка: Неверный режим. Попробуйте снова.")
        return

    # Получаем текст уведомления
    notification_text = update.message.text.strip()

    if not notification_text:
        await update.message.reply_text("⚠️ Текст уведомления не может быть пустым. Пожалуйста, добавьте текст.")
        return

    # Проверяем формат уведомления с кнопками
    buttons = []
    if "|" in notification_text:
        lines = notification_text.split("\n")
        text_lines = []
        for line in lines:
            if "|" in line:
                try:
                    button_text, button_link = line.split("|", 1)
                    # Проверка правильности ссылки (должна быть либо внешняя ссылка, либо команда бота)
                    if not (button_link.startswith("http")):
                        await update.message.reply_text(f"⚠️ Неправильная ссылка:\n{line}\nУбедитесь, что ссылка начинается с 'http'")
                        return
                    buttons.append([InlineKeyboardButton(button_text.strip(), url=button_link.strip() if button_link.startswith("http") else None, callback_data=button_link.strip() if not button_link.startswith("http") else None)])
                except ValueError:
                    await update.message.reply_text(f"⚠️ Ошибка в формате кнопки: {line}\nПроверьте, что формат правильный (Текст|Ссылка).")
                    return
            else:
                text_lines.append(line)

        # Соединяем строки текста обратно
        notification_text = "\n".join(text_lines)

    # Проверка текста уведомления (например, текст должен быть обязательно)
    if not notification_text.strip():
        await update.message.reply_text("⚠️ Текст уведомления не может быть пустым! Пожалуйста, добавьте текст.")
        return

    # Клавиатура с кнопками (если есть)
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    # Отправляем уведомление конкретному пользователю
    target_user_id = context.user_data.get('target_user_id')
    target_user = await get_user(target_user_id)

    if target_user:
        # Отправка уведомления пользователю
        await context.bot.send_message(chat_id=target_user_id, text=notification_text, reply_markup=reply_markup)
        await update.message.reply_text(f"✅ Уведомление отправлено пользователю {target_user_id}!")

    # Сброс режима
    context.user_data['current_mode'] = None

# Обработка сообщений пользователя
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем текущий режим
    current_mode = context.user_data.get('current_mode')

    if current_mode == "search_books":
        await search_books(update, context)

    elif current_mode == "chat_with_ai":
        await chat_with_ai(update, context)

    elif current_mode == "gift_subscription":
        await gift_subscription(update, context)
    
    elif current_mode == "set_subscription_days":
        await set_subscription_days(update, context)

    elif current_mode == "limit_page_book":
        await limit_page_in_book(update, context)

    elif current_mode == "edit_hour_in_chat_with_ai":
        await edit_hour_in_chat_with_ai(update, context)

    elif current_mode == "edit_count_in_chat_with_ai":
        await edit_count_in_chat_with_ai(update, context)
    
    elif current_mode == "process_single_notification":
        await process_single_notification(update, context)
    
    elif current_mode == "notify_single_user":
        await process_single_user_notification(update, context)
    
    elif current_mode == "Limit_books_day":
        await Limit_books_day(update, context)

    elif current_mode == 'add_subscription':
        await add_subscription(update, context)
    
    elif current_mode == 'Limit_books_day_subscribe':
        await Limit_books_day_subscribe(update, context)

    elif current_mode == 'search_user':
        await search_user(update, context)

    elif current_mode == 'process_notification':
        await process_notification(update, context)

    else:
        await update.message.reply_text("Используйте /start для выбора режима.")

async def chat_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    # Получаем ID пользователя
    user_id = update.message.from_user.id

    # Убедитесь, что 'chat_context' инициализирован
    if 'chat_context' not in context.user_data:
        context.user_data['chat_context'] = []

    # Добавляем сообщение пользователя в историю
    context.user_data['chat_context'].append({"role": "user", "content": user_message})

    # Ограничиваем историю до 10 сообщений
    if len(context.user_data['chat_context']) > 10:
        context.user_data['chat_context'] = context.user_data['chat_context'][-10:]

    # Ищем пользователя
    user = await get_user(user_id)

    if not user:
        await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        return

    # Ищем активную подписку пользователя
    user_subscriptions = await get_user_subscriptions(user_id)
    active_subscription = next(
        (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
        None
    )

    # Если админ выключил проверку подписки
    if subscription_chat_with_ai_is_true:
        # Если подписка не активна
        if active_subscription is None:
            current_time = datetime.now(MOSCOW_TZ)

            await increment_count_words(user_id)

            # Проверяем, нужно ли сбросить лимит
            user = await get_user(user_id)
            if user['reset_time'] and current_time >= user['reset_time']:
                print('мы сюда заходим')
                await update_count_words(user_id, 0)
                await update_reset_time(user_id, None)

            # Проверка, превышен ли лимит
            user = await get_user(user_id)
            if user['count_words'] > count_limit_chat_with_ai:
                # Устанавливаем время сброса лимита
                user = await get_user(user_id)
                if not user['reset_time']:
                    date = current_time + timedelta(hours=wait_hour)
                    date_naive = date.replace(tzinfo=None)  # Убираем временную зону
                    await update_reset_time(user_id, date_naive)  # Обновляем reset_time в базе данных

                    # Дожидаемся завершения записи времени сброса
                    user = await get_user(user_id)  # Получаем актуальные данные из базы данных
                    reset_time = user['reset_time']

                    if reset_time is not None:
                        time_left = reset_time - current_time
                        # Рассчитываем оставшееся время
                        hours_left, remainder = divmod(time_left.seconds, 3600)
                        minutes_left, _ = divmod(remainder, 60)

                        # Сообщение о блокировке
                        await update.message.reply_text(
                            f"⏳ Вы достигли лимита в {count_limit_chat_with_ai} сообщений! 📩\n\n"
                            f"🔒 Ваш лимит будет автоматически сброшен через "
                            f"{hours_left} часов и {minutes_left} минут.\n\n"
                            f"💎 Хотите больше возможностей? Оформите подписку, чтобы отключить лимит и пользоваться ботом без ограничений!"
                        )
                        return
                else:
                    await update.message.reply_text("Ошибка: время сброса лимита не установлено. Пожалуйста, попробуйте снова.")
                    return

    # Запрос к ChatGPT
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=context.user_data['chat_context'],
        max_tokens=500
    )

    # Ответ ИИ
    ai_reply = response['choices'][0]['message']['content']
    global count_chat_ai
    count_chat_ai += 1
    # Добавляем ответ ИИ в историю
    context.user_data['chat_context'].append({"role": "assistant", "content": ai_reply})
    
    await update.message.reply_text(ai_reply)

async def generate_pdf_and_send(update, context, full_text, exact_title):
    user_id = update.message.from_user.id
    # Создание PDF
    pdf = FPDF()
    pdf.add_font('Garamond', '', 'Garamond.ttf', uni=True)  # Подключение шрифта Garamond
    pdf.set_font('Garamond', size=18)  # Установка шрифта и размера
    pdf.add_page()

    # Разбиваем текст на строки для PDF
    pdf.multi_cell(0, 10, full_text)

    # Проверяем библиотеку пользователя
    user = await get_user(user_id)

    if not user:
        if context.user_data.get('book_language') == 'russian':
            await update.message.reply_text("⚠️ Ошибка: пользователь не найден. Обратитесь к администратору.")
        else:
            await update.message.reply_text("⚠️ Error: user not found. Contact your administrator.")
        return

    user_dict = dict(user)

    # Убедимся, что library — это список
    if isinstance(user_dict['library'], str):  # Если это строка
        user_dict['library'] = json.loads(user_dict['library'])  # Преобразуем строку в список
    elif not isinstance(user_dict['library'], list):  # Если это не список
        user_dict['library'] = []  # Инициализируем как пустой список

    # Теперь обработаем каждый элемент в user_dict['library']
    for i in range(len(user_dict['library'])):
        if isinstance(user_dict['library'][i], str):  # Если элемент — строка
            user_dict['library'][i] = {'title': user_dict['library'][i]}  # Преобразуем в словарь

    # Далее проверяем уникальность
    unique_title = exact_title
    suffix = 1

    # Проверяем на уникальность название книги
    while any(book['title'] == unique_title for book in user_dict['library']):
        suffix += 1
        unique_title = f"{exact_title}_{suffix}"
    
    # Преобразуем в строку JSON с корректной обработкой datetime
    def datetime_converter(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()  # Преобразуем datetime в строку ISO
        raise TypeError(f"Type {obj.__class__.__name__} not serializable")

    library_json = json.dumps(user_dict, default=datetime_converter)

    # После изменений можно записать обратно в базу данных
    await update_user_library_dict(user_id, library_json)

    # Уникальное имя файла
    file_name = f"{user_id}_{unique_title}.pdf"
    file_path = f"media/{file_name}"

    # Сохраняем PDF
    pdf.output(file_path)

    # Сохраняем PDF в буфер
    pdf_output = io.BytesIO()
    pdf_output.write(pdf.output(dest='S').encode('latin1'))  # Сохраняем PDF как строку в буфер
    pdf_output.seek(0)  # Перемещаем указатель в начало буфера

    # Отправляем PDF пользователю
    await update.message.reply_document(document=pdf_output, filename=f"{unique_title}.pdf")

    # Добавляем книгу в библиотеку
    user_dict['library'].append({
        "title": unique_title,
        "file_path": file_path,  # Сохраняем полный путь к файлу
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    # Обновляем библиотеку в базе данных
    library_json = json.dumps(user_dict, default=datetime_converter)
    await update_user_library_dict(user_id, library_json)

    # Уведомляем пользователя
    if context.user_data.get('book_language') == 'russian':
        await update.message.reply_text(
            f"📚 Книга {unique_title} готова! 🎉\n📚 Книга успешно добавлена в вашу библиотеку! 🎉",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📚 Моя библиотека", callback_data='my_library'),
                InlineKeyboardButton("🔙 Назад в меню", callback_data='menu')
            ]])
        )
    else:
        await update.message.reply_text(
            f"📚 Book {unique_title} is ready! 🎉\n📚 The book has been successfully added to your library! 🎉",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📚 My library", callback_data='my_library'),
                InlineKeyboardButton("🔙 Back to menu", callback_data='menu')
            ]])
        )

async def process_book(update: Update, context: ContextTypes.DEFAULT_TYPE, num_pages: int):
    """Асинхронная обработка создания книги."""
    user_id = update.message.from_user.id
    user = await get_user(user_id)

    # Обновляем поле is_process_book в базе данных
    await update_user_process_book(user_id, True)

    list_parts = context.user_data.get('list_parts')
    exact_title = context.user_data.get('exact_title')
    
    total_words = num_pages * 140  # общее количество слов
    total_words_in_dop = 0
    # Получаем список ключей, где значение True
    selected_options_keys = [key for key, value in context.user_data.get('options', {}).items() if value]
    full_total_words = total_words
    if selected_options_keys:
        for option in selected_options_keys:
            if option == 'option_1':
                procent = 0.10
                total_words_in_dop += total_words * procent
            elif option == 'option_2':
                procent = 0.05
                total_words_in_dop += total_words * procent
            elif option == 'option_3':
                procent = 0.05
                total_words_in_dop += total_words * procent
            elif option == 'option_4':
                procent = 0.10
                total_words_in_dop += total_words * procent
        total_words = total_words - total_words_in_dop
    
    words_per_part = total_words / 7
    subparts_per_part_float = words_per_part / 100
    if subparts_per_part_float < 1:
        subparts_per_part_float = 1.0
    subparts_per_part_base = math.floor(subparts_per_part_float)
    fractional_part = round((subparts_per_part_float - subparts_per_part_base) * 10)

    subparts = [subparts_per_part_base] * 7
    if fractional_part in {2, 4, 6, 8}:
        extra_subparts = fractional_part // 2
        for i in range(extra_subparts):
            subparts[i] += 1

    last_text_in_pdf = []
    
    if context.user_data.get('book_language') == 'russian':
        progress_message = await update.message.reply_text("⏳ Начинаем обработку...")
    else:
        progress_message = await update.message.reply_text("⏳ Let's start processing...")

    for index, part_number in enumerate(list_parts, start=1):
        for subpart_index in range(1, subparts[index - 1] + 1):
            if context.user_data.get('book_language') == 'russian':
                prompt = (
                    f"Книга '{exact_title}' содержит {num_pages} страниц."
                    f"Мы сейчас рассматриваем часть {part_number}, подчасть {subpart_index}/{subparts[index - 1]}."
                    f"В этой подчасти должно быть 190 слов."
                    "Учитывая это, напишите о содержании данной подчасти книги. И не пиши подчасть и часть в тексте"
                )
            else:
                prompt = (
                    f"Book '{exact_title}' contains {num_pages} pages."
                    f"We are now considering part {part_number}, subpart {subpart_index}/{subparts[index - 1]}."
                    f"This subpart should be 190 words long."
                    "With this in mind, write about the content of this sub-part of the book. And do not write subpart and part in the text"
                )

            response = await openai.ChatCompletion.acreate(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000
            )
            chat_gpt_reply = response['choices'][0]['message']['content']
            last_text_in_pdf.append(chat_gpt_reply)

            if progress_message:
                if context.user_data.get('book_language') == 'russian':
                    await progress_message.edit_text(
                        f"⏳ Обрабатываем часть {index}/7, подчасть {subpart_index}/{subparts[index - 1]}"
                    )
                else:
                    await progress_message.edit_text(
                        f"⏳ Processing part {index}/7, subpart {subpart_index}/{subparts[index - 1]}"
                    )

    # Получаем список ключей, где значение True
    selected_options_keys = [key for key, value in context.user_data.get('options', {}).items() if value]
    if selected_options_keys:
        apend_ture = False
        for option in selected_options_keys:
            if option == 'option_3':
                pass
            else:
                if apend_ture: 
                    pass
                else:
                    apend_ture = True
                    last_text_in_pdf.append('--------------------------------------------------------------------------------------------')
        count_pages = 0
        count = 0

        if context.user_data.get('book_language') == 'russian':
            progress_message = await update.message.reply_text("⏳ Начинаем обработку...")
        else:
            progress_message = await update.message.reply_text("⏳ Let's start processing...")
        # Итерируемся по ним
        for option in selected_options_keys:
            count += 1

            if progress_message:
                if context.user_data.get('book_language') == 'russian':
                    await progress_message.edit_text(
                        f"⏳ Обрабатываем часть {count}/{len(selected_options_keys)}"
                    )
                else:
                    await progress_message.edit_text(
                        f"⏳ Processing part {count}/{len(selected_options_keys)}"
                    )
            if option == 'option_1':
                procent = 0.1
                remainder = full_total_words * procent
                if remainder <= 140:
                    if context.user_data.get('book_language') == 'russian':
                        prompt = (
                            f"Напиши мне подробный анализ этой книги {exact_title} и разбор ключевых идей"
                            f"В этом подробном анализе и разборе ключевых идей должно быть {remainder + 50} слов."
                        )
                    else:
                        prompt = (
                            f"Write me a detailed analysis of this book {exact_title} and an analysis of key ideas"
                            f"This detailed analysis and analysis of key ideas should contain {remainder + 50} words."
                        )

                    response = await openai.ChatCompletion.acreate(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=500
                    )

                    chat_gpt_reply = response['choices'][0]['message']['content']
                    last_text_in_pdf.append(chat_gpt_reply)
                else:
                    count_pages = int(remainder // 140)
                    for page in range(1, count_pages + 1):
                        if context.user_data.get('book_language') == 'russian':
                            prompt = (
                                f"Напиши мне подробный анализ этой книги {exact_title} и разбор ключевых идей"
                                f"Мы сейчас рассматриваем часть {page}/{count_pages}."
                                f"В этом подробном анализе и разборе ключевых идей должно быть 190 слов."
                            )
                        else:
                            prompt = (
                                f"Write me a detailed analysis of this book {exact_title} and an analysis of key ideas"
                                f"We are now looking at the {page}/{count_pages} part."
                                f"This detailed analysis and analysis of key ideas should contain 190 words."
                            )
                        response = await openai.ChatCompletion.acreate(
                            model="gpt-3.5-turbo",
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=500
                        )

                        chat_gpt_reply = response['choices'][0]['message']['content']
                        last_text_in_pdf.append(chat_gpt_reply)

            elif option == 'option_2':
                procent = 0.05
                remainder = full_total_words * procent
                if remainder <= 140:
                    if context.user_data.get('book_language') == 'russian':
                        prompt = (
                            f"напиши мне обширный подбор цитат из книги {exact_title}"
                            f"В этом обширном подборе цитат из книги должно быть {remainder + 50} слов."
                        )
                    else:
                        prompt = (
                            f"write me an extensive selection of quotes from the book {exact_title}"
                            f"This extensive selection of book quotes should contain {remainder + 50} words."
                        )
                    response = await openai.ChatCompletion.acreate(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=500
                        )
                    chat_gpt_reply = response['choices'][0]['message']['content']
                    last_text_in_pdf.append(chat_gpt_reply)
                else:
                    count_pages = int(remainder // 140)
                    for page in range(1, count_pages + 1):
                        if context.user_data.get('book_language') == 'russian':
                            prompt = (
                                f"напиши мне обширный подбор цитат из книги {exact_title}"
                                f"Мы сейчас рассматриваем часть {page}/{count_pages}."
                                f"В этом подробном анализе и разборе ключевых идей должно быть 190 слов."
                            )
                        else:
                            prompt = (
                                f"write me an extensive selection of quotes from the book {exact_title}"
                                f"We are now looking at the {page}/{count_pages} part."
                                f"This detailed analysis and analysis of key ideas should contain 190 words."
                            )
                        response = await openai.ChatCompletion.acreate(
                            model="gpt-3.5-turbo",
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=500
                            )
                        chat_gpt_reply = response['choices'][0]['message']['content']
                        last_text_in_pdf.append(chat_gpt_reply)

            elif option == 'option_3':
                procent = 0.05
                remainder = full_total_words * procent
                if remainder <= 140:
                    if context.user_data.get('book_language') == 'russian':
                        prompt = (
                            f"напиши мне небольшую биографию автора из книги {exact_title}"
                            f"В этой небольшой биографии автора должно быть {remainder + 50} слов."
                        )
                    else:
                        prompt = (
                            f"write me a short biography of the author from the book {exact_title}"
                            f"This short author bio should be {remainder + 50} words."
                        )

                    response = await openai.ChatCompletion.acreate(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=500
                    )

                    chat_gpt_reply = response['choices'][0]['message']['content']
                    last_text_in_pdf.insert(0, chat_gpt_reply)
                else:
                    count_pages = int(remainder // 140)
                    first_iteration_done = False
                    first_iteration_done_count = 0
                    for page in range(1, count_pages + 1):
                        if context.user_data.get('book_language') == 'russian':
                            prompt = (
                                f"напиши мне небольшую биографию автора из книги {exact_title}"
                                f"Мы сейчас рассматриваем часть {page}/{count_pages}."
                                f"В этой небольшой биографии автора должно быть 190 слов."
                            )
                        else:
                            prompt = (
                                f"write me a short biography of the author from the book {exact_title}"
                                f"We are now looking at the {page}/{count_pages} part."
                                f"This short author bio should be 190 words."
                            )
                        response = await openai.ChatCompletion.acreate(
                            model="gpt-3.5-turbo",
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=500
                        )

                        chat_gpt_reply = response['choices'][0]['message']['content']

                        if not first_iteration_done:
                            # Если это первая итерация, добавляем в начало
                            last_text_in_pdf.insert(0, chat_gpt_reply)
                            first_iteration_done = True
                            first_iteration_done_count = 1
                        elif first_iteration_done_count == 1:
                            # Вторая итерация, добавляем под первой записью
                            last_text_in_pdf.insert(1, chat_gpt_reply)
                            first_iteration_done_count = 2
                        elif first_iteration_done_count == 2:
                            # Третья итерация, добавляем на третью позицию
                            last_text_in_pdf.insert(2, chat_gpt_reply)
                            first_iteration_done_count = 3
                        elif first_iteration_done_count == 3:
                            # Четвертая итерация, добавляем на четвертую позицию
                            last_text_in_pdf.insert(3, chat_gpt_reply)
                            first_iteration_done_count = 4
                        elif first_iteration_done_count == 4:
                            # Пятая итерация, добавляем на пятую позицию
                            last_text_in_pdf.insert(4, chat_gpt_reply)
                            first_iteration_done_count = 5
                        elif first_iteration_done_count == 5:
                            # Если больше 5 итераций, добавляем в конец
                            last_text_in_pdf.insert(5, chat_gpt_reply)

            elif option == 'option_4':
                procent = 0.1
                remainder = full_total_words * procent
                if remainder <= 140:
                    if context.user_data.get('book_language') == 'russian':
                        prompt = (
                            f"напиши о критике данной книги {exact_title}"
                            f"В этой критике должно быть {remainder + 50} слов."
                        )
                    else:
                        prompt = (
                            f"write about criticism of this book {exact_title}"
                            f"This critique should be {remainder + 50} words."
                        )
                    response = await openai.ChatCompletion.acreate(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=500
                        )
                    chat_gpt_reply = response['choices'][0]['message']['content']
                    last_text_in_pdf.append(chat_gpt_reply)

                else:
                    count_pages = int(remainder // 140)
                    for page in range(1, count_pages + 1):
                        if context.user_data.get('book_language') == 'russian':
                            prompt = (
                                f"напиши о критике данной книги {exact_title}"
                                f"Мы сейчас рассматриваем часть {page}/{count_pages}."
                                f"В этой критике должно быть 190 слов."
                            )
                        else:
                            prompt = (
                                f"write about criticism of this book {exact_title}"
                                f"We are now looking at the {page}/{count_pages} part."
                                f"This critique should be 190 words."
                            )
                        response = await openai.ChatCompletion.acreate(
                            model="gpt-3.5-turbo",
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=500
                            )
                        chat_gpt_reply = response['choices'][0]['message']['content']
                        last_text_in_pdf.append(chat_gpt_reply)

    full_text = "\n\n".join(last_text_in_pdf)

    current_book_count = user['daily_book_count']
    new_book_count = current_book_count + 1
    # Обновляем значение в базе данных
    await update_user_daily_book_count(user_id, new_book_count)
    await update_user_process_book(user_id, False)

    await generate_pdf_and_send(update, context, full_text, exact_title)
    context.user_data.clear()

async def search_books(update, context):
    user_id = update.message.from_user.id

    # Проверка пользователя
    user = await get_user(user_id)

    if not user:
        if context.user_data.get('book_language') == 'russian':
            await update.message.reply_text("⚠️ Пользователь не найден. Обратитесь к администратору.")
        else:
            await update.message.reply_text("⚠️ User not found. Contact your administrator.")
        return

    if user['is_process_book'] == True:
        keyboard = [
        [InlineKeyboardButton("🔙 Назад в меню", callback_data='menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if context.user_data.get('book_language') == 'russian':
            await update.message.reply_text(
                "⚠️ Вы уже запустили процесс создания книги. Пожалуйста, подождите, пока завершится обработка предыдущей.",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                "⚠️ You have already started the process of creating a book. Please wait while the previous one is processed.",
                reply_markup=reply_markup
            )
        return
    user_subscriptions = await get_user_subscriptions(user_id)
    active_subscription = next(
        (sub for sub in user_subscriptions if sub["end_date"] >= datetime.now().date()), 
        None
    )

    today_date = datetime.now().date()
    print(user.get('last_book_date'))
    print(today_date)
    if user.get('last_book_date') != today_date:
        await update_user_last_book_date(user_id, today_date)
        #user['last_book_date'] = today_date
        #user['daily_book_count'] = 0
        await update_user_daily_book_count(user_id, 0)

    # Проверка лимита на книги за день
    daily_book_count = user.get('daily_book_count', 0)
    if subscription_search_book_is_true:
        if active_subscription is None:
            if daily_book_count >= count_limit_book_day:
                if context.user_data.get('book_language') == 'russian':
                    await update.message.reply_text(
                    f"❌ Лимит книг на сегодня исчерпан.\nПопробуйте завтра! 🕒\n📝 Либо оформите подписку."
                    )
                else:
                    await update.message.reply_text(
                    f"❌ The book limit for today has been reached.\nTry tomorrow! 🕒\n📝 Or subscribe."
                    )

                await handle_menu(update, context)
                return
        else:
            if daily_book_count >= count_limit_book_in_subscribe_day:
                if context.user_data.get('book_language') == 'russian':
                    await update.message.reply_text(
                    f"❌ Лимит книг на сегодня исчерпан.\nПопробуйте завтра! 🕒"
                    )
                else:
                    await update.message.reply_text(
                    f"❌ The book limit for today has been reached.\nTry tomorrow! 🕒"
                    )
                await handle_menu(update, context)
                return
    else:
        if daily_book_count >= count_limit_book_in_subscribe_day:
            if context.user_data.get('book_language') == 'russian':
                await update.message.reply_text(
                f"❌ Лимит книг на сегодня исчерпан.\nПопробуйте завтра! 🕒"
                )
            else:
                await update.message.reply_text(
                f"❌ The book limit for today has been reached.\nTry tomorrow! 🕒"
                )
            await handle_menu(update, context)
            return

    book_title = update.message.text
    if context.user_data.get('awaiting_pages'):
        try:
            num_pages = int(book_title)
        except ValueError:
            if context.user_data.get('book_language') == 'russian':
                await update.message.reply_text("✏️ Пожалуйста, укажите количество страниц числом")
            else:
                await update.message.reply_text("✏️ Please indicate the number of pages as a number")
            return

        if subscription_search_book_is_true:
            if active_subscription is None:
                if num_pages < 5 or num_pages > limit_page_book:
                    if context.user_data.get('book_language') == 'russian':
                        await update.message.reply_text(f"✏️ Количество страниц должно быть от 5 до {limit_page_book}")
                    else:
                        await update.message.reply_text(f"✏️ The number of pages should be from 5 to {limit_page_book}")
                    return
            else:
                if num_pages < 5 or num_pages > 50:
                    if context.user_data.get('book_language') == 'russian':
                        await update.message.reply_text("✏️ Количество страниц должно быть от 5 до 50")
                    else:
                        await update.message.reply_text("✏️ The number of pages should be from 5 to 50")
                    return
        else:
            if num_pages < 5 or num_pages > 50:
                if context.user_data.get('book_language') == 'russian':
                    await update.message.reply_text("✏️ Количество страниц должно быть от 5 до 50")
                else:
                    await update.message.reply_text("✏️ The number of pages should be from 5 to 50")
                return

        # Запускаем обработку книги в фоне
        global count_search_book
        count_search_book += 1
        asyncio.create_task(process_book(update, context, num_pages))
        if context.user_data.get('book_language') == 'russian':
            await update.message.reply_text(
                "📚 Обработка книги началась. Вы можете продолжить пользоваться ботом, пока книга создается!"
            )
        else:
            await update.message.reply_text(
                "📚 Processing of the book has begun. You can continue to use the bot while the book is being created!"
            )
        return

    context.user_data['book_title'] = book_title
    exact_title, book_exists, list_parts = await get_chatgpt_response(update, book_title)

    if book_exists == "да":
        context.user_data['exact_title'] = exact_title
        context.user_data['list_parts'] = list_parts
        if subscription_search_book_is_true:
            if active_subscription is None:
                if context.user_data.get('book_language') == 'russian':
                    await update.message.reply_text(
                        f"📚 Книга {exact_title} найдена! 🎉\n"
                        f"📖 Сколько страниц в этой книге вы хотите? (от 5 до {limit_page_book})",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ Это не та книга", callback_data='menu')
                        ]])
                        )
                else:
                    await update.message.reply_text(
                        f"📚 Book {exact_title} found! 🎉\n"
                        f"📖 How many pages in this book do you want? (from 5 to {limit_page_book})",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ This is the wrong book", callback_data='menu')
                        ]])
                    )
                context.user_data['awaiting_pages'] = True
            else:
                if context.user_data.get('book_language') == 'russian':
                    await update.message.reply_text(
                        f"📚 Книга {exact_title} найдена! 🎉\n"
                        f"📖 Сколько страниц в этой книге вы хотите? (от 5 до 50)",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ Это не та книга", callback_data='menu')
                        ]])
                        )
                else:
                    await update.message.reply_text(
                        f"📚 Book {exact_title} found! 🎉\n"
                        f"📖 How many pages in this book do you want? (from 5 to 50)",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ This is the wrong book", callback_data='menu')
                        ]])
                    )
                context.user_data['awaiting_pages'] = True
        else:
            if context.user_data.get('book_language') == 'russian':
                await update.message.reply_text(
                    f"📚 Книга {exact_title} найдена! 🎉\n"
                    f"📖 Сколько страниц в этой книге вы хотите? (от 5 до 50)",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Это не та книга", callback_data='menu')
                    ]])
                    )
            else:
                await update.message.reply_text(
                    f"📚 Book {exact_title} found! 🎉\n"
                    f"📖 How many pages in this book do you want? (from 5 to 50)",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ This is the wrong book", callback_data='menu')
                    ]])
                )
            context.user_data['awaiting_pages'] = True
    elif book_exists == 'не 7':
        if context.user_data.get('book_language') == 'russian':
            await update.message.reply_text(
                f"❌ Произошла ошибка. Попробуйте заново",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад в меню", callback_data='menu')
                ]])
            )
        else:
            await update.message.reply_text(
                f"❌ An error has occurred. Try again",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to menu", callback_data='menu')
                ]])
            )
    elif book_exists == 'нет':
        if context.user_data.get('book_language') == 'russian':
            await update.message.reply_text(
                f"❌ Книга '{book_title}' не найдена. Попробуйте другое название",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад в меню", callback_data='menu')
                ]])
            )
        else:
            await update.message.reply_text(
                f"❌ Book '{book_title}' not found. Try a different name",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to menu", callback_data='menu')
                ]])
            )

async def get_chatgpt_response(update: Update, message):
    prompt = (
        f"Раздели книгу под названием \"{message}\" обязательно ровно на 7 частей."
        "Если книга существует, раздели на 7 подробных частей, и укажи правильное название (в кавычках)"
        "Если книга не существует, напиши, что книга не существует."
    )
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000
        )
        answer = response.choices[0].message['content']
        #print('GPT ответ:', answer)
        # Поиск точного названия книги в ответе
        found_title_match = re.search(r'"([^"]+)"', answer)
        exact_title = found_title_match.group(1) if found_title_match else None
        
        # Проверка на отсутствие книги
        if any(phrase in answer.lower() for phrase in ["нет", "не существует", "не найдена"]):
            book_exists = "нет"
            exact_title = None
            list_parts = None
            return exact_title, book_exists, list_parts
        else:
            book_exists = "да"
            found_title_match = re.search(r'"([^"]+)"', answer)
            exact_title = found_title_match.group(1) if found_title_match else None

            # Разделение на части по пунктам списка
            parts = re.split(r'\n\d+\.\s', answer, maxsplit=7)  # Ищем начало частей в формате "1. ", "2. " и т.д.

            # Проверяем, что получили как минимум 7 частей
            if len(parts) < 8:  # Пролог + 7 частей
                print("Ошибка: Ответ не содержит 7 частей. ---_-_-_-_----___--__--_--__--_____---__--_--__--_-_-_-_---")
                part_1, part_2, part_3, part_4, part_5, part_6, part_7 = [None] * 7
                book_exists = 'не 7'
                exact_title = None
                list_parts = None
            else:
                # Записываем каждую часть в отдельную переменную, пропуская пролог (часть до "1.")
                part_1, part_2, part_3, part_4, part_5, part_6, part_7 = [part.strip() for part in parts[1:8]]
                list_parts = [part_1, part_2, part_3, part_4, part_5, part_6, part_7]

            # Возвращаем название книги, её существование и все части
            return exact_title, book_exists, list_parts

    except openai.error.APIConnectionError:
        print("Ошибка openai.error.APIConnectionError соединения с API. Повторная попытка через 5 секунд.")
        await update.message.reply_text("Ошибка соединения с API. Повторная попытка через 5 секунд.")
        await asyncio.sleep(5)
        return None, "нет", None  # Вернуть значения по умолчанию при ошибке
    
    except openai.error.Timeout:
        print("Ошибка openai.error.Timeout таймаута при запросе к OpenAI. Повторная попытка через 5 секунд.")
        await update.message.reply_text("Ошибка соединения с API. Повторная попытка через 5 секунд.")
        await asyncio.sleep(5)
        return await get_chatgpt_response(prompt)

# Главная функция
def main():
    application = Application.builder().token(telegram_bot_token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_menu_selection))
    application.run_polling()

if __name__ == "__main__":
    main()