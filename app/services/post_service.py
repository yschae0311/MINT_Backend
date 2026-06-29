import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
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
from app.search.post_content import (
    get_post_content,
    mget_post_contents,
    pg_ai_summary_placeholder,
    save_post_content,
    sync_post_metadata,
)
from app.search.post_indexer import delete_post_index
from app.search.post_search_query import PostSearchFilters, load_posts_ordered, search_posts


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
        if get_settings().search_uses_elasticsearch:
            es_page = self._list_posts_es(
                organization_id,
                board_type=board_type,
                status=status,
                importance=importance,
                category=category,
                keyword=keyword,
                source_id=source_id,
                page=page,
                size=size,
            )
            if es_page is not None:
                return es_page

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
            from app.models.ai_output import AIOutput
            from sqlalchemy import or_

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
            from app.models.ai_output import AIOutput
            from sqlalchemy import or_

            count_q = count_q.outerjoin(AIOutput).where(
                or_(Post.title.ilike(like), Post.raw_content.ilike(like), AIOutput.summary.ilike(like))
            )
        total = self.db.scalar(count_q) or 0
        posts = self.db.scalars(
            q.order_by(Post.collected_at.desc()).offset((page - 1) * size).limit(size)
        ).unique().all()

        contents = mget_post_contents(self.db, [post.id for post in posts])
        items = [self._to_read(p, contents.get(p.id)) for p in posts]
        pages = max(1, (total + size - 1) // size)
        return PaginatedResponse(items=items, total=total, page=page, size=size, pages=pages)

    def get_post(self, post_id: UUID, organization_id: UUID) -> PostDetail:
        post = self._get_or_404(post_id, organization_id)
        content = get_post_content(self.db, post.id)
        detail = PostDetail.model_validate(post)
        detail.source_name = post.source.name if post.source else None
        outputs = sorted(post.ai_outputs, key=lambda o: o.created_at, reverse=True)
        detail.ai_outputs = [self._enrich_ai_output(o, content) for o in outputs]
        detail.latest_ai = detail.ai_outputs[0] if detail.ai_outputs else None
        self._apply_content(detail, content)
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
            original_url=None,
            raw_content="",
            content_hash=content_hash,
            category=data.category,
            status=status,
            importance=data.importance,
            created_by=CreatedBy.admin,
        )
        self.db.add(post)
        self.db.flush()
        save_post_content(
            self.db,
            post,
            original_url=data.original_url,
            body=data.raw_content,
            merge_existing=False,
        )
        self.db.commit()
        self.db.refresh(post)
        return self._to_read(post)

    def update_post(self, post_id: UUID, organization_id: UUID, data: PostUpdate) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(post, field, value)
        self.db.commit()
        self.db.refresh(post)
        sync_post_metadata(self.db, post)
        return self._to_read(post)

    def approve(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.status = PostStatus.published
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        sync_post_metadata(self.db, post)
        return self._to_read(post)

    def hide(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.status = PostStatus.hidden
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        sync_post_metadata(self.db, post)
        return self._to_read(post)

    def delete_post(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.status = PostStatus.deleted
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        sync_post_metadata(self.db, post)
        delete_post_index(self.db, post.id)
        return self._to_read(post)

    def promote(self, post_id: UUID, organization_id: UUID, user: User) -> PostRead:
        post = self._get_or_404(post_id, organization_id)
        post.board_type = BoardType.trusted
        post.status = PostStatus.published
        post.reviewed_by = user.id
        post.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        sync_post_metadata(self.db, post)
        return self._to_read(post)

    def purge_stale_pending_discovery(
        self,
        organization_id: UUID,
        *,
        retention_days: int | None = None,
    ) -> int:
        if retention_days is None:
            from app.services.org_settings_service import OrgSettingsService

            days = OrgSettingsService(self.db).discovery_pending_retention_days(organization_id)
        else:
            days = retention_days
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

    def _list_posts_es(
        self,
        organization_id: UUID,
        *,
        board_type: BoardType | None,
        status: PostStatus | None,
        importance: str | None,
        category: str | None,
        keyword: str | None,
        source_id: UUID | None,
        page: int,
        size: int,
    ) -> PaginatedResponse[PostRead] | None:
        filters = PostSearchFilters(
            organization_id=organization_id,
            query=(keyword or "").strip() or None,
            board_type=board_type.value if board_type else None,
            status=status.value if status else None,
            exclude_statuses=[] if status else ["deleted"],
            category=category,
            importance=importance,
            source_id=source_id,
        )
        result = search_posts(
            filters,
            page=page,
            size=size,
            highlight=bool((keyword or "").strip()),
        )
        if result is None:
            return None

        posts = load_posts_ordered(self.db, [hit.post_id for hit in result.hits])
        hit_by_id = {hit.post_id: hit for hit in result.hits}
        contents = mget_post_contents(self.db, [post.id for post in posts])
        items: list[PostRead] = []
        for post in posts:
            read = self._to_read(post, contents.get(post.id))
            hit = hit_by_id.get(post.id)
            if hit:
                read.title_highlight = hit.highlight_title
                read.summary_highlight = hit.highlight_summary
            items.append(read)
        pages = max(1, (result.total + size - 1) // size)
        return PaginatedResponse(items=items, total=result.total, page=page, size=size, pages=pages)

    def _get_or_404(self, post_id: UUID, organization_id: UUID) -> Post:
        post = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(Post.id == post_id, Post.organization_id == organization_id)
        ).unique().first()
        if not post:
            raise NotFoundError("Post not found")
        return post

    def _to_read(self, post: Post, content=None) -> PostRead:
        read = PostRead.model_validate(post)
        read.source_name = post.source.name if post.source else None
        if content is None:
            content = get_post_content(self.db, post.id)
        if post.ai_outputs:
            latest = max(post.ai_outputs, key=lambda o: o.created_at)
            read.latest_ai = self._enrich_ai_output(latest, content)
        self._apply_content(read, content)
        return read

    @staticmethod
    def _enrich_ai_output(output: AIOutput, content) -> AIOutputRead:
        read = AIOutputRead.model_validate(output)
        summary = (read.summary or "").strip()
        if content.summary and (not summary or summary == pg_ai_summary_placeholder().strip()):
            read.summary = content.summary
        if content.impact:
            read.impact = content.impact
        if content.action_items is not None:
            read.action_items = content.action_items
        return read

    @staticmethod
    def _apply_content(read: PostRead, content) -> None:
        read.original_url = content.original_url
        read.raw_content = content.body or ""
