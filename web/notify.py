"""Telegram-уведомления, отправляемые из Mini App (паритет с ботом)."""
import asyncio
import logging

logger = logging.getLogger(__name__)

# Upper bound for a single Telegram send so a hung API call cannot hold an
# HTTP request (or a background task) open forever.
_SEND_TIMEOUT = 5.0


def _zone(value: int, target: int, config) -> tuple[str, str]:
    pct = (value / target) * 100 if target else 100
    if pct >= getattr(config, "ZONE_GREEN", 80):
        return "🟢", "Зелёная"
    if pct >= getattr(config, "ZONE_YELLOW", 60):
        return "🟡", "Жёлтая"
    return "🔴", "Красная"


def _display_name(config, user_id: int) -> str:
    if user_id == getattr(config, "CHILD_ID", 0):
        return getattr(config, "CHILD_NAME", "Ребёнок")
    if user_id in (getattr(config, "PARENT_IDS", []) or []):
        return "Родитель"
    return "Кто-то"


async def _send(bot, recipients, text: str) -> None:
    if bot is None:
        return
    for pid in recipients:
        try:
            await asyncio.wait_for(bot.send_message(pid, text), timeout=_SEND_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("Таймаут отправки уведомления %s", pid)
        except Exception as e:
            logger.error("Не удалось отправить уведомление %s: %s", pid, e)


async def notify_added(bot, config, who: int, pef: int, tod: str, target: int) -> None:
    """Сообщить родителям (кроме автора), что добавлен замер."""
    name = _display_name(config, who)
    zone_emoji, _ = _zone(pef, target, config)
    tod_name = "Утро" if tod == "morning" else "Вечер"
    child = getattr(config, "CHILD_NAME", "Ребёнок")
    text = f"📝 {name} добавил для {child}: {pef} л/мин {zone_emoji} ({tod_name})"
    recipients = [
        p for p in (getattr(config, "PARENT_IDS", []) or [])
        if p != who and p != getattr(config, "CHILD_ID", 0)
    ]
    await _send(bot, recipients, text)


async def notify_red_zone(bot, config, pef: int, tod: str, target: int, who: int | None = None) -> None:
    """Тревога родителям при ПСВ ниже жёлтой зоны.

    ``who`` — автор замера; ему тревога не отправляется (он и так знает).
    """
    _, zone_name = _zone(pef, target, config)
    pct = int((pef / target) * 100) if target else 100
    child = getattr(config, "CHILD_NAME", "Ребёнок")
    text = f"🚨 {child}: ПСВ {pef} л/мин — {zone_name}!\nНорма: {target} л/мин ({pct}%). Свяжитесь с врачом."
    recipients = [
        p for p in (getattr(config, "PARENT_IDS", []) or [])
        if who is None or p != who
    ]
    await _send(bot, recipients, text)
