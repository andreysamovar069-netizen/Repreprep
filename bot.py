import os
import logging
import asyncio
import math
from typing import List, Dict, Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН")
DOWNLOAD_DIR = "downloads"
ITEMS_PER_PAGE = 5

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

SEARCH_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def search_tracks_all(query: str, max_results: int = 25) -> List[Dict[str, Any]]:
    """Поиск треков на SoundCloud с обходом блокировок хостинга."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'default_search': 'auto',
    }

    results = []
    # Используем SoundCloud как основной стабильный источник для Render
    search_queries = [f"scsearch{max_results}:{query}", f"bcsearch{max_results}:{query}"]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for search_q in search_queries:
            try:
                info = ydl.extract_info(search_q, download=False)
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if not entry:
                            continue
                        
                        duration = entry.get('duration', 0)
                        try:
                            duration = int(duration) if duration else 0
                        except (ValueError, TypeError):
                            duration = 0

                        results.append({
                            'id': entry.get('id'),
                            'url': entry.get('url') or entry.get('webpage_url'),
                            'title': entry.get('title', 'Без названия'),
                            'uploader': entry.get('uploader') or entry.get('artist') or 'Исполнитель',
                            'duration': duration,
                            'source': 'SoundCloud' if 'scsearch' in search_q else 'Bandcamp'
                        })
            except Exception as e:
                logger.error(f"Ошибка поиска: {e}")

    return results


def download_audio_file(url_or_query: str) -> Dict[str, Any]:
    """Загрузка аудио в MP3 с дополнительными аргументами для стабильности."""
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
        'nocheckcertificate': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url_or_query, download=True)
        filename = ydl.prepare_filename(info)
        base_name, _ = os.path.splitext(filename)
        
        duration = info.get('duration', 0)
        try:
            duration = int(duration) if duration else None
        except (ValueError, TypeError):
            duration = None

        return {
            'file_path': f"{base_name}.mp3",
            'title': info.get('title', 'Аудиозапись'),
            'performer': info.get('uploader') or info.get('artist') or 'Исполнитель',
            'duration': duration
        }


def build_search_keyboard(query_key: str, page: int = 0) -> InlineKeyboardMarkup:
    tracks = SEARCH_CACHE.get(query_key, [])
    total_pages = max(1, math.ceil(len(tracks) / ITEMS_PER_PAGE))
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_tracks = tracks[start_idx:end_idx]

    keyboard = []
    for idx, track in enumerate(current_tracks):
        real_idx = start_idx + idx
        btn_text = f"🎵 {track['title'][:28]} - {track['uploader'][:15]}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"dl_{query_key}_{real_idx}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{query_key}_{page - 1}"))
    
    nav_buttons.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"page_{query_key}_{page + 1}"))

    keyboard.append(nav_buttons)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("👋 Отправь название песни/артиста или ссылку на SoundCloud/YouTube!")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message):
    query = message.text.strip()
    status_msg = await message.answer("🔍 Ищу треки...")

    if query.startswith("http://") or query.startswith("https://"):
        try:
            audio_data = await asyncio.to_thread(download_audio_file, query)
            await message.answer_audio(
                audio=FSInputFile(audio_data['file_path']),
                title=audio_data['title'],
                performer=audio_data['performer'],
                duration=audio_data['duration']
            )
            await status_msg.delete()
            if os.path.exists(audio_data['file_path']):
                os.remove(audio_data['file_path'])
        except Exception as e:
            logger.error(f"Ошибка загрузки по ссылке: {e}")
            await status_msg.edit_text("❌ Ошибка при скачивании по ссылке.")
        return

    tracks = await asyncio.to_thread(search_tracks_all, query, 25)
    if not tracks:
        await status_msg.edit_text("😔 Ничего не найдено.")
        return

    cache_key = str(abs(hash(query)))[:10]
    SEARCH_CACHE[cache_key] = tracks

    markup = build_search_keyboard(cache_key, page=0)
    await status_msg.edit_text(
        f"🔎 **Результаты по запросу:** `{query}`\n*(Найдено: {len(tracks)})*",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("page_"))
async def handle_page_change(callback: CallbackQuery):
    _, cache_key, page_str = callback.data.split("_")
    page = int(page_str)

    if cache_key not in SEARCH_CACHE:
        await callback.answer("Сессия поиска истекла. Напишите запрос заново.", show_alert=True)
        return

    markup = build_search_keyboard(cache_key, page=page)
    await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("dl_"))
async def handle_download_callback(callback: CallbackQuery):
    _, cache_key, idx_str = callback.data.split("_")
    idx = int(idx_str)

    tracks = SEARCH_CACHE.get(cache_key, [])
    if not tracks or idx >= len(tracks):
        await callback.answer("Ошибка: трек не найден.", show_alert=True)
        return

    track = tracks[idx]
    await callback.answer("Скачиваю...")
    progress_msg = await callback.message.answer("⏳ Скачиваю файл...")

    try:
        audio_data = await asyncio.to_thread(download_audio_file, track['url'])
        await callback.message.answer_audio(
            audio=FSInputFile(audio_data['file_path']),
            title=audio_data['title'],
            performer=audio_data['performer'],
            duration=audio_data['duration']
        )
        await progress_msg.delete()
        if os.path.exists(audio_data['file_path']):
            os.remove(audio_data['file_path'])
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        await progress_msg.edit_text("❌ Ошибка при скачивании трека.")


@dp.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    query = inline_query.query.strip()
    if not query:
        return

    try:
        tracks = await asyncio.to_thread(search_tracks_all, query, 10)
        results = []

        for idx, track in enumerate(tracks):
            dur = track['duration']
            dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "--:--"

            content = (
                f"🎧 **{track['title']}**\n"
                f"👤 Исполнитель: {track['uploader']}\n"
                f"🔗 {track['url']}"
            )
            
            bot_info = await bot.get_me()
            btn = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📥 Скачать в боте", url=f"https://t.me/{bot_info.username}")
            ]])

            results.append(InlineQueryResultArticle(
                id=str(idx),
                title=track['title'],
                description=f"👤 {track['uploader']} | ⏱ {dur_str}",
                input_message_content=InputTextMessageContent(message_text=content, parse_mode="Markdown"),
                reply_markup=btn
            ))

        await inline_query.answer(results, cache_time=10)
    except Exception as e:
        logger.error(f"Ошибка инлайн-поиска: {e}")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
                        
