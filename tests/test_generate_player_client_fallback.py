import unittest

from generate_player import (
    HTML_TEMPLATE,
    apply_playback_risk_override,
    choose_best_hls_variant_smoothness,
    filter_browser_candidates_when_primary_links_exist,
    is_client_browser_viable_probe_failure,
    make_client_browser_candidate_probe,
    score_stream_probe,
    stream_reliability_rank,
    summarize_hls_smoothness,
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

    def test_smooth_hls_samples_are_scored_above_buffer_risk(self):
        smooth = summarize_hls_smoothness([
            {"ok": True, "estimated_download_ms": 450, "download_ratio": 0.12, "headroom": 4.5},
            {"ok": True, "estimated_download_ms": 520, "download_ratio": 0.14, "headroom": 4.1},
        ])
        risky = summarize_hls_smoothness([
            {"ok": True, "estimated_download_ms": 5200, "download_ratio": 1.3, "headroom": 0.75},
            {"ok": True, "estimated_download_ms": 7600, "download_ratio": 1.9, "headroom": 0.55},
        ])

        self.assertEqual(smooth["smoothness_label"], "smooth")
        self.assertGreater(smooth["smoothness_score"], 0)
        self.assertEqual(risky["smoothness_label"], "buffer-risk")
        self.assertLess(risky["smoothness_score"], 0)
        self.assertGreater(smooth["smoothness_score"], risky["smoothness_score"])

    def test_smoothness_changes_stream_score_ordering(self):
        base = score_stream_probe("hls", 200, height=720, bandwidth=2_500_000)
        smooth = score_stream_probe("hls", 200, height=720, bandwidth=2_500_000, smoothness_score=70)
        risky = score_stream_probe("hls", 200, height=720, bandwidth=2_500_000, smoothness_score=-70)

        self.assertGreater(smooth, base)
        self.assertLess(risky, base)

    def test_player_template_uses_fast_startup_but_slow_stall_failover(self):
        self.assertIn("STARTUP_AUTOSWITCH_DELAY_MS = 1200", HTML_TEMPLATE)
        self.assertIn("STARTUP_TIMEOUT_MS = 12000", HTML_TEMPLATE)
        self.assertIn("POST_START_RECOVERY_MS = 60000", HTML_TEMPLATE)
        self.assertIn("STABLE_PLAYBACK_LOCK_MS = 60000", HTML_TEMPLATE)
        self.assertIn("HLS_STALL_TIMEOUT_MS = 30000", HTML_TEMPLATE)
        self.assertIn("function reliabilityRank", HTML_TEMPLATE)
        self.assertNotIn("Math.min(HLS_STALL_TIMEOUT_MS, 5000)", HTML_TEMPLATE)

    def test_player_template_freezes_visible_link_order(self):
        self.assertIn("let linkOrderLocked = false", HTML_TEMPLATE)
        self.assertIn("if (linkOrderLocked)", HTML_TEMPLATE)
        self.assertIn("linkOrderLocked = true", HTML_TEMPLATE)

    def test_player_template_keeps_post_start_hls_failover_inside_hls(self):
        self.assertIn(
            "const failoverType = preferredFailoverType || ((playbackStarted || playbackHealthySince) ? failedType : null);",
            HTML_TEMPLATE,
        )
        self.assertIn(
            "activeLnk.type === 'hls' && nextLnk && nextLnk.type !== 'hls'",
            HTML_TEMPLATE,
        )
        self.assertIn("startStallWatchdog(attemptId, hlsFailureText(), POST_START_RECOVERY_MS)", HTML_TEMPLATE)

    def test_reliability_rank_prefers_stable_hls_over_unknown_dash_and_risk(self):
        stable_hls = {"stream_type": "hls", "probe": {"smoothness_label": "stable"}}
        unknown_dash = {"stream_type": "dash", "probe": {"smoothness_label": "unknown"}}
        risky_hls = {"stream_type": "hls", "probe": {"smoothness_label": "buffer-risk"}}

        self.assertLess(stream_reliability_rank(stable_hls), stream_reliability_rank(unknown_dash))
        self.assertLess(stream_reliability_rank(stable_hls), stream_reliability_rank(risky_hls))

    def test_browser_candidates_are_hidden_when_verified_links_exist(self):
        verified = {"stream_type": "hls", "probe": {"working": True, "smoothness_label": "stable"}}
        client_candidate = {"stream_type": "hls", "probe": {"working": True, "browser_candidate": True}}

        self.assertEqual(filter_browser_candidates_when_primary_links_exist([client_candidate, verified]), [verified])
        self.assertEqual(filter_browser_candidates_when_primary_links_exist([client_candidate]), [client_candidate])

    def test_hls_variant_selector_prefers_sustainable_video_variant(self):
        selected = choose_best_hls_variant_smoothness([
            {
                "reason": "hls-media-ok",
                "status": 200,
                "smoothness": {
                    "smoothness_label": "buffer-risk",
                    "smoothness_score": -115,
                    "selected_height": 1440,
                    "selected_bandwidth": 11_545_600,
                },
            },
            {
                "reason": "hls-media-ok",
                "status": 200,
                "smoothness": {
                    "smoothness_label": "stable",
                    "smoothness_score": 55,
                    "selected_height": 720,
                    "selected_bandwidth": 2_500_000,
                },
            },
            {
                "reason": "hls-media-ok",
                "status": 200,
                "smoothness": {
                    "smoothness_label": "smooth",
                    "smoothness_score": 90,
                    "selected_height": None,
                    "selected_bandwidth": 128_000,
                },
            },
        ])

        self.assertEqual(selected["smoothness"]["smoothness_label"], "stable")
        self.assertEqual(selected["smoothness"]["selected_height"], 720)

    def test_known_browser_buffering_source_is_forced_to_risk(self):
        probe = apply_playback_risk_override(
            "https://ts.sptck.cfd/hls/tist1.m3u8",
            {
                "validation_reason": "hls-media-ok",
                "smoothness_label": "smooth",
                "smoothness_score": 90,
            },
        )

        self.assertEqual(probe["smoothness_label"], "buffer-risk")
        self.assertLessEqual(probe["smoothness_score"], -120)
        self.assertIn("playback-risk:frequent-browser-buffering", probe["validation_reason"])


if __name__ == "__main__":
    unittest.main()
