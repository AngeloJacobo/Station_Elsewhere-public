"""Auto reels: ChatGPT (through the Codex CLI, signed in with ChatGPT) plans the next reel scripts.

The program, not ChatGPT, decides each reel's genre, storytelling shape and mood. The prompt is kept
short and focused on three rules: everyone can relate, meaningful and impactful, exactly 20 seconds.
reels_history.md records every planned reel (and later its export) so ChatGPT never repeats a topic. ChatGPT only writes the scripts; images, narration and export
are done by the app afterwards.
"""
import json
import random
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

from . import imagegen
from .config import ROOT

HISTORY_FILE = ROOT / "reels_history.md"
PLAN_TIMEOUT = 900
MAX_COUNT = 12

# Target share of the four permanent pillars (used by the Random genre) and the viewer feeling each aims for.
PILLARS = {
    "Observation / reflection": (40, "“I've never noticed that before.”"),
    "Nostalgia": (25, "“I remember that.”"),
    "Comfort": (20, "“Someone understands this feeling.”"),
    "Quiet encouragement": (15, "“Maybe I can try again tomorrow.”"),
}
# Genres you can pick in Auto reels. "Random" balances the four plan pillars above across the history.
GENRES = {
    "Life lessons": "“I needed to hear that.” A clear, earned lesson about how to live, love, rest, grow or let go, "
                    "shown through a concrete moment and landed in the final lines",
    "Nostalgia": PILLARS["Nostalgia"][1],
    "Comfort": PILLARS["Comfort"][1],
    "Quiet encouragement": PILLARS["Quiet encouragement"][1],
    "Observation / reflection": PILLARS["Observation / reflection"][1],
}
RANDOM = "Random"
DEFAULT_GENRE = "Life lessons"

# How a reel delivers its point. Every shape ends on the payoff; they only vary how it gets there.
STRUCTURES = {
    "Truth, proof, payoff": "state the truth, show the everyday moment that proves it, land what it means",
    "Then and now": "contrast how something was with how it is now, and say what changed in us",
    "You moment": "put the viewer inside a moment they have lived (“you…”), then reveal what it meant",
    "Question and answer": "open with a question everyone has asked themselves, answer it simply and warmly",
    "Everyday object": "an object nearly everyone has (a phone, keys, a mug, a bed) carries the point",
    "Quiet turn": "an ordinary moment turns out to show what really matters",
}
# Deeper and lighter moods alternate so two heavy reels never run back to back.
# Every mood must still carry a clear, meaningful message: no novelty or joke moods.
DEEP_MOODS = ["bittersweet", "wistful", "reflective", "tender"]
LIGHT_MOODS = ["warm", "hopeful", "comforting", "uplifting"]

TITLE_WORDS = (4, 12)
LINE_COUNT = (8, 10)
# 20–25 s with this app's voice, pauses, intro and outro (unchanged): exported reels ran 33 words → 17.6 s,
# 34 → 20.2 s, 41 → 26.0 s, i.e. 0.53–0.63 s per word. 38–42 words lands inside 20–25 s at either pace.
TOTAL_WORDS = (37, 42)
TARGET_SECONDS = "20–25"
LINE_WORDS = 7

HEADER = """# Station Elsewhere — reel history

Every reel planned by **🎬 Auto reels** is recorded here, oldest first. ChatGPT reads this file before
planning new reels so topics, objects, settings, pillars and structures don't repeat. You can edit or
delete entries by hand; keep the `## title` heading, the `- Field: value` lines and the `text` block.
"""

_lock = threading.Lock()


class PlanError(Exception):
    pass


# ---- history -------------------------------------------------------------------------------------
def _words(text):
    return re.findall(r"[A-Za-z0-9’']+", text or "")


def _norm(text):
    return " ".join(w.lower().replace("’", "'") for w in _words(text))


def read_history():
    """Entries oldest first: {id, title, lines, and the - Field: value pairs in lower case}."""
    try:
        text = HISTORY_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        entry = {"title": head.strip(), "id": "", "lines": []}
        m = re.search(r"<!--\s*reel:\s*([\w-]+)\s*-->", body)
        if m:
            entry["id"] = m.group(1)
        for key, value in re.findall(r"^- ([A-Za-z ]+):\s*(.*)$", body, flags=re.M):
            entry[key.strip().lower()] = value.strip()
        m = re.search(r"```text\n(.*?)```", body, flags=re.S)
        if m:
            entry["lines"] = [l.strip() for l in m.group(1).splitlines() if l.strip()]
        entries.append(entry)
    return entries


