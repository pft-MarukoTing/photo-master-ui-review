import io
import math
import base64
import re

from PIL import Image, ImageEnhance


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


# ── Crop candidates ────────────────────────────────────────────────────────────

def _candidate_key(crop_box: dict) -> str:
    if not crop_box or not crop_box.get("enabled"):
        return "original-frame"
    return ":".join([
        crop_box.get("aspect_ratio") or "",
        f"{float(crop_box.get('x', 0)):.2f}",
        f"{float(crop_box.get('y', 0)):.2f}",
        f"{float(crop_box.get('w', 0)):.2f}",
        f"{float(crop_box.get('h', 0)):.2f}",
    ])


def build_crop_candidates(analysis: dict, high_risk: bool = False) -> list:
    candidates, seen = [], set()

    def _add(candidate):
        key = _candidate_key(candidate.get("cropBox") or candidate.get("crop_box"))
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    for i, c in enumerate(analysis.get("crop_candidates") or []):
        src = c.get("crop_box") or c.get("cropBox")
        if not src:
            continue
        crop_box = (
            {"enabled": False, "x": 0, "y": 0, "w": 1, "h": 1, "aspect_ratio": ""}
            if src.get("enabled") is False
            else clamp_raw_crop_box(src, analysis)
        )
        _add({
            "id": c.get("id") or f"ai-candidate-{i + 1}",
            "label": c.get("label") or f"AI 候選 {i + 1}",
            "cropBox": crop_box,
            "compositionIntent": c.get("composition_intent") or c.get("compositionIntent") or "",
            "tradeoffs": c.get("tradeoffs") or "",
        })

    if (analysis.get("crop_box") or {}).get("enabled") is True:
        base = clamp_raw_crop_box(analysis["crop_box"], analysis)
        _add({
            "id": "ai-plan",
            "label": "AI 初始裁切建議",
            "cropBox": base,
            "compositionIntent": analysis.get("composition_intent") or "",
            "tradeoffs": analysis.get("crop_recommendation") or "",
        })

    _add({
        "id": "original-frame",
        "label": "保留原始構圖",
        "cropBox": {"enabled": False, "x": 0, "y": 0, "w": 1, "h": 1, "aspect_ratio": ""},
        "compositionIntent": "保留完整原圖與原始場景脈絡",
        "tradeoffs": "不做構圖裁切，只進行修圖與色彩整理",
    })

    limit = 6 if high_risk else 4
    if len(candidates) <= limit:
        return candidates
    original = next((c for c in candidates if c["id"] == "original-frame"), None)
    selected = [c for c in candidates if c["id"] != "original-frame"][: max(0, limit - 1)]
    if original:
        selected.append(original)
    return selected


def crop_validation_risk(analysis: dict) -> dict:
    normalized = normalize_crop_box(analysis.get("crop_box"), analysis)
    if not normalized:
        return {"shouldValidate": False, "level": "none", "reasons": []}

    reasons = []
    area = normalized["w"] * normalized["h"]
    intent = analysis.get("crop_intent") or {}

    if (analysis.get("crop_risk") or {}).get("needs_validation") is True:
        reasons.append("analysis-risk")
    if area < 0.72:
        reasons.append("large-crop")
    if normalized["x"] > 0.08 or normalized["y"] > 0.08 or normalized["w"] < 0.86 or normalized["h"] < 0.86:
        reasons.append("meaningful-reframe")
    if intent.get("trim_left") or intent.get("trim_right") or intent.get("trim_top") or intent.get("trim_bottom"):
        reasons.append("edge-trim")
    for box in protected_boxes(analysis):
        if box["x"] < 0.08 or box["y"] < 0.08 or box["x"] + box["w"] > 0.92 or box["y"] + box["h"] > 0.92:
            reasons.append(f"{box['kind']}-near-edge")

    level = "high" if len(reasons) >= 2 else ("medium" if reasons else "low")
    return {"shouldValidate": bool(reasons), "level": level, "reasons": reasons}


def user_direction_affects_crop(user_instruction: dict, raw_direction: str) -> bool:
    if (user_instruction or {}).get("crop_or_framing_requested") is True:
        return True
    texts = [
        raw_direction or "",
        (user_instruction or {}).get("user_intent_summary") or "",
        (user_instruction or {}).get("normalized_user_direction") or "",
        (user_instruction or {}).get("crop_guidance") or "",
        *((user_instruction or {}).get("explicit_priorities") or []),
        *((user_instruction or {}).get("explicit_prohibitions") or []),
    ]
    text = " ".join(filter(None, texts)).lower()
    return bool(re.search(
        r"裁切|不要裁|別裁|構圖|取景|比例|留白|邊緣|邊界|全身|半身|特寫|置中|水平|透視|"
        r"crop|framing|frame|reframe|aspect|composition|center|edge|border",
        text,
    ))


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


def create_candidate_preview_bytes(image_bytes: bytes, crop_box: dict, context: dict) -> bytes:
    if not crop_box or not crop_box.get("enabled"):
        return create_preview_bytes(image_bytes)

    raw = clamp_raw_crop_box(crop_box, context)
    img = _open_rgb(image_bytes)

    sx = max(0, min(round(raw["x"] * img.width), img.width - 1))
    sy = max(0, min(round(raw["y"] * img.height), img.height - 1))
    sw = max(1, min(round(raw["w"] * img.width), img.width - sx))
    sh = max(1, min(round(raw["h"] * img.height), img.height - sy))

    return _to_jpeg_bytes(img.crop((sx, sy, sx + sw, sy + sh)))
