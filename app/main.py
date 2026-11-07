"""Station Elsewhere video maker: local web server. Run with `python -m app.main` (or start.bat)."""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import traceback
import uuid
import webbrowser
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import soundfile as sf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import dialogs, fb_station, imagegen, recording, reels, codespace_monitor, shared_features
from . import config as config_module
from .config import (ARTSTYLE_DIR, CONFIG, EXPORTS_DIR, IS_WINDOWS, MEDIA_DIR, MY_DEFAULTS_FILE, PROJECTS_DIR, ROOT,
                     WORKSPACE, fix_project, local_path)
from .render import (AUDIO_DIR, DEFAULT_SETTINGS, PREVIEW_DIR, VOICE_EFFECTS, Job, RenderError,
                     build_timeline, cache_info, cleanup_cache, clear_video_cache, merged_settings,
                     apply_my_defaults, builtin_looks, defaults_from, lut_kind, narrate, render, scene_summary,
                     trim_segments, apply_voice_effect, intro_audio, voice_master_tag, INTRO_SOUNDS)
from .tts import (ELEVEN_MODEL, ELEVEN_MODELS, SAMPLE_RATE, Cancelled, Cartesia, CartesiaError, ElevenLabs,
                  Narrator)

STATIC = Path(__file__).resolve().parent / "static"
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
OVERLAY_TYPES = {".gif", ".png", ".webp"}       # pictures that can go on top of the video (GIFs animate)
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}

@asynccontextmanager
async def lifespan(app):
    if auto_stop:
        auto_stop.start()
    try:
        yield
    finally:
        if auto_stop:
            auto_stop.close()


app = FastAPI(title="Station Elsewhere", lifespan=lifespan)
storage_write_lock = asyncio.Lock()
app.mount("/static", StaticFiles(directory=STATIC), name="static")
APP_DIR = Path(__file__).resolve().parent


def _code_mtime():
    return max(p.stat().st_mtime for p in APP_DIR.glob("*.py"))


STARTED_CODE_MTIME = _code_mtime()


@app.middleware("http")
async def no_stale_static(request, call_next):
    # Always revalidate page files so the browser never mixes old and new versions.
    if auto_stop and request.method == "POST":
        async with storage_write_lock:
            if auto_stop.status()["stopping"] and request.url.path != "/api/autostop":
                return JSONResponse(status_code=409, content={"detail": "Codespace shutdown has started."})
            response = await call_next(request)
    else:
        response = await call_next(request)
    if request.url.path.startswith(("/static/", "/api/audio/")):     # audio files are rewritten in place
        response.headers["Cache-Control"] = "no-cache"
    return response
def make_narrator():
    n = Narrator(CONFIG["kokoro_model"], CONFIG["kokoro_voices"], WORKSPACE / "cache" / "tts",
                 torch_dir=CONFIG.get("kokoro_torch_dir", ""), engine=CONFIG.get("kokoro_engine", "auto"))
    if CONFIG.get("cartesia_api_key"):       # API keys live in config.json: per PC, never shared or committed
        n.cartesia = Cartesia(CONFIG["cartesia_api_key"])
    ElevenLabs.model = CONFIG.get("elevenlabs_model") if CONFIG.get("elevenlabs_model") in ELEVEN_MODELS else ELEVEN_MODEL
    if CONFIG.get("elevenlabs_api_key"):
        n.eleven = ElevenLabs(CONFIG["elevenlabs_api_key"])
    return n


narrator = make_narrator()
jobs = {}
current = {"job": None}
def monitored_jobs():
    result = [*jobs.values(), *image_jobs.values(), *reel_jobs.values()]
    if shared_features.facebook:
        result.extend(SimpleNamespace(id=f"facebook-{job.id}", kind="facebook", state=job.state,
                                      progress=job.progress, result=None, error=job.error)
                      for job in shared_features.facebook.jobs.values())
    return result


auto_stop = codespace_monitor.create(monitored_jobs)
autosave_clients = {}
facebook = fb_station.bind(shared_features.facebook)     # Station Elsewhere's own page only
storage = shared_features.storage.Storage(ROOT) if shared_features.storage else None


@app.get("/api/autostop")
def autostop_status():
    return auto_stop.status() if auto_stop else {"available": False}


@app.post("/api/autostop")
def autostop_configure(body: dict):
    if not auto_stop:
        raise HTTPException(400, "Shared auto-stop requires the sibling Lost NEET checkout in a Codespace.")
    try:
        auto_stop.configure(body.get("enabled"))
    except codespace_monitor.watchdog.AutoStopError as error:
        raise HTTPException(400, str(error)) from error
    return auto_stop.status()


@app.post("/api/autostop/activity")
def autostop_activity():
    if auto_stop:
        auto_stop.activity("Project edited in Station Elsewhere.")
    return {"ok": True}


@app.post("/api/project/autosave")
def autosave_project(body: dict):
    if not auto_stop:
        raise HTTPException(400, "Disk checkpoints require the shared Codespace monitor.")
    return codespace_monitor.saves.autosave(body, auto_stop, autosave_clients, save_project)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/instance")
def instance():
    """Lets a second start.bat tell this copy apart from other apps (e.g. Mr Sidequests) on the port."""
    return {"app": "Station Elsewhere", "root": str(ROOT)}


@app.get("/api/status")
def status(refresh_voices: bool = False):
    """refresh_voices (sent once when the page opens) re-reads the online voice lists, so a new
    clone shows up after reloading the page."""
    if refresh_voices:
        for client in (narrator.cartesia, narrator.eleven):
            if client:
                client.voices(refresh=True)
    voices = narrator.voices()
    defaults = json.loads(json.dumps(DEFAULT_SETTINGS))
    kokoro_count = len(voices)
    clouds = {}
    for name, client in (("cartesia", narrator.cartesia), ("elevenlabs", narrator.eleven)):
        clouds[name] = {"configured": bool(client), "error": None, "count": 0}
        if client:
            listed = client.voices()
            clouds[name].update(error=client.error, count=len(listed))
            voices = voices + listed
    clouds["elevenlabs"].update(model=ElevenLabs.model, models=ELEVEN_MODELS)
    # The default voice may be an online one (e.g. your ElevenLabs voice); only replace it if it's gone.
    if voices and defaults["voice"]["voice"] not in {v["id"] for v in voices}:
        defaults["voice"]["voice"] = voices[0]["id"]
    return {"kokoro_error": narrator.check(), "kokoro_engine": narrator.engine_label,
            "ffmpeg": CONFIG.get("ffmpeg") or None, **clouds,
            "kokoro_voice_count": kokoro_count,
            "voices": voices, "defaults": defaults,
            "voice_effects": {k: v[0] for k, v in VOICE_EFFECTS.items()},
            "intro_sounds": [{"file": str(f), "name": name, "length": length, "voice_at": at}
                             for f, name, length, at in INTRO_SOUNDS if f.is_file()],
            "looks": builtin_looks(),
            "fonts": list_fonts(),
            "projects_dir": str(PROJECTS_DIR), "config_file": str(ROOT / "config.json"),
            "web_dialogs": dialogs.web_mode(), "windows": IS_WINDOWS, "start_project": _start_project(),
            "facebook_posting": facebook is not None, "storage_cleanup": storage is not None,
            "restart_needed": _code_mtime() > STARTED_CODE_MTIME}


