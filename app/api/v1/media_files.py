"""Serve uploaded media through the API prefix (works behind /api reverse proxies).

Note: <img src> cannot send Authorization headers, so this route is public.
Paths include opaque UUIDs (org/report ids), which is acceptable for editorial assets.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.exceptions import NotFoundError

router = APIRouter()


@router.get("/{file_path:path}")
def get_media_file(file_path: str):
    """Public media fetch under /api/v1/files/... (for <img> tags)."""
    settings = get_settings()
    root = Path(settings.media_root).resolve()
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise NotFoundError("Media file not found")
    return FileResponse(target)
