"""Entrypoint for the PhotoAgent analyze (batch scoring) service.

Runs as its own process on its own port, independent from the main
PhotoAgent backend. Loads the shared .env.default first (for
OPENAI_API_KEY) and then analyze/backend/.env.backend for service-local
overrides, mirroring how the main PhotoAgent backend layers its config.

Usage (from the PhotoAgent directory):
    python -m analyze.run_analyze
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_ANALYZE_DIR = Path(__file__).resolve().parent
_PHOTOAGENT_DIR = _ANALYZE_DIR.parent

# Resolve both files from this module instead of the process working directory.
# This guarantees that the scoring process reads PhotoAgent/.env.default when
# launched through ``python -m analyze.run_analyze``.
load_dotenv(str(_PHOTOAGENT_DIR / ".env.default"))
load_dotenv(str(_ANALYZE_DIR / "backend" / ".env.backend"), override=True)

from analyze.backend.app import create_app  # noqa: E402  (must import after env is loaded)

if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("ANALYZE_HOST", "0.0.0.0")
    port = int(os.environ.get("ANALYZE_PORT", "8971"))
    debug = os.environ.get("ANALYZE_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
