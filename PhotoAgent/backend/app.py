import os

from flask import Flask
from flask_cors import CORS

from backend.routes.api import api_bp
from backend.routes.static import static_bp


def create_app(config=None):
    app = Flask(__name__)
    app.config["FRONTEND_DIR"] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "js_version"
    )
    if config:
        app.config.update(config)

    CORS(app)
    app.register_blueprint(static_bp)
    app.register_blueprint(api_bp)

    return app
