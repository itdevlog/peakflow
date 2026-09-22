"""REST API Mini App. SP2a: чтение данных дневника ПСВ."""
import asyncio
import hmac
import json
import logging
import os
import sqlite3
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from database import (
    DEFAULT_FAMILY_ID,
    add_or_replace_measurement,
    backup_family_db,
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
    get_measurement_dates,
    count_measurements,
    get_achievements,
    get_reminder_hours,
    get_stats,
    get_system_counts,
    get_today_measurements,
    get_effective_target as _db_effective_target,
    get_member,
    list_family_children,
    list_family_parents,
    resolve_active_child,
    set_active_child,
    set_note,
    set_setting,
    validate_reminder_hours,
)
import gamification
import metrics
import report_pdf
from report import build_csv_content as _build_csv, daily_average_series, parse_month
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


def _effective_target(config, family_id: int = DEFAULT_FAMILY_ID) -> int:
    return _db_effective_target(config.DB_PATH, getattr(config, "TARGET_PEF", 0), family_id)


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
                                tod: str, target: int, pct: int, recipients,
                                child_name=None,
                                child_id: int | None = None) -> None:
    """Queue Telegram notifications off the request path (BackgroundTasks).

    A slow or unavailable Telegram API must not delay the HTTP response, so
    the sends run after it is returned. ``recipients``/``child_name``/
    ``child_id`` carry the caller's tenant (family parents + active child)
    explicitly; there is no env fallback.
    """
    background.add_task(notify_added, bot, config, who, pef, tod, target,
                        recipients=recipients, child_name=child_name,
                        child_id=child_id)
    if pct < getattr(config, "ZONE_YELLOW", 60):
        background.add_task(notify_red_zone, bot, config, pef, tod, target, who=who,
                            recipients=recipients, child_name=child_name,
                            child_id=child_id)


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


class ActiveChildIn(BaseModel):
    child_id: int


REMINDER_KEYS = ("child_morning", "child_evening", "parent_morning", "parent_evening")


def _today(config) -> str:
    offset = getattr(config, "TZ_OFFSET", 0)
    return datetime.now(timezone(timedelta(hours=offset))).strftime("%Y-%m-%d")


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = f"{year:04d}-{month:02d}-01"
    first_next = (datetime(year, month, 28) + timedelta(days=4)).replace(day=1)
    last_day = first_next - timedelta(days=1)
    return start, last_day.strftime("%Y-%m-%d")


def _date_list(start_iso: str, end_iso: str) -> list:
    d, e = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _shape_children(children: list) -> list:
    """Trim member rows to the fields the Mini App needs."""
    return [
        {
            "telegram_id": c["telegram_id"],
            "family_id": c["family_id"],
            "role": c["role"],
            "name": c.get("name") or "Ребёнок",
        }
        for c in children
    ]


