import os

from flask import Flask
from flask_cors import CORS

from backend.compression import init_compression
from backend.routes.api import api_bp
from backend.routes.chat import chat_bp, start_watcher
from backend.routes.static import static_bp


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
    init_compression(app)
    app.register_blueprint(static_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(chat_bp)
    if not app.testing:
        start_watcher(app)

    return app
