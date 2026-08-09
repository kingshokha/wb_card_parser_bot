import re
import logging
import aiohttp
from config import WB_SELLER_TOKEN

logger = logging.getLogger(__name__)

def extract_article(text: str) -> int | None:
    """Извлекает артикул Wildberries из ссылки или текста."""
    match = re.search(r'catalog/(\d+)/detail', text) or re.search(r'(\d{7,10})', text)
    if match:
        return int(match.group(1))
    return None

def get_basket_host(vol: int) -> str:
    """Определяет домен basket-XX на основе значения vol."""
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
    else: return "basket-21.wbbasket.ru"

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

    return 105 # Фоллбэк ID если категория не найдена

async def fetch_wb_card(article: int) -> dict | None:
    """
    Универсальный парсер карточек Wildberries.
    Работает через CDN basket JSON и поиск по сайту.
    """
    vol = article // 100000
    part = article // 1000

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9"
    }

    product_info = {}
    card_data = None
    working_basket = get_basket_host(vol)

    async with aiohttp.ClientSession(headers=headers) as session:
        # Стратегия 1: Прямой запрос к CDN card.json
        cdn_url = f"https://{working_basket}/vol{vol}/part{part}/{article}/info/ru/card.json"
        try:
            async with session.get(cdn_url, timeout=3) as resp:
                if resp.status == 200:
                    card_data = await resp.json()
        except Exception:
            pass

        # Стратегия 2: Если прямой basket не сработал — перебираем сервера 01..25
        if not card_data:
            for b_num in range(1, 26):
                b_host = f"basket-{b_num:02d}.wbbasket.ru"
                alt_cdn_url = f"https://{b_host}/vol{vol}/part{part}/{article}/info/ru/card.json"
                try:
                    async with session.get(alt_cdn_url, timeout=2) as resp:
                        if resp.status == 200:
                            card_data = await resp.json()
                            working_basket = b_host
                            break
                except Exception:
                    pass

        if not card_data:
            logger.error(f"Не удалось найти данные товара {article} ни на одном сервере CDN WB.")
            return None

        # Сбор основных полей из card.json
        name = card_data.get("imt_name") or card_data.get("name") or "Товар WB"
        subj_name = card_data.get("subj_name", "")
        brand = card_data.get("selling", {}).get("brand_name") or card_data.get("brand", "Без бренда")
        description = card_data.get("description", "")
        options = card_data.get("options", [])
        grouped_options = card_data.get("grouped_options", [])

        # Проверка и сбор доступных ссылок на фотографии
        image_urls = []
        for img_idx in range(1, 15):
            img_url = f"https://{working_basket}/vol{vol}/part{part}/{article}/images/big/{img_idx}.webp"
            try:
                async with session.head(img_url, timeout=2) as img_resp:
                    if img_resp.status == 200:
                        image_urls.append(img_url)
                    else:
                        break
            except Exception:
                break

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

        # Формируем размеры
        product_info["sizes"] = [{"tech_size": "0", "wb_size": "", "orig_price": 1000}]

    return product_info
