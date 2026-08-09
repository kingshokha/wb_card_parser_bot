import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, WB_SELLER_TOKEN
from wb_parser import extract_article, fetch_wb_card
from wb_api import upload_card, attach_photos_to_card, generate_short_vendor_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

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
        "• Выводит список характеристик, пропущенных API WB (maxCount=0) для ручного заполнения\n"
        "• Загружает до 30 оригинальных фотографий в высоком качестве"
    )
    await message.answer(welcome_text)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 <b>Инструкция:</b>\n"
        "Отправьте ссылку на товар WB. Бот скопирует карточку с характеристиками и фотографиями в ваш кабинет."
    )
    await message.answer(help_text)

@dp.message(F.text)
async def process_wb_link(message: types.Message):
    text = message.text.strip()
    logger.info(f"Получено сообщение в Telegram: '{text}'")

    if not WB_SELLER_TOKEN or WB_SELLER_TOKEN == "YOUR_WB_CONTENT_API_TOKEN_HERE":
        await message.answer("⚠️ <b>Ошибка:</b> Не задан <code>WB_SELLER_TOKEN</code> в файле <code>.env</code>!")
        return

    article = extract_article(text)
    logger.info(f"Извлеченный артикул: {article}")

    if not article:
        await message.answer("⚠️ Не удалось найти артикул WB в вашем сообщении. Отправьте ссылку на товар.")
        return

    status_msg = await message.answer(f"🔎 <b>Найден артикул WB: <code>{article}</code>. Извлекаю данные...</b>")

    # 1. Получаем данные товара с WB
    product_info = await fetch_wb_card(article)
    if not product_info:
        await status_msg.edit_text(f"❌ <b>Не удалось получить данные о товаре {article}.</b> Проверьте ссылку.")
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
    await status_msg.edit_text(preview_text)

    # 2. Создаем карточку в кабинете WB
    success, result_msg, skipped_charcs = await upload_card(
        product_info=product_info,
        vendor_code=short_vendor_code
    )

    if not success:
        await status_msg.edit_text(f"❌ <b>Ошибка при создании карточки:</b>\n<code>{result_msg}</code>")
        return

    # Callback функция обновления текста
    async def update_status_text(new_text: str):
        try:
            await status_msg.edit_text(new_text)
        except Exception:
            pass

    # 3. Привязываем фотографии к созданной карточке
    photos_attached = False
    photo_res_text = "Нет фото"
    if image_urls:
        await status_msg.edit_text(
            f"📷 <b>Карточка создана! Ожидаю готовность в WB и загружаю {len(image_urls)} фото...</b>"
        )
        photos_attached, photo_res_text = await attach_photos_to_card(
            short_vendor_code,
            image_urls,
            status_callback=update_status_text
        )

    # Формируем блок пропущенных характеристик (maxCount == 0)
    skipped_section = ""
    if skipped_charcs:
        skipped_items = "\n".join([f"  • <b>{c['name']}:</b> {c['value']}" for c in skipped_charcs])
        skipped_section = (
            f"\n\n⚠️ <b>Не переданы через API (заблокированы WB <code>maxCount=0</code>):</b>\n"
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
    await status_msg.edit_text(final_report)

async def main():
    logger.info("Бот запускается с выводом пропущенных характеристик maxCount=0...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
