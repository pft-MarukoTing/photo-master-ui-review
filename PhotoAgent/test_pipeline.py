"""Quick local smoke-test for the pipeline. Run from PhotoAgent/:
    OPENAI_API_KEY=sk-... python test_pipeline.py path/to/photo.jpg
"""

import json
import os
import sys

from pipeline import run_pipeline

if len(sys.argv) < 2:
    print("Usage: python test_pipeline.py <image_path> [user_prompt] [preset] [quality]")
    sys.exit(1)

image_path = sys.argv[1]
user_prompt = sys.argv[2] if len(sys.argv) > 2 else ""
preset_key  = sys.argv[3] if len(sys.argv) > 3 else "natural"
quality     = sys.argv[4] if len(sys.argv) > 4 else "medium"

with open(image_path, "rb") as f:
    image_bytes = f.read()

print(f"Running pipeline: preset={preset_key} quality={quality} has_prompt={bool(user_prompt)}")
result = run_pipeline(
    image_bytes=image_bytes,
    user_prompt=user_prompt,
    preset_key=preset_key,
    quality=quality,
)

out_image = "test_output.png"
out_json  = "test_output.json"

import base64
with open(out_image, "wb") as f:
    f.write(base64.b64decode(result["result_image"]))
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(result["result_text"], f, ensure_ascii=False, indent=2)

print(f"Done. Image -> {out_image}  Text -> {out_json}")
qa = result["result_text"].get("quality_check", {})
print(f"QA: pass={qa.get('pass')} score={qa.get('score')} status={qa.get('overall_status')}")
