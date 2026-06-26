import unittest

from app.services.post_dedup import compute_content_hash, normalize_crawl_url, normalize_title_for_dedup


class PostDedupTest(unittest.TestCase):
    def test_normalize_crawl_url_strips_tracking_params(self) -> None:
        a = "https://www.example.com/news/ev-charger?utm_source=google&id=12"
        b = "https://example.com/news/ev-charger?id=12"
        self.assertEqual(normalize_crawl_url(a), normalize_crawl_url(b))

    def test_compute_content_hash_uses_canonical_url(self) -> None:
        a = compute_content_hash(
            "https://example.com/article/1?utm_campaign=x",
            "Original English headline",
        )
        b = compute_content_hash(
            "https://www.example.com/article/1",
            "Different translated headline",
        )
        self.assertEqual(a, b)

    def test_normalize_title_for_dedup_collapses_whitespace(self) -> None:
        self.assertEqual(
            normalize_title_for_dedup("  Tesla   opens   hub  "),
            normalize_title_for_dedup("Tesla opens hub"),
        )


if __name__ == "__main__":
    unittest.main()
