import hashlib
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.enums import BoardType, CreatedBy, PostStatus, SearchIndexAction, TrustLevel
from app.models.organization import Organization
from app.models.post import Post
from app.models.search_index_queue import SearchIndexQueue
from app.search.index_outbox import enqueue_search_index, pending_search_index_count, process_search_index_queue


class SearchIndexOutboxTest(unittest.TestCase):
    def setUp(self) -> None:
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
        self.post = Post(
            organization_id=org.id,
            board_type=BoardType.trusted,
            title="OCPP 충전 뉴스",
            raw_content="",
            content_hash=hashlib.sha256(b"post").hexdigest(),
            status=PostStatus.published,
            trust_level=TrustLevel.high,
            reliability_score=90,
            created_by=CreatedBy.crawler,
        )
        self.db.add(self.post)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @patch("app.core.config.get_settings")
    def test_enqueue_and_process_index(self, mock_settings) -> None:
        settings = mock_settings.return_value
        settings.search_uses_elasticsearch = True

        with patch("app.search.index_outbox.save_post_content", return_value=True) as save_mock:
            enqueue_search_index(
                self.db,
                self.post.id,
                SearchIndexAction.index,
                payload={"summary": "요약", "body": "본문"},
            )
            self.db.commit()
            self.assertEqual(pending_search_index_count(self.db), 1)

            with patch("app.workers.tasks.process_search_index_queue_task.delay"):
                ok, failed = process_search_index_queue(self.db)
            self.assertEqual((ok, failed), (1, 0))
            self.assertEqual(pending_search_index_count(self.db), 0)
            save_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
