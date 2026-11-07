#!/usr/bin/env bash
# pull_latest.sh - clone or update eventstream on a headless Raspberry Pi.
# Safe to run repeatedly. Generates an SSH key only if missing.
# Bootstraps venv, dependencies, env file, layout, and health check.

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Self-heal CRLF if this file was checked out on Windows.
# ---------------------------------------------------------------------------
if grep -q $'\r' "$0" 2>/dev/null; then
  echo "[pull_latest] CRLF detected, fixing line endings"
  sed -i 's/\r$//' "$0"
  exec "$0" "$@"
fi

REPO_URL="${1:-git@github.com:RetroMaximus/eventstream.git}"
TARGET_DIR="${2:-$HOME/eventstream}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/id_ed25519_eventstream}"
GIT_EMAIL="${GIT_EMAIL:-retromaximus@github}"
ENV_DIR="${ENV_DIR:-$HOME/.config/eventstream}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/eventstream.env}"
ENV_TEMPLATE="${ENV_TEMPLATE:-.env.example}"

# ===========================================================================
# 0.5 Self-update: if this file differs from origin/main, reset it.
# Only affects tracked files, not var/ or untracked generated files.
# ===========================================================================
if [ -d "$TARGET_DIR/.git" ]; then
  _repo="$TARGET_DIR"

  # Fetch quietly; ignore network errors so the rest of the script can proceed.
  git -C "$_repo" fetch --quiet origin main 2>/dev/null || true

  # If this script itself is dirty, reset it and re-exec.
  if ! git -C "$_repo" diff --quiet -- pull_latest.sh 2>/dev/null; then
    echo "[pull_latest] local changes to pull_latest.sh detected, resetting"
    git -C "$_repo" checkout -- pull_latest.sh
    exec "$0" "$@"
  fi

  # If the repo is behind and can fast-forward, pull and re-exec.
  _local_sha="$(git -C "$_repo" rev-parse HEAD 2>/dev/null || true)"
  _remote_sha="$(git -C "$_repo" rev-parse origin/main 2>/dev/null || true)"
  if [ -n "$_local_sha" ] && [ -n "$_remote_sha" ] && [ "$_local_sha" != "$_remote_sha" ]; then
    if git -C "$_repo" merge-base --is-ancestor "$_local_sha" "$_remote_sha"; then
      echo "[pull_latest] fast-forwarding repository"
      git -C "$_repo" checkout -- . 2>/dev/null || true
      git -C "$_repo" pull --ff-only
      exec "$0" "$@"
    fi
  fi
fi
# ===========================================================================
# End self-update.
# ===========================================================================

# Resolve TARGET_DIR to an absolute path so venv paths are unambiguous.
if [ -d "$TARGET_DIR" ]; then
  TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
else
  TARGET_DIR="$(cd "$(dirname "$TARGET_DIR")" && pwd)/$(basename "$TARGET_DIR")"
fi

VENV_DIR="${VENV_DIR:-$TARGET_DIR/.venv}"

echo "[pull_latest] repo:   $REPO_URL"

# Resolve TARGET_DIR to an absolute path so venv paths are unambiguous.
if [ -d "$TARGET_DIR" ]; then
  TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
else
  # If it does not exist yet, resolve its parent and rebuild the path.
  TARGET_DIR="$(cd "$(dirname "$TARGET_DIR")" && pwd)/$(basename "$TARGET_DIR")"
fi

VENV_DIR="${VENV_DIR:-$TARGET_DIR/.venv}"

echo "[pull_latest] repo:   $REPO_URL"
echo "[pull_latest] target: $TARGET_DIR"
echo "[pull_latest] key:    $KEY_PATH"
echo "[pull_latest] venv:   $VENV_DIR"
echo "[pull_latest] env:    $ENV_FILE"

# ---------------------------------------------------------------------------
# 1. SSH key - only generate if missing
# ---------------------------------------------------------------------------
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [ ! -f "$KEY_PATH" ]; then
  echo "[pull_latest] generating SSH key at $KEY_PATH"
  ssh-keygen -t ed25519 -C "$GIT_EMAIL" -f "$KEY_PATH" -N ""
  echo
  echo "[pull_latest] ---------------------------------------------------------------"
  echo "[pull_latest] PUBLIC KEY - paste into https://github.com/settings/keys"
  echo "[pull_latest] ---------------------------------------------------------------"
  cat "$KEY_PATH.pub"
  echo "[pull_latest] ---------------------------------------------------------------"
  echo
  read -r -p "[pull_latest] press Enter once the key is added to GitHub: " _
else
  echo "[pull_latest] SSH key already exists, skipping generation"
fi

chmod 600 "$KEY_PATH"
chmod 644 "$KEY_PATH.pub"

# ---------------------------------------------------------------------------
# 2. SSH config
# ---------------------------------------------------------------------------
SSH_CONFIG="$HOME/.ssh/config"
touch "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

if ! grep -q "IdentityFile $KEY_PATH" "$SSH_CONFIG" 2>/dev/null; then
  echo "[pull_latest] adding github.com host block to $SSH_CONFIG"
  cat >> "$SSH_CONFIG" <<EOF

Host github.com
    HostName github.com
    User git
    IdentityFile $KEY_PATH
    IdentitiesOnly yes
EOF
fi

# ---------------------------------------------------------------------------
# 3. ssh-agent
# ---------------------------------------------------------------------------
if [ -z "${SSH_AUTH_SOCK:-}" ]; then
  eval "$(ssh-agent -s)" >/dev/null
