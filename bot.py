import os
import sys
import io
import fcntl
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F, Router
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
    PEF_NORM_BY_AGE, ZONE_GREEN, ZONE_YELLOW, ZONE_RED,
    REMINDER_MORNING_DEADLINE, REMINDER_EVENING_DEADLINE,
    WEEKLY_REPORT_DAY, WEEKLY_REPORT_HOUR, TZ_OFFSET,
    is_parent, is_child, get_effective_target,
)
from database import (
    init_db, add_measurement, edit_measurement, delete_measurement,
    get_last_measurement, get_all_measurements, get_today_measurements,
    has_today_measurement, get_measurements_paginated,
    get_measurements_for_chart, get_stats, get_last_two_weeks,
    mark_reminder_sent, was_reminder_sent,
    get_setting, set_setting,
)

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


def acquire_lock() -> int:
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd.fileno()
    except BlockingIOError:
        logger.error("Бот уже запущен!")
        lock_fd.close()
        sys.exit(1)


def release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------
class Measurement(StatesGroup):
    waiting_delete_confirm = State()
    editing_target_pef = State()
    # Пошаговый inline-ввод ПСВ (сотни → десятки)
    pef_input_hundreds = State()
    pef_input_tens = State()


def get_effective_target() -> int:
    """Get target PEF from DB settings, fallback to config."""
    try:
        val = int(get_setting(DB_PATH, "target_pef", str(TARGET_PEF)))
        return val if val > 0 else 300
    except (ValueError, Exception):
        return TARGET_PEF or 300


def auto_time_of_day() -> str:
    """Auto-detect morning/evening based on current hour."""
    hour = now_tz().hour
    return "morning" if hour < 12 else "evening"


def is_reminder_minute(minute: int) -> bool:
    """True for minutes inside the reminder window (0–1).

    The scheduler ticks every 60 seconds, so an exact 'minute == 0' check
    can be skipped by sleep drift. A two-minute window guarantees the
    reminder fires; per-day flags in the DB prevent duplicates.
    """
    return minute < 2


def tod_emoji(tod: str) -> str:
    return "☀️" if tod == "morning" else "🌙"


def tod_label(tod: str) -> str:
    return "Утро" if tod == "morning" else "Вечер"


def pef_zone(value: int, target: int) -> tuple[str, str]:
    pct = (value / target) * 100 if target else 100
    if pct >= ZONE_GREEN:
        return "🟢", "Зелёная"
    elif pct >= ZONE_YELLOW:
        return "🟡", "Жёлтая"
    else:
        return "🔴", "Красная"


def pct_of(value: int, target: int) -> int:
    return int((value / target) * 100) if target else 100


async def answer_callback(callback: types.CallbackQuery):
    try:
        await callback.answer()
    except TelegramBadRequest:
        # query already answered or too old — safe to ignore
        pass
    try:
        await callback.message.delete()
    except (TelegramBadRequest, Exception):
        pass


async def respond(callback: types.CallbackQuery, text: str,
                  kb: InlineKeyboardMarkup = None, parse_mode: str = "Markdown"):
    await answer_callback(callback)
    return await callback.message.answer(text, parse_mode=parse_mode, reply_markup=kb)


def safe_delete(msg):
    try:
        asyncio.create_task(msg.delete())
    except Exception:
        pass


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
        # У ребёнка — только "Измерение", ничего лишнего
        pass
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
                InlineKeyboardButton(text=f"🗑️", callback_data=f"del_{m['id']}"),
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
    today = get_today_measurements(DB_PATH, CHILD_ID)
    target = get_effective_target()

    morning_val = None
    evening_val = None
    for m in today:
        if m["time_of_day"] == "morning":
            morning_val = m["pef_value"]
        elif m["time_of_day"] == "evening":
            evening_val = m["pef_value"]

    morning_display = f"{tod_emoji('morning')} {morning_val} {pef_zone(morning_val, target)[0]}" if morning_val else f"{tod_emoji('morning')} —"
    evening_display = f"{tod_emoji('evening')} {evening_val} {pef_zone(evening_val, target)[0]}" if evening_val else f"{tod_emoji('evening')} —"

    # Last measurement diff
    all_m = get_all_measurements(DB_PATH, CHILD_ID)
    diff = ""
    if len(all_m) >= 2:
        d = all_m[0]["pef_value"] - all_m[1]["pef_value"]
        sign = "+" if d > 0 else ""
        zone, _ = pef_zone(all_m[0]["pef_value"], target)
        diff = f"\nПоследний: {all_m[0]['pef_value']} {zone} ({sign}{d})"

    return (
        f"👋 *{CHILD_NAME}* | Целевая: {target} л/мин\n\n"
        f"Сегодня: {morning_display} | {evening_display}"
        f"{diff}"
    )


