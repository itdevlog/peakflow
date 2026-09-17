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


def _display_name(config, user_id: int, child_id=None, child_name=None,
                  parent_ids=None) -> str:
    """Display label for an author.

    The tenant-aware caller passes the active child's id/name and the family's
    parents explicitly. The defaults keep the family-#1 ``member=None`` path
    (env ``CHILD_ID``/``PARENT_IDS``) working.
    """
    if child_id is None:
        child_id = getattr(config, "CHILD_ID", 0)
    if child_name is None:
        child_name = getattr(config, "CHILD_NAME", "Ребёнок")
    if parent_ids is None:
        parent_ids = getattr(config, "PARENT_IDS", []) or []
    if user_id == child_id:
        return child_name
    if user_id in parent_ids:
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


async def notify_added(bot, config, who: int, pef: int, tod: str, target: int, *,
                       recipients, child_name=None, child_id=None) -> None:
    """Сообщить родителям семьи (кроме автора), что добавлен замер.

    ``recipients`` обязателен — Telegram-id родителей вызывающей семьи. Его
    отсутствие (``None``) не означает семью №1: уведомление пропускается, чтобы
    действие одной семьи не уходило родителям другой. Для семьи №1 вызывающий
    передаёт env-список явно.
    """
    if recipients is None:
        logger.error("notify_added: recipients не задан — уведомление пропущено")
        return
    if child_name is None:
        child_name = getattr(config, "CHILD_NAME", "Ребёнок")
    if child_id is None:
        child_id = getattr(config, "CHILD_ID", 0)
    name = _display_name(config, who, child_id=child_id,
                         child_name=child_name, parent_ids=recipients)
    zone_emoji, _ = _zone(pef, target, config)
    tod_name = "Утро" if tod == "morning" else "Вечер"
    text = f"📝 {name} добавил для {child_name}: {pef} л/мин {zone_emoji} ({tod_name})"
    targets = [p for p in recipients if p != who and p != child_id]
    await _send(bot, targets, text)


async def notify_red_zone(bot, config, pef: int, tod: str, target: int, *,
                          recipients, who: int | None = None,
                          child_name=None, child_id=None) -> None:
    """Тревога родителям семьи при ПСВ ниже жёлтой зоны.

    ``recipients`` обязателен (см. ``notify_added``). ``who`` — автор замера;
    ему тревога не отправляется (он и так знает).
    """
    if recipients is None:
        logger.error("notify_red_zone: recipients не задан — уведомление пропущено")
        return
    if child_name is None:
        child_name = getattr(config, "CHILD_NAME", "Ребёнок")
    _, zone_name = _zone(pef, target, config)
    pct = int((pef / target) * 100) if target else 100
    text = f"🚨 {child_name}: ПСВ {pef} л/мин — {zone_name}!\nНорма: {target} л/мин ({pct}%). Свяжитесь с врачом."
    targets = [p for p in recipients if who is None or p != who]
    await _send(bot, targets, text)
