"""Static file serving routes."""

import os
from flask import Blueprint, send_from_directory, redirect, current_app

static_bp = Blueprint('static', __name__)


@static_bp.route('/')
def index():
    frontend_dir = current_app.config['FRONTEND_DIR']
    return send_from_directory(frontend_dir, 'index.html')

@static_bp.route('/<path:filepath>')
@static_bp.route('/web/<path:filepath>')
def webpage(filepath):
    frontend_dir = current_app.config['FRONTEND_DIR']
    return send_from_directory(frontend_dir, filepath)

