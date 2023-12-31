from aiogram import Bot, Dispatcher, types, executor
import logging
import pandas as pd
import numpy as np
import asyncio
import time
from sentence_transformers import SentenceTransformer, InputExample
from sklearn.metrics.pairwise import cosine_similarity
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from config import TOKEN, ADMIN_USER_ID, CREDS
from aiogram.contrib.middlewares.logging import LoggingMiddleware

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Указываем путь к файлу с ключами API Google Sheets
CREDENTIALS_FILE = 'creds.json'

# Указываем ID таблицы
SPREADSHEET_ID = CREDS

model = SentenceTransformer(
    "sentence-transformers/msmarco-distilbert-base-tas-b")
# model = SentenceTransformer(model_name)

# Авторизуемся в Google Sheets API
creds = ServiceAccountCredentials.from_json_keyfile_name(
    CREDENTIALS_FILE, ['https://www.googleapis.com/auth/spreadsheets'])
client = gspread.authorize(creds)
# Получение данных из таблицы и создание DataFrame
sheet = client.open_by_key(SPREADSHEET_ID).worksheet('ТЦ «Сити Парк»')
worksheet_data = sheet.get_all_values()
df = pd.DataFrame(worksheet_data[1:], columns=worksheet_data[0])
df.set_index('Время', inplace=True)


def get_free_time_by_day(data, day):
    free = data.query(f'{day} == ""').index.to_list()
    return '\n'.join(free)


data = pd.read_csv('data/Q_and_A_data2 .csv')
train_examples = []
for _, row in data.iterrows():
    question = row['Вопросы']
    answer = row['Ответы']
    train_examples.append(InputExample(texts=[question, answer]))
embeddings = model.encode([example.texts[0] for example in train_examples])

GREETING_PHRASES = ["здраст", "привет", "добр"]
THANKS_PHRASES = ['Спасибо', 'Благодарю', 'Ок']


logging.basicConfig(filename='Bot/chat_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# # Инициализация словаря для хранения временных меток последнего сообщения от каждого пользователя
# last_messages = {}

# Инициализация словаря для отслеживания пользователей, ожидающих ответа
waiting_for_response = {}


# Обработчик текстовых сообщений
@dp.message_handler()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    user_query = message.text

    # Здесь обрабатывается запрос пользователя о свободных окошках
    if "свободные окошки" in user_query.lower() or "свободное время" in user_query.lower():
        waiting_for_response[user_id] = True
        await bot.send_message(user_id, "Какой день недели вас интересует?")

    # Здесь обрабатывается запрос о свободном времени на день недели
    elif any(user_query.lower().startswith(phrase) for phrase in GREETING_PHRASES):
        response = f"Здравствуйте, {user_name}!\n\nЧем я могу помочь?"
        await message.reply(response)
    elif any(user_query.lower() == phrase.lower() for phrase in THANKS_PHRASES):
        response = f"Спасибо, что написали, если у Вас еще остались вопросы, с радостью ответим на них!"
        await message.reply(response)
    elif "цена" in user_query.lower() or "записаться" in user_query.lower() or "стоит" in user_query.lower():
        response = f"На этот вопрос Вам ответит наш администратор, он свяжется с вами в ближайшее время."
        await message.reply(response)

        admin_user_id = ADMIN_USER_ID
        await bot.send_message(admin_user_id, "Клиенту нужна помощь!")

        loop = asyncio.get_event_loop()
        loop.create_task(check_unanswered_messages(user_id))

    # Здесь обрабатывается запрос о свободном времени на конкретный день недели
    elif any(day in user_query.lower() for day in ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]):
        # Получаем день недели из текстового сообщения
        day = user_query.lower()

        # Проверяем, что день недели в расписании
        if day not in sheet.row_values(1):
            await bot.send_message(user_id, "Извини, я не знаю такого дня недели.")
            return

        # Получаем свободное время для дня недели
        free_time = get_free_time_by_day(df, day)

        # Отправляем пользователю свободное время
        await bot.send_message(user_id, f"Мастер может вас принять в {day}:\n{free_time}")

    else:
        # Получение векторного представления для пользовательского запроса
        user_query_embedding = model.encode([user_query])

        # Вычисление близости между пользовательским запросом и векторами вопросов
        similarities = cosine_similarity(user_query_embedding, embeddings)

        # Находим индексы трех наиболее близких вопросов
        closest_indices = np.argsort(similarities[0])[-3:][::-1]

        # Создаем векторное представление для исходного пользовательского запроса
        original_query_embedding = model.encode([user_query])

        # Вычисление близости между наиболее близкими вопросами и исходным запросом
        similarities_with_original_query = cosine_similarity(
            original_query_embedding, embeddings[closest_indices])

        # Находим индексы трех наиболее близких ответов к наиболее близким вопросам
        closest_answer_indices = np.argsort(
            similarities_with_original_query[0])[-3:][::-1]

        # Формирование и отправка ответа
        response = f"{user_name}!\n"

        # Находим индекс самого близкого ответа
        closest_answer_index = closest_answer_indices[0]

        response += f"{data.loc[closest_indices[closest_answer_index], 'Ответы']}\n\n"
        last_messages[user_id] = time.time()
        await message.reply(response)
        log_entry = f"User: {user_name} (ID: {user_id})\nQuestion: {user_query}\nBot Response: {response}\n"
        logging.info(log_entry)

        loop = asyncio.get_event_loop()
        loop.create_task(check_unanswered_messages(user_id))


last_messages = {}


async def check_unanswered_messages():
    while True:
        await asyncio.sleep(15)
        now = time.time()
        for user_id, last_message_time in last_messages.items():
            if now - last_message_time > 15:
                # Отправляем вопрос, если прошло более 15 секунд после последнего сообщения
                user_name = (await bot.get_chat(user_id)).first_name
                await bot.send_message(user_id, f"{user_name}, у Вас еще остались вопросы?")
                del last_messages[user_id]
                await asyncio.sleep(10)  # Ожидание 10 секунд
                if user_id in last_messages:
                    # Если пользователь ответил в течение 10 секунд, завершаем функцию
                    continue
                # Отправляем благодарность за проявленный интерес
                await bot.send_message(user_id, f"Спасибо, {user_name}, за проявленный интерес! Мы всегда готовы ответить на ваши вопросы.")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    loop = asyncio.get_event_loop()
    # Создаем задачу проверки неотвеченных сообщений
    loop.create_task(check_unanswered_messages())
    executor.start_polling(dp, skip_updates=True)
