import io
import math
import base64
import re

from PIL import Image, ImageEnhance
import pillow_heif

# Pillow has no built-in HEIC/HEIF decoder (patent-encumbered codec, not
# bundled by default) -- this registers a PIL-compatible plugin so every
# Image.open() below transparently accepts .heic/.heif photos (e.g. straight
# off an iPhone) the same as JPEG/PNG/WebP, with no call-site changes needed.
pillow_heif.register_heif_opener()


VISION_PREVIEW_MAX_SIDE = 1024


# ── Bytes / encoding helpers ───────────────────────────────────────────────────

def create_preview_bytes(image_bytes: bytes, max_side: int = VISION_PREVIEW_MAX_SIDE) -> bytes:
    img = _open_rgb(image_bytes)
    longest = max(img.width, img.height)
    if longest <= max_side:
        return _to_jpeg_bytes(img)
    scale = max_side / longest
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return _to_jpeg_bytes(img)


def ensure_png_bytes(image_bytes: bytes) -> bytes:
    """Re-encode arbitrary input bytes (HEIC/JPEG/WebP/PNG/...) as a real PNG.

    The edit-model APIs (OpenAI images/edits, Gemini inlineData) are told the
    upload is "image/png" regardless of what the user actually uploaded -- if
    the source bytes aren't genuinely PNG-encoded (most importantly a
    HEIC/HEIF photo straight off an iPhone, which neither API can decode
    under any label), the edit call fails outright with an "invalid image
    file" error. Pillow can already open every format this app accepts (see
    pillow_heif.register_heif_opener above), so normalize once here instead
    of trusting the original bytes to already be what we claim they are.
    """
    return _to_png_bytes(_open_rgb(image_bytes))


def image_to_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def get_image_dimensions(image_bytes: bytes):
    img = Image.open(io.BytesIO(image_bytes))
    return img.width, img.height


