import load_env

import argparse
import json
import os
import time

import requests

from set_task_to_redis import (
    enqueue,
    get_photo_agent_params_template,
    get_photo_agent_custom_params_template,
    get_photo_agent_editorial_params_template,
    get_photo_agent_portrait_params_template,
    s3_accessor,
    redis_conn,
    MSG_DETAIL_QUEUE,
)
from aiserver_utils.s3_utils import get_bucket_name


def _derive_output_keys(base_dst_key):
    prefix = base_dst_key.rsplit('/', 1)[0] + '/' if '/' in base_dst_key else ''
    return [
        prefix + '0_res0_thumb_0.png',
        prefix + '0_res0_thumb_1.json',
    ]


def wait_for_result(job_id, timeout=300, poll_interval=2):
    key = MSG_DETAIL_QUEUE.replace("{msg_id}", job_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        values = redis_conn.hmget(key, ['status'])
        status = values[0]
        if status == 'success':
            return 'success'
        if status == 'error':
            return 'error'
        time.sleep(poll_interval)
    return 'timeout'


def s3_create_presigned_url(src_key, action='get_object', expiration=3600):
    bucket = get_bucket_name(src_key)
    return s3_accessor.s3_client_proxy.s3Client.generate_presigned_url(
        action,
        Params={'Bucket': bucket, 'Key': src_key},
        ExpiresIn=expiration,
    )


def _build_params(mode):
    if mode == "natural":
        return get_photo_agent_params_template()
    if mode == "custom":
        return get_photo_agent_custom_params_template()
    if mode == "editorial":
        return get_photo_agent_editorial_params_template()
    if mode == "portrait":
        return get_photo_agent_portrait_params_template()
    raise ValueError(f"Unsupported mode: {mode}")


def main():
    parser = argparse.ArgumentParser(description="Enqueue one photoAgent example job.")
    parser.add_argument("--mode", choices=["natural", "custom", "editorial", "portrait"], default="natural")
    parser.add_argument("--src", help="Path to source image file", default="/app/sample_images/pancake.webp")
    parser.add_argument("--no-ai-edit", action="store_true", help="Disable AI photo editing (local enhancement only)")
    parser.add_argument("--image-quality", choices=["low", "medium", "high"], help="Override image quality")
    parser.add_argument("--user-prompt", help="User instruction for the edit (e.g. 'warm golden tones')")
    args = parser.parse_args()

    with open(args.src, "rb") as f:
        image_bytes = f.read()

    params = _build_params(args.mode)
    params["acts"][0]["params"]["ai_photo_editing"] = not args.no_ai_edit
    if args.image_quality:
        params["acts"][0]["params"]["image_quality"] = args.image_quality
    if args.user_prompt:
        params["acts"][0]["params"]["user_prompt"] = args.user_prompt
    job_id = enqueue(params, [image_bytes])
    output_keys = _derive_output_keys(params["dstKeys"][0])
    return job_id, output_keys, params["acts"][0]["params"]

if __name__ == "__main__":
    job_id, output_keys, enqueued_params = main()
    print(f"\nEnqueued photoAgent job: {job_id}")
    print(f"  preset={enqueued_params['preset']}")
    print(f"  image_quality={enqueued_params['image_quality']}")
    print(f"  ai_photo_editing={enqueued_params['ai_photo_editing']}")
    print(f"  user_prompt={enqueued_params.get('user_prompt') or '(none)'}")
    print("Expected output keys:")
    for key in output_keys:
        print(f"  {key}")

    print("\nWaiting for job result...")
    status = wait_for_result(job_id)
    print(f"Job status: {status}")

    if status == 'success':
        out_dir = os.path.join("results", job_id)
        os.makedirs(out_dir, exist_ok=True)

        urls = {}
        for key in output_keys:
            url = s3_create_presigned_url(key, "get_object")
            urls[key] = url

        summary_path = os.path.join(out_dir, "result_urls.json")
        with open(summary_path, "w") as f:
            json.dump({"job_id": job_id, "status": status, "urls": urls}, f, indent=2)
        print(f"URLs saved to: {summary_path}")

        print("Downloading result files...")
        for key, url in urls.items():
            filename = key.split("/")[-1]
            local_path = os.path.join(out_dir, filename)
            response = requests.get(url)
            response.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(response.content)
            print(f"  Saved: {local_path}")
