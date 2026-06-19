import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.ai_output import AIOutput
from app.models.enums import BoardType, CreatedBy, PostStatus
from app.models.post import Post
from app.models.source import Source
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.post import AIOutputRead, PostCreate, PostDetail, PostRead, PostUpdate


class PostService:
    def __init__(self, db: Session):
        self.db = db

    def list_posts(
        self,
        organization_id: UUID,
        board_type: BoardType | None = None,
        status: PostStatus | None = None,
        importance: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        source_id: UUID | None = None,
        page: int = 1,
        size: int = 20,
    ) -> PaginatedResponse[PostRead]:
        q = (
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(Post.organization_id == organization_id)
            .where(Post.status != PostStatus.deleted)
        )
        if board_type:
            q = q.where(Post.board_type == board_type)
        if status:
            q = q.where(Post.status == status)
        if importance:
            q = q.where(Post.importance == importance)
        if category:
            q = q.where(Post.category == category)
        if source_id:
            q = q.where(Post.source_id == source_id)
        if keyword:
            like = f"%{keyword}%"
            q = q.outerjoin(AIOutput).where(
                or_(Post.title.ilike(like), Post.raw_content.ilike(like), AIOutput.summary.ilike(like))
            )

        count_q = select(func.count(func.distinct(Post.id))).select_from(Post)
        count_q = count_q.where(Post.organization_id == organization_id, Post.status != PostStatus.deleted)
        if board_type:
            count_q = count_q.where(Post.board_type == board_type)
        if status:
            count_q = count_q.where(Post.status == status)
        if importance:
            count_q = count_q.where(Post.importance == importance)
        if category:
            count_q = count_q.where(Post.category == category)
        if source_id:
            count_q = count_q.where(Post.source_id == source_id)
        if keyword:
            like = f"%{keyword}%"
            count_q = count_q.outerjoin(AIOutput).where(
                or_(Post.title.ilike(like), Post.raw_content.ilike(like), AIOutput.summary.ilike(like))
            )
        total = self.db.scalar(count_q) or 0
        posts = self.db.scalars(
            q.order_by(Post.collected_at.desc()).offset((page - 1) * size).limit(size)
        ).unique().all()

        items = [self._to_read(p) for p in posts]
        pages = max(1, (total + size - 1) // size)
        return PaginatedResponse(items=items, total=total, page=page, size=size, pages=pages)

    def get_post(self, post_id: UUID, organization_id: UUID) -> PostDetail:
        post = self._get_or_404(post_id, organization_id)
        detail = PostDetail.model_validate(post)
        detail.source_name = post.source.name if post.source else None
        outputs = sorted(post.ai_outputs, key=lambda o: o.created_at, reverse=True)
        detail.ai_outputs = [AIOutputRead.model_validate(o) for o in outputs]
        detail.latest_ai = detail.ai_outputs[0] if detail.ai_outputs else None
        return detail

    def create_post(self, organization_id: UUID, data: PostCreate) -> PostRead:
        content_hash = hashlib.sha256(f"{data.title}|{data.raw_content}".encode()).hexdigest()
        status = data.status or (
            PostStatus.published if data.board_type == BoardType.trusted else PostStatus.pending
        )
        post = Post(
            organization_id=organization_id,
            source_id=data.source_id,
            board_type=data.board_type,
            title=data.title,
            original_url=data.original_url,
            raw_content=data.raw_content,
            content_hash=content_hash,
            category=data.category,
            status=status,
            importance=data.importance,
            created_by=CreatedBy.admin,
        )
        self.db.add(post)
        self.db.commit()
        self.db.refresh(post)
        return self._to_read(post)

    def update_post(self, post_id: UUID, organization_id: UUID, data: PostUpdate) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(post, field, value)
        self.db.commit()
        self.db.refresh(post)
        return self._to_read(post)

    def approve(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.status = PostStatus.published
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        return self._to_read(post)

    def hide(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.status = PostStatus.hidden
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        return self._to_read(post)

    def delete_post(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.status = PostStatus.deleted
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        return self._to_read(post)

    def promote(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.board_type = BoardType.trusted
        post.status = PostStatus.published
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        return self._to_read(post)

    def purge_stale_pending_discovery(
        self,
        organization_id: UUID,
        *,
        retention_days: int | None = None,
    ) -> int:
        days = (
            retention_days
            if retention_days is not None
            else get_settings().discovery_pending_retention_days
        )
        if days <= 0:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        posts = list(
            self.db.scalars(
                select(Post).where(
                    Post.organization_id == organization_id,
                    Post.board_type == BoardType.discovery,
                    Post.status == PostStatus.pending,
                    Post.collected_at < cutoff,
                )
            ).all()
        )
        if not posts:
            return 0

        now = datetime.now(timezone.utc)
        for post in posts:
            post.status = PostStatus.deleted
            post.reviewed_at = now
        self.db.commit()
        return len(posts)

    def pending_count(self, organization_id: UUID) -> int:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(Post)
                .where(
                    Post.organization_id == organization_id,
                    Post.board_type == BoardType.discovery,
                    Post.status == PostStatus.pending,
                )
            )
            or 0
        )

    def _get_or_404(self, post_id: UUID, organization_id: UUID) -> Post:
        post = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(Post.id == post_id, Post.organization_id == organization_id)
        ).unique().first()
        if not post:
            raise NotFoundError("Post not found")
        return post

    def _to_read(self, post: Post) -> PostRead:
        read = PostRead.model_validate(post)
        read.source_name = post.source.name if post.source else None
        if post.ai_outputs:
            latest = max(post.ai_outputs, key=lambda o: o.created_at)
            read.latest_ai = AIOutputRead.model_validate(latest)
        return read