def _open_rgb(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _to_jpeg_bytes(img: Image.Image, quality: int = 88) -> bytes:
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _to_png_bytes(img: Image.Image) -> bytes:
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Aspect ratio ───────────────────────────────────────────────────────────────

def parse_aspect_ratio(aspect_ratio: str):
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", str(aspect_ratio or ""))
    if not m:
        return None
    w, h = float(m.group(1)), float(m.group(2))
    if not (math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0):
        return None
    return w / h


def clean_aspect_ratio(aspect_ratio) -> str:
    value = str(aspect_ratio or "").strip().lower()
    if not value or value in ("original", "none"):
        return ""
    return value if parse_aspect_ratio(value) else ""


def _recommended_aspect_ratio(context: dict, fallback: str = "") -> str:
    return clean_aspect_ratio(
        context.get("recommended_aspect_ratio")
        or (context.get("crop_box") or {}).get("aspect_ratio")
        or (context.get("crop_box") or {}).get("aspectRatio")
        or fallback
    )


def apply_aspect_ratio_preference(context: dict, aspect_ratio_pref: str) -> dict:
    """When the user explicitly asked to keep the original photo's aspect
    ratio, force recommended_aspect_ratio/crop_box(.candidates) aspect_ratio
    to "original" so prompt text and fit_crop_to_aspect's local crop geometry
    don't fight that choice. Any other value ("auto") leaves the analysis's
    own recommendation untouched -- same "unset/other -> passthrough"
    contract as apply_framing_size."""
    if aspect_ratio_pref != "original":
        return context

    ctx = dict(context)
    ctx["recommended_aspect_ratio"] = "original"

    crop_box = context.get("crop_box")
    if crop_box:
        ctx["crop_box"] = {**crop_box, "aspect_ratio": "original"}

    candidates = context.get("crop_candidates")
    if candidates:
        ctx["crop_candidates"] = [
            {**c, "crop_box": {**c["crop_box"], "aspect_ratio": "original"}} if c.get("crop_box") else c
            for c in candidates
        ]
    return ctx


# Fixed size/aspect enums the edit-model APIs that *can't* take an arbitrary
# ratio accept -- both are closed sets (OpenAI images/edits `size` for
# gpt-image-1.5, Gemini imageConfig.aspectRatio), so a continuous target ratio
# has to snap to the nearest one.
_OPENAI_EDIT_SIZE_CHOICES = [("1024x1024", 1.0), ("1536x1024", 1536 / 1024), ("1024x1536", 1024 / 1536)]
_GEMINI_ASPECT_RATIO_CHOICES = [
    ("1:1", 1.0), ("2:3", 2 / 3), ("3:2", 3 / 2), ("3:4", 3 / 4), ("4:3", 4 / 3),
    ("4:5", 4 / 5), ("5:4", 5 / 4), ("9:16", 9 / 16), ("16:9", 16 / 9), ("21:9", 21 / 9),
]

# gpt-image-2 (unlike gpt-image-1.5) accepts an arbitrary "WIDTHxHEIGHT" `size`
# instead of a fixed enum, so the source photo's exact aspect ratio can be
# requested directly rather than snapped. The API still requires both edges
# divisible by 16 and the ratio within 1:3..3:1.
_OPENAI_ARBITRARY_SIZE_MODELS = {"gpt-image-2"}
_OPENAI_SIZE_GRID = 16
_OPENAI_SIZE_MIN_RATIO = 1 / 3
_OPENAI_SIZE_MAX_RATIO = 3.0
_OPENAI_SIZE_TARGET_PIXELS = 1536 * 1024  # same pixel budget as the old 1536x1024 enum choice


def _nearest_by_ratio(ratio: float, choices: list) -> str:
    return min(choices, key=lambda choice: abs(math.log(ratio / choice[1])))[0]


def nearest_openai_edit_size(image_width: int, image_height: int) -> str:
    """Snap the source photo's own ratio onto OpenAI's fixed images/edits
    `size` enum -- only meant to be called for the "original" aspect-ratio
    preference, for models without arbitrary-size support (see
    supports_arbitrary_openai_size). "auto" is intentionally left alone
    (passed through as the literal "auto" value) so the edit model keeps the
    same freedom it always had to size the output however it judges best;
    forcing "auto" through this same snap made it degenerate into "original"
    in practice, since the analysis usually recommends
    recommended_aspect_ratio="original" itself."""
    ratio = (image_width / image_height) if image_width and image_height else 1.0
    return _nearest_by_ratio(ratio, _OPENAI_EDIT_SIZE_CHOICES)


def supports_arbitrary_openai_size(model: str) -> bool:
    return model in _OPENAI_ARBITRARY_SIZE_MODELS


def openai_edit_size_for_ratio(image_width: int, image_height: int) -> str:
    """Build a gpt-image-2 `size` string matching the source photo's own
    aspect ratio as closely as the API allows, instead of snapping to one of
    nearest_openai_edit_size's fixed choices. Only meant to be called for
    models where supports_arbitrary_openai_size is true."""
    ratio = (image_width / image_height) if image_width and image_height else 1.0
    ratio = max(_OPENAI_SIZE_MIN_RATIO, min(_OPENAI_SIZE_MAX_RATIO, ratio))

    height = math.sqrt(_OPENAI_SIZE_TARGET_PIXELS / ratio)
    width = height * ratio

    def snap(value: float) -> int:
        return max(_OPENAI_SIZE_GRID, round(value / _OPENAI_SIZE_GRID) * _OPENAI_SIZE_GRID)

    return f"{snap(width)}x{snap(height)}"


def nearest_gemini_aspect_ratio(image_width: int, image_height: int) -> str:
    """Same contract as nearest_openai_edit_size, for Gemini's
    imageConfig.aspectRatio -- only called for the "original" preference."""
    ratio = (image_width / image_height) if image_width and image_height else 1.0
    return _nearest_by_ratio(ratio, _GEMINI_ASPECT_RATIO_CHOICES)


# ── ROI / protected box helpers ────────────────────────────────────────────────

def normalize_roi_box(roi_box: dict):
    if not roi_box:
        return None
    try:
        x, y, w, h = (
            float(roi_box.get("x", 0)),
            float(roi_box.get("y", 0)),
            float(roi_box.get("w", 0)),
            float(roi_box.get("h", 0)),
        )
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    cx, cy = max(0, min(0.98, x)), max(0, min(0.98, y))
    return {"x": cx, "y": cy, "w": max(0.02, min(1 - cx, w)), "h": max(0.02, min(1 - cy, h))}


def protected_boxes(context: dict) -> list:
    boxes = []

    def _add(box, kind, label=None):
        n = normalize_roi_box(box)
        if not n:
            return
        boxes.append({
            **n,
            "kind": kind,
            "label": label or (box or {}).get("label") or (box or {}).get("reason") or kind,
        })

    _add(context.get("roi_box"), "roi", "ROI")
    _add(context.get("subject_extent_box"), "subject", "subject extent")
    for r in context.get("protected_regions") or []:
        _add(r, "protected", (r or {}).get("label"))

    return boxes


def _include_protected_box(crop: dict, box: dict) -> dict:
    safety = 0.05 if box["kind"] == "roi" else 0.065
    pl = max(0, box["x"] - safety)
    pt = max(0, box["y"] - safety)
    pr = min(1, box["x"] + box["w"] + safety)
    pb = min(1, box["y"] + box["h"] + safety)

    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]

    if x > pl:
        w = min(1 - pl, w + (x - pl))
        x = pl
    if x + w < pr:
        delta = pr - (x + w)
        x = x - delta if x >= delta else x
        w = min(1 - x, w + delta)
    if y > pt:
        h = min(1 - pt, h + (y - pt))
        y = pt
    if y + h < pb:
        delta = pb - (y + h)
        y = y - delta if y >= delta else y
        h = min(1 - y, h + delta)

    x, y = max(0, min(0.98, x)), max(0, min(0.98, y))
    return {**crop, "x": x, "y": y, "w": max(0.02, min(1 - x, w)), "h": max(0.02, min(1 - y, h))}


