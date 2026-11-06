# =============================================================================
# aiHair Configuration
# =============================================================================
# Add task-specific configuration here.
import os

# gpt-image's render settings for every feature (hairfx/core.py's defaults
# where unset: gpt-image-2.5-sunburst, medium quality, the photo's aspect).
AIHAIR_RENDER_MODEL = os.getenv("AIHAIR_RENDER_MODEL") or None
AIHAIR_RENDER_QUALITY = os.getenv("AIHAIR_RENDER_QUALITY") or None
AIHAIR_RENDER_SIZE = os.getenv("AIHAIR_RENDER_SIZE") or "match"
# Billed OpenAI calls one task may have in flight at once (HairFeatures'
# main.py --workers).
AIHAIR_CALLS_PER_TASK = max(int(os.getenv("AIHAIR_CALLS_PER_TASK", "4")), 1)
# The OpenAI client's timeout and retries; HairFeatures' policy is no SDK
# retries, so a slow call fails at the timeout instead of re-billing.
AIHAIR_OPENAI_TIMEOUT_S = os.getenv("AIHAIR_OPENAI_TIMEOUT_S") or None
AIHAIR_OPENAI_MAX_RETRIES = int(os.getenv("AIHAIR_OPENAI_MAX_RETRIES", "0"))
