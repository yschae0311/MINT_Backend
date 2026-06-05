from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db

router = APIRouter()
settings = get_settings()


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "database": db_status,
    }