def _start_project():
    """STORYCRAFT_START_PROJECT (set by start.sh): the project opened when the browser has no autosaved work."""
    name = os.environ.get("STORYCRAFT_START_PROJECT", "").strip()
    if not name:
        return None
    p = Path(name) if Path(name).is_absolute() else ROOT / name
    return str(p) if p.is_file() else None


def list_fonts():
    if not IS_WINDOWS:
        return _list_fonts_fontconfig()
    import winreg
    names = set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[1]):
            name = winreg.EnumValue(key, i)[0]
            name = re.sub(r"\s*\((TrueType|OpenType|All res)\)$", "", name)
            for part in name.split(" & "):
                part = re.sub(r"\s+(Bold|Italic|Bold Italic|Regular|Light|Semibold|SemiBold)$", "", part)
                if part and not part.startswith("@"):
                    names.add(part.strip())
    return sorted(names, key=str.lower)


_fonts_cache = []


def _list_fonts_fontconfig():
    """Linux / macOS: font families known to fontconfig (incl. fonts/), plus the Windows names that
    fonts/ has stand-ins for (so projects made on Windows show their font)."""
    if _fonts_cache:
        return _fonts_cache
    names = {"Segoe UI", "Segoe UI Light", "Segoe UI Semilight", "Segoe UI Semibold", "Consolas", "Arial"}
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            for part in line.split(","):
                if part.strip() and not part.strip().startswith("."):
                    names.add(part.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    _fonts_cache[:] = sorted(names, key=str.lower)
    return _fonts_cache


# ---- preview fonts ---------------------------------------------------------------------------
# On Linux FFmpeg/libass draws text with the server's fonts (or stand-ins like Consolas -> Inconsolata),
# while the browser would use the viewer's own fonts. /api/fonts.css serves the server's font files
# under the same names, so the preview shows exactly what the video will.
FONT_ALIASES = ("Segoe UI", "Segoe UI Light", "Segoe UI Semilight", "Segoe UI Semibold", "Consolas", "Arial",
                "Arial Black", "Calibri", "Times New Roman", "Courier New")
FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".ttc": "font/collection", ".woff": "font/woff",
              ".woff2": "font/woff2"}
_font_faces_cache = {}


def _css_weight(fc_weight):
    """fontconfig weight -> CSS font-weight."""
    table = ((0, 100), (40, 200), (50, 300), (55, 350), (75, 380), (80, 400), (100, 500), (180, 600),
             (200, 700), (205, 800), (210, 900))
    return min(table, key=lambda t: abs(t[0] - fc_weight))[1]


def _font_faces():
    """{"css": ..., "files": {key: path}} for every font family FFmpeg can use here (cached)."""
    if "css" in _font_faces_cache:
        return _font_faces_cache
    files, faces = {}, {}

    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    info = {}                                           # file -> (css weight, css style)
    real = set()
    for line in run(["fc-list", "--format", "%{family}\t%{weight}\t%{slant}\t%{file}\n"]).splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or Path(parts[3]).suffix.lower() not in FONT_TYPES:
            continue
        family, weight, slant, path = parts
        try:
            desc = (_css_weight(float(weight.split()[0].strip("[]"))), "normal" if int(float(slant)) == 0 else "italic")
        except ValueError:
            continue
        info.setdefault(path, desc)
        for name in family.split(","):
            name = name.strip()
            if name and not name.startswith("."):
                real.add(name)
                faces.setdefault((name, *desc), path)
    for name in FONT_ALIASES:
        if name in real:                                # the real font is installed (e.g. copied into fonts/)
            continue
        for variant in ("", ":bold", ":italic", ":bold:italic"):
            path = run(["fc-match", "--format", "%{file}", name + variant]).strip()
            if path in info:                            # declared with the file's real weight/style, so the
                faces.setdefault((name, *info[path]), path)  # browser fakes bold/italic just like libass
    rules = []
    for (name, weight, style), path in sorted(faces.items()):
        key = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
        files[key] = path
        family = name.replace("\\", "\\\\").replace('"', '\\"')
        rules.append(f'@font-face {{ font-family: "{family}"; src: url("/api/font-file/{key}"); '
                     f"font-weight: {weight}; font-style: {style}; font-display: block; }}")
    _font_faces_cache.update(css="\n".join(rules) + "\n", files=files)
    return _font_faces_cache


@app.get("/api/fonts.css")
def fonts_css():
    css = "" if IS_WINDOWS else _font_faces()["css"]    # on Windows the browser already has the same fonts
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/api/font-file/{key}")
def font_file(key: str):
    path = None if IS_WINDOWS else _font_faces()["files"].get(key)
    if not path or not Path(path).is_file():
        raise HTTPException(404, "Font not found.")
    return FileResponse(path, media_type=FONT_TYPES[Path(path).suffix.lower()],
                        headers={"Cache-Control": "max-age=86400"})


# ---- images ----------------------------------------------------------------------------------
@app.post("/api/upload")
async def upload(files: list[UploadFile]):
    """Store a copy of each uploaded image in media/ (content-addressed). Originals are untouched."""
    saved = []
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in IMAGE_TYPES:
            raise HTTPException(400, f"{f.filename}: only PNG, JPG and WebP images are supported.")
        data = await f.read()
        digest = hashlib.sha1(data).hexdigest()[:16]
        safe = re.sub(r"[^\w.\- ]", "_", Path(f.filename).name)
        target = MEDIA_DIR / f"{digest}_{safe}"
        if not target.exists():
            target.write_bytes(data)
        saved.append({"path": str(target), "name": Path(f.filename).name})
    return saved


@app.get("/api/image")
def image(path: str):
    p = Path(local_path(path))
    if p.suffix.lower() not in IMAGE_TYPES | OVERLAY_TYPES or not p.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(p)


@app.post("/api/check_images")
def check_images(body: dict):
    """Missing images, plus automatic fixes for images that are in this app's media folder
    (e.g. after the app folder was copied to another PC or renamed)."""
    missing, relinked = [], {}
    for p in body.get("paths", []):
        if p and Path(p).is_file():
            continue
        fixed = relink(p)
        if fixed:
            relinked[p] = fixed
        else:
            missing.append(p)
    return {"missing": missing, "relinked": relinked}


def relink(path):
    """Find a moved image: the same file in this Station Elsewhere folder (project made on another PC / in a
    copy of the folder), or the same file name inside this app's media/ folder."""
    if not path:
        return None
    mapped = local_path(path)
    if mapped != path and Path(mapped).is_file():
        return mapped
    from pathlib import PureWindowsPath
    candidate = MEDIA_DIR / PureWindowsPath(path).name
    return str(candidate) if candidate.is_file() else None


# ---- narration -------------------------------------------------------------------------------
def _require_voice(voice):
    """The chosen voice's engine must be ready: local Kokoro, or Cartesia with a working API key.
    An online voice without its API key is allowed: narration that is already cached (workspace/cache/tts)
    is still used, and only a sentence that was never spoken asks for the key."""
    from .tts import is_cartesia, is_eleven
    if (is_cartesia(voice) and not narrator.cartesia) or (is_eleven(voice) and not narrator.eleven):
        return
    problem = narrator.check(voice)
    if problem:
        raise HTTPException(500, problem)


