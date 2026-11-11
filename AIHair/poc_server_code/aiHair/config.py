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
# A photo over this many pixels is shrunk to it (aspect kept, area
# averaging) before the feature runs, and the results are that size
# (hairfx.core.capped_photo); 0 runs every photo at its own size. Time and
# memory go with the photo's pixels (on 2026-11-10's profile, a 9.8 MP
# photo's task took 2-3x a 4 MP one's: hairline 3.4 GB and 75 s against
# 1.5 GB and 29 s), while gpt-image's render is ~1.6 MP whatever the photo.
AIHAIR_MAX_PIXELS = max(int(os.getenv("AIHAIR_MAX_PIXELS", "4000000")), 0)
# Billed OpenAI calls one task may have in flight at once (HairFeatures'
# main.py --workers).
AIHAIR_CALLS_PER_TASK = max(int(os.getenv("AIHAIR_CALLS_PER_TASK", "4")), 1)
# Tasks that may compute at once (the feature's own steps and its
# post-processing), whatever ENGINE_WORKER_NUM is: each lets its slot go
# while it waits on OpenAI (ai_hair/cpu_slots.py). 1 suits a 2 vCPU server;
# 0: no limit.
AIHAIR_CPU_SLOTS = max(int(os.getenv("AIHAIR_CPU_SLOTS", "1")), 0)
# How fluffy pastes its render back (its -o paste): "shifted", rootRetouch's
# composite (the default since 2026-11-11: 9.1 s against 15.8 on 5 renders),
# or "crown", the route before (only the raised crown pasted, its colour
# matched place by place); unset, hairfx's default.
AIHAIR_FLUFFY_PASTE = os.getenv("AIHAIR_FLUFFY_PASTE") or None
if AIHAIR_FLUFFY_PASTE not in (None, "shifted", "crown"):
    raise ValueError(f"AIHAIR_FLUFFY_PASTE must be shifted or crown, not {AIHAIR_FLUFFY_PASTE!r}")
# The OpenAI client's timeout and retries; HairFeatures' policy is no SDK
# retries, so a slow call fails at the timeout instead of re-billing.
AIHAIR_OPENAI_TIMEOUT_S = os.getenv("AIHAIR_OPENAI_TIMEOUT_S") or None
AIHAIR_OPENAI_MAX_RETRIES = int(os.getenv("AIHAIR_OPENAI_MAX_RETRIES", "0"))
