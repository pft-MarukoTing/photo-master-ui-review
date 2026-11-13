"""Local UI preview: answer the panel's analysis calls with sample data.

Only active when PHOTOAGENT_UI_MOCK=1 (set by a developer's local start script, never in a
deployment). Without Redis/S3/models a local analysis always fails, so the panel's workspace
(Photo Overview, Analysis & Adjustments, version strip, Retouch) can't be seen or laid out.
With this on, /analyze and /analyze-batch keep the uploaded photos in a temp folder and hand
back "mock-" job ids; /status, /analyze/result and /source/<id>/image|thumbnail answer for
those ids, so the panel's own code renders the real workspace from a sample analysis. Every
other request goes to the real routes untouched.
"""

import copy
import json
import os
import re
import tempfile
import threading
import time
import uuid

from flask import jsonify, request, send_file

ENV_FLAG = "PHOTOAGENT_UI_MOCK"
_STORE = os.path.join(tempfile.gettempdir(), "photoagent-ui-mock")
_PREFIX = "/api/photo-agent"
_ID = re.compile(r"(mock-[0-9a-f]{12})")

SAMPLE_ANALYSIS = {
    "photo_summary": "一位人物在自然光下的照片，背景柔和，整體清新自然。",
    "subject_category": "人物",
    "scene_category": "戶外",
    "main_subject": "看向鏡頭的人物",
    "scene_type": "戶外人像",
    "composition_mood": "清新、自然、溫暖",
    "aesthetic_diagnosis": "主體清楚，光線柔和；臉部稍暗，背景有些許雜物分散注意力。",
    "detected_issues": [
        "臉部受光不足，膚色略顯暗沉。",
        "背景右側有雜物，分散對主體的注意力。",
        "畫面下方留白偏多，主體不夠集中。",
    ],
    "retouch_recipe": [
        "重新構圖：裁成 4:5，讓人物在畫面中更集中",
        "提亮臉部與中間調，保留自然膚色",
        "柔化背景雜物，讓主體更突出",
    ],
    "correction_plan": [
        "Reframe to 4:5 keeping the subject centered",
        "Lift face exposure and midtones",
        "Soften distracting background clutter",
    ],
    "recommended_aspect_ratio": "4:5",
    "aspect_ratio_reason": "直式人像，4:5 能讓主體更集中。",
    "subjects": [
        {
            "is_person": True,
            "label": "畫面中的人物",
            # Where the person is, as fractions of the image (the panel's 人像 tab crops the
            # avatar from the upper part of this box). A rough centre-frame guess for any photo.
            "box": {"x": 0.25, "y": 0.08, "w": 0.5, "h": 0.8},
            "blink_issue": {"should_fix": False, "level": "none"},
            "gaze_issue": {"should_fix": True, "level": "moderate"},
            "hair_issue": {"should_fix": False, "level": "none"},
            "smile_issue": {"adjust_safe": False, "smile_intensity": "broad"},
        }
    ],
    "user_instruction": {"text": "", "overwrite_prompt": False},
}


SAMPLE_ANALYSIS_EN = {
    "photo_summary": "A person photographed in natural light with a soft background; the overall look is fresh and natural.",
    "subject_category": "People",
    "scene_category": "Outdoor",
    "main_subject": "A person looking at the camera",
    "scene_type": "Outdoor portrait",
    "composition_mood": "Fresh, natural, warm",
    "aesthetic_diagnosis": "Clear subject and soft light; the face is a little dark and background clutter distracts.",
    "detected_issues": [
        "The face is underexposed and skin looks slightly dull.",
        "Clutter on the right side of the background distracts from the subject.",
        "Too much empty space at the bottom; the subject is not focused enough.",
    ],
    "retouch_recipe": [
        "Reframe: crop to 4:5 to keep the person more centered",
        "Brighten the face and midtones while keeping natural skin tone",
        "Soften background clutter so the subject stands out",
    ],
}


