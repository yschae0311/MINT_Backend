import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.enums import SourceType
from app.models.source import Source
from app.services.crawl_progress import CrawlProgressTracker, estimate_discovery_candidates_per_source


class CrawlProgressTests(unittest.TestCase):
    def test_estimate_rss_source(self):
        source = Source(
            organization_id=uuid4(),
            name="test",
            url="https://example.com/feed",
            source_type=SourceType.rss,
            category="정책",
        )
        self.assertEqual(estimate_discovery_candidates_per_source(source), 15)

    def test_estimate_community_source(self):
        source = Source(
            organization_id=uuid4(),
            name="forum",
            url="https://example.com/board",
            source_type=SourceType.community_forum,
            category="커뮤니티",
        )
        self.assertEqual(estimate_discovery_candidates_per_source(source), 10)

    def test_tracker_message_increments(self):
        jobs = MagicMock()
        job_id = uuid4()
        tracker = CrawlProgressTracker(
            jobs,
            job_id,
            source_total=2,
            estimated_candidate_total=30,
        )
        tracker.on_source_start(1, "정책브리핑")
        tracker.on_candidate_done(created=True)
        tracker.on_candidate_done(created=False)
        tracker.finish()

        last_call = jobs.update_progress.call_args_list[-1]
        self.assertEqual(last_call.args[1], 2)
        self.assertIn("2 / 30", last_call.args[3])
        self.assertIn("등록 1", last_call.args[3])
        self.assertIn("스킵 1", last_call.args[3])


if __name__ == "__main__":
    unittest.main()
