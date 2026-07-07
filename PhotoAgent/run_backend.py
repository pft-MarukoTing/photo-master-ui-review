"""Entrypoint for the PhotoAgent Flask backend.

Runs as its own process, separate from server.py's Redis-queue worker.
Talks to the same Redis Cluster and S3 bucket as the worker via the shared
.env.default config.
"""

import argparse
import os

from dotenv import load_dotenv

load_dotenv(".env.default")
load_dotenv(os.path.join(os.path.dirname(__file__), "backend", ".env.backend"), override=True)

from backend.app import create_app  # noqa: E402  (must import after env is loaded)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug-ui",
        action="store_true",
        default=os.environ.get("DEBUG_UI", "false").lower() == "true",
        help="Expose testbed-only frontend controls (e.g. the processing-path selector).",
    )
    args = parser.parse_args()

    app = create_app(config={"DEBUG_UI": args.debug_ui})
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8991"))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
