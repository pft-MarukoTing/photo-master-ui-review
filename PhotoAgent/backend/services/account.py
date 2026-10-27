"""Username-only account + processing-history store, backed by the same
shared Redis client as put_job.py/aiserver_utils.queue. No auth/password --
a username is just a namespace to group a person's own job history under.

History keeps EVERY edit of a photo, not just the latest one. That shape is
what drives the three-key layout below; the single-hash v2 schema it replaces
is documented at LEGACY_HISTORY_KEY_FORMAT.

    <base>:history:v3                 HASH   field = case_id
                                             value = case summary (below)
    <base>:case:<case_id>:versions    HASH   field = "<version_id>|<job_id>|<run>"
                                             value = one result image's record
    <base>:jobindex                   HASH   field = job_id -> case_id

Three properties this layout is bought for, in the order they mattered:

1. One field per RESULT IMAGE, so every write is an HSET to a field that did
   not exist before. v2 kept a whole case in one field's JSON, which Redis
   sees as an opaque string -- appending to it meant read-modify-write, and
   two requests landing together (a run_count>1 revise submits N jobs at
   once) would each write back a copy that predated the other, silently
   dropping one. Nothing here reads before it writes, so that race cannot
   happen at all rather than being guarded against.

2. The list page reads ONLY the summary hash. /history/<user> re-fetches
   every case on every page of scrolling, so anything stored per-version
   would be re-transferred there on each one -- at a realistic 10-20
   versions per case that is megabytes per request. Versions live in their
   own per-case key and are read only when that case is opened.

3. Deleting one version (or one image of a multi-run version) is a single
   HDEL, with no chance of the "deleted version comes back" that a
   read-modify-write delete racing an append would produce.

Three stored identifiers are deliberately OPAQUE strings -- their current
values are noted, but nothing downstream may assume them, because each is
expected to be computed differently later:

  case_id     today: the job's resolved srcKeys[0] (the original photo's S3
              key), which is stable across a whole revise chain since
              /continue carries srcKeys forward unchanged. If editing ever
              chains off the PREVIOUS RESULT instead of the original, srcKeys
              stops being stable and case_id becomes "whatever case the prior
              job belongs to" -- which is exactly what jobindex above answers.
  version_id  today: the action_id grouping one "Run" click's N jobs. If
              those N jobs ever become N runs of ONE job (the way a combined
              /submit already handles run_count), action_id disappears and
              this is simply the job's own id.
  based_on    today: always None, i.e. "ran on the original photo", because
              that is all /continue can currently do. Points at a specific
              version field once editing can chain off a previous result.
"""

import json
import os
import re
import time

