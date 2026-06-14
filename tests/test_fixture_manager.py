import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from fixture_manager import merge_schedule_with_fixtures, normalize_fixture_list, schedule_match_allowed, team_key
from pipeline_storage import archive_entries


class FixtureManagerTests(unittest.TestCase):
    def test_aliases_normalize_team_keys(self):
        self.assertEqual(team_key("Türkiye"), team_key("Turkey"))
        self.assertEqual(team_key("Cura"), team_key("Curacao"))
        self.assertEqual(team_key("Qater"), team_key("Qatar"))

    def test_generated_fixture_ids_are_compact_and_stable(self):
        fixtures, errors = normalize_fixture_list(
            [
                {
                    "competition": "FIFA World Cup 2026",
                    "team1": "Germany",
                    "team2": "Curacao",
                    "match_time": "2026-06-14T17:00:00Z",
                }
            ]
        )
        self.assertEqual(errors, [])
        self.assertEqual(fixtures[0]["fixture_id"], "fifa_world_cup_2026_20260614_1700_germany_curacao")

    def test_world_cup_mode_rejects_non_world_cup_fixture_rows(self):
        self.assertFalse(
            schedule_match_allowed(
                {
                    "fixture_id": "premier_league_20260801_arsenal_psg",
                    "competition": "Premier League",
                },
                {"mode": "world_cup"},
            )
        )
        self.assertTrue(
            schedule_match_allowed(
                {
                    "fixture_id": "fifa_world_cup_2026_20260614_1700_germany_curacao",
                    "competition": "FIFA World Cup 2026",
                },
                {"mode": "world_cup"},
            )
        )

    def test_merge_dedupes_and_preserves_portal_fields(self):
        schedule = [
            {
                "match_name": "Germany Vs Cura",
                "match_time": "2026-06-14T17:00:00Z",
                "new_blogger_page_id": "page-1",
                "new_blogger_page_url": "https://www.goforsports.net/p/ger-info.html",
                "new_blogger_post_id": "post-1",
                "source_url": "https://sportscorner3697.blogspot.com/2026/06/germany-vs-curacao.html",
                "status": "pending",
            },
            {
                "match_name": "Germany Vs Curacao",
                "match_time": "2026-06-14T17:00:00Z",
                "source_url": [],
                "status": "pending",
            },
            {
                "match_name": "Arsenal Vs PSG",
                "match_time": "2026-06-14T04:00:00Z",
                "new_blogger_page_id": "club-page",
                "status": "pending",
            },
        ]
        fixtures, errors = normalize_fixture_list(
            [
                {
                    "team1": "Germany",
                    "team2": "Curacao",
                    "match_time": "2026-06-14T17:00:00Z",
                    "group": "E",
                    "venue": "Houston Stadium",
                }
            ]
        )
        self.assertEqual(errors, [])

        next_schedule, removed, stats = merge_schedule_with_fixtures(schedule, fixtures, prune_unsynced=True)

        self.assertEqual(len(next_schedule), 1)
        self.assertEqual(next_schedule[0]["match_name"], "Germany Vs Curacao")
        self.assertEqual(next_schedule[0]["new_blogger_page_id"], "page-1")
        self.assertEqual(next_schedule[0]["new_blogger_post_id"], "post-1")
        self.assertEqual(next_schedule[0]["venue"], "Houston Stadium")
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["match_name"], "Arsenal Vs PSG")
        self.assertEqual(stats["deduped"], 1)
        self.assertEqual(stats["removed"], 1)

    def test_archive_entries_are_written_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"scheduler": {"data_dir": tmpdir, "history_dir": os.path.join(tmpdir, "history")}}
            entry = {
                "fixture_id": "world_cup_2026_germany_curacao",
                "match_name": "Germany Vs Curacao",
                "match_time": "2026-06-14T17:00:00Z",
                "status": "completed",
            }
            now = datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc)
            archive_entries([entry], config=config, now=now, reason="completed")
            archive_entries([entry], config=config, now=now, reason="completed")
            history_path = os.path.join(tmpdir, "history", "2026-06.jsonl")
            with open(history_path, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["archive_reason"], "completed")


if __name__ == "__main__":
    unittest.main()
