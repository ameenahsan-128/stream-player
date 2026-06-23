import unittest

from generate_player import (
    is_client_browser_viable_probe_failure,
    make_client_browser_candidate_probe,
)


class GeneratePlayerClientFallbackTests(unittest.TestCase):
    def test_access_and_cors_failures_are_browser_candidates(self):
        self.assertTrue(is_client_browser_viable_probe_failure({"validation_reason": "http-403", "status_code": 403}))
        self.assertTrue(is_client_browser_viable_probe_failure({"validation_reason": "hls-child-http-403", "media_probe_status": 403}))
        self.assertTrue(is_client_browser_viable_probe_failure({"validation_reason": "hls-segment-cors-blocked"}))
        self.assertTrue(is_client_browser_viable_probe_failure({"validation_reason": "dash-init-cors-blocked"}))

    def test_dns_and_junk_failures_are_not_browser_candidates(self):
        self.assertFalse(is_client_browser_viable_probe_failure({"validation_reason": "NameResolutionError: failed to resolve"}))
        self.assertFalse(is_client_browser_viable_probe_failure({"validation_reason": "junk-iframe-blocked"}))
        self.assertFalse(is_client_browser_viable_probe_failure({"validation_reason": "iframe-is-raw-website"}))
        self.assertFalse(is_client_browser_viable_probe_failure({"validation_reason": "hls-vod-or-ended-playlist"}))

    def test_client_browser_candidate_probe_is_low_priority_backup(self):
        probe = make_client_browser_candidate_probe(
            {
                "validation_reason": "hls-child-http-403",
                "status_code": 200,
                "media_probe_status": 403,
                "latency_ms": 120,
            },
            "hls",
            "https://cdn.example/live/index.m3u8",
        )

        self.assertTrue(probe["working"])
        self.assertTrue(probe["backup"])
        self.assertTrue(probe["browser_candidate"])
        self.assertEqual(probe["validation_status"], "client_browser_candidate")
        self.assertIn("hls-child-http-403", probe["validation_reason"])
        self.assertLess(probe["score"], 300)


if __name__ == "__main__":
    unittest.main()
