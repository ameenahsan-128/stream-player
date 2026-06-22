import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Import functions to test
import sys
sys.path.append("/home/expertz/stream")
from precreate_posts import preview_publish_overrides, published_date_mismatch, match_key

class TestPrecreateOrder(unittest.TestCase):
    def test_preview_publish_overrides_ordering(self):
        # Setup schedule of 2 matches
        # Match A (earlier kickoff): June 21, 16:00
        # Match B (later kickoff): June 21, 19:00
        now = datetime(2026, 6, 21, 6, 0, 0, tzinfo=timezone.utc)
        schedule = [
            {
                "match_name": "Spain Vs Saudi Arabia",
                "match_time": "2026-06-21T16:00:00Z"
            },
            {
                "match_name": "Belgium Vs Iran",
                "match_time": "2026-06-21T19:00:00Z"
            }
        ]
        
        with patch("precreate_posts.match_in_precreate_scope", return_value=True):
            overrides = preview_publish_overrides(
                schedule,
                target_ist_date=None,
                within_hours=None,
                now=now,
                enabled=True
            )
            
        key_a = match_key(schedule[0])
        key_b = match_key(schedule[1])
        
        self.assertIn(key_a, overrides)
        self.assertIn(key_b, overrides)
        
        # Parse output dates
        date_a = datetime.fromisoformat(overrides[key_a].replace("Z", "+00:00"))
        date_b = datetime.fromisoformat(overrides[key_b].replace("Z", "+00:00"))
        
        # Verify that earlier kickoff (Match A) has a LATER (newer) published date than Match B
        self.assertTrue(date_a > date_b, f"Expected {date_a} > {date_b} to preserve descending order for blogger homepage")
        
        # Verify that the generated dates are fresh (in June 2026, close to 'now')
        self.assertEqual(date_a.year, 2026)
        self.assertEqual(date_a.month, 6)
        self.assertEqual(date_a.day, 20)

    @patch("requests.get")
    def test_published_date_mismatch_detection(self, mock_get):
        # Setup blogger response with an old published date
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "published": "2023-07-14T01:00:00-07:00"
        }
        mock_get.return_value = mock_response
        
        config = {"blog_id": "test_blog_id"}
        access_token = "test_token"
        post_id = "test_post_id"
        desired_pub = "2026-06-20T08:00:00Z"
        
        # Mismatch should be detected
        mismatch = published_date_mismatch(config, access_token, post_id, desired_pub)
        self.assertTrue(mismatch)
        
        # Check call parameters
        mock_get.assert_called_with(
            "https://www.googleapis.com/blogger/v3/blogs/test_blog_id/posts/test_post_id",
            headers={"Authorization": "Bearer test_token"},
            timeout=10
        )
        
        # Same date should NOT trigger a mismatch (ignoring small offset/timezone format details)
        mock_response.json.return_value = {
            "published": "2026-06-20T08:00:00Z"
        }
        mismatch = published_date_mismatch(config, access_token, post_id, desired_pub)
        self.assertFalse(mismatch)

if __name__ == '__main__':
    unittest.main()