def _protect_roi(crop: dict, context: dict) -> dict:
    for box in protected_boxes(context):
        crop = _include_protected_box(crop, box)
    return crop


# ── Framing size (close / closer) geometry ─────────────────────────────────────

# How much breathing room close/closer keep around the chosen subject,
# expressed as a fraction of the subject box's own size added on each side.
FRAMING_CLOSE_PADDING = 0.15    # moderate: subject plus some surrounding context
FRAMING_CLOSER_PADDING = 0.08   # tight single-subject close-up (non-person)
# For a person, expand the face-ish ROI into a face+shoulders crop.
FRAMING_PERSON_WIDTH_SCALE = 1.6
FRAMING_PERSON_DOWN_SCALE = 1.8   # extend downward toward shoulders/chest
FRAMING_PERSON_HEADROOM = 0.12    # fraction of face height kept above the head
# Minimum fraction of the ROI a subject must cover to count as "inside" the
# focal region when picking which single subject a closer close-up features.
ROI_OVERLAP_MIN = 0.15


def _pad_box(box: dict, pad: float) -> dict:
    """Grow a normalized box outward by pad * its own size on each side."""
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    px, py = w * pad, h * pad
    nx = max(0.0, x - px)
    ny = max(0.0, y - py)
    nr = min(1.0, x + w + px)
    nb = min(1.0, y + h + py)
    return {"x": nx, "y": ny, "w": nr - nx, "h": nb - ny}


def _clamp_within(inner: dict, outer: dict) -> dict:
    """Clamp inner so it stays inside outer."""
    ox, oy = outer["x"], outer["y"]
    orr, ob = outer["x"] + outer["w"], outer["y"] + outer["h"]
    x = min(max(inner["x"], ox), orr)
    y = min(max(inner["y"], oy), ob)
    r = min(max(inner["x"] + inner["w"], ox), orr)
    b = min(max(inner["y"] + inner["h"], oy), ob)
    return {"x": x, "y": y, "w": max(0.02, r - x), "h": max(0.02, b - y)}


def _box_center_inside(inner: dict, outer: dict) -> bool:
    cx = inner["x"] + inner["w"] / 2
    cy = inner["y"] + inner["h"] / 2
    return (outer["x"] <= cx <= outer["x"] + outer["w"]
            and outer["y"] <= cy <= outer["y"] + outer["h"])


