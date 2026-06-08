import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.ai_output import AIOutput
from app.models.enums import PostStatus
from app.models.post import Post
from app.models.source import Source
from app.schemas.search import GlobalSearchResponse, SearchPostHit, SearchSourceHit


class SearchService:
    def __init__(self, db: Session):
        self.db = db

    def search(self, organization_id: UUID, query: str, limit: int = 8) -> GlobalSearchResponse:
        q = query.strip()
        if not q:
            return GlobalSearchResponse(query=query, posts=[], sources=[])

        tokens = self._tokens(q)
        post_limit = max(4, limit // 2 + 2)
        source_limit = max(4, limit // 2)

        post_filter = self._token_filter(
            tokens,
            (
                Post.title,
                Post.raw_content,
                AIOutput.summary,
            ),
        )
        source_filter = self._token_filter(
            tokens,
            (
                Source.name,
                Source.url,
                Source.category,
            ),
        )

        posts = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .outerjoin(AIOutput)
            .where(Post.organization_id == organization_id, Post.status != PostStatus.deleted)
            .where(post_filter)
            .order_by(Post.collected_at.desc())
            .limit(post_limit)
        ).unique().all()

        sources = self.db.scalars(
            select(Source)
            .where(Source.organization_id == organization_id)
            .where(source_filter)
            .order_by(Source.name)
            .limit(source_limit)
        ).all()

        return GlobalSearchResponse(
            query=q,
            posts=[self._post_hit(p) for p in posts],
            sources=[SearchSourceHit.model_validate(s) for s in sources],
        )

    def _tokens(self, query: str) -> list[str]:
        parts = [t for t in re.split(r"[\s,?.!·]+", query) if t]
        tokens: list[str] = []
        seen: set[str] = set()

        def add(token: str) -> None:
            token = token.strip()
            if token and token.lower() not in seen:
                seen.add(token.lower())
                tokens.append(token)

        add(query)
        for part in parts:
            add(part)
        return tokens

    def _token_filter(self, tokens: list[str], columns: tuple) -> or_:
        clauses = []
        for token in tokens:
            like = f"%{token}%"
            clauses.append(or_(*(col.ilike(like) for col in columns)))
        return or_(*clauses)

    def _post_hit(self, post: Post) -> SearchPostHit:
        summary = None
        if post.ai_outputs:
            latest = max(post.ai_outputs, key=lambda o: o.created_at)
            summary = latest.summary
        return SearchPostHit(
            id=post.id,
            title=post.title,
            board_type=post.board_type,
            source_name=post.source.name if post.source else None,
            summary=summary,
            original_url=post.original_url,
        )
