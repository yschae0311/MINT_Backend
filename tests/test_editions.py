import hashlib
import os
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
import app.models  # noqa: F401
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.edition import Edition
from app.models.enums import (
    AccountApprovalStatus,
    BoardType,
    CreatedBy,
    Importance,
    KeywordMatchMethod,
    PostStatus,
    SourceType,
    TrustLevel,
    UserRole,
)
from app.models.organization import Organization
from app.models.personalization import Keyword, PostKeyword
from app.models.post import Post
from app.models.source import Source
from app.models.user import User
from app.schemas.edition import EditionCreate
from app.services.edition_service import AUTONOMOUS_SLUG, EV_SLUG, EditionService
from app.services.ev_relevance import passes_keyword_gate
from app.services.personalization_service import PersonalizedNewsService, TaxonomyService
from app.services.post_service import PostService
from app.services.topic_gate import load_edition_topic_terms, load_topic_terms


class EditionsServiceTest(unittest.TestCase):
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
        self.org_id = org.id
        self.user = User(
            organization_id=org.id,
            email="editor@example.com",
            password_hash="x",
            name="편집장",
            role=UserRole.admin,
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        TaxonomyService(self.db).ensure_defaults(org.id)
        self.db.commit()
        self.editions = EditionService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        if self._prev_search_backend is None:
            os.environ.pop("SEARCH_BACKEND", None)
        else:
            os.environ["SEARCH_BACKEND"] = self._prev_search_backend
        get_settings.cache_clear()

    def _keyword(self, name: str) -> Keyword:
        return self.db.scalar(
            select(Keyword).where(
                Keyword.organization_id == self.org_id,
                Keyword.name == name,
            )
        )

    def _post(self, *, title: str, index: int, collected_at: datetime | None = None) -> Post:
        post = Post(
            organization_id=self.org_id,
            board_type=BoardType.trusted,
            title=title,
            raw_content=title,
            content_hash=hashlib.sha256(f"{title}-{index}".encode()).hexdigest(),
            status=PostStatus.published,
            trust_level=TrustLevel.high,
            reliability_score=90,
            created_by=CreatedBy.crawler,
            collected_at=collected_at or datetime.now(timezone.utc),
        )
        self.db.add(post)
        self.db.flush()
        return post

    def test_seeds_ev_and_autonomous_editions(self) -> None:
        rows = self.editions.list_editions(self.org_id, active_only=True)
        slugs = {row.slug for row in rows}
        self.assertEqual({EV_SLUG, AUTONOMOUS_SLUG}, slugs)
        av = self._keyword("로보택시")
        ev = self._keyword("OCPP")
        self.assertIsNotNone(av)
        self.assertIsNotNone(ev)
        self.assertNotEqual(av.edition_id, ev.edition_id)
        self.assertTrue(av.is_featured)
        self.assertTrue(ev.is_featured)

    def test_create_edition_adds_another_vertical(self) -> None:
        row = self.editions.create(
            self.org_id,
            EditionCreate(name="수소", slug="hydrogen", topic_terms=["수소차", "수소 충전"]),
        )
        self.assertEqual(row.slug, "hydrogen")
        slugs = {item.slug for item in self.editions.list_editions(self.org_id, active_only=True)}
        self.assertIn("hydrogen", slugs)
        self.assertIn(EV_SLUG, slugs)

    def test_missing_sources_when_no_tagged_or_general_source(self) -> None:
        reads = self.editions.list_reads(self.org_id, active_only=True)
        self.assertTrue(all(item.missing_sources for item in reads))

        source = Source(
            organization_id=self.org_id,
            name="일반 정책",
            url="https://example.com/policy.xml",
            source_type=SourceType.rss,
            is_active=True,
        )
        self.db.add(source)
        self.db.commit()
        reads = self.editions.list_reads(self.org_id, active_only=True)
        self.assertTrue(all(not item.missing_sources for item in reads))

    def test_editorial_feed_includes_av_without_ev_regex(self) -> None:
        av_kw = self._keyword("로보택시")
        ev_kw = self._keyword("OCPP")
        av_post = self._post(title="로보택시 운행 허가 확대", index=1)
        ev_post = self._post(title="OCPP CSMS 충전 프로토콜 개정", index=2)
        self.db.add(
            PostKeyword(
                post_id=av_post.id,
                keyword_id=av_kw.id,
                confidence=0.95,
                matched_by=KeywordMatchMethod.ai,
            )
        )
        self.db.add(
            PostKeyword(
                post_id=ev_post.id,
                keyword_id=ev_kw.id,
                confidence=0.95,
                matched_by=KeywordMatchMethod.ai,
            )
        )
        self.db.commit()

        av_edition = self.db.scalar(
            select(Edition).where(
                Edition.organization_id == self.org_id,
                Edition.slug == AUTONOMOUS_SLUG,
            )
        )
        ev_edition = self.db.scalar(
            select(Edition).where(
                Edition.organization_id == self.org_id,
                Edition.slug == EV_SLUG,
            )
        )
        news = PersonalizedNewsService(self.db)
        av_feed = news.list_editorial(self.user, av_edition.id)
        ev_feed = news.list_editorial(self.user, ev_edition.id)
        self.assertEqual([item.id for item in av_feed.items], [av_post.id])
        self.assertEqual([item.id for item in ev_feed.items], [ev_post.id])

    def test_personal_feed_is_recency_within_window(self) -> None:
        keyword = self._keyword("OCPP")
        TaxonomyService(self.db).set_subscriptions(self.user, [keyword.id, self._keyword("CSMS").id, self._keyword("충전 인프라").id])
        fresh = self._post(title="OCPP 충전 표준 업데이트", index=10)
        stale = self._post(
            title="OCPP 옛 소식",
            index=11,
            collected_at=datetime.now(timezone.utc) - timedelta(days=8),
        )
        for post in (fresh, stale):
            self.db.add(
                PostKeyword(
                    post_id=post.id,
                    keyword_id=keyword.id,
                    confidence=0.9,
                    matched_by=KeywordMatchMethod.ai,
                )
            )
        self.db.commit()
        feed = PersonalizedNewsService(self.db).list_news(
            self.user, personalized=True, recency=True
        )
        ids = [item.id for item in feed.items]
        self.assertIn(fresh.id, ids)
        self.assertNotIn(stale.id, ids)

    def test_purge_keeps_report_linked_posts(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=8)
        kept = self._post(title="리포트 연결 전기차 충전 기사", index=21, collected_at=old)
        dropped = self._post(title="만료된 전기차 충전 기사", index=22, collected_at=old)
        report = DailyReport(
            organization_id=self.org_id,
            report_date=datetime.now(timezone.utc).date(),
            title="stub",
            summary="stub",
            model="none",
        )
        self.db.add(report)
        self.db.flush()
        self.db.add(
            DailyReportItem(
                report_id=report.id,
                post_id=kept.id,
                reason="브리핑",
                importance=Importance.medium,
            )
        )
        self.db.commit()
        count = PostService(self.db).purge_stale_published(self.org_id, retention_days=7)
        self.assertEqual(count, 1)
        self.db.refresh(kept)
        self.db.refresh(dropped)
        self.assertEqual(kept.status, PostStatus.published)
        self.assertEqual(dropped.status, PostStatus.deleted)

    def test_topic_terms_include_av_keywords(self) -> None:
        terms = load_topic_terms(self.db, self.org_id)
        lowered = {term.lower() for term in terms}
        self.assertTrue({"로보택시", "자율주행", "ocpp"} <= lowered or "ocpp" in lowered)
        self.assertIn("로보택시", lowered)
        self.assertIn("자율주행", lowered)

    def test_edition_topic_terms_stay_separate(self) -> None:
        editions = {row.slug: row for row in self.editions.list_editions(self.org_id)}
        ev_terms = {
            term.lower()
            for term in load_edition_topic_terms(self.db, self.org_id, editions[EV_SLUG].id)
        }
        av_terms = {
            term.lower()
            for term in load_edition_topic_terms(
                self.db, self.org_id, editions[AUTONOMOUS_SLUG].id
            )
        }
        self.assertIn("ocpp", ev_terms)
        self.assertIn("로보택시", av_terms)
        self.assertNotIn("로보택시", ev_terms)
        self.assertNotIn("ocpp", av_terms)

    def test_generate_briefings_are_edition_specific(self) -> None:
        prev_provider = os.environ.get("LLM_PROVIDER")
        os.environ["LLM_PROVIDER"] = "mock"
        get_settings.cache_clear()
        try:
            from zoneinfo import ZoneInfo

            from app.services.report_service import ReportService

            now = datetime.now(ZoneInfo("Asia/Seoul"))
            ev_post = self._post(title="전기차 충전 요금 인하", index=31, collected_at=now)
            av_post = self._post(title="자율주행 로보택시 운행 허가", index=32, collected_at=now)
            self.db.commit()
            editions = {row.slug: row for row in self.editions.list_editions(self.org_id)}
            svc = ReportService(self.db)
            ev_report = svc.generate(
                self.org_id, now.date(), allow_empty=True, edition_id=editions[EV_SLUG].id
            )
            av_report = svc.generate(
                self.org_id,
                now.date(),
                allow_empty=True,
                edition_id=editions[AUTONOMOUS_SLUG].id,
            )
            self.assertIn("전기차", ev_report.title)
            self.assertIn("자율주행", av_report.title)
            self.assertIn("전기차", ev_report.summary)
            self.assertIn("자율주행", av_report.summary)
            ev_ids = {item.post_id for item in ev_report.items}
            av_ids = {item.post_id for item in av_report.items}
            self.assertIn(ev_post.id, ev_ids)
            self.assertNotIn(av_post.id, ev_ids)
            self.assertIn(av_post.id, av_ids)
            self.assertNotIn(ev_post.id, av_ids)
        finally:
            if prev_provider is None:
                os.environ.pop("LLM_PROVIDER", None)
            else:
                os.environ["LLM_PROVIDER"] = prev_provider
            get_settings.cache_clear()


class TopicGateUnitTest(unittest.TestCase):
    def test_keyword_gate_accepts_av(self) -> None:
        self.assertTrue(
            passes_keyword_gate(
                "로보택시 운행 허가",
                "서울에서 자율주행 로보택시 시범 운행이 확대된다.",
                "https://example.com/av",
            )
        )

    def test_keyword_gate_accepts_extra_terms(self) -> None:
        self.assertTrue(
            passes_keyword_gate(
                "신규 모빌리티 시범",
                "로보버스 노선이 확대된다.",
                "https://example.com/robo",
                ["로보버스"],
            )
        )
        self.assertFalse(
            passes_keyword_gate(
                "신규 모빌리티 시범",
                "로보버스 노선이 확대된다.",
                "https://example.com/robo",
            )
        )


if __name__ == "__main__":
    unittest.main()
