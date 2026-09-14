import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "peakflow.db")


def normalize_webapp_url(url: str) -> str:
    """Публичный URL Mini App: http(s) без хвостового слеша; иначе пусто."""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        return ""
    return url


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# 127.0.0.1 по умолчанию: доступ к боту только через reverse proxy (Caddy),
# чтобы порт не был открыт в интернет. Для нескольких ботов — свой порт каждому.
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "127.0.0.1")
WEBAPP_PORT = _parse_int(os.getenv("WEBAPP_PORT", "8080") or "0", 0)
WEBAPP_URL = normalize_webapp_url(os.getenv("WEBAPP_URL", ""))

# Часовой пояс (смещение от UTC в часах). По умолчанию UTC+5 (Екатеринбург).
# Примеры: Москва=3, Екатеринбург=5, Владивосток=10
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "5"))

# ============================================================================
# IDs семьи — все трое прописаны здесь
# ============================================================================
# Telegram ID ребёнка (получить через @userinfobot или /start с новым ботом)
CHILD_ID = int(os.getenv("CHILD_ID", "0"))

# Telegram ID родителей
PARENT_IDS = [
    int(x) for x in os.getenv("PARENT_IDS", "0,0").split(",") if x.strip().isdigit()
]

# Имя ребёнка (отображается в сообщениях)
CHILD_NAME = os.getenv("CHILD_NAME", "Ребёнок")

# Целевая ПСВ (от врача)
TARGET_PEF = int(os.getenv("TARGET_PEF", "260"))

# ============================================================================
# Нормы ПСВ по возрасту (справочник, если TARGET_PEF не задан)
# ============================================================================
PEF_NORM_BY_AGE = {
    4: 140, 5: 170, 6: 200, 7: 230, 8: 260, 9: 290,
    10: 320, 11: 350, 12: 380, 13: 410, 14: 440, 15: 470,
    16: 500, 17: 530, 18: 560,
}

# ============================================================================
# Пороги зон
# ============================================================================
ZONE_GREEN = 80   # ≥ 80% — хорошая компенсация
ZONE_YELLOW = 60  # 60–79% — внимание
ZONE_RED = 50     # < 60% — опасно

# ============================================================================
# Время напоминаний
# ============================================================================
REMINDER_MORNING_HOUR = 8    # утреннее напоминание
REMINDER_MORNING_DEADLINE = 10  # если к этому часу нет замера — уведомить
REMINDER_EVENING_HOUR = 20   # вечернее напоминание
REMINDER_EVENING_DEADLINE = 22  # если к этому часу нет замера — уведомить

# День недели для отчёта: 0=понедельник, 6=воскресенье
WEEKLY_REPORT_DAY = 6  # воскресенье
WEEKLY_REPORT_HOUR = 21  # в 21:00

# ============================================================================
# Helpers
# ============================================================================
def is_parent(user_id: int) -> bool:
    return user_id in PARENT_IDS


def is_child(user_id: int) -> bool:
    return user_id == CHILD_ID


def get_effective_target() -> int:
    return TARGET_PEF or 300  # fallback