# 本機沒有人臉偵測：看範例照片的檔名猜人數（couple／friends 兩個人，picnic 三個人），其他都當一個人。
# 兩個人以上時，分析裡就有幾位人物，面板的「人像」分頁會出現幾張頭像讓使用者挑。
_PEOPLE = {}
_PEOPLE_BY_NAME = ((re.compile(r"picnic", re.I), 3), (re.compile(r"couple|friends|pair|duo", re.I), 2))


def _people_from_name(name):
    for pattern, count in _PEOPLE_BY_NAME:
        if pattern.search(name or ""):
            return count
    return 1


def _sample(lang=None, people=1):
    """The sample in the page's language (X-PhotoAgent-Lang): Chinese stays the source."""
    if lang is None:
        lang = request.headers.get("X-PhotoAgent-Lang") or ""
    lang = lang.lower()
    en = bool(lang) and not lang.startswith("zh")
    data = copy.deepcopy(SAMPLE_ANALYSIS)
    if en:
        data.update(copy.deepcopy(SAMPLE_ANALYSIS_EN))
        data["aspect_ratio_reason"] = "Portrait orientation: 4:5 keeps the subject more focused."
        data["subjects"][0]["label"] = "Person in the photo"
    if people > 1:
        names_zh = {2: ["左邊的人物", "右邊的人物"], 3: ["左邊的人物", "中間的人物", "右邊的人物"]}[min(people, 3)]
        names_en = {2: ["Person on the left", "Person on the right"], 3: ["Person on the left", "Person in the middle", "Person on the right"]}[min(people, 3)]
        base = data["subjects"][0]
        subjects = []
        slot = 0.9 / people
        for i in range(people):
            subject = copy.deepcopy(base)
            subject["label"] = (names_en if en else names_zh)[i]
            subject["box"] = {"x": round(0.05 + i * slot + 0.02, 3), "y": 0.1, "w": round(slot - 0.04, 3), "h": 0.75}
            # 每位的狀況不一樣，頭像上的提示和開關才看得出差別。
            subject["gaze_issue"] = {"should_fix": i % 2 == 0, "level": "moderate" if i % 2 == 0 else "none"}
            subject["blink_issue"] = {"should_fix": i == 1, "level": "mild" if i == 1 else "none"}
            subjects.append(subject)
        data["subjects"] = subjects
        data["main_subject"] = "One person on the left and one on the right" if en and people == 2 else data["main_subject"]
    return data


# 本機預覽的「處理紀錄」：面板的版本列（Version History）是從紀錄讀出來的，要有 user 才有。
# 假的分析／美化工作也記進真的紀錄（services.account.record_history，跑在本機的假 Redis 上），
# 這樣版本列、最近照片這些靠紀錄的畫面才看得到。job -> 它所屬的 case（第一次分析那個 job 的 id）。
_CASE = {}
# 假的美化要「跑」多久（秒）：給你看進行中的畫面；PHOTOAGENT_UI_MOCK_RETOUCH_SECONDS 可調，0 = 立刻完成。
_BORN = {}
_RETOUCH_SECONDS = float(os.getenv("PHOTOAGENT_UI_MOCK_RETOUCH_SECONDS", "8"))


def _record(user, job_id, case_id, version_id, params):
    if not user:
        return
    try:
        from backend.services.account import normalize_username, record_history
        record_history(normalize_username(user), job_id, "local", case_id, version_id, params=params)
    except Exception:  # 紀錄寫不進去就算了，不影響假工作本身
        pass


def _save(file_storage):
    os.makedirs(_STORE, exist_ok=True)
    job_id = f"mock-{uuid.uuid4().hex[:12]}"
    file_storage.save(os.path.join(_STORE, job_id))
    return job_id


def _path(job_id):
    path = os.path.join(_STORE, job_id)
    return path if os.path.isfile(path) else None


