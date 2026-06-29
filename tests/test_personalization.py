import hashlib
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
from app.models.enums import (
    AccountApprovalStatus,
    BoardType,
    CreatedBy,
    PostStatus,
    ReviewQueueReason,
    ReviewQueueStatus,
    TrustLevel,
    UserRole,
)
from app.models.organization import Organization
from app.models.personalization import Keyword, ReviewQueueItem
from app.models.post import Post
from app.models.user import User
from app.services.llm_client import MockLLMClient
from app.services.personalization_service import (
    ClassificationService,
    PersonalReportService,
    PersonalizedNewsService,
    ReviewQueueService,
    TaxonomyService,
)


class PersonalizationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_search_backend = os.environ.get("SEARCH_BACKEND")
        os.environ["SEARCH_BACKEND"] = "postgres"
        get_settings.cache_clear()

        self.engine = create_engine(
            "sqlite:///:memory:",
            execution_options={"schema_translate_map": {"mint": None}},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        org = Organization(name="Test", industry="EV")
        self.db.add(org)
        self.db.flush()
        self.user = User(
            organization_id=org.id,
            email="user@example.com",
            password_hash="x",
            name="테스터",
            role=UserRole.member,
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.taxonomy = TaxonomyService(self.db)
        self.taxonomy.ensure_defaults(org.id)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        if self._prev_search_backend is None:
            os.environ.pop("SEARCH_BACKEND", None)
        else:
            os.environ["SEARCH_BACKEND"] = self._prev_search_backend
        get_settings.cache_clear()

    def test_requires_at_least_three_keywords(self) -> None:
        keywords = self.taxonomy.list_keywords(self.user)
        with self.assertRaises(Exception):
            self.taxonomy.set_subscriptions(self.user, [keywords[0].id, keywords[1].id])

    def test_post_is_deduplicated_in_personal_feed(self) -> None:
        keywords = self.taxonomy.list_keywords(self.user)
        chosen = [item for item in keywords if item.name in {"OCPP", "CSMS", "충전 인프라"}]
        self.taxonomy.set_subscriptions(self.user, [item.id for item in chosen])
        post = self._post()
        ClassificationService(self.db).classify_post(
            post,
            {
                "category": "CSMS/OCPP",
                "confidence": 0.9,
                "keywords": [
                    {"name": "OCPP", "confidence": 0.95},
                    {"name": "CSMS", "confidence": 0.9},
                ],
            },
        )
        self.db.commit()
        feed = PersonalizedNewsService(self.db).list_news(
            self.user,
            personalized=True,
            size=20,
        )
        self.assertEqual(feed.total, 1)
        self.assertTrue(
            {"OCPP", "CSMS"}.issubset(
                {item.name for item in feed.items[0].matched_keywords}
            )
        )

    def test_personal_report_uses_at_most_ten_matching_posts(self) -> None:
        keywords = self.taxonomy.list_keywords(self.user)
        chosen = [item for item in keywords if item.name in {"OCPP", "CSMS", "충전 인프라"}]
        self.taxonomy.set_subscriptions(self.user, [item.id for item in chosen])
        for index in range(12):
            post = self._post(index)
            ClassificationService(self.db).classify_post(
                post,
                {
                    "category": "CSMS/OCPP",
                    "confidence": 0.9,
                    "keywords": [{"name": "OCPP", "confidence": 0.9}],
                },
            )
        self.db.commit()
        with patch(
            "app.services.personalization_service.get_llm_client",
            return_value=MockLLMClient(),
        ):
            report = PersonalReportService(self.db).generate_for_user(
                self.user,
                datetime.now().date(),
            )
        self.assertIsNotNone(report)
        self.assertEqual(report.item_count, 10)

    def test_classify_without_result_extracts_keywords_from_content(self) -> None:
        post = self._post()
        post.title = "New wireless charging pilot for fleet depots"
        post.raw_content = (
            "A wireless charging pilot launches at commercial fleet depots with OCPP "
            "integration and CSMS monitoring."
        )
        self.db.commit()
        with patch(
            "app.services.personalization_service.get_llm_client",
            return_value=MockLLMClient(),
        ):
            names, reasons = ClassificationService(self.db).classify_post(post)
        self.db.commit()
        self.assertTrue(names)
        self.assertIn(post.category, {"CSMS/OCPP", "충전 인프라", "정책/규제", "기술", "시장/기업"})
        self.assertNotIn("no_keywords", [reason.value for reason in reasons])
        self.assertNotIn("uncategorized", [reason.value for reason in reasons])

    def test_personal_keyword_is_not_exposed_to_another_user(self) -> None:
        other = User(
            organization_id=self.user.organization_id,
            email="other@example.com",
            password_hash="x",
            name="다른 사용자",
            role=UserRole.member,
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        self.db.add(other)
        self.db.commit()
        custom = self.taxonomy.create_custom_keyword(self.user, "비공개 관심사")
        post = self._post()
        post.title = "비공개 관심사 OCPP 기사"
        self.db.commit()
        ClassificationService(self.db).classify_post(post)
        self.db.commit()
        owner_news = PersonalizedNewsService(self.db).list_news(
            self.user,
            personalized=False,
            size=20,
        )
        other_news = PersonalizedNewsService(self.db).list_news(
            other,
            personalized=False,
            size=20,
        )
        self.assertIn(custom.name, {item.name for item in owner_news.items[0].matched_keywords})
        self.assertNotIn(custom.name, {item.name for item in other_news.items[0].matched_keywords})

    def test_review_queue_pending_count_excludes_hidden_posts(self) -> None:
        visible = self._post()
        hidden = self._post(1)
        hidden.status = PostStatus.hidden
        self.db.add(
            ReviewQueueItem(
                organization_id=self.user.organization_id,
                post_id=visible.id,
                reason=ReviewQueueReason.no_keywords,
                status=ReviewQueueStatus.pending,
            )
        )
        self.db.add(
            ReviewQueueItem(
                organization_id=self.user.organization_id,
                post_id=hidden.id,
                reason=ReviewQueueReason.no_keywords,
                status=ReviewQueueStatus.pending,
            )
        )
        self.db.commit()
        service = ReviewQueueService(self.db)
        self.assertEqual(service.pending_count(self.user.organization_id), 1)
        self.assertEqual(len(service.list(self.user.organization_id, ReviewQueueStatus.pending)), 1)

    def test_classified_post_appears_in_news_feed(self) -> None:
        post = self._post()
        ClassificationService(self.db).classify_post(
            post,
            {
                "category": "CSMS/OCPP",
                "confidence": 0.92,
                "keywords": [{"name": "OCPP", "confidence": 0.95}],
            },
        )
        self.db.commit()
        news = PersonalizedNewsService(self.db).list_news(self.user, personalized=False, size=20)
        self.assertEqual(news.total, 1)
        self.assertEqual(news.items[0].id, post.id)
        self.assertTrue(news.items[0].matched_keywords)

    def test_apply_manual_keywords_resolves_review_queue(self) -> None:
        post = self._post()
        post.title = "일반 산업 뉴스"
        post.raw_content = "키워드가 없는 기사 본문입니다."
        queue_item = ReviewQueueItem(
            organization_id=self.user.organization_id,
            post_id=post.id,
            reason=ReviewQueueReason.no_keywords,
            status=ReviewQueueStatus.pending,
        )
        self.db.add(queue_item)
        self.db.commit()

        keyword = self.db.scalar(
            select(Keyword).where(
                Keyword.organization_id == self.user.organization_id,
                Keyword.name == "OCPP",
            )
        )
        self.assertIsNotNone(keyword)

        linked, resolved_ids = ReviewQueueService(self.db).apply_keywords(
            queue_item.id,
            self.user.organization_id,
            self.user.id,
            keyword_ids=[keyword.id],
            new_keyword_names=[],
            category="CSMS/OCPP",
        )
        self.assertEqual(linked, ["OCPP"])
        self.assertIn(queue_item.id, resolved_ids)
        self.db.refresh(queue_item)
        self.assertEqual(queue_item.status, ReviewQueueStatus.resolved)
        pending = self.db.scalars(
            select(ReviewQueueItem).where(
                ReviewQueueItem.post_id == post.id,
                ReviewQueueItem.status == ReviewQueueStatus.pending,
            )
        ).all()
        self.assertEqual(len(pending), 0)

    def _post(self, index: int = 0) -> Post:
        post = Post(
            organization_id=self.user.organization_id,
            board_type=BoardType.trusted,
            title=f"OCPP CSMS 충전 뉴스 {index}",
            raw_content="OCPP 기반 충전 인프라와 CSMS 운영 소식",
            content_hash=hashlib.sha256(f"post-{index}".encode()).hexdigest(),
            status=PostStatus.published,
            trust_level=TrustLevel.high,
            reliability_score=90,
            created_by=CreatedBy.crawler,
        )
        self.db.add(post)
        self.db.flush()
        return post


if __name__ == "__main__":
    unittest.main()
