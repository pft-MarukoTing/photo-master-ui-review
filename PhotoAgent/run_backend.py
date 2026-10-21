"""Entrypoint for the PhotoAgent Flask backend.

Runs as its own process, separate from server.py's Redis-queue worker.
Talks to the same Redis Cluster and S3 bucket as the worker via the shared
.env.default config.
"""

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env.default"), override=True)
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
    parser.add_argument(
        "--skip-chat-build",
        action="store_true",
        help="Serve frontend/chat/ as-is instead of rebuilding it from frontend-chat/ first.",
    )
    args = parser.parse_args()

    # frontend/chat/ is a build output and isn't tracked in git, so build it
    # from the checked-out source before serving. Skip it in the reloader's
    # child process (DEBUG=true), which re-runs this file after the parent
    # has already built.
    if not args.skip_chat_build and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "build_chat_bundle.sh")
        if subprocess.run(["bash", script]).returncode != 0:
            sys.exit("Building the chat bundle failed; fix it, or pass --skip-chat-build to serve the existing one.")

    app = create_app(config={"DEBUG_UI": args.debug_ui})
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8991"))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    # threaded=True: batch mode fires off several long-running requests at
    # once (synchronous per-image analyze/upload calls in the /analyze-batch,
    # /aipoc/analyze-batch, and /submit routes) -- Werkzeug's dev server
    # defaults to handling one request at a time, so without this a large
    # batch blocks every other concurrent request (status polls, other users,
    # update-check.js) until it finishes, surfacing as spurious 500s/timeouts.
    app.run(host=host, port=port, debug=debug, threaded=True)
