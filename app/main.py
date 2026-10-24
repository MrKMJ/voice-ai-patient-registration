"""Application factory: wires config, database, routers, error handling and the dashboard."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import Base, get_db, make_engine, make_session_factory
from app.errors import ApiError, register_error_handlers
from app.routers import patients, voice
from app.schemas import Envelope
from app.seed import seed_demo_data

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    engine = make_engine(settings.sqlalchemy_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)  # idempotent; see README "Trade-offs" re: migrations
        if settings.seed_demo_data:
            seed_demo_data(session_factory)
        if not settings.vapi_webhook_secret:
            logger.warning("VAPI_WEBHOOK_SECRET is not set - the voice webhook is %s",
                           "DISABLED" if settings.is_production else "unauthenticated (dev only)")
        yield
        engine.dispose()

    app = FastAPI(
        title="Patient Registration - Voice AI Agent",
        version="1.0.0",
        description="REST API behind the patient-registration phone agent.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    register_error_handlers(app)
    app.include_router(patients.router)
    app.include_router(voice.router)

    @app.get("/health", tags=["meta"])
    def health(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            logger.exception("health check failed")
            raise ApiError(503, "unhealthy", "Database is unreachable.") from None
        return Envelope(data={"status": "ok"})

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "dashboard.html")

    return app


app = create_app()
