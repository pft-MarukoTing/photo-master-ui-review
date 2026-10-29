import hashlib
import os

from flask import Blueprint, current_app, jsonify, redirect, request, send_from_directory

from aiserver_utils.pf_redis import pf_redis_client
from backend.services.compatibility import get_current_version as get_current_compatibility_version, get_min_compatible_version
import config as photoagent_config
from photo_agent import capabilities

static_bp = Blueprint("static_bp", __name__)

# Matches server.py's WORKER_VERSION_KEY -- the worker container publishes its
# version here on startup, since it's a separate process/container from this
# Flask app with no other shared state. Namespaced per deployment (like
# REDIS_USERS_SET_KEY in account.py) since dev/poc share the same Redis
# cluster and would otherwise clobber each other's published version.
WORKER_VERSION_KEY = os.getenv("REDIS_WORKER_VERSION_KEY", "photoagent:version:worker")


def _frontend_version(frontend_dir):
    """Fingerprint frontend/ so an edited file is detected without restarting
    this process -- static files are served straight off disk, with no build
    step or cache-busted filename to version them otherwise."""
    mtimes = []
    for root, _dirs, files in os.walk(frontend_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                mtimes.append((os.path.relpath(path, frontend_dir), os.path.getmtime(path)))
            except OSError:
                continue
    mtimes.sort()
    return hashlib.sha1(repr(mtimes).encode()).hexdigest()[:12]


def _no_cache(resp):
    """Forces revalidation (a cheap 304 if unchanged) on every request for a
    frontend file, instead of leaving it to each browser's own heuristics --
    send_from_directory already sets Last-Modified/ETag, so revalidation
    itself is free, but with no explicit Cache-Control some browsers (iOS
    Safari especially) will happily serve a page/script straight from their
    own cache for a long time without even asking the server, so an edited
    app.js/index.html can silently keep serving the pre-edit version on a
    phone that "looks like" it refreshed. no-cache (not no-store) still lets
    the browser keep a local copy -- it just requires that revalidation
    round-trip before trusting it. See _frontend_version's own doc above for
    the deeper reason: these files have no cache-busted build hash to make a
    stale copy detectable by URL alone."""
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@static_bp.get("/")
def index():
    return _no_cache(send_from_directory(current_app.config["FRONTEND_DIR"], "index.html"))


@static_bp.get("/api/config")
def config():
    return jsonify({
        "debugUi": current_app.config.get("DEBUG_UI", False),
        "aipocDefault": current_app.config.get("AIPOC_DEFAULT", False),
        # Per-deployment stamp ("DEV") shown next to the logo and in <title>.
        # Read from the env (.env.backend) rather than baked into the
        # frontend, so the same bundle ships to dev and poc unchanged.
        "envLabel": os.getenv("ENV_LABEL", "").strip(),
        # Internal server-side feature switch mirrored to the UI.  The
        # backend remains authoritative; this only gates the read-only preview.
        "enableBodyReshape": bool(getattr(photoagent_config, "ENABLE_BODY_RESHAPE", True)),
        # Shows the panel's 接著這張調整 button; /continue-version checks it too.
        "renderFromVersion": bool(getattr(photoagent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", False)),
        # Lets the debug-mode version display (app.js) show a case's own
        # compatibility_version next to what this deployment is running now,
        # and lets finishSingleFlow gate a resumed case client-side (in
        # addition to continue_job's own authoritative check) regardless of
        # which history-view path it was resumed from. "compatibility" (not
        # just "version") keeps this distinct from /api/version's unrelated
        # frontend/worker build version, and from a client's own appVer.
        # Read from Redis (backend/services/compatibility.py, published by
        # the worker) rather than imported directly, so a worker-only deploy
        # shows up here without this separate Flask process restarting too.
        "compatibilityVersion": get_current_compatibility_version(),
        "minCompatibleVersion": get_min_compatible_version(),
    })


@static_bp.get("/api/capabilities")
def capabilities_list():
    """Every option a client may offer, from the feature registry
    (photo_agent/capabilities.py): debug-only entries only when DEBUG_UI is
    on, Body Reshape only when ENABLE_BODY_RESHAPE is, internal options never."""
    return jsonify(capabilities.describe(debug=bool(current_app.config.get("DEBUG_UI", False))))


@static_bp.get("/api/version")
def version():
    try:
        worker_version = pf_redis_client.get(WORKER_VERSION_KEY)
        if isinstance(worker_version, bytes):
            worker_version = worker_version.decode("utf-8")
    except Exception:
        # Redis hiccup shouldn't take down frontend-only update detection.
        worker_version = None

    return jsonify({
        "frontend": _frontend_version(current_app.config["FRONTEND_DIR"]),
        "worker": worker_version,
    })


@static_bp.get("/chat")
def chat_redirect():
    # Relative, so it keeps whatever prefix a proxy mounted this app under.
    # Keep the encrypted user_id (and other flags) when adding the slash.
    query = request.query_string.decode("ascii")
    return redirect("chat/" + ("?" + query if query else ""))


@static_bp.get("/chat/")
def chat_index():
    """The chat page (built from frontend-chat/ into frontend/chat/)."""
    return _no_cache(send_from_directory(current_app.config["FRONTEND_DIR"], "chat/index.html"))


@static_bp.get("/<path:filepath>")
def static_files(filepath):
    return _no_cache(send_from_directory(current_app.config["FRONTEND_DIR"], filepath))
