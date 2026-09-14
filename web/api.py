"""REST API Mini App. SP2a: чтение данных дневника ПСВ."""
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from database import get_setting
from web.auth import get_user_from_init_data


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

    return app