# ---------------------------------------------------------------------------
# Send main menu
# ---------------------------------------------------------------------------
async def send_main_menu(message_or_callback, user_id: int):
    is_p = is_parent(user_id)
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
@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if not is_parent(uid) and not is_child(uid):
        await message.answer(
            "⚠️ Этот бот только для семьи. Обратитесь к администратору."
        )
        return

    who = "👨‍👧 Родитель" if is_parent(uid) else f"👶 {CHILD_NAME}"
    await message.answer(f"✅ Привет! Вы вошли как *{who}*", parse_mode="Markdown")
    await send_main_menu(message, uid)
    await state.clear()
    logger.info("Пользователь %d (%s) вошёл в бот", uid, who)


# ---------------------------------------------------------------------------
# ADD measurement — auto time of day, inline пошаговый ввод
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "add")
async def cb_add(callback: types.CallbackQuery, state: FSMContext):
    tod = auto_time_of_day()
    tod_icon = tod_emoji(tod)
    tod_name = tod_label(tod)

    # Check if already done
    if has_today_measurement(DB_PATH, CHILD_ID, tod):
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
        if has_today_measurement(DB_PATH, CHILD_ID, tod):
            tod = "evening" if tod == "morning" else "morning"

    who = callback.from_user.id
    mid = add_measurement(DB_PATH, pef, tod, CHILD_ID, who)

    target = get_effective_target()
    zone_emoji, zone_name = pef_zone(pef, target)

    # Diff
    all_m = get_all_measurements(DB_PATH, CHILD_ID)
    diff_msg = ""
    if len(all_m) >= 2:
        d = pef - all_m[1]["pef_value"]
        sign = "+" if d > 0 else ""
        diff_msg = f"\n📈 Изменение: {sign}{d} л/мин"

    await respond(callback,
        f"✅ {tod_emoji(tod)} {tod_label(tod)}: *{pef}* л/мин {zone_emoji}\n"
        f"Зона: {zone_name} ({pct_of(pef, target)}% от нормы){diff_msg}",
    )

    # Notify other parents
    added_by_name = _user_display_name(who)
    for pid in PARENT_IDS:
        if pid != who and pid != CHILD_ID:
            try:
                await bot.send_message(
                    pid,
                    f"📝 {added_by_name} добавил для *{CHILD_NAME}*: "
                    f"{pef} л/мин {zone_emoji} ({tod_label(tod)})",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    # Alert if red zone
    if pct_of(pef, target) < ZONE_YELLOW:
        for pid in PARENT_IDS:
            try:
                await bot.send_message(
                    pid,
                    f"🚨 *{CHILD_NAME}*: ПСВ *{pef}* л/мин — {zone_name}!\n"
                    f"Норма: {target} л/мин ({pct_of(pef, target)}%). Свяжитесь с врачом.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    await state.clear()
    await send_main_menu(callback, who)
    logger.info("Измерение: %d л/мин, %s, добавил %d", pef, tod, who)


async def _save_edit_last(callback: types.CallbackQuery, state: FSMContext, new_val: int, data: dict):
    """Сохранить исправление последнего измерения."""
    mid = data.get("edit_id")
    if mid is None:
        await callback.answer("❌ Ошибка: нет ID записи", show_alert=True)
        return

    ok = edit_measurement(DB_PATH, mid, new_val, CHILD_ID)
    if ok:
        target = get_effective_target()
        zone, _ = pef_zone(new_val, target)
        await respond(callback,
            f"✅ Исправлено: *{new_val}* л/мин {zone}",
        )
    else:
        await respond(callback, "❌ Не удалось изменить запись.")

    await state.clear()
    await send_main_menu(callback, callback.from_user.id)


async def _save_edit_any(callback: types.CallbackQuery, state: FSMContext, new_val: int, data: dict):
    """Сохранить исправление любого измерения."""
    mid = data.get("edit_id")
    if mid is None:
        await callback.answer("❌ Ошибка: нет ID записи", show_alert=True)
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("UPDATE measurements SET pef_value = ? WHERE id = ? AND user_id = ?",
                       (new_val, mid, CHILD_ID))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()

    if ok:
        target = get_effective_target()
        zone, _ = pef_zone(new_val, target)
        await respond(callback,
            f"✅ Запись #{mid} исправлена: *{new_val}* л/мин {zone}",
        )
    else:
        await respond(callback, "❌ Не удалось изменить запись.")

    await state.clear()
    await _show_history_from_callback(callback)


# ---------------------------------------------------------------------------
# Пошаговый inline-ввод: сотни → десятки
# ---------------------------------------------------------------------------
@router.callback_query(Measurement.pef_input_hundreds, F.data.startswith("h_"))
async def cb_select_hundreds(callback: types.CallbackQuery, state: FSMContext):
    h = int(callback.data.replace("h_", ""))
    await state.update_data(hundreds=h)
    await callback.answer()
    await callback.message.edit_text(
        f"💨 Выбрано: {h}__ л/мин\n\n"
        f"Теперь выбери десятки:",
        reply_markup=kb_pef_tens(),
    )
    await state.set_state(Measurement.pef_input_tens)


@router.callback_query(Measurement.pef_input_tens, F.data.startswith("t_"))
async def cb_select_tens(callback: types.CallbackQuery, state: FSMContext):
    d = int(callback.data.replace("t_", ""))
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
        await _save_edit_last(callback, state, pef, data)
    elif context == "edit_any":
        await _save_edit_any(callback, state, pef, data)


@router.callback_query(Measurement.pef_input_hundreds, F.data == "back")
@router.callback_query(Measurement.pef_input_tens, F.data == "back")
async def cb_back_from_pef_input(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(callback, callback.from_user.id)


# ---------------------------------------------------------------------------
# EDIT last measurement — inline пошаговый ввод
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "edit_last")
async def cb_edit_last(callback: types.CallbackQuery, state: FSMContext):
    last = get_last_measurement(DB_PATH, CHILD_ID)
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
async def cb_history(callback: types.CallbackQuery, state: FSMContext):
    await _show_history(callback, page=1)


@router.callback_query(F.data.startswith("hist_page_"))
async def cb_history_page(callback: types.CallbackQuery, state: FSMContext):
    page = int(callback.data.replace("hist_page_", ""))
    await _show_history(callback, page=page)


async def _show_history(callback: types.CallbackQuery, page: int = 1):
    measurements, total, total_pages = get_measurements_paginated(DB_PATH, CHILD_ID, page=page, per_page=10)
    target = get_effective_target()
    is_p = is_parent(callback.from_user.id)

    if not measurements:
        await respond(callback, "📭 Нет измерений.", kb=kb_back())
        return

    lines = []
    for m in measurements:
        zone, _ = pef_zone(m["pef_value"], target)
        ts = m["measured_at"][5:16].replace("T", " ")
        who = "👨‍👧" if is_parent(m.get("added_by", 0)) else "👶"
        lines.append(f"{tod_emoji(m['time_of_day'])} {ts} → *{m['pef_value']}* {zone} {who}")

    kb = kb_pagination(page, total_pages, is_p, measurements)

    await respond(callback,
        f"📋 История *{CHILD_NAME}*:\n\n" + "\n".join(lines),
        kb=kb,
    )


# ---------------------------------------------------------------------------
# EDIT any measurement (parent only) — inline пошаговый ввод
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("edit_"))
async def cb_edit_any(callback: types.CallbackQuery, state: FSMContext):
    # Skip if this is edit_last (handled elsewhere)
    if callback.data == "edit_last":
        return
    if not is_parent(callback.from_user.id):
        await callback.answer("⚠️ Только родители могут редактировать.", show_alert=True)
        return

    mid = int(callback.data.replace("edit_", ""))
    # Fetch the measurement
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM measurements WHERE id = ?", (mid,)).fetchone()
    conn.close()

    if not row:
        await callback.answer("❌ Запись не найдена.", show_alert=True)
        return

    await state.update_data(edit_id=mid, input_context="edit_any")
    await respond(callback,
        f"✏️ Запись #{mid}: *{row['pef_value']}* л/мин "
        f"({tod_emoji(row['time_of_day'])} {tod_label(row['time_of_day'])}, "
        f"{row['measured_at'][:10]})\n\n"
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
async def cb_delete_confirm(callback: types.CallbackQuery, state: FSMContext):
    if not is_parent(callback.from_user.id):
        await callback.answer("⚠️ Только родители могут удалять.", show_alert=True)
        return

    mid = int(callback.data.replace("del_confirm_", ""))
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM measurements WHERE id = ? AND user_id = ?", (mid, CHILD_ID))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()

    if ok:
        await callback.answer("✅ Запись удалена.", show_alert=True)
    else:
        await callback.answer("❌ Не удалось удалить.", show_alert=True)

    await state.clear()
    await _show_history_from_callback(callback)


@router.callback_query(F.data.startswith("del_"))
async def cb_delete(callback: types.CallbackQuery, state: FSMContext):
    if not is_parent(callback.from_user.id):
        await callback.answer("⚠️ Только родители могут удалять.", show_alert=True)
        return

    mid = int(callback.data.replace("del_", ""))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM measurements WHERE id = ? AND user_id = ?", (mid, CHILD_ID)).fetchone()
    conn.close()

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
# ---------------------------------------------------------------------------
# SETTINGS — parents only
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "settings")
async def cb_settings(callback: types.CallbackQuery):
    if not is_parent(callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return

    target = get_effective_target()
    measurements = get_all_measurements(DB_PATH, CHILD_ID)

    await respond(callback,
        f"⚙️ *Настройки*\n\n"
        f"👤 Ребёнок: *{CHILD_NAME}*\n"
        f"🎯 Целевая ПСВ: *{target}* л/мин\n"
        f"📊 Всего замеров: {len(measurements)}\n\n"
        f"Выберите действие:",
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🎯 Изменить цель ({target})", callback_data="change_target"),
            ],
            [
                InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="export"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back"),
            ],
        ]),
    )


