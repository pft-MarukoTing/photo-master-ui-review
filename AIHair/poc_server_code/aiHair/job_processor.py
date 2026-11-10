"""One-job aiHair processing lifecycle for the queue worker."""

import os
import shutil

from ai_hair import EFFECT_NAME, run_ai_hair_pipeline, validate_ai_hair_request
from ai_hair.c2pa_sign import sign_file
from ai_hair.exceptions import C2PASignFailedError, C2PAUnsupportedError
from ai_hair.timing import TaskTiming, export_timing


# PIL format and save options for each result extension dstKeys may have.
_SAVE_FORMATS = {
    ".jpg": ("JPEG", {"quality": 95}),
    ".jpeg": ("JPEG", {"quality": 95}),
    ".png": ("PNG", {}),
    ".webp": ("WEBP", {"quality": 95}),
}
# The timing fields the server log's one-line summary shows, in order (the
# JSON line in the timing log has them all).
_TIMING_SUMMARY = ("download", "load", "generate", "postprocess", "cpu_wait", "upload",
                   "total")


def build_job_metadata(task_info):
    """Collect job metadata that the aiHair pipeline may need later."""
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


def save_image(image, path):
    """`image` (PIL) saved at `path` in the format its extension names."""
    fmt, options = _SAVE_FORMATS[os.path.splitext(path)[1].lower()]
    if fmt == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    image.save(path, format=fmt, **options)
    return path


def timing_summary(timing):
    """One line of `timing` for the server log."""
    parts = [f"feature={timing.get('feature')}", f"level={timing.get('level')}",
             f"status={timing.get('status')}"]
    for name in _TIMING_SUMMARY:
        if f"{name}_sec" in timing:
            parts.append(f"{name}={timing[f'{name}_sec']:.3f}s")
            if name == "generate" and "api_sec" in timing:
                parts.append(f"(api {timing['api_sec']:.3f}s)")
            if name == "postprocess" and timing.get("postprocess_breakdown"):
                parts.append("(" + " ".join(f"{k} {v:.3f}s" for k, v
                                            in timing["postprocess_breakdown"].items()) + ")")
    return " ".join(parts)


