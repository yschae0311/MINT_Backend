import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import BoardType
from app.models.source import Source
from app.schemas.search import GlobalSearchResponse, SearchPostHit, SearchSourceHit
from app.search.post_search_query import PostSearchFilters, search_posts


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

        posts: list[SearchPostHit] = []
        if get_settings().search_uses_elasticsearch:
            result = search_posts(
                PostSearchFilters(
                    organization_id=organization_id,
                    query=q,
                    exclude_statuses=["deleted"],
                ),
                page=1,
                size=post_limit,
                highlight=True,
            )
            if result:
                for hit in result.hits:
                    try:
                        board = BoardType(hit.board_type) if hit.board_type else BoardType.trusted
                    except ValueError:
                        board = BoardType.trusted
                    posts.append(
                        SearchPostHit(
                            id=hit.post_id,
                            title=hit.title,
                            board_type=board,
                            source_name=hit.source_name,
                            summary=hit.summary,
                            original_url=hit.original_url,
                            title_highlight=hit.highlight_title,
                            summary_highlight=hit.highlight_summary,
                        )
                    )

        if not posts and get_settings().search_uses_postgres:
            from app.models.ai_output import AIOutput
            from app.models.enums import PostStatus
            from app.models.post import Post
            from sqlalchemy.orm import joinedload

            from app.search.post_content import get_post_content
            from app.search.search_resolve import resolve_search_post_ids

            post_ids = resolve_search_post_ids(self.db, organization_id, q, limit=post_limit)
            if post_ids:
                loaded = list(
                    self.db.scalars(
                        select(Post)
                        .options(joinedload(Post.source), joinedload(Post.ai_outputs))
                        .where(
                            Post.organization_id == organization_id,
                            Post.status != PostStatus.deleted,
                            Post.id.in_(post_ids),
                        )
                    ).unique().all()
                )
                order = {pid: index for index, pid in enumerate(post_ids)}
                loaded.sort(key=lambda post: order.get(post.id, 9999))
                for post in loaded:
                    content = get_post_content(self.db, post.id)
                    summary = content.summary
                    posts.append(
                        SearchPostHit(
                            id=post.id,
                            title=post.title,
                            board_type=post.board_type,
                            source_name=post.source.name if post.source else None,
                            summary=summary,
                            original_url=content.original_url,
                        )
                    )

        source_filter = self._token_filter(
            tokens,
            (
                Source.name,
                Source.url,
                Source.category,
            ),
        )

        sources = self.db.scalars(
            select(Source)
            .where(Source.organization_id == organization_id)
            .where(source_filter)
            .order_by(Source.name)
            .limit(source_limit)
        ).all()

        return GlobalSearchResponse(
            query=q,
            posts=posts,
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
