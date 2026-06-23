import unittest

from sync_fixtures import parse_time_with_offset


class SyncFixturesTests(unittest.TestCase):
    def test_parse_time_with_signed_half_hour_offsets(self):
        self.assertEqual(
            parse_time_with_offset("2026-06-14", "10:00 p.m. UTC-5:30"),
            "2026-06-15T03:30:00Z",
        )
        self.assertEqual(
            parse_time_with_offset("2026-06-14", "10:00 p.m. UTC+5:30"),
            "2026-06-14T16:30:00Z",
        )


if __name__ == "__main__":
    unittest.main()
