from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.search.es_client import ping_elasticsearch

router = APIRouter()
settings = get_settings()


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"

    es_status, es_detail = await ping_elasticsearch()
    es_index_ready = None
    if settings.search_uses_elasticsearch and es_status == "ok":
        from app.search.index_mapping import ensure_posts_index

        es_index_ready = ensure_posts_index()

    overall_ok = db_status == "ok"
    if settings.search_backend == "elasticsearch" and es_status != "ok":
        overall_ok = False
    elif settings.search_backend == "dual" and db_status != "ok":
        overall_ok = False

    return {
        "status": "ok" if overall_ok else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "database": db_status,
        "search_backend": settings.search_backend,
        "elasticsearch": {
            "status": es_status,
            "detail": es_detail,
            "index": settings.elasticsearch_index_posts if es_status != "disabled" else None,
            "index_ready": es_index_ready,
        },
    }