def _content_disposition(filename: str) -> str:
    """RFC 5987 attachment header safe for non-ASCII names."""
    safe = filename.replace("\r", " ").replace("\n", " ").replace('"', "'")
    ascii_fallback = safe.encode("ascii", "ignore").decode("ascii") or "export"
    encoded = quote(safe, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=utf-8''{encoded}"


def create_app(services: dict) -> FastAPI:
    app = FastAPI(title="Peakflow Bot Mini App API", docs_url=None, redoc_url=None)
    config = services.get("config")

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            metrics.inc("http_requests_total", method=request.method, status="500")
            logger.warning("http method=%s path=%s status=500 duration_ms=%.1f",
                           request.method, request.url.path, duration_ms)
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        metrics.inc("http_requests_total", method=request.method,
                    status=str(response.status_code))
        metrics.set_gauge("http_last_duration_ms", duration_ms)
        log = logger.warning if response.status_code >= 500 else logger.info
        log("http method=%s path=%s status=%s duration_ms=%.1f",
            request.method, request.url.path, response.status_code, duration_ms)
        return response

    async def _resolve_user(init_data: str | None) -> dict:
        token = getattr(config, "BOT_TOKEN", "") or ""
        if not token or not init_data:
            raise HTTPException(403, "Нет доступа")
        user = get_user_from_init_data(init_data, token)
        if not user:
            raise HTTPException(403, "Нет доступа")
        uid = user.get("id")
        try:
            member = await _db(get_member, config.DB_PATH, uid)
        except sqlite3.OperationalError as e:
            # Uninitialized/locked DB: behave as "not a member" (403 via fallback)
            # instead of leaking a 500. Narrow to OperationalError so real
            # corruption/IO faults are not silently masked.
            logger.warning("Не удалось прочитать участника из БД: %s", e)
            member = None
        if member:
            role = member["role"]
            family_id = member["family_id"]
            # Load the family's children once; resolve the active one from that
            # list so the same query is not issued twice.
            children = await _db(list_family_children, config.DB_PATH, family_id)
            active_child_id = await _db(resolve_active_child, config.DB_PATH,
                                        member, children)
        else:
            # Family #1 fallback (member row missing): only the env-configured
            # ids are allowed. The active child is the env child, mirroring
            # bot._ctx's member=None branch.
            if uid == getattr(config, "CHILD_ID", 0):
                role, family_id = "child", DEFAULT_FAMILY_ID
            elif uid in (getattr(config, "PARENT_IDS", []) or []):
                role, family_id = "parent", DEFAULT_FAMILY_ID
            else:
                raise HTTPException(403, "Нет доступа")
            active_child_id = getattr(config, "CHILD_ID", 0) or None
            children = (
                [{"telegram_id": active_child_id, "family_id": family_id,
                  "role": "child", "name": getattr(config, "CHILD_NAME", "Ребёнок")}]
                if active_child_id else []
            )
        return {
            "user": user,
            "role": role,
            "family_id": family_id,
            "member": member,
            "active_child_id": active_child_id,
            "children": _shape_children(children),
        }

    async def require_user(x_telegram_init_data: str | None = Header(None)) -> dict:
        return await _resolve_user(x_telegram_init_data)

    async def require_parent(auth: dict = Depends(require_user)) -> dict:
        if auth["role"] != "parent":
            raise HTTPException(403, "Только родители")
        return auth

    def _child_name(auth: dict) -> str:
        """Display name of the caller's active child."""
        if auth["member"] is None:
            return getattr(config, "CHILD_NAME", "Ребёнок")
        for child in auth["children"]:
            if child["telegram_id"] == auth["active_child_id"]:
                return child["name"]
        return "Ребёнок"

    async def _family_parent_ids(auth: dict) -> list:
        if auth["member"] is None:
            return list(getattr(config, "PARENT_IDS", []) or [])
        rows = await _db(list_family_parents, config.DB_PATH, auth["family_id"])
        return [r["telegram_id"] for r in rows]

    @app.get("/healthz")
    async def healthz():
        state = services.get("state")
        if state is not None and not state.get("bot_ok", True):
            return JSONResponse(status_code=503, content={"status": "bot down"})
        counts = {"families": None, "children": None, "measurements": None}
        try:
            counts = await _db(get_system_counts, config.DB_PATH)
        except Exception as e:
            logger.warning("healthz: не удалось посчитать БД: %s", e)
        return {
            "status": "ok",
            "uptime_seconds": round(metrics.uptime_seconds(), 1),
            "last_scheduler_tick": metrics.get_gauge("scheduler_last_tick_timestamp"),
            **counts,
        }

    @app.get("/metrics")
    async def prometheus_metrics(request: Request):
        if not getattr(config, "METRICS_ENABLED", False):
            raise HTTPException(404, "Not found")
        token = getattr(config, "METRICS_TOKEN", "") or ""
        if token:
            provided = request.headers.get("Authorization", "")
            if not hmac.compare_digest(provided, f"Bearer {token}"):
                raise HTTPException(401, "Unauthorized")
        try:
            counts = await _db(get_system_counts, config.DB_PATH)
            metrics.set_gauge("families_count", counts["families"])
            metrics.set_gauge("children_count", counts["children"])
            metrics.set_gauge("measurements_count", counts["measurements"])
        except Exception as e:
            logger.warning("metrics: не удалось посчитать БД: %s", e)
        metrics.set_gauge("process_uptime_seconds", metrics.uptime_seconds())
        return PlainTextResponse(metrics.render_prometheus(),
                                 media_type="text/plain; version=0.0.4")

    @app.get("/api/children")
    async def children(auth: dict = Depends(require_user)):
        return {
            "children": auth["children"],
            "active_child_id": auth["active_child_id"],
        }

    @app.get("/api/me")
    async def me(auth: dict = Depends(require_user)):
        return {
            "user": auth["user"],
            "role": auth["role"],
            "family_id": auth["family_id"],
            "child_name": _child_name(auth),
            "active_child_id": auth["active_child_id"],
            "children": auth["children"],
            "target_pef": await _db(_effective_target, config, auth["family_id"]),
            "zones": {
                "green": getattr(config, "ZONE_GREEN", 80),
                "yellow": getattr(config, "ZONE_YELLOW", 60),
            },
        }

    @app.get("/api/status")
    async def status(auth: dict = Depends(require_user)):
        return {
            "today": await _db(get_today_measurements, config.DB_PATH,
                               auth["active_child_id"], auth["family_id"]),
            "last": await _db(get_last_measurement, config.DB_PATH,
                              auth["active_child_id"], auth["family_id"]),
            "target_pef": await _db(_effective_target, config, auth["family_id"]),
        }

    @app.get("/api/history")
    async def history(
        page: int = Query(1, ge=1),
        per_page: int = Query(10, ge=1, le=50),
        auth: dict = Depends(require_user),
    ):
        items, total, total_pages = await _db(
            get_measurements_paginated, config.DB_PATH, auth["active_child_id"],
            page, per_page, auth["family_id"]
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
        rows = await _db(get_measurements_for_month, config.DB_PATH,
                         auth["active_child_id"], year, month, False, auth["family_id"])
        points = [
            {
                "date": str(r["measured_at"])[:10],
                "tod": r["time_of_day"],
                "pef": r["pef_value"],
                "source": r.get("source") or "manual",
                "note": r.get("note") or "",
            }
            for r in rows
        ]
        available = await _db(get_available_months, config.DB_PATH,
                              auth["active_child_id"], auth["family_id"])
        requested = (year, month)
        return {
            "points": points,
            "target_pef": await _db(_effective_target, config, auth["family_id"]),
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

    @app.get("/api/chart/compare")
    async def chart_compare(period: str = "week", auth: dict = Depends(require_user)):
        if period not in ("week", "month"):
            raise HTTPException(422, "Неверный период")
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        today = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).date()
        cur_start, cur_end = report_pdf.period_bounds(period, today)
        prev_ref = date.fromisoformat(cur_start) - timedelta(days=1)
        prev_start, prev_end = report_pdf.period_bounds(period, prev_ref)
        cur_rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                             cur_start, cur_end, auth["family_id"])
        prev_rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                              prev_start, prev_end, auth["family_id"])
        current = daily_average_series(cur_rows, _date_list(cur_start, cur_end))
        previous = daily_average_series(prev_rows, _date_list(prev_start, prev_end))
        length = max(len(current), len(previous))
        current += [None] * (length - len(current))
        previous += [None] * (length - len(previous))
        labels = (["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][:length]
                  if period == "week" else [str(i + 1) for i in range(length)])
        return {
            "period": period,
            "labels": labels,
            "current": current,
            "previous": previous,
            "target_pef": await _db(_effective_target, config, auth["family_id"]),
            "zones": {"green": getattr(config, "ZONE_GREEN", 80),
                      "yellow": getattr(config, "ZONE_YELLOW", 60)},
            "title": (f"{report_pdf.period_label(period, today)} vs "
                      f"{report_pdf.period_label(period, prev_ref)}"),
        }

    @app.get("/api/stats")
    async def stats(auth: dict = Depends(require_user)):
        data = await _db(get_stats, config.DB_PATH, auth["active_child_id"], auth["family_id"])
        data["target_pef"] = await _db(_effective_target, config, auth["family_id"])
        return data

    @app.get("/api/gamification")
    async def gamification_endpoint(auth: dict = Depends(require_user)):
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        dates = await _db(get_measurement_dates, config.DB_PATH, child_id,
                          auth["family_id"])
        total = await _db(count_measurements, config.DB_PATH, child_id,
                          auth["family_id"])
        longest = gamification.longest_streak(dates)
        today = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).date()
        current = gamification.current_streak(dates, today)
        earned = gamification.evaluate(longest, total)
        unlocked = await _db(get_achievements, config.DB_PATH, child_id)
        achievements = [
            {"code": a["code"], "emoji": a["emoji"], "title": a["title"],
             "unlocked": a["code"] in earned,
             "unlocked_at": unlocked.get(a["code"])}
            for a in gamification.ACHIEVEMENTS
        ]
        return {"streak_current": current, "streak_longest": longest,
                "total": total, "achievements": achievements}

    @app.post("/api/measurements")
    async def add(background: BackgroundTasks, body: MeasurementIn, force: bool = False,
                  auth: dict = Depends(require_user)):
        who = auth["user"]["id"]
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(409, "В семье нет ребёнка")
        tod = _auto_time_of_day(config)
        target = await _db(_effective_target, config, auth["family_id"])
        mid, status = await _db(
            add_or_replace_measurement,
            config.DB_PATH, body.pef, tod, child_id, who, force,
            family_id=auth["family_id"],
        )
        if status == "exists":
            row = await _db(get_last_of_tod, config.DB_PATH, child_id, tod, auth["family_id"])
            raise HTTPException(409, detail=json.dumps({
                "message": f"{'Утренний' if tod == 'morning' else 'Вечерний'} замер уже есть сегодня",
                "tod": tod,
                "existing_id": row["id"] if row else None,
            }, ensure_ascii=False))
        prev = await _db(get_previous_of_tod, config.DB_PATH, child_id, tod, mid, auth["family_id"])
        diff = None
        if prev:
            diff = body.pef - prev["pef_value"]
        pct = _pct_of(body.pef, target)
        _schedule_add_notifications(
            background, services.get("bot"), config, who, body.pef, tod, target, pct,
            recipients=await _family_parent_ids(auth),
            child_name=_child_name(auth),
            child_id=child_id,
        )
        return {"id": mid, "pef": body.pef, "tod": tod,
                "zone": _pef_zone(body.pef, target, config), "pct": pct, "diff": diff}

    @app.patch("/api/measurements/{mid}")
    async def edit(mid: int, body: MeasurementIn, auth: dict = Depends(require_parent)):
        if not await _db(edit_measurement, config.DB_PATH, mid, body.pef,
                         auth["active_child_id"], auth["family_id"]):
            raise HTTPException(404, "Запись не найдена")
        return {"id": mid, "pef": body.pef}

    @app.delete("/api/measurements/{mid}")
    async def remove(mid: int, auth: dict = Depends(require_parent)):
        if not await _db(delete_measurement, config.DB_PATH, mid,
                         auth["active_child_id"], auth["family_id"]):
            raise HTTPException(404, "Запись не найдена")
        return {"deleted": True, "id": mid}

    @app.post("/api/measurements/{mid}/note")
    async def note(mid: int, body: NoteIn, auth: dict = Depends(require_user)):
        raw = body.note or ""
        note_text = raw.strip()[:200]
        if not await _db(set_note, config.DB_PATH, mid, note_text,
                         auth["active_child_id"], auth["family_id"]):
            raise HTTPException(404, "Запись не найдена")
        return {"id": mid, "note": note_text, "truncated": len(raw.strip()) > 200}

    @app.get("/api/settings")
    async def settings(auth: dict = Depends(require_parent)):
        return {
            "target_pef": await _db(_effective_target, config, auth["family_id"]),
            "child_name": _child_name(auth),
            "total": len(await _db(get_all_measurements, config.DB_PATH,
                                   auth["active_child_id"], True, auth["family_id"])),
            "reminder_hours": await _db(get_reminder_hours, config.DB_PATH, auth["family_id"]),
        }

    @app.put("/api/settings/target")
    async def put_target(body: TargetIn, auth: dict = Depends(require_parent)):
        await _db(set_setting, config.DB_PATH, "target_pef",
                  str(body.target_pef), auth["family_id"])
        return {"target_pef": body.target_pef}

    @app.put("/api/settings/reminders")
    async def put_reminders(body: RemindersIn, auth: dict = Depends(require_parent)):
        hours = {key: getattr(body, key) for key in REMINDER_KEYS}
        error = validate_reminder_hours(hours)
        if error:
            raise HTTPException(422, error)
        for key in REMINDER_KEYS:
            await _db(set_setting, config.DB_PATH, f"reminder_{key}",
                      str(getattr(body, key)), auth["family_id"])
        return {"reminder_hours": await _db(get_reminder_hours, config.DB_PATH, auth["family_id"])}

    @app.put("/api/active-child")
    async def put_active_child(body: ActiveChildIn, auth: dict = Depends(require_parent)):
        ok = await _db(set_active_child, config.DB_PATH, auth["user"]["id"], body.child_id)
        if not ok:
            raise HTTPException(404, "Ребёнок не найден")
        return {"active_child_id": body.child_id}

    @app.get("/api/export/periods")
    async def export_periods(auth: dict = Depends(require_parent)):
        months = [f"{y:04d}-{m:02d}" for y, m in await _db(
            get_available_months, config.DB_PATH, auth["active_child_id"], auth["family_id"])]
        return {"months": months, "latest": months[-1] if months else None}

    @app.get("/api/export/csv")
    async def export_csv(period: str = "all", auth: dict = Depends(require_parent)):
        child_id = auth["active_child_id"]
        target = await _db(_effective_target, config, auth["family_id"])
        child = _child_name(auth)
        parent_ids = await _family_parent_ids(auth)
        stamp = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).strftime("%Y%m%d_%H%M")
        if period == "all":
            rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                             "2000-01-01", _today(config), auth["family_id"])
            filename = f"peakflow_{child}_{stamp}.csv"
        else:
            parsed = parse_month(period)
            if not parsed:
                raise HTTPException(422, "Неверный период")
            y, m = parsed
            start, end = _month_bounds(y, m)
            rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                             start, end, auth["family_id"])
            filename = f"peakflow_{child}_{y:04d}-{m:02d}.csv"
        if not rows:
            raise HTTPException(404, "Нет записей за период")
        stats = await _db(get_stats, config.DB_PATH, child_id, auth["family_id"])

        def _display(uid: int) -> str:
            if uid == child_id:
                return child
            if uid in parent_ids:
                return "Родитель"
            return "Кто-то"

        content = _build_csv(rows, target, child, stats=stats,
                             display_name=_display,
                             zone_green=getattr(config, "ZONE_GREEN", 80),
                             zone_yellow=getattr(config, "ZONE_YELLOW", 60))
        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    @app.get("/api/report/pdf")
    async def report_pdf_endpoint(period: str = "month",
                                  auth: dict = Depends(require_parent)):
        if period not in report_pdf.PERIODS:
            raise HTTPException(422, "Неверный период")
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        today = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).date()
        date_from, date_to = report_pdf.period_bounds(period, today)
        rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                         date_from, date_to, auth["family_id"])
        if not rows:
            raise HTTPException(404, "Нет записей за период")
        target = await _db(_effective_target, config, auth["family_id"])
        child = _child_name(auth)
        label = report_pdf.period_label(period, today)
        try:
            pdf = await asyncio.to_thread(
                report_pdf.build_pdf, rows, target=target, child_name=child,
                period_label=label,
                zone_green=getattr(config, "ZONE_GREEN", 80),
                zone_yellow=getattr(config, "ZONE_YELLOW", 60),
            )
        except Exception as e:
            logger.error("Ошибка генерации PDF-отчёта: %s", e)
            raise HTTPException(500, "Не удалось создать отчёт")
        stamp = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).strftime("%Y%m%d_%H%M")
        filename = f"peakflow_report_{child}_{period}_{stamp}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    @app.get("/api/backup")
    async def backup(background: BackgroundTasks, auth: dict = Depends(require_parent)):
        stamp = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).strftime("%Y%m%d_%H%M")
        fd, dest = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            await _db(backup_family_db, config.DB_PATH, dest, auth["family_id"])
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
