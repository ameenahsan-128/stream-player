import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Import the check_and_run function to test
from match_scheduler import check_and_run, auto_discover_matches

class TestSchedulerSafety(unittest.TestCase):
    @patch("match_scheduler.load_automation_config")
    @patch("match_scheduler.load_schedule")
    @patch("match_scheduler.save_schedule")
    @patch("match_scheduler.get_player_slots")
    @patch("match_scheduler.has_oauth")
    @patch("match_scheduler.archive_completed_matches")
    @patch("match_scheduler.auto_discover_matches")
    @patch("match_scheduler.run_source_prediction")
    @patch("match_scheduler.ensure_runtime_dirs")
    def test_failed_match_moves_to_review(
        self,
        mock_ensure_dirs,
        mock_predict,
        mock_discover,
        mock_archive,
        mock_has_oauth,
        mock_get_slots,
        mock_save_schedule,
        mock_load_schedule,
        mock_load_config
    ):
        # Setup configs
        mock_load_config.return_value = {
            "scheduler": {
                "active_window_start_minutes": 15,
                "active_window_end_hours": 3,
                "max_scrape_failures": 5
            }
        }
        
        # Define a match that is in its active window but has 5 failures
        now = datetime.now(timezone.utc)
        match_time = now + timedelta(minutes=5)
        
        schedule = [
            {
                "match_name": "Test Match 1",
                "match_time": match_time.isoformat().replace("+00:00", "Z"),
                "status": "pending",
                "scrape_fail_count": 5
            }
        ]
        mock_load_schedule.return_value = schedule
        mock_has_oauth.return_value = False
        mock_get_slots.return_value = []
        mock_archive.side_effect = lambda s, *args: (s, [])
        
        # Run check_and_run
        check_and_run()
        
        # Verify match status was updated to review
        self.assertEqual(schedule[0]["status"], "review")
        # Verify save_schedule was called to persist the status update
        mock_save_schedule.assert_called()

    @patch("match_scheduler.load_automation_config")
    @patch("match_scheduler.load_schedule")
    @patch("match_scheduler.save_schedule")
    @patch("match_scheduler.get_player_slots")
    @patch("match_scheduler.has_oauth")
    @patch("match_scheduler.archive_completed_matches")
    @patch("match_scheduler.auto_discover_matches")
    @patch("match_scheduler.run_source_prediction")
    @patch("match_scheduler.ensure_runtime_dirs")
    def test_review_status_skipped(
        self,
        mock_ensure_dirs,
        mock_predict,
        mock_discover,
        mock_archive,
        mock_has_oauth,
        mock_get_slots,
        mock_save_schedule,
        mock_load_schedule,
        mock_load_config
    ):
        # Setup configs
        mock_load_config.return_value = {
            "scheduler": {
                "active_window_start_minutes": 15,
                "active_window_end_hours": 3
            }
        }
        
        # Define a match with status review
        now = datetime.now(timezone.utc)
        match_time = now + timedelta(minutes=5)
        
        schedule = [
            {
                "match_name": "Test Match 2",
                "match_time": match_time.isoformat().replace("+00:00", "Z"),
                "status": "review",
                "scrape_fail_count": 0
            }
        ]
        mock_load_schedule.return_value = schedule
        mock_has_oauth.return_value = False
        mock_get_slots.return_value = []
        mock_archive.side_effect = lambda s, *args: (s, [])
        
        # Run check_and_run
        check_and_run()
        
        # Since the review match is active (now <= run_end), it is skipped.
        # Verify save_schedule was not called since it was skipped.
        mock_save_schedule.assert_not_called()

    @patch("match_scheduler.is_internet_available")
    @patch("match_scheduler.load_automation_config")
    @patch("match_scheduler.load_schedule")
    @patch("match_scheduler.save_schedule")
    @patch("match_scheduler.get_player_slots")
    @patch("match_scheduler.has_oauth")
    @patch("match_scheduler.archive_completed_matches")
    @patch("match_scheduler.auto_discover_matches")
    @patch("match_scheduler.run_source_prediction")
    @patch("match_scheduler.ensure_runtime_dirs")
    @patch("subprocess.run")
    def test_offline_prevents_failure_increment(
        self,
        mock_run,
        mock_ensure_dirs,
        mock_predict,
        mock_discover,
        mock_archive,
        mock_has_oauth,
        mock_get_slots,
        mock_save_schedule,
        mock_load_schedule,
        mock_load_config,
        mock_is_internet
    ):
        mock_is_internet.return_value = False
        mock_run.side_effect = Exception("Failed to connect")
        mock_load_config.return_value = {
            "scheduler": {
                "active_window_start_minutes": 15,
                "active_window_end_hours": 3,
                "max_scrape_failures": 5
            }
        }
        
        now = datetime.now(timezone.utc)
        match_time = now + timedelta(minutes=5)
        
        schedule = [
            {
                "match_name": "Test Match Offline",
                "match_time": match_time.isoformat().replace("+00:00", "Z"),
                "status": "pending",
                "scrape_fail_count": 0,
                "source_url": ["https://test.com/stream"]
            }
        ]
        mock_load_schedule.return_value = schedule
        mock_has_oauth.return_value = False
        mock_get_slots.return_value = []
        mock_archive.side_effect = lambda s, *args: (s, [])
        
        # Run check_and_run
        check_and_run()
        
        # Since is_internet_available returned False, scrape_fail_count should remain 0
        self.assertEqual(schedule[0]["scrape_fail_count"], 0)

    @patch("match_scheduler.save_schedule")
    @patch("match_scheduler.save_scheduler_state")
    @patch("match_scheduler.load_scheduler_state")
    @patch("match_scheduler.resolve_source_shortcuts")
    @patch("match_scheduler.get_scheduler_config")
    @patch("match_scheduler.get_fixture_api_config")
    @patch("match_scheduler.load_schedule")
    @patch("match_scheduler.load_automation_config")
    def test_shortcut_resolution_flag_is_persisted_without_new_urls(
        self,
        mock_load_config,
        mock_load_schedule,
        mock_get_fixture_api,
        mock_get_scheduler_config,
        mock_resolve_shortcuts,
        mock_load_state,
        mock_save_state,
        mock_save_schedule,
    ):
        now = datetime.now(timezone.utc)
        schedule = [
            {
                "match_name": "Shortcut Match",
                "match_time": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "pending",
                "source_url": "https://source.example/match",
            }
        ]
        mock_load_config.return_value = {}
        mock_load_schedule.return_value = schedule
        mock_get_fixture_api.return_value = {"enabled": True}
        mock_get_scheduler_config.return_value = {
            "discovery_portals": [],
            "trusted_source_domains": [],
            "max_sources_per_match": 8,
        }
        mock_load_state.return_value = {}
        mock_resolve_shortcuts.return_value = ["https://source.example/match"]

        auto_discover_matches(force=True)

        self.assertTrue(schedule[0]["_shortcuts_resolved"])
        mock_save_schedule.assert_called_once_with(schedule, {})

    @patch("match_scheduler.clean_generated_files")
    @patch("match_scheduler.load_automation_config")
    @patch("match_scheduler.load_schedule")
    @patch("match_scheduler.save_schedule")
    @patch("match_scheduler.get_player_slots")
    @patch("match_scheduler.has_oauth")
    @patch("match_scheduler.archive_completed_matches")
    @patch("match_scheduler.ensure_runtime_dirs")
    def test_clean_generated_files_called(
        self,
        mock_ensure_dirs,
        mock_archive,
        mock_has_oauth,
        mock_get_slots,
        mock_save_schedule,
        mock_load_schedule,
        mock_load_config,
        mock_clean_files
    ):
        mock_load_config.return_value = {}
        mock_load_schedule.return_value = [{"match_name": "Test", "match_time": "2026-06-26T04:49:33.855562Z", "status": "completed"}]
        mock_archive.return_value = ([], [])
        mock_ensure_dirs.return_value = {}
        
        check_and_run()
        mock_clean_files.assert_called_once()


if __name__ == "__main__":
    unittest.main()