def _project_voice(project):
    return merged_settings(project)["voice"]["voice"]


def _require_project_voice(project):
    if not recording.of(project):      # your own recording needs no voice engine
        _require_voice(_project_voice(project))


CLOUD_VOICES = {"cartesia": ("cartesia_api_key", "cartesia", Cartesia),
                "elevenlabs": ("elevenlabs_api_key", "eleven", ElevenLabs)}


@app.post("/api/elevenlabs/model")
def elevenlabs_model(body: dict):
    model = body.get("model")
    if model not in ELEVEN_MODELS:
        raise HTTPException(400, "Unknown ElevenLabs model")
    ElevenLabs.model = CONFIG["elevenlabs_model"] = model
    config_module.save(CONFIG)
    return {"ok": True}


@app.post("/api/{provider}/key")
def cloud_voice_key(provider: str, body: dict):
    """Save (or remove, with an empty key) a Cartesia / ElevenLabs API key after checking that it works."""
    if provider not in CLOUD_VOICES:
        raise HTTPException(404, "Unknown voice service")
    config_key, attr, cls = CLOUD_VOICES[provider]
    key = (body.get("key") or "").strip()
    if key:
        trial = cls(key)
        problem = trial.check()
        if problem:
            raise HTTPException(400, problem)
        CONFIG[config_key] = key
        config_module.ENV_KEYS.discard(config_key)       # typed in the app: remembered in config.json
        setattr(narrator, attr, trial)
    else:
        CONFIG.pop(config_key, None)
        config_module.ENV_KEYS.discard(config_key)
        setattr(narrator, attr, None)
    config_module.save(CONFIG)
    client = getattr(narrator, attr)
    return {"ok": True, "count": len(client.voices()) if client else 0}


@app.post("/api/scene_audio")
def scene_audio(body: dict):
    """Narrate one scene (cached) so it can be played on its own."""
    if auto_stop:
        auto_stop.start_work()
    project, index = fix_project(body["project"]), int(body["index"])
    _require_project_voice(project)
    scene = project["scenes"][index]
    if recording.of(project):      # a scene's stretch depends on its neighbours: cut them all (fast)
        try:
            return scene_summary(build_timeline(project, narrate(project, narrator, Job("scene"))))[index]
        except (RenderError, recording.RecordingError) as e:
            raise HTTPException(400, str(e))
    if not (scene.get("script") or "").strip():
        raise HTTPException(400, "This scene has no narration script yet.")
    single = {**project, "scenes": [scene]}
    if index > 0:      # the intro sound only comes before the first scene
        given = project.get("settings") or {}
        single["settings"] = {**given, "intro": {**(given.get("intro") or {}), "enabled": False}}
    try:
        item = narrate(project, narrator, Job("scene"), only=index)[0]    # knows its neighbours (ElevenLabs context)
    except (RenderError, CartesiaError) as e:
        raise HTTPException(400, str(e))
    tl = build_timeline(single, [item])
    return scene_summary(tl)[0]


@app.post("/api/test_voice")
def test_voice(body: dict):
    if auto_stop:
        auto_stop.start_work()
    voice, speed = body.get("voice"), float(body.get("speed", 1.0))
    _require_voice(voice)
    text = (body.get("text") or "").strip() or \
        "At 8:43 PM, the world was about to end. Unfortunately, you had absolutely no idea."
    try:
        audio = narrator.scene(text, voice, speed, style=body.get("style", "story"))[0]
    except CartesiaError as e:
        raise HTTPException(400, str(e))
    model = ElevenLabs.model if str(voice).startswith("eleven:") else ""
    name = "test_" + hashlib.sha1(f"{voice}{speed}{body.get('style', 'story')}{model}{text}".encode()).hexdigest()[:16] + ".wav"
    sf.write(str(AUDIO_DIR / name), audio, SAMPLE_RATE)
    # The effect and the video's loudness are added when it is played (/api/audio, like ▶ Narration).
    return {"audio_url": f"/api/audio/{name}", "duration": round(len(audio) / SAMPLE_RATE, 2)}


@app.get("/api/audio/{name}")
def audio_file(name: str, effect: str = "none", soften_s: bool = False, volume: float = 40):
    """A narration file, with the chosen voice effect and volume (so ▶ Narration sounds like the video)."""
    p = AUDIO_DIR / Path(name).name
    if not p.is_file():
        raise HTTPException(404, "Audio not found")
    fx = {"effect": effect, "soften_s": soften_s, "volume": volume}
    if "_fx_" not in p.stem:      # effect + loudness of the video (an already processed file is sent as it is)
        out = AUDIO_DIR / f"{p.stem}_fx_{voice_master_tag(fx)}.wav"
        if not out.is_file() or out.stat().st_mtime < p.stat().st_mtime:
            apply_voice_effect(p, out, fx)
        p = out
    return FileResponse(p, media_type="audio/wav")


# ---- jobs (narrate / preview / export) -------------------------------------------------------
def _run_job(job, project):
    try:
        if job.kind == "transcribe":
            rec = recording.of(project) or {}
            heard = recording.transcribe(rec.get("file", ""), job)
            parts = recording.make_scenes(heard["words"], heard["duration"])
            job.result = {**heard, "parts": parts}
        elif job.kind == "narrate":
            scenes = project.get("scenes") or []
            if not scenes:
                raise RenderError("Add at least one scene.")
            empty = [str(i) for i, s in enumerate(scenes, 1) if not (s.get("script") or "").strip() and not s.get("image")]
            if empty and not recording.of(project):
                raise RenderError("These scenes are empty (no narration and no image): " + ", ".join(empty))
            tl = build_timeline(project, narrate(project, narrator, job))
            job.result = {"scenes": scene_summary(tl), "duration": round(tl["total"], 3)}
        else:
            preview = job.kind == "preview"
            result = render(project, narrator, job, preview=preview)
            if preview:
                result["url"] = f"/api/preview/{Path(result['path']).name}"
            job.result = result
        job.update("Done", 1.0)
        job.state = "done"
    except Cancelled:
        job.state, job.stage = "cancelled", "Cancelled"
    except (RenderError, CartesiaError, recording.RecordingError) as e:
        job.state, job.error = "error", str(e)
    except Exception as e:  # unexpected: keep the details for the user instead of failing silently
        traceback.print_exc()
        job.state, job.error = "error", f"{type(e).__name__}: {e}"
    finally:
        if job.kind not in ("narrate", "transcribe"):
            trim_segments()


@app.post("/api/jobs")
def start_job(body: dict):
    kind = body.get("kind")
    if kind not in ("narrate", "preview", "export", "transcribe"):
        raise HTTPException(400, "Unknown job")
    fix_project(body["project"])
    _require_project_voice(body["project"])
    running = current["job"]
    if running and running.state == "running":
        raise HTTPException(409, "Another task is still running. Wait for it or cancel it.")
    job = Job(kind)
    jobs[job.id] = job
    current["job"] = job
    if auto_stop:
        auto_stop.start_work()
    threading.Thread(target=_run_job, args=(job, body["project"]), daemon=True).start()
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Unknown job")
    return jobs[job_id].to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if job_id in jobs:
        jobs[job_id].cancel()
    return {"ok": True}


