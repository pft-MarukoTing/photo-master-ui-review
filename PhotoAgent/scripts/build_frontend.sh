#!/usr/bin/env bash
# Build the chat UI (frontend-chat) into frontend/chat/
# Uses the host's npm if there is one, otherwise runs npm inside a node Docker image.
# Usage: bash scripts/build_frontend.sh

# When sourced, run in a child bash so `set -e` can't close the caller's shell
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  bash "${BASH_SOURCE[0]}" "$@"
  return $?
fi

set -e

NODE_IMAGE="${NODE_IMAGE:-node:20}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
APP_DIR="$ROOT_DIR/frontend-chat"

if command -v npm >/dev/null 2>&1; then
  run_npm() { (cd "$APP_DIR" && npm "$@"); }
else
  echo "==> npm not found on host, using Docker image $NODE_IMAGE"
  MOUNTS=(-v "$ROOT_DIR:$ROOT_DIR")
  # node_modules may be a symlink to another checkout; mount its real path too
  if [ -L "$APP_DIR/node_modules" ]; then
    MODULES_DIR="$(readlink -f "$APP_DIR/node_modules")"
    MOUNTS+=(-v "$MODULES_DIR:$MODULES_DIR")
  fi
  run_npm() {
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
      "${MOUNTS[@]}" -w "$APP_DIR" "$NODE_IMAGE" npm "$@"
  }
fi

if [ ! -e "$APP_DIR/node_modules" ]; then
  echo "==> Installing dependencies..."
  run_npm ci
fi

echo "==> Building frontend-chat..."
run_npm run build

echo "==> Done. Changes under frontend/chat:"
git -C "$ROOT_DIR" status --short frontend/chat
