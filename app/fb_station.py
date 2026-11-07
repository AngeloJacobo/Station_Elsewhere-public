"""Station Elsewhere's own Facebook Page, on top of the shared Lost NEET posting helper.

- Credentials come ONLY from ~/.config/station-elsewhere/facebook.env (never from the environment, which
  may hold another app's page token), and every post first checks that the token's page is named
  "Station Elsewhere" (config.json "facebook_page_name") and, if config.json sets "facebook_page_id",
  that it has that ID.
- Schedule: two reels a day at 3:00 and 9:00 Philippine time. The next free slot follows the latest post
  known from this app's log (facebook_posts.json) and from the Page itself (scheduled + published videos).
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import CONFIG, ROOT

ENV_FILE = Path.home() / ".config" / "station-elsewhere" / "facebook.env"
LOG_FILE = ROOT / "facebook_posts.json"
PAGE_NAME = CONFIG.get("facebook_page_name") or "Station Elsewhere"
# Optional: lock posting to one page ID (useful when two of your pages share a name).
PAGE_ID = str(CONFIG.get("facebook_page_id") or "")
TZ = timezone(timedelta(hours=8), "PHT")          # Asia/Manila has no daylight saving time
SLOT_HOURS = (3, 9)
LEAD_SECONDS = 20 * 60          # Facebook needs 10 minutes; the rest covers the upload itself

fb = None                       # the shared facebook module, bound in bind()
_lock = threading.Lock()
_page_cache = {"at": 0.0, "key": None, "value": None}
_remote_cache = {"at": 0.0, "value": []}


def bind(module):
    """Point the shared helper at Station Elsewhere's own credentials."""
    global fb
    fb = module
    if module is not None:
        module.credentials = credentials
        module.ENV_FILE = ENV_FILE
    return module


def credentials():
    values = fb._env_file_values(ENV_FILE) if fb else {}
    return values.get("FB_PAGE_ID", "").strip(), values.get("FB_PAGE_ACCESS_TOKEN", "").strip()


def _lookup(page_id, token):
    """{id, name} of the page a token belongs to (a read-only Graph request)."""
    url = (f"https://graph.facebook.com/{fb.GRAPH_VERSION}/{urllib.parse.quote(page_id)}?"
           + urllib.parse.urlencode({"fields": "id,name", "access_token": token}))
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise fb.FacebookError(fb._graph_error(error.read(), error.code)) from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise fb.FacebookError(f"Could not reach Facebook: {error}") from None


def _same_name(name):
    return " ".join(str(name or "").split()).casefold() == PAGE_NAME.casefold()


def _is_station(info):
    return (not PAGE_ID or str(info.get("id")) == PAGE_ID) and _same_name(info.get("name"))


def _expected():
    return f"“{PAGE_NAME}”" + (f" (ID {PAGE_ID})" if PAGE_ID else "")


def _which(info):
    return f"“{info.get('name')}” (ID {info.get('id')})"


def page_info(force=False):
    page, token = credentials()
    if not page or not token:
        raise fb.FacebookError(f"Connect the {PAGE_NAME} Facebook Page first.")
    key = (page, token)
    if not force and _page_cache["key"] == key and time.time() - _page_cache["at"] < 600:
        return _page_cache["value"]
    info = _lookup(page, token)
    _page_cache.update(at=time.time(), key=key, value=info)
    return info


def require_station_page():
    info = page_info()
    if not _is_station(info):
        raise fb.FacebookError(f"The saved Facebook token belongs to the page {_which(info)}, not "
                               f"{_expected()}. Nothing was posted. Connect the {PAGE_NAME} page.")
    return info


def status():
    page, token = credentials()
    if not page or not token:
        return {"configured": False, "page": None, "expected": PAGE_NAME,
                "message": f"Not connected. Paste the {PAGE_NAME} Page ID and Page access token below."}
    try:
        info = page_info()
    except fb.FacebookError as error:
        return {"configured": False, "page": None, "expected": PAGE_NAME,
                "message": f"The saved {PAGE_NAME} token does not work: {error}"}
    if not _is_station(info):
        return {"configured": False, "page": info.get("name"), "expected": PAGE_NAME,
                "message": f"The saved token is for {_which(info)}, not {_expected()}. Connect the right page."}
    return {"configured": True, "page": info.get("name"), "expected": PAGE_NAME,
            "message": f"Posting to the Facebook Page “{info.get('name')}” (ID {info.get('id')})."}