# ---------------------------------------------------------------------------
# Change target PEF
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "change_target")
async def cb_change_target(callback: types.CallbackQuery, state: FSMContext):
    if not is_parent(callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return

    current = get_effective_target()
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

    set_setting(DB_PATH, "target_pef", str(new_val))
    zone, _ = pef_zone(new_val, new_val)  # target is 100% of itself

    await message.answer(
        f"✅ Цель изменена: *{new_val}* л/мин {zone}",
        parse_mode="Markdown",
    )

    await state.clear()
    # Send settings screen again
    await _send_settings_from_message(message)


async def _send_settings_from_message(message: types.Message):
    target = get_effective_target()
    measurements = get_all_measurements(DB_PATH, CHILD_ID)

    await message.answer(
        f"⚙️ *Настройки*\n\n"
        f"👤 Ребёнок: *{CHILD_NAME}*\n"
        f"🎯 Целевая ПСВ: *{target}* л/мин\n"
        f"📊 Всего замеров: {len(measurements)}\n\n"
        f"Выберите действие:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🎯 Изменить цель ({target})", callback_data="change_target"),
            ],
            [
                InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="export"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back"),
            ],
        ]),
    )


# ---------------------------------------------------------------------------
# EXPORT CSV
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "export")
async def cb_export(callback: types.CallbackQuery):
    measurements = get_all_measurements(DB_PATH, CHILD_ID)
    if not measurements:
        await respond(callback, "📭 Нет данных для экспорта.", kb=kb_back())
        return

    target = get_effective_target()

    # Build CSV content
    lines = ["Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил"]
    for m in reversed(measurements):  # oldest first
        ts = m["measured_at"].replace("T", " ")
        date_part = ts[:10]
        time_part = ts[11:16]
        pct = pct_of(m["pef_value"], target)
        zone_emoji, zone_name = pef_zone(m["pef_value"], target)
        who = _user_display_name(m.get("added_by", 0))
        lines.append(
            f"{date_part},{time_part},{tod_label(m['time_of_day'])},"
            f"{m['pef_value']},{pct}%,{zone_name},{who}"
        )

    csv_content = "\n".join(lines) + "\n"

    # Also build a summary
    stats = get_stats(DB_PATH, CHILD_ID)
    summary = (
        f"\n# Статистика\n"
        f"# Всего замеров: {stats.get('total', 0)}\n"
        f"# Среднее: {stats.get('avg', 0):.0f} л/мин\n"
        f"# Мин: {stats.get('min', 0)} | Макс: {stats.get('max', 0)}\n"
        f"# Цель: {target} л/мин\n"
        f"# Ребёнок: {CHILD_NAME}\n"
    )

    csv_content = summary + csv_content

    filename = f"peakflow_{CHILD_NAME}_{now_tz().strftime('%Y%m%d_%H%M')}.csv"

    await answer_callback(callback)
    await callback.message.answer_document(
        BufferedInputFile(csv_content.encode("utf-8-sig"), filename=filename),
        caption=f"📥 Экспорт: {len(measurements)} замеров",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")]
        ]),
    )


