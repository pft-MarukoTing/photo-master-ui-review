"""One-job PhotoAgent processing lifecycle for the queue worker."""

import base64
import json
import os
import shutil
import time

from aiserver_utils.queue import set_job_info, get_job_info
from engine_result_code import RESULT_INVALID_PARAMETER, RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import ensure_jpeg_bytes, RESULT_JPEG_QUALITY
from photo_agent import EFFECT_NAME, chat_assistant, i18n, validate_photo_agent_request
from photo_agent.selections import apply_selections
from pipeline import (
    analyze_preview_stage, analyze_stage, skipped_analysis_stage,
    reframe_stage, review_stage, finish_stage,
)


def build_job_metadata(task_info):
    return {
        "app": task_info.get("app"),
        "uid": task_info.get("uid"),
        "os": task_info.get("os"),
        "appVer": task_info.get("appVer"),
        "verType": task_info.get("verType"),
        "uniqueId": task_info.get("uniqueId"),
        "umaId": task_info.get("umaId"),
    }


def safe_task_folder(task_id):
    return str(task_id).replace('/', '_')


def derive_dst_keys(dst_key, include_pre_edit=False):
    prefix = dst_key.rsplit('/', 1)[0] + '/' if '/' in dst_key else ''
    keys = [
        prefix + '0_res0_thumb_0.jpg',   # result image
        prefix + '0_res0_thumb_1.json',  # result text (analysis + QA)
    ]
    if include_pre_edit:
        # Step 5's own output before Step 5b's gaze-repair pass overwrites
        # it (pipeline.py's finish_stage) -- only present when that pass
        # actually ran, so this key is appended, not fixed-position, and
        # callers must pass the same include_pre_edit they used when writing
        # local artifacts (_write_result_artifacts) so keys/paths line up.
        keys.append(prefix + '0_res0_thumb_2.jpg')
    return keys


def derive_analysis_key(dst_key):
    prefix = dst_key.rsplit('/', 1)[0] + '/' if '/' in dst_key else ''
    return prefix + '0_res0_analysis.json'


def derive_run_dst_keys(dst_key, run_index, include_pre_edit=False):
    """Per-RUN artifact keys for a combined job's run_count > 1 loop -- each
    RUN needs its own image/json pair so they don't overwrite each other in
    the artifact store, unlike derive_dst_keys' fixed single-result names."""
    prefix = dst_key.rsplit('/', 1)[0] + '/' if '/' in dst_key else ''
    keys = [
        prefix + f'0_res0_thumb_0_run{run_index}.jpg',
        prefix + f'0_res0_thumb_1_run{run_index}.json',
    ]
    if include_pre_edit:
        keys.append(prefix + f'0_res0_thumb_2_run{run_index}.jpg')
    return keys


