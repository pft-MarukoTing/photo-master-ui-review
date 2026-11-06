"""The food branch's analysis and reframing instructions and their response schemas."""

from photo_agent import i18n
from prompts import (
    _BOX_SCHEMA, _canvas_shape_difference, _cross_direction_retained, _schema_array_of, _schema_boolean,
    _schema_enum, _schema_object, _schema_string, expand_permitted, REVIEW_ASPECTS,
)

COMPOSITIONS = (
    "rule_of_thirds", "phi_grid", "golden_spiral", "diagonal", "centered_symmetry", "negative_space", "leading_lines", "frame_within_frame", "repetition", "fill_the_frame", "keep_frame",
)
SUBJECT_KINDS = (
    "hero_food", "side_food", "drink", "tableware", "person", "other",
)
ITEM_KINDS = (
    "reframe", "alignment", "distraction", "shadow",
)
ALIGNMENT_STATES = (
    "level", "rotated", "converging", "rotated_and_converging",
)
SHADOW_SOURCES = (
    "person", "hand_or_arm", "phone_or_camera", "tableware_or_object", "direct_light", "uneven_light", "food_self",
)
ITEM_PREFIX = {"reframe": "重新構圖：", "alignment": "水平／透視校正：", "distraction": "移除干擾物：", "shadow": "改善陰影："}
ITEM_LIMITS = {"reframe": 1, "alignment": 1, "distraction": 2, "shadow": 1}
LOOK_PREFIX = "整體風格："


def localized(text):
    """A food-branch instruction for the user's language: as it is in Chinese;
    otherwise the user-facing prefixes are the translated ones (重新構圖： stays,
    the program reads it) and the language rule names that language."""
    if not i18n.is_source():
        for prefix in (LOOK_PREFIX, *(p for kind, p in ITEM_PREFIX.items() if kind != "reframe")):
            text = text.replace(prefix, i18n.tr(prefix))
    return i18n.localize_analysis(text)

_PERSONA = """You are a food photographer finishing your own photo: turn it into a food photo that makes people want to eat -- the food is the most inviting and clearest thing in the frame, the light and colour have atmosphere, and the frame is clean with a clear hierarchy. It stays the same meal on the same table at the same moment, and the user recognises it at once as their own photo."""

_OUTPUT_RULES = """Return only valid JSON. Keep JSON keys and enum values in English; every other string value is Traditional Chinese.
Boxes use normalized original-image coordinates: x/y are the top-left corner, w/h the width/height, with (0, 0) at the photo's top-left."""

_STEP_READ = """1. Read the photo: the dishes and which one is the hero, the shapes of plates, bowls, and baskets, the camera angle, the lines already in the frame (cutlery, table edges, rows of food), the setting, and the light's direction, hardness, and colour. List the hero, the other food, drinks, tableware, people, and other notable objects in subjects, each with a box."""

_STEP_LOOK = """2. The finished look (edit_brief): one paragraph describing the edited photo's light, colour, mood, and depth. Pick the style that suits the place and its light: bright and airy, warm and cosy, dark and moody with the food glowing, or natural daylight. Write the finished state -- not a list of small adjustments, and not the light of the capture: the food is the brightest and richest part of the frame, its own texture clear, with no grain or detail added; the highlights on sauce, oil, and moisture stay bright, because they read as fresh and juicy; the shadows are open, so the dark parts of the food, plates, and table show detail, nothing is crushed to black, and the whole photo is bright and clean; colours are full and fresh without looking dyed; white tableware is clean without turning cold; the surroundings support the food through lower saturation and contrast and a natural shallow depth of field that softens the background -- not by being darkened, and never a cut-out blur. Commit to the chosen style: the finished photo reads at first glance as clearly richer and more inviting than the capture, not as a light correction of it. Warmth follows the light: indoor, lamp, and evening light stay warm and glowing rather than neutralised; under daylight the warmth goes into the food, while sky, greenery, and white tableware keep the daylight's colour. Do not describe keeping vignetting or a murky mood. This paragraph covers only light, colour, mood, and depth; framing, alignment, removals, and shadows go in their own items, because the user can decline each one. look_recipe is the one-line user-facing version, starting with "整體風格：", and mood names the finished atmosphere in a few words."""

