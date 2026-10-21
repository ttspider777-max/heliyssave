import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# главный админ: всегда имеет доступ к /admin и может назначать других админов
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "8717087613"))
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "savemod.db"))
# сколько дней хранить кэш сообщений (нужен, чтобы показывать удалённые)
MESSAGE_TTL_DAYS = int(os.getenv("MESSAGE_TTL_DAYS", "30"))