# ---------------------------------------------------------------------------
# Helper: show history from message (after edit)
# ---------------------------------------------------------------------------
async def _show_history_from_message(message: types.Message):
    measurements, total, total_pages = get_measurements_paginated(DB_PATH, CHILD_ID, page=1, per_page=10)
    target = get_effective_target()
    is_p = is_parent(message.from_user.id)

    if not measurements:
        await message.answer("📭 Нет измерений.", reply_markup=kb_back())
        return

    lines = []
    for m in measurements:
        zone, _ = pef_zone(m["pef_value"], target)
        ts = m["measured_at"][5:16].replace("T", " ")
        who = "👨‍👧" if is_parent(m.get("added_by", 0)) else "👶"
        lines.append(f"{tod_emoji(m['time_of_day'])} {ts} → *{m['pef_value']}* {zone} {who}")

    kb = kb_pagination(1, total_pages, is_p, measurements)

    await message.answer(
        f"📋 История *{CHILD_NAME}*:\n\n" + "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def _show_history_from_callback(callback: types.CallbackQuery):
    measurements, total, total_pages = get_measurements_paginated(DB_PATH, CHILD_ID, page=1, per_page=10)
    target = get_effective_target()
    is_p = is_parent(callback.from_user.id)

    if not measurements:
        await callback.message.answer("📭 Нет измерений.", reply_markup=kb_back())
        return

    lines = []
    for m in measurements:
        zone, _ = pef_zone(m["pef_value"], target)
        ts = m["measured_at"][5:16].replace("T", " ")
        who = "👨‍👧" if is_parent(m.get("added_by", 0)) else "👶"
        lines.append(f"{tod_emoji(m['time_of_day'])} {ts} → *{m['pef_value']}* {zone} {who}")

    kb = kb_pagination(1, total_pages, is_p, measurements)

    await callback.message.answer(
        f"📋 История *{CHILD_NAME}*:\n\n" + "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# CHART
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "chart")
async def cb_chart(callback: types.CallbackQuery):
    data = get_measurements_for_chart(DB_PATH, CHILD_ID, days=30)
    target = get_effective_target()

    if len(data) < 2:
        await respond(callback, "📊 Нужно минимум 2 измерения.", kb=kb_back())
        return

    dates = [datetime.fromisoformat(d["measured_at"]) for d in data]
    values = [d["pef_value"] for d in data]

    morning_d = [dates[i] for i, d in enumerate(data) if d["time_of_day"] == "morning"]
    morning_v = [values[i] for i, d in enumerate(data) if d["time_of_day"] == "morning"]
    evening_d = [dates[i] for i, d in enumerate(data) if d["time_of_day"] == "evening"]
    evening_v = [values[i] for i, d in enumerate(data) if d["time_of_day"] == "evening"]

    fig, ax = plt.subplots(figsize=(10, 5))
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
    ax.set_title(f"Пикфлоуметрия — {CHILD_NAME}")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    buf.seek(0)
    plt.close(fig)

    await answer_callback(callback)
    await callback.message.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="chart.png"),
        caption=f"📊 {CHILD_NAME} — 30 дней. Норма: {target} л/мин\n"
                f"🏆 Лучший: {max(values)} | ⚠️ Худший: {min(values)}",
        reply_markup=kb_back(),
    )


