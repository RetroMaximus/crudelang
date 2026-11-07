#!/usr/bin/env bash
# push_to_git.sh - commit and push crudelang from the dev machine.
#
# Usage:
#   bash push_to_git.sh "commit message"
#   bash push_to_git.sh "commit message" git@github.com:RetroMaximus/crudelang.git

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Self-heal CRLF if this file was checked out on Windows.
# ---------------------------------------------------------------------------
if grep -q $'\r' "$0" 2>/dev/null; then
  echo "[push_to_git] CRLF detected, fixing line endings"
  sed -i 's/\r$//' "$0"
  exec "$0" "$@"
fi

COMMIT_MSG="${1:-update}"
REPO_URL="${2:-git@github.com:RetroMaximus/crudelang.git}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/id_ed25519_crudelang}"
BRANCH="${BRANCH:-main}"

echo "[push_to_git] commit: $COMMIT_MSG"
echo "[push_to_git] remote: $REPO_URL"

# ---------------------------------------------------------------------------
# 1. SSH key
# ---------------------------------------------------------------------------
if [ ! -f "$KEY_PATH" ]; then
  echo "[push_to_git] no SSH key at $KEY_PATH"
  echo "[push_to_git] generating one now"
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -C "retromaximus@github" -f "$KEY_PATH" -N ""
  echo
  echo "[push_to_git] -------------------------------------------------------------------"
  echo "[push_to_git] PUBLIC KEY - paste into https://github.com/settings/keys"
  echo "[push_to_git] -------------------------------------------------------------------"
  cat "$KEY_PATH.pub"
  echo "[push_to_git] -------------------------------------------------------------------"
  echo
  read -r -p "[push_to_git] press Enter once the key is added to GitHub: " _
fi

chmod 600 "$KEY_PATH"
chmod 644 "$KEY_PATH.pub"

# ---------------------------------------------------------------------------
# 2. SSH config
# ---------------------------------------------------------------------------
SSH_CONFIG="$HOME/.ssh/config"
mkdir -p "$HOME/.ssh"
touch "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

if ! grep -q "IdentityFile $KEY_PATH" "$SSH_CONFIG" 2>/dev/null; then
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
# 4. SSH test
# ---------------------------------------------------------------------------
if ! ssh -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  echo "[push_to_git] WARNING: SSH test did not confirm authentication"
fi

# ---------------------------------------------------------------------------
# 5. Repo + remote
# ---------------------------------------------------------------------------
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "[push_to_git] initializing repo"
  git init
fi

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi

git branch -M "$BRANCH"

# ---------------------------------------------------------------------------
# 6. Commit
# ---------------------------------------------------------------------------
git add -A

if git diff --cached --quiet; then
  echo "[push_to_git] nothing to commit"
else
  git commit -m "$COMMIT_MSG"
fi

# ---------------------------------------------------------------------------
# 7. Pull then push
# ---------------------------------------------------------------------------
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git pull --rebase origin "$BRANCH"
fi

git push -u origin "$BRANCH"

echo "[push_to_git] done"