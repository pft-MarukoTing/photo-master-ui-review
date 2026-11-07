"""Flask application factory."""

import os
import logging
from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .routes.static import static_bp
from .routes.api import api_bp


def create_app(config: dict = None) -> Flask:
    """
    Create and configure the Flask application.

    Args:
        config: Optional configuration dictionary

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    # Trust X-Forwarded-* from the reverse proxy so request.host_url reflects
    # the original https:// scheme (otherwise upload URLs returned to the
    # browser are http:// and get blocked as mixed content on HTTPS pages).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # Default configuration
    app.config.update(
        FRONTEND_DIR=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend'),
    )

    # Apply custom config
    if config:
        app.config.update(config)

    # Enable CORS
    CORS(app)

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Register blueprints
    app.register_blueprint(static_bp)
    app.register_blueprint(api_bp)

    return app


# For flask CLI compatibility
app = create_app()
