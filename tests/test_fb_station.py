"""Station Elsewhere Facebook: own credentials, page-name guard and 3 AM / 9 AM Manila slots (no network)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import fb_station, shared_features

PHT = fb_station.TZ


def at(y, m, d, h, minute=0):
    return int(datetime(y, m, d, h, minute, tzinfo=PHT).timestamp())


@unittest.skipUnless(shared_features.facebook, "Requires sibling StoryCraft")
class FacebookStationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for item in (patch.object(fb_station, "ENV_FILE", root / "facebook.env"),
                     patch.object(fb_station, "LOG_FILE", root / "facebook_posts.json"),
                     patch.dict(fb_station._page_cache, {"at": 0.0, "key": None, "value": None}),
                     patch.object(fb_station, "PAGE_ID", "1000000000000001")):
            item.start()
            self.addCleanup(item.stop)

    def test_slots_continue_after_the_last_post(self):
        now = at(2026, 11, 7, 20)
        self.assertEqual(fb_station.next_slots(1, at(2026, 11, 8, 3), now), [at(2026, 11, 8, 9)])
        self.assertEqual(fb_station.next_slots(3, at(2026, 11, 8, 9), now),
                         [at(2026, 11, 9, 3), at(2026, 11, 9, 9), at(2026, 11, 10, 3)])

    def test_slots_never_in_the_past_or_too_soon(self):
        now = at(2026, 11, 8, 2, 50)           # 3:00 is only 10 minutes away: too soon to upload
        self.assertEqual(fb_station.next_slots(2, None, now), [at(2026, 11, 8, 9), at(2026, 11, 9, 3)])
        old = at(2026, 10, 1, 3)
        self.assertEqual(fb_station.next_slots(1, old, at(2026, 11, 7, 12)), [at(2026, 11, 8, 3)])

    def test_log_sets_the_last_post(self):
        page = fb_station.PAGE_ID
        fb_station.record({"video": "a.mp4", "time": at(2026, 11, 8, 3), "page_id": page})
        fb_station.record({"video": "b.mp4", "time": at(2026, 11, 8, 9), "page_id": page})
        fb_station.record({"video": "c.mp4", "time": at(2026, 11, 9, 3), "page_id": "1000000000000002"})
        self.assertEqual(fb_station.last_post_time(remote=False), at(2026, 11, 8, 9))
        self.assertEqual(fb_station.posted_names(), {"a.mp4", "b.mp4"})
        self.assertEqual(fb_station.label(at(2026, 11, 8, 9)), "Sun Nov 8, 2026 9:00 AM PHT")

    def test_environment_tokens_are_never_used(self):
        with patch.dict("os.environ", {"FB_PAGE_ID": "lostneet", "FB_PAGE_ACCESS_TOKEN": "x"}):
            self.assertEqual(fb_station.credentials(), ("", ""))
            self.assertIs(shared_features.facebook.credentials, fb_station.credentials)

    def test_other_pages_are_refused(self):
        with patch.object(fb_station, "_lookup", return_value={"id": "1", "name": "Lost NEET"}):
            with self.assertRaises(shared_features.facebook.FacebookError):
                fb_station.connect("1", "token")
            self.assertFalse(fb_station.ENV_FILE.exists())
            fb_station.ENV_FILE.write_text("FB_PAGE_ID=1\nFB_PAGE_ACCESS_TOKEN=t\n")
            with self.assertRaises(shared_features.facebook.FacebookError):
                fb_station.require_station_page()

    def test_station_page_is_saved_privately(self):
        with patch.object(fb_station, "_lookup", return_value={"id": fb_station.PAGE_ID, "name": "Station Elsewhere"}):
            self.assertTrue(fb_station.connect(fb_station.PAGE_ID, "token")["configured"])
        self.assertEqual(fb_station.credentials(), (fb_station.PAGE_ID, "token"))
        self.assertEqual(fb_station.ENV_FILE.stat().st_mode & 0o777, 0o600)

    def test_same_named_other_page_is_refused(self):
        with patch.object(fb_station, "_lookup", return_value={"id": "1000000000000002", "name": "Station Elsewhere"}):
            with self.assertRaises(shared_features.facebook.FacebookError):
                fb_station.connect("1000000000000002", "token")
        self.assertFalse(fb_station.ENV_FILE.exists())


if __name__ == "__main__":
    unittest.main()
