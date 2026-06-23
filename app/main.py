"""MINT FastAPI application entrypoint.

Startup: logging, DB init (create_all), default seed data, media dirs.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.core.logging import setup_logging
from app.services.seed import seed_defaults

settings = get_settings()
media_root = Path(settings.media_root)
media_root.mkdir(parents=True, exist_ok=True)
(media_root / "reports").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(settings.debug)
    init_db()
    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
app.mount(settings.media_url_prefix, StaticFiles(directory=str(media_root)), name="media")
