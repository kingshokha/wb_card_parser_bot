import os
import sys
from dotenv import load_dotenv

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WB_SELLER_TOKEN = os.getenv("WB_SELLER_TOKEN", "").strip()