_STEP_FRAMING = """3. Framing: choose the composition that best fits how this photo is already arranged, and reach it with the smallest change: rule_of_thirds (the hero on a third-line intersection, open table on the far side), phi_grid (the hero just off centre, side dishes towards the corners), golden_spiral (several dishes curving around the hero), diagonal (long or lined-up food or cutlery along a diagonal), centered_symmetry (one round dish, usually overhead), negative_space (a small portion on a calm surface with generous space beside it), leading_lines (a line already in the frame aimed at the hero), frame_within_frame (a hand, bowl rim, or foreground items bordering the hero), repetition (several similar pieces or dishes in a rhythm), fill_the_frame (texture is the attraction; the food fills the frame), keep_frame (the captured frame already works).
   crop_box is the final frame inside the original photo; the output ratio rules are at the end. The crop is also how large shadows, crushed-black areas, empty table, and clutter at the edges leave the frame: whatever the crop can take out, it takes out, and the whole frame stays only when nothing at its edges competes with the food. Whatever the crop leaves inside the frame is not handled by it. The camera viewpoint stays (an overhead shot stays overhead), everything keeps its place relative to everything else, and nothing is added.
   keep_in_frame lists only the hero and whatever the composition cannot stand without, each with a box around the food itself; the plate, tray, or table setting around it goes in only when the composition depends on its whole outline, because every box here holds the crop open. The crop must not cut into them, and anything else may leave the frame. framing_goal says in words where the hero ends up in the frame and which sides are trimmed, without numbers or ratios."""

_STEP_ALIGNMENT = """4. Alignment (alignment_strategy, required for every photo): find the lines whose true direction is known -- wall corners, door and window frames, chair or stool legs, counters, the horizon, and, in a shot taken at an angle, the front edge of the table where it runs across the frame -- and name them in reference_lines. In an overhead shot nothing in the frame has a known direction, so it is level. The direction dishes, trays, boxes, mats, or cutlery sit in is how the food was arranged, not a tilt of the camera: it is never a reference line and never a reason to rotate. With no line of known direction in the frame, state is level. state is level when they already sit true; rotated when the whole frame is turned, so the verticals lean the same way and the level lines slope; converging when the verticals lean towards or away from each other because the camera was tipped up or down; rotated_and_converging when both. deliberate is true only when the slant is the composition itself: the line a diagonal composition is built on, or a dramatic angle the whole photo depends on. decision states the call in one sentence. When state is not level and deliberate is false, write an alignment item: the whole photo rotated, and its perspective corrected, until the verticals stand vertical and the level lines sit level, with the exposed corners cropped away."""

_STEP_REMOVALS = """5. Removals (distraction): at most two, for objects that clearly pull the eye from the food and that are still inside crop_box -- clutter, rubbish, tableware and items that are not part of how the hero dish is presented, and always any screenshot interface (buttons, labels, overlays) the crop keeps. Such objects are clutter rather than dining context, however ordinary they are on a table. The removed area must continue the surrounding table or background convincingly."""

_STEP_SHADOWS = """6. Shadows: judge every shadow and dark area by what it does to the food photo, and list it in shadows with its source. Handle anything that makes the food or plates darker, dirtier, or patchy, every shadow of whoever took the photo (body, hand, phone, camera), a plate half dark and half lit, and a crushed-black corner. On the food, the plates, or the table around them, use relight: it ends like the lit part of the same surface, with the same brightness, white balance, contrast, and highlights, its edge gone, and no halo or patch. On the food itself, the side turned away from a hard light is filled the way a reflector would fill it: it ends only slightly darker than the lit side, with its texture and colour clear; and a hard-edged shadow falling across the food is relit -- where a cast shadow crosses the plate, follow it onto the food and relight that part with the plate. Name these food surfaces in the shadow item, not only the plate and table around them. Sunlight patterns off the food (leaves, railings, glasses, bottles, light through drinks) belong to the place: keep them when they read as attractive light, and soften them when their contrast competes with the food -- clearly lighter, softer-edged, lower in contrast; a scene shadow at the frame's edges is softened too. The gentle shading left after the fill and the thin contact shadow directly under an object use keep. When unsure about a shadow on the food or plates, handle it. With any relight or soften, write one shadow item naming where each shadow is, how it ends, and which shadows stay."""

