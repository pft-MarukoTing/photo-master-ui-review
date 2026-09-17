#!/usr/bin/env python3
"""pipeline.py -- generate two hand photos, transfer nail style via ComfyUI,
judge the transfer, and append one JSONL record.

Steps:
    1. Generate two hand photos from the same prompt via one provider
       (--provider), giving a random source hand and a random reference hand.
    2. Send them through ComfyUI's nail-transfer workflow (source gets the
       reference's nail style) via comfyui_run.run_pair.
    3. Ask OpenAI's gpt-5.5 to judge whether the transfer preserved the hand
       and matched the nail style, returning a structured
       {"verdict": "good"|"bad", "reason": "..."}. Single judge, not
       Gemini+OpenAI: a 16-case comparison against human QA labels found
       gpt-5.5 the most reliable of the models tried (see git log for this
       file), and cheaper/faster beat the small residual accuracy gap from
       also running a second judge.
    4. Append one record (source/reference/result paths + the verdict) to
       --log as a JSON line.

Usage:
    conda activate apicaller
    python pipeline.py --provider gemini \\
        --checkpoint nail_salon_lora/nail_salon_lora_20260826.safetensors \\
        --comfyui-port 8450
"""

import argparse
import asyncio
import json
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

JUDGE_MODEL = "gpt-5.5"


async def generate_pair(
    provider: str, prompt: str, out_dir: Path, run_id: str, gen_model: str | None = None
) -> tuple[Path, Path]:
    source_path = out_dir / f"{run_id}_source.png"
    reference_path = out_dir / f"{run_id}_reference.png"
    if provider == "gemini":
        model = gen_model or gemini_client.DEFAULT_MODEL
        await gemini_client.generate_image(prompt, str(source_path), model=model)
        await gemini_client.generate_image(prompt, str(reference_path), model=model)
    else:
        model = gen_model or openai_client.DEFAULT_MODEL
        openai_client.generate_image(prompt, str(source_path), model=model)
        openai_client.generate_image(prompt, str(reference_path), model=model)
    return source_path, reference_path


def judge_pair(source: Path, reference: Path, result: Path, judge_prompt: str) -> dict:
    image_paths = [str(source), str(reference), str(result)]
    try:
        return openai_client.judge_images_structured(
            image_paths, judge_prompt, JUDGE_JSON_SCHEMA, model=JUDGE_MODEL
        )
    except Exception as e:
        return {"verdict": "error", "reason": str(e)}


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

    print(f"[1/4] generating source + reference via {args.provider} ({args.gen_model or 'default'}) ...")
    source_path, reference_path = await generate_pair(args.provider, prompt, args.out_dir, run_id, args.gen_model)
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

    print(f"[3/4] judging ({JUDGE_MODEL}) ...")
    verdict = judge_pair(source_path, reference_path, result_path, judge_prompt)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))

    record = {
        "run_id": run_id,
        "provider": args.provider,
        "gen_model": args.gen_model or (gemini_client.DEFAULT_MODEL if args.provider == "gemini" else openai_client.DEFAULT_MODEL),
        "checkpoint": args.checkpoint,
        "workflow": str(args.workflow),
        "source": str(source_path),
        "reference": str(reference_path),
        "result": str(result_path),
        "judge_model": JUDGE_MODEL,
        "judge": verdict,
    }
    print(f"[4/4] appending to {args.log} ...")
    append_log(args.log, record)
    print(f"[done] {result_path} logged to {args.log}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["gemini", "openai"], default="gemini",
                         help="which provider generates the source/reference hand photos")
    parser.add_argument("--gen-model", default=None,
                         help="override the generation model (default: each provider's own default in api_caller)")
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
