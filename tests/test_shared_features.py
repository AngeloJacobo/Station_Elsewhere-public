"""Temporary data and mocked services only: no uploads, API spending or real cleanup."""
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import main, config, shared_features


@unittest.skipUnless(shared_features.facebook and shared_features.storage, "Requires sibling StoryCraft")
class SharedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.station = self.root / "Station_Elsewhere"
        self.story = self.root / "StoryCraft"
        self.station.mkdir()
        self.story.mkdir()
        self.exports = self.station / "exports"
        self.exports.mkdir()
        self.storage = shared_features.storage.Storage(self.station)
        for item in (
            patch.object(main, "storage", self.storage),
            patch.object(main, "EXPORTS_DIR", self.exports),
            patch.object(main, "jobs", {}),
            patch.object(main, "image_jobs", {}),
            patch.object(main, "auto_stop", Mock()),
            patch.object(main.facebook, "jobs", {}),
            patch.object(main.fb_station, "require_station_page", lambda: {"id": "1", "name": "Station Elsewhere"}),
            patch.object(main.fb_station, "LOG_FILE", self.root / "facebook_posts.json"),
        ):
            item.start()
            self.addCleanup(item.stop)

    def file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
        os.utime(path, (time.time() - 3600,) * 2)
        return path

    def test_facebook_is_bound_to_station_config_not_storycraft(self):
        self.assertIs(main.facebook.CONFIG, config.CONFIG)
        self.assertNotIn("StoryCraft", str(main.storage.root))

    def test_cleanup_is_restricted_to_station_data(self):
        station_file = self.file(self.station / "media/unused.png")
        story_file = self.file(self.story / "media/unused.png")
        preview = main.storage_preview({"current": {}})
        result = main.storage_delete({"token": preview["token"], "categories": ["media"], "current": {}})
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(station_file.exists())
        self.assertTrue(story_file.exists())

    def test_saved_sample_and_current_images_are_protected(self):
        for name in ("sample", "current", "unused"):
            self.file(self.station / f"media/{name}.png")
        project = self.station / "projects/first_project.json"
        project.parent.mkdir()
        project.write_text(json.dumps({"image": str(self.station / "media/sample.png")}))
        preview = main.storage_preview({"current": {"image": str(self.station / "media/current.png")}})
        media = next(category for category in preview["categories"] if category["id"] == "media")
        self.assertEqual(media["count"], 1)

    def test_facebook_upload_blocks_cleanup_and_counts_as_shared_progress(self):
        main.facebook.jobs["upload"] = SimpleNamespace(id="upload", state="running", progress=.4, error=None)
        for endpoint in (main.storage_preview, main.storage_delete):
            with self.assertRaises(main.HTTPException) as error:
                endpoint({})
            self.assertEqual(error.exception.status_code, 409)
        watched = main.monitored_jobs()[0]
        self.assertEqual((watched.kind, watched.id, watched.progress), ("facebook", "facebook-upload", .4))

    def test_facebook_rejects_other_apps_videos_and_unfinished_exports(self):
        own = self.file(self.exports / "finished.mp4")
        other = self.file(self.story / "exports/finished.mp4")
        partial = self.file(self.exports / "unfinished.partial.mp4")
        with patch.object(main.facebook, "start_post") as post:
            post.return_value = SimpleNamespace(to_dict=lambda: {"id": "test"})
            self.assertEqual(main.facebook_post({"path": str(own)}), {"id": "test"})
            for path in (other, partial):
                with self.assertRaises(main.HTTPException):
                    main.facebook_post({"path": str(path)})
            post.assert_called_once()

    def test_missing_helpers_return_explicit_errors(self):
        with patch.object(main, "facebook", None), patch.object(main, "storage", None):
            for endpoint in (main.facebook_status, main.facebook_videos):
                with self.assertRaises(main.HTTPException) as error:
                    endpoint()
                self.assertEqual(error.exception.status_code, 503)
            with self.assertRaises(main.HTTPException) as error:
                main.storage_preview({})
            self.assertEqual(error.exception.status_code, 503)

    def test_invalid_schedule_is_rejected_before_network_call(self):
        video = self.file(self.exports / "finished.mp4")
        with patch.object(main.facebook, "start_post") as post:
            for when in (True, "tomorrow", 0, -1):
                with self.assertRaises(main.HTTPException):
                    main.facebook_post({"path": str(video), "schedule_at": when})
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
