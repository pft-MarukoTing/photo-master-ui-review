"""Chat session store: the state the chat page and its detail panel share.

A session is a thin layer over the existing execution flow -- photos are
still analyzed and rendered by the same queued analyze/finish jobs, and every
render is still recorded as a history v3 version (case_id = the photo's
source key, version_id = the render's action_id). What lives here is only
what history does not already hold: which photos are in this conversation,
the draft plan for each, their in-flight jobs, and the message log.

    <base>:chat:<session_id>            HASH  meta / updated_at / defaults /
                                              photo:<id> / photo:<id>:plan /
                                              done:<job or action id>
    <base>:chat:<session_id>:messages   LIST  one JSON message per entry
    <base>:chat:sessions                ZSET  session_id -> updated_at

<base> is account.py's per-user prefix, so every key carries the same Redis
Cluster hash tag as that user's history. A photo's state (written by the
server as its jobs progress) and its plan (written by the user) are separate
fields, so a status poll and a plan edit landing together cannot overwrite
each other. Every key expires SESSION_TTL_SECONDS after the last write; the
versions a session produced stay in history, which has no TTL.
"""

import json
import time
import uuid

from backend.services.account import _user_base
from photo_agent.chat_plan import default_plan, default_settings

SESSION_TTL_SECONDS = 30 * 24 * 3600
MESSAGES_MAX_LEN = 500
MAX_PHOTOS_PER_SESSION = 20


def _client():
    # Imported on use so tests can swap in a fake client.
    from aiserver_utils.pf_redis import pf_redis_client
    return pf_redis_client


def _session_key(user, session_id):
    return f"{_user_base(user)}:chat:{session_id}"


def _messages_key(user, session_id):
    return f"{_session_key(user, session_id)}:messages"


def _index_key(user):
    return f"{_user_base(user)}:chat:sessions"


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


def _touch(user, session_id):
    now = time.time()
    redis = _client()
    redis.hset(_session_key(user, session_id), "updated_at", json.dumps(now))
    for key in (_session_key(user, session_id), _messages_key(user, session_id), _index_key(user)):
        redis.expire(key, SESSION_TTL_SECONDS)
    redis.zadd(_index_key(user), {session_id: now})
    return now


def _write(user, session_id, fields):
    _client().hset(_session_key(user, session_id), mapping={k: json.dumps(v) for k, v in fields.items()})
    _touch(user, session_id)


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def create_session(user):
    session_id = new_id("s")
    meta = {"session_id": session_id, "user": user, "created_at": time.time(), "photo_order": [], "active_photo_id": None}
    _write(user, session_id, {"meta": meta, "defaults": default_settings()})
    return load_session(user, session_id)


def load_session(user, session_id):
    """The whole session (without messages), or None if absent/expired."""
    raw = _client().hgetall(_session_key(user, session_id)) or {}
    fields = {_decode(k): json.loads(v) for k, v in raw.items()}
    meta = fields.get("meta")
    if meta is None:
        return None
    photos = []
    for photo_id in meta["photo_order"]:
        state = fields.get(f"photo:{photo_id}")
        if state is None:
            continue
        photos.append({**state, "plan": fields.get(f"photo:{photo_id}:plan") or default_plan(fields["defaults"])})
    return {**meta, "updated_at": fields.get("updated_at"), "defaults": fields["defaults"], "photos": photos}


def active_version(version_id, job_id, run=1):
    """A photo's current result. `at` is what lets a session summary tell
    which of its photos produced the newest one -- nothing else in the photo
    state is ordered in time."""
    return {"version_id": version_id, "job_id": job_id, "run": run, "at": time.time()}


def _summary(session_id, session):
    """One row of list_sessions: enough for a tile -- a thumbnail and the
    status badge over it."""
    photos = session["photos"]
    statuses = [p["status"] for p in photos]
    rendered = [p for p in photos if (p.get("active_version") or {}).get("job_id")]
    # The newest result is the session's thumbnail. Reversed so that photos
    # written before `at` existed (and any exact tie) fall back to the last
    # one in the conversation, which is the one most recently worked on.
    newest = max(reversed(rendered), key=lambda p: p["active_version"].get("at") or 0, default=None)
    if "rendering" in statuses:
        state = "rendering"
    elif "analyzing" in statuses:
        state = "analyzing"
    elif photos and all(s == "failed" for s in statuses):
        state = "failed"
    elif rendered:
        state = "done"
    else:
        state = "ready"
    return {
        "session_id": session_id,
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "photo_count": len(photos),
        # The original of the first photo: the fallback for a session that
        # has not rendered anything yet, and for a result thumbnail the
        # client cannot load.
        "thumbnail_job_id": photos[0]["analyze_job_id"] if photos else None,
        "thumbnail_result": (
            {"job_id": newest["active_version"]["job_id"], "run": newest["active_version"].get("run") or 1}
            if newest else None
        ),
        # A session of several photos shows as one tile with its first few
        # photos in it: each photo's current result, else its original.
        "thumbnails": [
            {"job_id": p["active_version"]["job_id"], "run": p["active_version"].get("run") or 1}
            if p in rendered else {"job_id": p["analyze_job_id"], "run": None}
            for p in photos[:4]
        ] if len(photos) > 1 else [],
        "status": state,
        "rendered_count": len(rendered),
        # Which history cases this session already covers: the home page
        # lists the rest of the user's history next to it (routes/chat.py).
        "case_ids": [p["case_id"] for p in photos],
    }


