"""Offline tests for the sibling-monitor integration, using temporary projects."""
import importlib.util
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import main, render, codespace_monitor

SOURCE = Path(__file__).resolve().parents[2] / "StoryCraft/app/disk_autosave.py"


@unittest.skipUnless(SOURCE.is_file(), "Shared monitor requires the sibling checkout")
class CodespaceMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("test_disk_autosave", SOURCE)
        self.saves = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.saves)
        self.monitor = SimpleNamespace(lock=threading.RLock(), status=lambda: {"stopping": False},
                                       checkpoint=Mock(), activity=Mock())
        for item in (patch.object(main, "auto_stop", self.monitor),
                     patch.object(main, "autosave_clients", {}),
                     patch.object(main, "PROJECTS_DIR", self.root),
                     patch.object(codespace_monitor, "available", True),
                     patch.object(codespace_monitor, "saves", self.saves, create=True),
                     patch.object(main.dialogs, "ask", side_effect=AssertionError("No desktop dialogs"))):
            item.start()
            self.addCleanup(item.stop)
        self.project = {"name": "Offline test", "scenes": [{"id": "s1", "script": "Hello"}], "settings": {}}

    def save(self, **overrides):
        return main.autosave_project({"client": "tab", "project_id": "one",
                                      "project": self.project, **overrides})

    def test_new_project_autosaves_atomically_and_heartbeat_never_counts_as_progress(self):
        first = self.save()
        self.assertEqual(json.loads(Path(first["path"]).read_text())["scenes"][0]["script"], "Hello")
        with patch.object(main, "save_project", side_effect=AssertionError("No repeated writes")):
            self.save(path=first["path"])
        self.monitor.activity.assert_not_called()
        self.monitor.checkpoint.assert_called_with("tab", True, confirmed=False)

    def test_edits_reuse_path_and_confirm_shutdown(self):
        first = self.save()
        self.project["scenes"][0]["script"] = "Edited"
        second = self.save(path=first["path"], confirm_shutdown=True)
        self.assertEqual(first["path"], second["path"])
        self.assertNotEqual(first["revision"], second["revision"])
        self.monitor.checkpoint.assert_called_with("tab", True, confirmed=True)

    def test_failed_write_preserves_old_file_and_blocks_shutdown(self):
        first = self.save()
        before = Path(first["path"]).read_bytes()
        self.project["scenes"][0]["script"] = "Changed"
        with patch.object(main.os, "fsync", side_effect=OSError("Disk full")):
            with self.assertRaises(main.HTTPException):
                self.save(path=first["path"])
        self.assertEqual(Path(first["path"]).read_bytes(), before)
        self.monitor.checkpoint.assert_called_with("tab", False)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_stale_browser_cannot_overwrite_a_newer_project_after_restart(self):
        first = self.save()
        main.autosave_clients.clear()
        self.project["scenes"][0]["script"] = "Stale browser"
        with self.assertRaises(main.HTTPException) as error:
            self.save(path=first["path"], revision=None)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(json.loads(Path(first["path"]).read_text())["scenes"][0]["script"], "Hello")

    def test_only_increasing_encoded_time_counts_as_ffmpeg_progress(self):
        job = render.Job("export")
        proc = SimpleNamespace(stdout=["out_time_us=N/A\n", "out_time_us=0\n", "out_time_us=10\n",
                                       "out_time_us=10\n", "out_time_us=9\n", "out_time_us=20\n"],
                               stderr=StringIO(""), wait=lambda: None, returncode=0)
        with patch.object(render.subprocess, "Popen", return_value=proc):
            render.run_ffmpeg([], job)
        self.assertEqual(job.work_units, 2)


if __name__ == "__main__":
    unittest.main()