def _entry_md(e):
    fields = [("Planned", e.get("planned")), ("Status", e.get("status") or "planned"), ("Pillar", e.get("pillar")),
              ("Structure", e.get("structure")), ("Mood", e.get("mood")), ("Topic", e.get("topic")),
              ("Motif", e.get("motif")), ("Setting", e.get("setting")), ("Message", e.get("message")),
              ("Uniqueness", e.get("uniqueness"))]
    out = [f"## {e['title']}", f"<!-- reel: {e['id']} -->"]
    out += [f"- {k}: {' '.join(str(v).split())}" for k, v in fields if v]
    out += ["", "```text", *e.get("lines", []), "```", ""]
    return "\n".join(out)


def append_history(reels):
    """Record reels (dicts with title + lines, optional pillar/structure/...). Returns them with ids."""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with _lock:
        recorded = []
        for r in reels:
            e = {**r, "id": r.get("id") or uuid.uuid4().hex[:12], "planned": r.get("planned") or stamp,
                 "status": r.get("status") or "planned"}
            recorded.append(e)
        current = HISTORY_FILE.read_text(encoding="utf-8") if HISTORY_FILE.is_file() else HEADER
        text = current.rstrip("\n") + "\n\n" + "\n".join(_entry_md(e) for e in recorded)
        _write(text)
        return recorded


def set_status(reel_id, status):
    """Replace the Status line of one entry (e.g. 'exported · exports/x.mp4 · 20.1 s')."""
    status = " ".join(str(status).split())[:300]
    with _lock:
        if not HISTORY_FILE.is_file():
            return False
        text = HISTORY_FILE.read_text(encoding="utf-8")
        marker = f"<!-- reel: {reel_id} -->"
        start = text.find(marker)
        if not reel_id or start < 0:
            return False
        end = text.find("\n## ", start)
        end = len(text) if end < 0 else end
        block = text[start:end]
        if re.search(r"^- Status:.*$", block, flags=re.M):
            block = re.sub(r"^- Status:.*$", lambda _: f"- Status: {status}", block, count=1, flags=re.M)
        else:
            block = block.replace(marker, f"{marker}\n- Status: {status}", 1)
        _write(text[:start] + block + text[end:])
        return True


