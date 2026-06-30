#!/usr/bin/env bash
# Sync tests into running Docker container and run pytest
# Usage: bash scripts/sync_test_env.sh

set -e

CONTAINER="photo-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Syncing test files into container..."
for f in "$SCRIPT_DIR"/test_*.py; do
    docker cp "$f" "$CONTAINER:/app/$(basename "$f")"
done

echo "==> Installing pytest in container..."
docker exec "$CONTAINER" python3 -m pip install pytest

echo "==> Running pytest..."
docker exec "$CONTAINER" python3 -m pytest /app/test_*.py -q

echo "==> Done. Connect with: docker exec -it $CONTAINER bash"
