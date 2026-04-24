import json
import logging
import os
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_REPO = "Bingsu/adetailer"
_MODEL_FILE = "hand_yolov8n.pt"
_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", _MODEL_FILE)


def _ensure_model():
    if not os.path.exists(_MODEL_PATH):
        os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
        logger.info("Downloading %s from HuggingFace ...", _MODEL_FILE)
        from huggingface_hub import hf_hub_download
        src = hf_hub_download(repo_id=_MODEL_REPO, filename=_MODEL_FILE)
        import shutil
        shutil.copy(src, _MODEL_PATH)
        logger.info("Model saved to %s", _MODEL_PATH)


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
    def __init__(self, min_detection_confidence: float = 0.5):
        self._conf = min_detection_confidence
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO
            _ensure_model()
            self._model = YOLO(_MODEL_PATH)

    def detect(self, bgr_image: np.ndarray) -> bool:
        self._ensure_model()
        results = self._model(bgr_image, device="cpu", verbose=False, conf=self._conf)
        return len(results[0].boxes) > 0


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
        conf = config["hand_detection"].get("min_detection_confidence", 0.5)
        detector._conf = conf
        if not detector.detect(img_bgr):
            stats.rejected_no_hand += 1
            logger.debug("No hand detected: %s", image_path)
            return False

    stats.passed += 1
    return True
