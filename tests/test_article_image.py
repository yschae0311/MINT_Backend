import unittest

from app.services.article_image import extract_article_image_url


class ArticleImageTests(unittest.TestCase):
    def test_prefers_og_image(self):
        html = """
        <html><head>
          <meta property="og:image" content="/photos/hero.jpg">
        </head><body>
          <article><img src="/tiny-icon.png" width="16" height="16"></article>
        </body></html>
        """
        url = extract_article_image_url(html, "https://news.example.com/a")
        self.assertEqual(url, "https://news.example.com/photos/hero.jpg")

    def test_falls_back_to_article_img(self):
        html = """
        <html><body>
          <article>
            <img src="https://cdn.example.com/story.jpg" width="800" height="450">
          </article>
        </body></html>
        """
        url = extract_article_image_url(html, "https://news.example.com/a")
        self.assertEqual(url, "https://cdn.example.com/story.jpg")

    def test_skips_tracking_and_svg(self):
        html = """
        <html><body>
          <article>
            <img src="https://cdn.example.com/pixel.gif">
            <img src="https://cdn.example.com/logo.svg">
            <img src="https://cdn.example.com/ok.jpg" width="640" height="360">
          </article>
        </body></html>
        """
        url = extract_article_image_url(html, "https://news.example.com/a")
        self.assertEqual(url, "https://cdn.example.com/ok.jpg")
