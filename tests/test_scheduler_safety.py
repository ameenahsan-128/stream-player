import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Import the check_and_run function to test
from match_scheduler import check_and_run

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
        with patch("match_scheduler.active_window") as mock_active_window:
            check_and_run()
            # If the review match is skipped at the start of the loop,
            # active_window should not be called for it.
            mock_active_window.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
