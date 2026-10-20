#!/usr/bin/env bash
# Sync tests into running Docker container and run pytest
# Usage: bash scripts/sync_test_env.sh

set -e

CONTAINER="photo-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Syncing tests/ into container..."
docker exec "$CONTAINER" rm -rf /app/tests
docker cp "$SCRIPT_DIR/tests" "$CONTAINER:/app/tests"

echo "==> Installing pytest in container..."
docker exec "$CONTAINER" python3 -m pip install pytest

echo "==> Running pytest..."
docker exec "$CONTAINER" sh -c "cd /app && python3 -m pytest tests -q"

echo "==> Done. Connect with: docker exec -it $CONTAINER bash"
