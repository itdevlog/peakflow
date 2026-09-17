import os
import sys
import io
import fcntl
import logging
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F, Router, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from config import (
    BOT_TOKEN, DB_PATH, CHILD_ID, PARENT_IDS, CHILD_NAME, TARGET_PEF,
    ZONE_GREEN, ZONE_YELLOW,
    WEEKLY_REPORT_DAY, WEEKLY_REPORT_HOUR, TZ_OFFSET,
    WEBAPP_PORT, WEBAPP_URL,
    is_parent, is_child,
)
from report import (
    month_title, tod_emoji, tod_label, pct_of, escape_md,
    parse_month as parse_csv_month,
    build_csv_content as _report_build_csv,
    pef_zone as _report_pef_zone,
)
from database import (
    init_db, add_measurement, edit_measurement, delete_measurement,
    get_last_measurement, get_all_measurements, get_today_measurements,
    get_recent_measurements,
    has_today_measurement, get_measurements_paginated,
    get_stats, get_last_two_weeks,
    mark_reminder_sent, was_reminder_sent,
    set_setting, set_note,
    get_effective_target as _db_get_effective_target,
    get_reminder_hours, backup_db, validate_reminder_hours,
    get_measurements_for_month, get_available_months, get_measurements_between,
    get_last_of_tod, replace_auto_measurement,
    get_previous_of_tod,
    get_measurement_by_id,
    get_member,
    create_family_with_owner,
    join_by_invite,
    get_family_invite,
    regenerate_family_invite,
    list_family_children,
    list_child_cards,
    create_invite,
    delete_invite,
)

import config as app_config

from web.server import run_webapp

# Часовой пояс из config (UTC+N)
TZ = timezone(timedelta(hours=TZ_OFFSET))


def now_tz() -> datetime:
    """Текущее время в настроенном часовом поясе."""
    return datetime.now(TZ)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("peakflow_bot")

# ---------------------------------------------------------------------------
# Singleton lock
# ---------------------------------------------------------------------------
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")

# Strong reference to the open lock file. Without it the file object would be
# garbage-collected the moment acquire_lock() returns, closing the descriptor
# and releasing the flock — silently allowing a second bot instance.
_lock_handle = None


def acquire_lock():
    """Take an exclusive non-blocking flock. Returns the open file handle.

    The caller must keep the returned handle alive (and pass it to
    release_lock) for as long as the lock should be held.
    """
    global _lock_handle
    lock_file = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        _lock_handle = lock_file
        return lock_file
    except BlockingIOError:
        logger.error("Бот уже запущен!")
        lock_file.close()
        sys.exit(1)


def release_lock(handle):
    global _lock_handle
    try:
        if handle is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        _lock_handle = None
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Role resolution + member injection
# ---------------------------------------------------------------------------
def _role(member, uid: int) -> str:
    """Роль участника: из БД (member), иначе fallback на .env (семья №1)."""
    if member:
        return member["role"]
    if is_parent(uid):
        return "parent"
    if is_child(uid):
        return "child"
    return "unknown"


def _is_parent_member(member, uid: int) -> bool:
    return _role(member, uid) == "parent"


def _can_manage_family(member, uid: int) -> bool:
    """Strict parent gate for family-management screens.

    Unlike ``_is_parent_member``, the .env fallback is intentionally NOT
    accepted: these screens dereference ``member['family_id']`` to resolve the
    family, so an env parent with no ``members`` row (possible when added to
    .env after the first init) must register first instead of crashing.
    """
    return member is not None and member.get("role") == "parent"


def _family_deny_text(member) -> str:
    return "⚠️ Сначала войдите в семью." if member is None else "⚠️ Только для родителей."


