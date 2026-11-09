"""The photo's face as 68 landmarks, from face_alignment (Adrian Bulat's
PyTorch FAN, as face-landmarks.py runs it): where align.line_up's local
alignment finds the brows, eyes, nose, mouth and chin, and blend's
hair_brow mask the brows.

aiHair: FACE_PYTHON (HAIR_FACE_PYTHON) is this python unless set, and then
the model runs in this process, loaded once (_model) and kept, as the hair
model is (hairseg). HairFeatures ran it in a python of its own (the
face-align conda env), starting it, torch and the model afresh for every
call: about 18 s a call on the dev server's CPU, against well under one
here once loaded. Set to another python, it still runs there, as it did.
Either way a photo's face comes out the same (_largest_face is the job's
own loop). FACE_PYTHON is also the python region_align runs its OpenCV
steps in.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# aiHair: HAIR_FACE_PYTHON sets it; unset, this python, which runs the
# model in this process (in_process).
FACE_PYTHON = Path(os.environ.get("HAIR_FACE_PYTHON") or sys.executable)
# One run at a time: in another python each loads torch and the model; in
# this one they share the model.
_LOCK = threading.Lock()
_LOAD_LOCK = threading.Lock()
_MODEL = None
# Faces found so far, by the image's pixels: a photo run again (by another
# effect, in replay-blend.py) isn't sent to the model twice.
_FOUND: dict[str, dict | None] = {}
# aiHair: a long-running server would grow _FOUND with every photo, so only
# the latest _FOUND_MAX faces are kept (a dict keeps them in insertion order).
_FOUND_MAX = 256
_FOUND_LOCK = threading.Lock()
# What the face model needs in the python it runs in (and region_align
# cv2).
_NEEDS = ("face_alignment", "torch", "cv2")

# Run in the face python: face-landmarks.py's model over a JSON list of
# images, each shrunk to the given longest side first, keeping the largest
# face whose landmark score (FAN's mean heatmap peak) reaches the given
# minimum — on 0_Original the subjects score 0.79–0.87, sfd's misfires on
# necks, sweaters and hair 0.11–0.51.
# compile=False: torch.compile needs a C++ toolchain this Windows setup lacks.
_FACE_JOB = r'''
import json, sys
from pathlib import Path
import numpy as np
import face_alignment
import torch
from PIL import Image
paths, out, min_score, max_side = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
device = "cuda" if torch.cuda.is_available() else "cpu"
model = face_alignment.FaceAlignment(face_alignment.LandmarksType.TWO_D, device=device,
                                     face_detector="sfd", flip_input=False, compile=False)
faces = []
for src in json.loads(Path(paths).read_text(encoding="utf-8")):
    image = Image.open(src).convert("RGB")
    scale = min(1.0, max_side / max(image.size))
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)),
                             Image.Resampling.BOX)
    rgb = np.asarray(image)
    found, scores, boxes = model.get_landmarks_from_image(
        rgb, return_bboxes=True, return_landmark_score=True)
    best = None
    for i, points in enumerate(found or []):
        score = float(np.mean(scores[i]))
        box = [float(v) / scale for v in boxes[i][:4]]
        area = (box[2] - box[0]) * (box[3] - box[1])
        if score >= min_score and (best is None or area > best["area"]):
            best = {"points": (points[:, :2] / scale).tolist(), "bbox": box, "score": score,
                    "area": area}
    faces.append(best)
    print(f"  face {Path(src).name}: " + (f"score {best['score']:.2f}" if best else "none"),
          flush=True)
Path(out).write_text(json.dumps(faces), encoding="utf-8")
'''
_MIN_SCORE = 0.6
# sfd misses a face that fills much of a large photo: on
# female_batch06_282_F (2316x3088, the face ~1200 px across) it found only
# boxes on the hair and neck, 0.38 at best; shrunk to 1024 the face, 0.85.
# fluffy_composite.py shrinks to this before its face model too.
_MAX_SIDE = 1024


def in_process(face_python) -> bool:
    """Whether `face_python` is this python, so the model runs here."""
    try:
        return bool(sys.executable) and (Path(face_python).resolve()
                                         == Path(sys.executable).resolve())
    except OSError:
        return False


def available(face_python=None) -> tuple[bool, str]:
    """Whether the face model can run in `face_python` (None, FACE_PYTHON),
    and if not why: this python, with face_alignment, torch and cv2
    installed; another, there."""
    face_python = face_python or FACE_PYTHON
    if in_process(face_python):
        missing = [m for m in _NEEDS if importlib.util.find_spec(m) is None]
        if missing:
            return False, (f"this python ({sys.executable}) lacks {', '.join(missing)}; "
                           "install them (Dockerfile) or set HAIR_FACE_PYTHON to a python "
                           "that has them")
        return True, ""
    if not Path(face_python).is_file():
        return False, (f"the face model's python isn't there: {face_python} (set "
                       "HAIR_FACE_PYTHON, or leave it unset to run it in this python)")
    return True, ""


def _model():
    """The FAN model, loaded once in this process: the job's own settings."""
    global _MODEL
    with _LOAD_LOCK:
        if _MODEL is None:
            import face_alignment
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _MODEL = face_alignment.FaceAlignment(
                face_alignment.LandmarksType.TWO_D, device=device, face_detector="sfd",
                flip_input=False, compile=False)
        return _MODEL


