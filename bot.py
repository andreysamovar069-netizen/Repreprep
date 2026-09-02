import os
import glob
import logging
import asyncio
from typing import List, Dict, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile
)
import yt_dlp

# --- НАСТРОЙКИ И ЛОГИРОВАНИЕ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТЕЛЕГРАМ_ТОКЕН_ЗДЕСЬ")
DOWNLOAD_DIR = "downloads"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаем папку для временных загрузок, если её нет
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ YT-DLP ---

def search_tracks(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Поиск треков на различных площадках с помощью yt-dlp.
    Сначала ищет по SoundCloud, если нет результатов — по YouTube/Music.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'default_search': 'auto',
    }

    results = []
    
    # Запрос для поиска: сначала пробуем SoundCloud, затем YouTube
    search_queries = [f"scsearch{limit}:{query}", f"ytsearch{limit}:{query}"]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for search_q in search_queries:
            try:
                info = ydl.extract_info(search_q, download=False)
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if not entry:
                            continue
                        results.append({
                            'id': entry.get('id'),
                            'url': entry.get('url') or entry.get('webpage_url'),
                            'title': entry.get('title', 'Без названия'),
                            'uploader': entry.get('uploader') or entry.get('artist') or 'Неизвестный исполнитель',
                            'duration': entry.get('duration', 0),
                            'source': 'SoundCloud' if 'scsearch' in search_q else 'YouTube/Yandex'
                        })
                        if len(results) >= limit:
                            break
            except Exception as e:
                logger.error(f"Ошибка поиска для {search_q}: {e}")
            
            if len(results) >= limit:
                break

    return results


def download_audio_file(url_or_query: str) -> Dict[str, Any]:
    """
    Загрузка и конвертация аудио в формат MP3.
    Возвращает словарь с путями к файлу и метаданными.
    """
    output_template = os.path.join(DOWNLOAD_DIR, '%(id)s_%(title)s.%(ext)s')
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url_or_query, download=True)
        filename = ydl.prepare_filename(info)
        # Так как postprocessor меняет расширение на mp3
        base_name, _ = os.path.splitext(filename)
        mp3_filename = f"{base_name}.mp3"

        return {
            'file_path': mp3_filename,
            'title': info.get('title', 'Аудиозапись'),
            'performer': info.get('uploader') or info.get('artist') or 'Исполнитель',
            'duration': int(info.get('duration', 0)) if info.get('duration') else None
        }