def connect(page_id, token):
    """Check that the token belongs to the Station Elsewhere page, then save it (private to this machine)."""
    page_id, token = str(page_id or "").strip(), str(token or "").strip()
    if not page_id or not token:
        raise fb.FacebookError("Enter both the Page ID and the Page access token.")
    info = _lookup(page_id, token)
    if not _is_station(info):
        raise fb.FacebookError(f"That token is for the page {_which(info)}, not {_expected()}. It was not saved.")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENV_FILE.with_name(f".{ENV_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(f"# {PAGE_NAME} Facebook Page (private: never commit)\nFB_PAGE_ID={info.get('id') or page_id}\n"
                       f"FB_PAGE_ACCESS_TOKEN={token}\n")
        tmp.replace(ENV_FILE)
    finally:
        tmp.unlink(missing_ok=True)
    _page_cache.update(at=0.0, key=None, value=None)
    _remote_cache.update(at=0.0, value=[])
    return status()


# ---- posting log -----------------------------------------------------------------------------------
def read_log():
    try:
        data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record(entry):
    with _lock:
        log = read_log()
        log.append(entry)
        tmp = LOG_FILE.with_name(f".{LOG_FILE.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(LOG_FILE)
        finally:
            tmp.unlink(missing_ok=True)


def page_log():
    """Posts made to the current Station Elsewhere page (entries for another page are kept but ignored)."""
    page = PAGE_ID or credentials()[0]
    return [e for e in read_log() if str(e.get("page_id") or "") == page]


def posted_names():
    return {e.get("video") for e in page_log() if e.get("video")}


def _remote_times():
    """Times of the Page's scheduled posts and recently published videos (best effort, cached briefly)."""
    if time.time() - _remote_cache["at"] < 120:
        return _remote_cache["value"]
    times = []
    try:
        page = require_station_page()["id"]
        for item in fb.graph("GET", f"{page}/scheduled_posts", {"fields": "scheduled_publish_time", "limit": 100}).get("data", []):
            if item.get("scheduled_publish_time"):
                times.append(int(item["scheduled_publish_time"]))
        for item in fb.graph("GET", f"{page}/videos", {"fields": "created_time", "limit": 25}).get("data", []):
            if item.get("created_time"):
                times.append(int(datetime.strptime(item["created_time"], "%Y-%m-%dT%H:%M:%S%z").timestamp()))
    except Exception:           # offline / not connected: the local log still works
        return []
    _remote_cache.update(at=time.time(), value=times)
    return times


def last_post_time(remote=True):
    times = [int(e.get("time") or 0) for e in page_log()]
    if remote:
        times += _remote_times()
    times = [t for t in times if t > 0]
    return max(times) if times else None


def next_slots(count, last=None, now=None):
    """The next `count` 3:00 / 9:00 PHT slots after the latest post, never sooner than LEAD_SECONDS from now."""
    now = time.time() if now is None else now
    earliest = now + LEAD_SECONDS
    after = last or 0
    day = datetime.fromtimestamp(max(after, now), TZ).date() - timedelta(days=1)
    slots = []
    while len(slots) < count:
        for hour in SLOT_HOURS:
            t = int(datetime(day.year, day.month, day.day, hour, tzinfo=TZ).timestamp())
            if t > after and t >= earliest and len(slots) < count:
                slots.append(t)
        day += timedelta(days=1)
    return slots


def label(t):
    if not t:
        return ""
    d = datetime.fromtimestamp(t, TZ)
    return f"{d:%a %b} {d.day}, {d.year} {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'} PHT"


def watch(job, video, title, schedule_at, on_done=None):
    """Record a post in the log once Facebook has accepted the upload."""
    page_id = credentials()[0]

    def run():
        while getattr(job, "state", None) == "running":
            time.sleep(2)
        if getattr(job, "state", None) != "done":
            return
        when = int(schedule_at) if schedule_at else int(time.time())
        record({"video": Path(video).name, "title": title, "time": when, "scheduled": bool(schedule_at),
                "video_id": job.video_id, "posted_at": int(time.time()), "page_id": page_id})
        _remote_cache.update(at=0.0, value=[])
        if on_done:
            on_done(when)
    threading.Thread(target=run, daemon=True).start()