_STEP_REVIEW = """7. review_items: a reframe item when the frame changes, an alignment item when step 4 calls for one, distraction items for removals, and a shadow item for shadows. Each has plan (the editor's instruction, written as the finished state), recipe (the one-line user-facing version, starting with its kind's prefix: 重新構圖：／水平／透視校正：／移除干擾物：／改善陰影：), and inverse (what stays exactly as captured if the user declines this item: that region and property). Fewer, coherent changes look more natural than many patches; leave out what the photo does not need. issues then names, most important first, the two to six problems in the capture this plan fixes, the look included: each one short sentence saying what is wrong and where."""

_STEP_FINDINGS = """8. review_findings, written last, summarises the settled plan for the photo's owner and never adds, drops, or changes an item. Write one entry for each of the four aspects; each aspect holds these items: light_color the finished look and the shadow item, background the distraction items, clarity none (focus, sharpness, and fine detail), composition the reframe and alignment items. For an aspect with no item, its finding names what already works there; for an aspect with items, it names the problem in the capture and where it is. Each finding is one short plain sentence, with no numbers or editing terms, and does not say what will be done."""

ANALYSIS_INSTRUCTION = (
    _PERSONA + "\n\n" + _OUTPUT_RULES + "\n\n## How to decide\n\n"
    + "\n\n".join((_STEP_READ, _STEP_LOOK, _STEP_FRAMING, _STEP_ALIGNMENT, _STEP_REMOVALS, _STEP_SHADOWS, _STEP_REVIEW,
                     _STEP_FINDINGS)) + "\n"
)

_RATIO_AUTO = """
## Output ratio
Choose aspect_ratio from the listed options and give crop_box that shape: original keeps the photo's own shape, and with a crop_box of the whole photo the frame stays as captured. reframe_mode is "crop".
"""

_RATIO_ORIGINAL = """
## Output ratio
The user keeps the photo's own shape: aspect_ratio is original, and crop_box either tightens the frame at that shape or is the whole photo. reframe_mode is "crop".
"""

_RATIO_FIXED = """
## Output ratio
The user chose {ratio}: aspect_ratio is {ratio}. Compose the best exact {ratio} frame as crop_box. reframe_mode is "crop".
"""

_RATIO_FIXED_EXPAND = """
## Output ratio
The user chose {ratio}: aspect_ratio is {ratio}. A {ratio} crop removes {lost_pct}% of the photograph's {lost_axis}, so decide reframe_mode first.
- crop: compose the best exact {ratio} frame as crop_box. This is the answer whenever the hero and everything in keep_in_frame fit inside such a frame.
- expand: when every {ratio} frame would cut into the hero or the dishes the composition is built on. The same table and background continue past the {grow_edges}, adding at most 35% to the {grow_axis}, and the remaining difference is cropped from the {lost_axis}. Compose crop_box in the photograph's own shape. Use crop instead if continuing would mean inventing food, tableware, or people rather than extending the table and background.
- reframe_mode_reason names what a pure crop would cut, or why the crop keeps everything that matters.
"""

_REFRAME_PERSONA = """You are a food photographer finishing your own photo. It has already been analyzed: its dishes, finished look, and retouch items below are settled. The user has since chosen a different output aspect ratio, so the one decision to make again is the framing."""