# ---- Chat mode: S3 + the worker, faked in-process ---------------------------------------
# Chat routes enqueue jobs on Redis (nobody consumes them locally) and move photos through
# S3. Here S3 is a dict of bytes and a thread plays the worker: every analyze/reframe job
# succeeds with the sample analysis, every finish job "renders" the ORIGINAL photo.
_BLOBS = {}
_BLOBS_LOCK = threading.Lock()


class _FakeS3:
    def upload_to_s3(self, buffers, keys):
        with _BLOBS_LOCK:
            for data, key in zip(buffers, keys):
                _BLOBS[key] = data

    def upload_buffer(self, callback, buf, key, task_id, content_type=None):
        with _BLOBS_LOCK:
            _BLOBS[key] = buf
        callback({"task_id": task_id, "status": "done"})

    def download_buffer(self, callback, key, task_id):
        with _BLOBS_LOCK:
            data = _BLOBS.get(key)
        callback({"img_buffer": data, "error": None if data is not None else "not found (ui mock)"})


def _mock_work(redis, job_id, info_key):
    info = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in redis.hgetall(info_key).items()}
    acts = json.loads(info.get("acts") or "[]")
    params = (acts[0].get("params") if acts else None) or {}
    src_keys = json.loads(info.get("srcKeys") or "[]")
    stage = params.get("stage", "full")
    if stage in ("analyze", "reframe"):
        analysis = _sample(params.get("lang") or "zh")
        if isinstance(params.get("analysis"), dict):
            analysis = {**params["analysis"], **{k: v for k, v in analysis.items() if k not in params["analysis"]}}
        analysis["_aspect_ratio_pref_at_analysis"] = str(params.get("aspect_ratio") or "auto").strip().lower()
        key = f"mock/{job_id}/analysis.json"
        _BLOBS[key] = json.dumps(analysis, ensure_ascii=False).encode()
        res0 = {"cusKeys": [key]}
    elif stage == "finish":
        source = _BLOBS.get(src_keys[0]) if src_keys else None
        if source is None:
            raise RuntimeError("source photo missing (ui mock)")
        img_key, json_key = f"mock/{job_id}/res.jpg", f"mock/{job_id}/res.json"
        _BLOBS[img_key] = source
        _BLOBS[json_key] = json.dumps({"ui_mock": True}).encode()
        res0 = {"cusKeys": [img_key, json_key]}
    else:
        raise RuntimeError(f"stage {stage} is not mocked")
    redis.hset(info_key, mapping={"0_res0": json.dumps(res0), "status": "success",
                                  "endTs": int(time.time() * 1000)})


def _mock_worker(app):
    from backend.services.put_job import MSG_DETAIL_QUEUE, MSG_ID_QUEUE, get_redis_conn
    from photo_agent.chat_jobs import CHAT_QUEUE
    redis = get_redis_conn()
    while True:
        busy = False
        for queue in (MSG_ID_QUEUE, CHAT_QUEUE):
            try:
                popped = redis.zpopmin(queue, 1)
            except Exception:
                time.sleep(1)
                continue
            if not popped:
                continue
            busy = True
            raw = popped[0][0]
            job_id = raw.decode() if isinstance(raw, bytes) else raw
            info_key = MSG_DETAIL_QUEUE.replace("{msg_id}", job_id)
            try:
                _mock_work(redis, job_id, info_key)
            except Exception as exc:  # e.g. chat/preview jobs: fail fast, the routes treat them as best-effort
                redis.hset(info_key, mapping={"status": "error", "error": str(exc)})
        if not busy:
            time.sleep(0.15)


def _init_chat_mock(app):
    from backend.services import put_job
    fake = _FakeS3()
    put_job.get_s3_accessor = lambda: fake
    put_job.get_s3_accessor_v2 = lambda: fake
    threading.Thread(target=_mock_worker, args=(app,), daemon=True, name="ui-mock-worker").start()
    app.logger.warning("%s=1: chat jobs run on an in-process mock worker; results are the original photo", ENV_FLAG)


