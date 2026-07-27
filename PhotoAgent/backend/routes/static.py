import os

from flask import Blueprint, current_app, jsonify, send_from_directory

static_bp = Blueprint("static_bp", __name__)


@static_bp.get("/")
def index():
    return send_from_directory(current_app.config["FRONTEND_DIR"], "index.html")


@static_bp.get("/api/config")
def config():
    return jsonify({
        "debugUi": current_app.config.get("DEBUG_UI", False),
        "aipocDefault": current_app.config.get("AIPOC_DEFAULT", False)
    })


@static_bp.get("/<path:filepath>")
def static_files(filepath):
    return send_from_directory(current_app.config["FRONTEND_DIR"], filepath)
