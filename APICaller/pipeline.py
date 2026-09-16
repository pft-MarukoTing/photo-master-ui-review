#!/usr/bin/env python3
"""pipeline.py -- generate two hand photos, transfer nail style via ComfyUI,
judge the transfer with both Gemini and OpenAI, and append one JSONL record.

Steps:
    1. Generate two hand photos from the same prompt via one provider
       (--provider), giving a random source hand and a random reference hand.
    2. Send them through ComfyUI's nail-transfer workflow (source gets the
       reference's nail style) via comfyui_run.run_pair.
    3. Ask both Gemini (aipoc gateway) and OpenAI to judge whether the
       transfer preserved the hand and matched the nail style, each
       returning a structured {"verdict": "good"|"bad", "reason": "..."}.
    4. Append one record (source/reference/result paths + both judges'
       verdicts) to --log as a JSON line.

Usage:
    conda activate apicaller
    python pipeline.py --provider gemini \\
        --checkpoint nail_salon_lora/nail_salon_lora_20260826.safetensors \\
        --comfyui-port 8450
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from api_caller import gemini_client, openai_client
from tools.comfyui_run import DEFAULT_WORKFLOW, run_pair

TZ_UTC8 = timezone(timedelta(hours=8))
HERE = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = HERE / ".env"
DEFAULT_NAIL_PROMPT_PATH = HERE / "nail_prompt.txt"
DEFAULT_JUDGE_PROMPT_PATH = HERE / "judge_prompt.txt"
DEFAULT_LOG_PATH = HERE / "pipeline_log.jsonl"

JUDGE_JSON_SCHEMA = {
    "type": "json_schema",
    "name": "nail_transfer_judgement",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["good", "bad"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
}

# Gemini (via the aipoc gateway) has no structured-output mode -- it only
# ever returns free text (see gemini_client.judge_images). Asking it to
# answer with nothing but a JSON object and parsing that text is the closest
# equivalent; a genuinely malformed answer is surfaced as verdict "unparsed"
# rather than silently dropped. This suffix is mechanical (a gateway
# limitation), not part of the judging criteria, so it's appended in code
# rather than living in judge_prompt.txt.
GEMINI_JSON_ONLY_SUFFIX = (
    "\n\n請只回傳一個 JSON 物件，不要有任何其他文字、不要用 markdown code block，"
    '格式為 {"verdict": "good" 或 "bad", "reason": "..."}。'
)


def _parse_json_loose(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


async def generate_pair(provider: str, prompt: str, out_dir: Path, run_id: str) -> tuple[Path, Path]:
    source_path = out_dir / f"{run_id}_source.png"
    reference_path = out_dir / f"{run_id}_reference.png"
    if provider == "gemini":
        await gemini_client.generate_image(prompt, str(source_path))
        await gemini_client.generate_image(prompt, str(reference_path))
    else:
        openai_client.generate_image(prompt, str(source_path))
        openai_client.generate_image(prompt, str(reference_path))
    return source_path, reference_path


async def judge_pair(source: Path, reference: Path, result: Path, judge_prompt: str) -> dict:
    image_paths = [str(source), str(reference), str(result)]
    verdicts = {}

    try:
        raw = await gemini_client.judge_images(image_paths, judge_prompt + GEMINI_JSON_ONLY_SUFFIX)
        try:
            verdicts["gemini"] = _parse_json_loose(raw)
        except Exception:
            verdicts["gemini"] = {"verdict": "unparsed", "reason": raw}
    except Exception as e:
        verdicts["gemini"] = {"verdict": "error", "reason": str(e)}

    try:
        verdicts["openai"] = openai_client.judge_images_structured(image_paths, judge_prompt, JUDGE_JSON_SCHEMA)
    except Exception as e:
        verdicts["openai"] = {"verdict": "error", "reason": str(e)}

    return verdicts


def append_log(log_path: Path, record: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def run(args: argparse.Namespace) -> None:
    prompt_path = args.prompt_file or DEFAULT_NAIL_PROMPT_PATH
    prompt = Path(prompt_path).read_text(encoding="utf-8").strip()
    judge_prompt_path = args.judge_prompt_file or DEFAULT_JUDGE_PROMPT_PATH
    judge_prompt = Path(judge_prompt_path).read_text(encoding="utf-8").strip()
    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] generating source + reference via {args.provider} ...")
    source_path, reference_path = await generate_pair(args.provider, prompt, args.out_dir, run_id)
    print(f"  source={source_path}\n  reference={reference_path}")

    print("[2/4] running ComfyUI nail-transfer workflow ...")
    result_path = args.out_dir / f"{run_id}_result.jpg"
    run_pair(
        source_path, reference_path, args.checkpoint, result_path,
        workflow=args.workflow, comfyui_host=args.comfyui_host, comfyui_port=args.comfyui_port,
        seed=args.seed, timeout=args.timeout,
        source_title=args.source_title, reference_title=args.reference_title, lora_title=args.lora_title,
        filename_prefix=f"apicaller/pipeline/{run_id}",
    )
    print(f"  result={result_path}")

    print("[3/4] judging (gemini + openai) ...")
    verdicts = await judge_pair(source_path, reference_path, result_path, judge_prompt)
    print(json.dumps(verdicts, ensure_ascii=False, indent=2))

    record = {
        "run_id": run_id,
        "provider": args.provider,
        "checkpoint": args.checkpoint,
        "workflow": str(args.workflow),
        "source": str(source_path),
        "reference": str(reference_path),
        "result": str(result_path),
        "judges": verdicts,
    }
    print(f"[4/4] appending to {args.log} ...")
    append_log(args.log, record)
    print(f"[done] {result_path} logged to {args.log}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["gemini", "openai"], default="gemini",
                         help="which provider generates the source/reference hand photos")
    parser.add_argument("--prompt-file", type=Path, default=None,
                         help="defaults to nail_prompt.txt next to this script")
    parser.add_argument("--judge-prompt-file", type=Path, default=None,
                         help="defaults to judge_prompt.txt next to this script")
    parser.add_argument("--checkpoint", required=True,
                         help="lora_name as ComfyUI resolves it (relative to its own models/loras/ dir)")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--source-title", default="Input Image 1 (Target)")
    parser.add_argument("--reference-title", default="Input Image 2 (Reference)")
    parser.add_argument("--lora-title", default="Nail LoRA Loader")
    parser.add_argument("--out-dir", type=Path, default=HERE / "out")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--env-file", default=DEFAULT_ENV_PATH)
    args = parser.parse_args()

    load_dotenv(args.env_file)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
