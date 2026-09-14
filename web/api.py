"""REST API Mini App. SP2a: чтение данных дневника ПСВ."""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from database import (
    get_available_months,
    get_last_measurement,
    get_last_two_weeks,
    get_measurements_for_month,
    get_measurements_paginated,
    get_setting,
    get_stats,
    get_today_measurements,
)
from web.auth import get_user_from_init_data

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _effective_target(config) -> int:
    try:
        val = int(get_setting(config.DB_PATH, "target_pef", str(config.TARGET_PEF)))
        return val if val > 0 else 300
    except (ValueError, Exception):
        return getattr(config, "TARGET_PEF", 0) or 300


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
        if uid == getattr(config, "CHILD_ID", 0):
            role = "child"
        elif uid in (getattr(config, "PARENT_IDS", []) or []):
            role = "parent"
        else:
            raise HTTPException(403, "Нет доступа")
        return {"user": user, "role": role}

    def require_user(x_telegram_init_data: str | None = Header(None)) -> dict:
        return _resolve_user(x_telegram_init_data)

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
            "target_pef": _effective_target(config),
        }

    @app.get("/api/status")
    async def status(auth: dict = Depends(require_user)):
        return {
            "today": get_today_measurements(config.DB_PATH, config.CHILD_ID),
            "last": get_last_measurement(config.DB_PATH, config.CHILD_ID),
            "target_pef": _effective_target(config),
        }

    @app.get("/api/summary")
    async def summary(auth: dict = Depends(require_user)):
        return {
            "today": get_today_measurements(config.DB_PATH, config.CHILD_ID),
            "target_pef": _effective_target(config),
        }

    @app.get("/api/history")
    async def history(
        page: int = Query(1, ge=1),
        per_page: int = Query(10, ge=1, le=50),
        auth: dict = Depends(require_user),
    ):
        items, total, total_pages = get_measurements_paginated(
            config.DB_PATH, config.CHILD_ID, page, per_page
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
        rows = get_measurements_for_month(config.DB_PATH, config.CHILD_ID, year, month)
        points = [
            {
                "date": str(r["measured_at"])[:10],
                "tod": r["time_of_day"],
                "pef": r["pef_value"],
                "source": r.get("source") or "manual",
            }
            for r in rows
        ]
        available = get_available_months(config.DB_PATH, config.CHILD_ID)
        requested = (year, month)
        return {
            "points": points,
            "target_pef": _effective_target(config),
            "zones": {
                "green": getattr(config, "ZONE_GREEN", 80),
                "yellow": getattr(config, "ZONE_YELLOW", 60),
                "red": getattr(config, "ZONE_RED", 50),
            },
            "month": f"{year:04d}-{month:02d}",
            "title": f"{MONTH_NAMES[month - 1]} {year}",
            "can_prev": any(m < requested for m in available),
            "can_next": any(m > requested for m in available),
            "available_months": [f"{y:04d}-{m:02d}" for y, m in available],
        }

    @app.get("/api/stats")
    async def stats(auth: dict = Depends(require_user)):
        data = get_stats(config.DB_PATH, config.CHILD_ID)
        data["target_pef"] = _effective_target(config)
        return data

    @app.get("/api/weekly")
    async def weekly(offset: int = Query(0, ge=0, le=0), auth: dict = Depends(require_user)):
        this_week, prev_week = get_last_two_weeks(config.DB_PATH, config.CHILD_ID)
        return {"this_week": this_week, "prev_week": prev_week, "offset": offset}

    return app
