import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.search.post_search_query import PostSearchFilters, search_posts


class PostSearchQueryTest(unittest.TestCase):
    @patch("app.search.post_search_query.get_es_client")
    @patch("app.search.post_search_query.get_settings")
    def test_search_posts_applies_filters_and_highlight(self, mock_settings, mock_client) -> None:
        settings = mock_settings.return_value
        settings.search_uses_elasticsearch = True
        settings.elasticsearch_index_posts = "mint-posts-v1"

        client = MagicMock()
        mock_client.return_value = client
        client.search.return_value = {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "post-1",
                        "_score": 2.5,
                        "_source": {
                            "post_id": str(uuid4()),
                            "title": "OCPP 충전",
                            "summary": "충전 인프라",
                            "board_type": "trusted",
                            "source_name": "테스트",
                            "original_url": "https://example.com",
                        },
                        "highlight": {
                            "summary": ["<em>충전</em> 인프라"],
                        },
                    }
                ],
            }
        }

        org_id = uuid4()
        result = search_posts(
            PostSearchFilters(organization_id=org_id, query="충전", category="기술"),
            page=2,
            size=10,
            highlight=True,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.total, 1)
        self.assertEqual(result.hits[0].highlight_summary, "<em>충전</em> 인프라")

        kwargs = client.search.call_args.kwargs
        self.assertEqual(kwargs["from"], 10)
        self.assertEqual(kwargs["size"], 10)
        self.assertIn("highlight", kwargs)
        bool_query = kwargs["query"]["bool"]
        self.assertTrue(any("category" in str(clause) for clause in bool_query["filter"]))


if __name__ == "__main__":
    unittest.main()
