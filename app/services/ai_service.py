from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import NotFoundError
from app.models.ai_output import AIOutput
from app.models.enums import Importance
from app.models.post import Post
from app.schemas.post import AIOutputRead
from app.services.llm_client import get_llm_client


class AIService:
    def __init__(self, db: Session):
        self.db = db

    def summarize_post(self, post_id: UUID, organization_id: UUID) -> AIOutputRead:
        post = self.db.scalar(
            select(Post)
            .options(joinedload(Post.source))
            .where(Post.id == post_id, Post.organization_id == organization_id)
        )
        if not post:
            raise NotFoundError("Post not found")

        content = (post.raw_content or "").strip()
        if not content and post.original_url:
            from app.services.crawler_service import CrawlerService

            try:
                source_type = post.source.source_type if post.source else None
                from app.models.enums import SourceType

                st = source_type or SourceType.webpage
                content = CrawlerService(self.db)._fetch_article_text(post.original_url, st)
            except Exception:
                content = post.title

        client = get_llm_client()
        result = client.summarize_post(post.title, content or post.title)
        imp_raw = result.get("importance", "medium")
        try:
            importance = Importance(imp_raw)
        except ValueError:
            importance = Importance.medium

        model_name = getattr(client, "summary_model", "mock")
        output = AIOutput(
            post_id=post.id,
            summary=result.get("summary", ""),
            impact=result.get("impact"),
            action_items=result.get("action_items"),
            importance=importance,
            confidence=result.get("confidence"),
            model=model_name,
            prompt_version="v1",
        )
        post.importance = importance
        self.db.add(output)
        self.db.commit()
        self.db.refresh(output)
        return AIOutputRead.model_validate(output)
