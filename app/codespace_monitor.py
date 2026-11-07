"""Reuse the sibling Lost NEET watchdog when both apps share a Codespace."""
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

from .config import ROOT, CONFIG

SOURCE = ROOT.parent / "StoryCraft" / "app"
available = bool(os.environ.get("CODESPACE_NAME") and (SOURCE / "shared_autostop.py").is_file())

if available:
    spec = importlib.util.spec_from_file_location(
        "storycraft_monitor", SOURCE / "__init__.py", submodule_search_locations=[str(SOURCE)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    watchdog = importlib.import_module("storycraft_monitor.autostop")
    saves = importlib.import_module("storycraft_monitor.disk_autosave")


def create(jobs):
    if not available:
        return None
    config = dict(CONFIG)
    if "codespace_auto_stop" not in config:
        source_config = SOURCE.parent / "config.json"
        config["codespace_auto_stop"] = json.loads(source_config.read_text(encoding="utf-8")).get(
            "codespace_auto_stop", False) if source_config.is_file() else False
    return watchdog.AutoStop(ROOT, config, jobs,
                             shared_path=ROOT.parent / ".runtime" / "codespace-autostop.sqlite3")


def revision(project):
    if available:
        return saves.project_revision(project)
    return None