def init_ui_mock(app):
    if os.getenv(ENV_FLAG, "") != "1":
        return
    _init_chat_mock(app)
    app.logger.warning("%s=1: panel analysis calls answer with sample data (local UI preview)", ENV_FLAG)

    @app.before_request
    def _ui_mock():
        path = request.path
        if not path.startswith(_PREFIX):
            return None
        rest = path[len(_PREFIX):]

        if request.method == "POST" and rest == "/analyze-batch":
            files = request.files.getlist("image")
            ids = [_save(f) for f in files]
            for jid, f in zip(ids, files):
                _PEOPLE[jid] = _people_from_name(f.filename)
                _CASE[jid] = jid
                _record(request.form.get("user"), jid, jid, jid, {"stage": "analyze"})
            return jsonify({"jobs": [{"job_id": jid} for jid in ids]})
        if request.method == "POST" and rest == "/analyze":
            image = request.files.get("image")
            jid = _save(image) if image else f"mock-{uuid.uuid4().hex[:12]}"
            _PEOPLE[jid] = _people_from_name(image.filename if image else "")
            _CASE[jid] = jid
            _record(request.form.get("user"), jid, jid, jid, {"stage": "analyze"})
            return jsonify({"job_id": jid})

        # 美化（Retouch）：用還沒美化的原圖當結果。/continue/<分析的 job>、/continue-version、/rerun、
        # /analyze-and-finish/<job> 都回一個新的 mock job，它的圖就是原圖（或上一版）的複本。
        if request.method == "POST" and (
            rest.startswith("/continue/") or rest in ("/continue-version", "/rerun")
            or rest.startswith("/analyze-and-finish/")
        ):
            source = _ID.search(rest)
            if not source:
                body = request.get_json(silent=True)
                source = _ID.search(json.dumps(body)) if body is not None else None
            src_path = _path(source.group(1)) if source else None
            new_id = f"mock-{uuid.uuid4().hex[:12]}"
            body_json = request.get_json(silent=True) or {}
            case_id = _CASE.get(source.group(1), source.group(1)) if source else new_id
            _CASE[new_id] = case_id
            _BORN[new_id] = time.time()
            _record(body_json.get("user"), new_id, case_id, body_json.get("action_id") or new_id,
                    {"stage": "finish", "user_prompt": body_json.get("user_prompt"), "run_count": 1})
            if src_path:
                os.makedirs(_STORE, exist_ok=True)
                with open(src_path, "rb") as fin, open(os.path.join(_STORE, new_id), "wb") as fout:
                    fout.write(fin.read())
            return jsonify({"job_id": new_id}), 202

        match = _ID.search(rest)
        if not match:
            return None
        job_id = match.group(1)
        if request.method == "GET" and rest.startswith("/result/"):
            tail = rest.rsplit("/", 1)[-1]
            if tail in ("image", "thumbnail", "preview", "pre_edit_image"):
                file_path = _path(job_id)
                return send_file(file_path) if file_path else ("", 404)
            return jsonify({
                "status": "success",
                "image_url": f"{_PREFIX}/result/{job_id}/image",
                "result": {"analysis": _sample(people=_PEOPLE.get(_CASE.get(job_id, job_id), 1)), "quality_check": {}, "usage": {}},
            })
        if rest.startswith("/status/"):
            # 美化（finish）的假工作先「處理中」幾秒，才看得到美化進行中的畫面。
            born = _BORN.get(job_id)
            if born and time.time() - born < _RETOUCH_SECONDS:
                return jsonify({"status": "processing"})
            return jsonify({"status": "success"})
        if rest.startswith("/analyze/result/"):
            return jsonify({"status": "success", "result": {
                "analysis": _sample(people=_PEOPLE.get(job_id, 1)), "compatible": True}})
        if rest.startswith("/source/"):
            file_path = _path(job_id)
            return send_file(file_path) if file_path else ("", 404)
        # Anything else about a mock job (history rows, case lookups…): an empty success.
        return jsonify({})
