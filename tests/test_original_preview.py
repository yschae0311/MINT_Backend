import unittest

from app.services.original_preview_service import iframe_embed_blocked


class IframeEmbedBlockedTests(unittest.TestCase):
    def test_x_frame_options_sameorigin(self):
        headers = {"X-Frame-Options": "SAMEORIGIN"}
        self.assertTrue(iframe_embed_blocked(headers))

    def test_x_frame_options_deny(self):
        headers = {"X-Frame-Options": "DENY"}
        self.assertTrue(iframe_embed_blocked(headers))

    def test_csp_frame_ancestors_self(self):
        headers = {
            "Content-Security-Policy": "default-src 'self'; frame-ancestors 'self'",
        }
        self.assertTrue(iframe_embed_blocked(headers))

    def test_csp_frame_ancestors_none(self):
        headers = {"Content-Security-Policy": "frame-ancestors 'none'"}
        self.assertTrue(iframe_embed_blocked(headers))

    def test_csp_frame_ancestors_wildcard(self):
        headers = {"Content-Security-Policy": "frame-ancestors *"}
        self.assertFalse(iframe_embed_blocked(headers))

    def test_no_blocking_headers(self):
        headers = {"Content-Type": "text/html"}
        self.assertFalse(iframe_embed_blocked(headers))

    def test_meta_x_frame_options(self):
        html = '<html><head><meta http-equiv="X-Frame-Options" content="SAMEORIGIN"></head></html>'
        self.assertTrue(iframe_embed_blocked({}, html))

    def test_zdnet_like_headers(self):
        headers = {
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https:; "
                "style-src 'self' 'unsafe-inline' https:;"
            ),
        }
        self.assertTrue(iframe_embed_blocked(headers))

    def test_x_frame_options_combined_sameorigin_deny(self):
        headers = {"X-Frame-Options": "SAMEORIGIN, DENY"}
        self.assertTrue(iframe_embed_blocked(headers))

    def test_clien_like_duplicate_xfo(self):
        class _Headers:
            def multi_items(self):
                return [
                    ("X-Frame-Options", "SAMEORIGIN"),
                    ("X-Frame-Options", "DENY"),
                ]

        self.assertTrue(iframe_embed_blocked(_Headers()))


if __name__ == "__main__":
    unittest.main()