class AiHairJobProcessor:
    def __init__(self, artifact_store, logger, monitor_dst_dir='./tmp', timing_log_path=None):
        self.artifact_store = artifact_store
        self.logger = logger
        self.monitor_dst_dir = monitor_dst_dir
        self.timing_log_path = timing_log_path

    def download_sources(self, task_id, src_keys, session_dir):
        src_paths = []
        input_folder = os.path.join(session_dir, 'input')
        for src_key in src_keys:
            self.logger.info(task_id, f'[{EFFECT_NAME}] Download source: {src_key}')
            src_paths.append(self.artifact_store.download_file(src_key, folder_name=input_folder))
        return src_paths

    def upload_results(self, task_id, images, dst_keys, work_dir):
        """Each of `images` saved in its dst key's format, C2PA-signed where it
        can be (unsigned, with a warning, where it can't, as aiSong uploads its
        songs) and uploaded to that key. Returns (keys, sizes)."""
        keys, sizes = [], []
        for i, (image, dst_key) in enumerate(zip(images, dst_keys)):
            local_path = save_image(image, os.path.join(
                work_dir, f"result_{i}{os.path.splitext(dst_key)[1].lower()}"))
            self._sign(task_id, local_path, dst_key)
            self._upload(task_id, local_path, dst_key)
            keys.append(dst_key)
            sizes.append(os.path.getsize(local_path))
        return keys, sizes

    def _sign(self, task_id, local_path, dst_key):
        with open(local_path, "rb") as f:
            data = f.read()
        try:
            signed = sign_file(data, local_path)
        except (C2PASignFailedError, C2PAUnsupportedError) as error:
            self.logger.warning(
                task_id,
                f'[{EFFECT_NAME}] C2PA sign skipped, uploading unsigned: '
                f'engine={EFFECT_NAME}, key={dst_key}, error={error}',
            )
            return
        with open(local_path, "wb") as f:
            f.write(signed)

    def _upload(self, task_id, local_path, dst_key):
        self.logger.info(task_id, f'[{EFFECT_NAME}] Upload artifact: {local_path} -> {dst_key}')
        self.artifact_store.upload_file(local_path, dst_key)

    def export_timing(self, task_id, timing):
        """The task's timing to the server log (one line) and the timing log
        (one JSON line); failing to write it never fails the task."""
        try:
            self.logger.info(task_id, f'[{EFFECT_NAME}][timing] {timing_summary(timing)}')
            export_timing(dict(timing), self.timing_log_path)
        except Exception as e:  # noqa: BLE001 — the job's result matters more
            self.logger.warning(task_id, f'[{EFFECT_NAME}] timing export failed: {e}')

    def process_for_api(self, task_id, task_info):
        timing = TaskTiming(task_id=str(task_id), tsk_id=(task_info or {}).get('tskId'),
                            engine=EFFECT_NAME, status="error")
        safe_task_id = safe_task_folder(task_id)
        session_dir = os.path.join('server_output', safe_task_id)
        try:
            request = validate_ai_hair_request(task_info, task_id=task_id)
            params = request["params"]
            dst_keys = request["dst_keys"]
            timing.update(feature=params["feature"], level=params["level"],
                          n_images=len(dst_keys))
            work_dir = os.path.join(self.monitor_dst_dir, session_dir, 'work')
            os.makedirs(work_dir, exist_ok=True)

            actInfo = task_info['acts'][0]['params']
            print(f'[{task_id}] actInfo: {actInfo}')
            self.logger.info(task_id, f'[{EFFECT_NAME}] actInfo: {actInfo}')
            if 'inference_style_list' in actInfo:
                self.logger.info(
                    task_id,
                    f'[{EFFECT_NAME}] Inference style: style_id={request["style_id"]}, '
                    f'has_style_values={request["has_style_values"]}, '
                    f'applied_keys={request["applied_style_keys"]}',
                )
            self.logger.info(task_id, f'[{EFFECT_NAME}] Start job: feature={params["feature"]}, '
                                      f'level={params["level"]}, dst_keys={dst_keys}')
            self.logger.info(task_id, f'[{EFFECT_NAME}] Metadata: {build_job_metadata(task_info)}')

            with timing.section("download"):
                src_paths = self.download_sources(task_id, request["src_keys"], session_dir)
            pipeline_result = run_ai_hair_pipeline(
                params=params,
                src_paths=src_paths,
                n_images=len(dst_keys),
                task_id=task_id,
                logger=self.logger,
                timing=timing,
            )
            skipped = pipeline_result["skipped"]
            if skipped:
                # The effect left the photo as it is (grayHair with no grey
                # hair, hairline with the forehead covered): the photo is the
                # result.
                self.logger.info(task_id, f'[{EFFECT_NAME}] Skipped by {params["feature"]}: '
                                          f'{skipped}; uploading the photo as it is')
                images = [pipeline_result["photo"]] * len(dst_keys)
            else:
                images = pipeline_result["outputs"]
                if len(images) < len(dst_keys):
                    self.logger.warning(task_id, f'[{EFFECT_NAME}] {len(dst_keys)} results asked '
                                                 f'for, {len(images)} made')
            with timing.section("upload"):
                keys, sizes = self.upload_results(task_id, images, dst_keys, work_dir)
            result_keys = {
                "dstKeys": keys,
                "sizes": sizes,
                "feature": params["feature"],
                "skipped": skipped or "",
            }
            job_results = {f"{request['act_id']}_res0": result_keys}
            timing["status"] = "skipped" if skipped else "success"
            timing["n_results"] = len(keys)
            self.logger.info(task_id, f'[{EFFECT_NAME}] Job results: {job_results}')
            return job_results
        except Exception as e:
            timing["error"] = str(e.args[0]) if e.args else repr(e)
            raise
        finally:
            self.export_timing(task_id, timing.close())
            cleanup_dir = os.path.join(self.monitor_dst_dir, session_dir)
            print(f'[{task_id}] Cleaning up: {cleanup_dir}')
            if os.path.exists(cleanup_dir):
                shutil.rmtree(cleanup_dir)
