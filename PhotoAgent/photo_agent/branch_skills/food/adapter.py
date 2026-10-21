"""Turns the food branch's own JSON into the shared analysis contract the review screen, geometry and QA read."""

import copy

from image_tools import resolve_crop_box_with_report
from prompts import ANALYSIS_RESPONSE_SCHEMA_OVERALL, _replace_reframe_item

from .instructions import ITEM_LIMITS, ITEM_PREFIX, LOOK_PREFIX

BRANCH = "food"
LOOK_AXES = ["exposure", "contrast", "white_balance", "saturation"]
ITEM_AXES = {"reframe": ["crop"], "alignment": ["alignment"], "distraction": ["distraction"], "shadow": ["shadow"]}
LOOK_DECLINED = (
    "Keep the original overall light, colour, contrast, and white balance; only the approved items change them, and "
    "only where they apply."
)
ALIGNMENT_DECLINED = "保留原圖的傾斜與透視。"
_FULL_FRAME = 0.965


def normalize(result):
    """Per-kind limits in the analysis's order, and an alignment item exactly when alignment_strategy calls for one."""
    counts, kept = {}, []
    for item in result["review_items"]:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        if counts[item["kind"]] <= ITEM_LIMITS[item["kind"]]:
            kept.append(item)
    strategy = result["alignment_strategy"]
    needed = strategy["state"] != "level" and not strategy["deliberate"]
    if not needed:
        kept = [item for item in kept if item["kind"] != "alignment"]
    elif not any(item["kind"] == "alignment" for item in kept):
        kept.append({"kind": "alignment", "plan": strategy["decision"],
                     "recipe": ITEM_PREFIX["alignment"] + strategy["decision"], "inverse": ALIGNMENT_DECLINED})
    result["review_items"] = kept
    return result


def _empty_like(schema):
    kind = schema.get("type")
    if kind == "array":
        return []
    if kind == "object":
        return {key: _empty_like(value) for key, value in schema.get("properties", {}).items()}
    if kind == "boolean":
        return False
    if kind in ("integer", "number"):
        return 0
    return ""


def _hero(result):
    return next((s for s in result["subjects"] if s["kind"] == "hero_food"), None)


def _is_full(box):
    return box["w"] > _FULL_FRAME and box["h"] > _FULL_FRAME


def _fitted_crop(framing, hero, protected, image_size):
    """The crop box fitted to the chosen ratio, or None for the whole photo.

    Local geometry measures the rectangle to pick the auto ratio, so the model's loosely shaped box is fitted to its
    own label first; an expand plan keeps the box in the photograph's shape for the extension to work from.
    """
    width, height = image_size
    label, box = framing["aspect_ratio"], framing["crop_box"]
    expanding = framing["reframe_mode"] == "expand"
    if label == "original" and _is_full(box) and not expanding:
        return None
    ratio = f"{width}:{height}" if label == "original" or expanding else label
    context = {"subjects": [{"box": k["box"]} for k in framing["keep_in_frame"]] or [{"box": hero["box"] if hero else None}],
               "roi_box": hero["box"] if hero else None, "protected_regions": protected,
               "recommended_aspect_ratio": ratio}
    fitted = resolve_crop_box_with_report({**box, "enabled": True, "aspect_ratio": ratio}, context, width, height)["cropBox"]
    if not fitted or (_is_full(fitted) and not expanding):
        return None
    return {key: fitted[key] for key in ("x", "y", "w", "h")}


