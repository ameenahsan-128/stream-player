import unittest

from match_metadata import (
    add_metadata_url,
    content_hash,
    extract_result_from_html,
    metadata_urls_for_match,
)


class MatchMetadataTests(unittest.TestCase):
    def test_metadata_urls_are_separate_from_stream_sources(self):
        match = {
            "fixture_source_url": "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_Group_E",
            "source_url": ["https://worldcup.epicsports.mobi/stream.html"],
        }
        self.assertEqual(
            metadata_urls_for_match(match),
            ["https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_Group_E"],
        )

    def test_add_metadata_url_records_domain_and_dedupes(self):
        match = {}
        self.assertTrue(add_metadata_url(match, "https://www.fifa.com/match-centre", role="search"))
        self.assertFalse(add_metadata_url(match, "https://www.fifa.com/match-centre", role="search"))
        self.assertEqual(match["metadata_urls"], ["https://www.fifa.com/match-centre"])
        self.assertEqual(match["metadata_records"][0]["domain"], "fifa.com")
        self.assertEqual(match["metadata_records"][0]["role"], "search")

    def test_extracts_final_score_near_team_names(self):
        html = """
        <html><body>
          <h1>Germany 2-1 Curacao</h1>
          <p>Full-time result from the FIFA World Cup.</p>
        </body></html>
        """
        result = extract_result_from_html(
            html,
            {"team1": "Germany", "team2": "Curacao"},
            source_url="https://www.fifa.com/match",
            checked_at="2026-06-14T20:00:00Z",
        )
        self.assertEqual(result["status"], "final")
        self.assertEqual(result["team1_score"], 2)
        self.assertEqual(result["team2_score"], 1)

    def test_content_hash_is_stable(self):
        self.assertEqual(content_hash("same html"), content_hash("same html"))
        self.assertNotEqual(content_hash("same html"), content_hash("other html"))


if __name__ == "__main__":
    unittest.main()
