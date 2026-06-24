import unittest

from portal_renderer import render_preview_post, render_streaming_page


class PortalRendererTests(unittest.TestCase):
    def test_preview_post_omits_live_coverage_smartlink(self):
        config = {
            "default_league": "FIFA World Cup 2026",
            "default_quality": "HD",
            "smartlink": {
                "enabled": True,
                "text": "Continue To Live Coverage",
                "url": "https://smart.example/live",
            },
        }
        match = {
            "match_name": "Brazil Vs Morocco",
            "match_time": "2026-06-14T17:00:00Z",
            "new_blogger_page_url": "https://www.goforsports.net/p/brazil-vs-morocco.html",
        }

        html = render_preview_post(config, match)

        self.assertIn("Click Here For Match Info", html)
        self.assertNotIn("Continue To Live Coverage", html)
        self.assertNotIn("https://smart.example/live", html)
        self.assertNotIn("live coverage buttons", html)

    def test_streaming_page_still_renders_smartlink(self):
        config = {
            "default_league": "FIFA World Cup 2026",
            "default_quality": "HD",
            "smartlink": {
                "enabled": True,
                "text": "Continue To Live Coverage",
                "url": "https://smart.example/live",
            },
        }
        match = {
            "match_name": "Brazil Vs Morocco",
            "match_time": "2026-06-14T17:00:00Z",
            "new_blogger_page_url": "https://www.goforsports.net/p/brazil-vs-morocco.html",
        }

        html = render_streaming_page(config, match)

        self.assertIn("Continue To Live Coverage", html)
        self.assertIn("https://smart.example/live", html)

    def test_preview_post_shows_countdown_not_final_score(self):
        config = {"default_league": "FIFA World Cup 2026"}
        match = {
            "match_name": "Portugal Vs Uzbekistan",
            "match_time": "2026-06-24T15:00:00Z",
            "new_blogger_page_url": "https://www.goforsports.net/p/portugal-info.html",
            "status": "completed",
            "result": {
                "status": "final",
                "team1_score": 2,
                "team2_score": 1,
                "score_text": "Portugal 2-1 Uzbekistan",
            },
        }

        html = render_preview_post(config, match)

        self.assertIn("Match Countdown", html)
        self.assertNotIn("Final Score", html)
        self.assertNotIn("Portugal 2-1 Uzbekistan", html)

    def test_streaming_page_shows_countdown_not_final_score(self):
        config = {"default_league": "FIFA World Cup 2026"}
        match = {
            "match_name": "Portugal Vs Uzbekistan",
            "match_time": "2026-06-24T15:00:00Z",
            "new_blogger_page_url": "https://www.goforsports.net/p/portugal-info.html",
            "status": "completed",
            "result": {
                "status": "final",
                "team1_score": 2,
                "team2_score": 1,
                "score_text": "Portugal 2-1 Uzbekistan",
            },
        }

        html = render_streaming_page(config, match, state="ended")

        self.assertIn("Match Countdown", html)
        self.assertNotIn("Final Score", html)
        self.assertNotIn("Portugal 2-1 Uzbekistan", html)


if __name__ == "__main__":
    unittest.main()