def _shrink_toward_center(box: dict, max_w: float, max_h: float) -> dict:
    """Cap a box's size while keeping its center, clamped to [0, 1]."""
    cx = box["x"] + box["w"] / 2
    cy = box["y"] + box["h"] / 2
    w = min(box["w"], max_w)
    h = min(box["h"], max_h)
    x = max(0.0, min(1 - w, cx - w / 2))
    y = max(0.0, min(1 - h, cy - h / 2))
    return {"x": x, "y": y, "w": w, "h": h}


def _person_head_shoulders(roi: dict, primary: dict) -> dict:
    """Expand a face-ish ROI into a face+shoulders box, kept inside primary."""
    x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
    cx = x + w / 2
    new_w = w * FRAMING_PERSON_WIDTH_SCALE
    new_x = cx - new_w / 2
    new_y = max(0.0, y - h * FRAMING_PERSON_HEADROOM)
    new_h = (y + h - new_y) + h * FRAMING_PERSON_DOWN_SCALE
    return _clamp_within({"x": new_x, "y": new_y, "w": new_w, "h": new_h}, primary)


def _intersection_area(a: dict, b: dict) -> float:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2 = min(a["x"] + a["w"], b["x"] + b["w"])
    y2 = min(a["y"] + a["h"], b["y"] + b["h"])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def _select_primary_subject(context: dict):
    """Return (box, is_person, label) for the single subject a close-up should
    feature, or None.

    subjects[] is ordered by the analysis so higher `prominence` means greater
    semantic importance as the subject -- NOT which element is merely brightest
    or most saturated (see the analysis prompt). prominence is therefore the
    primary arbiter of which subject to feature.

    The ROI (roi_box) is the analysis's focal region. In a group it usually
    spans several people, so it is used to *filter out* subjects that sit off
    the focal area rather than to rank the ones inside it: among subjects that
    meaningfully overlap the ROI we feature the most prominent one (ties broken
    by ROI coverage). This stops an off-to-the-side or background person being
    chosen while still letting prominence decide between the central candidates.
    If no subject overlaps the ROI we fall back to the most prominent subject
    overall, then to the ROI box itself."""
    subjects = context.get("subjects") or []
    roi = normalize_roi_box(context.get("roi_box"))

    def _prom(s):
        # Higher == more prominent; missing prominence ranks last.
        return (s or {}).get("prominence", -1)

    if subjects:
        # Most prominent first (was previously sorted ascending, which selected
        # the LEAST prominent subject in the fallback path).
        ranked = sorted(subjects, key=_prom, reverse=True)
        if roi:
            roi_area = (roi["w"] * roi["h"]) or 1e-9

            def _roi_cover(s):
                b = normalize_roi_box((s or {}).get("box"))
                return (_intersection_area(b, roi) / roi_area) if b else 0.0

            in_roi = [s for s in ranked if _roi_cover(s) >= ROI_OVERLAP_MIN]
            if in_roi:
                best = max(in_roi, key=lambda s: (_prom(s), _roi_cover(s)))
                box = normalize_roi_box(best.get("box"))
                if box:
                    return box, bool(best.get("is_person")), (best.get("label") or "")
        top = ranked[0] or {}
        box = normalize_roi_box(top.get("box"))
        if box:
            return box, bool(top.get("is_person")), (top.get("label") or "")

    if roi:
        return roi, None, ""
    return None


