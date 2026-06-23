import json
import os
import tempfile
import unittest
from unittest.mock import patch

import hoturl_api


class HotUrlApiTests(unittest.TestCase):
    def test_save_schedule_and_log_submission_create_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schedule_path = os.path.join(tmpdir, "nested", "match_schedule.json")
            log_path = os.path.join(tmpdir, "nested", "hoturl_log.json")

            with patch.object(hoturl_api, "SCHEDULE_FILE", schedule_path), patch.object(hoturl_api, "HOT_LOG_FILE", log_path):
                hoturl_api.save_schedule([{"match_name": "Brazil Vs Morocco"}])
                hoturl_api.log_submission({"match": "Brazil Vs Morocco"})

            with open(schedule_path, "r", encoding="utf-8") as f:
                schedule = json.load(f)
            with open(log_path, "r", encoding="utf-8") as f:
                logs = json.load(f)

            self.assertEqual(schedule[0]["match_name"], "Brazil Vs Morocco")
            self.assertEqual(logs[0]["match"], "Brazil Vs Morocco")


if __name__ == "__main__":
    unittest.main()