from aiserver_utils.pf_redis import pf_redis_client

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Namespaced per deployment (like REDIS_MSG_QUEUE/REDIS_MSG_INFO_FORMAT in
# put_job.py) so dev and poc, which otherwise share the same Redis cluster,
# don't collide on the same username's account/history entries.
USERS_SET_KEY = os.getenv("REDIS_USERS_SET_KEY", "yce_ai:photoAgent:users")

# v2: one HASH per user, one field per case, the whole case in that field's
# JSON. Read-only now -- get_history/get_history_entry still surface entries
# that only exist here (as a single-version case), and _seed_from_legacy
# migrates one lazily the first time it is written to again, so no migration
# pass or downtime is needed. Nothing writes a new v2 entry.
LEGACY_HISTORY_KEY_FORMAT = os.getenv(
    "REDIS_USER_HISTORY_KEY_FORMAT", "yce_ai:photoAgent:user:{user}:history:v2"
)


def _derive_user_base(legacy_format):
    """The per-user key prefix the v3 keys hang off, taken from the v2 format
    so a deployment that only overrides REDIS_USER_HISTORY_KEY_FORMAT (see
    .env.default) still gets its own namespace here without setting three
    more env vars.

    The `{user}` placeholder is rewritten to `{{{user}}}` so the formatted key
    carries a literal `{username}` -- a Redis Cluster hash tag, which pins all
    of one user's keys to the same slot. Without it the summary hash, each
    case's versions hash and jobindex could land on different nodes, and
    clear_history could not delete them in one multi-key call.
    """
    base = legacy_format.rsplit(":history:", 1)[0]
    return base.replace("{user}", "{{{user}}}")


_USER_BASE = os.getenv("REDIS_USER_KEY_BASE_FORMAT") or _derive_user_base(LEGACY_HISTORY_KEY_FORMAT)

# No TTL/eviction exists anywhere else in this codebase's Redis usage --
# these two caps are the only bound on unlimited growth. They bound different
# axes: how many distinct PHOTOS a user's history holds, and how many EDITS
# of one photo it keeps.
HISTORY_MAX_LEN = 200
VERSIONS_MAX_PER_CASE = int(os.getenv("PHOTO_AGENT_MAX_VERSIONS_PER_CASE", "50"))

# Mirrors job_processor.py's own clamp on the same value, so a case never
# records more result images than the worker will actually produce.
MAX_RUN_COUNT = 20

# Which params the case summary re-exposes at its top level. The frontend has
# read these off a history entry directly since v2 (viewHistoryRecord in
# app.js), so the summary keeps carrying the LATEST version's copy of them and
# that code needs no change; per-version values live on each version record.
_SUMMARY_PARAM_KEYS = (
    "preset", "image_quality", "image_model", "analysis_model", "aspect_ratio",
    "output_scale", "ai_photo_editing", "run_qa", "user_prompt", "overwrite_prompt", "run_count",
    "advanced_options", "single_pass_edit", "stage", "compatibility_version",
)


def normalize_username(raw):
    name = (raw or "").strip()
    if not USERNAME_RE.match(name):
        raise ValueError("user must be 1-64 characters, letters/digits/_/- only")
    return name


def create_account(username):
    """Idempotent: returns True if this username is newly registered."""
    added = pf_redis_client.sadd(USERS_SET_KEY, username)
    return bool(added)


def account_exists(username):
    return bool(pf_redis_client.sismember(USERS_SET_KEY, username))


# ── Key helpers ───────────────────────────────────────────────────────────

def _user_base(username):
    return _USER_BASE.format(user=username)


def _cases_key(username):
    return f"{_user_base(username)}:history:v3"


def _versions_key(username, case_id):
    # case_id is an S3 path (slashes, dots) -- fine in a Redis key, and it
    # cannot contain braces, so the hash tag _user_base already placed stays
    # the FIRST brace pair and therefore the one the cluster hashes on.
    return f"{_user_base(username)}:case:{case_id}:versions"


def _jobindex_key(username):
    return f"{_user_base(username)}:jobindex"


def _case_analysis_key(username):
    return f"{_user_base(username)}:case_analysis"


def _legacy_key(username):
    return LEGACY_HISTORY_KEY_FORMAT.format(user=username)


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


def _loads(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# ── Version records ───────────────────────────────────────────────────────

def _version_field(version_id, job_id, run):
    """One field per result image. Both multi-result shapes collapse onto it:
    a two-stage revise with run_count>1 submits N separate jobs that each
    produce one image (N fields, differing job_id, run always 1), while a
    combined /submit loops run_count times inside ONE job (N fields, same
    job_id, differing run). The frontend already flattens both into a single
    (job_id, run) list -- see fetchAllHistoryRuns in app.js -- so this is the
    storage side of an addressing scheme that already exists.
    """
    return f"{version_id}|{job_id}|{run}"


def _parse_version_field(field):
    parts = _decode(field).split("|")
    if len(parts) != 3:
        return None
    version_id, job_id, run = parts
    try:
        run = int(run)
    except (TypeError, ValueError):
        return None
    return version_id, job_id, run


def _clamp_run_count(params):
    try:
        value = int((params or {}).get("run_count") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_RUN_COUNT, value))


def _load_version_records(username, case_id):
    """Every result image of one case, as a list of flat records. Cheap by
    design -- one case's hash holds at most VERSIONS_MAX_PER_CASE versions,
    unlike the summary hash which holds every case the user has."""
    raw = pf_redis_client.hgetall(_versions_key(username, case_id)) or {}
    records = []
    for field, value in raw.items():
        parsed = _parse_version_field(field)
        entry = _loads(value)
        if not parsed or entry is None:
            continue
        version_id, job_id, run = parsed
        records.append({
            **entry,
            "field": _decode(field),
            "version_id": version_id,
            "job_id": job_id,
            "run": run,
        })
    return records


def _group_versions(records):
    """Result images -> versions, oldest first.

    A version's own timestamp is the earliest of its images': the N images of
    one run_count>1 action are written together, but a later repair/retry
    appended to the same version must not make that version look newer than
    versions that came after it.
    """
    grouped = {}
    for record in records:
        version = grouped.setdefault(record["version_id"], {
            "version_id": record["version_id"],
            "kind": record.get("kind") or "edit",
            "created_at": record.get("created_at") or 0,
            "based_on": record.get("based_on"),
            "params": record.get("params") or {},
            "path": record.get("path") or "local",
            "results": [],
            "runs": [],
        })
        version["created_at"] = min(version["created_at"] or record.get("created_at") or 0,
                                    record.get("created_at") or 0)
        version["results"].append({
            "job_id": record["job_id"],
            "run": record["run"],
            "field": record["field"],
        })
        if record["job_id"] not in version["runs"]:
            version["runs"].append(record["job_id"])
    for version in grouped.values():
        version["results"].sort(key=lambda r: (r["job_id"], r["run"]))
    return sorted(grouped.values(), key=lambda v: (v["created_at"], v["version_id"]))


# ── Case summary ──────────────────────────────────────────────────────────

def _build_summary(versions):
    """The case summary is derived ENTIRELY from its versions -- nothing is
    accumulated in it. That is what makes the one non-atomic write in here
    harmless: two concurrent writers both recompute from the same version
    hash, so the worst a lost update costs is a summary that briefly lags,
    and the next write (or _repair_summary on read) restores it. Compare v2,
    where the same race dropped real data.
    """
    if not versions:
        return None
    latest = versions[-1]
    edits = [v for v in versions if v.get("kind") != "analyze"]
    params = latest.get("params") or {}
    summary = {
        "path": latest.get("path") or "local",
        # `ts` remains the latest-activity timestamp used to sort/prune.
        # uploaded_at is pinned to the first submission, while modified_at
        # advances only once an EDIT job exists (an analyze-only job is still
        # just the upload stage) -- both now fall out of the version list
        # instead of being carried forward by hand.
        "ts": latest.get("created_at"),
        "uploaded_at": versions[0].get("created_at"),
        "modified_at": edits[-1].get("created_at") if edits else None,
        # Named `runs` (not `latest_runs`) because that is what a history
        # entry's job ids have been called since v2 and what the history grid
        # still reads to fetch thumbnails and poll status.
        "runs": list(latest.get("runs") or []),
        "version_id": latest.get("version_id"),
        "version_count": len(versions),
    }
    for key in _SUMMARY_PARAM_KEYS:
        if key in params:
            summary[key] = params[key]
    return summary


def _write_summary(username, case_id, versions):
    summary = _build_summary(versions)
    if summary is None:
        pf_redis_client.hdel(_cases_key(username), case_id)
        return None
    pf_redis_client.hset(_cases_key(username), case_id, json.dumps(summary))
    return summary


def _rebuild_case(username, case_id):
    """Recompute this case's summary from its version hash, pruning versions
    over the per-case cap first. Returns (summary, versions)."""
    versions = _group_versions(_load_version_records(username, case_id))
    versions = _prune_versions(username, case_id, versions)
    return _write_summary(username, case_id, versions), versions


# ── v2 compatibility ──────────────────────────────────────────────────────

def _legacy_entry(username, case_id):
    raw = pf_redis_client.hget(_legacy_key(username), case_id)
    return _loads(raw) if raw else None


def _legacy_as_case(entry, case_id):
    """A v2 entry presented in the v3 shape: one case, one version, holding
    whatever job ids that entry had accumulated. Read-only -- it is what the
    history list and a report lookup show for a case never touched since the
    upgrade."""
    if not entry:
        return None
    params = {key: entry[key] for key in _SUMMARY_PARAM_KEYS if key in entry}
    created_at = entry.get("uploaded_at") or entry.get("ts") or 0
    # `runs` is v2's own list of this entry's job ids; `job_id` is the single
    # field entries written before that list existed used instead.
    job_ids = list(entry.get("runs") or ([entry["job_id"]] if entry.get("job_id") else []))
    version = {
        "version_id": entry.get("action_id") or case_id,
        "kind": "analyze" if entry.get("stage") == "analyze" else "edit",
        "created_at": entry.get("ts") or created_at,
        "based_on": None,
        "params": params,
        "path": entry.get("path") or "local",
        "runs": job_ids,
        "results": [{"job_id": job_id, "run": 1, "field": None} for job_id in job_ids],
        "legacy": True,
    }
    summary = {
        "path": version["path"],
        "ts": entry.get("ts"),
        "uploaded_at": entry.get("uploaded_at") or entry.get("ts"),
        # Same rule the v2 reader used: analyze-only records deliberately have
        # no modified_at until an edit has run.
        "modified_at": entry.get("modified_at",
                                 None if entry.get("stage") == "analyze" else entry.get("ts")),
        "runs": version["runs"],
        "version_id": version["version_id"],
        "version_count": 1,
        "legacy": True,
        **params,
    }
    return summary, [version]


def _seed_from_legacy(username, case_id):
    """Move a v2 case into v3 the first time it gains a new version, so its
    history is not split across the two schemas. A no-op for a case that is
    already in v3, or never existed in v2."""
    if pf_redis_client.hexists(_cases_key(username), case_id):
        return
    if pf_redis_client.exists(_versions_key(username, case_id)):
        return
    converted = _legacy_as_case(_legacy_entry(username, case_id), case_id)
    if not converted:
        return
    _, versions = converted
    version = versions[0]
    for job_id in version["runs"]:
        _write_version_record(username, case_id, version["version_id"], job_id, 1, {
            "kind": version["kind"],
            "created_at": version["created_at"],
            "based_on": None,
            "params": version["params"],
            "path": version["path"],
        })


# ── Writing ───────────────────────────────────────────────────────────────

def _write_version_record(username, case_id, version_id, job_id, run, payload):
    pf_redis_client.hset(
        _versions_key(username, case_id),
        _version_field(version_id, job_id, run),
        json.dumps(payload),
    )
    pf_redis_client.hset(_jobindex_key(username), job_id, case_id)


def record_history(username, job_id, path, case_id, version_id, params=None, based_on=None):
    """Record one submitted job as a version (or as more result images of an
    existing version) of `case_id`.

    One row per result image the job will produce: run_count is 1 for every
    job the two-stage path submits (the frontend fans out N separate
    /continue calls, so each is its own job) and N for a combined /submit,
    which loops finish_stage N times inside the single job. Rows are written
    at submit time, before the images exist -- a job that fails, or produces
    fewer images than asked for, simply leaves rows whose result never
    arrives, which is the same thing a failed job already did in v2 and which
    readers already skip.

    Re-recording the same (version_id, job_id, run) overwrites that one row
    rather than adding another, which is what makes this safe to call again
    for a job already recorded.

    params carries the submitted settings (preset/image_quality/image_model/
    aspect_ratio/ai_photo_editing/run_qa/user_prompt...) -- not just for
    display, but so the frontend can resubmit a local-path job using exactly
    what it was first processed with, without re-deriving those from the
    job's own Redis hash. Stored per version, because that is the unit those
    settings actually belong to: two versions of one photo are usually two
    different settings.
    """
    _seed_from_legacy(username, case_id)
    now = time.time()
    payload = {
        "kind": "analyze" if (params or {}).get("stage") == "analyze" else "edit",
        "created_at": now,
        "based_on": based_on,
        "params": params or {},
        "path": path,
    }
    for run in range(1, _clamp_run_count(params) + 1):
        _write_version_record(username, case_id, version_id, job_id, run, payload)
    _rebuild_case(username, case_id)
    _prune_oldest(username)


def attach_case_analysis(username, case_id, job_id):
    """Point `case_id` at an analysis run on its photo after the fact -- the
    one a 「只照需求改」 upload skipped, asked for later in either mode.

    The analysis is also recorded as a version of its own (record_history);
    this pointer is what both modes read back off the case
    (get_history_entry's analysis_job_id) to fill in the stub analysis such a
    case's renders carry."""
    pf_redis_client.hset(_case_analysis_key(username), case_id, job_id)


# ── Pruning ───────────────────────────────────────────────────────────────

def _prune_versions(username, case_id, versions):
    """Drop the oldest versions of one case once it is over the cap. Returns
    the versions that remain."""
    if len(versions) <= VERSIONS_MAX_PER_CASE:
        return versions
    overflow = versions[:len(versions) - VERSIONS_MAX_PER_CASE]
    kept = versions[len(versions) - VERSIONS_MAX_PER_CASE:]
    fields = [result["field"] for version in overflow for result in version["results"]
              if result.get("field")]
    if fields:
        pf_redis_client.hdel(_versions_key(username, case_id), *fields)
    _forget_jobs(username, overflow, kept)
    return kept


def _prune_oldest(username):
    """Hash fields aren't insertion-ordered/trimmable like a list -- fetch the
    summaries (small, one per case), and if over the cap drop whichever cases
    have the oldest `ts` until back at HISTORY_MAX_LEN."""
    raw_entries = pf_redis_client.hgetall(_cases_key(username)) or {}
    if len(raw_entries) <= HISTORY_MAX_LEN:
        return
    parsed = []
    for field, raw in raw_entries.items():
        entry = _loads(raw) or {}
        parsed.append((_decode(field), entry.get("ts") or 0))
    parsed.sort(key=lambda item: item[1])
    overflow = len(parsed) - HISTORY_MAX_LEN
    for case_id, _ in parsed[:overflow]:
        delete_history_entry(username, case_id)


def _forget_jobs(username, removed_versions, kept_versions):
    """Drop jobindex rows for jobs that no version of this case points at any
    more. Scoped to the jobs actually removed, and kept if some surviving
    version still references the same job id (a combined job's N images share
    one job_id, so removing one image must not orphan the rest)."""
    still_referenced = {result["job_id"]
                        for version in kept_versions for result in version["results"]}
    stale = {result["job_id"]
             for version in removed_versions for result in version["results"]}
    stale -= still_referenced
    if stale:
        pf_redis_client.hdel(_jobindex_key(username), *stale)


# ── Reading ───────────────────────────────────────────────────────────────

def get_history(username, limit=50, offset=0):
    """Case summaries, newest first. Reads only the summary hash -- opening a
    case (get_history_entry) is what pulls its versions."""
    raw_entries = pf_redis_client.hgetall(_cases_key(username)) or {}
    analyses = {_decode(field): _decode(job_id)
                for field, job_id in (pf_redis_client.hgetall(_case_analysis_key(username)) or {}).items()}
    entries = []
    seen = set()
    for field, raw in raw_entries.items():
        entry = _loads(raw)
        if entry is None:
            continue
        case_id = _decode(field)
        seen.add(case_id)
        # image_key (the hash field itself, not part of the stored value) --
        # the frontend needs this back to identify which entry to delete
        # (delete_history_entry below), since it's otherwise never exposed.
        # Still called image_key on the wire: it is what every existing
        # caller, frontend included, already names this identifier.
        entry["image_key"] = case_id
        _with_case_analysis(entry, analyses.get(case_id))
        entries.append(entry)
    # Cases that only ever existed under v2 and haven't been touched since.
    for field, raw in (pf_redis_client.hgetall(_legacy_key(username)) or {}).items():
        case_id = _decode(field)
        if case_id in seen:
            continue
        converted = _legacy_as_case(_loads(raw), case_id)
        if not converted:
            continue
        summary, _ = converted
        summary["image_key"] = case_id
        entries.append(summary)
    entries.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    try:
        offset = max(0, int(offset))
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        offset, limit = 0, 50
    return entries[offset:offset + limit]


def _with_case_analysis(entry, job_id):
    if job_id:
        entry["analysis_job_id"] = job_id
    return entry


def get_history_entry(username, case_id, include_versions=True):
    """Resolve one case by its durable case/history key, with its full version
    list -- newest last, the order the UI walks them in."""
    raw = pf_redis_client.hget(_cases_key(username), case_id)
    entry = _loads(raw) if raw else None
    analysis_job_id = _decode(pf_redis_client.hget(_case_analysis_key(username), case_id))
    if entry is None:
        converted = _legacy_as_case(_legacy_entry(username, case_id), case_id)
        if not converted:
            return None
        entry, versions = converted
        entry["image_key"] = case_id
        if include_versions:
            entry["versions"] = versions
        return _with_case_analysis(entry, analysis_job_id)
    entry["image_key"] = case_id
    _with_case_analysis(entry, analysis_job_id)
    if include_versions:
        entry["versions"] = _group_versions(_load_version_records(username, case_id))
    return entry


def get_history_entry_for_job(username, job_id):
    """Return the case containing ``job_id``, if it is still in history.

    Reports store a job id rather than a case key. jobindex answers this in
    one HGET; before it existed this walked every entry's job ids, which the
    v3 layout would have turned into one read per case.
    """
    case_id = _decode(pf_redis_client.hget(_jobindex_key(username), job_id))
    if case_id:
        entry = get_history_entry(username, case_id)
        if entry:
            return entry
    # Reports predating jobindex, whose case may still only exist in v2.
    for field, raw in (pf_redis_client.hgetall(_legacy_key(username)) or {}).items():
        entry = _loads(raw) or {}
        if job_id in (entry.get("runs") or []) or entry.get("job_id") == job_id:
            converted = _legacy_as_case(entry, _decode(field))
            if converted:
                summary, versions = converted
                summary["image_key"] = _decode(field)
                summary["versions"] = versions
                return summary
    return None


# ── Deleting ──────────────────────────────────────────────────────────────

def delete_history_entry(username, case_id):
    """Removes one case (one distinct photo, however many versions and result
    images it accumulated) from a user's history -- the job(s) it points to
    aren't touched, only the history pointers to them."""
    versions = _group_versions(_load_version_records(username, case_id))
    _forget_jobs(username, versions, [])
    pf_redis_client.delete(_versions_key(username, case_id))
    pf_redis_client.hdel(_cases_key(username), case_id)
    pf_redis_client.hdel(_case_analysis_key(username), case_id)
    # A case that was never migrated out of v2 would otherwise reappear in
    # get_history's legacy pass right after being deleted.
    pf_redis_client.hdel(_legacy_key(username), case_id)


def delete_version(username, case_id, version_id, job_id=None, run=None):
    """Remove one version, or one single result image of it.

    A plain HDEL of the matching fields: no read-modify-write, so this cannot
    race an incoming version and resurrect what was just deleted (or lose
    what just arrived). Same scope as delete_history_entry -- the underlying
    job and its S3 artifacts are left alone, only the history pointer goes.

    Deleting the last version deletes the case too; an empty case has nothing
    to show in the history grid and nothing to reopen.
    """
    records = _load_version_records(username, case_id)
    targets = [r for r in records
               if r["version_id"] == version_id
               and (job_id is None or r["job_id"] == job_id)
               and (run is None or r["run"] == run)]
    if not targets:
        return False
    pf_redis_client.hdel(_versions_key(username, case_id), *[r["field"] for r in targets])
    remaining = _group_versions([r for r in records if r not in targets])
    _forget_jobs(username, _group_versions(targets), remaining)
    if not remaining:
        delete_history_entry(username, case_id)
        return True
    _write_summary(username, case_id, remaining)
    return True


def clear_history(username):
    """Removes every case from a user's history in one sweep -- same "only the
    history pointers, not the underlying jobs" scope as delete_history_entry,
    just for all of them at once. All of a user's keys share a cluster slot
    (see _derive_user_base), so the per-case version hashes can be deleted
    alongside the summary/index keys."""
    case_ids = [_decode(field) for field in (pf_redis_client.hgetall(_cases_key(username)) or {})]
    for case_id in case_ids:
        pf_redis_client.delete(_versions_key(username, case_id))
    pf_redis_client.delete(_cases_key(username))
    pf_redis_client.delete(_jobindex_key(username))
    pf_redis_client.delete(_case_analysis_key(username))
    pf_redis_client.delete(_legacy_key(username))