def apply_framing_size(context: dict, size: str) -> dict:
    """Return a context whose crop geometry reflects the selected framing size.

    wide/wider (and unset) are returned unchanged -- those remain text-only
    style hints. close/closer rewrite crop_box; closer additionally collapses
    the protected geometry (roi_box / subject_extent_box / protected_regions)
    onto a single subject so resolve_crop_box_with_report's ROI protection does
    not re-expand the frame back over the other subjects."""
    if size not in ("close", "closer"):
        return context

    if size == "close":
        base = normalize_roi_box(context.get("subject_extent_box")) or normalize_roi_box(context.get("roi_box"))
        if not base:
            return context
        target = _pad_box(base, FRAMING_CLOSE_PADDING)
        ctx = dict(context)
        ctx["crop_box"] = {
            "enabled": True,
            "x": target["x"], "y": target["y"], "w": target["w"], "h": target["h"],
            "aspect_ratio": "",
            "reason": "framing size: close -- tighten toward the subject.",
        }
        return ctx

    # closer -- isolate a single subject
    selected = _select_primary_subject(context)
    if not selected:
        return context
    primary, is_person, label = selected

    if is_person:
        roi = normalize_roi_box(context.get("roi_box"))
        if roi and _box_center_inside(roi, primary):
            subject_box = _person_head_shoulders(roi, primary)
        else:
            subject_box = _pad_box(primary, FRAMING_CLOSER_PADDING)
    else:
        subject_box = _pad_box(primary, FRAMING_CLOSER_PADDING)

    # Guard: if the scene has several subjects but the chosen box still spans
    # most of the frame, analysis probably returned the whole group rather than
    # one subject. Shrink it toward its center to a plausible single-subject
    # size so closer still isolates one subject instead of the group.
    multi = len(context.get("subjects") or []) > 1 or len(context.get("protected_regions") or []) > 1
    if multi and (subject_box["w"] * subject_box["h"] > 0.5 or subject_box["w"] > 0.72):
        subject_box = _shrink_toward_center(subject_box, max_w=0.5, max_h=0.85)

    ctx = dict(context)
    ctx["crop_box"] = {
        "enabled": True,
        "x": subject_box["x"], "y": subject_box["y"], "w": subject_box["w"], "h": subject_box["h"],
        "aspect_ratio": "",
        "reason": "framing size: closer -- isolate a single subject.",
    }
    # Collapse protected geometry onto the chosen subject so _protect_roi does
    # not pull the crop back out over the other subjects. Only roi_box is kept
    # as the single protection anchor -- setting subject_extent_box too would
    # stack a second safety margin and inflate the closer crop.
    ctx["roi_box"] = {**subject_box, "reason": "closer single-subject focus"}
    ctx["subject_extent_box"] = None
    ctx["protected_regions"] = []
    # Overwrite the scene description's group-level subject fields so the edit
    # model does not reassert "the four women" as the subject to keep. The image
    # itself still carries identity/lighting context; these strings only steer
    # what to feature.
    focus_label = label or "單一最突出的主體"
    ctx["main_subject"] = f"{focus_label}(單一主體特寫,其餘人物/元素刻意裁切出畫面)"
    ctx["photo_summary"] = (
        f"單一主體特寫,以{focus_label}為主角並填滿畫面。原始場景可能包含多個人物或元素,"
        f"但此特寫刻意只聚焦於此主體。"
    )
    # Marker consumed by build_edit_prompt / QA so the scene description's other
    # people/elements don't get reasserted as content to preserve.
    ctx["_framing_focus"] = {"size": "closer", "label": label}
    return ctx


# ── Crop box normalization ─────────────────────────────────────────────────────

def normalize_crop_box(crop_box: dict, context: dict):
    if not crop_box or crop_box.get("enabled") is not True:
        return None
    try:
        x, y, w, h = (
            float(crop_box.get("x", 0)),
            float(crop_box.get("y", 0)),
            float(crop_box.get("w", 0)),
            float(crop_box.get("h", 0)),
        )
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None

    cx, cy = max(0, min(0.95, x)), max(0, min(0.95, y))
    crop = {
        "x": cx,
        "y": cy,
        "w": max(0.12, min(1 - cx, w)),
        "h": max(0.12, min(1 - cy, h)),
        "aspectRatio": clean_aspect_ratio(
            crop_box.get("aspect_ratio") or crop_box.get("aspectRatio") or _recommended_aspect_ratio(context)
        ),
    }
    crop = _protect_roi(crop, context)
    if crop["w"] > 0.965 and crop["h"] > 0.965:
        return None
    return crop


def clamp_raw_crop_box(crop_box: dict, context: dict) -> dict:
    x = max(0, min(0.95, float(crop_box.get("x") or 0)))
    y = max(0, min(0.95, float(crop_box.get("y") or 0)))
    w = max(0.02, min(1 - x, float(crop_box.get("w") or 1)))
    h = max(0.02, min(1 - y, float(crop_box.get("h") or 1)))
    return {
        "enabled": True,
        "x": x, "y": y, "w": w, "h": h,
        "aspect_ratio": clean_aspect_ratio(
            crop_box.get("aspect_ratio") or _recommended_aspect_ratio(context)
        ),
    }


