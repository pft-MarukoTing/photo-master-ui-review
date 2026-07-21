"""Flask application factory for the batch scoring service."""

import datetime
import json
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask
from flask_cors import CORS

from analyze.backend import config
from analyze.backend.routes.api import api_bp
from analyze.backend.routes.static import static_bp

# Optional structured fields callers may attach via logging's ``extra=``; these
# mirror the top-level keys of the main PhotoAgent backend (aiserver_utils) log
# schema so both services' log lines carry the same shape.
_EXTRA_LOG_FIELDS = (
    "status", "ver", "task_id", "task_status", "error_code", "error_type", "params",
)

_LEVEL_ACTIONS = {
    "DEBUG": "Debug",
    "INFO": "Info",
    "WARNING": "Warning",
    "ERROR": "Error",
    "CRITICAL": "Error",
}


class _JsonLogFormatter(logging.Formatter):
    """Serialises each record as one JSON line matching the main PhotoAgent
    backend (aiserver_utils) schema, so both services' logs parse identically:
    date (UTC, millisecond precision, trailing Z), action, msg, log_src, pid,
    plus any structured extras a caller attaches via ``extra=``.
    """

    def format(self, record):
        info = {
            "date": datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "action": _LEVEL_ACTIONS.get(record.levelname, record.levelname),
            "msg": record.getMessage(),
            # This service is application ("engine") code; the main backend uses
            # "lib" only for aiserver_utils-internal log lines.
            "log_src": "eng",
            "pid": record.process,
        }
        for field in _EXTRA_LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                info[field] = value
        if record.exc_info:
            info.setdefault("error_type", record.exc_info[0].__name__)
            info["traceback"] = self.formatException(record.exc_info)
        return json.dumps(info, ensure_ascii=False)


def _init_logging(enable_file=True):
    logger = logging.getLogger("analyze")
    logger.setLevel(config.EVAL_LOG_LEVEL)
    if logger.handlers:
        return
    formatter = _JsonLogFormatter()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if enable_file and config.EVAL_LOG_PATH:
        try:
            directory = os.path.dirname(config.EVAL_LOG_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            file_handler = RotatingFileHandler(
                config.EVAL_LOG_PATH,
                maxBytes=config.EVAL_LOG_MAX_BYTES,
                backupCount=config.EVAL_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning("analyze log file unavailable (%s): %s", config.EVAL_LOG_PATH, exc)

    logger.info("analyze logging initialized", extra={"status": "init", "ver": config.SCHEMA_VERSION})


def create_app(app_config=None):
    app = Flask(__name__)
    app.config["FRONTEND_DIR"] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend"
    )
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_ZIP_BYTES
    if app_config:
        app.config.update(app_config)

    _init_logging(enable_file=not app.config.get("TESTING"))
    CORS(app)
    app.register_blueprint(static_bp)
    app.register_blueprint(api_bp)

    return app
