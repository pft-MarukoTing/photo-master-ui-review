"""Forward-compatibility tracking for the analysis payload analyze_stage
produces (pipeline.py) and finish_stage later consumes.

Called "compatibility version" throughout (not just "version") to keep it
distinct from the app/frontend/worker version concepts elsewhere in this
codebase (e.g. /api/version's frontend content hash + worker version, or a
client's own appVer) -- this one specifically tracks the shape of
analyze_stage's output.

A history item picked up in a later app version carries whatever
analyze_stage shape was current when it was created. Most schema changes
here are additive (finish_stage reads new fields with a fallback) and don't
break older payloads at all -- only a change that actually breaks how
finish_stage reads an older payload should move MIN_COMPATIBLE_VERSION.
"""

CURRENT_COMPATIBILITY_VERSION = 0

# A saved analysis whose compatibility_version is below this floor is no
# longer accepted for /continue -- see backend/routes/api.py's continue_job.
# Bump this to CURRENT_COMPATIBILITY_VERSION only when a change actually
# breaks older payloads; leave it alone for additive changes so most version
# bumps don't invalidate any existing history.
MIN_COMPATIBLE_VERSION = 0


def get_compatibility_version(analysis: dict) -> int:
    """Analysis saved before this field existed reads as version 0."""
    return analysis.get("compatibility_version", 0)


def is_compatible(analysis: dict, min_version: int = None) -> bool:
    """min_version overrides MIN_COMPATIBLE_VERSION -- see
    backend/services/compatibility.py, which the Flask app (a separate
    process from the worker that actually owns this module's constants)
    uses to pass in the worker's current floor instead of this process's own,
    possibly-stale, imported value."""
    floor = MIN_COMPATIBLE_VERSION if min_version is None else min_version
    return get_compatibility_version(analysis) >= floor
