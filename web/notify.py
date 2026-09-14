"""Telegram-уведомления, отправляемые из Mini App (паритет с ботом)."""
import logging

logger = logging.getLogger(__name__)


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
            await bot.send_message(pid, text)
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


async def notify_red_zone(bot, config, pef: int, tod: str, target: int) -> None:
    """Тревога родителям при ПСВ ниже жёлтой зоны."""
    _, zone_name = _zone(pef, target, config)
    pct = int((pef / target) * 100) if target else 100
    child = getattr(config, "CHILD_NAME", "Ребёнок")
    text = f"🚨 {child}: ПСВ {pef} л/мин — {zone_name}!\nНорма: {target} л/мин ({pct}%). Свяжитесь с врачом."
    await _send(bot, list(getattr(config, "PARENT_IDS", []) or []), text)