def _write(text):
    tmp = HISTORY_FILE.with_name(f".{HISTORY_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(HISTORY_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def summary():
    entries = read_history()
    counts = Counter(e.get("pillar") for e in entries if e.get("pillar") in GENRES)
    return {"genres": [*GENRES, RANDOM], "default_genre": DEFAULT_GENRE,"file": str(HISTORY_FILE), "total": len(entries),
            "exported": sum(1 for e in entries if (e.get("status") or "").startswith("exported")),
            "pillars": {p: counts.get(p, 0) for p in GENRES},
            "targets": {p: share for p, (share, _) in PILLARS.items()},
            "recent": [{"title": e["title"], "pillar": e.get("pillar", ""), "status": e.get("status", "")}
                       for e in entries[-8:]][::-1]}


# ---- editorial slots ---------------------------------------------------------------------------
def plan_slots(history, count, rng=random, genre=RANDOM):
    """Genre ("pillar"), structure and mood for each new reel. A chosen genre is used for every reel; with
    Random, the four pillars follow their target shares across the whole history and never repeat back to
    back. Structures and moods never repeat back to back, and deep/light moods alternate."""
    counts = Counter(e.get("pillar") for e in history if e.get("pillar") in PILLARS)
    done = sum(counts.values())
    last = history[-1] if history else {}
    prev_pillar, prev_structure, prev_mood = last.get("pillar"), last.get("structure"), last.get("mood")
    recent_structures = [e.get("structure") for e in history[-8:]]
    recent_moods = [e.get("mood") for e in history[-10:]]
    deep = prev_mood in DEEP_MOODS
    slots = []
    for _ in range(count):
        n = done + 1
        pillar = genre if genre in GENRES else max(
            (p for p in PILLARS if p != prev_pillar),
            key=lambda p: (PILLARS[p][0] / 100 * n - counts[p], PILLARS[p][0], rng.random()))
        used = Counter(recent_structures[-8:])
        structure = min((s for s in STRUCTURES if s != prev_structure),
                        key=lambda s: (used[s], rng.random()))
        deep = not deep
        moods = [m for m in (DEEP_MOODS if deep else LIGHT_MOODS) if m != prev_mood]
        mood_used = Counter(recent_moods[-10:])
        mood = min(moods, key=lambda m: (mood_used[m], rng.random()))
        slots.append({"pillar": pillar, "structure": structure, "mood": mood})
        counts[pillar] += 1
        done += 1
        recent_structures.append(structure)
        recent_moods.append(mood)
        prev_pillar, prev_structure, prev_mood = pillar, structure, mood
    return slots


# ---- validation ----------------------------------------------------------------------------------
def problems(reel, taken_titles):
    """(hard, soft) problems. Hard problems drop a reel; soft ones are retried, then accepted with a note."""
    hard, soft = [], []
    title, lines = (reel.get("title") or "").strip(), [l.strip() for l in reel.get("lines") or [] if l.strip()]
    if not title:
        hard.append("the title is empty")
    elif _norm(title) in taken_titles:
        hard.append("the title repeats an earlier reel")
    elif not TITLE_WORDS[0] <= len(_words(title)) <= TITLE_WORDS[1]:
        soft.append(f"the title has {len(_words(title))} words (use {TITLE_WORDS[0]}–{TITLE_WORDS[1]})")
    later = [w for w in _words(title)[1:] if len(w) > 3]
    if later and all(w[0].isupper() for w in later):
        soft.append("the title is in Title Case like a headline; write it as a sentence-case statement")
    if len(lines) < LINE_COUNT[0]:
        hard.append(f"only {len(lines)} lines (at least {LINE_COUNT[0]} are required)")
    elif len(lines) > LINE_COUNT[1]:
        soft.append(f"{len(lines)} lines (use {LINE_COUNT[0]}–{LINE_COUNT[1]})")
    total = sum(len(_words(l)) for l in lines)
    if not TOTAL_WORDS[0] <= total <= TOTAL_WORDS[1]:
        change = f"cut {total - TOTAL_WORDS[1]} or more words" if total > TOTAL_WORDS[1] else f"add {TOTAL_WORDS[0] - total} or more words"
        soft.append(f"{total} spoken words in total, {change} (use {TOTAL_WORDS[0]}–{TOTAL_WORDS[1]} for {TARGET_SECONDS} seconds)")
    long = [l for l in lines if len(_words(l)) > LINE_WORDS]
    if long:
        soft.append(f"lines longer than {LINE_WORDS} words: " + "; ".join(f"“{l}”" for l in long[:3]))
    if sum(1 for l in lines if l.endswith("?")) > 1:
        soft.append("more than one line ends with a question mark")
    if any(re.search(r"[.,;:!]$", l) for l in lines):
        soft.append("some lines end with punctuation (only an occasional “?” is allowed)")
    return hard, soft


def clean(reel):
    lines = [re.sub(r"\s+", " ", l).strip() for l in reel.get("lines") or []]
    lines = [re.sub(r"[.,;:!]+$", "", l).strip() for l in lines if l.strip()]
    lines = [l[0].upper() + l[1:] for l in lines if l]
    title = re.sub(r"\s+", " ", reel.get("title") or "").strip().strip("\"“”'").rstrip(".")
    return {**reel, "title": title, "lines": [l for l in lines if l]}


# ---- ChatGPT ---------------------------------------------------------------------------------------
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reels"],
    "properties": {"reels": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["slot", "title", "message", "topic", "motif", "setting", "lines", "uniqueness"],
        "properties": {
            "slot": {"type": "integer"},
            "title": {"type": "string"},
            "topic": {"type": "string"},
            "motif": {"type": "string"},
            "setting": {"type": "string"},
            "lines": {"type": "array", "items": {"type": "string"}},
            "uniqueness": {"type": "string"},
            "message": {"type": "string"},
        }}}},
}

EXAMPLE = """Some days are meant to be survived
Not conquered
You didn't make progress
You didn't become stronger
You simply made it through
But not every hard day
Needs to teach you a lesson
Sometimes
Making it to tomorrow
Is enough"""


def _history_text(history):
    if not history:
        return "(none yet)"
    return "\n".join(f"- “{e['title']}” — {e.get('pillar') or '?'}; opens “{(e.get('lines') or [''])[0]}”"
                     + (f"; point: {e['message']}" if e.get("message") else f"; topic: {e.get('topic') or '?'}")
                     for e in history[-60:])


