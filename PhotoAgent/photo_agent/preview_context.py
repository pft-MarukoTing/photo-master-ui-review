"""Trusted JSON preview snapshot with separate detector and semantic geometry.

Flask loads it from the owned job's Redis snapshot or artifact, freezes it in the formal queue
request, and the worker revalidates against the downloaded original. There is
no worker dependency on a preview token/Redis TTL after formal submission.
"""
import copy
import hashlib
import json
import math
import os
import re
import time
from functools import lru_cache

from photo_agent.subject_preview import COORDINATE_SPACE, _valid_subject_box, validate_subject_preview

FORMAT_VERSION = 3
PREVIEW_CONTEXT_FIELD = "preview_context"
PREVIEW_RETENTION_SECONDS = 30 * 24 * 3600  # same lifetime as Chat sessions
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def source_sha256(data):
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=4)
def _detector_model_hash(path, modified, size):
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def preview_versions():
    from prompts import SUBJECT_PREVIEW_INSTRUCTION, SUBJECT_PREVIEW_RESPONSE_SCHEMA
    from image_tools import (_FACE_CROP_MAX_COUNT, _FACE_CROP_MAX_SIDE,
                             _FACE_DETECT_CONFIDENCE_THRESHOLD, _FACE_DETECT_NMS_THRESHOLD)
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    import inspect
    import image_tools
    # Include geometry/filter implementation and model bytes, not just a
    # handwritten name that could survive an incompatible deployment.
    detector_code = inspect.getsource(image_tools)
    path = image_tools._FACE_DETECTOR_MODEL_PATH
    stat = os.stat(path)
    model_hash = _detector_model_hash(path, stat.st_mtime_ns, stat.st_size)
    from photo_agent import ab_preview
    import config
    policy = repr((config.SUBJECT_PREVIEW_LOW_CONFIDENCE_THRESHOLD,
        config.SUBJECT_PREVIEW_MAX_LOW_CONFIDENCE_CANDIDATES,
        config.SUBJECT_PREVIEW_MAX_LOW_CONFIDENCE_ZOOMS, config.SUBJECT_PREVIEW_AB_MAX_OUTPUT_TOKENS,
        ab_preview.POLICY, ab_preview.MODE))
    return {"detector": "sdl-exif-v1:" + digest(repr((_FACE_CROP_MAX_COUNT, _FACE_CROP_MAX_SIDE,
                _FACE_DETECT_CONFIDENCE_THRESHOLD, _FACE_DETECT_NMS_THRESHOLD)) + detector_code + model_hash),
            "prompt": digest(SUBJECT_PREVIEW_INSTRUCTION + ab_preview.INSTRUCTION + ab_preview.FIXED_INSTRUCTION + policy),
            "schema": digest(json.dumps([SUBJECT_PREVIEW_RESPONSE_SCHEMA, ab_preview.selection_schema()], sort_keys=True))}


