import logging
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramAPIError

from config import BOT_TOKEN, WB_SELLER_TOKEN
from wb_parser import extract_article, fetch_wb_card
from wb_api import upload_card, attach_photos_to_card, generate_short_vendor_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Инициализируем бота с кастомной сессией и авто-повторами при сбоях сети
session = AiohttpSession()
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

async def safe_edit_text(message: types.Message, text: str, retries: int = 3):
    """Надежное редактирование текста сообщения с повторами при сетевых сбоях Telegram."""
    for attempt in range(retries):
        try:
            return await message.edit_text(text)
        except TelegramNetworkError as e:
            logger.warning(f"Сетевой сбой при редактировании сообщения (попытка #{attempt+1}): {e}")
            await asyncio.sleep(1.5)
        except TelegramAPIError as e:
            # Игнорируем ошибку "текст сообщения не изменился"
            if "message is not modified" in str(e).lower():
                return message
            logger.warning(f"Ошибка Telegram API: {e}")
            break
        except Exception as e:
            logger.error(f"Исключение при безопасном edit_text: {e}")
            break
    return message

async def safe_reply(message: types.Message, text: str, retries: int = 3) -> types.Message | None:
    """Надежная отправка ответа пользователю с повторами при сетевых сбоях Telegram."""
    for attempt in range(retries):
        try:
            return await message.answer(text)
        except TelegramNetworkError as e:
            logger.warning(f"Сетевой сбой при отправке ответа (попытка #{attempt+1}): {e}")
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"Исключение при безопасном answer: {e}")
            break
    return None

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 <b>Привет! Я бот для автоматического создания карточек Wildberries.</b>\n\n"
        "📥 <b>Как использовать:</b>\n"
        "Просто отправь мне ссылку на любой товар WB или его артикул.\n"
        "Например: <code>https://www.wildberries.ru/catalog/12345678/detail.aspx</code>\n\n"
        "⚙️ <b>Возможности:</b>\n"
        "• Генерирует короткий артикул продавца\n"
        "• Оставляет бренд пустым\n"
        "• Парсит все свойства и характеристики\n"
        "• Добавляет 'Вес с упаковкой' в габариты товара\n"
        "• Передает числовые параметры (charcType=4) строгими числами\n"
        "• Загружает до 30 оригинальных фотографий в высоком качестве"
    )
    await safe_reply(message, welcome_text)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 <b>Инструкция:</b>\n"
        "Отправьте ссылку на товар WB. Бот скопирует карточку с характеристиками и фотографиями в ваш кабинет."
    )
    await safe_reply(message, help_text)

@dp.message(F.text)
async def process_wb_link(message: types.Message):
    text = message.text.strip()
    logger.info(f"Получено сообщение в Telegram: '{text}'")

    if not WB_SELLER_TOKEN or WB_SELLER_TOKEN == "YOUR_WB_CONTENT_API_TOKEN_HERE":
        await safe_reply(message, "⚠️ <b>Ошибка:</b> Не задан <code>WB_SELLER_TOKEN</code> в файле <code>.env</code>!")
        return

    article = extract_article(text)
    logger.info(f"Извлеченный артикул: {article}")

    if not article:
        await safe_reply(message, "⚠️ Не удалось найти артикул WB в вашем сообщении. Отправьте ссылку на товар.")
        return

    status_msg = await safe_reply(message, f"🔎 <b>Найден артикул WB: <code>{article}</code>. Извлекаю данные...</b>")
    if not status_msg:
        logger.error("Не удалось отправить начальное сообщение пользователю.")
        return

    # 1. Получаем данные товара с WB
    product_info = await fetch_wb_card(article)
    if not product_info:
        await safe_edit_text(status_msg, f"❌ <b>Не удалось получить данные о товаре {article}.</b> Проверьте ссылку.")
        return

    title = product_info.get("name", "Товар")
    subj_name = product_info.get("subj_name", "Категория не определена")
    pics_count = product_info.get("pics_count", 0)
    image_urls = product_info.get("image_urls", [])
    options_count = len(product_info.get("options", []))

    short_vendor_code = generate_short_vendor_code(title, article)

    preview_text = (
        f"⏳ <b>Создаю карточку в вашем кабинете WB...</b>\n\n"
        f"• <b>Товар:</b> {title}\n"
        f"• <b>Артикул WB:</b> <code>{article}</code>\n"
        f"• <b>Ваш артикул (SKU):</b> <code>{short_vendor_code}</code>\n"
        f"• <b>Категория:</b> {subj_name}\n"
        f"• <b>Характеристик:</b> {options_count} шт.\n"
        f"• <b>Фотографий к загрузке:</b> {pics_count} шт."
    )
    await safe_edit_text(status_msg, preview_text)

    # 2. Создаем карточку в кабинете WB
    success, result_msg, skipped_charcs = await upload_card(
        product_info=product_info,
        vendor_code=short_vendor_code
    )

    if not success:
        await safe_edit_text(status_msg, f"❌ <b>Ошибка при создании карточки:</b>\n<code>{result_msg}</code>")
        return

    # Callback функция обновления текста с устойчивостью к сбоям сети
    async def update_status_text(new_text: str):
        await safe_edit_text(status_msg, new_text)

    # 3. Привязываем фотографии к созданной карточке
    photos_attached = False
    photo_res_text = "Нет фото"
    if image_urls:
        await safe_edit_text(
            status_msg,
            f"📷 <b>Карточка создана! Ожидаю готовность в WB и загружаю {len(image_urls)} фото...</b>"
        )
        photos_attached, photo_res_text = await attach_photos_to_card(
            short_vendor_code,
            image_urls,
            status_callback=update_status_text
        )

    # Формируем блок пропущенных характеристик (если есть отключенные charcType=0)
    skipped_section = ""
    if skipped_charcs:
        skipped_items = "\n".join([f"  • <b>{c['name']}:</b> {c['value']}" for c in skipped_charcs])
        skipped_section = (
            f"\n\n⚠️ <b>Не переданы через API (заблокированы WB <code>charcType=0</code>):</b>\n"
            f"{skipped_items}\n"
            f"<i>💡 Вы можете заполнить их вручную при необходимости в кабинете WB.</i>"
        )

    # 4. Перезаписываем ИСХОДНОЕ сообщение финальным отчетом
    final_report = (
        f"🎉 <b>Карточка успешно создана!</b>\n\n"
        f"• <b>Название:</b> {title}\n"
        f"• <b>Артикул WB донора:</b> <code>{article}</code>\n"
        f"• <b>Ваш артикул продавца (SKU):</b> <code>{short_vendor_code}</code>\n"
        f"• <b>Бренд:</b> <i>[Пусто]</i>\n"
        f"• <b>Категория:</b> {subj_name}\n"
        f"• <b>Статус фотографий:</b> {'✅ ' + photo_res_text if photos_attached else '⚠️ ' + photo_res_text}"
        f"{skipped_section}\n\n"
        f"📌 <i>Карточка с фотографиями добавлена в ваш кабинет продавца WB.</i>"
    )
    await safe_edit_text(status_msg, final_report)

async def main():
    logger.info("Бот запускается с авто-повторами при сетевых сбоях...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