def framing_fields(framing, hero, image_size):
    """The geometry fields local cropping reads, built so only the hero and keep_in_frame hold the crop open."""
    keep = framing["keep_in_frame"]
    protected = [{**k["box"], "label": k["label"], "reason": "構圖必須完整保留。"} for k in keep]
    crop = _fitted_crop(framing, hero, protected, image_size)
    return {
        "recommended_aspect_ratio": framing["aspect_ratio"],
        "aspect_ratio_reason": framing["reframe_mode_reason"],
        "reframe_mode": framing["reframe_mode"],
        "reframe_mode_reason": framing["reframe_mode_reason"],
        "crop_box": {**(crop or framing["crop_box"]), "enabled": crop is not None,
                     "aspect_ratio": framing["aspect_ratio"], "reason": framing["framing_goal"]},
        "protected_regions": protected,
        "layout_method": framing["composition"],
        "composition_strategy": framing["framing_goal"],
        "crop_recommendation": framing["framing_goal"],
        "roi_box": {**hero["box"], "reason": "主菜。"} if hero else None,
    }


def _review_entry(item):
    return {"correction_plan": item["plan"], "correction_axes": list(ITEM_AXES[item["kind"]]),
            "retouch_recipe": item["recipe"], "correction_inverse": item["inverse"]}


def to_analysis(result, photo_facts, image_size):
    """The shared analysis for one food-branch result; result is kept whole under food_branch."""
    result = normalize(copy.deepcopy(result))
    analysis = _empty_like(ANALYSIS_RESPONSE_SCHEMA_OVERALL["schema"])
    hero = _hero(result)
    keep = result["framing"]["keep_in_frame"] or ([{"label": hero["label"], "box": hero["box"]}] if hero else [])
    look_recipe = result["look_recipe"]
    if not look_recipe.startswith(LOOK_PREFIX):
        look_recipe = LOOK_PREFIX + look_recipe
    entries = [{"correction_plan": result["edit_brief"], "correction_axes": list(LOOK_AXES),
                "retouch_recipe": look_recipe, "correction_inverse": LOOK_DECLINED}]
    entries += [_review_entry(item) for item in result["review_items"]]
    analysis.update(
        {key: [entry[key] for entry in entries]
         for key in ("correction_plan", "correction_axes", "retouch_recipe", "correction_inverse")},
        **{field: photo_facts[field] for field in ("photo_summary", "scene_category", "scene_type")},
        subject_category="食物",
        main_subject=hero["label"] if hero else result["plan"],
        portrait_framing="不適用",
        edit_intent=result["edit_brief"],
        alignment_strategy=result["alignment_strategy"]["decision"],
        distraction_strategy="；".join(i["plan"] for i in result["review_items"] if i["kind"] == "distraction"),
        subjects=[{"label": k["label"], "box": k["box"], "prominence": index + 1, "is_person": False,
                   "local_detection_index": -1, "skin": "不適用", "reason": "構圖必須完整保留。"}
                  for index, k in enumerate(keep)],
        local_face_review=[],
        crop_risk={"needs_validation": False, "risk_level": "low", "reasons": []},
        **framing_fields({**result["framing"], "keep_in_frame": keep}, hero, image_size),
    )
    analysis["subject_extent_box"] = None
    analysis["analysis_branch"] = BRANCH
    analysis["food_branch"] = result
    return analysis


def merge_reframe(analysis, reframe, aspect_ratio_pref, image_size):
    """analysis with its framing replaced by a food reframing answer; local geometry still has to resolve it."""
    from prompts import _REFRAME_STALE_KEYS

    merged = copy.deepcopy(analysis)
    for key in _REFRAME_STALE_KEYS:
        merged.pop(key, None)
    framing = reframe["framing"]
    hero = _hero(merged["food_branch"])
    if not framing["keep_in_frame"] and hero:
        framing = {**framing, "keep_in_frame": [{"label": hero["label"], "box": hero["box"]}]}
    merged["food_branch"]["framing"] = framing
    merged.update(framing_fields(framing, hero, image_size))
    item = reframe["reframe_item"]
    _replace_reframe_item(merged, {"enabled": item["enabled"], "retouch_recipe": item["recipe"],
                                   "correction_plan": item["plan"], "correction_inverse": item["inverse"]})
    merged["_aspect_ratio_pref_at_analysis"] = str(aspect_ratio_pref or "auto").strip().lower()
    return merged