def validate_preview_binding(binding, *, check_age=True):
    if not isinstance(binding, dict) or set(binding) != {
        "preview_job_id", "user", "source_sha256", "created_at", "versions", "analysis_model"
    }:
        raise ValueError("invalid_preview_binding")
    if (not isinstance(binding["preview_job_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", binding["preview_job_id"])
            or not isinstance(binding["user"], str) or not 1 <= len(binding["user"]) <= 128
            or not isinstance(binding["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", binding["source_sha256"])
            or type(binding["created_at"]) not in (int, float)
            or not math.isfinite(binding["created_at"])
            or (check_age and not 0 <= time.time() - binding["created_at"] <= PREVIEW_RETENTION_SECONDS)
            or binding["versions"] != preview_versions()
            or not isinstance(binding["analysis_model"], str) or len(binding["analysis_model"]) > 100):
        raise ValueError("stale_or_invalid_preview_binding")
    return copy.deepcopy(binding)


def validate_preview_context(context, *, image_bytes=None, src_keys=None, binding=None):
    if (not isinstance(context, dict) or type(context.get("format_version")) is not int
            or context["format_version"] not in (1, 2, FORMAT_VERSION)):
        raise ValueError("invalid_preview_format")
    # This is a frozen formal request, not a live preview token. Retention was
    # enforced by Flask before enqueue; a delayed worker does not re-expire it.
    own = validate_preview_binding(context.get("binding"), check_age=False)
    if binding is not None and own != binding:
        raise ValueError("preview_binding_mismatch")
    if context.get("coordinate_space") != COORDINATE_SPACE:
        raise ValueError("invalid_preview_coordinate_space")
    keys = context.get("src_keys")
    if (not isinstance(keys, list) or len(keys) != 1 or not isinstance(keys[0], str)
            or not keys[0] or len(keys[0]) > 1024 or (src_keys is not None and keys != src_keys)):
        raise ValueError("preview_source_mismatch")
    size = context.get("image_size")
    if not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
        raise ValueError("invalid_preview_image_size")
    if image_bytes is not None:
        from image_tools import get_image_dimensions
        if own["source_sha256"] != source_sha256(image_bytes) or list(get_image_dimensions(image_bytes)) != size:
            raise ValueError("preview_image_mismatch")
    crops = context.get("detections")
    if not isinstance(crops, list):
        raise ValueError("invalid_preview_detections")
    for index, crop in enumerate(crops, 1):
        expected_fields = {"detection_index", "box", "face_box", "sent_to_model"}
        if context['format_version'] == 3:
            expected_fields.add('confidence')
        if (not isinstance(crop, dict) or set(crop) != expected_fields
                or type(crop["detection_index"]) is not int or crop["detection_index"] != index
                or type(crop["sent_to_model"]) is not bool
                or not _valid_subject_box(crop["box"]) or not _valid_subject_box(crop["face_box"])):
            raise ValueError("invalid_preview_detection")
        padded, face = crop["box"], crop["face_box"]
        if (padded["x"] > face["x"] + 1e-6 or padded["y"] > face["y"] + 1e-6
                or padded["x"] + padded["w"] < face["x"] + face["w"] - 1e-6
                or padded["y"] + padded["h"] < face["y"] + face["h"] - 1e-6):
            raise ValueError("preview_crop_does_not_contain_face")
    from image_tools import _FACE_CROP_MAX_COUNT
    if any(c["sent_to_model"] != (i < _FACE_CROP_MAX_COUNT) for i, c in enumerate(crops)):
        raise ValueError("invalid_preview_crop_budget")
    preview = context.get("subject_preview")
    if not isinstance(preview, dict) or preview.get("state") not in {"ready", "unavailable"}:
        raise ValueError("invalid_preview_state")
    if context['format_version'] == 3:
        from photo_agent.ab_preview import validate_context_fields
        preview, extra = validate_context_fields(context, crops, preview)
        return {'format_version': 3, 'binding': own, 'src_keys': list(keys),
                'coordinate_space': COORDINATE_SPACE, 'image_size': list(size),
                'detections': copy.deepcopy(crops), 'subject_preview': copy.deepcopy(preview), **extra}
    if preview["state"] == "ready":
        classified = preview.get("detections")
        if not isinstance(classified, list) or any(not isinstance(d, dict) for d in classified):
            raise ValueError("invalid_preview_classifications")
        model_fields = {key: preview.get(key) for key in (
            "main_subject_kind", "primary_detection_index", "detections", "summary")}
        model_fields["detections"] = [{k: d.get(k) for k in (
            "detection_index", "role", "face_readable", "label")} for d in classified]
        if context["format_version"] == 2:
            from photo_agent.subject_preview import validate_additional_people
            model_fields["additional_people"] = validate_additional_people(preview.get("additional_people", []))
        for d, original in zip(model_fields['detections'], classified):
            if 'reason' in original:
                d['reason'] = original['reason']
        verified = validate_subject_preview(model_fields, crops)
        if context["format_version"] == 2:
            state = preview.get("augmentation_state", verified.get("augmentation_state"))
            if state not in {"ready", "unavailable"} or (state == "unavailable" and verified["additional_people"]):
                raise ValueError("invalid_preview_augmentation_state")
            verified["augmentation_state"] = state
        if verified["detections"] != preview.get("detections"):
            raise ValueError("preview_classification_geometry_mismatch")
        preview = verified
    else:
        from photo_agent.subject_preview import unavailable_preview
        reason = preview.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 100 or type(preview.get("skipped")) is not bool:
            raise ValueError("invalid_preview_unavailable")
        preview = unavailable_preview(reason, skipped=preview["skipped"])
    people_fields = {}
    if context["format_version"] == 2:
        people_fields["preview_people"] = _validate_preview_people(context.get("preview_people"), crops, preview)
    # Allowlist the frozen snapshot; API envelopes/raw LLM diagnostics never enter params.
    return {"format_version": context["format_version"], **people_fields, "binding": own, "src_keys": list(keys),
            "coordinate_space": COORDINATE_SPACE, "image_size": list(size),
            "detections": copy.deepcopy(crops), "subject_preview": copy.deepcopy(preview)}


def _validate_preview_people(people, crops, preview):
    from config import SUBJECT_PREVIEW_MAX_DETECTIONS, SUBJECT_PREVIEW_MAX_ADDITIONAL_PEOPLE
    fields = {"preview_person_id", "source", "detection_index", "box", "face_box",
              "sent_to_model", "label", "role", "face_readable"}
    if not isinstance(people, list) or len(people) > SUBJECT_PREVIEW_MAX_DETECTIONS + SUBJECT_PREVIEW_MAX_ADDITIONAL_PEOPLE:
        raise ValueError("invalid_preview_people")
    ids, detectors, extras = set(), set(), []
    classifications = {d["detection_index"]: d for d in preview.get("detections", [])}
    for person in people:
        if (not isinstance(person, dict) or set(person) != fields
                or not isinstance(person["preview_person_id"], str)
                or not re.fullmatch(r"pp_[a-f0-9]{32}", person["preview_person_id"])
                or person["preview_person_id"] in ids
                or not isinstance(person["label"], str) or not 1 <= len(person["label"].strip()) <= 60
                or not isinstance(person["role"], str) or person["role"] not in {"included", "bystander", "uncertain"}
                or (person["face_readable"] is not None and type(person["face_readable"]) is not bool)
                or type(person["sent_to_model"]) is not bool or not _valid_subject_box(person["box"])):
            raise ValueError("invalid_preview_person")
        ids.add(person["preview_person_id"])
        if person["source"] == "detector":
            index = person["detection_index"]
            if type(index) is not int or not 1 <= index <= len(crops) or index in detectors:
                raise ValueError("invalid_preview_person_detection")
            crop = crops[index - 1]
            decision = classifications.get(index, {})
            if (any(person[k] != crop[k] for k in ("box", "face_box", "sent_to_model"))
                    or person["role"] != decision.get("role", "uncertain")
                    or person["label"] != decision.get("label", "人物 " + str(index))
                    or person["face_readable"] != decision.get("face_readable")):
                raise ValueError("preview_person_detector_mismatch")
            detectors.add(index)
        elif person["source"] == "full_image":
            if person["detection_index"] is not None or person["face_box"] is not None or person["sent_to_model"]:
                raise ValueError("fabricated_preview_detector_evidence")
            extras.append({k: person[k] for k in ("label", "role", "face_readable", "box")})
        else:
            raise ValueError("invalid_preview_person_source")
    expected = {i for i in range(1, len(crops) + 1) if classifications.get(i, {}).get("role") != "not_real_face"}
    if detectors != expected or extras != preview.get("additional_people", []):
        raise ValueError("preview_people_proposal_mismatch")
    return copy.deepcopy(people)