# ---------------------------------------------------------------------------
# SUMMARY — today's overview (parents only)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "summary")
async def cb_summary(callback: types.CallbackQuery):
    target = get_effective_target()
    today = get_today_measurements(DB_PATH, CHILD_ID)

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
    stats = get_stats(DB_PATH, CHILD_ID)

    m_avg = stats.get('morning_avg')
    e_avg = stats.get('evening_avg')
    m_avg_str = f"{m_avg:.0f}" if m_avg is not None else "—"
    e_avg_str = f"{e_avg:.0f}" if e_avg is not None else "—"

    await respond(callback,
        f"📊 *{CHILD_NAME}* — сегодня, {now_tz().strftime('%d %B')}\n\n"
        f"{m_str}\n{e_str}\n\n"
        f"📈 Всего: {stats.get('total', 0)} | Среднее: {stats.get('avg', 0):.0f}\n"
        f"🌅 Утро avg: {m_avg_str} | 🌆 Вечер avg: {e_avg_str}",
        kb=kb_back(),
    )


# ---------------------------------------------------------------------------
# WEEKLY report
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "weekly")
async def cb_weekly(callback: types.CallbackQuery):
    await callback.answer()
    await _send_weekly_report(callback.message)


async def _send_weekly_report(message=None):
    this_week, prev_week = get_last_two_weeks(DB_PATH, CHILD_ID)
    target = get_effective_target()

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

    text = f"📋 *{CHILD_NAME}* — неделя {this_week[0]['measured_at'][:10]} — {this_week[-1]['measured_at'][:10]}\n\n"
    text += f"Замеров: {len(this_week)} (🌅 {len(this_morning)} / 🌆 {len(this_evening)})\n"
    text += f"Среднее: {sum(this_vals)/len(this_vals):.0f}\n"
    text += f"🏆 Лучший: {best} ({tod_emoji(best_m['time_of_day'])} {best_m['measured_at'][:10]})\n"
    text += f"⚠️ Худший: {worst} ({tod_emoji(worst_m['time_of_day'])} {worst_m['measured_at'][:10]})\n"

    if prev_vals:
        this_m_avg = sum(this_morning) / len(this_morning) if this_morning else 0
        prev_m_avg = sum(prev_morning) / len(prev_morning) if prev_morning else 0
        this_e_avg = sum(this_evening) / len(this_evening) if this_evening else 0
        prev_e_avg = sum(prev_evening) / len(prev_evening) if prev_evening else 0

        text += f"\n📈 Эта vs прошлая:\n"
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
    stats = get_stats(DB_PATH, CHILD_ID)
    target = get_effective_target()

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
        f"📊 *{CHILD_NAME}*\n\n"
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
async def cb_back(callback: types.CallbackQuery, state: FSMContext):
    await send_main_menu(callback, callback.from_user.id)
    await state.clear()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery):
    await callback.answer()


