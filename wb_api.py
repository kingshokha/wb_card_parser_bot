import re
import logging
import asyncio
import time
import aiohttp
from config import WB_SELLER_TOKEN

logger = logging.getLogger(__name__)

BASE_URL = "https://content-api.wildberries.ru"

TRANSLIT_MAP = {
    'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'е':'e', 'ё':'yo', 'ж':'zh',
    'з':'z', 'и':'i', 'й':'y', 'к':'k', 'л':'l', 'м':'m', 'н':'n', 'о':'o',
    'п':'p', 'р':'r', 'с':'s', 'т':'t', 'у':'u', 'ф':'f', 'х':'h', 'ц':'ts',
    'ч':'ch', 'ш':'sh', 'щ':'sch', 'ъ':'', 'ы':'y', 'ь':'', 'э':'e', 'ю':'yu', 'я':'ya'
}

def transliterate(text: str) -> str:
    res = []
    for char in text.lower():
        res.append(TRANSLIT_MAP.get(char, char))
    return "".join(res)

def generate_short_vendor_code(title: str, article: int) -> str:
    """Генерирует гарантированно уникальный супер-короткий артикул продавца из названия товара."""
    clean_title = re.sub(r'[^a-zA-Zа-яА-Я0-9\s]', '', title).strip()
    words = clean_title.split()
    
    short_words = words[:2] if words else ["item"]
    short_title = "-".join(short_words)
    
    transliterated = transliterate(short_title)
    transliterated = re.sub(r'-+', '-', transliterated).strip('-')
    
    rand_suffix = f"{int(time.time()) % 10000:04d}"
    code = f"{transliterated[:10]}-{rand_suffix}".upper()
    return code

def get_headers() -> dict:
    return {
        "Authorization": WB_SELLER_TOKEN,
        "Content-Type": "application/json"
    }

def parse_numeric_value(val_str: str) -> int | float | None:
    """Извлекает числовое значение (int или float) из строки."""
    if val_str is None:
        return None
    val_clean = str(val_str).replace(',', '.').strip()
    match = re.search(r'[-+]?\d*\.?\d+', val_clean)
    if match:
        num_str = match.group(0)
        if '.' in num_str:
            try:
                return float(num_str)
            except ValueError:
                pass
        else:
            try:
                return int(num_str)
            except ValueError:
                pass
    return None

async def generate_barcodes(count: int = 1) -> list[str]:
    """Генерирует уникальные штрихкоды через WB API."""
    url = f"{BASE_URL}/content/v2/barcodes"
    payload = {"count": count}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=get_headers(), json=payload, timeout=10) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    return data.get("data", [])
    except Exception as e:
        logger.error(f"Исключение при генерации баркодов: {e}")
    
    return []

async def get_category_characteristics(subject_id: int) -> list[dict]:
    """Получает справочник характеристик для категории WB."""
    url = f"{BASE_URL}/content/v2/object/charcs/{subject_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=get_headers(), timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
    except Exception as e:
        logger.error(f"Ошибка при получении характеристик категории {subject_id}: {e}")
    
    return []

def map_characteristics(donor_options: list[dict], category_charcs: list[dict]) -> tuple[list[dict], dict]:
    """
    Сопоставляет характеристики донора с официальным справочником WB.
    1. Заполняет блок dimensions (длина, ширина, высота).
    2. Исключает характеристики габаритов и веса из массива characteristics,
       так как WB проверяет их через dimensions.
    3. Для остальных числовых характеристик (charcType == 4) строго передает числа (int/float).
    """
    charc_lookup = {}
    for c in category_charcs:
        c_name = c.get("name", "").strip().lower()
        if c_name:
            charc_lookup[c_name] = c

    mapped_charcs = []
    dimensions = {"length": 10, "width": 10, "height": 10}

    DIM_WEIGHT_KEYWORDS = ["длина", "ширина", "высота", "глубина", "вес"]

    for opt in donor_options:
        raw_name = opt.get("name", "").strip()
        raw_val = opt.get("value", "")

        if not raw_name or raw_val is None:
            continue

        name_lower = raw_name.lower()

        if "длина" in name_lower:
            num = parse_numeric_value(str(raw_val))
            if num is not None and num > 0: dimensions["length"] = int(num)
        elif "ширина" in name_lower:
            num = parse_numeric_value(str(raw_val))
            if num is not None and num > 0: dimensions["width"] = int(num)
        elif "высота" in name_lower or "глубина" in name_lower:
            num = parse_numeric_value(str(raw_val))
            if num is not None and num > 0: dimensions["height"] = int(num)

        if any(kw in name_lower for kw in DIM_WEIGHT_KEYWORDS):
            continue

        if name_lower in charc_lookup:
            charc_info = charc_lookup[name_lower]
            charc_id = charc_info.get("charcID")
            charc_type = charc_info.get("charcType", 1)

            if charc_type == 4:
                if isinstance(raw_val, list):
                    nums = [parse_numeric_value(v) for v in raw_val]
                    vals = [n for n in nums if n is not None]
                else:
                    num = parse_numeric_value(str(raw_val))
                    vals = [num] if num is not None else []
            else:
                if isinstance(raw_val, str):
                    vals = [v.strip() for v in re.split(r'[;,]', raw_val) if v.strip()]
                elif isinstance(raw_val, list):
                    vals = [str(v) for v in raw_val]
                else:
                    vals = [str(raw_val)]

            if vals and charc_id:
                mapped_charcs.append({
                    "id": charc_id,
                    "value": vals
                })

    return mapped_charcs, dimensions

