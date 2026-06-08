import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.ai_output import AIOutput
from app.models.enums import PostStatus
from app.models.post import Post
from app.schemas.chat import ChatAskResponse, ChatCitation
from app.services.llm_client import get_llm_client


class ChatService:
    def __init__(self, db: Session):
        self.db = db

    def ask(self, organization_id: UUID, message: str) -> ChatAskResponse:
        posts = self._find_relevant_posts(organization_id, message)
        context, citations = self._build_context(posts)
        client = get_llm_client()
        reply = client.answer_question(message, context)
        return ChatAskResponse(reply=reply, citations=citations)

    def _find_relevant_posts(self, organization_id: UUID, question: str, limit: int = 8) -> list[Post]:
        tokens = [t for t in re.split(r"[\s,?.!]+", question) if len(t) >= 2][:5]
        seen: set[UUID] = set()
        posts: list[Post] = []

        base = (
            select(Post)
            .options(joinedload(Post.ai_outputs))
            .outerjoin(AIOutput)
            .where(Post.organization_id == organization_id, Post.status != PostStatus.deleted)
        )

        for token in tokens:
            like = f"%{token}%"
            found = self.db.scalars(
                base.where(or_(Post.title.ilike(like), AIOutput.summary.ilike(like)))
                .order_by(Post.collected_at.desc())
                .limit(5)
            ).unique().all()
            for post in found:
                if post.id not in seen:
                    seen.add(post.id)
                    posts.append(post)

        if len(posts) < 5:
            recent = self.db.scalars(
                select(Post)
                .options(joinedload(Post.ai_outputs))
                .where(Post.organization_id == organization_id, Post.status != PostStatus.deleted)
                .order_by(Post.collected_at.desc())
                .limit(limit)
            ).unique().all()
            for post in recent:
                if post.id not in seen:
                    seen.add(post.id)
                    posts.append(post)

        return posts[:limit]

    def _build_context(self, posts: list[Post]) -> tuple[str, list[ChatCitation]]:
        if not posts:
            return "수집된 게시글이 없습니다.", []

        blocks: list[str] = []
        citations: list[ChatCitation] = []

        for post in posts:
            summary = None
            if post.ai_outputs:
                latest = max(post.ai_outputs, key=lambda o: o.created_at)
                summary = latest.summary
            blocks.append(
                f"- 제목: {post.title}\n"
                f"  URL: {post.original_url or '(없음)'}\n"
                f"  AI요약: {summary or '(없음)'}\n"
                f"  중요도: {post.importance.value}"
            )
            citations.append(
                ChatCitation(
                    post_id=post.id,
                    title=post.title,
                    url=post.original_url,
                    summary=summary,
                )
            )

        return "\n\n".join(blocks), citations
