"""Serve uploaded media through the API prefix (works behind /api reverse proxies)."""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()


@router.get("/{file_path:path}")
def get_media_file(file_path: str, user: User = Depends(get_current_user)):
    """Authenticated media fetch under /api/v1/files/..."""
    _ = user
    settings = get_settings()
    root = Path(settings.media_root).resolve()
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise NotFoundError("Media file not found")
    return FileResponse(target)