@app.get("/api/cache")
def cache_status():
    return cache_info()


def _storage_idle():
    if any(job.state == "running" for job in [*jobs.values(), *image_jobs.values()]) \
            or facebook and any(job.state == "running" for job in facebook.jobs.values()):
        raise HTTPException(409, "Wait for all narration, rendering, image and Facebook jobs to finish before cleanup.")


@app.post("/api/storage/preview")
def storage_preview(body: dict):
    if storage is None:
        raise HTTPException(503, "Storage cleanup requires the sibling Lost NEET checkout.")
    _storage_idle()
    try:
        return storage.preview(body.get("current"))
    except (OSError, shared_features.storage.StorageError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/storage/delete")
def storage_delete(body: dict):
    if storage is None:
        raise HTTPException(503, "Storage cleanup requires the sibling Lost NEET checkout.")
    _storage_idle()
    try:
        return storage.delete(body.get("token"), body.get("categories"), body.get("current"))
    except (OSError, shared_features.storage.StorageError) as error:
        raise HTTPException(400, str(error)) from error


def _require_facebook():
    if facebook is None:
        raise HTTPException(503, "Facebook posting requires the sibling Lost NEET checkout.")


def _exported_video(path):
    p = Path(str(path or "")).resolve()
    if p.suffix.lower() != ".mp4" or p.name.endswith(".partial.mp4") or not p.is_file() \
            or not p.is_relative_to(EXPORTS_DIR.resolve()):
        raise HTTPException(404, "Exported video not found. Only videos in this app's exports folder can be posted.")
    return p


@app.get("/api/facebook/status")
def facebook_status():
    _require_facebook()
    return fb_station.status()


@app.post("/api/facebook/connect")
def facebook_connect(body: dict):
    _require_facebook()
    try:
        return fb_station.connect(body.get("page_id"), body.get("token"))
    except facebook.FacebookError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/facebook/schedule")
def facebook_schedule(count: int = 1):
    """The next free 3 AM / 9 AM Philippine-time slots after the latest post."""
    _require_facebook()
    count = max(1, min(60, count))
    last = fb_station.last_post_time()
    slots = fb_station.next_slots(count, last)
    return {"last": last, "last_label": fb_station.label(last), "timezone": "Asia/Manila (UTC+8)",
            "slots": [{"time": t, "label": fb_station.label(t)} for t in slots]}


@app.get("/api/facebook/videos")
def facebook_videos():
    _require_facebook()
    files = [p for p in EXPORTS_DIR.glob("*.mp4") if p.is_file() and not p.name.endswith(".partial.mp4")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    posted = fb_station.posted_names()
    return {"videos": [{"path": str(p), "name": p.name, "mb": round(p.stat().st_size / 1048576, 1),
                        "modified": p.stat().st_mtime, "posted": p.name in posted} for p in files]}


@app.post("/api/facebook/post")
def facebook_post(body: dict):
    _require_facebook()
    video = _exported_video(body.get("path"))
    schedule_at = body.get("schedule_at")
    if schedule_at is not None and (type(schedule_at) not in (int, float) or schedule_at <= 0):
        raise HTTPException(400, "Invalid schedule time.")
    try:
        thumb_at = facebook.parse_timestamp(body["thumb_at"]) if str(body.get("thumb_at") or "").strip() else None
        fb_station.require_station_page()           # never post to another page (e.g. Lost NEET's)
        activity = auto_stop.activity if auto_stop else lambda reason: None
        title = str(body.get("title") or "").strip()
        job = facebook.start_post(video, title,
                                  str(body.get("description") or "").strip(), schedule_at, thumb_at,
                                  body.get("remove_captions") is not False, on_activity=activity)
    except facebook.FacebookError as error:
        raise HTTPException(400, str(error)) from error
    reel_id = str(body.get("reel_id") or "")

    def note_in_history(when):
        entry = next((e for e in reels.read_history() if e.get("id") == reel_id), None) if reel_id else None
        if entry:
            what = "scheduled" if schedule_at else "posted"
            base = (entry.get("status") or "").split(" · Facebook:")[0]
            reels.set_status(reel_id, f"{base} · Facebook: {what} {fb_station.label(when)}")
    fb_station.watch(job, video, title, schedule_at, note_in_history)
    if auto_stop:
        auto_stop.start_work()
        auto_stop.activity("Facebook post started.")
    return job.to_dict()


@app.get("/api/facebook/jobs/{job_id}")
def facebook_job(job_id: str):
    _require_facebook()
    job = facebook.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown Facebook post (was the server restarted?)")
    return job.to_dict()


@app.post("/api/cache/clear")
def cache_clear():
    running = current["job"]
    if running and running.state == "running":
        raise HTTPException(409, "Wait for the current task to finish before clearing the cache.")
    clear_video_cache()
    return cache_info()


# ---- series style: characters + art style shared by all projects -----------------------------------
SERIES_FILE = ROOT / "series_style.json"
RECOMMENDED_STYLE = 'Flat 2D webcomic style, like animated storytelling channels on YouTube. Simple shapes and clean dark outlines of even thickness. Flat colors with at most one soft shadow tone; no gradients, textures or glossy highlights. Muted, slightly desaturated natural palette (greys, greens, browns, dusty blues). Plain, even lighting (overcast daylight or ordinary indoor light). Simple backgrounds in the same flat style, everything in focus. Characters are simple cartoon people with round heads, small dot eyes, simple eyebrows and small mouths, wearing plain clothes; emotions are shown with eyebrows, small mouth shapes and body language, never with exaggerated anime faces. Calm, readable poses.'
RECOMMENDED_AVOID = 'realistic humans, photorealism, photographs, 3D render, anime or manga style, painterly or highly detailed rendering, cinematic or dramatic lighting, glowing light sources, light rays, lens flare, bokeh, depth-of-field blur, rain or wet reflections, neon, oversaturated colors, detailed textures, exaggerated anime faces or huge open screaming mouths'
DEFAULT_SERIES = {"style": RECOMMENDED_STYLE, "avoid": RECOMMENDED_AVOID,
                  "style_images": [], "characters": []}


def _fix_paths(paths):
    out = []
    for p in paths or []:
        if p and Path(p).is_file():
            out.append(p)
        elif relink(p):
            out.append(relink(p))
    return out


# ---- art style folder: every image in artstyle/ is a style example; artstyle/rules.txt adds rules ----
ART_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


def _migrate_style_settings():
    """One time: earlier versions kept the art style in settings. Move custom rules into rules.txt and
    old style example images into the folder, so nothing is lost."""
    if not SERIES_FILE.exists():
        return
    try:
        data = json.loads(SERIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("style_migrated"):
        return
    rules = ARTSTYLE_DIR / "rules.txt"
    lines = []
    style = (data.get("style") or "").strip()
    if style and style != RECOMMENDED_STYLE:
        for sentence in re.findall(r"[^.!?]+[.!?]?", style):
            sentence = sentence.strip()
            if sentence and sentence not in RECOMMENDED_STYLE and "follow the style of the character" not in sentence.lower():
                lines.append(sentence)
    known = {x.strip().lower() for x in RECOMMENDED_AVOID.split(",")} | {"detailed realistic faces"}
    extra = [x.strip() for x in re.split(r"[,;]", data.get("avoid") or "") if x.strip() and x.strip().lower() not in known]
    if extra:
        lines.append("Never draw: " + ", ".join(extra) + ".")
    if lines and not rules.exists():
        rules.write_text("# Extra rules added to every image prompt (one per line). Lines starting with # are ignored.\n"
                         + "\n".join(lines) + "\n", encoding="utf-8")
    if not any(p.suffix.lower() in ART_TYPES for p in ARTSTYLE_DIR.iterdir()):
        for p in _fix_paths(data.get("style_images")):
            shutil.copy2(p, ARTSTYLE_DIR / Path(p).name)
    data["style_migrated"] = True
    SERIES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@app.get("/api/artstyle")
def artstyle():
    _migrate_style_settings()
    images = sorted((p for p in ARTSTYLE_DIR.iterdir() if p.is_file() and p.suffix.lower() in ART_TYPES),
                    key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)])
    rules = _art_lines("rules.txt")
    style, avoid = " ".join(_art_lines("style.txt")), ", ".join(_art_lines("avoid.txt"))
    return {"folder": str(ARTSTYLE_DIR), "images": [str(p) for p in images], "rules": rules,
            "style": style or RECOMMENDED_STYLE, "avoid": avoid or RECOMMENDED_AVOID, "custom_style": bool(style),
            "last_scene": _art_lines("last_scene.txt"), "places": _art_lines("places.txt")}


def _art_lines(name):
    """Non-empty lines of a text file in artstyle/, without # comments. style.txt (the look) and avoid.txt
    (things never to draw) replace the built-in style when present; rules.txt adds rules; last_scene.txt adds rules
    for the final scene only; places.txt lists places a scene can be set in."""
    path = ARTSTYLE_DIR / name
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return [l.strip().rstrip(",") for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


@app.post("/api/artstyle/open")
def artstyle_open():
    if not IS_WINDOWS:         # no file manager here (e.g. Codespaces): the page shows the folder instead
        return {"ok": False, "folder": str(ARTSTYLE_DIR)}
    os.startfile(str(ARTSTYLE_DIR))
    return {"ok": True}


@app.get("/api/series_style/recommended")
def recommended_series_style():
    return {"style": RECOMMENDED_STYLE, "avoid": RECOMMENDED_AVOID}


@app.get("/api/series_style")
def get_series_style():
    data = dict(DEFAULT_SERIES)
    if SERIES_FILE.exists():
        try:
            data.update(json.loads(SERIES_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    data["style_images"] = _fix_paths(data.get("style_images"))
    for c in data.get("characters", []):
        c["images"] = _fix_paths(c.get("images"))
    return data


@app.post("/api/series_style")
def save_series_style(body: dict):
    clean = {"style": str(body.get("style", ""))[:4000], "avoid": str(body.get("avoid", ""))[:2000],
             "style_images": [str(p) for p in body.get("style_images", [])][:4], "characters": [],
             "style_migrated": bool(body.get("style_migrated", True))}
    # Generous limits: only a scene's own characters (up to 3) go into its prompt, so the list can grow.
    for c in body.get("characters", [])[:200]:
        clean["characters"].append({"id": str(c.get("id", ""))[:40], "name": str(c.get("name", ""))[:80],
                                    "description": str(c.get("description", ""))[:2000], "main": bool(c.get("main")),
                                    "images": [str(p) for p in c.get("images", [])][:2]})
    tmp = SERIES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SERIES_FILE)
    return {"ok": True}


# ---- scene images with ChatGPT (Codex CLI signed in with ChatGPT) ----------------------------------
image_jobs = {}


@app.get("/api/imagegen/models")
def imagegen_models(force: bool = False):
    return {"models": [{"slug": imagegen.REVIEW_MODEL, "name": "GPT-6.1 Terra · medium"}],
            "chosen": imagegen.REVIEW_MODEL, "default": "GPT-6.1 Terra · medium"}


@app.post("/api/imagegen/model")
def imagegen_set_model(body: dict):
    slug = str(body.get("model") or "")
    if slug != imagegen.REVIEW_MODEL:
        raise HTTPException(400, "Scene generation and review use GPT-6.1 Terra at medium reasoning.")
    return {"chosen": imagegen.REVIEW_MODEL}


@app.get("/api/imagegen/status")
def imagegen_status(force: bool = False):
    s = imagegen.status(force=force)
    return {k: s[k] for k in ("ready", "version", "message")}


REF_GRID_DIR = WORKSPACE / "cache" / "refgrids"


def _reference_grid(paths):
    """Several character sheets combined into one image (ChatGPT accepts only 5 reference images).
    Cells are filled left to right, top to bottom: 2 per row for up to 4 sheets, else 3."""
    paths = [str(p) for p in paths if p and Path(p).is_file()]
    if len(paths) < 2:
        return paths[0] if paths else None
    REF_GRID_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([[p, Path(p).stat().st_mtime] for p in paths]).encode()).hexdigest()[:20]
    out = REF_GRID_DIR / f"{key}.png"
    if out.exists():
        return str(out)
    cols, w, h, m = (3 if len(paths) > 4 else 2), 768, 480, 8
    args = [CONFIG["ffmpeg"], "-y", "-v", "error"]
    for p in paths:
        args += ["-i", p]
    cells = "".join(f"[{k}:v]scale={w - 2 * m}:{h - 2 * m}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1[c{k}];" for k in range(len(paths)))
    layout = "|".join(f"{(k % cols) * w}_{(k // cols) * h}" for k in range(len(paths)))
    args += ["-filter_complex", cells + "".join(f"[c{k}]" for k in range(len(paths))) +
             f"xstack=inputs={len(paths)}:layout={layout}:fill=white[out]",
             "-map", "[out]", "-frames:v", "1", str(out)]
    r = subprocess.run(args, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not out.exists():
        raise imagegen.ImageError("Could not combine the character sheets: " + (r.stderr.strip().splitlines() or ["ffmpeg failed"])[-1][:200])
    return str(out)


def _expand_references(refs):
    out = []
    for r in refs or []:
        path = _reference_grid(r.get("grid") or []) if isinstance(r, dict) else r
        if path:
            out.append(path)
    return out


def _run_image_job(job, body):
    try:
        job.update("Starting Codex…", 0.05)
        path, warning = imagegen.generate_reviewed(body.get("prompt", ""), _expand_references(body.get("references")),
                                                   cancelled=job.cancelled, on_progress=lambda m: job.update(m, 0.3))
        job.result = {"path": path, "name": Path(path).name, "review_warning": warning}
        job.update("Done", 1.0)
        job.state = "done"
    except imagegen.Cancelled:
        job.state, job.stage = "cancelled", "Cancelled"
    except imagegen.ImageError as e:
        job.state, job.error = "error", str(e)
    except Exception as e:
        traceback.print_exc()
        job.state, job.error = "error", f"{type(e).__name__}: {e}"


@app.post("/api/imagegen/jobs")
def imagegen_start(body: dict):
    if not (body.get("prompt") or "").strip():
        raise HTTPException(400, "The image prompt is empty.")
    job = Job("image")
    image_jobs[job.id] = job
    if auto_stop:
        auto_stop.start_work()
    threading.Thread(target=_run_image_job, args=(job, body), daemon=True).start()
    return job.to_dict()


@app.get("/api/imagegen/jobs/{job_id}")
def imagegen_job(job_id: str):
    if job_id not in image_jobs:
        raise HTTPException(404, "Unknown image job")
    return image_jobs[job_id].to_dict()


@app.post("/api/imagegen/jobs/{job_id}/cancel")
def imagegen_cancel(job_id: str):
    if job_id in image_jobs:
        image_jobs[job_id].cancel()
    return {"ok": True}


# ---- auto reels: ChatGPT plans the scripts, the browser then generates images and exports each reel ----
reel_jobs = {}


def _run_reel_plan(job, count, genre):
    try:
        job.update("Reading the plan and reel history…", 0.05)
        job.result = reels.plan(count, job, genre=genre)
        job.update("Done", 1.0)
        job.state = "done"
    except imagegen.Cancelled:
        job.state, job.stage = "cancelled", "Cancelled"
    except (reels.PlanError, imagegen.ImageError) as e:
        job.state, job.error = "error", str(e)
    except Exception as e:
        traceback.print_exc()
        job.state, job.error = "error", f"{type(e).__name__}: {e}"


@app.post("/api/reels/plan")
def reels_plan(body: dict):
    try:
        count = int(body.get("count") or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "The number of reels must be a whole number.")
    if not 1 <= count <= reels.MAX_COUNT:
        raise HTTPException(400, f"Plan between 1 and {reels.MAX_COUNT} reels at a time.")
    genre = str(body.get("genre") or reels.DEFAULT_GENRE)
    if genre not in reels.GENRES and genre != reels.RANDOM:
        raise HTTPException(400, f"Unknown genre: {genre}")
    if any(j.state == "running" for j in reel_jobs.values()):
        raise HTTPException(409, "ChatGPT is already planning reels. Wait for it or stop it.")
    job = Job("reels")
    reel_jobs[job.id] = job
    if auto_stop:
        auto_stop.start_work()
    threading.Thread(target=_run_reel_plan, args=(job, count, genre), daemon=True).start()
    return job.to_dict()


@app.get("/api/reels/jobs/{job_id}")
def reels_job(job_id: str):
    if job_id not in reel_jobs:
        raise HTTPException(404, "Unknown planning job")
    return reel_jobs[job_id].to_dict()


@app.post("/api/reels/jobs/{job_id}/cancel")
def reels_cancel(job_id: str):
    if job_id in reel_jobs:
        reel_jobs[job_id].cancel()
    return {"ok": True}


@app.get("/api/reels/history")
def reels_history():
    return reels.summary()


@app.post("/api/reels/record")
def reels_record(body: dict):
    """Scripts typed or pasted by hand (not planned by ChatGPT) still go into the history."""
    items = []
    for r in body.get("reels") or []:
        title = " ".join(str(r.get("title") or "").split())
        lines = [" ".join(str(l).split()) for l in r.get("lines") or [] if str(l).strip()]
        if title and lines:
            items.append({"title": title, "lines": lines, "pillar": str(r.get("pillar") or ""),
                          "structure": "", "mood": "", "topic": "", "uniqueness": "written or edited by hand"})
    if not items:
        raise HTTPException(400, "No script with a title and lines was given.")
    known = {reels._norm(e["title"]): e for e in reels.read_history() if e.get("id")}
    new = [r for r in items if reels._norm(r["title"]) not in known]
    added = {reels._norm(e["title"]): e for e in (reels.append_history(new) if new else [])}
    return {"reels": [known.get(reels._norm(r["title"])) or added[reels._norm(r["title"])] for r in items]}


@app.post("/api/reels/status")
def reels_status(body: dict):
    if not reels.set_status(str(body.get("id") or ""), str(body.get("status") or "")):
        raise HTTPException(404, "That reel is not in the history.")
    return {"ok": True}


@app.get("/api/preview/{name}")
def preview_file(name: str):
    p = PREVIEW_DIR / Path(name).name
    if not p.is_file():
        raise HTTPException(404, "Preview not found")
    return FileResponse(p, media_type="video/mp4")


# ---- projects & dialogs ----------------------------------------------------------------------
@app.post("/api/project/save")
def save_project(body: dict):
    project, path = body["project"], body.get("path")
    if body.get("autosave"):
        if not path or not Path(path).is_absolute():
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", project.get("name") or "Station Elsewhere")
            path = PROJECTS_DIR / f"{name}.storycraft.json"
            k = 2
            while path.exists():
                path, k = PROJECTS_DIR / f"{name} ({k}).storycraft.json", k + 1
    elif not path or body.get("save_as"):
        name = (project.get("name") or "Station Elsewhere").strip() or "Station Elsewhere"
        path = dialogs.ask("save", title="Save project", initialdir=str(PROJECTS_DIR),
                           initialfile=f"{name}.storycraft.json", defaultextension=".json",
                           filetypes=[["Station Elsewhere project", "*.json"]])
        if not path:
            return {"path": None}
    project = {**project, "version": 1}
    if "revision" in body and Path(path).is_file() and codespace_monitor.revision(path) != body["revision"]:
        raise HTTPException(409, "The project file changed outside this tab. Open it again or use Save as.")
    tmp = Path(path).with_name(f".{Path(path).name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as file:
            file.write(json.dumps(project, indent=2, ensure_ascii=False))
            file.flush()
            os.fsync(file.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    if auto_stop and not body.get("autosave"):
        auto_stop.activity("Project saved.")
    return {"path": str(Path(path)), "revision": codespace_monitor.revision(project)}


@app.post("/api/project/open")
def open_project(body: dict):
    path = body.get("path") or dialogs.ask("open", title="Open project", initialdir=str(PROJECTS_DIR),
                                           filetypes=[["Station Elsewhere project", "*.json"]])
    if not path:
        return {"path": None}
    try:
        project = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(400, f"Could not open project: {e}")
    if not isinstance(project.get("scenes"), list):
        raise HTTPException(400, "This file is not a Station Elsewhere project.")
    fix_project(project)                 # made on another PC: its media/ etc. paths point into this folder
    project["settings"] = merged_settings(project)
    missing, relinked = [], 0
    for scene in project["scenes"]:
        image = scene.get("image")
        if image and Path(image).is_file():
            continue
        fixed = relink(image)
        if fixed:
            scene["image"], relinked = fixed, relinked + 1
        else:
            missing.append(image)
    return {"path": path, "project": project, "missing": missing, "relinked": relinked,
            "revision": codespace_monitor.revision(path)}


@app.post("/api/setup/locate")
def setup_locate(body: dict):
    """Let the user point to Kokoro or FFmpeg when auto-detection failed; saved in config.json."""
    global narrator
    kind = body.get("kind")
    if kind == "ffmpeg":
        path = dialogs.ask("open", title="Locate ffmpeg.exe", filetypes=[["ffmpeg.exe", "ffmpeg.exe"]])
        if not path:
            return {"ok": False}
        probe = Path(path).with_name("ffprobe.exe")
        if not probe.exists():
            raise HTTPException(400, "ffprobe.exe must be in the same folder as ffmpeg.exe.")
        CONFIG.update(ffmpeg=str(Path(path)), ffprobe=str(probe))
    elif kind == "kokoro":
        path = dialogs.ask("open", title="Locate the Kokoro model file",
                           filetypes=[["Kokoro model", "kokoro-v1.0.onnx kokoro-v1_0.pth"], ["All files", "*.*"]])
        if not path:
            return {"ok": False}
        p = Path(path)
        if p.name.endswith(".onnx"):
            voices = p.with_name("voices-v1.0.bin")
            if not voices.exists():
                raise HTTPException(400, "voices-v1.0.bin must be in the same folder as the .onnx model.")
            CONFIG.update(kokoro_engine="onnx", kokoro_model=str(p), kokoro_voices=str(voices), kokoro_torch_dir="")
        elif p.name.endswith(".pth"):
            CONFIG.update(kokoro_engine="torch", kokoro_torch_dir=str(p.parent), kokoro_model="", kokoro_voices="")
        else:
            raise HTTPException(400, "Choose kokoro-v1.0.onnx (ONNX) or kokoro-v1_0.pth (PyTorch / Kokoro-82M).")
        narrator = make_narrator()
    else:
        raise HTTPException(400, "Unknown setup item")
    config_module.save(CONFIG)
    return {"ok": True}


# ---- episodes: "continues from" a previous episode's project file -----------------------------------
def _episode_name(path):
    name = Path(path).name
    for ext in (".storycraft.json", ".sidequest.json", ".json"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


# ---- background music ------------------------------------------------------------------------
@app.post("/api/music/pick")
def music_pick():
    """Choose a music file; a copy is kept in media/ so the project still works if the original moves."""
    path = dialogs.ask("open", title="Choose background music",
                       filetypes=[["Audio", " ".join("*" + e for e in sorted(AUDIO_TYPES))]])
    if not path:
        return {"path": None}
    src = Path(path)
    if src.suffix.lower() not in AUDIO_TYPES:
        raise HTTPException(400, "Choose an audio file: " + ", ".join(sorted(AUDIO_TYPES)))
    data = src.read_bytes()
    safe = re.sub(r"[^\w.\- ]", "_", src.name)
    target = MEDIA_DIR / f"music_{hashlib.sha1(data).hexdigest()[:16]}_{safe}"
    if not target.exists():
        target.write_bytes(data)
    return {"path": str(target), "name": src.name}


@app.post("/api/intro/pick")
def intro_pick():
    """Choose your own intro sound; a copy is kept in media/ like the music. Returns its length too."""
    path = dialogs.ask("open", title="Choose an intro sound",
                       filetypes=[["Audio", " ".join("*" + e for e in sorted(AUDIO_TYPES))]])
    if not path:
        return {"path": None}
    src = Path(path)
    if src.suffix.lower() not in AUDIO_TYPES:
        raise HTTPException(400, "Choose an audio file: " + ", ".join(sorted(AUDIO_TYPES)))
    data = src.read_bytes()
    safe = re.sub(r"[^\w.\- ]", "_", src.name)
    target = MEDIA_DIR / f"intro_{hashlib.sha1(data).hexdigest()[:16]}_{safe}"
    if not target.exists():
        target.write_bytes(data)
    try:
        audio = intro_audio({"intro": {"enabled": True, "file": str(target), "volume": 100}})
    except subprocess.CalledProcessError:
        audio = None
    if audio is None:
        raise HTTPException(400, "That audio file can't be read.")
    if len(audio) / SAMPLE_RATE > 15:
        raise HTTPException(400, "Choose a short sound (15 seconds or less): the voice waits until it ends.")
    return {"path": str(target), "name": src.stem, "length": round(len(audio) / SAMPLE_RATE, 3)}


@app.post("/api/overlay/pick")
def overlay_pick():
    """Choose a picture to put on top of the video (an animated GIF, or a PNG / WebP with transparency);
    a copy is kept in media/ so the project still works if the original moves."""
    path = dialogs.ask("open", title="Choose an overlay (GIF, PNG or WebP)",
                       filetypes=[["Pictures", " ".join("*" + e for e in sorted(OVERLAY_TYPES))]])
    if not path:
        return {"path": None}
    src = Path(path)
    if src.suffix.lower() not in OVERLAY_TYPES:
        raise HTTPException(400, "Choose a GIF, PNG or WebP picture.")
    data = src.read_bytes()
    safe = re.sub(r"[^\w.\- ]", "_", src.name)
    target = MEDIA_DIR / f"overlay_{hashlib.sha1(data).hexdigest()[:16]}_{safe}"
    if not target.exists():
        target.write_bytes(data)
    return {"path": str(target), "name": src.stem}


@app.post("/api/defaults")
def save_my_defaults(body: dict):
    """Make these settings the starting point of every new project (saved in my_defaults.json)."""
    mine = defaults_from(body.get("settings") or {})
    MY_DEFAULTS_FILE.write_text(json.dumps(mine, indent=2, ensure_ascii=False), encoding="utf-8")
    apply_my_defaults(mine)
    return {"ok": True}


@app.post("/api/defaults/reset")
def reset_my_defaults():
    """Back to Station Elsewhere's own starting settings."""
    MY_DEFAULTS_FILE.unlink(missing_ok=True)
    apply_my_defaults({})
    return {"ok": True}


@app.post("/api/lut/pick")
def lut_pick():
    """Choose a .cube color grade; a copy is kept in media/ so the project still works if the original moves."""
    path = dialogs.ask("open", title="Choose a color grade (.cube LUT)", filetypes=[["LUT", "*.cube"]])
    if not path:
        return {"path": None}
    src = Path(path)
    if not lut_kind(src):
        raise HTTPException(400, "That file is not a .cube LUT (it has no LUT_3D_SIZE or LUT_1D_SIZE line).")
    data = src.read_bytes()
    safe = re.sub(r"[^\w.\- ]", "_", src.name)
    target = MEDIA_DIR / f"lut_{hashlib.sha1(data).hexdigest()[:16]}_{safe}"
    if not target.exists():
        target.write_bytes(data)
    return {"path": str(target), "name": src.stem}


@app.post("/api/recording/pick")
def recording_pick():
    """Choose your voice-over; a copy is kept in media/ so the project still works if the original moves."""
    path = dialogs.ask("open", title="Choose your recording",
                       filetypes=[["Audio", " ".join("*" + e for e in sorted(AUDIO_TYPES))]])
    if not path:
        return {"path": None}
    src = Path(path)
    if src.suffix.lower() not in AUDIO_TYPES:
        raise HTTPException(400, "Choose an audio file: " + ", ".join(sorted(AUDIO_TYPES)))
    data = src.read_bytes()
    safe = re.sub(r"[^\w.\- ]", "_", src.name)
    target = MEDIA_DIR / f"recording_{hashlib.sha1(data).hexdigest()[:16]}_{safe}"
    if not target.exists():
        target.write_bytes(data)
    try:
        duration = len(recording.decoded(str(target))) / SAMPLE_RATE
    except recording.RecordingError as e:
        raise HTTPException(400, str(e))
    return {"path": str(target), "name": src.name, "duration": round(duration, 3)}


@app.get("/api/music")
def music_file(path: str):
    p = Path(local_path(path))
    if p.suffix.lower() not in AUDIO_TYPES or not p.is_file():
        raise HTTPException(404, "Music file not found")
    return FileResponse(p)


@app.post("/api/episode/pick")
def episode_pick(body: dict):
    path = dialogs.ask("open", title="Choose the previous episode", initialdir=str(PROJECTS_DIR),
                       filetypes=[["Station Elsewhere project", "*.json"]])
    if not path:
        return {"path": None}
    if body.get("current") and Path(path).resolve() == Path(body["current"]).resolve():
        raise HTTPException(400, "That's this episode itself. Choose the episode that comes before it.")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert isinstance(data.get("scenes"), list)
    except (OSError, ValueError, AssertionError):
        raise HTTPException(400, "That file is not a Station Elsewhere project.")
    return {"path": str(Path(path)), "name": _episode_name(path)}


@app.post("/api/episode/context")
def episode_context(body: dict):
    """The previous episodes (newest first, up to 3 back, following each one's own "continues from"):
    their narration and images, for continuity of story, place and time."""
    chain, seen, path = [], set(), local_path(body.get("path"))
    while path and len(chain) < 3:
        key = str(Path(path).resolve()).lower()
        if key in seen:
            break
        seen.add(key)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if not chain:
                raise HTTPException(404, f"The previous episode file was not found: {Path(path).name}")
            break
        scenes = []
        for sc in data.get("scenes", []):
            img = sc.get("image") or ""
            if img and not Path(img).is_file():
                img = relink(img) or ""
            scenes.append({"script": sc.get("script") or "", "image": img})
        story = (data.get("settings") or {}).get("story") or {}
        chain.append({"name": _episode_name(path), "path": str(path), "scenes": scenes, "world": story.get("world") or ""})
        path = local_path(story.get("continues_from") or "")
    return {"chain": chain}


@app.post("/api/dialog/folder")
def choose_folder(body: dict):
    return {"path": dialogs.ask("folder", title="Choose output folder",
                                initialdir=body.get("initial") or str(ROOT)) or None}


@app.post("/api/reveal")
def reveal(body: dict):
    p = Path(body.get("path", ""))
    if not IS_WINDOWS:          # no file manager here (e.g. Codespaces): download the file instead
        return {"ok": p.is_file(), "download": f"/api/download?path={_quote(str(p))}" if p.is_file() else None}
    if p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif p.is_dir():
        os.startfile(str(p))
    return {"ok": True}


def _quote(s):
    from urllib.parse import quote
    return quote(s, safe="")


DOWNLOAD_TYPES = {".mp4", ".json", ".wav", ".mp3", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".cube", ".txt"}


@app.get("/api/download")
def download(path: str):
    """Download an exported video (or another Station Elsewhere file) to the computer the browser runs on."""
    p = Path(local_path(path))
    if p.suffix.lower() not in DOWNLOAD_TYPES or not p.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(p, filename=p.name)


# ---- browser file dialogs (when there's no desktop for native ones, e.g. GitHub Codespaces) --------------
def _web_only():
    if not dialogs.web_mode():
        raise HTTPException(403, "Only used when Station Elsewhere runs without a desktop.")


@app.get("/api/dialog/pending")
def dialog_pending():
    return {"request": dialogs.pending() if dialogs.web_mode() else None}


@app.post("/api/dialog/answer")
def dialog_answer(body: dict):
    _web_only()
    return {"ok": dialogs.answer(str(body.get("id", "")), str(body.get("path") or ""))}


@app.get("/api/fs/list")
def fs_list(dir: str = ""):
    _web_only()
    d = Path(local_path(dir) or ROOT).expanduser()
    if not d.is_dir():
        d = ROOT
    d = d.resolve()
    folders, files = [], []
    try:
        for e in sorted(d.iterdir(), key=lambda e: e.name.lower()):
            if e.name.startswith(".") or e.name == "__pycache__":
                continue
            try:
                if e.is_dir():
                    folders.append(e.name)
                elif e.is_file():
                    files.append({"name": e.name, "size": e.stat().st_size})
            except OSError:
                continue
    except OSError as e:
        raise HTTPException(400, f"Can't read {d}: {e}")
    places = [("Station Elsewhere", ROOT), ("Projects", PROJECTS_DIR), ("Media", MEDIA_DIR), ("Exports", EXPORTS_DIR),
              ("Home", Path.home())]
    return {"dir": str(d), "parent": str(d.parent) if d.parent != d else None, "sep": os.sep,
            "folders": folders, "files": files, "places": [{"name": n, "path": str(p)} for n, p in places if p.is_dir()]}


@app.post("/api/fs/upload")
async def fs_upload(file: UploadFile, dir: str = ""):
    """A file from the computer the browser runs on, stored in the folder the browser dialog shows."""
    _web_only()
    d = Path(local_path(dir) or ROOT)
    if not d.is_dir():
        raise HTTPException(400, "That folder doesn't exist.")
    name = re.sub(r"[^\w.\- ()]", "_", Path(file.filename or "upload").name) or "upload"
    target, k = d / name, 2
    while target.exists():
        target = d / f"{Path(name).stem} ({k}){Path(name).suffix}"
        k += 1
    target.write_bytes(await file.read())
    return {"path": str(target)}


def _port_owner(port):
    """'free', 'self' (this Station Elsewhere folder is already serving it) or 'other'."""
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/instance", timeout=3) as r:
            info = json.loads(r.read().decode("utf-8"))
        same = os.path.normcase(os.path.abspath(info.get("root", ""))) == os.path.normcase(str(ROOT))
        return "self" if same else "other"
    except (OSError, ValueError):
        return "other"


def main():
    port = int(CONFIG.get("port", 8767))
    for candidate in range(port, port + 20):
        owner = _port_owner(candidate)
        if owner == "other":
            continue                                    # another app (e.g. Mr Sidequests): skip it
        port = candidate
        break
    else:
        print(f"No free port found between {port} and {port + 19}.")
        return
    if port != int(CONFIG.get("port", 8767)):
        print(f"Port {CONFIG.get('port')} is used by another app, using {port} instead.")
    url = f"http://127.0.0.1:{port}"
    if owner == "self":                                 # already running: just open it
        print(f"Station Elsewhere is already running at {url}")
        if not os.environ.get("STORYCRAFT_NO_BROWSER"):
            webbrowser.open(url)
        return
    cleanup_cache()
    import uvicorn
    host = os.environ.get("STORYCRAFT_HOST", "127.0.0.1")

    def announce_when_ready():
        # VS Code's "Open Station Elsewhere" opens the browser on this line, so print it only once the
        # server really answers (printing it earlier showed ERR_EMPTY_RESPONSE on the first load).
        import urllib.request
        for _ in range(600):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/instance", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        print(f"Station Elsewhere is running at {url}  (close this window to stop)", flush=True)
        if not os.environ.get("STORYCRAFT_NO_BROWSER"):
            webbrowser.open(url)

    threading.Thread(target=announce_when_ready, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