def build_prompt(slots, history, avoid_titles=(), feedback=""):
    slot_text = "\n".join(
        f"Reel {k}: genre = {s['pillar']} — {GENRES.get(s['pillar'], '')}. Shape = {s['structure']}: "
        f"{STRUCTURES.get(s['structure'], '')}. Mood = {s['mood']}." for k, s in slots)
    avoid = f"\nAlso taken in this batch: {'; '.join(avoid_titles)}." if avoid_titles else ""
    fix = f"\n\nFIX THESE PROBLEMS FROM YOUR PREVIOUS ATTEMPT:\n{feedback}" if feedback else ""
    return f"""Write {len(slots)} script(s) for "Station Elsewhere", a Facebook Reels page of calm, illustrated {TARGET_SECONDS} second reels with a soft narrator voice.

RULE 1 — EVERYONE CAN RELATE (most important):
Write about experiences nearly every adult has lived, anywhere in the world: being tired after a long day, missing someone, parents getting older, friends drifting apart, overthinking at night, feeling behind in life, starting over, being kind to yourself, home, love, time passing. The test: 9 out of 10 adults hear it and think "that's me". Avoid anything niche: particular jobs, hobbies (no pianos, painting, gardening), skills, places, cultures, or experiences only some people have. Speak to "you". Use simple everyday details everyone shares (a phone at 2 AM, a quiet car after work, a parent's voice, an empty chair).

RULE 2 — MEANINGFUL AND IMPACTFUL:
One clear message in the reel's genre that shifts how the viewer sees their own life. They should understand it on the first listen (simple words, one idea, no riddles, puns, trivia or vague imagery) and feel it.
- Line 1 is the hook (the first 3 seconds decide if people keep watching): a bold, emotional truth that stops the scroll by itself, e.g. "One day you'll hug your parents for the last time", "Nobody tells you friendships end quietly", "You were never too much". Never a scene-setting detail.
- The middle lines make it real with relatable moments.
- The last 2–3 lines are the payoff (they decide if people follow the page): the meaning lands, memorably, and the final line feels like an ending. Earned, not a cliché poster slogan.

RULE 3 — {TARGET_SECONDS} SECONDS:
- {LINE_COUNT[0]}–{LINE_COUNT[1]} lines; each line is one scene with its own illustration, at most {LINE_WORDS} words, and something that can be drawn. The lines read as one natural spoken thought, breaking at breath points.
- STRICT LENGTH: {TOTAL_WORDS[0]}–{TOTAL_WORDS[1]} spoken words in total, aim for exactly 40 (fewer than {TOTAL_WORDS[0]} runs under 20 seconds, more than {TOTAL_WORDS[1]} runs over 25). 9–10 lines of 3–6 words work best. Count the words of every reel before answering.
- Start each line with a capital letter; no punctuation at line ends (one "?" allowed).
- "title": the reel's main truth as a short sentence-case statement, {TITLE_WORDS[0]}–{TITLE_WORDS[1]} words, e.g. "Some days are meant to be survived". Not a headline label. It is not spoken, so line 1 must not repeat it.

GOOD (hook, relatable middle, payoff; 41 words in 10 lines ran 26 seconds, just over — aim for 40):
{EXAMPLE}

BAD (confusing, niche, no impact): You begged for receipts / To play shop at home / You scanned each tin / And counted pretend change / Now at the checkout / The receipt keeps unrolling / You watch it curl / You check the cheese price

WRITE THESE:
{slot_text}
Each reel needs a different subject and different opening words.

ALREADY MADE (don't repeat their subject, point or opening):
{_history_text(history)}{avoid}{fix}

Reply with JSON only: {{"reels": [...]}}, one object per reel with "slot" (its reel number), "title", "message" (its point in one plain sentence), "topic" (the life experience, a few words), "motif" (the main image or object), "setting" (the main place), "lines", and "uniqueness" (one short line on why everyone relates)."""