def fit_crop_to_aspect(crop: dict, image_width: int, image_height: int, context: dict) -> dict:
    target = parse_aspect_ratio(crop.get("aspectRatio", ""))
    if not target or not image_width or not image_height:
        return crop

    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    current = (w * image_width) / (h * image_height)
    if abs(current - target) < 0.01:
        return crop

    anchor = str(
        (context.get("crop_intent") or {}).get("subject_anchor") or context.get("roi_anchor") or ""
    ).lower()

    if current > target:
        new_w = max(0.12, min(w, target * h * image_height / image_width))
        center = x + w / 2
        if "right" in anchor:
            x = max(0, min(1 - new_w, x + w - new_w))
        elif "left" in anchor:
            x = max(0, min(1 - new_w, x))
        else:
            x = max(0, min(1 - new_w, center - new_w / 2))
        w = new_w
    else:
        new_h = max(0.12, min(h, (w * image_width) / (target * image_height)))
        center = y + h / 2
        if "upper" in anchor:
            y = max(0, min(1 - new_h, y))
        else:
            y = max(0, min(1 - new_h, center - new_h / 2))
        h = new_h

    return _protect_roi({**crop, "x": x, "y": y, "w": w, "h": h}, context)


# ── Crop report ────────────────────────────────────────────────────────────────

def _crop_delta_reasons(before: dict, after: dict, stage: str) -> list:
    if not before or not after:
        return []
    changed = any(abs(float(before.get(k, 0)) - float(after.get(k, 0))) > 0.004 for k in ("x", "y", "w", "h"))
    return [stage] if changed else []


def resolve_crop_box_with_report(crop_box: dict, context: dict, image_width: int, image_height: int) -> dict:
    raw = clamp_raw_crop_box(crop_box, context) if (crop_box or {}).get("enabled") is True else None
    normalized = normalize_crop_box(crop_box, context)

    if not normalized:
        return {
            "cropBox": None,
            "adjustmentReasons": (
                ["local geometry disabled crop because normalized crop was invalid or effectively full-frame"]
                if raw else []
            ),
        }

    fitted = fit_crop_to_aspect(normalized, image_width, image_height, context)
    reasons = list(set(
        _crop_delta_reasons(raw, normalized, "local geometry clamped bounds or expanded crop to protect ROI/protected regions")
        + _crop_delta_reasons(normalized, fitted, "local geometry adjusted crop to satisfy aspect ratio while protecting safety regions")
    ))
    return {"cropBox": fitted, "adjustmentReasons": reasons}


# ── Image crop operations ──────────────────────────────────────────────────────

def create_cropped_bytes(image_bytes: bytes, crop_box: dict, context: dict) -> bytes:
    normalized = normalize_crop_box(crop_box, context)
    if not normalized:
        return image_bytes

    img = _open_rgb(image_bytes)
    fitted = fit_crop_to_aspect(normalized, img.width, img.height, context)

    sx = max(0, min(round(fitted["x"] * img.width), img.width - 1))
    sy = max(0, min(round(fitted["y"] * img.height), img.height - 1))
    sw = max(1, min(round(fitted["w"] * img.width), img.width - sx))
    sh = max(1, min(round(fitted["h"] * img.height), img.height - sy))

    return _to_png_bytes(img.crop((sx, sy, sx + sw, sy + sh)))


def create_local_enhanced_bytes(image_bytes: bytes, crop_box: dict, context: dict) -> bytes:
    cropped = create_cropped_bytes(image_bytes, crop_box, context)
    img = _open_rgb(cropped)

    img = ImageEnhance.Brightness(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.06)

    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.012 + 2)))
    g = g.point(lambda v: min(255, int(v * 1.006 + 1)))
    b = b.point(lambda v: max(0, int(v * 0.992)))
    img = Image.merge("RGB", (r, g, b))

    return _to_png_bytes(img)
