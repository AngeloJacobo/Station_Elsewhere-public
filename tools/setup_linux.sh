#!/usr/bin/env bash
# StoryCraft one-time setup for Linux / GitHub Codespaces. Safe to run again: finished steps are skipped.
#   - FFmpeg (static build with libass + fontconfig, into ./ffmpeg/bin)
#   - Python environment (.venv) with the app's packages (no Kokoro: narration uses Cartesia)
#   - optional: the Codex CLI for "Scene images (ChatGPT)"
# Skip the Codex CLI with STORYCRAFT_SKIP_CODEX=1.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

say() { printf '\033[1;36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup] %s\033[0m\n' "$*"; }

SUDO=""
if [ "$(id -u)" != "0" ] && command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then SUDO="sudo"; fi

download() {   # url target [sha256]
  local url="$1" target="$2" sum="${3:-}"
  mkdir -p "$(dirname "$target")"
  curl -fL --retry 3 --progress-bar -o "$target.part" "$url" || { rm -f "$target.part"; return 1; }
  if [ -n "$sum" ] && ! echo "$sum  $target.part" | sha256sum -c --status; then
    warn "Checksum mismatch for $(basename "$target")"; rm -f "$target.part"; return 1
  fi
  mv "$target.part" "$target"
}

# ---- 1. system tools ---------------------------------------------------------------------------------
apt_pkgs=()
command -v curl >/dev/null 2>&1 || apt_pkgs+=(curl)
command -v xz >/dev/null 2>&1 || apt_pkgs+=(xz-utils)
command -v fc-list >/dev/null 2>&1 || apt_pkgs+=(fontconfig)
[ -e /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf ] || apt_pkgs+=(fonts-liberation)
python3 -c "import venv, ensurepip" >/dev/null 2>&1 || apt_pkgs+=(python3-venv)
if [ ${#apt_pkgs[@]} -gt 0 ] && command -v apt-get >/dev/null 2>&1 && { [ -n "$SUDO" ] || [ "$(id -u)" = "0" ]; }; then
  say "Installing system packages: ${apt_pkgs[*]}"
  $SUDO apt-get update -qq >/dev/null 2>&1
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${apt_pkgs[@]}" >/dev/null 2>&1 \
    || warn "Some system packages could not be installed (${apt_pkgs[*]}); continuing."
fi

# ---- 2. FFmpeg ---------------------------------------------------------------------------------------
has_ass() { "$1" -hide_banner -filters 2>/dev/null | grep -qE '^ +[TSC.]{2,3} +ass +'; }
if [ -x "$ROOT/ffmpeg/bin/ffmpeg" ] && [ -x "$ROOT/ffmpeg/bin/ffprobe" ]; then
  : # already installed
else
  case "$(uname -m)" in aarch64|arm64) arch="linuxarm64" ;; *) arch="linux64" ;; esac
  say "Downloading FFmpeg (static build, $arch)…"
  tmp="$(mktemp -d)"
  if download "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-$arch-gpl.tar.xz" "$tmp/ffmpeg.tar.xz" \
     && tar -xJf "$tmp/ffmpeg.tar.xz" -C "$tmp" --wildcards '*/bin/ffmpeg' '*/bin/ffprobe'; then
    mkdir -p "$ROOT/ffmpeg/bin"
    mv "$tmp"/ffmpeg-*/bin/ffmpeg "$tmp"/ffmpeg-*/bin/ffprobe "$ROOT/ffmpeg/bin/"
    chmod +x "$ROOT/ffmpeg/bin/ffmpeg" "$ROOT/ffmpeg/bin/ffprobe"
    say "FFmpeg: $("$ROOT/ffmpeg/bin/ffmpeg" -hide_banner -version | head -n1)"
  elif ! command -v ffmpeg >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    warn "FFmpeg download failed; installing the system FFmpeg instead."
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null 2>&1 || warn "Could not install FFmpeg."
  fi
  rm -rf "$tmp"
fi
if [ -x "$ROOT/ffmpeg/bin/ffmpeg" ] && ! has_ass "$ROOT/ffmpeg/bin/ffmpeg"; then
  warn "This FFmpeg has no subtitle (libass) support: captions can't be burned in."
fi

# ---- 3. Python environment -----------------------------------------------------------------------------
PY=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then warn "Python 3.10 or newer was not found."; exit 1; fi
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  [ -d "$ROOT/.venv" ] && rm -rf "$ROOT/.venv"      # e.g. a Windows .venv copied over
  say "Creating the Python environment (.venv) with $($PY --version)…"
  "$PY" -m venv "$ROOT/.venv" || { warn "Could not create .venv"; exit 1; }
fi
VPY="$ROOT/.venv/bin/python"
REQ="$ROOT/requirements-linux.txt"
STAMP="$ROOT/.venv/.storycraft-requirements"
if [ ! -f "$STAMP" ] || ! cmp -s "$REQ" "$STAMP"; then
  say "Installing Python packages (one time)…"
  "$VPY" -m pip install --disable-pip-version-check -q --upgrade pip >/dev/null 2>&1
  if ! "$VPY" -m pip install --disable-pip-version-check -q -r "$REQ"; then
    warn "Pinned versions failed; installing the latest versions instead."
    "$VPY" -m pip install --disable-pip-version-check -q fastapi uvicorn python-multipart soundfile numpy \
      || { warn "Installing Python packages failed. Check the internet connection and run ./start.sh again."; exit 1; }
  fi
  cp "$REQ" "$STAMP"
fi

# ---- 4. Kokoro is not installed here: narration uses Cartesia (cached clips in workspace/cache/tts, plus your
#         CARTESIA_API_KEY for new lines). On Windows, start.bat keeps using your local Kokoro as before.

# ---- 5. Codex CLI (optional: generating scene images with your ChatGPT plan) -----------------------------
if [ "${STORYCRAFT_SKIP_CODEX:-}" != "1" ] && ! command -v codex >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  say "Installing the Codex CLI (for scene images; sign in later with: codex login --device-auth)…"
  npm install -g @openai/codex >/dev/null 2>&1 || $SUDO npm install -g @openai/codex >/dev/null 2>&1 \
    || warn "Codex CLI not installed (only needed for generating new scene images)."
fi

say "Setup complete."