class MemberMiddleware(BaseMiddleware):
    """Inject the caller's member row (role, family_id) from the DB."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        uid = getattr(user, "id", None)
        data["member"] = await _db(get_member, DB_PATH, uid) if uid else None
        return await handler(event, data)


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

member_middleware = MemberMiddleware()
router.message.middleware(member_middleware)
router.callback_query.middleware(member_middleware)

# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------
class Measurement(StatesGroup):
    editing_target_pef = State()
    editing_reminder_hour = State()
    waiting_note = State()
    # Пошаговый inline-ввод ПСВ (сотни → десятки)
    pef_input_hundreds = State()
    pef_input_tens = State()


class Registration(StatesGroup):
    entering_family_name = State()
    entering_invite_code = State()
    adding_child_name = State()


def get_effective_target() -> int:
    """Целевая ПСВ из БД (settings), fallback — TARGET_PEF из .env."""
    return _db_get_effective_target(DB_PATH, TARGET_PEF)


async def _db(func, *args, **kwargs):
    """Run a synchronous SQLite/database helper off the event loop.

    Blocking the aiogram (and shared uvicorn) event loop on disk I/O freezes
    every other user; handlers must await this wrapper instead of calling the
    database functions directly.
    """
    return await asyncio.to_thread(func, *args, **kwargs)


def auto_time_of_day() -> str:
    """Auto-detect morning/evening based on current hour."""
    hour = now_tz().hour
    return "morning" if hour < 12 else "evening"


def parse_callback_int(data, prefix: str):
    """Safely parse the integer suffix of a callback_data string.

    Returns None for missing/non-numeric payloads instead of raising, so a
    malformed callback cannot crash a handler.
    """
    if not data or not isinstance(data, str) or not data.startswith(prefix):
        return None
    try:
        return int(data[len(prefix):])
    except (TypeError, ValueError):
        return None


def is_reminder_minute(minute: int) -> bool:
    """True for minutes inside the reminder window (0–1).

    The scheduler ticks every 60 seconds, so an exact 'minute == 0' check
    can be skipped by sleep drift. A two-minute window guarantees the
    reminder fires; per-day flags in the DB prevent duplicates.
    """
    return minute < 2


def seconds_until_next_minute(now: datetime) -> float:
    """Seconds to sleep so the next tick lands on a minute boundary.

    Without alignment the loop drifts and can skip the reminder window
    entirely when one iteration takes longer than expected.
    """
    return max(1.0, 60.0 - now.second - now.microsecond / 1_000_000)


def pef_zone(value: int, target: int) -> tuple[str, str]:
    """Зоны с порогами из config (обёртка над report.pef_zone)."""
    return _report_pef_zone(value, target, ZONE_GREEN, ZONE_YELLOW)


async def answer_callback(callback: types.CallbackQuery):
    try:
        await callback.answer()
    except TelegramBadRequest:
        # query already answered or too old — safe to ignore
        pass
    try:
        await callback.message.delete()
    except Exception:
        pass


async def respond(callback: types.CallbackQuery, text: str,
                  kb: InlineKeyboardMarkup = None, parse_mode: str = "Markdown"):
    await answer_callback(callback)
    return await callback.message.answer(text, parse_mode=parse_mode, reply_markup=kb)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def kb_main(is_parent_user: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💨 Измерение", callback_data="add")],
    ]
    if is_parent_user:
        rows.append([
            InlineKeyboardButton(text="📋 История", callback_data="history"),
            InlineKeyboardButton(text="📊 График", callback_data="chart"),
        ])
        rows.append([
            InlineKeyboardButton(text="📊 Сводка", callback_data="summary"),
            InlineKeyboardButton(text="📈 Неделя", callback_data="weekly"),
        ])
        rows.append([
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        ])
        rows.append([InlineKeyboardButton(text="✏️ Исправить последний", callback_data="edit_last")])
    else:
        # У ребёнка — измерение + свой график и статистика (мотивация)
        rows.append([
            InlineKeyboardButton(text="📊 Мой график", callback_data="chart"),
            InlineKeyboardButton(text="📈 Моя статистика", callback_data="stats"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
    ])


def kb_pagination(page: int, total_pages: int, is_parent_user: bool, measurements: list) -> InlineKeyboardMarkup:
    rows = []

    # Per-measurement edit/delete buttons for parents
    if is_parent_user and measurements:
        for m in measurements:
            rows.append([
                InlineKeyboardButton(text=f"✏️ {m['pef_value']}", callback_data=f"edit_{m['id']}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"del_{m['id']}"),
            ])

    # Page navigation
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text="⏮️", callback_data=f"hist_page_{page - 1}"))
    row.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        row.append(InlineKeyboardButton(text="⏭️", callback_data=f"hist_page_{page + 1}"))
    rows.append(row)

    # Bottom row: back (and settings for parents)
    bottom = [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
    if is_parent_user:
        bottom.append(InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"))
    rows.append(bottom)

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Клавиатуры для пошагового ввода ПСВ (сотни → десятки)
# ---------------------------------------------------------------------------
def kb_pef_hundreds() -> InlineKeyboardMarkup:
    """Клавиатура для выбора сотен: 1 2 3 4 5 6."""
    row = [InlineKeyboardButton(text=str(h), callback_data=f"h_{h}") for h in range(1, 7)]
    row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="back"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def kb_pef_tens() -> InlineKeyboardMarkup:
    """Клавиатура для выбора десятков: 00 10 20 ... 90 (2 ряда по 5) + Назад."""
    row1 = [InlineKeyboardButton(text=f"{d*10:02d}", callback_data=f"t_{d*10:02d}") for d in range(0, 5)]
    row2 = [InlineKeyboardButton(text=f"{d*10:02d}", callback_data=f"t_{d*10:02d}") for d in range(5, 10)]
    row3 = [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3])


# ---------------------------------------------------------------------------
# Status block — shows in main menu
# ---------------------------------------------------------------------------
async def build_status_block() -> str:
    today = await asyncio.to_thread(get_today_measurements, DB_PATH, CHILD_ID)
    target = await asyncio.to_thread(get_effective_target)

    morning_val = None
    evening_val = None
    for m in today:
        if m["time_of_day"] == "morning":
            morning_val = m["pef_value"]
        elif m["time_of_day"] == "evening":
            evening_val = m["pef_value"]

    morning_display = f"{tod_emoji('morning')} {morning_val} {pef_zone(morning_val, target)[0]}" if morning_val else f"{tod_emoji('morning')} —"
    evening_display = f"{tod_emoji('evening')} {evening_val} {pef_zone(evening_val, target)[0]}" if evening_val else f"{tod_emoji('evening')} —"

    # Last measurement diff (only the two latest rows are needed)
    recent = await asyncio.to_thread(get_recent_measurements, DB_PATH, CHILD_ID, 2)
    diff = ""
    if len(recent) >= 2:
        d = recent[0]["pef_value"] - recent[1]["pef_value"]
        sign = "+" if d > 0 else ""
        zone, _ = pef_zone(recent[0]["pef_value"], target)
        diff = f"\nПоследний: {recent[0]['pef_value']} {zone} ({sign}{d})"

    return (
        f"👋 *{escape_md(CHILD_NAME)}* | Целевая: {target} л/мин\n\n"
        f"Сегодня: {morning_display} | {evening_display}"
        f"{diff}"
    )


# ---------------------------------------------------------------------------
# Send main menu
# ---------------------------------------------------------------------------
async def send_main_menu(message_or_callback, user_id: int, member=None):
    is_p = _role(member, user_id) == "parent"
    status = await build_status_block()

    if isinstance(message_or_callback, types.CallbackQuery):
        await answer_callback(message_or_callback)
        await message_or_callback.message.answer(
            status, parse_mode="Markdown", reply_markup=kb_main(is_p)
        )
    else:
        await message_or_callback.answer(
            status, parse_mode="Markdown", reply_markup=kb_main(is_p)
        )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
def kb_registration() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Создать семью", callback_data="reg_create")],
        [InlineKeyboardButton(text="🔑 Войти по коду", callback_data="reg_join")],
    ])


async def _show_registration(message_or_callback, extra_text: str = ""):
    prefix = f"{extra_text}\n\n" if extra_text else ""
    await message_or_callback.answer(
        f"{prefix}👋 Добро пожаловать в Пикфлоуметр!\n\n"
        f"Создайте семью или войдите по коду приглашения.",
        reply_markup=kb_registration(),
    )


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext, member=None):
    uid = message.from_user.id
    role = _role(member, uid)

    # Deep-link: /start <token>
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        if role != "unknown":
            # Already registered: a token must never move a member to another
            # family (controller ruling, Task 2 review).
            await message.answer("ℹ️ Вы уже в семье.")
            await send_main_menu(message, uid, member=member)
            await state.clear()
            return
        token = parts[1].strip()
        result = await _db(join_by_invite, DB_PATH, token, uid)
        if result:
            who = "👨‍👧 Родитель" if result["role"] == "parent" else f"👶 {escape_md(result['name'])}"
            await message.answer(f"✅ Вы вошли в семью как *{who}*", parse_mode="Markdown")
            await state.clear()
            member = await _db(get_member, DB_PATH, uid)
            await send_main_menu(message, uid, member=member)
            logger.info("Пользователь %d вошёл по deep-link как %s", uid, result["role"])
            return
        await message.answer("❌ Код не найден. Попробуйте ещё раз.")
        # fall through to the registration screen

    if role == "unknown":
        await _show_registration(message)
        return

    who = "👨‍👧 Родитель" if role == "parent" else f"👶 {escape_md(CHILD_NAME)}"
    await message.answer(f"✅ Привет! Вы вошли как *{who}*", parse_mode="Markdown")
    await send_main_menu(message, uid, member=member)
    await state.clear()
    logger.info("Пользователь %d (%s) вошёл в бот", uid, who)


# ---------------------------------------------------------------------------
# Registration — create family / join by invite code
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "reg_create")
async def cb_reg_create(callback: types.CallbackQuery, state: FSMContext, member=None):
    if _role(member, callback.from_user.id) != "unknown":
        await callback.answer("ℹ️ Вы уже в семье.", show_alert=True)
        return
    await state.set_state(Registration.entering_family_name)
    await answer_callback(callback)
    await callback.message.answer(
        "🏠 Введите название семьи:",
        reply_markup=kb_back(),
    )


@router.callback_query(F.data == "reg_join")
async def cb_reg_join(callback: types.CallbackQuery, state: FSMContext, member=None):
    if _role(member, callback.from_user.id) != "unknown":
        await callback.answer("ℹ️ Вы уже в семье.", show_alert=True)
        return
    await state.set_state(Registration.entering_invite_code)
    await answer_callback(callback)
    await callback.message.answer(
        "🔑 Введите код приглашения:",
        reply_markup=kb_back(),
    )


# ---------------------------------------------------------------------------
# /cancel — exit any FSM state
# ---------------------------------------------------------------------------
@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext, member=None):
    uid = message.from_user.id
    current = await state.get_state()
    # Always clear the FSM state first: an unknown user (mid-registration) must
    # be able to escape a text state with /cancel.
    await state.clear()
    if current:
        await message.answer("❌ Действие отменено.")
    if _role(member, uid) == "unknown":
        await _show_registration(message)
        return
    await send_main_menu(message, uid, member=member)


# ---------------------------------------------------------------------------
# Registration text input — registered AFTER cmd_cancel so commands such as
# /cancel win the routing (bare F.text would otherwise swallow them).
# ---------------------------------------------------------------------------
@router.message(Registration.entering_family_name, F.text, ~F.text.startswith("/"))
async def input_family_name(message: types.Message, state: FSMContext, member=None):
    name = (message.text or "").strip()[:100]
    if not name:
        await message.answer("Введите название семьи:", reply_markup=kb_back())
        return

    family_id = await _db(create_family_with_owner, DB_PATH, message.from_user.id, name)
    invite = await _db(get_family_invite, DB_PATH, family_id)
    token = invite["token"] if invite else ""

    await message.answer(
        f"✅ Семья «{escape_md(name)}» создана.\n\n"
        f"🔑 Код для приглашения родителя:\n`{token}`",
        parse_mode="Markdown",
    )
    await state.clear()
    member = await _db(get_member, DB_PATH, message.from_user.id)
    await send_main_menu(message, message.from_user.id, member=member)
    logger.info("Пользователь %d создал семью %d", message.from_user.id, family_id)


@router.message(Registration.entering_invite_code, F.text, ~F.text.startswith("/"))
async def input_invite_code(message: types.Message, state: FSMContext, member=None):
    uid = message.from_user.id
    if _role(member, uid) != "unknown":
        # Already registered: never reassign to another family.
        await message.answer("ℹ️ Вы уже в семье.")
        await state.clear()
        await send_main_menu(message, uid, member=member)
        return

    token = (message.text or "").strip()
    result = await _db(join_by_invite, DB_PATH, token, uid)
    if not result:
        await message.answer("❌ Код не найден. Попробуйте ещё раз:", reply_markup=kb_back())
        return

    who = "👨‍👧 Родитель" if result["role"] == "parent" else f"👶 {escape_md(result['name'])}"
    await message.answer(f"✅ Вы вошли в семью как *{who}*", parse_mode="Markdown")
    await state.clear()
    member = await _db(get_member, DB_PATH, uid)
    await send_main_menu(message, uid, member=member)
    logger.info("Пользователь %d вошёл по коду как %s", uid, result["role"])


# ---------------------------------------------------------------------------
# ADD measurement — auto time of day, inline пошаговый ввод
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "add")
async def cb_add(callback: types.CallbackQuery, state: FSMContext):
    tod = auto_time_of_day()
    tod_icon = tod_emoji(tod)
    tod_name = tod_label(tod)

    # Check if already done (auto-carry doesn't count — it gets replaced)
    if await _db(has_today_measurement, DB_PATH, CHILD_ID, tod, skip_auto=True):
        await respond(callback,
            f"⚠️ {tod_icon} {tod_name} уже измерено сегодня.\n\n"
            f"Хотите добавить ещё одно или исправить последнее?",
            kb=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=f"✅ Всё равно {tod_icon}", callback_data=f"add_force_{tod}"),
                    InlineKeyboardButton(text="✏️ Исправить", callback_data="edit_last"),
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
            ]),
        )
        return

    await state.update_data(input_context="add")
    await respond(callback,
        f"💨 Выбери ПСВ (л/мин)\n"
        f"{tod_icon} {tod_name} (определено автоматически)\n"
        f"Диапазон: 100–690",
        kb=kb_pef_hundreds(),
    )
    await state.set_state(Measurement.pef_input_hundreds)


@router.callback_query(F.data.startswith("add_force_"))
async def cb_add_force(callback: types.CallbackQuery, state: FSMContext):
    tod = callback.data.replace("add_force_", "")
    await state.update_data(input_context="add", forced_tod=tod)
    await respond(callback,
        f"💨 Выбери ПСВ (л/мин)\n"
        f"{tod_emoji(tod)} {tod_label(tod)} (повторный замер)",
        kb=kb_pef_hundreds(),
    )
    await state.set_state(Measurement.pef_input_hundreds)


# ---------------------------------------------------------------------------
# Пошаговый inline-ввод: сотни → десятки — функции сохранения
# ---------------------------------------------------------------------------
async def _save_measurement(callback: types.CallbackQuery, state: FSMContext, pef: int, data: dict):
    """Сохранить новое измерение после inline-ввода."""
    if "forced_tod" in data:
        tod = data["forced_tod"]
    else:
        tod = auto_time_of_day()
        if await _db(has_today_measurement, DB_PATH, CHILD_ID, tod, skip_auto=True):
            # The auto-detected slot is already filled by a real entry.
            # Never silently flip morning↔evening (that corrupts statistics) —
            # keep the chosen value and ask the user which slot to use.
            await state.update_data(pending_pef=pef)
            await respond(callback,
                f"⚠️ {tod_emoji(tod)} {tod_label(tod)} уже измерено сегодня.\n\n"
                f"Куда записать значение *{pef}* л/мин?",
                kb=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="☀️ Утро", callback_data="pick_tod_morning"),
                        InlineKeyboardButton(text="🌙 Вечер", callback_data="pick_tod_evening"),
                    ],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
                ]),
            )
            return

    await _persist_measurement(callback, state, pef, tod)


async def _persist_measurement(callback: types.CallbackQuery, state: FSMContext, pef: int, tod: str):
    """Insert/replace a measurement with a definitive time of day."""
    who = callback.from_user.id

    # Auto-carry record for this slot today → replace it with the real value
    replaced_id = await _db(replace_auto_measurement, DB_PATH, CHILD_ID, tod, pef, who)
    if replaced_id:
        mid = replaced_id
    else:
        mid = await _db(add_measurement, DB_PATH, pef, tod, CHILD_ID, who)

    target = await _db(get_effective_target)
    zone_emoji, zone_name = pef_zone(pef, target)

    # Diff against the previous measurement of the same time of day
    prev = await _db(get_previous_of_tod, DB_PATH, CHILD_ID, tod, mid)
    diff_msg = ""
    if prev:
        d = pef - prev["pef_value"]
        sign = "+" if d > 0 else ""
        diff_msg = f"\n📈 Изменение: {sign}{d} л/мин"

    await respond(callback,
        f"✅ {tod_emoji(tod)} {tod_label(tod)}: *{pef}* л/мин {zone_emoji}\n"
        f"Зона: {zone_name} ({pct_of(pef, target)}% от нормы){diff_msg}\n\n"
        f"Добавить заметку? (болел, после спорта, забыл лекарство…)",
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Да", callback_data="note_add"),
                InlineKeyboardButton(text="Пропустить", callback_data="note_skip"),
            ],
        ]),
    )

    # Notify other parents
    added_by_name = _user_display_name(who)
    for pid in PARENT_IDS:
        if pid != who and pid != CHILD_ID:
            try:
                await bot.send_message(
                    pid,
                    f"📝 {escape_md(added_by_name)} добавил для *{escape_md(CHILD_NAME)}*: "
                    f"{pef} л/мин {zone_emoji} ({tod_label(tod)})",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    # Alert if red zone (skip the author — they already know the value)
    if pct_of(pef, target) < ZONE_YELLOW:
        for pid in PARENT_IDS:
            if pid == who:
                continue
            try:
                await bot.send_message(
                    pid,
                    f"🚨 *{escape_md(CHILD_NAME)}*: ПСВ *{pef}* л/мин — {zone_name}!\n"
                    f"Норма: {target} л/мин ({pct_of(pef, target)}%). Свяжитесь с врачом.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    await state.update_data(note_for_id=mid)
    await state.set_state(Measurement.waiting_note)
    logger.info("Измерение: %d л/мин, %s, добавил %d", pef, tod, who)


@router.callback_query(F.data.in_({"pick_tod_morning", "pick_tod_evening"}))
async def cb_pick_tod(callback: types.CallbackQuery, state: FSMContext):
    """User explicitly chose the time of day for a pending measurement."""
    data = await state.get_data()
    pef = data.get("pending_pef")
    if pef is None:
        await callback.answer("❌ Значение потеряно, начните заново.", show_alert=True)
        return
    tod = "morning" if callback.data == "pick_tod_morning" else "evening"
    await _persist_measurement(callback, state, pef, tod)


async def _save_edit_last(callback: types.CallbackQuery, state: FSMContext, new_val: int, data: dict, member=None):
    """Сохранить исправление последнего измерения."""
    mid = data.get("edit_id")
    if mid is None:
        await callback.answer("❌ Ошибка: нет ID записи", show_alert=True)
        return

    ok = await _db(edit_measurement, DB_PATH, mid, new_val, CHILD_ID)
    if ok:
        target = await _db(get_effective_target)
        zone, _ = pef_zone(new_val, target)
        await respond(callback,
            f"✅ Исправлено: *{new_val}* л/мин {zone}",
        )
    else:
        await respond(callback, "❌ Не удалось изменить запись.")

    await state.clear()
    await send_main_menu(callback, callback.from_user.id, member=member)


async def _save_edit_any(callback: types.CallbackQuery, state: FSMContext, new_val: int, data: dict, member=None):
    """Сохранить исправление любого измерения."""
    mid = data.get("edit_id")
    if mid is None:
        await callback.answer("❌ Ошибка: нет ID записи", show_alert=True)
        return

    ok = await _db(edit_measurement, DB_PATH, mid, new_val, CHILD_ID)

    if ok:
        target = await _db(get_effective_target)
        zone, _ = pef_zone(new_val, target)
        await respond(callback,
            f"✅ Запись #{mid} исправлена: *{new_val}* л/мин {zone}",
        )
    else:
        await respond(callback, "❌ Не удалось изменить запись.")

    await state.clear()
    await _show_history(callback, page=1, member=member)


# ---------------------------------------------------------------------------
# Пошаговый inline-ввод: сотни → десятки
# ---------------------------------------------------------------------------
@router.callback_query(Measurement.pef_input_hundreds, F.data.startswith("h_"))
async def cb_select_hundreds(callback: types.CallbackQuery, state: FSMContext):
    h = parse_callback_int(callback.data, "h_")
    if h is None:
        await callback.answer("❌ Некорректный ввод.", show_alert=True)
        return
    await state.update_data(hundreds=h)
    await callback.answer()
    await callback.message.edit_text(
        f"💨 Выбрано: {h}__ л/мин\n\n"
        f"Теперь выбери десятки:",
        reply_markup=kb_pef_tens(),
    )
    await state.set_state(Measurement.pef_input_tens)


@router.callback_query(Measurement.pef_input_tens, F.data.startswith("t_"))
async def cb_select_tens(callback: types.CallbackQuery, state: FSMContext, member=None):
    d = parse_callback_int(callback.data, "t_")
    if d is None:
        await callback.answer("❌ Некорректный ввод.", show_alert=True)
        return
    data = await state.get_data()
    h = data.get("hundreds")
    if h is None:
        await callback.answer("Ошибка: не выбраны сотни", show_alert=True)
        return

    pef = h * 100 + d
    context = data.get("input_context", "add")

    # Обрабатываем по контексту
    if context == "add":
        await _save_measurement(callback, state, pef, data)
    elif context == "edit_last":
        await _save_edit_last(callback, state, pef, data, member=member)
    elif context == "edit_any":
        await _save_edit_any(callback, state, pef, data, member=member)


@router.callback_query(Measurement.pef_input_hundreds, F.data == "back")
@router.callback_query(Measurement.pef_input_tens, F.data == "back")
async def cb_back_from_pef_input(callback: types.CallbackQuery, state: FSMContext, member=None):
    await state.clear()
    await send_main_menu(callback, callback.from_user.id, member=member)


# ---------------------------------------------------------------------------
# NOTE flow — after saving a measurement
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "note_add")
async def cb_note_add(callback: types.CallbackQuery, state: FSMContext):
    """User chose to attach a note — wait for text."""
    if await state.get_state() != Measurement.waiting_note:
        await callback.answer("Сначала сделайте замер.", show_alert=True)
        return
    await answer_callback(callback)
    await callback.message.answer(
        "📝 Напишите заметку к замеру (до 200 символов):",
        reply_markup=kb_back(),
    )


@router.callback_query(F.data == "note_skip")
async def cb_note_skip(callback: types.CallbackQuery, state: FSMContext, member=None):
    """Skip the note — back to main menu."""
    await state.clear()
    await send_main_menu(callback, callback.from_user.id, member=member)


@router.message(Measurement.waiting_note, F.text)
async def input_note(message: types.Message, state: FSMContext, member=None):
    """Save the note text to the last measurement."""
    data = await state.get_data()
    mid = data.get("note_for_id")
    if mid is None:
        await state.clear()
        await send_main_menu(message, message.from_user.id, member=member)
        return

    note = message.text.strip()[:200]
    ok = await _db(set_note, DB_PATH, mid, note, CHILD_ID)

    truncated = "" if len(message.text.strip()) <= 200 else " (обрезано до 200 символов)"
    await message.answer(
        "✅ Заметка сохранена" + truncated if ok else "❌ Не удалось сохранить заметку."
    )

    await state.clear()
    await send_main_menu(message, message.from_user.id, member=member)


# ---------------------------------------------------------------------------
# EDIT last measurement — inline пошаговый ввод
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "edit_last")
async def cb_edit_last(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только родители могут редактировать.", show_alert=True)
        return
    last = await _db(get_last_measurement, DB_PATH, CHILD_ID)
    if not last:
        await respond(callback, "📭 Нет измерений для исправления.", kb=kb_back())
        return

    await state.update_data(edit_id=last["id"], input_context="edit_last")
    await respond(callback,
        f"✏️ Текущее значение: *{last['pef_value']}* л/мин "
        f"({tod_emoji(last['time_of_day'])} {tod_label(last['time_of_day'])})\n\n"
        f"Выбери новое значение:",
        kb=kb_pef_hundreds(),
    )
    await state.set_state(Measurement.pef_input_hundreds)


# ---------------------------------------------------------------------------
# HISTORY
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "history")
async def cb_history(callback: types.CallbackQuery, state: FSMContext, member=None):
    await _show_history(callback, page=1, member=member)


@router.callback_query(F.data.startswith("hist_page_"))
async def cb_history_page(callback: types.CallbackQuery, state: FSMContext, member=None):
    page = parse_callback_int(callback.data, "hist_page_")
    if page is None or page < 1:
        await callback.answer("❌ Некорректная страница.", show_alert=True)
        return
    await _show_history(callback, page=page, member=member)


def _history_line(m: dict, target: int) -> str:
    """One history row: emoji, timestamp, value, zone, author, auto/note marks."""
    zone, _ = pef_zone(m["pef_value"], target)
    ts = m["measured_at"][5:16].replace("T", " ")
    who = "👨‍👧" if is_parent(m.get("added_by", 0)) else "👶"
    auto = " 🤖" if m.get("source") == "auto" else ""
    note = f" ℹ️ {escape_md(m['note'])}" if m.get("note") else ""
    return f"{tod_emoji(m['time_of_day'])} {ts} → *{m['pef_value']}* {zone} {who}{auto}{note}"


def _format_history_lines(measurements: list, target: int) -> list:
    return [_history_line(m, target) for m in measurements]


async def _show_history(callback: types.CallbackQuery, page: int = 1, member=None):
    if page < 1:
        page = 1
    measurements, total, total_pages = await _db(
        get_measurements_paginated, DB_PATH, CHILD_ID, page=page, per_page=10)
    # A stale keyboard (page since deleted) may point past the last page —
    # clamp instead of rendering an empty screen.
    if page > total_pages:
        page = total_pages
        measurements, total, total_pages = await _db(
            get_measurements_paginated, DB_PATH, CHILD_ID, page=page, per_page=10)
    target = await _db(get_effective_target)
    is_p = _role(member, callback.from_user.id) == "parent"

    if not measurements:
        await respond(callback, "📭 Нет измерений.", kb=kb_back())
        return

    lines = _format_history_lines(measurements, target)
    kb = kb_pagination(page, total_pages, is_p, measurements)

    await respond(callback,
        f"📋 История *{escape_md(CHILD_NAME)}*:\n\n" + "\n".join(lines),
        kb=kb,
    )


# ---------------------------------------------------------------------------
# EDIT any measurement (parent only) — inline пошаговый ввод
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("edit_"))
async def cb_edit_any(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только родители могут редактировать.", show_alert=True)
        return

    mid = parse_callback_int(callback.data, "edit_")
    if mid is None:
        await callback.answer("❌ Некорректный ID записи.", show_alert=True)
        return
    measurement = await _db(get_measurement_by_id, DB_PATH, mid)
    if not measurement:
        await callback.answer("❌ Запись не найдена.", show_alert=True)
        return

    await state.update_data(edit_id=mid, input_context="edit_any")
    await respond(callback,
        f"✏️ Запись #{mid}: *{measurement['pef_value']}* л/мин "
        f"({tod_emoji(measurement['time_of_day'])} {tod_label(measurement['time_of_day'])}, "
        f"{measurement['measured_at'][:10]})\n\n"
        f"Выбери новое значение:",
        kb=kb_pef_hundreds(),
    )
    await state.set_state(Measurement.pef_input_hundreds)


# ---------------------------------------------------------------------------
# DELETE any measurement (parent only)
# ---------------------------------------------------------------------------
# IMPORTANT: del_confirm_ handler MUST be registered BEFORE del_ handler
# so aiogram matches the more specific pattern first.
@router.callback_query(F.data.startswith("del_confirm_"))
async def cb_delete_confirm(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только родители могут удалять.", show_alert=True)
        return

    mid = parse_callback_int(callback.data, "del_confirm_")
    if mid is None:
        await callback.answer("❌ Некорректный ID записи.", show_alert=True)
        return
    ok = await _db(delete_measurement, DB_PATH, mid, CHILD_ID)

    if ok:
        await callback.answer("✅ Запись удалена.", show_alert=True)
    else:
        await callback.answer("❌ Не удалось удалить.", show_alert=True)

    await state.clear()
    await _show_history(callback, page=1, member=member)


@router.callback_query(F.data.startswith("del_") & ~F.data.startswith("del_child_"))
async def cb_delete(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только родители могут удалять.", show_alert=True)
        return

    mid = parse_callback_int(callback.data, "del_")
    if mid is None:
        await callback.answer("❌ Некорректный ID записи.", show_alert=True)
        return
    row = await _db(get_measurement_by_id, DB_PATH, mid)

    if not row:
        await callback.answer("❌ Запись не найдена.", show_alert=True)
        return

    await respond(callback,
        f"⚠️ Удалить запись #{mid}?\n\n"
        f"{tod_emoji(row['time_of_day'])} {row['pef_value']} л/мин "
        f"({row['measured_at'][:10]})\n\n"
        f"Это действие необратимо!",
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"del_confirm_{mid}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="history"),
            ]
        ]),
    )


# ---------------------------------------------------------------------------
# SETTINGS — parents only
# ---------------------------------------------------------------------------
def kb_settings(target: int) -> InlineKeyboardMarkup:
    """Settings screen keyboard (shared by callback and message entry points)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 Изменить цель ({target})", callback_data="change_target")],
        [InlineKeyboardButton(text="⏰ Напоминания", callback_data="reminders")],
        [InlineKeyboardButton(text="👨‍👩‍👧 Участники", callback_data="members")],
        [InlineKeyboardButton(text="🧒 Дети", callback_data="children")],
        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="export")],
        [InlineKeyboardButton(text="💾 Скачать бэкап", callback_data="backup")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


def build_settings_text(target: int, total: int) -> str:
    return (
        f"⚙️ *Настройки*\n\n"
        f"👤 Ребёнок: *{escape_md(CHILD_NAME)}*\n"
        f"🎯 Целевая ПСВ: *{target}* л/мин\n"
        f"📊 Всего замеров: {total}\n\n"
        f"Выберите действие:"
    )


@router.callback_query(F.data == "settings")
async def cb_settings(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return

    target = await _db(get_effective_target)
    measurements = await _db(get_all_measurements, DB_PATH, CHILD_ID, include_auto=True)

    await respond(callback,
        build_settings_text(target, len(measurements)),
        kb=kb_settings(target),
    )


# ---------------------------------------------------------------------------
# Reminders settings (hours configurable via settings)
# ---------------------------------------------------------------------------
def build_reminders_text(hours: dict) -> str:
    return (
        "⏰ *Напоминания*\n\n"
        f"🌅 Ребёнку утром: {hours['child_morning']:02d}:00\n"
        f"🌙 Ребёнку вечером: {hours['child_evening']:02d}:00\n"
        f"👨‍👧 Родителям (нет утреннего): {hours['parent_morning']:02d}:00\n"
        f"👨‍👧 Родителям (нет вечернего): {hours['parent_evening']:02d}:00\n\n"
        f"Изменения применяются сразу, без перезапуска."
    )


def kb_reminders() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 Утро ребёнку", callback_data="rem_set_child_morning"),
         InlineKeyboardButton(text="🌙 Вечер ребёнку", callback_data="rem_set_child_evening")],
        [InlineKeyboardButton(text="🌅 Утро родителям", callback_data="rem_set_parent_morning"),
         InlineKeyboardButton(text="🌙 Вечер родителям", callback_data="rem_set_parent_evening")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


@router.callback_query(F.data == "reminders")
async def cb_reminders(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    hours = await _db(get_reminder_hours, DB_PATH)
    await respond(callback, build_reminders_text(hours), kb=kb_reminders())


@router.callback_query(F.data.startswith("rem_set_"))
async def cb_rem_set(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    key = callback.data.replace("rem_set_", "")
    await state.update_data(reminder_key=key)
    await state.set_state(Measurement.editing_reminder_hour)
    labels = {
        "child_morning": "🌅 Ребёнку утром",
        "child_evening": "🌙 Ребёнку вечером",
        "parent_morning": "🌅 Родителям (утро)",
        "parent_evening": "🌙 Родителям (вечер)",
    }
    await answer_callback(callback)
    await callback.message.answer(
        f"⏰ {labels.get(key, key)}\n\nВведите час (0–23):",
        reply_markup=kb_back(),
    )


@router.message(Measurement.editing_reminder_hour, F.text)
async def input_reminder_hour(message: types.Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("reminder_key")
    if not key or not message.text.strip().isdigit():
        await message.answer("Введите число 0–23:")
        return
    hour = int(message.text.strip())
    if not (0 <= hour <= 23):
        await message.answer("Введите число 0–23:")
        return

    hours = await _db(get_reminder_hours, DB_PATH)
    hours[key] = hour
    error = validate_reminder_hours(hours)
    if error:
        await message.answer(f"⚠️ {error}")
        return

    await _db(set_setting, DB_PATH, f"reminder_{key}", str(hour))
    await message.answer(f"✅ Час изменён: {hour:02d}:00")

    await state.clear()
    await _send_reminders_from_message(message)


async def _send_reminders_from_message(message: types.Message):
    hours = await _db(get_reminder_hours, DB_PATH)
    await message.answer(
        build_reminders_text(hours),
        parse_mode="Markdown",
        reply_markup=kb_reminders(),
    )


# ---------------------------------------------------------------------------
# Change target PEF
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "change_target")
async def cb_change_target(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return

    current = await _db(get_effective_target)
    await respond(callback,
        f"🎯 Текущая цель: *{current}* л/мин\n\n"
        f"Введите новое значение (100–800):",
        kb=kb_back(),
    )
    await state.set_state(Measurement.editing_target_pef)


@router.message(Measurement.editing_target_pef, F.text.isdigit())
async def input_target(message: types.Message, state: FSMContext):
    new_val = int(message.text.strip())
    if not (100 <= new_val <= 800):
        await message.answer("Диапазон: 100–800 л/мин.", reply_markup=kb_back())
        return

    await _db(set_setting, DB_PATH, "target_pef", str(new_val))
    zone, _ = pef_zone(new_val, new_val)  # target is 100% of itself

    await message.answer(
        f"✅ Цель изменена: *{new_val}* л/мин {zone}",
        parse_mode="Markdown",
    )

    await state.clear()
    # Send settings screen again
    await _send_settings_from_message(message)


async def _send_settings_from_message(message: types.Message):
    target = await _db(get_effective_target)
    measurements = await _db(get_all_measurements, DB_PATH, CHILD_ID, include_auto=True)

    await message.answer(
        build_settings_text(target, len(measurements)),
        parse_mode="Markdown",
        reply_markup=kb_settings(target),
    )


# ---------------------------------------------------------------------------
# EXPORT CSV — period selection screen
# ---------------------------------------------------------------------------
def build_csv_content(rows, target, include_summary=True):
    stats = get_stats(DB_PATH, CHILD_ID) if include_summary else None
    return _report_build_csv(rows, target, CHILD_NAME, stats=stats,
                             include_summary=include_summary,
                             display_name=_user_display_name,
                             zone_green=ZONE_GREEN, zone_yellow=ZONE_YELLOW)


def kb_export_periods(months: list) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📚 Всё время", callback_data="export_all")]]
    for y, m in reversed(months[-3:]):  # newest month on top
        rows.append([InlineKeyboardButton(
            text=f"📅 {month_title(y, m)}",
            callback_data=f"csv_{y:04d}-{m:02d}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Настройки", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "export")
async def cb_export(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    months = await _db(get_available_months, DB_PATH, CHILD_ID)
    if not months:
        await respond(callback, "📭 Нет данных для экспорта.", kb=kb_back())
        return
    await respond(callback, "📥 Экспорт CSV — выберите период:", kb=kb_export_periods(months))


async def _send_csv(callback: types.CallbackQuery, rows: list, filename: str, caption: str):
    target = await _db(get_effective_target)
    if not rows:
        await callback.answer("📭 В выбранном периоде нет записей.", show_alert=True)
        return
    content = await _db(build_csv_content, rows, target, True)
    await answer_callback(callback)
    await callback.message.answer_document(
        BufferedInputFile(content.encode("utf-8-sig"), filename=filename),
        caption=caption,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")]
        ]),
    )


@router.callback_query(F.data == "export_all")
async def cb_export_all(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    rows = await _db(
        get_measurements_between,
        DB_PATH, CHILD_ID, "2000-01-01", now_tz().strftime("%Y-%m-%d")
    )
    filename = f"peakflow_{CHILD_NAME}_{now_tz().strftime('%Y%m%d_%H%M')}.csv"
    await _send_csv(callback, rows, filename, f"📥 Экспорт (всё): {len(rows)} записей")


@router.callback_query(F.data.startswith("csv_"))
async def cb_export_month(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    parsed = parse_csv_month(callback.data)
    if not parsed:
        await callback.answer("❌ Неверный период.", show_alert=True)
        return
    y, m = parsed
    # [first of month, first of next month) — last_day is the day before next month
    last_day = (datetime(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    rows = await _db(get_measurements_between, DB_PATH, CHILD_ID,
                     f"{y:04d}-{m:02d}-01", last_day.strftime("%Y-%m-%d"))
    # get_measurements_between includes auto (CSV shows them with 'auto' source column)
    filename = f"peakflow_{CHILD_NAME}_{y:04d}-{m:02d}.csv"
    await _send_csv(callback, rows, filename,
                    f"📥 Экспорт за {month_title(y, m)}: {len(rows)} записей")


# ---------------------------------------------------------------------------
# BACKUP — parents only
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "backup")
async def cb_backup(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return

    stamp = now_tz().strftime("%Y%m%d_%H%M")
    dest = f"backup_{CHILD_NAME}_{stamp}.db"
    try:
        await _db(backup_db, DB_PATH, dest)
    except Exception as e:
        logger.error("Ошибка бэкапа: %s", e)
        await callback.answer("❌ Не удалось создать бэкап.", show_alert=True)
        return

    measurements = await _db(get_all_measurements, DB_PATH, CHILD_ID, include_auto=True)
    try:
        def _read_backup():
            with open(dest, "rb") as f:
                return f.read()
        data = await asyncio.to_thread(_read_backup)
        await answer_callback(callback)
        await callback.message.answer_document(
            BufferedInputFile(data, filename=f"peakflow_backup_{stamp}.db"),
            caption=f"💾 Бэкап БД: {len(measurements)} замеров",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")]
            ]),
        )
    except Exception as e:
        logger.error("Ошибка отправки бэкапа: %s", e)
        await callback.answer("❌ Не удалось отправить файл.", show_alert=True)
    finally:
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# FAMILY MANAGEMENT — «Участники» / «Дети» (parents only)
# ---------------------------------------------------------------------------
def _parse_child_token(data, prefix: str):
    """Safely extract the invite token from a callback payload.

    ``parse_callback_int`` cannot be used here: a token is an arbitrary
    URL-safe string, not an integer. Returns None for missing/empty payloads
    so a malformed callback cannot crash the handler.
    """
    if not data or not isinstance(data, str) or not data.startswith(prefix):
        return None
    token = data[len(prefix):]
    return token or None


def _members_text(children: list, invite) -> str:
    lines = ["👨‍👩‍👧 *Участники семьи*"]
    lines.append("👨‍👧 Родители: вы")
    if children:
        lines.append("👶 Дети:")
        for c in children:
            lines.append(f"  • {escape_md(c['name'])}")
    else:
        lines.append("👶 Дети: пока нет")
    lines.append("")
    token = invite["token"] if invite else None
    if token:
        lines.append(f"🔑 Код для приглашения родителя:\n`{token}`")
    else:
        lines.append("🔑 Код приглашения родителя отсутствует.")
    return "\n".join(lines)


def kb_members() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Перегенерировать код", callback_data="regen_invite")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


@router.callback_query(F.data == "members")
async def cb_members(callback: types.CallbackQuery, member=None):
    if not _can_manage_family(member, callback.from_user.id):
        await callback.answer(_family_deny_text(member), show_alert=True)
        return
    family_id = member["family_id"]
    invite = await _db(get_family_invite, DB_PATH, family_id)
    children = await _db(list_family_children, DB_PATH, family_id)
    await respond(callback, _members_text(children, invite), kb=kb_members())


@router.callback_query(F.data == "regen_invite")
async def cb_regen_invite(callback: types.CallbackQuery, member=None):
    if not _can_manage_family(member, callback.from_user.id):
        await callback.answer(_family_deny_text(member), show_alert=True)
        return
    family_id = member["family_id"]
    token = await _db(regenerate_family_invite, DB_PATH, family_id)
    await callback.answer("✅ Код обновлён.", show_alert=True)
    children = await _db(list_family_children, DB_PATH, family_id)
    invite = await _db(get_family_invite, DB_PATH, family_id)
    # regenerate already returned the new token; prefer it if the re-read lags.
    if invite is None:
        invite = {"token": token}
    await respond(callback, _members_text(children, invite), kb=kb_members())


def _children_text(cards: list) -> str:
    if not cards:
        return "🧒 *Дети*\n\nПока нет карточек для добавления детей."
    lines = ["🧒 *Дети*", "", "Карточки для входа ребёнка:"]
    for c in cards:
        name = escape_md(c["name"]) if c.get("name") else "без имени"
        lines.append(f"  • {name}")
    return "\n".join(lines)


def kb_children(cards: list) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Добавить ребёнка", callback_data="add_child")]]
    for c in cards:
        name = c["name"] if c.get("name") else "без имени"
        rows.append([InlineKeyboardButton(
            text=f"🗑️ {name}", callback_data=f"del_child_{c['token']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_children(callback: types.CallbackQuery, family_id: int):
    cards = await _db(list_child_cards, DB_PATH, family_id)
    await respond(callback, _children_text(cards), kb=kb_children(cards))


@router.callback_query(F.data == "children")
async def cb_children(callback: types.CallbackQuery, member=None):
    if not _can_manage_family(member, callback.from_user.id):
        await callback.answer(_family_deny_text(member), show_alert=True)
        return
    await _show_children(callback, member["family_id"])


@router.callback_query(F.data == "add_child")
async def cb_add_child(callback: types.CallbackQuery, state: FSMContext, member=None):
    if not _can_manage_family(member, callback.from_user.id):
        await callback.answer(_family_deny_text(member), show_alert=True)
        return
    await state.update_data(family_id=member["family_id"])
    await state.set_state(Registration.adding_child_name)
    await answer_callback(callback)
    await callback.message.answer("🧒 Введите имя ребёнка:", reply_markup=kb_back())


@router.message(Registration.adding_child_name, F.text, ~F.text.startswith("/"))
async def input_child_name(message: types.Message, state: FSMContext, member=None):
    if not _can_manage_family(member, message.from_user.id):
        await message.answer(_family_deny_text(member))
        await state.clear()
        return
    name = (message.text or "").strip()[:100]
    if not name:
        await message.answer("Введите имя ребёнка:", reply_markup=kb_back())
        return
    data = await state.get_data()
    family_id = data.get("family_id") or member["family_id"]
    token = await _db(create_invite, DB_PATH, family_id, "child", name)
    await message.answer(
        f"✅ Карточка для *{escape_md(name)}* создана.\n\n"
        f"🔑 Код для входа ребёнка:\n`{token}`",
        parse_mode="Markdown",
    )
    await state.clear()
    cards = await _db(list_child_cards, DB_PATH, family_id)
    await message.answer(_children_text(cards), parse_mode="Markdown",
                         reply_markup=kb_children(cards))


@router.callback_query(F.data.startswith("del_child_"))
async def cb_del_child(callback: types.CallbackQuery, member=None):
    if not _can_manage_family(member, callback.from_user.id):
        await callback.answer(_family_deny_text(member), show_alert=True)
        return
    token = _parse_child_token(callback.data, "del_child_")
    if not token:
        await callback.answer("❌ Некорректная карточка.", show_alert=True)
        return
    ok = await _db(delete_invite, DB_PATH, token)
    if ok:
        await callback.answer("✅ Карточка удалена.", show_alert=True)
    else:
        await callback.answer("❌ Карточка не найдена.", show_alert=True)
    await _show_children(callback, member["family_id"])


# ---------------------------------------------------------------------------
# CHART — month navigation
# ---------------------------------------------------------------------------
def parse_chart_month(payload: str):
    """'chart_2026-08' → (2026, 8); invalid → None."""
    try:
        y, m = payload.replace("chart_", "").split("-")
        y, m = int(y), int(m)
        if 1 <= m <= 12 and 2000 <= y <= 2100:
            return y, m
    except (ValueError, AttributeError):
        pass
    return None


def kb_chart_nav(year: int, month: int, can_next: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="⬅️ Прошлый месяц", callback_data=prev_month_cb(year, month))]]
    if can_next:
        rows[0].append(InlineKeyboardButton(text="Следующий месяц ➡️",
                                            callback_data=f"chart_{next_month_str(year, month)}"))
    rows.append([InlineKeyboardButton(text="📥 Сохранить картинку",
                                      callback_data=f"chart_dl_{year:04d}-{month:02d}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prev_month_cb(year: int, month: int) -> str:
    if month == 1:
        return f"chart_{year - 1:04d}-12"
    return f"chart_{year:04d}-{month - 1:02d}"


def next_month_str(year: int, month: int) -> str:
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def _render_chart_png(rows: list, target: int, title: str) -> bytes:
    """Render measurements to PNG bytes (matplotlib Agg, in-memory)."""
    dates = [datetime.strptime(d["measured_at"], "%Y-%m-%d %H:%M:%S") for d in rows]
    values = [d["pef_value"] for d in rows]

    morning_d = [dates[i] for i, d in enumerate(rows) if d["time_of_day"] == "morning"]
    morning_v = [values[i] for i, d in enumerate(rows) if d["time_of_day"] == "morning"]
    evening_d = [dates[i] for i, d in enumerate(rows) if d["time_of_day"] == "evening"]
    evening_v = [values[i] for i, d in enumerate(rows) if d["time_of_day"] == "evening"]

    fig, ax = plt.subplots(figsize=(10, 5))
    try:
        ax.plot(dates, values, marker="o", linewidth=2, label="ПСВ", color="#2196F3", markersize=4, zorder=3)

        if morning_d:
            ax.scatter(morning_d, morning_v, color="#FF9800", label="Утро", zorder=5, s=80, edgecolors="white", linewidth=1.5)
        if evening_d:
            ax.scatter(evening_d, evening_v, color="#9C27B0", label="Вечер", zorder=5, s=80, edgecolors="white", linewidth=1.5)

        # Best / Worst
        best_idx = values.index(max(values))
        worst_idx = values.index(min(values))
        ax.annotate(f"🏆 {max(values)}", (dates[best_idx], values[best_idx]),
                    textcoords="offset points", xytext=(0, 12), ha="center",
                    fontsize=9, fontweight="bold", color="green")
        ax.annotate(f"⚠️ {min(values)}", (dates[worst_idx], values[worst_idx]),
                    textcoords="offset points", xytext=(0, -14), ha="center",
                    fontsize=9, fontweight="bold", color="red")

        if target:
            ax.axhline(y=target, color="green", linestyle="--", label=f"Норма ({target})", linewidth=1.5, zorder=2)
            ax.axhline(y=int(target * ZONE_GREEN / 100), color="orange", linestyle=":", alpha=0.5, linewidth=1)
            ax.axhline(y=int(target * ZONE_YELLOW / 100), color="yellow", linestyle=":", alpha=0.5, linewidth=1)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.set_ylabel("ПСВ (л/мин)")
        ax.set_title(f"Пикфлоуметрия — {title}")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
    finally:
        # Always release the figure, even if rendering raised.
        plt.close(fig)
    return buf.getvalue()


async def _render_chart_png_async(rows: list, target: int, title: str) -> bytes:
    """Run the CPU-bound matplotlib render off the event loop."""
    return await asyncio.to_thread(_render_chart_png, rows, target, title)


async def _send_month_chart(callback: types.CallbackQuery, year: int, month: int):
    target = await _db(get_effective_target)
    rows = await _db(get_measurements_for_month, DB_PATH, CHILD_ID, year, month)

    now = now_tz()
    can_next = (year, month) < (now.year, now.month)

    if len(rows) < 2:
        await respond(callback,
            f"📊 {month_title(year, month)}: меньше 2 измерений.",
            kb=kb_chart_nav(year, month, can_next),
        )
        return

    png = await _render_chart_png_async(rows, target, f"{CHILD_NAME} — {month_title(year, month)}")
    values = [r["pef_value"] for r in rows]

    await answer_callback(callback)
    await callback.message.answer_photo(
        BufferedInputFile(png, filename="chart.png"),
        caption=f"📊 {CHILD_NAME} — {month_title(year, month)}. Норма: {target} л/мин\n"
                f"🏆 Лучший: {max(values)} | ⚠️ Худший: {min(values)}",
        reply_markup=kb_chart_nav(year, month, can_next),
    )


@router.callback_query(F.data == "chart")
async def cb_chart(callback: types.CallbackQuery):
    now = now_tz()
    await _send_month_chart(callback, now.year, now.month)


@router.callback_query(F.data.regexp(r"^chart_\d{4}-\d{2}$"))
async def cb_chart_month(callback: types.CallbackQuery):
    parsed = parse_chart_month(callback.data)
    if not parsed:
        await callback.answer("❌ Неверный месяц.", show_alert=True)
        return
    await _send_month_chart(callback, parsed[0], parsed[1])


@router.callback_query(F.data.startswith("chart_dl_"))
async def cb_chart_download(callback: types.CallbackQuery):
    parsed = parse_chart_month(callback.data.replace("chart_dl_", "chart_"))
    if not parsed:
        await callback.answer("❌ Неверный месяц.", show_alert=True)
        return
    year, month = parsed
    rows = await _db(get_measurements_for_month, DB_PATH, CHILD_ID, year, month)
    if len(rows) < 2:
        await callback.answer("В этом месяце меньше 2 измерений.", show_alert=True)
        return
    target = await _db(get_effective_target)
    png = await _render_chart_png_async(rows, target, f"{CHILD_NAME} — {month_title(year, month)}")
    await answer_callback(callback)
    await callback.message.answer_document(
        BufferedInputFile(png, filename=f"chart_{CHILD_NAME}_{year:04d}-{month:02d}.png"),
        caption=f"📥 График за {month_title(year, month)}",
        reply_markup=kb_back(),
    )


# ---------------------------------------------------------------------------
# SUMMARY — today's overview (parents only)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "summary")
async def cb_summary(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    target = await _db(get_effective_target)
    today = await _db(get_today_measurements, DB_PATH, CHILD_ID)

    morning = [m for m in today if m["time_of_day"] == "morning"]
    evening = [m for m in today if m["time_of_day"] == "evening"]

    m_str = ""
    if morning:
        vals = ", ".join(f"{m['pef_value']} {pef_zone(m['pef_value'], target)[0]}" for m in morning)
        m_str = f"🌅 Утро: {vals}"
    else:
        m_str = "🌅 Утро: ещё нет"

    e_str = ""
    if evening:
        vals = ", ".join(f"{m['pef_value']} {pef_zone(m['pef_value'], target)[0]}" for m in evening)
        e_str = f"🌆 Вечер: {vals}"
    else:
        e_str = "🌆 Вечер: ещё нет"

    # Stats
    stats = await _db(get_stats, DB_PATH, CHILD_ID)

    m_avg = stats.get('morning_avg')
    e_avg = stats.get('evening_avg')
    m_avg_str = f"{m_avg:.0f}" if m_avg is not None else "—"
    e_avg_str = f"{e_avg:.0f}" if e_avg is not None else "—"

    await respond(callback,
        f"📊 *{escape_md(CHILD_NAME)}* — сегодня, {now_tz().strftime('%d %B')}\n\n"
        f"{m_str}\n{e_str}\n\n"
        f"📈 Всего: {stats.get('total', 0)} | Среднее: {stats.get('avg', 0):.0f}\n"
        f"🌅 Утро avg: {m_avg_str} | 🌆 Вечер avg: {e_avg_str}",
        kb=kb_back(),
    )


# ---------------------------------------------------------------------------
# WEEKLY report
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "weekly")
async def cb_weekly(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    await callback.answer()
    await _send_weekly_report(callback.message)


async def _send_weekly_report(message=None):
    this_week, prev_week = await _db(get_last_two_weeks, DB_PATH, CHILD_ID)

    if not this_week:
        if message:
            await message.answer("📋 За эту неделю нет измерений.", reply_markup=kb_back())
        return

    # Compute
    this_vals = [m["pef_value"] for m in this_week]
    this_morning = [m["pef_value"] for m in this_week if m["time_of_day"] == "morning"]
    this_evening = [m["pef_value"] for m in this_week if m["time_of_day"] == "evening"]

    prev_vals = [m["pef_value"] for m in prev_week]
    prev_morning = [m["pef_value"] for m in prev_week if m["time_of_day"] == "morning"]
    prev_evening = [m["pef_value"] for m in prev_week if m["time_of_day"] == "evening"]

    best = max(this_vals)
    worst = min(this_vals)
    best_m = max(this_week, key=lambda m: m["pef_value"])
    worst_m = min(this_week, key=lambda m: m["pef_value"])

    text = f"📋 *{escape_md(CHILD_NAME)}* — неделя {this_week[0]['measured_at'][:10]} — {this_week[-1]['measured_at'][:10]}\n\n"
    text += f"Замеров: {len(this_week)} (🌅 {len(this_morning)} / 🌆 {len(this_evening)})\n"
    text += f"Среднее: {sum(this_vals)/len(this_vals):.0f}\n"
    text += f"🏆 Лучший: {best} ({tod_emoji(best_m['time_of_day'])} {best_m['measured_at'][:10]})\n"
    text += f"⚠️ Худший: {worst} ({tod_emoji(worst_m['time_of_day'])} {worst_m['measured_at'][:10]})\n"

    if prev_vals:
        this_m_avg = sum(this_morning) / len(this_morning) if this_morning else 0
        prev_m_avg = sum(prev_morning) / len(prev_morning) if prev_morning else 0
        this_e_avg = sum(this_evening) / len(this_evening) if this_evening else 0
        prev_e_avg = sum(prev_evening) / len(prev_evening) if prev_evening else 0

        text += "\n📈 Эта vs прошлая:\n"
        if this_morning and prev_morning:
            d = this_m_avg - prev_m_avg
            sign = "+" if d > 0 else ""
            zone = "🟢" if d >= 0 else "🟡"
            text += f"🌅 Утро: {prev_m_avg:.0f} → {this_m_avg:.0f} ({sign}{d:.0f}) {zone}\n"
        if this_evening and prev_evening:
            d = this_e_avg - prev_e_avg
            sign = "+" if d > 0 else ""
            zone = "🟢" if d >= 0 else "🟡"
            text += f"🌆 Вечер: {prev_e_avg:.0f} → {this_e_avg:.0f} ({sign}{d:.0f}) {zone}\n"

    if message:
        await message.answer(text, parse_mode="Markdown", reply_markup=kb_back())
    return text


# ---------------------------------------------------------------------------
# STATS (for child)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "stats")
async def cb_stats(callback: types.CallbackQuery):
    stats = await _db(get_stats, DB_PATH, CHILD_ID)
    target = await _db(get_effective_target)

    if stats.get("total", 0) == 0:
        await respond(callback, "📭 Нет измерений.", kb=kb_back())
        return

    trend = ""
    if stats.get("trend") is not None:
        sign = "+" if stats["trend"] > 0 else ""
        trend = f"\n📈 Тренд: {sign}{stats['trend']:.0f} л/мин"

    zone, _ = pef_zone(stats["latest"], target)

    tod_info = ""
    if stats.get("morning_avg"):
        tod_info += f"\n🌅 Утро avg: {stats['morning_avg']:.0f} ({stats['morning_count']} замеров)"
    if stats.get("evening_avg"):
        tod_info += f"\n🌆 Вечер avg: {stats['evening_avg']:.0f} ({stats['evening_count']} замеров)"

    await respond(callback,
        f"📊 *{escape_md(CHILD_NAME)}*\n\n"
        f"Всего: {stats['total']} | Сегодня: {stats['today_count']}\n"
        f"Среднее: {stats['avg']:.0f}\n"
        f"Мин: {stats['min']} | Макс: {stats['max']}\n"
        f"Последний: {stats['latest']} {zone}{tod_info}{trend}",
        kb=kb_back(),
    )


# ---------------------------------------------------------------------------
# BACK
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "back")
async def cb_back(callback: types.CallbackQuery, state: FSMContext, member=None):
    await send_main_menu(callback, callback.from_user.id, member=member)
    await state.clear()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery):
    await callback.answer()


# ---------------------------------------------------------------------------
# CATCH-ALL
# ---------------------------------------------------------------------------
_FSM_HINTS = {
    "Measurement:pef_input_hundreds": (
        "Выберите значение кнопками ниже или отправьте /cancel для отмены."
    ),
    "Measurement:pef_input_tens": (
        "Выберите значение кнопками ниже или отправьте /cancel для отмены."
    ),
    "Measurement:editing_target_pef": "Введите целое число 100–800 или /cancel.",
    "Registration:entering_family_name": (
        "Введите название семьи или отправьте /cancel для отмены."
    ),
    "Registration:entering_invite_code": (
        "Введите код приглашения или отправьте /cancel для отмены."
    ),
    "Registration:adding_child_name": (
        "Введите имя ребёнка или отправьте /cancel для отмены."
    ),
}


@router.message(F.text)
async def catch_all(message: types.Message, state: FSMContext, member=None):
    uid = message.from_user.id
    current = await state.get_state()
    if current:
        hint = _FSM_HINTS.get(current)
        if hint:
            await message.answer(hint, reply_markup=kb_back())
        return
    if _role(member, uid) == "unknown":
        await message.answer(
            "⚠️ Этот бот только для семьи. Обратитесь к администратору."
        )
        return
    await send_main_menu(message, uid, member=member)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
_scheduler_task = None


def _user_display_name(user_id: int, member=None) -> str:
    if _role(member, user_id) == "child":
        return CHILD_NAME
    if _role(member, user_id) == "parent":
        return "Родитель"
    return "Кто-то"


def _log_scheduler_crash(task: asyncio.Task):
    """Surface unexpected scheduler failures instead of losing them silently."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Планировщик упал: %s", exc, exc_info=exc)


async def on_startup():
    global _scheduler_task
    # Keep a strong reference: a bare create_task() result may be garbage
    # collected, silently stopping the scheduler.
    _scheduler_task = asyncio.create_task(scheduler_loop())
    _scheduler_task.add_done_callback(_log_scheduler_crash)
    logger.info("Бот запущен, планировщик активен")
    await _setup_menu_button()


async def _setup_menu_button():
    if not WEBAPP_URL or not WEBAPP_PORT:
        return
    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="💨 Дневник", web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )
        logger.info("Кнопка Mini App установлена: %s", WEBAPP_URL)
    except Exception as e:
        logger.error("Не удалось установить кнопку Mini App: %s", e)


async def _maybe_ping_child(tod: str, hours: dict, hour: int, minute: int, today: str):
    """Ping the child to do a measurement (child_morning / child_evening)."""
    key = "child_morning" if tod == "morning" else "child_evening"
    flag = f"child_{tod}"
    if hour != hours[key] or not is_reminder_minute(minute):
        return
    if await _db(was_reminder_sent, DB_PATH, today, flag, CHILD_ID):
        return
    if await _db(has_today_measurement, DB_PATH, CHILD_ID, tod, skip_auto=True):
        await _db(mark_reminder_sent, DB_PATH, today, flag, CHILD_ID)
        return
    icon = "☀️" if tod == "morning" else "🌙"
    try:
        await bot.send_message(
            CHILD_ID,
            f"{icon} Привет, *{escape_md(CHILD_NAME)}*! Пора сделать "
            f"{'утренний' if tod == 'morning' else 'вечерний'} замер 💨",
            parse_mode="Markdown",
        )
    except Exception as e:
        # Do not set the flag: retry on the next tick (the 2-minute window).
        logger.error("Не удалось напомнить ребёнку (%s): %s", tod, e)
        return
    await _db(mark_reminder_sent, DB_PATH, today, flag, CHILD_ID)
    logger.info("Напоминание ребёнку: %s", tod)


async def _escalate_parents(tod: str, hours: dict, hour: int, minute: int, today: str):
    """No measurement at deadline → auto-carry record + inform parents."""
    flag = f"{tod}_missing"
    auto_flag = f"auto_{tod}"
    key = f"parent_{tod}"
    if hour != hours[key] or not is_reminder_minute(minute):
        return
    if await _db(was_reminder_sent, DB_PATH, today, flag, CHILD_ID):
        return
    if await _db(has_today_measurement, DB_PATH, CHILD_ID, tod, skip_auto=True):
        await _db(mark_reminder_sent, DB_PATH, today, flag, CHILD_ID)
        return

    # Auto-carry: reuse last real value of this time of day
    last = await _db(get_last_of_tod, DB_PATH, CHILD_ID, tod)
    if last and not await _db(was_reminder_sent, DB_PATH, today, auto_flag, CHILD_ID):
        await _db(add_measurement, DB_PATH, last["pef_value"], tod, CHILD_ID, 0, source="auto")
        await _db(mark_reminder_sent, DB_PATH, today, auto_flag, CHILD_ID)
        logger.info("Авто-запись: %s = %d (%s)", tod, last["pef_value"], today)

    icon = "☀️" if tod == "morning" else "🌙"
    if last:
        text = (
            f"⚠️ {icon} *{escape_md(CHILD_NAME)}* не сделал {'утренний' if tod == 'morning' else 'вечерний'} замер.\n"
            f"🤖 Записали как в последний раз: *{last['pef_value']}* (авто, не измерено).\n"
            f"Скорректируйте, если знаете реальное значение."
        )
    else:
        text = (
            f"⚠️ {icon} *{escape_md(CHILD_NAME)}* ещё не сделал {'утренний' if tod == 'morning' else 'вечерний'} замер!\n"
            f"Напомните, пожалуйста."
        )

    delivered = False
    for pid in PARENT_IDS:
        try:
            await bot.send_message(pid, text, parse_mode="Markdown")
            delivered = True
        except Exception:
            pass
    if not delivered:
        # No parent received it — allow a retry on the next tick.
        logger.error("Эскалация родителям (%s) не доставлена, повтор", tod)
        return
    await _db(mark_reminder_sent, DB_PATH, today, flag, CHILD_ID)
    logger.info("Эскалация родителям: %s", tod)


async def scheduler_loop():
    """Main scheduler: child pings, auto-carry + parent escalation, weekly report."""
    logger.info("Планировщик запущен")
    while True:
        try:
            now = now_tz()
            today = now.strftime("%Y-%m-%d")
            hour = now.hour
            minute = now.minute

            hours = await _db(get_reminder_hours, DB_PATH)

            # Child pings (08:00 / 20:00 by default)
            await _maybe_ping_child("morning", hours, hour, minute, today)
            await _maybe_ping_child("evening", hours, hour, minute, today)

            # Parent escalation with auto-carry (10:00 / 22:00 by default)
            await _escalate_parents("morning", hours, hour, minute, today)
            await _escalate_parents("evening", hours, hour, minute, today)

            # Weekly report
            if now.weekday() == WEEKLY_REPORT_DAY and hour == WEEKLY_REPORT_HOUR and is_reminder_minute(minute):
                if not await _db(was_reminder_sent, DB_PATH, today, "weekly", CHILD_ID):
                    text = await _send_weekly_report()
                    if text:
                        delivered = False
                        for pid in PARENT_IDS:
                            try:
                                await bot.send_message(pid, text, parse_mode="Markdown")
                                delivered = True
                            except Exception:
                                pass
                        if delivered:
                            await _db(mark_reminder_sent, DB_PATH, today, "weekly", CHILD_ID)
                            logger.info("Недельный отчёт отправлен")
                        else:
                            logger.error("Недельный отчёт не доставлен, повтор")

        except Exception as e:
            logger.error("Ошибка планировщика: %s", e)

        # Align the next tick to the minute boundary so the 2-minute reminder
        # window cannot be skipped by drift.
        await asyncio.sleep(seconds_until_next_minute(now_tz()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _web_services(state: dict, shutdown_event: asyncio.Event) -> dict:
    return {"config": app_config, "bot": bot, "state": state, "shutdown_event": shutdown_event}


async def run_async() -> int:
    dp.startup.register(on_startup)

    state = {"bot_ok": False, "crashed": False}
    shutdown_event = asyncio.Event()

    async def _run_polling():
        state["bot_ok"] = True
        try:
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            state["crashed"] = True
            logger.error("Polling остановлен с ошибкой: %s", e)
            shutdown_event.set()
        finally:
            state["bot_ok"] = False

    if not WEBAPP_PORT:
        try:
            await dp.start_polling(bot)
        finally:
            await bot.session.close()
        return 0

    polling = asyncio.create_task(_run_polling())
    try:
        await run_webapp(_web_services(state, shutdown_event))
    finally:
        polling.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await polling
        await bot.session.close()

    return 1 if state["crashed"] else 0


def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан в .env!")
        sys.exit(1)
    if CHILD_ID == 0:
        print("❌ CHILD_ID не задан в .env!")
        sys.exit(1)
    if not PARENT_IDS or all(p == 0 for p in PARENT_IDS):
        print("❌ PARENT_IDS не заданы в .env (все нули)!")
        sys.exit(1)

    lock_fd = acquire_lock()
    try:
        init_db(DB_PATH)
        logger.info("Пикфлоуметр: %s, родители: %s", CHILD_NAME, PARENT_IDS)
        exit_code = asyncio.run(run_async())
        if exit_code:
            sys.exit(exit_code)
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
