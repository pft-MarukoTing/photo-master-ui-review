"""The review page's findings cards: every review item grouped under the one aspect of the photo it improves,
with a rating and a one-sentence finding per aspect."""

import re

from photo_agent import i18n
from photo_agent.branch_skills.food.instructions import LOOK_PREFIX
from prompts import AXIS_ALIASES, LENS_AXIS, smile_adjustment_suggested

CATEGORIES = ("light_color", "background", "portrait", "clarity", "composition")
# Portrait comes from the portrait-QC pass, so the analysis model writes only the other five.
MODEL_CATEGORIES = tuple(category for category in CATEGORIES if category != "portrait")
RATINGS = ("good", "could_be_better", "needs_work")

_AXIS_CATEGORY = {
    "exposure": "light_color", "shadow": "light_color", "highlight": "light_color", "contrast": "light_color",
    "white_balance": "light_color", "saturation": "light_color", "skin": "light_color",
    "distraction": "background",
    "sharpen": "clarity",
    "crop": "composition", "alignment": "composition", LENS_AXIS: "composition",
    "expression": "portrait", "hair": "portrait",
}
# These axes name one specific reviewable decision, so an item carrying one always belongs to its card.
_FIXED_AXIS_CATEGORY = {"crop": "composition", "alignment": "composition", LENS_AXIS: "composition"}

_PORTRAIT_TOGGLES = {
    "blink": "eye_closed_detection", "gaze": "gaze_contact_detection", "hair": "hair_mess_detection",
    "smile": "smile_adjustment",
}


def _axes(entry):
    names = [entry] if isinstance(entry, str) else (entry or [])
    return [AXIS_ALIASES.get(name.strip().lower(), name.strip().lower())
            for name in names if isinstance(name, str) and name.strip()]


def fixed_category(entry):
    return next((_FIXED_AXIS_CATEGORY[axis] for axis in _axes(entry) if axis in _FIXED_AXIS_CATEGORY), None)


def fallback_category(entry):
    """The card an item belongs to when no model assigned it: a fixed axis, else its first known axis."""
    return fixed_category(entry) or next(
        (_AXIS_CATEGORY[axis] for axis in _axes(entry) if axis in _AXIS_CATEGORY), "light_color")


# kind: (problem, action); the action is completed with who has it.
_PORTRAIT_KINDS = {
    "blink": ("閉眼", "讓{who}自然睜開眼睛"),
    "gaze": ("眼睛沒有看向鏡頭", "讓{who}看向鏡頭"),
    "hair": ("頭髮凌亂", "整理{who}的亂髮"),
    "smile": ("笑容可以調整", "讓{who}自然微笑"),
}
# The lens item comes from the portrait-QC pass, so its finding is written here rather than by a model.
LENS_FINDING = "偵測到有人物在畫面邊緣被廣角拉寬。"
# A card's finding when the model wrote none that fits its rating.
GOOD_FALLBACK = {
    "light_color": "光線與色彩表現自然。", "background": "背景沒有搶走主體的元素。",
    "clarity": "主體細節清楚。", "composition": "構圖平衡穩定。",
}
PROBLEM_FALLBACK = {
    "light_color": "光線或色彩還有可以改善的地方。", "background": "背景有分散注意力的元素。",
    "clarity": "細節還可以更清楚。", "composition": "構圖還可以更好。",
}
# The food look item is a suggested style on this page; other screens keep its own prefix.
FOOD_LOOK_LABEL = "建議風格："


def _portrait_flagged(subject, kind):
    """Whether the analysis suggests this fix for one person: a smile is suggested, not a defect to fix."""
    issue = subject.get(f"{kind}_issue") or {}
    return smile_adjustment_suggested(issue) if kind == "smile" else bool(issue.get("should_fix"))


def _portrait_reading(analysis):
    """(items, problems, has_blink): one item and one problem per kind, honoring the advanced-option toggles."""
    options = analysis.get("_advanced_options") or {}
    people = [(index, subject) for index, subject in enumerate(analysis.get("subjects") or [])
              if isinstance(subject, dict) and subject.get("is_person")]
    items, problems = [], []
    for kind, (problem, action) in _PORTRAIT_KINDS.items():
        if options.get(_PORTRAIT_TOGGLES[kind], True) is False:
            continue
        hit = [(index, subject) for index, subject in people if _portrait_flagged(subject, kind)]
        if not hit:
            continue
        if len(people) == 1:
            named = i18n.tr("人物")
        elif len(hit) == len(people):
            named = i18n.tr("所有人物")
        elif len(hit) == 1:
            named = hit[0][1].get("label") or i18n.tr("這位人物")
        else:
            named = i18n.tr("{n}位人物", n=len(hit))
        items.append({"index": None, "kind": kind, "subject_indices": [index for index, _ in hit],
                      "text": i18n.tr(action, who=named)})
        problems.append(i18n.tr(problem))
    return items, problems, any(item["kind"] == "blink" for item in items)


def _portrait_card(analysis, review_items):
    if not any(isinstance(s, dict) and s.get("is_person") for s in analysis.get("subjects") or []):
        return None
    items, problems, has_blink = _portrait_reading(analysis)
    items = items + review_items
    if not items:
        return {"rating": "good", "finding": i18n.tr("人物的表情、眼神與頭髮都很自然。"), "items": []}
    return {
        "rating": "needs_work" if has_blink else "could_be_better",
        "finding": i18n.tr("偵測到有人物{problems}。", problems=i18n.join(problems)) if problems else "",
        "items": items,
    }


