from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.search import GlobalSearchResponse
from app.services.search_service import SearchService

router = APIRouter()


@router.get("", response_model=GlobalSearchResponse)
def global_search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=8, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SearchService(db).search(user.organization_id, q, limit=limit)