# --- ОБРАБОТЧИКИ КОМАНД ЛИЧНЫХ СООБЩЕНИЙ ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """
    Приветственное сообщение и инструкция по использованию.
    """
    text = (
        "👋 **Привет! Я музыкальный бот.**\n\n"
        "🎶 **Как мною пользоваться:**\n"
        "1. Отправь мне **ссылку** (SoundCloud, Yandex Music, YouTube и др.) или **название трека** прямо сюда.\n"
        "2. Вызови меня в **любом чате**, написав `@имя_бота название трека` (Инлайн-режим)!"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message):
    """
    Обработка текстового сообщения от пользователя (поиск или ссылка).
    """
    query = message.text.strip()
    status_msg = await message.answer("🔍 Ищу музыку, подождите...")

    # Если отправлена прямая ссылка
    if query.startswith("http://") or query.startswith("https://"):
        await status_msg.edit_text("⏳ Загружаю аудио по ссылке...")
        try:
            audio_data = await asyncio.to_thread(download_audio_file, query)
            audio_file = FSInputFile(audio_data['file_path'])

            await message.answer_audio(
                audio=audio_file,
                title=audio_data['title'],
                performer=audio_data['performer'],
                duration=audio_data['duration']
            )
            await status_msg.delete()
            # Удаляем временный файл
            if os.path.exists(audio_data['file_path']):
                os.remove(audio_data['file_path'])
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            await status_msg.edit_text("❌ Не удалось скачать аудио по данной ссылке.")
        return

    # Если отправлен обычный текст — запускаем поиск
    try:
        results = await asyncio.to_thread(search_tracks, query, 5)
        if not results:
            await status_msg.edit_text("😔 Ничего не найдено по вашему запросу.")
            return

        builder = InlineKeyboardMarkup(inline_keyboard=[])
        for idx, track in enumerate(results):
            # Сохраняем ссылку в callback_data (или укороченный идентификатор)
            btn = InlineKeyboardButton(
                text=f"🎵 {track['title'][:30]} - {track['uploader'][:20]}",
                callback_data=f"dl_{idx}"
            )
            builder.inline_keyboard.append([btn])

        # Сохраним результаты временным образом во вспомогательном сообщении
        await status_msg.edit_text(
            f"🔎 **Результаты поиска по запросу:** `{query}`\nВыберите трек для скачивания:",
            reply_markup=builder,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await status_msg.edit_text("❌ Произошла ошибка при поиске.")


@dp.callback_query(F.data.startswith("dl_"))
async def handle_download_callback(callback: CallbackQuery):
    """
    Обработка нажатия на кнопку скачивания выбранного трека из меню поиска.
    """
    await callback.answer("Начинаю скачивание...")
    await callback.message.edit_text("⏳ Идет скачивание и конвертация в MP3...")

    # Получаем исходный текст запроса из контекста сообщения
    try:
        # Для простоты заново вызываем скачивание по тексту нажатой кнопки
        button_text = ""
        for row in callback.message.reply_markup.inline_keyboard:
            if row[0].callback_data == callback.data:
                button_text = row[0].text.replace("🎵 ", "")
                break

        audio_data = await asyncio.to_thread(download_audio_file, f"ytsearch1:{button_text}")
        audio_file = FSInputFile(audio_data['file_path'])

        await callback.message.answer_audio(
            audio=audio_file,
            title=audio_data['title'],
            performer=audio_data['performer'],
            duration=audio_data['duration']
        )
        await callback.message.delete()

        if os.path.exists(audio_data['file_path']):
            os.remove(audio_data['file_path'])
    except Exception as e:
        logger.error(f"Ошибка при скачивании из callback: {e}")
        await callback.message.edit_text("❌ Ошибка при скачивании выбранного трека.")


# --- ОБРАБОТЧИК ИНЛАЙН-РЕЖИМА (@bot_username запрос) ---

@dp.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """
    Инлайн-поиск. Позволяет использовать бота в любом чате: @bot_name название
    """
    query = inline_query.query.strip()
    if not query:
        return

    try:
        # Получаем до 5 результатов
        tracks = await asyncio.to_thread(search_tracks, query, 5)
        results = []

        for idx, track in enumerate(tracks):
            duration_str = f"{track['duration'] // 60}:{track['duration'] % 60:02d}" if track['duration'] else "Неизвестно"
            
            # Текст сообщения, который отправится в чат при клике на результат
            content_text = (
                f"🎧 **{track['title']}**\n"
                f"👤 Исполнитель: {track['uploader']}\n"
                f"⏱ Длительность: {duration_str}\n"
                f"🔗 Источник: {track['url']}"
            )

            # Кнопка для быстрой отправки или перехода в бота
            bot_info = await bot.get_me()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📥 Скачать в боте",
                    url=f"https://t.me/{bot_info.username}?start=dl"
                )
            ]])

            article = InlineQueryResultArticle(
                id=str(idx),
                title=f"{track['title']}",
                description=f"👤 {track['uploader']} | ⏱ {duration_str} | [{track['source']}]",
                input_message_content=InputTextMessageContent(
                    message_text=content_text,
                    parse_mode="Markdown"
                ),
                reply_markup=keyboard
            )
            results.append(article)

        await inline_query.answer(results, cache_time=10)

    except Exception as e:
        logger.error(f"Ошибка инлайн-поиска: {e}")


# --- ТОЧКА ВХОДА ---

async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