def preload(face_python=None) -> bool:
    """Load the model now (server start) where it runs in this process and
    can; returns whether it did."""
    face_python = face_python or FACE_PYTHON
    if in_process(face_python) and available(face_python)[0]:
        _model()
        return True
    return False


def _largest_face(model, image, min_score: float, max_side: int):
    """_FACE_JOB's loop for one image, in this process: the largest face
    scoring `min_score` or more on `image` shrunk to `max_side`, as the job
    writes it (points a list, as JSON carries it), or None."""
    import numpy as np
    from PIL import Image

    image = image.convert("RGB")
    scale = min(1.0, max_side / max(image.size))
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)),
                             Image.Resampling.BOX)
    rgb = np.asarray(image)
    found, scores, boxes = model.get_landmarks_from_image(
        rgb, return_bboxes=True, return_landmark_score=True)
    best = None
    for i, points in enumerate(found or []):
        score = float(np.mean(scores[i]))
        box = [float(v) / scale for v in boxes[i][:4]]
        area = (box[2] - box[0]) * (box[3] - box[1])
        if score >= min_score and (best is None or area > best["area"]):
            best = {"points": (points[:, :2] / scale).tolist(), "bbox": box, "score": score,
                    "area": area}
    return best


def _faces_here(images: list) -> list:
    model = _model()
    with _LOCK:
        return [_largest_face(model, image, _MIN_SCORE, _MAX_SIDE) for image in images]


def _faces_there(images: list, face_python) -> list:
    with tempfile.TemporaryDirectory(prefix="face_landmarks_") as tmp:
        tmp = Path(tmp)
        paths = []
        for i, image in enumerate(images):
            image.convert("RGB").save(tmp / f"{i}.png")
            paths.append(str(tmp / f"{i}.png"))
        (tmp / "jobs.json").write_text(json.dumps(paths), encoding="utf-8")
        with _LOCK:
            done = subprocess.run([str(face_python), "-c", _FACE_JOB, str(tmp / "jobs.json"),
                                   str(tmp / "faces.json"), str(_MIN_SCORE), str(_MAX_SIDE)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding="utf-8", errors="replace")
        if done.returncode:
            raise RuntimeError(f"the face model failed (exit {done.returncode}):\n"
                               f"{(done.stderr or '')[-2000:]}")
        return json.loads((tmp / "faces.json").read_text(encoding="utf-8"))


def _key(image) -> str:
    rgb = image.convert("RGB")
    return hashlib.sha1(repr(rgb.size).encode() + rgb.tobytes()).hexdigest()


def face_landmarks(images: list, face_python: str) -> list:
    """The largest face in each of `images` (PIL), found on it shrunk to
    _MAX_SIDE, as a dict: "points", its 68 landmarks (68 x 2 numpy array, x
    and y in the image's pixels), "bbox" (the detector's box, x0, y0, x1,
    y1) and "score"; or None where there is none. Faces found before
    (_FOUND) aren't looked for again; the rest are, by the model in this
    process where `face_python` is this python, else in one run of it in
    `face_python`. Raises RuntimeError if it fails."""
    import numpy as np

    keys = [_key(image) for image in images]
    # aiHair: what this call returns is gathered here, so another thread
    # trimming _FOUND can't take a face out from under it.
    found = {}
    with _FOUND_LOCK:
        for k in keys:
            if k in _FOUND:
                found[k] = _FOUND[k]
    todo = {k: image for k, image in zip(keys, images) if k not in found}
    if todo:
        ok, why = available(face_python)
        if not ok:
            raise RuntimeError(why)
        if in_process(face_python):
            try:
                faces = _faces_here(list(todo.values()))
            except Exception as e:  # noqa: BLE001 — as the job's failure was
                raise RuntimeError(f"the face model failed — {type(e).__name__}: {e}") from e
        else:
            faces = _faces_there(list(todo.values()), face_python)
        for k, face in zip(todo, faces):
            if face is not None:
                face = {"points": np.array(face["points"]), "bbox": face["bbox"],
                        "score": face["score"]}
            found[k] = face
        with _FOUND_LOCK:
            _FOUND.update({k: found[k] for k in todo})
            while len(_FOUND) > _FOUND_MAX:
                del _FOUND[next(iter(_FOUND))]
    return [found[k] for k in keys]
