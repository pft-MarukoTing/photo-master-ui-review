#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAS_ROOT="${NAS_ROOT:-$HOME/mt_nas/AINail/datasets}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [--dry-run]

Commands:
  pull    Pull latest state from NAS into the local copy
  push    Push local add/move/edit changes to the NAS
          (files edited in place are locked, copied, and unlocked automatically)

  --dry-run   Print what would happen without changing any files

Environment:
  NAS_ROOT   Path to the mounted NAS folder (default: $HOME/mt_nas/AINail/datasets)
EOF
}

cmd="${1:-}"
apply_flag="--apply"
if [ "${2:-}" = "--dry-run" ]; then
  apply_flag=""
fi

case "$cmd" in
  pull)
    python3 "$SCRIPT_DIR/pull.py" --nas-root "$NAS_ROOT" $apply_flag
    ;;
  push)
    python3 "$SCRIPT_DIR/push.py" --nas-root "$NAS_ROOT" $apply_flag
    ;;
  *)
    usage
    exit 1
    ;;
esac
