"""Call 0: one small vision call before analysis that names the kind of photograph and its setting.

The model only describes; Python maps the category to a branch skill, so adding a skill never changes this prompt.
"""

import logging
import time

from image_tools import create_preview_bytes, image_to_data_url
from photo_agent import i18n

logger = logging.getLogger(__name__)

# Fixed rather than the user's analysis model, so every photo is sorted by the model the router was tested on.
ROUTER_MODEL = "gpt-5.6-luna"
ROUTER_IMAGE_MAX_SIDE = 512
ROUTER_REASONING_EFFORT = "none"
ROUTER_MAX_OUTPUT_TOKENS = 400

CATEGORIES = {
    "portrait": "人像",
    "food": "美食",
    "landscape": "風景",
    "architecture": "建築",
    "animal": "動物",
    "product": "商品／靜物",
    "street": "街拍／生活紀錄",
    "other": "其他",
}

ROUTER_INSTRUCTION = """You sort a photo by the kind of photograph it is, so it can be retouched the way that kind of photo is retouched.

First write photo_summary: one sentence on what the photographer framed this picture to show and how it reads as a whole -- the thing it is about, not everything visible in it. Then main_category is the kind of photograph that makes it:
- portrait: people are what it is about -- one person, a group, people doing something together.
- food: the dishes or drinks themselves are what it is about, with at most hands or the edge of a diner in frame; a picture of people eating together is about the people.
- landscape: nature, sky, water, or a wide view of a place.
- architecture: a building, an interior, or a monument.
- animal: a pet or other animal.
- product: an object other than food -- a product, a vehicle, a still life.
- street: an everyday scene with no single subject -- street life, a crowd, an event.
- other: anything else -- screenshots of documents, signs, artwork.

also_present lists other categories the picture clearly also is, in the same terms; leave it empty when there are none.

scene_category is the setting it was taken in, and scene_type names that setting more specifically.

is_screenshot is true when the image is a capture of a screen rather than the photograph alone: app or web interface, buttons, labels, or overlays laid over or around the picture.

Return only valid JSON. photo_summary and scene_type are Traditional Chinese."""

SCENE_CATEGORIES = ("室內", "戶外", "街拍", "商業", "旅行", "日常", "其他")

ROUTER_SCHEMA = {
    "type": "json_schema",
    "name": "photo_route",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["photo_summary", "main_category", "also_present", "scene_category", "scene_type", "is_screenshot"],
        "properties": {
            "photo_summary": {"type": "string"},
            "main_category": {"type": "string", "enum": list(CATEGORIES)},
            "also_present": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}},
            "scene_category": {"type": "string", "enum": list(SCENE_CATEGORIES)},
            "scene_type": {"type": "string"},
            "is_screenshot": {"type": "boolean"},
        },
    },
}


def route_photo(image_bytes, model=ROUTER_MODEL, *, max_side=ROUTER_IMAGE_MAX_SIDE):
    """Classify one photo. Returns the parsed route plus model, usage and duration."""
    import openai_client

    data_url = image_to_data_url(create_preview_bytes(image_bytes, max_side=max_side), "image/jpeg")
    payload = openai_client._responses_payload(
        model,
        [{"type": "input_text", "text": i18n.localize_analysis(ROUTER_INSTRUCTION)}, {"type": "input_image", "image_url": data_url}],
        text_format=ROUTER_SCHEMA, reasoning_effort=ROUTER_REASONING_EFFORT,
    )
    payload["max_output_tokens"] = ROUTER_MAX_OUTPUT_TOKENS
    started = time.perf_counter()
    data = openai_client._post_json(openai_client.OPENAI_RESPONSES_URL, payload, "Photo routing failed")
    route = openai_client._parse_response_json(data)
    route["also_present"] = [c for c in route.get("also_present") or [] if c != route.get("main_category")]
    return route, {"model": model, "usage": data.get("usage"), "reasoning_effort": ROUTER_REASONING_EFFORT,
                   "duration_seconds": round(time.perf_counter() - started, 3)}


def run_call0(image_bytes):
    """route_photo that never raises: (route with state "ready", meta), or ({"state": "unavailable", ...}, None)."""
    try:
        route, meta = route_photo(image_bytes)
        return {"state": "ready", **route}, meta
    except Exception as exc:
        logger.warning("Call 0 unavailable; Call A writes the photo facts itself: %s", exc)
        return {"state": "unavailable", "error": str(exc)[:200]}, None


def select_branch(route, branches):
    """The branch skill whose categories include the photo's, or None for the default Call A path."""
    if not route:
        return None
    return next((branch for branch in branches if route.get("main_category") in branch.categories), None)
