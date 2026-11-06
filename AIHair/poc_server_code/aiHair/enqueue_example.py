import load_env

import argparse
import os
import uuid

from ai_hair.features import FEATURE_NAMES
from set_task_to_redis import (
    enqueue,
    get_ai_hair_inference_style_params_template,
    get_ai_hair_params_template,
)


def _upload_photo(photo_path):
    """`photo_path` uploaded to a fresh demo source key, which it returns."""
    from upload_file_from_s3 import upload_file

    src_key = f"demo/aiHair/src/{uuid.uuid4()}/{os.path.basename(photo_path)}"
    upload_file(photo_path, src_key)
    return src_key


def main():
    parser = argparse.ArgumentParser(description="Enqueue one aiHair example job.")
    parser.add_argument("--feature", choices=FEATURE_NAMES + ("designer",), default="frizzCare")
    parser.add_argument("--level", type=int, choices=(1, 2), default=None,
                        help="hairline only: 1 (hairline_simple_lv1) or 2 (hairline_simple_lv1.5)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--src-key", help="the photo, already on S3")
    source.add_argument("--photo", help="a local photo, uploaded to S3 first")
    parser.add_argument("--n-results", type=int, default=1, help="result images (1-4)")
    parser.add_argument("--ext", choices=("jpg", "png", "webp"), default="jpg")
    args = parser.parse_args()

    src_key = args.src_key or _upload_photo(args.photo)
    if args.feature == "designer":
        params = get_ai_hair_inference_style_params_template(src_key)
    else:
        params = get_ai_hair_params_template(args.feature, src_key, args.level, args.n_results,
                                             args.ext)
    job_id = enqueue(params, None)
    return job_id, params["dstKeys"]


if __name__ == "__main__":
    job_id, output_keys = main()
    print(f"\nEnqueued aiHair job: {job_id}")
    print("Expected uploaded output keys:")
    for key in output_keys:
        print(f"  {key}")
