"""REST API Mini App. SP2a: чтение данных дневника ПСВ."""
import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from database import (
    add_or_replace_measurement,
    backup_db,
    delete_measurement,
    edit_measurement,
    get_all_measurements,
    get_available_months,
    get_last_measurement,
    get_last_of_tod,
    get_measurements_between,
    get_measurements_for_month,
    get_measurements_paginated,
    get_previous_of_tod,
    get_reminder_hours,    get_stats,
    get_today_measurements,
    get_effective_target as _db_effective_target,
    get_member,
    set_note,
    set_setting,
    validate_reminder_hours,
)
from report import build_csv_content as _build_csv, parse_month
from web.auth import get_user_from_init_data
from web.notify import notify_added, notify_red_zone

logger = logging.getLogger(__name__)


async def _db(func, *args, **kwargs):
    """Run a synchronous SQLite helper off the event loop.

    uvicorn shares the event loop with aiogram, so a blocking query freezes
    both the Mini App and the bot.
    """
    return await asyncio.to_thread(func, *args, **kwargs)

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _effective_target(config) -> int:
    return _db_effective_target(config.DB_PATH, getattr(config, "TARGET_PEF", 0))


def _auto_time_of_day(config) -> str:
    offset = getattr(config, "TZ_OFFSET", 0)
    hour = datetime.now(timezone(timedelta(hours=offset))).hour
    return "morning" if hour < 12 else "evening"


def _pct_of(pef: int, target: int) -> int:
    return int((pef / target) * 100) if target else 100


def _pef_zone(pef: int, target: int, config) -> str:
    pct = (pef / target) * 100 if target else 100
    if pct >= getattr(config, "ZONE_GREEN", 80):
        return "green"
    if pct >= getattr(config, "ZONE_YELLOW", 60):
        return "yellow"
    return "red"


def _schedule_add_notifications(background, bot, config, who: int, pef: int,
                                tod: str, target: int, pct: int) -> None:
    """Queue Telegram notifications off the request path (BackgroundTasks).

    A slow or unavailable Telegram API must not delay the HTTP response, so
    the sends run after it is returned.
    """
    background.add_task(notify_added, bot, config, who, pef, tod, target)
    if pct < getattr(config, "ZONE_YELLOW", 60):
        background.add_task(notify_red_zone, bot, config, pef, tod, target, who=who)


class MeasurementIn(BaseModel):
    pef: int = Field(ge=100, le=690)


class NoteIn(BaseModel):
    note: str = ""


class TargetIn(BaseModel):
    target_pef: int = Field(ge=100, le=800)


class RemindersIn(BaseModel):
    child_morning: int = Field(ge=0, le=23)
    child_evening: int = Field(ge=0, le=23)
    parent_morning: int = Field(ge=0, le=23)
    parent_evening: int = Field(ge=0, le=23)


REMINDER_KEYS = ("child_morning", "child_evening", "parent_morning", "parent_evening")


def _today(config) -> str:
    offset = getattr(config, "TZ_OFFSET", 0)
    return datetime.now(timezone(timedelta(hours=offset))).strftime("%Y-%m-%d")


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = f"{year:04d}-{month:02d}-01"
    first_next = (datetime(year, month, 28) + timedelta(days=4)).replace(day=1)
    last_day = first_next - timedelta(days=1)
    return start, last_day.strftime("%Y-%m-%d")


def _who(config, added_by: int) -> str:
    if added_by == getattr(config, "CHILD_ID", 0):
        return getattr(config, "CHILD_NAME", "Ребёнок")
    if added_by in (getattr(config, "PARENT_IDS", []) or []):
        return "Родитель"
    return "Кто-то"


