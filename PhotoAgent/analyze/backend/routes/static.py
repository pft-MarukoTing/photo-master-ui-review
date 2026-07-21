"""Routes that serve the Analyze browser frontend."""

from flask import Blueprint, current_app, send_from_directory


static_bp = Blueprint("static_bp", __name__)


@static_bp.get("/")
def index():
    return send_from_directory(current_app.config["FRONTEND_DIR"], "index.html")


@static_bp.get("/<path:filepath>")
def static_files(filepath):
    return send_from_directory(current_app.config["FRONTEND_DIR"], filepath)