fi
ssh-add "$KEY_PATH" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. SSH connectivity test (informational only)
# ---------------------------------------------------------------------------
echo "[pull_latest] testing SSH to github.com"
SSH_OUTPUT="$(ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -T git@github.com 2>&1 || true)"
if printf '%s' "$SSH_OUTPUT" | grep -q "successfully authenticated"; then
  echo "[pull_latest] SSH OK"
else
  echo "[pull_latest] SSH test inconclusive, continuing"
fi

resolve_generated_collisions() {
  local target_dir="$1"

  if [ ! -d "$target_dir/.git" ]; then
    return 0
  fi

  cd "$target_dir"

  # Fetch first so we know what origin/main contains.
  git fetch --quiet origin main || true

  # List of untracked files that also exist in origin/main.
  # git diff --name-only with --diff-filter=A shows files added remotely.
  # Intersect with git ls-files --others --exclude-standard (untracked local).
  local untracked remote_added
  untracked="$(git ls-files --others --exclude-standard || true)"
  remote_added="$(git diff --name-only --diff-filter=A HEAD..origin/main 2>/dev/null || true)"

  [ -z "$untracked" ] && return 0
  [ -z "$remote_added" ] && return 0

  local collisions
  collisions="$(comm -12 <(printf '%s\n' "$untracked" | sort -u) \
                        <(printf '%s\n' "$remote_added" | sort -u))"

  [ -z "$collisions" ] && return 0

  echo "[pull_latest] resolving generated file collisions:"
  while IFS= read -r path; do
    [ -z "$path" ] && continue

    local is_generated=0
    if [ -f "$path" ]; then
      # Empty file
      if [ ! -s "$path" ]; then
        is_generated=1
      fi
      # Standard installer docstring
      if [ "$(wc -l < "$path" 2>/dev/null || echo 0)" -le 2 ] && \
         head -n 1 "$path" 2>/dev/null | grep -q '"""eventstream package."""'; then
        is_generated=1
      fi
      # .gitkeep placeholder
      case "$path" in
        *.gitkeep) is_generated=1 ;;
      esac
    fi

    if [ "$is_generated" -eq 1 ]; then
      echo "[pull_latest]   removing generated file: $path"
      rm -f "$path"
    else
      echo "[pull_latest]   WARNING: collision with local file: $path"
      echo "[pull_latest]   moving to ${path}.pi-backup"
      mv "$path" "${path}.pi-backup"
    fi
  done <<< "$collisions"
}

# ---------------------------------------------------------------------------
# 5. Clone or pull
# ---------------------------------------------------------------------------
if [ ! -d "$TARGET_DIR/.git" ]; then
  echo "[pull_latest] cloning"
  git clone "$REPO_URL" "$TARGET_DIR"
else
  echo "[pull_latest] pulling latest"
  resolve_generated_collisions "$TARGET_DIR"
  cd "$TARGET_DIR"
  git pull --ff-only
fi
cd "$TARGET_DIR"

# ---------------------------------------------------------------------------
# 6. venv
# ---------------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  echo "[pull_latest] creating venv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  echo "[pull_latest] venv already exists at $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ---------------------------------------------------------------------------
# 7. dependencies
# ---------------------------------------------------------------------------
if [ -f requirements.lock.txt ]; then
  REQ_FILE="requirements.lock.txt"
else
  REQ_FILE="requirements.txt"
fi

if [ ! -f "$REQ_FILE" ]; then
  echo "[pull_latest] ERROR: $REQ_FILE missing"
  exit 1
fi

echo "[pull_latest] installing dependencies from $REQ_FILE"
python -m pip install --upgrade pip wheel \
  --timeout 60 --retries 5 --progress-bar off
python -m pip install -r "$REQ_FILE" \
  --timeout 60 --retries 5 --progress-bar off

# ---------------------------------------------------------------------------
# 8. env file
# ---------------------------------------------------------------------------
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

if [ ! -f "$ENV_TEMPLATE" ]; then
  echo "[pull_latest] ERROR: $ENV_TEMPLATE missing from repo"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_TEMPLATE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "[pull_latest] created $ENV_FILE"

  MASTER_KEY="$(python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')"
  SESSION_SECRET="$(python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')"

  python - "$ENV_FILE" "$MASTER_KEY" "$SESSION_SECRET" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
master = sys.argv[2]
session = sys.argv[3]

text = path.read_text(encoding="utf-8")
text = text.replace("EVENTSTREAM_MASTER_KEY=REPLACE_ME",
                    f"EVENTSTREAM_MASTER_KEY={master}")
text = text.replace("EVENTSTREAM_SESSION_SECRET=REPLACE_ME",
                    f"EVENTSTREAM_SESSION_SECRET={session}")
path.write_text(text, encoding="utf-8")
PY

  echo "[pull_latest] wrote generated secrets to $ENV_FILE"
  echo "[pull_latest] back this file up; losing the master key loses encrypted data"
else
  echo "[pull_latest] $ENV_FILE already exists, leaving as is"
fi

export EVENTSTREAM_ENV_FILE="$ENV_FILE"

# ---------------------------------------------------------------------------
# 9. layout
# ---------------------------------------------------------------------------
python install_init.py

# ---------------------------------------------------------------------------
# 10. health check
# ---------------------------------------------------------------------------
echo "[pull_latest] running health check"
python main.py --check || {
  echo "[pull_latest] health check failed, see output above"
  exit 1
}

echo
echo "[pull_latest] done"
echo "[pull_latest] activate with: source $VENV_DIR/bin/activate"
echo "[pull_latest] env file:      $ENV_FILE"
echo "[pull_latest] run with:      python main.py"