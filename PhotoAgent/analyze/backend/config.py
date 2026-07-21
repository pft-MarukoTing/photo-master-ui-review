# =============================================================================
# Analyze (batch scoring) configuration
#
# Scoring semantics (weights and schema_version) live in scoring_config.json
# so they can be tuned without
# touching code. Operational parameters (model, timeout, retries,
# concurrency, ZIP limits) come from environment variables, matching how the
# main PhotoAgent backend reads its settings.
# =============================================================================

import json
import os

_SCORING_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "scoring_config.json")

with open(_SCORING_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _scoring = json.load(_f)

SCHEMA_VERSION = _scoring["schema_version"]

# Base weights as integers summing to 100 (kept human-editable; divided by the
# active-weight sum at compute time, never pre-fractioned).
WEIGHTS = {key: float(value) for key, value in _scoring["weights"].items()}

# Canonical dimension order -- every schema, prompt, and result iterates this
# list, never the model's own keys.
DIMENSION_KEYS = list(WEIGHTS.keys())

def _validate_scoring_config():
    total = sum(WEIGHTS.values())
    if abs(total - 100) > 1e-6:
        raise RuntimeError(f"scoring_config.json weights must sum to 100, got {total}")


_validate_scoring_config()


# ── Operational parameters (env-driven) ────────────────────────────────────────

def _env_int(name, default):
    value = os.getenv(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _env_bool(name, default):
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value == "true"


# OpenAI call knobs. EVAL_MODEL defaults to the same analysis model the main
# PhotoAgent pipeline uses.
EVAL_MODEL = os.getenv("EVAL_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
EVAL_TIMEOUT = _env_int("EVAL_TIMEOUT", 120)
EVAL_MAX_RETRIES = _env_int("EVAL_MAX_RETRIES", 3)
EVAL_MAX_CONCURRENCY = _env_int("EVAL_MAX_CONCURRENCY", 4)

# Longest side of the previews sent to the vision model (bounds token cost).
EVAL_IMAGE_MAX_SIDE = _env_int("EVAL_IMAGE_MAX_SIDE", 1024)

# ZIP safety limits.
MAX_ZIP_BYTES = _env_int("MAX_ZIP_BYTES", 500 * 1024 * 1024)
MAX_UNCOMPRESSED_BYTES = _env_int("MAX_UNCOMPRESSED_BYTES", 1024 * 1024 * 1024)
MAX_TOTAL_IMAGES = _env_int("MAX_TOTAL_IMAGES", 500)
MAX_IMAGE_BYTES = _env_int("MAX_IMAGE_BYTES", 50 * 1024 * 1024)
MAX_SIDECAR_BYTES = _env_int("MAX_SIDECAR_BYTES", 10 * 1024 * 1024)
MAX_RUNS = _env_int("MAX_RUNS", 300)
MAX_COMPRESSION_RATIO = _env_int("MAX_COMPRESSION_RATIO", 200)
MAX_ENTRIES = _env_int("MAX_ENTRIES", 5000)

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# In-memory job store.
EVAL_JOB_TTL_SECONDS = _env_int("EVAL_JOB_TTL_SECONDS", 3600)
EVAL_MAX_JOBS = _env_int("EVAL_MAX_JOBS", 200)

# Result thumbnails (inline data URLs so temp files can be deleted).
EVAL_THUMBNAILS_ENABLED = _env_bool("EVAL_THUMBNAILS_ENABLED", True)
EVAL_THUMBNAIL_MAX_SIDE = _env_int("EVAL_THUMBNAIL_MAX_SIDE", 640)
EVAL_THUMBNAIL_JPEG_QUALITY = _env_int("EVAL_THUMBNAIL_JPEG_QUALITY", 92)

EVAL_LOG_LEVEL = os.getenv("EVAL_LOG_LEVEL", "INFO").strip().upper() or "INFO"


def _default_log_path():
    base = os.getenv("LOG_PATH", "").strip()
    if not base:
        return os.path.join("tmp", "env-log_analyze")
    stem, ext = os.path.splitext(base)
    return f"{stem}_analyze{ext}"


EVAL_LOG_PATH = os.getenv("ANALYZE_LOG_PATH", _default_log_path()).strip()
EVAL_LOG_MAX_BYTES = _env_int("ANALYZE_LOG_MAX_BYTES", 5 * 1024 * 1024)
EVAL_LOG_BACKUP_COUNT = _env_int("ANALYZE_LOG_BACKUP_COUNT", 5)