def _write_result_artifacts(work_dir, result, suffix=''):
    """Decode one finish_stage result into local files: the full-res JPEG and
    its JSON metadata, in the same order derive_dst_keys/derive_run_dst_keys
    list their S3 keys, so callers can zip them directly into
    upload_artifacts. (No thumbnail is generated here -- see
    backend/routes/api.py's result_thumbnail(): which job/RUN of a multi-job
    action or multi-run combined job actually ends up displayed by the
    history list is only decided client-side after the fact, so
    pre-generating one here would mostly go to waste. The thumbnail is
    instead generated lazily on first request and cached back to S3.)

    Stored as JPEG regardless of what the edit model handed back. The image
    models return PNG today, but that is their default rather than anything
    this pipeline pins down (see openai_client/gemini_client/mai_client,
    which pass the raw b64 straight through), and a lossless screenshot-grade
    encoding of a photograph costs several times the bytes for no visible
    gain on every stored result and every download. Normalizing here -- the
    one place every stored result passes through -- means the ".jpg" keys
    below are always honest about their contents, which is also what gives
    the S3 objects their image/jpeg content type (s3_process.py guesses it
    from the key's extension)."""
    image_bytes = ensure_jpeg_bytes(
        base64.b64decode(result["result_image"]), quality=RESULT_JPEG_QUALITY
    )

    image_path = os.path.join(work_dir, f'0_res0_thumb_0{suffix}.jpg')
    with open(image_path, 'wb') as f:
        f.write(image_bytes)

    json_path = os.path.join(work_dir, f'0_res0_thumb_1{suffix}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result["result_text"], f, ensure_ascii=False, indent=2)

    paths = [image_path, json_path]

    pre_edit_b64 = result.get("pre_edit_image")
    if pre_edit_b64:
        pre_edit_path = os.path.join(work_dir, f'0_res0_thumb_2{suffix}.jpg')
        with open(pre_edit_path, 'wb') as f:
            f.write(ensure_jpeg_bytes(
                base64.b64decode(pre_edit_b64), quality=RESULT_JPEG_QUALITY
            ))
        paths.append(pre_edit_path)

    return paths


class PhotoAgentJobProcessor:
    def __init__(self, artifact_store, logger, monitor_dst_dir='./tmp'):
        self.artifact_store = artifact_store
        self.logger = logger
        self.monitor_dst_dir = monitor_dst_dir

    def download_sources(self, task_id, src_keys, session_dir):
        src_paths = []
        input_folder = os.path.join(session_dir, 'input')
        for src_key in src_keys:
            self.logger.info(task_id, f'[{EFFECT_NAME}] Download source: {src_key}')
            src_paths.append(self.artifact_store.download_file(src_key, folder_name=input_folder))
            self.logger.info(task_id, f'[{EFFECT_NAME}] Download source: {src_key} success, local path: {src_paths[-1]}')
        return src_paths

    def upload_artifacts(self, task_id, local_paths, dst_keys):
        for i, local_path in enumerate(local_paths):
            if i >= len(dst_keys):
                raise RuntimeError(
                    f"{EFFECT_NAME}: missing dstKeys[{i}] for pipeline artifact",
                    RESULT_INVALID_PARAMETER,
                )
            self.logger.info(task_id, f'[{EFFECT_NAME}] Upload artifact: {local_path} -> {dst_keys[i]}')
            self.artifact_store.upload_file(local_path, dst_keys[i])

    def _continuation_reference(self, task_id, params, src_keys, session_dir):
        """The original photo's bytes when a 接著這張調整's editor also gets
        it as a reference, else None.

        A continuation that may need it (continuation.continuation_analysis's
        _continuation_reference) carries the original as the second source.
        Only the request decides: a new change on the version goes alone, so
        the editor is not pulled back toward the original; a request that
        wants something as it was gets the original beside it. The read is
        recorded as analysis["_continuation_original"], which edit_prompt
        turns into the editor's note about the reference. A failed read sends
        the original -- the editor is told it is a reference only, so that
        costs little. A job queued before the read moved here already carries
        _continuation_original, and is taken as it is.

        The original is downloaded only here, once the editor is getting it:
        a new change that goes alone never fetches it."""
        analysis = params.get("analysis") or {}
        if len(src_keys) < 2:
            return None
        if analysis.get("_continuation_reference") and "_continuation_original" not in analysis:
            started = time.perf_counter()
            try:
                read = chat_assistant.read_continuation(params.get("user_prompt") or "")
            except Exception as exc:
                self.logger.info(task_id, f'[{EFFECT_NAME}] Continuation request read failed, sending the original: {exc}')
                read = {"needs_original": True, "restore": ""}
            self.logger.info(task_id, f'[{EFFECT_NAME}] Continuation request read in {time.perf_counter() - started:.2f}s: '
                                      f'needs_original={read["needs_original"]} restore={read["restore"]!r}')
            if read["needs_original"]:
                analysis["_continuation_original"] = {"restore": read["restore"]}
        if not analysis.get("_continuation_original"):
            return None
        (reference_path,) = self.download_sources(task_id, [src_keys[1]], session_dir)
        with open(reference_path, 'rb') as f:
            return f.read()

    def process_for_api(self, task_id, task_info):
        # The language the job's user-visible text is written in (i18n); the
        # job's params carry it only when it is not Chinese.
        acts = task_info.get("acts") if isinstance(task_info, dict) else None
        params = acts[0].get("params") if isinstance(acts, list) and acts and isinstance(acts[0], dict) else None
        with i18n.use(params.get("lang") if isinstance(params, dict) else None):
            return self._process_for_api(task_id, task_info)

    def _process_for_api(self, task_id, task_info):
        start_ts = time.time()
        request = validate_photo_agent_request(task_info, task_id=task_id)

        safe_id = safe_task_folder(task_id)
        session_dir = os.path.join('server_output', safe_id)
        work_dir = os.path.join(self.monitor_dst_dir, session_dir, 'work')
        os.makedirs(work_dir, exist_ok=True)

        params = request["params"]
        self.logger.info(task_id, f'[{EFFECT_NAME}] Start job: src={len(request["src_keys"])}, dst_key={request["dst_key"]}')
        self.logger.info(task_id, f'[{EFFECT_NAME}] Params: preset={params["preset"]} image_quality={params["image_quality"]} image_model={params["image_model"]} analysis_model={params["analysis_model"]} aspect_ratio={params.get("aspect_ratio", "auto")} output_scale={params.get("output_scale", "1K")} ai_photo_editing={params["ai_photo_editing"]} run_qa={params.get("run_qa", True)} overwrite_prompt={params.get("overwrite_prompt", False)} advanced_options={params.get("advanced_options", {})} single_pass_edit={params.get("single_pass_edit", True)} has_prompt={bool(params.get("user_prompt"))}')
        if (bool(params.get("user_prompt"))):
            self.logger.info(task_id, f'[{EFFECT_NAME}] User prompt: {params.get("user_prompt")}')
        self.logger.info(task_id, f'[{EFFECT_NAME}] Metadata: {build_job_metadata(task_info)}')

        stage = params.get("stage", "full")
        try:
            # Only the image the job works on. A second source is a
            # continuation's original, which _continuation_reference fetches
            # if the editor ends up getting it.
            image_path, = self.download_sources(task_id, request["src_keys"][:1], session_dir)

            with open(image_path, 'rb') as f:
                image_bytes = f.read()

            if stage == "analyze_preview":
                binding = params["preview_binding"]
                if binding["preview_job_id"] != task_id:
                    raise ValueError("preview_job_identity_mismatch")
                self.logger.info(task_id, f'Preview queue_wait_seconds={max(0, start_ts - params.get("preview_enqueued_at", binding["created_at"])):.4f}')
                self.logger.info(task_id, f'[{EFFECT_NAME}] Running {stage}_stage ({len(image_bytes)} bytes)')
                preview_started = time.perf_counter()
                try:
                    # A retry after publication must reuse identities, even when
                    # the previous artifact upload failed. Never mint IDs again
                    # under the same preview job once a snapshot was published.
                    from photo_agent.preview_context import PREVIEW_CONTEXT_FIELD, validate_preview_context
                    previous = get_job_info(task_id, keyToGet=[PREVIEW_CONTEXT_FIELD])
                    raw = previous.get(PREVIEW_CONTEXT_FIELD) if isinstance(previous, dict) else None
                    if isinstance(raw, str):
                        raw = json.loads(raw)
                    if raw is not None:
                        preview = validate_preview_context(raw, image_bytes=image_bytes,
                            binding=binding, src_keys=request["src_keys"])
                        if isinstance(raw.get("timing"), dict):
                            preview["timing"] = raw["timing"]
                    else:
                        preview = analyze_preview_stage(image_bytes, analysis_model=params["analysis_model"],
                            binding=binding, src_keys=request["src_keys"])
                except Exception:
                    self.logger.info(task_id, f'[{EFFECT_NAME}] Failed {stage}_stage after {time.perf_counter() - preview_started:.4f}s')
                    raise
                timing = preview.get("timing", {})
                self.logger.info(task_id, f'[{EFFECT_NAME}] Finished {stage}_stage in {time.perf_counter() - preview_started:.4f}s '
                    f'(detector={timing.get("detector_seconds", "n/a")}s, llm={timing.get("llm_seconds", "n/a")}s, '
                    f'state={preview["subject_preview"]["state"]})')
                # Publish one complete, validated snapshot before artifact I/O.
                # This is reusable data, not a lifecycle status update: a later
                # upload failure must not invalidate an already finished 2.0.
                from photo_agent.preview_context import PREVIEW_CONTEXT_FIELD, validate_preview_context
                snapshot = validate_preview_context(preview, binding=binding, src_keys=request["src_keys"])
                try:
                    self.logger.info(task_id, f'[{EFFECT_NAME}] Reporting preview snapshot to Redis (before artifact upload)')
                    report_started = time.perf_counter()
                    set_job_info(job_id=task_id, results={PREVIEW_CONTEXT_FIELD: {
                        **snapshot, "timing": {"completed_at": time.time()},
                    }})
                    # The queue helper handles its own Redis errors internally;
                    # returning is not an independent confirmation of storage.
                    self.logger.info(task_id, f'[{EFFECT_NAME}] Preview snapshot report returned in {time.perf_counter() - report_started:.4f}s')
                except Exception as exc:
                    self.logger.info(task_id, f'Preview reporting failed ({type(exc).__name__}); continuing artifact upload')
                preview_path = os.path.join(work_dir, 'preview.json')
                with open(preview_path, 'w', encoding='utf-8') as f:
                    json.dump(preview, f, ensure_ascii=False, indent=2)
                prefix = request["dst_key"].rsplit('/', 1)[0] + '/' if '/' in request["dst_key"] else ''
                preview_key = prefix + '0_res0_preview.json'
                upload_started = time.perf_counter()
                self.upload_artifacts(task_id, [preview_path], [preview_key])
                self.logger.info(task_id, f'[{EFFECT_NAME}] Preview artifact upload finished in {time.perf_counter() - upload_started:.4f}s')
                return {f"{request['act_id']}_res0": {"cusKeys": [preview_key]}}

            if stage in ("analyze", "reframe"):
                self.logger.info(task_id, f'[{EFFECT_NAME}] Running {stage}_stage ({len(image_bytes)} bytes)')
                if stage == "reframe":
                    analysis = reframe_stage(
                        image_bytes,
                        params["analysis"],
                        aspect_ratio=params.get("aspect_ratio", "auto"),
                        analysis_model=params["analysis_model"],
                    )
                elif params.get("skip_analysis"):
                    analysis = skipped_analysis_stage(
                        image_bytes,
                        aspect_ratio=params.get("aspect_ratio", "auto"),
                        advanced_options=params.get("advanced_options", {}),
                    )
                else:
                    context = params.get("precomputed_preview")
                    if context is not None:
                        from photo_agent.preview_context import validate_preview_context
                        context = validate_preview_context(context, image_bytes=image_bytes, src_keys=request["src_keys"])
                    analysis = analyze_stage(
                        image_bytes,
                        aspect_ratio=params.get("aspect_ratio", "auto"),
                        advanced_options=params.get("advanced_options", {}),
                        analysis_model=params["analysis_model"],
                        **({"precomputed_preview": context} if context is not None else {}),
                        **({"analysis_user_requests": params["analysis_user_requests"]} if params.get("analysis_user_requests") else {}),
                    )

                analysis_path = os.path.join(work_dir, 'analysis.json')
                with open(analysis_path, 'w', encoding='utf-8') as f:
                    json.dump(analysis, f, ensure_ascii=False, indent=2)

                analysis_key = derive_analysis_key(request["dst_key"])
                self.logger.info(task_id, f'[{EFFECT_NAME}] {stage}_stage done, uploading artifact')
                self.upload_artifacts(task_id, [analysis_path], [analysis_key])

                job_results = {f"{request['act_id']}_res0": {"cusKeys": [analysis_key]}}
                self.logger.info(task_id, f'[{EFFECT_NAME}] Job results: {job_results}')
                return job_results

            if stage == "finish":
                self.logger.info(task_id, f'[{EFFECT_NAME}] Running finish_stage ({len(image_bytes)} bytes)')
                reference_image_bytes = self._continuation_reference(task_id, params, request["src_keys"], session_dir)
                result = finish_stage(
                    image_bytes=image_bytes,
                    **({'reference_image_bytes': reference_image_bytes} if reference_image_bytes else {}),
                    analysis=params["analysis"],
                    user_prompt=params["user_prompt"],
                    **({'literal_texts': params['literal_texts']} if 'literal_texts' in params else {}),
                    preset_key=params["preset"],
                    quality=params["image_quality"],
                    ai_photo_editing=params["ai_photo_editing"],
                    image_model=params["image_model"],
                    analysis_model=params["analysis_model"],
                    run_qa=params.get("run_qa", True),
                    aspect_ratio=params.get("aspect_ratio", "auto"),
                    overwrite_prompt=params.get("overwrite_prompt", False),
                    single_pass_edit=params.get("single_pass_edit", True),
                    output_scale=params.get("output_scale", "1K"),
                )

                derived_dst_keys = derive_dst_keys(request["dst_key"], include_pre_edit=bool(result.get("pre_edit_image")))
                local_paths = _write_result_artifacts(work_dir, result)

                self.logger.info(task_id, f'[{EFFECT_NAME}] Pipeline done, uploading artifacts')
                self.upload_artifacts(task_id, local_paths, derived_dst_keys)

                job_results = {f"{request['act_id']}_res0": {"cusKeys": derived_dst_keys}}
                self.logger.info(task_id, f'[{EFFECT_NAME}] Job results: {job_results}')
                return job_results

            if stage != "full":
                raise ValueError("unsupported_stage")

            # Combined job ("full" stage, or stage omitted): analyze_stage
            # once, then finish_stage `run_count` times INSIDE this single
            # queued job, from the SAME analysis -- keeps every RUN's
            # generation entirely server-side and independent of the client
            # tab staying open, unlike driving each RUN as its own
            # client-submitted job (which stalls indefinitely if the phone
            # sleeps between RUNs, since nothing client-side can run in the
            # background to submit the next one). Each RUN's result is
            # written to Redis as soon as IT finishes (not only once every
            # RUN is done), so a client that's still polling can render RUNs
            # incrementally as they land; a client that isn't polling (phone
            # asleep) simply catches up to however many are done once it
            # resumes -- the work itself never depended on it watching.
            run_count = max(1, min(20, int(params.get("run_count", 1))))
            self.logger.info(task_id, f'[{EFFECT_NAME}] Running pipeline ({len(image_bytes)} bytes), run_count={run_count}')
            set_job_info(job_id=task_id, results={"stage": "analyze"})
            analysis = analyze_stage(
                image_bytes,
                aspect_ratio=params.get("aspect_ratio", "auto"),
                advanced_options=params.get("advanced_options", {}),
                analysis_model=params["analysis_model"],
            )
            # The review-screen choices of a 美化 on a case that skipped its
            # analysis (api.analyze_and_finish): picked before this analysis
            # existed, so they land here, the way /continue applies them.
            if params.get("selections") is not None:
                analysis = apply_selections(analysis, params["selections"])
            set_job_info(job_id=task_id, results={"stage": "edit", "run_count": run_count, "runs_done": 0})

            # Step 3 once for the whole job: every RUN receives the same
            # user direction (see pipeline.review_stage).
            review = review_stage(
                analysis,
                params["user_prompt"],
                overwrite_prompt=params.get("overwrite_prompt", False),
                analysis_model=params["analysis_model"],
            )

            run_results = []
            for run_index in range(run_count):
                result = finish_stage(
                    image_bytes=image_bytes,
                    analysis=analysis,
                    user_prompt=params["user_prompt"],
                    preset_key=params["preset"],
                    quality=params["image_quality"],
                    ai_photo_editing=params["ai_photo_editing"],
                    image_model=params["image_model"],
                    analysis_model=params["analysis_model"],
                    run_qa=params.get("run_qa", True),
                    aspect_ratio=params.get("aspect_ratio", "auto"),
                    overwrite_prompt=params.get("overwrite_prompt", False),
                    single_pass_edit=params.get("single_pass_edit", True),
                    output_scale=params.get("output_scale", "1K"),
                    review=review,
                    **({'literal_texts': params['literal_texts']} if 'literal_texts' in params else {}),
                )

                run_dst_keys = derive_run_dst_keys(request["dst_key"], run_index, include_pre_edit=bool(result.get("pre_edit_image")))
                local_paths = _write_result_artifacts(work_dir, result, suffix=f'_run{run_index}')

                self.logger.info(task_id, f'[{EFFECT_NAME}] RUN {run_index + 1}/{run_count} done, uploading artifacts')
                self.upload_artifacts(task_id, local_paths, run_dst_keys)

                run_results.append({"cusKeys": run_dst_keys})
                set_job_info(job_id=task_id, results={
                    f"{request['act_id']}_res0": {"runs": run_results},
                    "runs_done": run_index + 1,
                })

            job_results = {f"{request['act_id']}_res0": {"runs": run_results}}
            self.logger.info(task_id, f'[{EFFECT_NAME}] Job results: {job_results}')
            return job_results

        finally:
            elapsed = time.time() - start_ts
            self.logger.info(task_id, f'[{EFFECT_NAME}] process_for_api finished in {elapsed:.2f}s')
            cleanup_dir = os.path.join(self.monitor_dst_dir, session_dir)
            if os.path.exists(cleanup_dir):
                shutil.rmtree(cleanup_dir)
