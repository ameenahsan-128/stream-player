import unittest

from daily_blog_poster import _clean_feed_text, fallback_blog_content, build_post_html


class DailyBlogPosterTests(unittest.TestCase):
    def test_clean_feed_text_strips_cdata_before_tags(self):
        cleaned = _clean_feed_text("<![CDATA[Team <b>wins</b> & more]]>")

        self.assertIn("Team", cleaned)
        self.assertIn("wins", cleaned)
        self.assertNotIn("<![CDATA", cleaned)
        self.assertNotIn("<b>", cleaned)

    def test_fallback_blog_content_escapes_rss_fields(self):
        _title, body = fallback_blog_content([
            {
                "title": '<img src=x onerror="alert(1)">',
                "summary": '<script>alert(1)</script> summary',
                "link": "javascript:alert(1)",
            }
        ])

        self.assertIn("&lt;img", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("javascript:alert", body)

    def test_build_post_html_escapes_source_titles_and_links(self):
        html = build_post_html(
            "Title",
            "<p>Body</p>",
            "",
            [{"title": "<b>Bad</b>", "link": "https://example.com/?q=<x>"}],
            "June 23, 2026",
        )

        self.assertIn("&lt;b&gt;Bad&lt;/b&gt;", html)
        self.assertIn("https://example.com/?q=%3Cx%3E", html)
        self.assertNotIn("<b>Bad</b>", html)


if __name__ == "__main__":
    unittest.main()