async def upload_card(product_info: dict, vendor_code: str) -> tuple[bool, str]:
    """
    Создает новую карточку товара в кабинете продавца через WB Content API v2.
    """
    subject_id = product_info.get("subject_id", 105)

    category_charcs = await get_category_characteristics(subject_id)
    donor_options = product_info.get("options", [])
    
    mapped_charcs, dimensions = map_characteristics(donor_options, category_charcs)

    parsed_sizes = product_info.get("sizes", [])
    sizes_count = max(1, len(parsed_sizes))
    generated_barcodes = await generate_barcodes(count=sizes_count)

    sizes_payload = []
    for idx, size_item in enumerate(parsed_sizes):
        barcode = generated_barcodes[idx] if idx < len(generated_barcodes) else ""
        sizes_payload.append({
            "techSize": size_item.get("tech_size", "0"),
            "wbSize": size_item.get("wb_size", ""),
            "price": max(100, product_info.get("price", 1000)),
            "skus": [barcode] if barcode else []
        })

    if not sizes_payload:
        sizes_payload = [{
            "techSize": "0",
            "price": max(100, product_info.get("price", 1000)),
            "skus": generated_barcodes[:1] if generated_barcodes else []
        }]

    variant_data = {
        "vendorCode": vendor_code,
        "title": product_info.get("name", "Товар")[:60],
        "description": product_info.get("description", "Описание товара")[:5000],
        "brand": "",
        "dimensions": dimensions,
        "characteristics": mapped_charcs,
        "sizes": sizes_payload
    }

    payload = [
        {
            "subjectID": subject_id,
            "variants": [variant_data]
        }
    ]

    url = f"{BASE_URL}/content/v2/cards/upload"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=get_headers(), json=payload, timeout=15) as resp:
                resp_data = await resp.json()
                if resp.status in (200, 201) and not resp_data.get("error"):
                    return True, f"Карточка создана!"
                else:
                    error_msg = resp_data.get("errorText") or resp_data.get("additionalErrors") or str(resp_data)
                    return False, f"Ошибка от WB API: {error_msg}"
    except Exception as e:
        logger.exception("Ошибка при отправке карточки в WB Content API")
        return False, f"Исключение при отправке запроса: {e}"

async def get_card_nmid_by_vendor_code(vendor_code: str) -> int | None:
    """
    Ищет nmID созданной карточки по её СТРОГОМУ артикулу продавца (vendorCode).
    ВАЖНО: Возвращает nmID ТОЛЬКО при точном совпадении vendorCode!
    """
    url = f"{BASE_URL}/content/v2/get/cards/list"
    payload = {
        "settings": {
            "cursor": {"limit": 50},
            "filter": {
                "withPhoto": -1,
                "textSearch": vendor_code
            }
        }
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=get_headers(), json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cards = data.get("cards", [])
                    target_code = vendor_code.strip().upper()
                    for card in cards:
                        c_code = str(card.get("vendorCode", "")).strip().upper()
                        if c_code == target_code:
                            return card.get("nmID")
    except Exception as e:
        logger.error(f"Ошибка при поиске nmID для {vendor_code}: {e}")
    return None

async def attach_photos_to_card(vendor_code: str, image_urls: list[str]) -> tuple[bool, str]:
    """
    Загружает фотографии товара через WB Content Media API (POST /content/v3/media/file).
    1. Ждет появление новой карточки с ТОЧНЫМ vendorCode и получает её nmID.
    2. Скачивает байты изображений и загружает файл за файлом с X-Nm-Id и X-Photo-Number.
    """
    if not image_urls:
        return True, "Нет фотографий для загрузки."

    nm_id = None
    # Ждем до 30 секунд (10 попыток по 3 сек), пока WB зарегистрирует новую карточку
    for attempt in range(10):
        await asyncio.sleep(3)
        nm_id = await get_card_nmid_by_vendor_code(vendor_code)
        if nm_id:
            logger.info(f"Найдена созданная карточка nmID: {nm_id} для vendorCode: {vendor_code} (попытка #{attempt+1})")
            break

    if not nm_id:
        logger.warning(f"Не удалось найти nmID для артикула продавца {vendor_code} после 10 попыток.")
        return False, "Карточка еще обрабатывается серверами WB. Фото появятся в течение пары минут."

    logger.info(f"Начало загрузки {len(image_urls)} фото для карточки nmID: {nm_id}")
    uploaded_count = 0
    url = f"{BASE_URL}/content/v3/media/file"

    async with aiohttp.ClientSession() as session:
        for idx, img_url in enumerate(image_urls[:10]):
            try:
                async with session.get(img_url, timeout=10) as img_resp:
                    if img_resp.status != 200:
                        continue
                    img_bytes = await img_resp.read()

                headers = {
                    "Authorization": WB_SELLER_TOKEN,
                    "X-Nm-Id": str(nm_id),
                    "X-Photo-Number": str(idx + 1)
                }

                form = aiohttp.FormData()
                form.add_field('uploadfile', img_bytes, filename=f'photo_{idx+1}.jpg', content_type='image/jpeg')

                async with session.post(url, headers=headers, data=form, timeout=15) as upload_resp:
                    if upload_resp.status in (200, 201):
                        resp_json = await upload_resp.json()
                        if not resp_json.get("error"):
                            uploaded_count += 1
            except Exception as e:
                logger.error(f"Ошибка при загрузке фото #{idx+1} для nmID {nm_id}: {e}")

            await asyncio.sleep(0.5)

    if uploaded_count > 0:
        return True, f"Загружено {uploaded_count} из {len(image_urls)} фото!"
    else:
        return False, "Не удалось загрузить фотографии."
