"""接著這張調整, shared by 對話模式 (routes/chat.py) and 面板模式 (routes/api.py).

A continuation edits one finished version's output with this round's request
only. It never re-applies the original recipe (brighten, smoothing, eye fixes,
person removal, styles, framing): those are already in the source image. Both
modes record it in the case's own history with based_on and source_result, so
either one reads a version the other made the same way.
"""

from flask import current_app

from aiserver_utils.queue import get_job_info
from backend.services.account import get_history_entry
from backend.services.put_job import lost_job_error
from compatibility_version import CURRENT_COMPATIBILITY_VERSION
from photo_agent import capabilities, chat_assistant


class ContinuationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def source_result_ref(value):
    """{version_id, job_id, run} from a request body, or a 400."""
    if not isinstance(value, dict):
        raise ContinuationError("source_result must be an object with version_id, job_id and run")
    version_id, job_id, run = value.get("version_id"), value.get("job_id"), value.get("run")
    if not (isinstance(version_id, str) and version_id and isinstance(job_id, str) and job_id
            and type(run) is int and run >= 1):
        raise ContinuationError("source_result must be an object with version_id, job_id and run")
    return {"version_id": version_id, "job_id": job_id, "run": run}


def same_source(a, b):
    return bool(a and b) and all(a.get(k) == b.get(k) for k in ("version_id", "job_id", "run"))


def result_key(job_id, run):
    """The output image key of one run of a finished job, or None. A finish job
    (one chat run) keeps it in cusKeys; a multi-run full job under runs."""
    res0 = (get_job_info(job_id, keyToGet=["0_res0"]) or {}).get("0_res0") or {}
    runs = res0.get("runs")
    if runs:
        keys = (runs[run - 1] or {}).get("cusKeys") if run <= len(runs) else None
    else:
        keys = res0.get("cusKeys") if run == 1 else None
    return (keys or [None])[0]


def resolve_source_result(user, case_id, source):
    """The chosen version's output, checked against the case's own history:
    {version_id, job_id, run, key}. Never falls back to the original or to
    another version -- a source that does not check out is an error."""
    entry = get_history_entry(user, case_id) or {}
    version = next((v for v in entry.get("versions") or [] if v["version_id"] == source["version_id"]), None)
    if (version is None or version.get("kind") == "analyze"
            or not any(r["job_id"] == source["job_id"] and r["run"] == source["run"] for r in version["results"])):
        raise ContinuationError("source_not_found", 404)
    info = get_job_info(source["job_id"], keyToGet=["status", "error", "error_message", "ts"]) or {}
    lost = lost_job_error(source["job_id"], info)
    if (lost["status"] if lost else info.get("status")) != "success":
        raise ContinuationError("source_not_finished", 409)
    key = result_key(source["job_id"], source["run"])
    if not key:
        raise ContinuationError("source_not_finished", 409)
    return {**source, "key": key}


def continuation_form(form, text):
    """A finish render form turned into 接著這張調整's: `text` as the whole
    request, and no ratio change, crop or eye-repair pass."""
    form.update({"user_prompt": text, "overwrite_prompt": True, "aspect_ratio": "original",
                 "single_pass_edit": True,
                 "advanced_options": {o.key: False for o in capabilities.ADVANCED_OPTIONS if not o.choices}})
    form.pop("literal_texts", None)
    return form


def continuation_analysis(text, styles, image_model):
    """The analysis a continuation runs with: a skipped-analysis stub marked
    _continuation, which edit_prompt reads as request-only, plus the styles
    picked for this round. _continuation_original is set when the editor also
    gets the original photo (the caller then sends it second in srcKeys)."""
    analysis = {"_skipped_analysis": True, "_continuation": True, "_aspect_ratio_pref_at_analysis": "original",
                "prompts": {}, "timings": {}, "usage": {}, "compatibility_version": CURRENT_COMPATIBILITY_VERSION}
    if styles:
        analysis["style_options"] = dict(styles)
    original = _continuation_original(text, image_model) if text else None
    if original is not None:
        analysis["_continuation_original"] = original
    return analysis


def _continuation_original(text, image_model):
    """{"restore": ...} when this continuation's editor also gets the original
    photo, else None. Only the request decides: a new change on the version
    goes alone, so the editor is not pulled back toward the original; a
    request that wants something as it was gets the original beside it. Only
    the OpenAI editor takes a second image. A failed read sends the original
    -- the editor is told it is a reference only, so that costs little."""
    if capabilities.image_model(image_model).provider != "openai":
        return None
    try:
        read = chat_assistant.read_continuation(text)
    except Exception as exc:
        current_app.logger.warning("Continuation request read failed, sending the original: %s", exc)
        read = {"needs_original": True, "restore": ""}
    return {"restore": read["restore"]} if read["needs_original"] else None
