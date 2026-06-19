from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import BoardType, Importance, PostStatus
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.post import AIOutputRead, PostCreate, PostDetail, PostRead, PostUpdate
from app.services.ai_service import AIService
from app.services.original_preview_service import OriginalPreviewService
from app.services.post_service import PostService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[PostRead])
def list_posts(
    board_type: BoardType | None = None,
    status: PostStatus | None = None,
    importance: Importance | None = None,
    category: str | None = None,
    keyword: str | None = None,
    source_id: UUID | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PostService(db).list_posts(
        user.organization_id,
        board_type=board_type,
        status=status,
        importance=importance,
        category=category,
        keyword=keyword,
        source_id=source_id,
        page=page,
        size=size,
    )


@router.get("/{post_id}", response_model=PostDetail)
def get_post(
    post_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PostService(db).get_post(post_id, user.organization_id)


@router.get("/{post_id}/original-preview", response_class=HTMLResponse)
def original_preview(
    post_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    html = OriginalPreviewService(db).build_preview(post_id, user.organization_id)
    return HTMLResponse(content=html, headers={"Cache-Control": "private, max-age=300"})


@router.post("", response_model=PostRead)
def create_post(
    data: PostCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PostService(db).create_post(user.organization_id, data)


@router.patch("/{post_id}", response_model=PostRead)
def update_post(
    post_id: UUID,
    data: PostUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PostService(db).update_post(post_id, user.organization_id, data)


@router.post("/{post_id}/approve", response_model=PostRead)
def approve_post(post_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return PostService(db).approve(post_id, user.organization_id, user)


@router.post("/{post_id}/hide", response_model=PostRead)
def hide_post(post_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return PostService(db).hide(post_id, user.organization_id, user)


@router.post("/{post_id}/delete", response_model=PostRead)
def delete_post(post_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return PostService(db).delete_post(post_id, user.organization_id, user)


@router.post("/{post_id}/promote", response_model=PostRead)
def promote_post(post_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return PostService(db).promote(post_id, user.organization_id, user)


@router.post("/{post_id}/summarize", response_model=AIOutputRead)
def summarize_post(post_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return AIService(db).summarize_post(post_id, user.organization_id)
