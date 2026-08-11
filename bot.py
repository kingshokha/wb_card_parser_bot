import logging
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, BotCommandScopeDefault

from config import BOT_TOKEN, WB_SELLER_TOKEN
from wb_parser import extract_article, fetch_wb_card
from wb_api import upload_card, attach_photos_to_card, generate_short_vendor_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Состояния FSM для запроса артикула характеристик
class CharcState(StatesGroup):
    waiting_for_article = State()

session = AiohttpSession()
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

async def setup_bot_commands(bot: Bot):
    """Регистрирует список команд в интерфейсе Telegram (всплывающее меню при вводе /)."""
    commands = [
        BotCommand(command="start", description="Запустить бота и получить инструкцию"),
        BotCommand(command="help", description="Справка по использованию"),
        BotCommand(command="charcs", description="Посмотреть детальные характеристики товара")
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("Команды бота успешно зарегистрированы в меню Telegram.")
    except Exception as e:
        logger.error(f"Не удалось зарегистрировать меню команд Telegram: {e}")

async def safe_edit_text(message: types.Message, text: str, retries: int = 3):
    """Надежное редактирование текста сообщения с повторами при сетевых сбоях Telegram."""
    for attempt in range(retries):
        try:
            return await message.edit_text(text)
        except TelegramNetworkError as e:
            logger.warning(f"Сетевой сбой при редактировании сообщения (попытка #{attempt+1}): {e}")
            await asyncio.sleep(1.5)
        except TelegramAPIError as e:
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
        "1. Просто отправь мне ссылку на любой товар WB или его артикул — я создам карточку с фото в твоем кабинете.\n"
        "2. Используй команду <b>/charcs</b> или <b>«Характеристики карточки»</b>, чтобы узнать все характеристики любого товара.\n\n"
        "⚙️ <b>Возможности:</b>\n"
        "• Генерирует короткий артикул продавца\n"
        "• Оставляет бренд пустым\n"
        "• Парсит все свойства и характеристики\n"
        "• Передает вес в габариты упаковки (`weightBrutto`)\n"
        "• Передает числовые параметры (charcType=4) строгими числами\n"
        "• Загружает до 30 оригинальных фотографий в высоком качестве"
    )
    await safe_reply(message, welcome_text)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 <b>Инструкция:</b>\n"
        "• Отправьте ссылку на товар WB для копирования в ваш кабинет.\n"
        "• Напишите <code>/charcs [артикул]</code> или команду <b>«Характеристики карточки»</b> для просмотра списка характеристик."
    )
    await safe_reply(message, help_text)

async def show_card_characteristics(message: types.Message, text_with_art: str, status_msg: types.Message | None = None):
    """Извлекает артикул и отображает подробные характеристики товара."""
    article = extract_article(text_with_art)
    if not article:
        msg_text = "⚠️ Не удалось распознать артикул. Отправьте ссылку или 8-10 цифр артикула WB."
        if status_msg:
            await safe_edit_text(status_msg, msg_text)
        else:
            await safe_reply(message, msg_text)
        return

    if not status_msg:
        status_msg = await safe_reply(message, f"🔎 <b>Запрашиваю характеристики товара <code>{article}</code>...</b>")

    prod = await fetch_wb_card(article)
    if not prod:
        await safe_edit_text(status_msg, f"❌ <b>Не удалось найти характеристики для товара {article}.</b>")
        return

    title = prod.get("name", "Товар WB")
    subj_name = prod.get("subj_name", "Категория не определена")
    options = prod.get("options", [])

    options_text_list = []
    for opt in options:
        name = opt.get("name", "").strip()
        val = opt.get("value", "")
        if not name or val is None:
            continue
        val_str = ", ".join(val) if isinstance(val, list) else str(val)
        options_text_list.append(f"  • <b>{name}:</b> {val_str}")

    options_block = "\n".join(options_text_list) if options_text_list else "<i>Характеристики не найдены.</i>"

    report = (
        f"📋 <b>Характеристики товара (Артикул WB: <code>{article}</code>)</b>\n\n"
        f"• <b>Название:</b> {title}\n"
        f"• <b>Категория:</b> {subj_name}\n"
        f"• <b>Всего характеристик:</b> {len(options_text_list)} шт.\n\n"
        f"🛠 <b>Детальный список характеристик:</b>\n"
        f"{options_block}"
    )

    if len(report) > 4000:
        header = (
            f"📋 <b>Характеристики товара (Артикул WB: <code>{article}</code>)</b>\n\n"
            f"• <b>Название:</b> {title}\n"
            f"• <b>Категория:</b> {subj_name}\n"
            f"• <b>Всего характеристик:</b> {len(options_text_list)} шт.\n\n"
            f"🛠 <b>Часть 1:</b>"
        )
        await safe_edit_text(status_msg, header)
        chunk = ""
        for line in options_text_list:
            if len(chunk) + len(line) > 3800:
                await safe_reply(message, chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            await safe_reply(message, chunk)
    else:
        await safe_edit_text(status_msg, report)

# Хэндлер команды просмотров характеристик /charcs и текстовой «Характеристики карточки»
@dp.message(Command("charcs", "characteristics"))
@dp.message(F.text.ilike("Характеристики карточки%") | F.text.ilike("Характеристики%") | F.text.ilike("/Характеристики_карточки%"))
async def cmd_charcs(message: types.Message, state: FSMContext):
    text = message.text.strip()
    article = extract_article(text)

    if article:
        await state.clear()
        await show_card_characteristics(message, text)
    else:
        await state.set_state(CharcState.waiting_for_article)
        await safe_reply(
            message,
            "📥 <b>Пожалуйста, отправьте артикул или ссылку на товар WB</b>, чтобы посмотреть все его характеристики:"
        )

# Хэндлер ввода артикула в состоянии waiting_for_article
@dp.message(CharcState.waiting_for_article)
async def process_charc_article(message: types.Message, state: FSMContext):
    text = message.text.strip()
    await state.clear()
    await show_card_characteristics(message, text)

# Основной хэндлер для создания карточки при отправке ссылок/артикулов
@dp.message(F.text)
async def process_wb_link(message: types.Message, state: FSMContext):
    await state.clear()
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

    # Callback функция обновления текста
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
    logger.info("Бот запускается с автоматической регистрацией меню команд в Telegram...")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_bot_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
