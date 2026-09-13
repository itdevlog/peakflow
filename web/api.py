"""REST API Mini App. SP1: только health-check; данные — в SP2."""
from fastapi import FastAPI


def create_app(services: dict) -> FastAPI:
    app = FastAPI(title="Peakflow Bot Mini App API", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app