# ---------------------------------------------------------------------------
# CATCH-ALL
# ---------------------------------------------------------------------------
@router.message(F.text)
async def catch_all(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current:
        return
    await send_main_menu(message, message.from_user.id)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
def _user_display_name(user_id: int) -> str:
    if is_child(user_id):
        return CHILD_NAME
    if user_id in PARENT_IDS:
        return "Родитель"
    return "Кто-то"


async def on_startup():
    asyncio.create_task(scheduler_loop())
    logger.info("Бот запущен, планировщик активен")


async def scheduler_loop():
    """Main scheduler: missing reminders + weekly report."""
    logger.info("Планировщик запущен")
    while True:
        try:
            now = now_tz()
            today = now.strftime("%Y-%m-%d")
            hour = now.hour
            minute = now.minute

            # Morning missing reminder
            if hour == REMINDER_MORNING_DEADLINE and is_reminder_minute(minute):
                if not was_reminder_sent(DB_PATH, today, "morning_missing"):
                    if not has_today_measurement(DB_PATH, CHILD_ID, "morning"):
                        for pid in PARENT_IDS:
                            try:
                                await bot.send_message(
                                    pid,
                                    f"⏰ *{CHILD_NAME}* ещё не сделал утренний замер!\n"
                                    f"Напомните, пожалуйста.",
                                    parse_mode="Markdown",
                                )
                            except Exception:
                                pass
                        mark_reminder_sent(DB_PATH, today, "morning_missing")
                        logger.info("Напоминание: утренний замер пропущен")

            # Evening missing reminder
            if hour == REMINDER_EVENING_DEADLINE and is_reminder_minute(minute):
                if not was_reminder_sent(DB_PATH, today, "evening_missing"):
                    if not has_today_measurement(DB_PATH, CHILD_ID, "evening"):
                        for pid in PARENT_IDS:
                            try:
                                await bot.send_message(
                                    pid,
                                    f"⏰ *{CHILD_NAME}* ещё не сделал вечерний замер!",
                                    parse_mode="Markdown",
                                )
                            except Exception:
                                pass
                        mark_reminder_sent(DB_PATH, today, "evening_missing")
                        logger.info("Напоминание: вечерний замер пропущен")

            # Weekly report
            if now.weekday() == WEEKLY_REPORT_DAY and hour == WEEKLY_REPORT_HOUR and is_reminder_minute(minute):
                if not was_reminder_sent(DB_PATH, today, "weekly"):
                    text = await _send_weekly_report()
                    if text:
                        for pid in PARENT_IDS:
                            try:
                                await bot.send_message(pid, text, parse_mode="Markdown")
                            except Exception:
                                pass
                        mark_reminder_sent(DB_PATH, today, "weekly")
                        logger.info("Недельный отчёт отправлен")

        except Exception as e:
            logger.error("Ошибка планировщика: %s", e)

        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
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

        dp.startup.register(on_startup)
        dp.run_polling(bot)
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
