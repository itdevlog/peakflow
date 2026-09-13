"""REST API Mini App. SP1: health-check; данные — в SP2."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse


def create_app(services: dict) -> FastAPI:
    app = FastAPI(title="Peakflow Bot Mini App API", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        state = services.get("state")
        if state is not None and not state.get("bot_ok", True):
            return JSONResponse(status_code=503, content={"status": "bot down"})
        return {"status": "ok"}

    return app
