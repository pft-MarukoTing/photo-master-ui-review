#!/usr/bin/env python3
"""Application startup script."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

# # Add MTVideoAI so aiserver_utils is importable
# _mtvideoai_path = os.path.join(os.path.dirname(__file__), '..', 'MTVideoAI')
# sys.path.insert(0, os.path.abspath(_mtvideoai_path))

# Load environment variables from .env.default
def load_env():
    """Load environment from .env.default file."""
    env_file = os.path.join(os.path.dirname(__file__), '.env.default')
    if not os.path.exists(env_file):
        print(f"Warning: {env_file} not found")
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
    except ImportError:
        # Fallback: manually parse .env.default
        print(f"python-dotenv not installed, manually loading {env_file}")
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()
                    # Only set if not already in environment
                    if key and key not in os.environ:
                        os.environ[key] = value

load_env()

# Ensure log directory exists
log_path = os.environ.get('LOG_PATH', './tmp/env-log')
log_dir = os.path.dirname(log_path) if os.path.dirname(log_path) else './tmp'
os.makedirs(log_dir, exist_ok=True)

from backend.app import create_app

if __name__ == '__main__':
    app = create_app()

    # Get configuration from environment
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 8993))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'

    print(f"Starting server at http://{host}:{port}")
    print("Press Ctrl+C to stop")

    app.run(host=host, port=port, debug=debug)
