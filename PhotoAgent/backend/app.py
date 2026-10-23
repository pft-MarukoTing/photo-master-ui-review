import os

from flask import Flask, request
from flask_cors import CORS

from backend.compression import init_compression
from backend.routes.api import api_bp
from backend.routes.chat import chat_bp, start_watcher
from backend.routes.static import static_bp
from photo_agent import i18n


def create_app(config=None):
    app = Flask(__name__)
    app.config["FRONTEND_DIR"] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend"
    )
    app.config["DEMO_IMAGES_DIR"] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "demo_images"
    )
    if config:
        app.config.update(config)

    CORS(app)

    @app.before_request
    def _set_language():
        # Every request sets it, so a thread never carries the last request's.
        i18n.set_current(request.headers.get(i18n.HEADER))

    init_compression(app)
    app.register_blueprint(static_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(chat_bp)
    if not app.testing:
        start_watcher(app)

    return app