def _ask(prompt, cancelled=None):
    st = imagegen.status()
    if not st["ready"]:
        raise PlanError(st["message"])
    with tempfile.TemporaryDirectory(prefix="station-reels-") as work:
        schema, output = Path(work) / "schema.json", Path(work) / "result.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        args = imagegen.isolated_args(str(schema), str(output), [], image_generation=False, effort="high")
        proc = subprocess.Popen([st["codex"], *args], cwd=work, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env=imagegen._env(), creationflags=imagegen.NO_WINDOW)
        errors = []
        reader = threading.Thread(target=lambda: errors.extend(proc.stderr.readlines()), daemon=True)
        reader.start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            started = time.monotonic()
            while proc.poll() is None:
                if cancelled and cancelled():
                    raise imagegen.Cancelled()
                if time.monotonic() - started > PLAN_TIMEOUT:
                    raise PlanError("ChatGPT took too long to write the scripts. Please try again.")
                time.sleep(0.3)
            reader.join(timeout=5)
            if proc.returncode:
                text = "".join(errors)
                if imagegen.LIMIT.search(text):
                    raise PlanError("Your ChatGPT limit for Codex has been reached. Try again after it resets.")
                reason = next((l.strip() for l in reversed(errors) if l.strip() and "reading prompt" not in l.lower()), "")
                raise PlanError("ChatGPT could not write the scripts. " + (reason[:400] or "Please try again."))
            try:
                data = json.loads(output.read_text(encoding="utf-8"))
                reels = data["reels"]
                if not isinstance(reels, list):
                    raise ValueError
                return reels
            except (OSError, ValueError, KeyError, TypeError):
                raise PlanError("ChatGPT returned scripts in an unexpected format. Please try again.") from None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def plan(count, job=None, attempts=3, ask=_ask, genre=DEFAULT_GENRE):
    """Plan `count` reels, validate them, record them in the history and return them."""
    count = max(1, min(MAX_COUNT, int(count)))
    cancelled = job.cancelled if job else None
    history = read_history()
    slots = plan_slots(history, count, genre=genre)
    taken = {_norm(e["title"]) for e in history}
    accepted, pending, feedback, warnings = {}, list(range(1, count + 1)), {}, []
    best = {}
    for attempt in range(attempts):
        if not pending:
            break
        if job:
            job.update(f"ChatGPT is writing {len(pending)} script{'s' if len(pending) != 1 else ''}"
                       f"{' (fixing problems)' if attempt else ''}…", 0.1 + 0.25 * attempt)
        note = "\n".join(f"- Slot {k}: {feedback[k]}" for k in pending if k in feedback)
        prompt = build_prompt([(k, slots[k - 1]) for k in pending], history,
                              [accepted[k]["title"] for k in sorted(accepted)], note)
        replies = ask(prompt, cancelled)
        by_slot = {}
        for r in replies:
            if isinstance(r, dict) and isinstance(r.get("slot"), int) and r["slot"] in pending:
                by_slot.setdefault(r["slot"], r)
        # Replies without usable slot numbers fill the remaining slots in order.
        loose = [r for r in replies if isinstance(r, dict) and r not in by_slot.values()]
        for k in pending:
            if k not in by_slot and loose:
                by_slot[k] = loose.pop(0)
        still = []
        for k in pending:
            reel = clean(by_slot.get(k) or {})
            batch_titles = {_norm(a["title"]) for a in accepted.values()}
            hard, soft = problems(reel, taken | batch_titles) if reel.get("lines") or reel.get("title") else (["no script was returned"], [])
            if not hard and not soft:
                accepted[k] = reel
            else:
                if not hard:
                    best[k] = (reel, soft)
                feedback[k] = "; ".join(hard + soft) + (
                    f". Your draft was: {reel.get('title', '')} / " + " / ".join(reel.get("lines", [])) if reel.get("lines") else "")
                still.append(k)
        pending = still
    for k in pending:
        if k in best and _norm(best[k][0]["title"]) not in {_norm(a["title"]) for a in accepted.values()}:
            accepted[k] = best[k][0]
            warnings.append(f"“{best[k][0]['title']}”: accepted with notes — {'; '.join(best[k][1])}.")
        else:
            warnings.append(f"Slot {k} ({slots[k - 1]['pillar']}) was dropped: {feedback.get(k, 'no usable script')}.")
    if not accepted:
        raise PlanError("ChatGPT did not return any usable script. " + " ".join(warnings))
    reels = [{**slots[k - 1], **{f: accepted[k].get(f, "") for f in ("title", "lines", "message", "topic", "motif", "setting", "uniqueness")},
              "source": "chatgpt"} for k in sorted(accepted)]
    return {"reels": append_history(reels), "warnings": warnings}
