"""One-job PhotoAgent processing lifecycle for the queue worker."""

import base64
import json
import os
import shutil
import time

from engine_result_code import RESULT_INVALID_PARAMETER, RESULT_UNKNOWN_INTERNAL_ERROR
from photo_agent import EFFECT_NAME, validate_photo_agent_request
from pipeline import run_pipeline, analyze_stage, finish_stage


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


def derive_dst_keys(dst_key):
    prefix = dst_key.rsplit('/', 1)[0] + '/' if '/' in dst_key else ''
    return [
        prefix + '0_res0_thumb_0.png',   # result image
        prefix + '0_res0_thumb_1.json',  # result text (analysis + QA)
    ]


def derive_analysis_key(dst_key):
    prefix = dst_key.rsplit('/', 1)[0] + '/' if '/' in dst_key else ''
    return prefix + '0_res0_analysis.json'


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

    def process_for_api(self, task_id, task_info):
        start_ts = time.time()
        request = validate_photo_agent_request(task_info, task_id=task_id)

        safe_id = safe_task_folder(task_id)
        session_dir = os.path.join('server_output', safe_id)
        work_dir = os.path.join(self.monitor_dst_dir, session_dir, 'work')
        os.makedirs(work_dir, exist_ok=True)

        params = request["params"]
        self.logger.info(task_id, f'[{EFFECT_NAME}] Start job: src={len(request["src_keys"])}, dst_key={request["dst_key"]}')
        self.logger.info(task_id, f'[{EFFECT_NAME}] Params: preset={params["preset"]} image_quality={params["image_quality"]} ai_photo_editing={params["ai_photo_editing"]} has_prompt={bool(params.get("user_prompt"))}')
        if (bool(params.get("user_prompt"))):
            self.logger.info(task_id, f'[{EFFECT_NAME}] User prompt: {params.get("user_prompt")}')
        self.logger.info(task_id, f'[{EFFECT_NAME}] Metadata: {build_job_metadata(task_info)}')

        stage = params.get("stage", "full")

        try:
            src_paths = self.download_sources(task_id, request["src_keys"], session_dir)
            image_path = src_paths[0]

            with open(image_path, 'rb') as f:
                image_bytes = f.read()

            if stage == "analyze":
                self.logger.info(task_id, f'[{EFFECT_NAME}] Running analyze_stage ({len(image_bytes)} bytes)')
                analysis = analyze_stage(image_bytes)

                analysis_path = os.path.join(work_dir, 'analysis.json')
                with open(analysis_path, 'w', encoding='utf-8') as f:
                    json.dump(analysis, f, ensure_ascii=False, indent=2)

                analysis_key = derive_analysis_key(request["dst_key"])
                self.logger.info(task_id, f'[{EFFECT_NAME}] analyze_stage done, uploading artifact')
                self.upload_artifacts(task_id, [analysis_path], [analysis_key])

                job_results = {f"{request['act_id']}_res0": {"cusKeys": [analysis_key]}}
                self.logger.info(task_id, f'[{EFFECT_NAME}] Job results: {job_results}')
                return job_results

            if stage == "finish":
                self.logger.info(task_id, f'[{EFFECT_NAME}] Running finish_stage ({len(image_bytes)} bytes)')
                result = finish_stage(
                    image_bytes=image_bytes,
                    analysis=params["analysis"],
                    user_prompt=params["user_prompt"],
                    preset_key=params["preset"],
                    quality=params["image_quality"],
                    ai_photo_editing=params["ai_photo_editing"],
                )
            else:
                self.logger.info(task_id, f'[{EFFECT_NAME}] Running pipeline ({len(image_bytes)} bytes)')
                result = run_pipeline(
                    image_bytes=image_bytes,
                    user_prompt=params["user_prompt"],
                    preset_key=params["preset"],
                    quality=params["image_quality"],
                    ai_photo_editing=params["ai_photo_editing"],
                )

            derived_dst_keys = derive_dst_keys(request["dst_key"])

            result_image_path = os.path.join(work_dir, '0_res0_thumb_0.png')
            with open(result_image_path, 'wb') as f:
                f.write(base64.b64decode(result["result_image"]))

            result_json_path = os.path.join(work_dir, '0_res0_thumb_1.json')
            with open(result_json_path, 'w', encoding='utf-8') as f:
                json.dump(result["result_text"], f, ensure_ascii=False, indent=2)

            self.logger.info(task_id, f'[{EFFECT_NAME}] Pipeline done, uploading artifacts')
            self.upload_artifacts(task_id, [result_image_path, result_json_path], derived_dst_keys)

            job_results = {f"{request['act_id']}_res0": {"cusKeys": derived_dst_keys}}
            self.logger.info(task_id, f'[{EFFECT_NAME}] Job results: {job_results}')
            return job_results

        finally:
            elapsed = time.time() - start_ts
            self.logger.info(task_id, f'[{EFFECT_NAME}] process_for_api finished in {elapsed:.2f}s')
            cleanup_dir = os.path.join(self.monitor_dst_dir, session_dir)
            if os.path.exists(cleanup_dir):
                shutil.rmtree(cleanup_dir)
