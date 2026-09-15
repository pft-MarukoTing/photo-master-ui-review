import io
import logging
import math
import os
import base64
import re
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps
import pillow_heif

try:
    from aiserver_utils.logger import logger as aiutils_logger
except Exception:
    # That logger writes to LOG_PATH and cannot even be constructed without it,
    # which the service platform sets but a test run or a local script does not.
    # The timings below are telemetry, so dropping them beats being unimportable.
    class _DroppedTelemetry:
        def log(self, *args, **kwargs):
            pass

    aiutils_logger = _DroppedTelemetry()
from sdl_face_detector import SDLFaceDetector

logger = logging.getLogger(__name__)

# Pillow has no built-in HEIC/HEIF decoder (patent-encumbered codec, not
# bundled by default) -- this registers a PIL-compatible plugin so every
# Image.open() below transparently accepts .heic/.heif photos (e.g. straight
# off an iPhone) the same as JPEG/PNG/WebP, with no call-site changes needed.
pillow_heif.register_heif_opener()

# Both cv2's DNN inference and onnxruntime's CPUExecutionProvider (used below
# by the sdl face detector) default to fanning a single inference call out
# across every logical core on the box. Detection is already serialized
# process-wide by _FACE_DETECTOR_LOCK, so that parallelism buys no extra
# throughput -- it just means each request briefly saturates all cores.
# Capped here instead; tunable via env for perf testing.
_INFERENCE_NUM_THREADS = max(int(os.getenv("PHOTOAGENT_CV2_NUM_THREADS", "1")), 1)
cv2.setNumThreads(_INFERENCE_NUM_THREADS)


VISION_PREVIEW_MAX_SIDE = 1024

# Small JPEG variant used purely for fast-loading UI surfaces (history list
# thumbnails) -- much smaller than VISION_PREVIEW_MAX_SIDE since these are
# rendered at a few dozen CSS pixels, not analyzed by a model.
HISTORY_THUMBNAIL_MAX_SIDE = 320

# Debug-only escape hatch for comparing analysis quality with vs. without the
# preview downscale -- set PHOTOAGENT_SKIP_PREVIEW_RESIZE=1 in the worker's
# environment to send the model the full-resolution photo instead. Only
# applies to the VISION_PREVIEW_MAX_SIDE (analysis) call sites below, never to
# an explicit smaller max_side like HISTORY_THUMBNAIL_MAX_SIDE.
_SKIP_VISION_PREVIEW_RESIZE = False


# ── Bytes / encoding helpers ───────────────────────────────────────────────────

def create_preview_bytes(image_bytes: bytes, max_side: int = VISION_PREVIEW_MAX_SIDE) -> bytes:
    t0 = time.perf_counter()
    img = _open_rgb(image_bytes)
    resized = False
    if max_side == VISION_PREVIEW_MAX_SIDE and _SKIP_VISION_PREVIEW_RESIZE:
        result = _to_jpeg_bytes(img)
    else:
        longest = max(img.width, img.height)
        if longest <= max_side:
            result = _to_jpeg_bytes(img)
        else:
            scale = max_side / longest
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
            result = _to_jpeg_bytes(img)
            resized = True
    aiutils_logger.log('[create_preview_bytes] timing', total_ms=round((time.perf_counter() - t0) * 1000, 1),
                        resized=resized, src_dims=f"{img.width}x{img.height}")
    return result


_FACE_DETECTOR = None
# ENGINE_WORKER_NUM (server.py) runs several jobs concurrently as threads in
# one process, all sharing this lazily-created detector. onnxruntime sessions
# are safe to call concurrently, but the lock is kept anyway so detection
# stays single-flight per the _INFERENCE_NUM_THREADS reasoning above, and to
# avoid re-litigating thread-safety if the model is swapped again later.
_FACE_DETECTOR_LOCK = threading.Lock()
_FACE_DETECTOR_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "face_detector_sdl.onnx")
_FACE_DETECT_CONFIDENCE_THRESHOLD = 0.739
_FACE_DETECT_NMS_THRESHOLD = 0.4
_FACE_CROP_MAX_SIDE = 768
_FACE_CROP_MAX_COUNT = 3


def _face_detector() -> SDLFaceDetector:
    global _FACE_DETECTOR
    if _FACE_DETECTOR is None:
        _FACE_DETECTOR = SDLFaceDetector(_FACE_DETECTOR_MODEL_PATH, intra_op_num_threads=_INFERENCE_NUM_THREADS)
    return _FACE_DETECTOR


