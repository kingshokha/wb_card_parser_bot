import asyncio
import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin

import aiohttp

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path(__file__).resolve().parent / "downloads"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

# Видео WB хранятся на отдельных серверах videonme-basket-XX с собственной схемой путей:
# vol = артикул % 144, part = артикул // 10000, номер корзины = vol // 12 + 1.
VIDEO_HLS_PATH = "hls/1440p/index.m3u8"
VIDEO_MP4_PATH = "mp4/360p/1.mp4"
VIDEO_BASKETS_TO_SCAN = 20

StatusCallback = Callable[[str], Awaitable[None]] | None


def get_video_base_urls(article: int) -> list[str]:
    """Возвращает кандидаты базовых URL видео: сначала расчетный сервер, затем остальные."""
    vol = article % 144
    part = article // 10000
    calc_basket = vol // 12 + 1
    baskets = [calc_basket] + [b for b in range(1, VIDEO_BASKETS_TO_SCAN + 1) if b != calc_basket]
    return [
        f"https://videonme-basket-{b:02d}.wbbasket.ru/vol{vol}/part{part}/{article}/"
        for b in baskets
    ]


async def _url_exists(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return resp.status == 200
    except Exception:
        return False


async def find_video_base_url(session: aiohttp.ClientSession, article: int) -> str | None:
    """Находит сервер, на котором лежит видео товара (HLS или MP4)."""
    candidates = get_video_base_urls(article)

    # 1. Расчетный сервер
    if await _url_exists(session, candidates[0] + VIDEO_HLS_PATH):
        return candidates[0]

    # 2. Параллельный перебор остальных серверов
    results = await asyncio.gather(*[
        _url_exists(session, base + VIDEO_HLS_PATH) for base in candidates[1:]
    ])
    for base, ok in zip(candidates[1:], results):
        if ok:
            return base

    # 3. Товары только с MP4 без HLS
    results = await asyncio.gather(*[
        _url_exists(session, base + VIDEO_MP4_PATH) for base in candidates
    ])
    for base, ok in zip(candidates, results):
        if ok:
            return base

    return None


async def _download_file(session: aiohttp.ClientSession, url: str, dest: Path) -> bool:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                logger.warning(f"Не удалось скачать {url}: HTTP {resp.status}")
                return False
            dest.write_bytes(await resp.read())
            return True
    except Exception as e:
        logger.warning(f"Ошибка скачивания {url}: {e}")
        return False


async def _download_hls(session: aiohttp.ClientSession, playlist_url: str, dest_ts: Path) -> bool:
    """Скачивает все сегменты HLS-плейлиста и склеивает их в один .ts файл."""
    try:
        async with session.get(playlist_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return False
            playlist = await resp.text()
    except Exception as e:
        logger.warning(f"Не удалось получить плейлист {playlist_url}: {e}")
        return False

    segment_urls = [
        urljoin(playlist_url, line.strip())
        for line in playlist.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not segment_urls:
        return False

    semaphore = asyncio.Semaphore(6)

    async def fetch_segment(url: str) -> bytes | None:
        async with semaphore:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        return await resp.read()
            except Exception as e:
                logger.warning(f"Ошибка скачивания сегмента {url}: {e}")
            return None

    segments = await asyncio.gather(*[fetch_segment(u) for u in segment_urls])
    if any(s is None for s in segments):
        logger.warning(f"Не все сегменты видео скачаны ({playlist_url})")
        return False

    with dest_ts.open("wb") as f:
        for seg in segments:
            f.write(seg)
    return True


async def _remux_to_mp4(src_ts: Path, dest_mp4: Path) -> bool:
    """Перепаковывает .ts в .mp4 без перекодирования, если в системе есть ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-loglevel", "error", "-i", str(src_ts),
        "-c", "copy", "-movflags", "+faststart", str(dest_mp4),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(f"ffmpeg не смог перепаковать видео: {stderr.decode(errors='ignore').strip()}")
        dest_mp4.unlink(missing_ok=True)
        return False
    return True


async def download_video(session: aiohttp.ClientSession, article: int, folder: Path) -> Path | None:
    """
    Скачивает видео товара в максимальном доступном качестве.
    1. HLS 1440p: сегменты склеиваются в .ts и при наличии ffmpeg перепаковываются в .mp4.
    2. Запасной вариант — прямой MP4 360p.
    """
    base_url = await find_video_base_url(session, article)
    if not base_url:
        logger.info(f"Видео для товара {article} не найдено на серверах WB.")
        return None

    video_ts = folder / "video.ts"
    video_mp4 = folder / "video.mp4"

    if await _download_hls(session, base_url + VIDEO_HLS_PATH, video_ts):
        if await _remux_to_mp4(video_ts, video_mp4):
            video_ts.unlink(missing_ok=True)
            return video_mp4
        return video_ts

    if await _download_file(session, base_url + VIDEO_MP4_PATH, video_mp4):
        return video_mp4

    return None


async def download_product_media(
    article: int,
    image_urls: list[str],
    has_video: bool = True,
    status_callback: StatusCallback = None,
) -> tuple[Path, list[Path], Path | None]:
    """
    Скачивает все фото и видео товара в папку downloads/<артикул>/.
    Возвращает (папка, список_фото, видео_или_None).
    """
    folder = DOWNLOADS_DIR / str(article)
    folder.mkdir(parents=True, exist_ok=True)

    # Удаляем медиа от прошлой загрузки, чтобы в папке не остались устаревшие фото
    for old in list(folder.glob("photo_*")) + list(folder.glob("video.*")):
        old.unlink(missing_ok=True)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        if status_callback:
            await status_callback(f"📷 <b>Скачиваю {len(image_urls)} фото...</b>")

        semaphore = asyncio.Semaphore(8)

        async def fetch_photo(idx: int, url: str) -> Path | None:
            ext = Path(url).suffix or ".webp"
            dest = folder / f"photo_{idx:02d}{ext}"
            async with semaphore:
                return dest if await _download_file(session, url, dest) else None

        results = await asyncio.gather(*[
            fetch_photo(i, url) for i, url in enumerate(image_urls, start=1)
        ])
        photos = [p for p in results if p]

        video = None
        if has_video:
            if status_callback:
                await status_callback(
                    f"🎬 <b>Фото скачаны ({len(photos)} шт.). Скачиваю видео...</b>"
                )
            video = await download_video(session, article, folder)

    logger.info(f"Медиа товара {article} сохранены в {folder}: фото {len(photos)}, видео {'да' if video else 'нет'}")
    return folder, photos, video