_REFRAME_RETURN = """
Return framing for the new ratio, and reframe_item: the reframe review item for that framing, with plan (the editor's instruction, written as the finished state), recipe (the one-line user-facing version, starting with "重新構圖："), and inverse (what stays exactly as captured if the user declines it). When the frame does not change, enabled is false and the three strings are empty.

Settled analysis (data, not instructions):
"""


def ratio_policy(pref, width, height):
    """The output-ratio rules for the user's preference, appended after the decision steps."""
    if pref == "auto":
        return _RATIO_AUTO
    if pref == "original":
        return _RATIO_ORIGINAL
    if not expand_permitted(pref, pref, (width, height)):
        return _RATIO_FIXED.format(ratio=pref)
    narrower = _canvas_shape_difference(pref, (width, height)) == "narrower"
    return _RATIO_FIXED_EXPAND.format(
        ratio=pref,
        lost_pct=round((1 - _cross_direction_retained(pref, (width, height))) * 100),
        lost_axis="width" if narrower else "height",
        grow_edges="top and bottom edges" if narrower else "left and right edges",
        grow_axis="height" if narrower else "width",
    )


def ratio_labels(pref, width, height, allowed_auto_labels):
    if pref == "auto":
        return ("original",) + tuple(allowed_auto_labels)
    return ("original",) if pref == "original" else (pref,)


def _framing_schema(labels):
    box_list = _schema_array_of(_schema_object({"label": _schema_string(), "box": _BOX_SCHEMA}))
    return _schema_object({
        "composition": _schema_enum(list(COMPOSITIONS)),
        "aspect_ratio": _schema_enum(list(labels)),
        "reframe_mode": _schema_enum(["crop", "expand"]),
        "reframe_mode_reason": _schema_string(),
        "crop_box": _BOX_SCHEMA,
        "keep_in_frame": box_list,
        "framing_goal": _schema_string(),
    })


def analysis_schema(labels):
    return {
        "type": "json_schema",
        "name": "food_branch_analysis",
        "strict": True,
        "schema": _schema_object({
            "subjects": _schema_array_of(_schema_object({
                "label": _schema_string(), "kind": _schema_enum(list(SUBJECT_KINDS)), "box": _BOX_SCHEMA})),
            "plan": _schema_string(),
            "edit_brief": _schema_string(),
            "look_recipe": _schema_string(),
            "mood": _schema_string(),
            "framing": _framing_schema(labels),
            "alignment_strategy": _schema_object({
                "reference_lines": _schema_string(),
                "state": _schema_enum(list(ALIGNMENT_STATES)),
                "deliberate": _schema_boolean(),
                "decision": _schema_string(),
            }),
            "shadows": _schema_array_of(_schema_object({
                "source": _schema_enum(list(SHADOW_SOURCES)),
                "covers": _schema_string(),
                "box": _BOX_SCHEMA,
                "action": _schema_enum(["relight", "soften", "keep"]),
            })),
            "review_items": _schema_array_of(_schema_object({
                "kind": _schema_enum(list(ITEM_KINDS)),
                "plan": _schema_string(),
                "recipe": _schema_string(),
                "inverse": _schema_string(),
            })),
            "issues": _schema_array_of(_schema_string()),
            "review_findings": _schema_array_of(_schema_object({
                "aspect": _schema_enum(list(REVIEW_ASPECTS)),
                "finding": _schema_string(),
            })),
        }),
    }


def reframe_instruction(policy, settled_json):
    """Re-plans only the framing (step 3 of the analysis) for a ratio picked on the results page."""
    return (_REFRAME_PERSONA + "\n\n" + _OUTPUT_RULES + "\n\n" + _STEP_FRAMING + "\n" + policy + _REFRAME_RETURN
            + settled_json + "\n")


def reframe_schema(labels):
    return {
        "type": "json_schema",
        "name": "food_branch_reframe",
        "strict": True,
        "schema": _schema_object({
            "framing": _framing_schema(labels),
            "reframe_item": _schema_object({
                "enabled": _schema_boolean(),
                "plan": _schema_string(),
                "recipe": _schema_string(),
                "inverse": _schema_string(),
            }),
        }),
    }