def session_summary(user, session_id):
    """list_sessions' row for one session, or None if absent/expired."""
    session = load_session(user, session_id)
    return _summary(session_id, session) if session else None


def list_sessions(user, limit=20):
    """Newest first. Index entries whose session has expired are dropped."""
    redis = _client()
    out = []
    for raw_id in redis.zrevrange(_index_key(user), 0, max(0, limit) - 1):
        session_id = _decode(raw_id)
        session = load_session(user, session_id)
        if session is None:
            redis.zrem(_index_key(user), session_id)
            continue
        out.append(_summary(session_id, session))
    return out


def delete_session(user, session_id):
    """Drops one session, its messages and its index entry."""
    redis = _client()
    redis.delete(_session_key(user, session_id), _messages_key(user, session_id))
    redis.zrem(_index_key(user), session_id)


def delete_all_sessions(user):
    """Drops every session of the user, live or not, and the index."""
    redis = _client()
    for raw_id in redis.zrange(_index_key(user), 0, -1):
        session_id = _decode(raw_id)
        redis.delete(_session_key(user, session_id), _messages_key(user, session_id))
    redis.delete(_index_key(user))


def find_session_for_case(user, case_id):
    """The newest live session holding the photo `case_id`, or None -- a
    case reopened from history goes back to its own conversation while
    that still exists."""
    for raw_id in _client().zrevrange(_index_key(user), 0, -1):
        session_id = _decode(raw_id)
        session = load_session(user, session_id)
        if session and any(p["case_id"] == case_id for p in session["photos"]):
            return session_id
    return None


def save_batch_plans(user, session_id, defaults, plans):
    """One write for a batch edit: the new defaults plus {photo_id: plan}."""
    fields = {"defaults": defaults}
    fields.update({f"photo:{photo_id}:plan": plan for photo_id, plan in plans.items()})
    _write(user, session_id, fields)


def save_photo_plan(user, session_id, photo_id, plan):
    _write(user, session_id, {f"photo:{photo_id}:plan": plan})


def save_photo_state(user, session_id, photo):
    state = {k: v for k, v in photo.items() if k != "plan"}
    _write(user, session_id, {f"photo:{photo['photo_id']}": state})


def add_photos(user, session_id, meta, photos):
    """Append new photos (state dicts) to the session. `meta` is the session
    as loaded; its photo_order is extended here."""
    order = list(meta["photo_order"]) + [p["photo_id"] for p in photos]
    new_meta = {k: meta[k] for k in ("session_id", "user", "created_at")}
    new_meta["photo_order"] = order
    new_meta["active_photo_id"] = meta.get("active_photo_id") or (order[0] if order else None)
    fields = {"meta": new_meta}
    for photo in photos:
        fields[f"photo:{photo['photo_id']}"] = photo
        fields[f"photo:{photo['photo_id']}:plan"] = default_plan(meta["defaults"])
    _write(user, session_id, fields)


def claim(user, session_id, token):
    """True exactly once per token -- lets whichever status poll first sees a
    job finish post its message, and no other."""
    return bool(_client().hsetnx(_session_key(user, session_id), f"done:{token}", "1"))


def append_messages(user, session_id, messages):
    if not messages:
        return
    redis = _client()
    key = _messages_key(user, session_id)
    redis.rpush(key, *[json.dumps(m) for m in messages])
    redis.ltrim(key, -MESSAGES_MAX_LEN, -1)
    _touch(user, session_id)


def replace_last_message(user, session_id, message_id, card):
    """Rewrite the newest message's card, if message_id is still the newest
    -- a step that is still going on (like looking through the options) stays
    one message. Returns the new message, or None when something came after."""
    redis = _client()
    key = _messages_key(user, session_id)
    raw = redis.lindex(key, -1)
    last = json.loads(raw) if raw else None
    if not last or last["id"] != message_id:
        return None
    message = {**last, "card": card, "created_at": time.time()}
    redis.lset(key, -1, json.dumps(message))
    _touch(user, session_id)
    return message


def get_messages(user, session_id, limit=100):
    raw = _client().lrange(_messages_key(user, session_id), -max(1, limit), -1) or []
    return [json.loads(m) for m in raw]


def make_message(role, text="", card=None):
    return {"id": new_id("m"), "role": role, "created_at": time.time(), "text": text, "card": card}