def detect_face_crops(image_bytes: bytes) -> list:
    """Zoomed-in JPEG crops of the most confidently detected faces, for
    handing to the analysis model alongside the full-frame preview.

    Vision APIs resample any input image down to their own fixed canonical
    resolution before tiling it for the model (measured empirically: sending
    a 1024px vs. 1536px preview produced the same image_tokens), so on a
    half-body/full-body/group photo each eye ends up a handful of pixels
    regardless of how large a preview we send -- raising VISION_PREVIEW_MAX_SIDE
    does not help. A face crop gets its own canonical-resolution budget spent
    entirely on that one face, which is the only way to give the model more
    effective eye resolution to judge blink state, gaze/pupil direction, and
    the lip and teeth detail a smile reading needs.

    Best-effort only: even this detector (a small DNN, well past Haar cascade
    in handling non-frontal/occluded faces and background-clutter false
    positives) can still miss a face, so an empty list is the expected outcome
    for plenty of photos, not an error -- callers must keep working from the
    full photo alone when this returns nothing.

    Returns a list of {"box": {x, y, w, h}, "face_box": {x, y, w, h}, "bytes":
    jpeg_bytes_or_None}, largest face first. Both boxes are normalized against
    the original (EXIF-transposed) image, same convention as roi_box/
    protected_regions, so callers can hand either straight to the frontend's
    analysis-box overlay without a separate coordinate space. "box" is the
    padded crop (see the 0.6/0.4 padding below); "face_box" is the detector's
    own tight, unpadded bbox -- the one worth handing to the analysis model as
    a face-location reference, since "box" would put a person-sized gap
    between the stated coordinates and the actual face.

    Only the first _FACE_CROP_MAX_COUNT entries have "bytes" populated and are
    meant to be sent to the model as zoomed images -- a token-cost cap, not a
    detection limit. A photo can have more faces than that; the rest still
    come back with real "box"/"face_box" values (cheap: pure arithmetic, no
    image crop/resize/encode work) and "bytes"=None, so callers can still map
    them against subjects[] and surface them (e.g. in the UI's face-thumbnail
    row) without paying to send them to the model.
    """
    img = _open_rgb(image_bytes)
    if img.width < 1 or img.height < 1:
        return []

    t_cvt0 = time.perf_counter()
    # The detector letterboxes to its own 640px input internally, so it's fed
    # the full-resolution image directly rather than pre-downscaled here.
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    t_detect0 = time.perf_counter()
    try:
        detector = _face_detector()
        with _FACE_DETECTOR_LOCK:
            faces = detector.detect(bgr, _FACE_DETECT_CONFIDENCE_THRESHOLD, _FACE_DETECT_NMS_THRESHOLD)
    except Exception:
        # Best-effort detection: no crop is a normal, handled outcome (see
        # docstring), never worth failing the whole analysis over.
        logger.warning("detect_face_crops: face detector failed, continuing without face crops", exc_info=True)
        return []
    t_detect1 = time.perf_counter()
    aiutils_logger.log('[detect_face_crops] timing', src_dims=f"{img.width}x{img.height}",
                        cvtcolor_ms=round((t_detect0 - t_cvt0) * 1000, 1),
                        detect_ms=round((t_detect1 - t_detect0) * 1000, 1))
    if not faces:
        return []

    # bbox is [x1, y1, x2, y2] in the original image's pixel space already.
    boxes = [(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in (f["bbox"] for f in faces)]
    boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    largest_area = boxes[0][2] * boxes[0][3]
    # A real face's false-positive companions from background texture/pattern
    # are usually far smaller than the real thing in the same frame; this
    # drops those without needing a second model to tell them apart.
    filtered = [b for b in boxes if b[2] * b[3] >= largest_area * 0.2]
    # Only the first _FACE_CROP_MAX_COUNT faces get an actual cropped image
    # sent to the analysis model (token-cost cap). A photo can legitimately
    # have more locally-detected faces than that -- those still get a
    # {"box", "face_box"} entry below (bytes=None) so callers can map them
    # against subjects[] and surface them in the UI, just without a zoomed
    # crop or "Local face detector reference" text ever reaching the model.
    kept, extra = filtered[:_FACE_CROP_MAX_COUNT], filtered[_FACE_CROP_MAX_COUNT:]
    logger.info("detect_face_crops: image=%dx%d raw_boxes=%s kept=%s extra=%s", img.width, img.height, boxes, kept, extra)

    def _padded_box(x, y, w, h):
        left = min(max(0, x - round(w * 0.6)), img.width)
        top = min(max(0, y - round(h * 0.6)), img.height)
        right = max(min(img.width, x + w + round(w * 0.6)), 0)
        bottom = max(min(img.height, y + h + round(h * 0.4)), 0)
        return left, top, right, bottom

    t_crop0 = time.perf_counter()
    crops = []
    for x, y, w, h in kept:
        left, top, right, bottom = _padded_box(x, y, w, h)
        logger.info("detect_face_crops: raw_box=(%d,%d,%d,%d) crop_box=(left=%d,top=%d,right=%d,bottom=%d)", x, y, w, h, left, top, right, bottom)
        crop = img.crop((left, top, right, bottom))
        longest = max(crop.width, crop.height)
        if longest < _FACE_CROP_MAX_SIDE:
            scale = _FACE_CROP_MAX_SIDE / longest
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
        crops.append({
            "box": {
                "x": left / img.width,
                "y": top / img.height,
                "w": (right - left) / img.width,
                "h": (bottom - top) / img.height,
            },
            "face_box": {
                "x": x / img.width,
                "y": y / img.height,
                "w": w / img.width,
                "h": h / img.height,
            },
            "bytes": _to_jpeg_bytes(crop),
        })
    aiutils_logger.log('[detect_face_crops] crop timing', crop_count=len(kept),
                        crop_total_ms=round((time.perf_counter() - t_crop0) * 1000, 1))

    # Cheap (arithmetic only, no PIL crop/resize/encode) entries for the
    # locally-detected faces beyond the crop cap -- never sent to the model.
    for x, y, w, h in extra:
        left, top, right, bottom = _padded_box(x, y, w, h)
        crops.append({
            "box": {
                "x": left / img.width,
                "y": top / img.height,
                "w": (right - left) / img.width,
                "h": (bottom - top) / img.height,
            },
            "face_box": {
                "x": x / img.width,
                "y": y / img.height,
                "w": w / img.width,
                "h": h / img.height,
            },
            "bytes": None,
        })
    return crops


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


def ensure_jpeg_bytes(image_bytes: bytes) -> bytes:
    """Re-encode arbitrary input bytes (HEIC/JPEG/WebP/PNG/...) as a real JPEG.

    Same reasoning as ensure_png_bytes, for the "serve the original upload
    back to the browser" endpoints (backend/routes/api.py's source_image/
    source_thumbnail) instead of an edit-model API: srcKeys is always a
    "src.jpg"-named key regardless of what the user actually uploaded (see
    put_job.py), and most browsers can't render a HEIC/HEIF photo in an
    <img> tag under any label -- returning those bytes as-is looks like a
    broken image to the user even though the file itself is fine.
    """
    return _to_jpeg_bytes(_open_rgb(image_bytes))


def image_to_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def get_image_dimensions(image_bytes: bytes):
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
    return img.width, img.height


def _open_rgb(image_bytes: bytes) -> Image.Image:
    # A phone shot in portrait can still store pixels in a landscape buffer,
    # relying on the EXIF Orientation tag to rotate it back for display --
    # this bakes that rotation into the pixels themselves at the earliest
    # point every consumer (preview/PNG/JPEG conversion, cropping, the
    # width/height fed to the edit-model APIs) reads the image, so nothing
    # downstream needs to know the tag ever existed.
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
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


# The closed set of output ratios the edit APIs can be asked for in auto mode
# -- the same list the analysis prompt used to choose from itself. "original"
# (the source photo's own ratio) is always an extra candidate alongside these.
AUTO_ASPECT_RATIO_LABELS = (
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9" #, "21:9", "1:4", "4:1", "1:8", "8:1",
)


_AUTO_RATIO_LABELS_NEEDING_EXTREME_SOURCE = ("16:9", "9:16")
_SOURCE_RATIO_MATCH_TOLERANCE = 0.02  # relative, so 1.74 counts as "already 16:9"


def _source_at_least_as_extreme(source_ratio, label_ratio) -> bool:
    """Is the source photo as far from square as this label, on the same side of
    square? Equal (within tolerance) counts as yes."""
    if not source_ratio or not label_ratio or source_ratio <= 0 or label_ratio <= 0:
        return False
    slack = math.log(1 + _SOURCE_RATIO_MATCH_TOLERANCE)
    source, label = math.log(source_ratio), math.log(label_ratio)
    if label > 0:  # landscape label -- the photo must be at least as wide
        return source >= label - slack
    return source <= label + slack  # portrait label -- at least as tall


def _allowed_auto_ratio_labels(source_ratio: float) -> tuple:
    """The auto-mode candidate labels for a photo of this shape -- the full set
    minus the extremes the source photo isn't already at least as elongated as."""
    return tuple(
        label for label in AUTO_ASPECT_RATIO_LABELS
        if label not in _AUTO_RATIO_LABELS_NEEDING_EXTREME_SOURCE
        or _source_at_least_as_extreme(source_ratio, parse_aspect_ratio(label))
    )


def derive_aspect_ratio_from_crop(crop_box: dict, image_width: int, image_height: int):
    """Pick the output ratio from the crop geometry the analysis proposed,
    instead of from the ratio label it wrote.

    The analysis was asked to keep the two consistent and repeatedly didn't --
    e.g. a 0.85 x 1.00 rectangle on a 1600x1600 photo (really 0.85:1, portrait)
    labelled "3:2". The label is what drives the requested output canvas, so a
    mislabelled rectangle makes the crop and the canvas mutually unsatisfiable,
    and the canvas always wins: the crop intent is discarded and the photo is
    reframed to something nobody asked for. Measuring the rectangle removes the
    disagreement at its source -- whatever framing the analysis proposed, the
    canvas becomes the closest supported ratio to that framing.

    The source photo's own ratio competes as "original" and is listed first, so
    a crop that keeps the original proportions (or a near-tie) keeps them
    rather than being snapped onto a named ratio for no reason. 16:9 and 9:16
    only compete for photos that are already at least that elongated (see
    _allowed_auto_ratio_labels).

    Returns (label, measured_ratio); measured_ratio is None when there is no
    usable crop to measure, in which case the label is "original".
    """
    if not image_width or not image_height:
        return "original", None
    if not crop_box or crop_box.get("enabled") is not True:
        return "original", None
    try:
        w = float(crop_box.get("w") or 0)
        h = float(crop_box.get("h") or 0)
    except (TypeError, ValueError):
        return "original", None
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
        return "original", None

    measured = (w * image_width) / (h * image_height)
    if not math.isfinite(measured) or measured <= 0:
        return "original", None

    source_ratio = image_width / image_height
    choices = [("original", source_ratio)]
    choices += [(label, parse_aspect_ratio(label))
                for label in _allowed_auto_ratio_labels(source_ratio)]
    return _nearest_by_ratio(measured, choices), measured


_CROP_AXIS_NAMES = {"crop", "cropping", "framing", "reframing"}
_RATIO_TOKEN_RE = re.compile(r"(?<!\d)\d+\s*[:：]\s*\d+(?!\d)")


def _crop_review_indices(context: dict):
    """Return aligned crop-item indices, or [] when the LLM arrays drifted."""
    plan = context.get("correction_plan") or []
    axes = context.get("correction_axes") or []
    inverse = context.get("correction_inverse") or []
    recipe = context.get("retouch_recipe") or []
    if not (len(plan) == len(axes) == len(inverse) == len(recipe)):
        return []
    indices = []
    for index, entry in enumerate(axes):
        values = [entry] if isinstance(entry, str) else (entry or [])
        if any(str(value or "").strip().lower() in _CROP_AXIS_NAMES for value in values):
            indices.append(index)
    return indices


def _reframing_detail(text):
    """Remove an analyzer/local ratio prefix while retaining crop intent."""
    value = str(text or "").strip()
    value = re.sub(r"^\s*重新構圖\s*[:：]\s*", "", value)
    value = re.sub(
        r"^\s*(?:以\s*" + _RATIO_TOKEN_RE.pattern + r"\s*輸出比例|維持原圖輸出比例並)"
        r"\s*重新構圖\s*[；;，,]*\s*",
        "",
        value,
    )
    value = re.sub(r"^\s*維持原圖(?:輸出)?比例\s*[，,；;]*\s*", "", value)
    value = re.sub(
        r"(?:裁(?:切)?成|調整(?:為|成)|改(?:為|成)|輸出(?:為|成)?|採用|選擇|使用)\s*"
        r"(?:比例\s*)?" + _RATIO_TOKEN_RE.pattern + r"(?:\s*(?:比例|畫幅|構圖))?",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = _RATIO_TOKEN_RE.sub("", value)
    value = re.sub(
        r"(?:改變|更改|調整|選擇|指定)(?:輸出)?(?:長寬比|畫幅比例)|(?:長寬比|畫幅比例)(?:改變|調整)",
        "",
        value,
    )
    value = re.sub(r"\s*([，,；;。])\s*\1+", r"\1", value)
    value = re.sub(r"^[\s，,；;。:：]+|[\s，,；;]+$", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value


def _sync_reframing_review_contract(context: dict, ratio_label: str, crop_box: dict):
    """Make review text describe locally resolved geometry, never stale LLM text."""
    ctx = dict(context)
    indices = _crop_review_indices(ctx)
    if not crop_box:
        if indices:
            remove = set(indices)
            for key in ("correction_plan", "correction_axes", "correction_inverse", "retouch_recipe"):
                ctx[key] = [item for index, item in enumerate(ctx.get(key) or []) if index not in remove]
        return ctx

    if not indices:
        return ctx

    plan = list(ctx.get("correction_plan") or [])
    recipe = list(ctx.get("retouch_recipe") or [])
    for index in indices:
        plan_detail = _reframing_detail(plan[index])
        recipe_detail = _reframing_detail(recipe[index])
        fallback = _reframing_detail(
            (ctx.get("crop_box") or {}).get("reason") or ctx.get("crop_recommendation")
        )
        plan_detail = plan_detail or recipe_detail or fallback or "依已解析的裁切框改善主體集中度與邊緣落點。"
        recipe_detail = recipe_detail or plan_detail
        if ratio_label == "original":
            plan[index] = f"維持原圖輸出比例並重新構圖；{plan_detail}"
            recipe[index] = f"重新構圖：維持原圖比例，{recipe_detail}"
        else:
            plan[index] = f"以{ratio_label}輸出比例重新構圖；{plan_detail}"
            recipe[index] = f"重新構圖：裁成{ratio_label}，{recipe_detail}"
    ctx["correction_plan"] = plan
    ctx["retouch_recipe"] = recipe
    ctx["crop_recommendation"] = plan[indices[0]]
    return ctx


def apply_derived_aspect_ratio(
    analysis: dict, image_width: int, image_height: int, aspect_ratio_pref: str = "auto",
) -> dict:
    """Resolve crop geometry, then make its ratio and review text agree.

    ``original`` is only an output shape: a smaller same-ratio crop remains an
    active reframe. A crop is removed only when the final ROI/protection-aware
    rectangle actually expands back to the complete source frame.
    """
    # A user-declined crop is the one case where the private analyzer geometry
    # must not be revived. Re-checking removes this marker before arriving
    # here, at which point the saved proposal becomes eligible again.
    source_crop = (
        analysis.get("crop_box")
        if analysis.get("_preserve_original_composition")
        else analysis.get("_reframing_geometry_source") or analysis.get("crop_box")
    )
    derived_label, measured = derive_aspect_ratio_from_crop(
        source_crop, image_width, image_height,
    )
    pref = str(aspect_ratio_pref or "auto").strip().lower()
    label = derived_label if pref == "auto" else ("original" if pref == "original" else pref)

    ctx = dict(analysis)
    if "_reframing_geometry_source" not in ctx and source_crop:
        ctx["_reframing_geometry_source"] = dict(source_crop)
    ctx["auto_aspect_ratio"] = derived_label
    ctx["recommended_aspect_ratio"] = label
    if source_crop:
        ctx["crop_box"] = {**source_crop, "aspect_ratio": label}

    resolved_crop = None
    fixed_shape_without_crop = pref not in {"auto", "original"}
    if image_width and image_height and (
        (source_crop or {}).get("enabled") is True or fixed_shape_without_crop
    ):
        geometry_ratio = label if label != "original" else f"{image_width}:{image_height}"
        geometry_ctx = dict(ctx)
        geometry_ctx["recommended_aspect_ratio"] = geometry_ratio
        geometry_ctx["crop_box"] = {
            **(ctx.get("crop_box") or {}), "aspect_ratio": geometry_ratio,
        }
        resolved_crop = resolve_crop_box_with_report(
            geometry_ctx["crop_box"], geometry_ctx, image_width, image_height,
        )["cropBox"]
        if resolved_crop and resolved_crop.get("w", 1) > 0.965 and resolved_crop.get("h", 1) > 0.965:
            resolved_crop = None

    if resolved_crop:
        ctx["crop_box"] = {
            **(ctx.get("crop_box") or {}),
            "enabled": True,
            "x": resolved_crop["x"], "y": resolved_crop["y"],
            "w": resolved_crop["w"], "h": resolved_crop["h"],
            "aspect_ratio": label,
        }
        ctx["resolved_crop_box_measured_ratio"] = (
            resolved_crop["w"] * image_width / (resolved_crop["h"] * image_height)
        )
    else:
        label = label if fixed_shape_without_crop else "original"
        ctx["recommended_aspect_ratio"] = label
        source_crop = ctx.get("crop_box") or {}
        ctx["crop_box"] = {
            **source_crop,
            "enabled": False,
            "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0,
            "aspect_ratio": label,
            "reason": "本機幾何解析後沒有可執行的構圖裁切，維持完整畫面。",
        }
        ctx["crop_candidates"] = []
        ctx["crop_intent"] = {
            "trim_left": False, "trim_right": False,
            "trim_top": False, "trim_bottom": False,
            "subject_anchor": "center", "looking_direction": "unknown",
            "remove_edge_distractions": [],
        }
        ctx["crop_recommendation"] = "本機幾何解析後沒有可執行的構圖裁切，維持完整畫面。"
        ctx["composition_strategy"] = "本機幾何解析後選擇完整原圖構圖，不執行裁切。"

    candidates = analysis.get("crop_candidates")
    if candidates and resolved_crop:
        ctx["crop_candidates"] = [
            {**c, "crop_box": {**c["crop_box"],
                               "aspect_ratio": derive_aspect_ratio_from_crop(c["crop_box"], image_width, image_height)[0]}}
            if c.get("crop_box") else c
            for c in candidates
        ]

    ctx["aspect_ratio_reason"] = (
        f"本機依 AI 建議裁切框的實際比例（{measured:.3f}）於支援清單中選出最接近的「{label}」；"
        "並完成 ROI／保護區域約束後確認此裁切可執行。"
        if pref == "auto" and measured is not None and resolved_crop is not None else
        f"本機依 AI 建議裁切框的實際比例（{measured:.3f}）選擇候選比例，但套用 ROI／保護區域約束後裁切擴回完整畫面，因此維持原圖比例。"
        if pref == "auto" and measured is not None else
        f"依使用者選擇的輸出比例「{label}」完成裁切框與 ROI／保護區域約束解析。"
        if resolved_crop is not None else
        f"使用者選擇的輸出比例為「{label}」；本機幾何解析後不需要額外構圖裁切。"
        if pref not in {"auto", "original"} else
        "AI 未提出裁切建議，維持原圖比例。"
    )
    # Debug/report fields: what the local decision was based on.
    if pref == "auto":
        ctx["aspect_ratio_source"] = "crop_box_geometry" if resolved_crop is not None else "crop_box_geometry_noop"
    else:
        ctx["aspect_ratio_source"] = "user_aspect_ratio"
    ctx["crop_box_measured_ratio"] = measured
    return _sync_reframing_review_contract(ctx, label, resolved_crop)


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

# gpt-image-2 and the gpt-image-2.5 family (unlike gpt-image-1.5) accept an
# arbitrary "WIDTHxHEIGHT" `size` instead of a fixed enum, so the source
# photo's exact aspect ratio can be requested directly rather than snapped.
# The API still requires both edges divisible by 16 and the ratio within
# 1:3..3:1.
_OPENAI_ARBITRARY_SIZE_MODELS = {"gpt-image-2", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare"}
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

    snapped_width, snapped_height = snap(width), snap(height)

    # Independent grid rounding can push a clamped 3:1 target just beyond the
    # API's hard 3:1 limit (for example 2176x720). Pull only the long edge back
    # to the closest valid multiple of 16; the fixed target pixel budget keeps
    # the other documented size constraints valid.
    if snapped_width / snapped_height > _OPENAI_SIZE_MAX_RATIO:
        snapped_width = math.floor(
            snapped_height * _OPENAI_SIZE_MAX_RATIO / _OPENAI_SIZE_GRID
        ) * _OPENAI_SIZE_GRID
    elif snapped_width / snapped_height < _OPENAI_SIZE_MIN_RATIO:
        snapped_height = math.floor(
            snapped_width / _OPENAI_SIZE_MIN_RATIO / _OPENAI_SIZE_GRID
        ) * _OPENAI_SIZE_GRID

    return f"{snapped_width}x{snapped_height}"


def nearest_gemini_aspect_ratio(image_width: int, image_height: int) -> str:
    """Same contract as nearest_openai_edit_size, for Gemini's
    imageConfig.aspectRatio -- only called for the "original" preference."""
    ratio = (image_width / image_height) if image_width and image_height else 1.0
    return _nearest_by_ratio(ratio, _GEMINI_ASPECT_RATIO_CHOICES)


# ── Output geometry verification ───────────────────────────────────────────────

# How far the measured ratios may drift before it is reported. Grid snapping and
# each provider's own raster tables account for well under 1%; anything past 2%
# is a real disagreement, not rounding.
GEOMETRY_RATIO_TOLERANCE = 0.02


def crop_plan_pixel_ratio(crop_plan: dict, image_width: int, image_height: int):
    """The crop region's real w:h in pixels -- not the aspectRatio label it
    carries, which is exactly the thing that needs verifying. None when there is
    no crop (the whole source frame is kept) or no usable dimensions."""
    if not crop_plan or not image_width or not image_height:
        return None
    try:
        w = float(crop_plan.get("w") or 0) * image_width
        h = float(crop_plan.get("h") or 0) * image_height
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w / h


def output_geometry_warnings(
    output_b64: str,
    requested_ratio_label: str,
    crop_plan: dict,
    image_width: int,
    image_height: int,
) -> list:
    """Check, deterministically, that an edit really came back with the geometry
    it was asked for. Returns human-readable warnings for the QA report (empty
    list = consistent).

    A stretched/squashed result is always produced the same way: the edit model
    is handed two frame shapes that cannot both be true, and the only way to
    satisfy both is to scale width and height by different factors. Two places
    that can disagree:
      1. the mandatory crop rectangle in the prompt vs the aspect ratio the same
         prompt (and the API's size/imageConfig request) declares -- local
         geometry can fail to reconcile them, e.g. when ROI protection
         re-expands a box that was just fitted to the target ratio;
      2. the canvas that came back vs the canvas that was requested.
    Neither was measured before, so a deformed output looked like a clean run.
    """
    requested = parse_aspect_ratio(requested_ratio_label)
    if requested is None and image_width and image_height:
        # "original"/blank means the source frame is the request.
        requested = image_width / image_height
    if not requested:
        return []

    warnings = []

    crop_ratio = crop_plan_pixel_ratio(crop_plan, image_width, image_height)
    if crop_ratio:
        drift = crop_ratio / requested - 1
        if abs(drift) > GEOMETRY_RATIO_TOLERANCE:
            warnings.append(
                f"裁切框比例與要求的輸出畫布不一致（裁切區 {crop_ratio:.3f} vs 要求 {requested:.3f}，"
                f"差 {drift:+.1%}）：修圖模型只能靠壓縮或拉伸畫面來同時滿足兩者。"
            )

    try:
        out_w, out_h = get_image_dimensions(base64.b64decode(output_b64))
    except Exception:
        return warnings
    if not out_w or not out_h:
        return warnings

    out_ratio = out_w / out_h
    drift = out_ratio / requested - 1
    if abs(drift) > GEOMETRY_RATIO_TOLERANCE:
        warnings.append(
            f"輸出畫布比例與要求不一致（實際 {out_w}x{out_h} = {out_ratio:.3f} vs 要求 "
            f"{requested:.3f}，差 {drift:+.1%}）。"
        )

    return warnings


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


def _hard_crop_protection_boxes(context: dict) -> list:
    """Boxes that may expand an analyzer-selected composition crop.

    ``subject_extent_box`` remains available through ``protected_boxes`` as a
    soft placement reference, but it is not a hard crop boundary. It often
    reaches an edge for a subject already cut by the captured frame; forcing a
    proposed crop to contain it would routinely expand a valid reframe back to
    the complete image. The focal ROI and explicitly chosen protected regions
    remain hard constraints.
    """
    return [box for box in protected_boxes(context) if box["kind"] != "subject"]


def _place_or_grow_axis(start: float, size: float, protected_start: float, protected_end: float):
    """Contain a protected span by sliding first, growing only when needed."""
    protected_size = max(0.02, protected_end - protected_start)
    new_size = min(1.0, max(size, protected_size))
    min_start = max(0.0, protected_end - new_size)
    max_start = min(protected_start, 1.0 - new_size)
    if min_start > max_start:
        min_start = max_start = max(0.0, min(1.0 - new_size, protected_start))
    return min(max(start, min_start), max_start), new_size


def _protect_roi(crop: dict, context: dict) -> dict:
    padded = []
    for box in _hard_crop_protection_boxes(context):
        safety = 0.05 if box["kind"] == "roi" else 0.065
        padded.append({
            "left": max(0.0, box["x"] - safety),
            "top": max(0.0, box["y"] - safety),
            "right": min(1.0, box["x"] + box["w"] + safety),
            "bottom": min(1.0, box["y"] + box["h"] + safety),
        })
    if not padded:
        return crop

    x, w = _place_or_grow_axis(
        crop["x"], crop["w"],
        min(box["left"] for box in padded), max(box["right"] for box in padded),
    )
    y, h = _place_or_grow_axis(
        crop["y"], crop["h"],
        min(box["top"] for box in padded), max(box["bottom"] for box in padded),
    )
    return {**crop, "x": x, "y": y, "w": w, "h": h}


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


def _axis_span(boxes: list, axis: str):
    """Union extent of a list of normalized boxes along one axis.

    Returns (lower, upper), or None when there is nothing to cover."""
    if not boxes:
        return None
    size_key = "w" if axis == "x" else "h"
    return (
        min(b[axis] for b in boxes),
        max(b[axis] + b[size_key] for b in boxes),
    )


def _place_window(start: float, size: float, spans: list) -> float:
    """Where to put a window of `size` along one axis so that it covers the
    given spans, moving as little as possible from `start`.

    Each span contributes the interval of window offsets that maximizes that
    span's coverage -- offsets that contain it outright when the window is the
    longer of the two, offsets that sit entirely inside it when it isn't. The
    intervals are intersected in the order given, and a span that cannot also
    be satisfied is skipped rather than allowed to break the ones before it, so
    the caller's first span wins any conflict.

    Within whatever interval survives, the offset closest to `start` is chosen:
    this only ever slides the window along the axis, never resizes it, so it
    cannot change the aspect ratio the caller just fitted."""
    lo, hi = 0.0, max(0.0, 1.0 - size)
    for span in spans:
        if not span:
            continue
        span_lo, span_hi = sorted((span[1] - size, span[0]))
        span_lo = min(max(span_lo, 0.0), hi)
        span_hi = min(max(span_hi, 0.0), hi)
        if span_hi < lo or span_lo > hi:
            continue
        lo, hi = max(lo, span_lo), min(hi, span_hi)
    return min(max(start, lo), hi)


def _grow_to_aspect(crop: dict, image_width: int, image_height: int, target: float, context: dict = None) -> dict:
    """Grow (preferentially, never shrink first) crop to match target, keeping
    its current bounds fully covered. Used after ROI protection may have
    re-expanded a crop past the aspect ratio already fitted below -- growing
    the short dimension (instead of re-shrinking, which could cut the
    protected region right back out) is what keeps the returned box's real
    w:h from silently drifting away from the aspectRatio it still claims to
    be."""
    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    current = (w * image_width) / (h * image_height)
    if abs(current - target) < 0.005:
        return crop

    if current < target:
        new_w = min(1.0, target * h * image_height / image_width)
        cx = x + w / 2
        x = max(0, min(1 - new_w, cx - new_w / 2))
        w = new_w
    else:
        new_h = min(1.0, w * image_width / (target * image_height))
        cy = y + h / 2
        y = max(0, min(1 - new_h, cy - new_h / 2))
        h = new_h

    # The grow above can itself be capped at the frame boundary (1.0) without
    # reaching target -- e.g. a protected region wider than the target ratio
    # allows for the source image's actual height (or vice versa). Ratio
    # compliance wins over full protected-region coverage in that case: the
    # returned box always matching its own declared aspectRatio is what keeps
    # the edit model from having to deform the whole image to fit the API's
    # output-size request, which is worse than the protected region losing
    # some margin. Shrink the other dimension to force the match.
    #
    # Shrinking alone used to leave the box's origin where it was, so the whole
    # loss came off one side -- and that side was often exactly where the ROI
    # sat, because _protect_roi had just grown the box in that direction. A
    # real case: a crop protected out to x=0.915 to cover an ROI ending at 0.85
    # was shrunk straight back to width 0.747 anchored at x=0, cutting 26% off
    # the ROI. The edit model, told to crop there but also to honor an ROI
    # reaching past that edge, resolved the contradiction by squeezing the
    # photo horizontally. So slide the shortened window along its axis first
    # (_place_window) and only accept the loss that sliding cannot avoid; the
    # ROI is tried before the full protected union, since it is the one region
    # that must survive.
    current = (w * image_width) / (h * image_height)
    if abs(current - target) >= 0.005:
        boxes = protected_boxes(context or {})
        roi_boxes = [b for b in boxes if b["kind"] == "roi"] or [b for b in boxes if b["kind"] == "subject"]
        if current < target:
            new_h = w * image_width / (target * image_height)
            y = _place_window(y, new_h, [_axis_span(roi_boxes, "y"), _axis_span(boxes, "y")])
            h = new_h
        else:
            new_w = h * image_height * target / image_width
            x = _place_window(x, new_w, [_axis_span(roi_boxes, "x"), _axis_span(boxes, "x")])
            w = new_w

    return {**crop, "x": x, "y": y, "w": w, "h": h}


def fit_crop_to_aspect(crop: dict, image_width: int, image_height: int, context: dict) -> dict:
    target = parse_aspect_ratio(crop.get("aspectRatio", ""))
    if not target or not image_width or not image_height:
        return crop

    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    current = (w * image_width) / (h * image_height)

    if abs(current - target) >= 0.01:
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

    protected = _protect_roi({**crop, "x": x, "y": y, "w": w, "h": h}, context)
    # _protect_roi above (and the one already baked into the input crop from
    # normalize_crop_box) may have expanded the box past the ratio just fitted
    # -- restore it by growing the short dimension rather than re-shrinking,
    # so the protected region stays covered instead of being cut again.
    return _grow_to_aspect(protected, image_width, image_height, target, context)


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
        # No crop_box was resolved (not enabled, invalid, or effectively
        # full-frame already) -- but the caller may still be about to request
        # a real, non-"original" output ratio from the edit API regardless
        # (recommended_aspect_ratio set here by the caller before this call).
        # Without a crop plan to justify why the output ratio would differ
        # from the source's own, the edit model is left to reconcile that gap
        # itself, which is what produces a squished/stretched result. Fall
        # back to a full-frame crop fitted to that ratio (bypassing
        # normalize_crop_box's own effectively-full-frame skip above, since
        # here full-frame is exactly the starting point, not a reason to
        # skip) so the crop instruction and the ratio requested via the API
        # always agree.
        target_ratio_label = clean_aspect_ratio(context.get("recommended_aspect_ratio"))
        if target_ratio_label and image_width and image_height:
            full_frame = _protect_roi({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "aspectRatio": target_ratio_label}, context)
            fitted_fallback = fit_crop_to_aspect(full_frame, image_width, image_height, context)
            if fitted_fallback["w"] < 0.999 or fitted_fallback["h"] < 0.999:
                return {
                    "cropBox": fitted_fallback,
                    "adjustmentReasons": [
                        "local geometry added a full-frame crop to reach the requested output aspect ratio, since no crop_box was resolved"
                    ],
                }
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
