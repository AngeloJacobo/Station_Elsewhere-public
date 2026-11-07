"""File dialogs (Open / Save / Choose folder).

On a PC with a desktop (Windows, or Linux with a display) the native dialog opens; each runs in a
short-lived subprocess so Tk never touches the web server's threads.

Without a desktop (GitHub Codespaces, a remote server) the dialog is shown in the browser instead:
ask() waits while the page picks a file (it polls /api/dialog/pending and answers with
/api/dialog/answer). Set STORYCRAFT_DIALOGS=web or =native to force one kind.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import uuid

_SCRIPT = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
args = json.loads(sys.argv[1])
root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); root.update()
kind = args.pop('kind')
fn = {'folder': filedialog.askdirectory, 'save': filedialog.asksaveasfilename,
      'open': filedialog.askopenfilename}[kind]
if 'filetypes' in args: args['filetypes'] = [tuple(x) for x in args['filetypes']]
result = fn(parent=root, **args)
root.destroy()
print(json.dumps(result or ''))
"""

WEB_TIMEOUT = 30 * 60          # a browser dialog nobody answers gives up after 30 minutes


def web_mode():
    """True when dialogs must be shown in the browser (no desktop to show a native window on)."""
    forced = os.environ.get("STORYCRAFT_DIALOGS", "").strip().lower()
    if forced in ("web", "native"):
        return forced == "web"
    if os.environ.get("CODESPACES") == "true":
        return True
    if os.name == "nt" or sys.platform == "darwin":
        return False
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return not (has_display and importlib.util.find_spec("tkinter") is not None)


def ask(kind, **options):
    """kind: 'folder' | 'save' | 'open'. Returns the chosen path or '' if cancelled."""
    if web_mode():
        return _ask_web(kind, **options)
    proc = subprocess.run([sys.executable, "-c", _SCRIPT, json.dumps({"kind": kind, **options})],
                          capture_output=True, text=True, timeout=600,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""


# ---- browser dialogs -------------------------------------------------------------------------------
_pending = {}
_lock = threading.Lock()


def _ask_web(kind, **options):
    entry = {"request": {"id": uuid.uuid4().hex, "kind": kind, "title": options.get("title") or "",
                         "initialdir": options.get("initialdir") or "", "initialfile": options.get("initialfile") or "",
                         "defaultextension": options.get("defaultextension") or "",
                         "filetypes": [list(x) for x in options.get("filetypes") or []]},
             "event": threading.Event(), "answer": ""}
    with _lock:
        for old in _pending.values():       # only one dialog at a time: an abandoned one is cancelled
            old["event"].set()
        _pending[entry["request"]["id"]] = entry
    try:
        entry["event"].wait(WEB_TIMEOUT)
        return entry["answer"] or ""
    finally:
        with _lock:
            _pending.pop(entry["request"]["id"], None)


def pending():
    """The dialog the page should show now, or None."""
    with _lock:
        for entry in _pending.values():
            if not entry["event"].is_set():
                return entry["request"]
    return None


def answer(request_id, path):
    with _lock:
        entry = _pending.get(request_id)
        if entry:
            entry["answer"] = path or ""
            entry["event"].set()
    return bool(entry)