def is_lens_item(entry):
    return LENS_AXIS in _axes(entry)


_REFRAME_PREFIX = re.compile(r"^\s*重新構圖\s*[:：]\s*")


def _item_text(analysis, text):
    if not i18n.is_source() and _REFRAME_PREFIX.match(text):
        # The prefix stays Chinese in the data because the program reads it; the card shows it translated.
        return _REFRAME_PREFIX.sub(i18n.tr("重新構圖："), text)
    if analysis.get("analysis_branch") == "food":
        for prefix in (LOOK_PREFIX, i18n.tr(LOOK_PREFIX)):
            if text.startswith(prefix):
                return i18n.tr(FOOD_LOOK_LABEL) + text[len(prefix):]
    return text


def build_findings(analysis, model_cards=None, assignment=None, source="fallback"):
    """The findings cards for an analysis.

    model_cards: {category: {"rating", "finding"}} for MODEL_CATEGORIES, as the analysis model wrote them.
    assignment: {review item index: category} from the model; fixed axes override it and unassigned items fall
    back to their axes, so every review item lands on exactly one card.
    """
    model_cards = model_cards or {}
    assignment = assignment or {}
    recipe = list(analysis.get("retouch_recipe") or [])
    axes = list(analysis.get("correction_axes") or [])
    owner = {}
    for index in range(len(recipe)):
        entry = axes[index] if index < len(axes) else []
        assigned = assignment.get(index)
        owner[index] = fixed_category(entry) or (
            assigned if assigned in MODEL_CATEGORIES else fallback_category(entry))

    cards = []
    for category in CATEGORIES:
        items = [{"index": index, "text": _item_text(analysis, recipe[index])}
                 for index in range(len(recipe)) if owner[index] == category]
        lens = [item for item in items if is_lens_item(axes[item["index"]] if item["index"] < len(axes) else [])]
        items = [item for item in items if item not in lens]
        if category == "portrait":
            card = _portrait_card(analysis, items)
            if card is None:
                continue
        else:
            written = model_cards.get(category) or {}
            card = {
                "rating": written.get("rating") if written.get("rating") in RATINGS else None,
                "finding": str(written.get("finding") or "").strip(),
                "items": items,
            }
        # The rating follows the items: no items is good, and items are never good.
        rating = ("could_be_better" if card["rating"] in (None, "good") else card["rating"]) if card["items"] else "good"
        adjusted = card["rating"] is not None and rating != card["rating"]
        if category in MODEL_CATEGORIES:
            # A finding written for the other kind of rating would contradict the card.
            if rating == "good" and (adjusted or not card["finding"]):
                card["finding"] = i18n.tr(GOOD_FALLBACK[category])
            elif rating != "good" and (card["rating"] == "good" or not card["finding"]):
                card["finding"] = i18n.tr(PROBLEM_FALLBACK[category])
        if lens:
            card["items"] = card["items"] + lens
            lens_finding = i18n.tr(LENS_FINDING)
            card["finding"] = lens_finding if rating == "good" else i18n.sentences(card["finding"], lens_finding)
            rating = "could_be_better" if rating == "good" else rating
        cards.append({"category": category, **card, "rating": rating, "rating_adjusted": adjusted})
    return {"source": source, "cards": cards, "item_count": sum(len(card["items"]) for card in cards)}


def take_review_findings(overall):
    """Moves Call A's correction_aspect and review_findings into _review_findings, keyed by plan text, since later
    steps may add, drop, or rewrite review items."""
    aspects = overall.pop("correction_aspect", None) or []
    written = overall.pop("review_findings", None) or []
    cards = {}
    for entry in written:
        if isinstance(entry, dict) and entry.get("aspect") in MODEL_CATEGORIES and entry["aspect"] not in cards:
            cards[entry["aspect"]] = {"rating": entry.get("rating"), "finding": entry.get("finding")}
    plans = dict(zip((str(plan) for plan in overall.get("correction_plan") or []), aspects))
    overall["_review_findings"] = {"cards": cards, "plans": plans, "written": [
        {"aspect": entry.get("aspect"), "rating": entry.get("rating")} for entry in written if isinstance(entry, dict)]}
    return overall


def findings_from_review(analysis):
    """The cards for an analysis whose Call A tagged each item with an aspect and wrote the aspects' findings."""
    summary = analysis.get("_review_findings")
    if not isinstance(summary, dict):
        return build_findings(analysis)
    plans = summary.get("plans") or {}
    cards = summary.get("cards") or {}
    axes = list(analysis.get("correction_axes") or [])
    assignment = {}
    for index, plan in enumerate(analysis.get("correction_plan") or []):
        if plan not in plans:
            continue
        aspect = plans[plan]
        if (cards.get(aspect) or {}).get("rating") == "good":
            # The model judged this aspect fine, so an item tagged to it more likely belongs where its axes point.
            aspect = fallback_category(axes[index] if index < len(axes) else [])
        assignment[index] = aspect
    return build_findings(analysis, cards, assignment, source="call_a")