def _content_disposition(filename: str) -> str:
    """RFC 5987 attachment header safe for non-ASCII names."""
    safe = filename.replace("\r", " ").replace("\n", " ").replace('"', "'")
    ascii_fallback = safe.encode("ascii", "ignore").decode("ascii") or "export"
    encoded = quote(safe, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=utf-8''{encoded}"


def create_app(services: dict) -> FastAPI:
    app = FastAPI(title="Peakflow Bot Mini App API", docs_url=None, redoc_url=None)
    config = services.get("config")

    def _resolve_user(init_data: str | None) -> dict:
        token = getattr(config, "BOT_TOKEN", "") or ""
        if not token or not init_data:
            raise HTTPException(403, "Нет доступа")
        user = get_user_from_init_data(init_data, token)
        if not user:
            raise HTTPException(403, "Нет доступа")
        uid = user.get("id")
        member = get_member(config.DB_PATH, uid)
        if member:
            role = member["role"]
            family_id = member["family_id"]
        else:
            # Fallback for family #1 before its members are read (defensive).
            if uid == getattr(config, "CHILD_ID", 0):
                role, family_id = "child", 1
            elif uid in (getattr(config, "PARENT_IDS", []) or []):
                role, family_id = "parent", 1
            else:
                raise HTTPException(403, "Нет доступа")
        return {"user": user, "role": role, "family_id": family_id, "member": member}

    def require_user(x_telegram_init_data: str | None = Header(None)) -> dict:
        return _resolve_user(x_telegram_init_data)

    def require_parent(auth: dict = Depends(require_user)) -> dict:
        if auth["role"] != "parent":
            raise HTTPException(403, "Только родители")
        return auth

    @app.get("/healthz")
    async def healthz():
        state = services.get("state")
        if state is not None and not state.get("bot_ok", True):
            return JSONResponse(status_code=503, content={"status": "bot down"})
        return {"status": "ok"}

    @app.get("/api/me")
    async def me(auth: dict = Depends(require_user)):
        return {
            "user": auth["user"],
            "role": auth["role"],
            "child_name": getattr(config, "CHILD_NAME", "Ребёнок"),
            "target_pef": await _db(_effective_target, config),
            "zones": {
                "green": getattr(config, "ZONE_GREEN", 80),
                "yellow": getattr(config, "ZONE_YELLOW", 60),
            },
        }

    @app.get("/api/status")
    async def status(auth: dict = Depends(require_user)):
        return {
            "today": await _db(get_today_measurements, config.DB_PATH, config.CHILD_ID),
            "last": await _db(get_last_measurement, config.DB_PATH, config.CHILD_ID),
            "target_pef": await _db(_effective_target, config),
        }

    @app.get("/api/history")
    async def history(
        page: int = Query(1, ge=1),
        per_page: int = Query(10, ge=1, le=50),
        auth: dict = Depends(require_user),
    ):
        items, total, total_pages = await _db(
            get_measurements_paginated, config.DB_PATH, config.CHILD_ID, page, per_page
        )
        return {"items": items, "page": page, "total": total, "total_pages": total_pages}

    @app.get("/api/chart")
    async def chart(
        year: int | None = Query(None, ge=2000, le=2100),
        month: int | None = Query(None, ge=1, le=12),
        auth: dict = Depends(require_user),
    ):
        offset = getattr(config, "TZ_OFFSET", 0)
        now = datetime.now(timezone(timedelta(hours=offset)))
        if year is None or month is None:
            year, month = now.year, now.month
        rows = await _db(get_measurements_for_month, config.DB_PATH, config.CHILD_ID, year, month)
        points = [
            {
                "date": str(r["measured_at"])[:10],
                "tod": r["time_of_day"],
                "pef": r["pef_value"],
                "source": r.get("source") or "manual",
            }
            for r in rows
        ]
        available = await _db(get_available_months, config.DB_PATH, config.CHILD_ID)
        requested = (year, month)
        return {
            "points": points,
            "target_pef": await _db(_effective_target, config),
            "zones": {
                "green": getattr(config, "ZONE_GREEN", 80),
                "yellow": getattr(config, "ZONE_YELLOW", 60),
            },
            "month": f"{year:04d}-{month:02d}",
            "title": f"{MONTH_NAMES[month - 1]} {year}",
            "can_prev": any(m < requested for m in available),
            "can_next": any(m > requested for m in available),
            "available_months": [f"{y:04d}-{m:02d}" for y, m in available],
        }

    @app.get("/api/stats")
    async def stats(auth: dict = Depends(require_user)):
        data = await _db(get_stats, config.DB_PATH, config.CHILD_ID)
        data["target_pef"] = await _db(_effective_target, config)
        return data

    @app.post("/api/measurements")
    async def add(background: BackgroundTasks, body: MeasurementIn, force: bool = False,
                  auth: dict = Depends(require_user)):
        who = auth["user"]["id"]
        tod = _auto_time_of_day(config)
        target = await _db(_effective_target, config)
        mid, status = await _db(
            add_or_replace_measurement,
            config.DB_PATH, body.pef, tod, config.CHILD_ID, who, force,
        )
        if status == "exists":
            row = await _db(get_last_of_tod, config.DB_PATH, config.CHILD_ID, tod)
            raise HTTPException(409, detail=json.dumps({
                "message": f"{'Утренний' if tod == 'morning' else 'Вечерний'} замер уже есть сегодня",
                "tod": tod,
                "existing_id": row["id"] if row else None,
            }, ensure_ascii=False))
        prev = await _db(get_previous_of_tod, config.DB_PATH, config.CHILD_ID, tod, mid)
        diff = None
        if prev:
            diff = body.pef - prev["pef_value"]
        pct = _pct_of(body.pef, target)
        _schedule_add_notifications(background, services.get("bot"), config,
                                    who, body.pef, tod, target, pct)
        return {"id": mid, "pef": body.pef, "tod": tod,
                "zone": _pef_zone(body.pef, target, config), "pct": pct, "diff": diff}

    @app.patch("/api/measurements/{mid}")
    async def edit(mid: int, body: MeasurementIn, auth: dict = Depends(require_parent)):
        if not await _db(edit_measurement, config.DB_PATH, mid, body.pef, config.CHILD_ID):
            raise HTTPException(404, "Запись не найдена")
        return {"id": mid, "pef": body.pef}

    @app.delete("/api/measurements/{mid}")
    async def remove(mid: int, auth: dict = Depends(require_parent)):
        if not await _db(delete_measurement, config.DB_PATH, mid, config.CHILD_ID):
            raise HTTPException(404, "Запись не найдена")
        return {"deleted": True, "id": mid}

    @app.post("/api/measurements/{mid}/note")
    async def note(mid: int, body: NoteIn, auth: dict = Depends(require_user)):
        raw = body.note or ""
        note_text = raw.strip()[:200]
        if not await _db(set_note, config.DB_PATH, mid, note_text, config.CHILD_ID):
            raise HTTPException(404, "Запись не найдена")
        return {"id": mid, "note": note_text, "truncated": len(raw.strip()) > 200}

    @app.get("/api/settings")
    async def settings(auth: dict = Depends(require_parent)):
        return {
            "target_pef": await _db(_effective_target, config),
            "child_name": getattr(config, "CHILD_NAME", "Ребёнок"),
            "total": len(await _db(get_all_measurements, config.DB_PATH, config.CHILD_ID, include_auto=True)),
            "reminder_hours": await _db(get_reminder_hours, config.DB_PATH),
        }

    @app.put("/api/settings/target")
    async def put_target(body: TargetIn, auth: dict = Depends(require_parent)):
        await _db(set_setting, config.DB_PATH, "target_pef", str(body.target_pef))
        return {"target_pef": body.target_pef}

    @app.put("/api/settings/reminders")
    async def put_reminders(body: RemindersIn, auth: dict = Depends(require_parent)):
        hours = {key: getattr(body, key) for key in REMINDER_KEYS}
        error = validate_reminder_hours(hours)
        if error:
            raise HTTPException(422, error)
        for key in REMINDER_KEYS:
            await _db(set_setting, config.DB_PATH, f"reminder_{key}", str(getattr(body, key)))
        return {"reminder_hours": await _db(get_reminder_hours, config.DB_PATH)}

    @app.get("/api/export/periods")
    async def export_periods(auth: dict = Depends(require_parent)):
        months = [f"{y:04d}-{m:02d}" for y, m in await _db(get_available_months, config.DB_PATH, config.CHILD_ID)]
        return {"months": months, "latest": months[-1] if months else None}

    @app.get("/api/export/csv")
    async def export_csv(period: str = "all", auth: dict = Depends(require_parent)):
        target = await _db(_effective_target, config)
        child = getattr(config, "CHILD_NAME", "Ребёнок")
        stamp = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).strftime("%Y%m%d_%H%M")
        if period == "all":
            rows = await _db(get_measurements_between, config.DB_PATH, config.CHILD_ID, "2000-01-01", _today(config))
            filename = f"peakflow_{child}_{stamp}.csv"
        else:
            parsed = parse_month(period)
            if not parsed:
                raise HTTPException(422, "Неверный период")
            y, m = parsed
            start, end = _month_bounds(y, m)
            rows = await _db(get_measurements_between, config.DB_PATH, config.CHILD_ID, start, end)
            filename = f"peakflow_{child}_{y:04d}-{m:02d}.csv"
        if not rows:
            raise HTTPException(404, "Нет записей за период")
        stats = await _db(get_stats, config.DB_PATH, config.CHILD_ID)
        content = _build_csv(rows, target, child, stats=stats,
                             display_name=lambda uid: _who(config, uid),
                             zone_green=getattr(config, "ZONE_GREEN", 80),
                             zone_yellow=getattr(config, "ZONE_YELLOW", 60))
        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    @app.get("/api/backup")
    async def backup(background: BackgroundTasks, auth: dict = Depends(require_parent)):
        stamp = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).strftime("%Y%m%d_%H%M")
        fd, dest = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            await _db(backup_db, config.DB_PATH, dest)
        except Exception as e:
            logger.error("Ошибка бэкапа: %s", e)
            if os.path.exists(dest):
                os.remove(dest)
            raise HTTPException(500, "Не удалось создать бэкап")
        background.add_task(os.remove, dest)
        return FileResponse(dest, media_type="application/octet-stream",
                            filename=f"peakflow_backup_{stamp}.db")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
