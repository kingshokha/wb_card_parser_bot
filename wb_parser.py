import re
import logging
import asyncio
import aiohttp
from config import WB_SELLER_TOKEN

logger = logging.getLogger(__name__)

def extract_article(text: str) -> int | None:
    """Извлекает артикул Wildberries из ссылки любого формата или текста."""
    if not text:
        return None

    # 1. Поиск по шаблону /catalog/123456789/
    match_catalog = re.search(r'catalog/(\d+)/', text)
    if match_catalog:
        return int(match_catalog.group(1))

    # 2. Поиск по параметрам product/123456789 или card=123456789 или nm=123456789
    match_param = re.search(r'(?:product|card|nm)[=/](\d+)', text, re.IGNORECASE)
    if match_param:
        return int(match_param.group(1))

    # 3. Поиск точного совпадения цифр
    match_digits = re.search(r'\b(\d{5,10})\b', text)
    if match_digits:
        return int(match_digits.group(1))

    return None

def get_basket_host(vol: int) -> str:
    """Определяет стартовый домен basket-XX на основе значения vol."""
    if 0 <= vol <= 143: return "basket-01.wbbasket.ru"
    elif 144 <= vol <= 287: return "basket-02.wbbasket.ru"
    elif 288 <= vol <= 431: return "basket-03.wbbasket.ru"
    elif 432 <= vol <= 719: return "basket-04.wbbasket.ru"
    elif 720 <= vol <= 1007: return "basket-05.wbbasket.ru"
    elif 1008 <= vol <= 1061: return "basket-06.wbbasket.ru"
    elif 1062 <= vol <= 1115: return "basket-07.wbbasket.ru"
    elif 1116 <= vol <= 1169: return "basket-08.wbbasket.ru"
    elif 1170 <= vol <= 1313: return "basket-09.wbbasket.ru"
    elif 1314 <= vol <= 1601: return "basket-10.wbbasket.ru"
    elif 1602 <= vol <= 1655: return "basket-11.wbbasket.ru"
    elif 1656 <= vol <= 1919: return "basket-12.wbbasket.ru"
    elif 1920 <= vol <= 2045: return "basket-13.wbbasket.ru"
    elif 2046 <= vol <= 2189: return "basket-14.wbbasket.ru"
    elif 2190 <= vol <= 2405: return "basket-15.wbbasket.ru"
    elif 2406 <= vol <= 2621: return "basket-16.wbbasket.ru"
    elif 2622 <= vol <= 2837: return "basket-17.wbbasket.ru"
    elif 2838 <= vol <= 3053: return "basket-18.wbbasket.ru"
    elif 3054 <= vol <= 3269: return "basket-19.wbbasket.ru"
    elif 3270 <= vol <= 3485: return "basket-20.wbbasket.ru"
    else: return f"basket-{(vol // 200) + 1:02d}.wbbasket.ru"

async def get_subject_id_by_name(subj_name: str) -> int:
    """Ищет subjectID по названию категории в WB Content API."""
    if not WB_SELLER_TOKEN or not subj_name:
        return 105

    url = f"https://content-api.wildberries.ru/content/v2/object/all?name={subj_name}"
    headers = {"Authorization": WB_SELLER_TOKEN}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", [])
                    for item in items:
                        if item.get("subjectName", "").lower() == subj_name.lower():
                            return item.get("subjectID", 105)
                    if items:
                        return items[0].get("subjectID", 105)
    except Exception as e:
        logger.warning(f"Не удалось найти subjectID по названию '{subj_name}': {e}")

    return 105

async def fetch_wb_card(article: int) -> dict | None:
    """
    Универсальный парсер карточек Wildberries.
    Использует параллельный сканер по корзинам 01..80 для мгновенного поиска любых новых товаров.
    Собирает до 30 фотографий (максимум WB).
    """
    logger.info(f"Начало парсинга товара по артикулу: {article}")
    vol = article // 100000
    part = article // 1000

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9"
    }

    card_data = None
    working_basket = None

    async with aiohttp.ClientSession(headers=headers) as session:
        async def check_basket(b_num: int):
            b_host = f"basket-{b_num:02d}.wbbasket.ru"
            cdn_url = f"https://{b_host}/vol{vol}/part{part}/{article}/info/ru/card.json"
            try:
                async with session.get(cdn_url, timeout=2.5) as resp:
                    if resp.status == 200:
                        c_json = await resp.json()
                        return c_json, b_host
            except Exception:
                pass
            return None, None

        # 1. Попытка на расчетном сервере
        calc_host = get_basket_host(vol)
        c_url = f"https://{calc_host}/vol{vol}/part{part}/{article}/info/ru/card.json"
        try:
            async with session.get(c_url, timeout=2) as resp:
                if resp.status == 200:
                    card_data = await resp.json()
                    working_basket = calc_host
        except Exception:
            pass

        # 2. Перебор серверов 01..80 если нужно
        if not card_data:
            tasks = [check_basket(i) for i in range(1, 81)]
            results = await asyncio.gather(*tasks)
            for c_json, b_host in results:
                if c_json:
                    card_data = c_json
                    working_basket = b_host
                    break

        if not card_data:
            logger.error(f"Не удалось найти данные товара {article} ни на одном сервере CDN WB (1..80).")
            return None

        # Сбор основных полей из card.json
        name = card_data.get("imt_name") or card_data.get("name") or "Товар WB"
        subj_name = card_data.get("subj_name", "")
        brand = card_data.get("selling", {}).get("brand_name") or card_data.get("brand", "")
        description = card_data.get("description", "")
        options = card_data.get("options", [])
        grouped_options = card_data.get("grouped_options", [])

        # Проверка всех доступных фотографий товара (до 30 штук — лимит WB)
        image_urls = []
        for img_idx in range(1, 31):
            img_url = f"https://{working_basket}/vol{vol}/part{part}/{article}/images/big/{img_idx}.webp"
            try:
                async with session.head(img_url, timeout=2) as img_resp:
                    if img_resp.status == 200:
                        image_urls.append(img_url)
                    else:
                        break
            except Exception:
                break

        product_info = {}
        product_info["article"] = article
        product_info["name"] = name
        product_info["subj_name"] = subj_name
        product_info["brand"] = brand
        product_info["description"] = description if description else f"Товар {name}"
        product_info["options"] = options
        product_info["grouped_options"] = grouped_options
        product_info["image_urls"] = image_urls
        product_info["pics_count"] = len(image_urls)
        product_info["price"] = 1000

        # Определяем subject_id
        subject_id = card_data.get("subj_root_id") or card_data.get("subj_id")
        if not subject_id and subj_name:
            subject_id = await get_subject_id_by_name(subj_name)
        product_info["subject_id"] = subject_id or 105

        product_info["sizes"] = [{"tech_size": "0", "wb_size": "", "orig_price": 1000}]

    logger.info(f"Успешно спарсен товар: '{name}', Категория: '{subj_name}', Фотографий: {len(image_urls)}")
    return product_info
