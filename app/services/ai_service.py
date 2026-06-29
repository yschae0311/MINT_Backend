from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import NotFoundError
from app.models.ai_output import AIOutput
from app.models.enums import Importance
from app.models.post import Post
from app.schemas.post import AIOutputRead
from app.search.post_content import get_post_content, pg_ai_summary_placeholder, save_post_content
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

        stored = get_post_content(self.db, post.id)
        content = (stored.body or "").strip()
        if not content and stored.original_url:
            from app.services.crawler_service import CrawlerService

            try:
                source_type = post.source.source_type if post.source else None
                from app.models.enums import SourceType

                st = source_type or SourceType.webpage
                content = CrawlerService(self.db)._fetch_article_text(stored.original_url, st)
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
            summary=pg_ai_summary_placeholder(),
            impact=None,
            action_items=None,
            importance=importance,
            confidence=result.get("confidence"),
            model=model_name,
            prompt_version="v2",
        )
        post.importance = importance
        self.db.add(output)
        self.db.flush()
        save_post_content(
            self.db,
            post,
            original_url=stored.original_url,
            summary=result.get("summary", ""),
            impact=result.get("impact"),
            body=content or stored.body,
            action_items=result.get("action_items"),
            merge_existing=True,
        )
        from app.services.personalization_service import ClassificationService

        ClassificationService(self.db).classify_post(post, result)
        self.db.commit()
        self.db.refresh(output)
        read = AIOutputRead.model_validate(output)
        read.summary = result.get("summary", "")
        read.impact = result.get("impact")
        read.action_items = result.get("action_items")
        return read
