import json
import logging
import os
import urllib.request
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

_HAND_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)
_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "hand_landmarker.task")


def _ensure_model():
    if not os.path.exists(_MODEL_PATH):
        os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
        logger.info("Downloading hand landmarker model to %s ...", _MODEL_PATH)
        urllib.request.urlretrieve(_HAND_LANDMARKER_URL, _MODEL_PATH)
        logger.info("Model downloaded.")


@dataclass
class FilterStats:
    total_seen: int = 0
    passed: int = 0
    rejected_resolution: int = 0
    rejected_duplicate: int = 0
    rejected_no_hand: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (
            f"Total: {self.total_seen} | "
            f"Passed: {self.passed} | "
            f"Rejected (resolution): {self.rejected_resolution} | "
            f"Rejected (duplicate): {self.rejected_duplicate} | "
            f"Rejected (no hand): {self.rejected_no_hand} | "
            f"Errors: {self.errors}"
        )


class HandDetector:
    def __init__(self, min_detection_confidence: float = 0.5,
                 fallback_confidence: float = 0.3):
        self._conf = min_detection_confidence
        self._fallback = fallback_confidence
        self._detector = None
        self._fallback_detector = None

    def _build(self, confidence: float):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        _ensure_model()
        base_options = mp_python.BaseOptions(model_asset_path=_MODEL_PATH)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=4,
            min_hand_detection_confidence=confidence,
        )
        return mp_vision.HandLandmarker.create_from_options(options)

    def _ensure_detectors(self):
        if self._detector is None:
            self._detector = self._build(self._conf)
        if self._fallback < self._conf and self._fallback_detector is None:
            self._fallback_detector = self._build(self._fallback)

    def _run(self, detector, rgb_image: np.ndarray) -> bool:
        import mediapipe as mp
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
        result = detector.detect(image)
        return len(result.hand_landmarks) > 0

    def detect(self, rgb_image: np.ndarray) -> bool:
        self._ensure_detectors()
        if self._run(self._detector, rgb_image):
            return True
        if self._fallback_detector is not None:
            return self._run(self._fallback_detector, rgb_image)
        return False


def check_resolution(image_path: str, min_megapixels: float) -> tuple:
    from PIL import Image
    try:
        with Image.open(image_path) as img:
            w, h = img.size
        passes = (w * h) >= int(min_megapixels * 1_000_000)
        return passes, (w, h)
    except Exception as e:
        logger.debug("Resolution check error for %s: %s", image_path, e)
        return False, (0, 0)


def _phash_hex(image_path: str) -> str:
    import imagehash
    from PIL import Image
    with Image.open(image_path) as img:
        return str(imagehash.phash(img))


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


class DuplicateFilter:
    def __init__(self, state_file: str, threshold: int = 8):
        self.state_file = state_file
        self.threshold = threshold
        self.seen: list = []

    def load(self):
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                self.seen = json.load(f)
            logger.info("Loaded %d known hashes from %s", len(self.seen), self.state_file)

    def save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.seen, f)
        logger.info("Saved %d hashes to %s", len(self.seen), self.state_file)

    def is_duplicate(self, image_path: str) -> bool:
        try:
            h = _phash_hex(image_path)
        except Exception as e:
            logger.debug("pHash error for %s: %s", image_path, e)
            return False
        for seen_h in self.seen:
            if _hamming(h, seen_h) <= self.threshold:
                return True
        self.seen.append(h)
        return False


def filter_image(image_path: str, config: dict, detector: HandDetector,
                 dedup: DuplicateFilter, stats: FilterStats) -> bool:
    """Caller must call dedup.commit() after saving if this returns True."""
    stats.total_seen += 1

    min_mp = config.get("resolution", {}).get("min_megapixels", 1.0)
    passes_res, (w, h) = check_resolution(image_path, min_mp)
    if not passes_res:
        stats.rejected_resolution += 1
        logger.debug("Resolution fail (%dx%d): %s", w, h, image_path)
        return False

    if config.get("dedup", {}).get("enabled", True):
        try:
            if dedup.is_duplicate(image_path):
                stats.rejected_duplicate += 1
                logger.debug("Duplicate: %s", image_path)
                return False
        except Exception as e:
            logger.debug("Dedup error for %s: %s", image_path, e)

    if config.get("hand_detection", {}).get("enabled", True):
        import cv2
        img_bgr = cv2.imread(image_path)
        if img_bgr is None:
            stats.errors += 1
            return False
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        conf = config["hand_detection"].get("min_detection_confidence", 0.5)
        fallback = config["hand_detection"].get("fallback_confidence", 0.3)
        detector._conf = conf
        detector._fallback = fallback
        if not detector.detect(img_rgb):
            stats.rejected_no_hand += 1
            logger.debug("No hand detected: %s", image_path)
            return False

    stats.passed += 1
    return True
