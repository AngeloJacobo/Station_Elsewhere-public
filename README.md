# Station Elsewhere


<img width="1907" height="867" alt="sc1" src="https://github.com/user-attachments/assets/829ab031-bae7-443e-9d09-789a2ea07101" />

<img width="1916" height="867" alt="sc2" src="https://github.com/user-attachments/assets/24bb5188-668a-4111-9db9-bd08a79fbad3" />


**Turn a folder of scene images and one narration script into a finished, narrated video,
automatically.**

---

## Run it in GitHub Codespaces (or any Linux)

This public repo contains the app, the built-in (CC0) station/cassette sounds, open-licensed fonts
and a few sample images. Your own media, projects, narration cache, default settings, reel history
and Facebook post log stay on your machine (they are listed in `.gitignore`).

1. Open the repo in a Codespace (**Code → Codespaces → Create codespace**). The dev container
   installs everything and starts Station Elsewhere; the app opens in a browser tab on port **8767**.
   *If you `git clone` it into an existing Codespace / Linux machine instead, run:* `./start.sh`
2. Add scene images and narration (or use **🎬 Auto reels**), press **Export MP4**, then
   **⬇ Download video** to save the MP4 to your computer (it's also in `exports/`).

Notes:

- **New lines of narration** need your Cartesia key. Add it as a Codespaces secret named
  `CARTESIA_API_KEY` (github.com → Settings → Codespaces → Secrets), or paste it in
  *Settings → Voice*. Narration is cached per line, so a line is only spoken (and paid for) once.
  Kokoro isn't installed here.
- **Open / Save / Choose music…** show a file browser inside the page (there's no desktop in a
  Codespace). **⬆ Upload from my computer** copies a file from your PC into the folder shown.
- **Fonts**: Windows fonts aren't on Linux, so free look-alikes in `fonts/` are used automatically:
  Segoe UI → *Selawik* (Microsoft's open, metric-compatible stand-in), Consolas → *Inconsolata*,
  Arial → *Liberation Sans*. For a pixel-identical match, copy your own `segoeuil.ttf` /
  `consola.ttf` from `C:\Windows\Fonts` into `fonts/` (don't commit them: they're licensed fonts),
  then restart the app. The page preview loads the same font files the video is rendered with, so
  text in the preview looks like the export. Preview video uses MP3 audio so it has sound in
  VS Code's built-in browser (which can't play AAC); exported MP4s keep AAC.
- Paths saved on Windows (`C:\Users\...\quote_project\media\...`) are found in this folder
  automatically, and projects saved here open on Windows too.
- Scene images with ChatGPT: the Codex CLI is installed; sign in once with `codex login --device-auth`.

---

## Alongside Lost NEET in the same Codespace

In this workspace Station Elsewhere uses port **8767**, while the sibling
`../StoryCraft` app uses **8765**. Run `bash start.sh` from this folder, or use
the workspace's **Open both apps** Run and Debug entry. Each app has its own
environment and data.
The workspace launch entries pass `--restart`: the sibling restart helper stops
only this checkout's existing server/progress feed before launching a fresh one.
This interrupts active jobs. Plain `bash start.sh` retains the standalone behavior
of reusing an existing instance. Each app has one server and one progress helper;
**Open both apps** consequently has two launch sessions, not one.

When the sibling contains the shared watchdog implementation, **Auto-stop**
in either app controls a single Codespace-wide policy. The first generation,
narration or render task arms the timer. Actual progress in either app resets
it; after 15 minutes with no progress in both, all open project tabs must
confirm a disk autosave before shutdown. Polling, retries and image-stage
percentages never reset it. Clean disconnected tabs expire after 45 seconds;
failed saves remain blockers. New project changes autosave to disk with
**Saved to disk** shown in the toolbar. Autosave creates a working copy of the
bundled sample rather than overwriting it.

The shared timer is stored outside both checkouts in
`../.runtime/codespace-autostop.sqlite3`, with no project content or API keys.
Either server can monitor it when the other is stopped. This integration reuses
the sibling Lost NEET's watchdog and conflict-safe autosave helpers; outside
this combined Codespace, Station Elsewhere still runs normally without the
shared policy. Keep the launcher terminal open: it prints real progress to help
avoid GitHub's separate idle timeout. Auto-stop cannot override GitHub's own
idle timeout, quotas, maximum running duration or infrastructure stops.

---

## Connected services and cleanup in this workspace

`start.sh` loads the private `~/.config/storycraft/env` file if it exists (for example
`CARTESIA_API_KEY=...`), so keys never need to be in the repository. Restart Station Elsewhere after changing the Cartesia key there. Codex uses the existing machine-wide ChatGPT sign-in; check it with
`codex login status`. New narration and image generation consume the corresponding
service allowance; setup checks do not generate media.

**Post to Facebook...** is available beside Export and in the export-complete
dialog. It lists only this app's exported MP4s and supports posting now, scheduling,
choosing a thumbnail frame, title/description and removing Facebook's auto-generated
captions. Posting happens only when you press the final Post/Schedule button.

Facebook posts go **only to the Page named "Station Elsewhere"** (change the name with
`facebook_page_name` in `config.json`; to also lock it to one page ID, for example when two of your
pages share a name, set `facebook_page_id`). Press **Connect page…** in either Facebook dialog and
paste the Page ID and a Page access token (permissions `pages_manage_posts`, `pages_read_engagement`,
`pages_show_list`; a Page token made from a long-lived user token does not expire). They are checked with Facebook and saved privately in
`~/.config/station-elsewhere/facebook.env`; a token for any other page is refused, and
the page name is checked again before every post. Lost NEET's `FB_*` variables are
never used.

**Schedule: two reels a day at 3:00 AM and 9:00 AM Philippine time.** The next free
slot follows the latest post known from `facebook_posts.json` (every post made from this
app) and from the Page itself (its scheduled posts and recent videos): if Nov 8 3 AM is
taken the next is Nov 8 9 AM, then Nov 9 3 AM, and so on. Times are shown and entered in
Philippine time and stay editable. After **🎬 Auto reels**, **📅 Schedule all on
Facebook** lists the unposted exports with that run's reels already ticked and given
consecutive slots; one click uploads them one after another. Each post's description is the
reel's title statement followed by `#quotes #deepthoughts #selfreflection` (editable). The reel's entry in
`reels_history.md` records when it was scheduled.

**Free up space...** in the toolbar previews cleanup candidates before asking for
permanent deletion. Saved/current project references and recently created files
are protected. Only this app's folders are scanned; the sibling Lost NEET data
is never cleaned. Narration caches and exports are optional, unchecked categories:
back them up first, since regenerating narration may cost money. Cleanup is blocked
during narration, rendering, image generation or Facebook uploads.

Both features reuse the sibling Lost NEET helper implementations but bind them
to Station Elsewhere's configuration and folders. They require that sibling
checkout; standalone copies hide the controls and return explicit service errors
if those endpoints are requested.

---

## Windows

Station Elsewhere is a small app that runs on your own Windows PC. You give it your story's images in
order and paste the narration. It speaks the narration with **Kokoro** (a free text-to-speech voice
running on your PC), times every scene to the speech, adds moving camera shots and burned-in
subtitles, and exports a vertical 9:16 MP4 (1080 × 1920) ready for YouTube Shorts, Facebook Reels
and TikTok.

No editing timeline and no accounts. There are no paid APIs and no cloud: everything is processed
locally and works offline once it's set up.

---

## How it works

```
images + script ──► split script into scenes ──► Kokoro speaks each scene
                                                        │
                    MP4 ◄── FFmpeg renders ◄── scene length = length of its narration
                             (motion, transitions,      subtitles timed to the spoken words
                              subtitles, watermark)
```

1. **Scenes**: each image is one scene, shown in order (`scene_2` comes before `scene_10`).
2. **Script → scenes**: paste the whole story once. If it has labels like `SCENE 1`, each section goes
   to its numbered scene. If not, Station Elsewhere divides it across your images at paragraph and
   sentence breaks, and you review and adjust the split before applying it.
3. **Narration**: Kokoro reads each scene. Station Elsewhere measures the real audio, so every image stays
   on screen exactly as long as its narration, plus a short pause. You never type durations.
4. **Subtitles**: Kokoro reports when every word is spoken, so short captions (about 3–7 words)
   appear and disappear in sync with the voice.
5. **Video**: FFmpeg animates each image with a slow zoom or pan, shows it whole without cropping,
   adds cuts or fades, burns in subtitles and your watermark, and encodes the MP4 (H.264 / AAC).

Narration is cached per sentence. Changing images, subtitle style, motion or timing never makes
Kokoro speak again; editing a sentence only re-speaks that sentence.

---

## What you need

| | |
|---|---|
| **Windows 10 / 11** | |
| **Python 3.10 or newer** | [python.org/downloads](https://www.python.org/downloads/) (keep the default options; tkinter is included) |
| **Kokoro**, installed in that Python | Either kind works, see below |
| **FFmpeg** | In a terminal: `winget install Gyan.FFmpeg` |
| **Codex** (optional, for scene images) | ChatGPT Codex app or `npm i -g @openai/codex`, then `codex login` → *Sign in with ChatGPT* |
| **Git** (to download / update) | [git-scm.com](https://git-scm.com/), or use GitHub's *Download ZIP* button |

**Kokoro**: Station Elsewhere uses the Kokoro you already have. It does not download its own.

- **hexgrad/Kokoro-82M (PyTorch)**: installed with `pip install kokoro` (which brings `torch`), with
  the model downloaded at least once (it lands in the Hugging Face cache), or in a folder containing
  `kokoro-v1_0.pth`, `config.json` and `voices\*.pt`.
- **kokoro-onnx**: `pip install kokoro-onnx` plus the files `kokoro-v1.0.onnx` and `voices-v1.0.bin`.

---

## Getting started (after cloning)

**1. Get the code**

```
git clone https://github.com/<your-account>/Station_Elsewhere.git
```

(or on GitHub: **Code → Download ZIP**, then unzip it)

**2. Double-click `start.bat`** in the `Station_Elsewhere` folder.

A black window opens. The **first start needs internet** and takes about a minute. It will:

- find the Python on your PC that has Kokoro installed;
- create Station Elsewhere's own environment (`.venv`) that reuses that Python's packages;
- install the few small packages Station Elsewhere needs (FastAPI, Uvicorn);
- find your Kokoro model and FFmpeg automatically;
- open the app in your browser at **http://127.0.0.1:8765**.

**3. Check the top-right corner** of the app. It should show **Kokoro (PyTorch) · 54 voices** (or
*Kokoro (ONNX)*) and **FFmpeg** with green dots. If so, you're ready.

From then on, just double-click `start.bat`. Keep the black window open while you work;
closing it stops Station Elsewhere.

### If something isn't found

- **"Setup needed" → Locate Kokoro…**: pick your model file: `kokoro-v1_0.pth` (Kokoro-82M) or
  `kokoro-v1.0.onnx`. A downloaded Kokoro-82M usually lives in
  `C:\Users\<you>\.cache\huggingface\hub\models--hexgrad--Kokoro-82M\snapshots\<id>\`.
- **"Setup needed" → Locate FFmpeg…**: install FFmpeg (`winget install Gyan.FFmpeg`) and restart
  Station Elsewhere, or pick `ffmpeg.exe`.
- **The black window says no Python has Kokoro**: your Kokoro is probably inside a separate
  virtual environment. Create a text file named `python_path.txt` next to `start.bat` containing
  the full path of that environment's Python, for example
  `C:\Users\you\kokoro-env\Scripts\python.exe`, then start again.
- **"Python 3.10 or newer was not found"**: install Python from python.org.

Your choices are remembered in `config.json` (created on your PC, never uploaded to GitHub).

### Updating

```
git pull
```

Then close the black window, double-click `start.bat` again, and press **Ctrl+F5** in the browser.
(The app also shows a banner when it notices its files changed and it needs a restart.)

---

## Making a video

1. **Add your images**: drag them onto the scene list (or click the drop area). PNG, JPG and WebP
   are supported. They're sorted by file name.
2. **Import full script**: paste your whole narration and click **Auto assign to scenes**.
   - *With labels* (`SCENE 1`, `Scene 01:`, `[Scene 1]`, `Scene 1 — Apartment`): each section goes
     to that scene and the labels aren't spoken. You get a warning if the count doesn't match the
     number of images.
   - *Without labels*: the story is split into one part per image, preferring paragraph and
     sentence breaks. **This is an estimate**: Station Elsewhere does not look at the pictures, so check
     each image against its text in the review. Use **▼ / ▲** to move a sentence to the
     neighbouring scene, or edit the text directly. The review confirms that every word was used
     exactly once, in order.
   - Nothing changes until you click **Apply**, and **Undo** is offered right after.
3. **Generate narration**: Kokoro speaks all scenes, and each scene shows its length. Use
   **▶ Narration** on a scene to hear just that one; pick the voice and speed under *Settings → Voice*
   (**Test voice** plays a sample).
4. **Preview video**: a quick half-size render with the real timing, motion, subtitles and
   transitions. Play, pause and seek it in the middle column.
5. **Export MP4**: the full-quality video goes to the output folder set under *Settings → Export*
   (default: the `exports` folder).

You can still edit any scene's text box by hand, reorder scenes by dragging **⋮⋮** (or ↑ ↓),
replace an image by clicking its thumbnail, and duplicate or delete scenes.

**Save / Open** keep a project as a `.storycraft.json` file (default folder: `projects`). Your
current work is also autosaved in the browser. Uploaded images are copied into the `media` folder;
your original files are never changed.

---

## Auto reels (one click: ChatGPT script → images → export)

**🎬 Auto reels** (top bar) makes several finished reels in a row:

1. Choose **Reels to make** and a **Genre** (Life lessons by default; also Nostalgia, Comfort, Quiet
   encouragement, Observation / reflection, or Random for the plan's mix) and press **▶ Plan & produce**. ChatGPT (GPT-6.1 Terra, high effort, through the
   signed-in Codex CLI) writes that many new scripts, then stops. Its short prompt has three rules:
   everyone can relate (rule 1), meaningful and impactful in the chosen genre (a strong first line and a
   payoff at the end), and 20–25 seconds. It also gets a one-line summary of every reel in
   `reels_history.md` so it doesn't repeat them. **✨ Plan only** stops here so you can review or edit the
   scripts, then **▶ Produce these scripts**. You can also paste your own scripts.
2. Each reel then goes in turn: its script is imported as its own project (the title statement is the
   project and video name; every following line is one scene), **all of its images are generated in
   parallel**, the project is saved to `projects/`, and it's exported (narration + video) to
   `exports/<title>.mp4`. Then the next reel starts. Images that fail are retried once; a reel that still
   fails is skipped and the next one continues. **■ Stop** cancels the current step.

Script format (blank line between reels):

```
Some days are meant to be survived
First scene line
Second scene line
...                (8 or more scene lines)
```

The program, not ChatGPT, assigns each reel's genre (the one you picked; with Random, observation 40% ·
nostalgia 25% · comfort 20% · encouragement 15%, balanced across the whole history), storytelling
structure and mood, never repeating a structure or mood back to back and alternating deeper and lighter
moods. ChatGPT chooses the subject within that genre. Scripts are checked for 8–10 lines and 37–42
words (aim 40: about 20–25 seconds with the current voice and pauses, which the app never changes) and a title not used before; ChatGPT is asked to fix
anything that fails. Every planned reel is appended to `reels_history.md` with its topic, motif,
setting, and later its export path and real duration, so future batches pick new topics. Edit or
delete entries there freely. Keep the browser tab open while it runs. Planning takes about a minute;
each reel takes about 3–5 minutes.

---

## Settings at a glance

- **Format**: always vertical 9:16 (Shorts / Reels), in 1080p or 720p (Export settings).
- **Voice**: any installed Kokoro voice, speed 0.5×–2×.
- **Subtitles**: on/off (**CC** switch under the preview), font, size, color, outline, shadow,
  bold, UPPERCASE, words per caption, and position: X, Y and caption width. You can also drag the
  caption on the preview. **✥ Adjust positions** lets you drag it on a still frame after rendering.
- **Scene images (ChatGPT)**: generate a scene's picture with your ChatGPT plan. **🎨 Image** on a
  scene opens a window with a prompt based on that scene's narration (edit it freely), **Generate**,
  then **Use this image** (Undo available). **🎨 Generate missing images** fills every scene that has
  no image, one after another. To keep your series' look:
  - **Art style = the `artstyle` folder**: put images with the look you want into the `artstyle`
    folder in Station Elsewhere's folder (*Settings → Scene images → Open folder*). Your best existing scenes
    work well. Up to 3 are sent with every image, rotating through all of them. Optionally add
    `artstyle\rules.txt` with your own rules, one per line (e.g. *Any blood is drawn bright pink.*).
    A flat 2D webcomic style description and a *never draw* list (no anime, no realistic humans,
    no dramatic lighting…) are built in. Images and rules in the folder stay on your PC; they're not
    uploaded to GitHub.
  - **Characters**: each with a name, one or two pictures and a short description of their look.
    The **main character** is in every scene (the "you" of the story). Others are included when the
    narration mentions their name, and you can tick or untick them per scene in the image window.
  - Every attached picture is labeled in the prompt (style example / which character), so ChatGPT
    knows what to copy. Characters are saved once for **all your projects** (`series_style.json`). This uses the **Codex app / Codex CLI signed in with
  your ChatGPT account**: no API key, and it counts against your plan's Codex limits. About a minute
  per image. Setup: install the ChatGPT Codex app (or `npm i -g @openai/codex`), run `codex login`,
  and choose *Sign in with ChatGPT*. The section shows *Connected* when it's ready.
- **Watermark**: your text (e.g. `@YourChannel`), bottom right by default. Snap it to any corner,
  edge or the center with the 3×3 grid, fine-tune with X / Y, or drag it on the preview. Size,
  opacity, font, color and outline are adjustable.
- **Motion & transitions**: static, slow zoom in/out, slow pan left/right, or random subtle movement
  (per scene or for all). Fit mode is *Contain*: the whole image is shown with black bars if
  needed, never stretched. The picture stays fixed in place and the **camera moves inside it**, like a
  Ken Burns move. During a move a thin band at the picture's edges is briefly out of view (up to
  about 7–8%). A zoom-in starts on the whole picture and a zoom-out ends on it. *Cover* fills the
  frame and crops. Transitions: cut,
  crossfade or fade through black. They always happen in the pause between scenes, so speech is
  never cut off.
- **Timing**: pause between scenes, pause between sentences, lead-in, hold at the end.
- **Export**: output folder, file name, frame rate (24/30/60), quality, and **Clear video cache**.

---

## Folder layout

```
start.bat            double-click to run (finds Python + Kokoro, see tools/launch.py)
app/                 the app: web server (main.py), Kokoro narration (tts.py),
                     subtitles (captions.py), video rendering (render.py), interface (static/)
tools/               launcher and share-zip builder
sample/              a few test images
make_share_zip.bat   optional: build dist\StoryCraft.zip to hand over without GitHub

Created on your PC (not on GitHub):
.venv/               Station Elsewhere's Python environment (rebuilt automatically if needed)
config.json          where Kokoro and FFmpeg are on this PC (and API keys: never committed)
exports/             your videos
workspace/           caches and previews; safe to delete, or use *Clear video cache*
                     (workspace/cache/tts and /audio = narration cache, private: never committed)

Linux / Codespaces:
start.sh, tools/setup_linux.sh, .devcontainer/   setup + launcher (FFmpeg goes into ffmpeg/)
fonts/               free look-alikes of Windows fonts used when rendering on Linux
```
