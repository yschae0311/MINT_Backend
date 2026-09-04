import unittest

from bs4 import BeautifulSoup

from app.services.article_text import html_to_article_text, normalize_article_text, soup_to_article_text


class ArticleTextTests(unittest.TestCase):
    def test_html_keeps_paragraphs_and_line_breaks(self):
        html = """
        <article>
          <p>첫 문단입니다. 전기차 충전 인프라가 늘어납니다.</p>
          <p>둘째 문단입니다.<br>줄이 바뀝니다.</p>
          <div>셋째 덩어리입니다.</div>
        </article>
        """
        text = html_to_article_text(html)
        self.assertIn("첫 문단입니다.", text)
        self.assertIn("둘째 문단입니다.", text)
        self.assertIn("줄이 바뀝니다.", text)
        self.assertIn("셋째 덩어리입니다.", text)
        self.assertIn("\n\n", text)
        self.assertIn("첫 문단입니다.", text.split("\n\n")[0])
        self.assertNotEqual(text.split("\n\n")[0], text)

    def test_html_does_not_break_inline_links(self):
        html = '<p>현대차가 <a href="/x">충전소</a>를 확대한다.</p>'
        text = html_to_article_text(html)
        self.assertEqual(text, "현대차가 충전소를 확대한다.")

    def test_rss_fragment_keeps_paragraphs(self):
        html = "<p>요약 첫째.</p><p>요약 둘째.</p>"
        text = html_to_article_text(html)
        self.assertEqual(text, "요약 첫째.\n\n요약 둘째.")

    def test_plain_text_collapses_spaces_but_keeps_breaks(self):
        text = normalize_article_text("  가   나  \n\n  다  ")
        self.assertEqual(text, "가 나\n\n다")

    def test_soup_from_selected_node(self):
        soup = BeautifulSoup(
            '<body><nav>메뉴</nav><div class="view"><p>본문만.</p></div></body>',
            "html.parser",
        )
        text = soup_to_article_text(soup.select_one(".view"))
        self.assertEqual(text, "본문만.")
        self.assertNotIn("메뉴", text)

    def test_max_chars_cuts_on_paragraph(self):
        text = normalize_article_text("하나.\n\n둘.\n\n셋.", max_chars=8)
        self.assertIn("하나.", text)
        self.assertNotIn("셋.", text)
