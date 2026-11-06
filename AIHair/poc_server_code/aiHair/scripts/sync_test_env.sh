#!/usr/bin/env bash
# Check C2PA files, sync tests into running Docker container, and run pytest
# Usage: bash scripts/sync_test_env.sh

set -e

CONTAINER="aihair"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Checking C2PA signing files in container..."
docker exec "$CONTAINER" sh -c '
    for file in /workspace/c2pa/cert.pem /workspace/c2pa/key.pem; do
        if [ ! -f "$file" ]; then
            echo "Error: missing C2PA signing file: $file" >&2
            exit 1
        fi
    done
'

echo "==> Syncing tests/ into container..."
docker cp "$SCRIPT_DIR/tests" "$CONTAINER:/app/tests"

echo "==> Installing pytest in container..."
docker exec "$CONTAINER" python3 -m pip install pytest

echo "==> Running pytest..."
docker exec "$CONTAINER" python3 -m pytest /app/tests -q

echo "==> Done. Connect with: docker exec -it $CONTAINER bash"
