#!/usr/bin/env python3
"""auto_loop.py -- fully automated, repeatable QA-and-data-expansion loop.

Every iteration:
  1. Copy a random image from out/natural_nail into out/autoloop_source (A).
  2. Pick a random category+item from nail_style.yaml (same mechanism as
     nail_style_generate.py) and generate one styled hand photo from
     nail_prompt_uniform_styled.txt into out/autoloop_reference (B) --
     filename keeps the category/item slugs for traceability.
  3. Run (A, B) through the nail-transfer ComfyUI workflow (comfyui_run.run_pair)
     -> out/autoloop_result (C).
  4. Judge (A, B, C) with the same single gpt-5.5 structured verdict pipeline.py
     uses (pipeline.judge_pair) -- judge.py's own output is free text, not
     parseable, so it can't drive this branch automatically.
       - "good": the example passed, loop back to step 1.
       - "bad": a hard case -- expand it into N training triples (steps 5-7),
         appended to the existing out/training_candidates_{src,ref,res} +
         out/training_candidates_mapping.csv dataset built earlier by hand,
         continuing its 5-digit code sequence.
  5. Copy B N times into training_candidates_reference (E), new codes.
  6. Lock-edit B (nail_lock_edit_prompt.txt) N times, varying pose/skin/
     background while keeping the nail design fixed, into
     training_candidates_result (F), same codes.
  7. For each F image, run it through ComfyUI against one random reference
     from --style-pool (comfy_random_pair.py's "one random reference per
     source" logic) into training_candidates_source (D), same codes -- this
     overwrites F's correct nail design with a random one, producing a
     "before" photo whose ground-truth correction is F/E.

Usage (run inside the apicaller conda env, from the APICaller/ directory):
    python tasks/AINail/tools/auto_loop.py --iterations 5 \\
        --checkpoint nail_salon_lora/nail_salon_lora_20260826.safetensors \\
        --comfyui-port 8450
"""

import argparse
import asyncio
import csv
import io
import json
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

HERE = Path(__file__).resolve().parent.parent
APICALLER_ROOT = HERE.parent.parent
sys.path.insert(0, str(APICALLER_ROOT))  # api_caller package
sys.path.insert(0, str(HERE))  # pipeline, tools.comfyui_run, tools.nail_style_generate -- must win over APICaller/tools/
import pipeline
from api_caller import gemini_client, openai_client
from tools.comfyui_run import DEFAULT_WORKFLOW, run_pair
from tools.nail_style_generate import _slug, build_prompt, load_categories

TZ_UTC8 = timezone(timedelta(hours=8))
DEFAULT_ENV_PATH = APICALLER_ROOT / ".env"
DEFAULT_STYLE_YAML = HERE / "nail_style.yaml"
DEFAULT_STYLE_TEMPLATE = HERE / "prompts/nail_prompt_uniform_styled.txt"
DEFAULT_LOCK_EDIT_PROMPT = HERE / "prompts/nail_lock_edit_prompt.txt"
DEFAULT_NATURAL_DIR = HERE / "out/natural_nail"
DEFAULT_STYLE_POOL = Path("/data/home/rdmt/jack_lin/aitoolkit2/ai-toolkit2/datasets/sam3_cropped_control_2")
IMG_EXTS = (".jpg", ".jpeg", ".png")

A_DIR = HERE / "out/autoloop_source"
B_DIR = HERE / "out/autoloop_reference"
C_DIR = HERE / "out/autoloop_result"
D_DIR = HERE / "out/training_candidates_source"
E_DIR = HERE / "out/training_candidates_reference"
F_DIR = HERE / "out/training_candidates_result"
LOCK_TMP_DIR = HERE / "out/autoloop_lock_tmp"
MAPPING_CSV = HERE / "out/training_candidates_mapping.csv"
LOOP_LOG = HERE / "out/autoloop_log.jsonl"
MAPPING_FIELDS = ["code", "batch", "ref_original", "src_original", "res_original"]


def list_images(d: Path) -> list:
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)


def to_jpeg(src: Path, dst: Path, quality: int = 95) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.convert("RGB").save(dst, format="JPEG", quality=quality)


def next_code(mapping_csv: Path) -> int:
    if not mapping_csv.exists():
        return 1
    with open(mapping_csv, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 1
    return max(int(r["code"]) for r in rows) + 1


def append_mapping(mapping_csv: Path, rows: list[dict]) -> None:
    is_new = not mapping_csv.exists()
    mapping_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(mapping_csv, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAPPING_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


async def gen_image(provider: str, prompt: str, out_path: Path, model: str | None = None) -> None:
    if provider == "gemini":
        await gemini_client.generate_image(prompt, str(out_path), model=model or gemini_client.DEFAULT_MODEL)
    else:
        openai_client.generate_image(prompt, str(out_path), model=model or openai_client.DEFAULT_MODEL)


async def edit_image(provider: str, image_path: Path, prompt: str, out_path: Path, model: str | None = None) -> None:
    if provider == "gemini":
        await gemini_client.edit_image(str(image_path), prompt, str(out_path), model=model or gemini_client.DEFAULT_MODEL)
    else:
        openai_client.edit_image(str(image_path), prompt, str(out_path), model=model or openai_client.DEFAULT_MODEL)


async def run_iteration(args: argparse.Namespace, categories: dict, judge_prompt: str,
                         style_template: str, lock_edit_prompt: str, style_pool: list[Path]) -> None:
    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")

    natural_pick = random.choice(list_images(args.natural_dir))
    a_path = A_DIR / f"{run_id}{natural_pick.suffix.lower()}"
    A_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(natural_pick, a_path)
    print(f"[1/4] A (natural source) = {a_path.name} (from {natural_pick.name})")

    category = random.choice(list(categories))
    item = random.choice(categories[category])
    style_text = f"{category} - {item}"
    prompt = build_prompt(style_template, style_text)
    b_path = B_DIR / f"{run_id}_{_slug(category)}__{_slug(item)}.png"
    print(f"[2/4] B (styled reference) style={style_text!r} -> generating ...")
    await gen_image(args.provider, prompt, b_path, args.gen_model)
    print(f"  B = {b_path.name}")

    c_path = C_DIR / f"{run_id}.jpg"
    print("[3/4] C (comfy result) -> running ComfyUI ...")
    run_pair(
        a_path, b_path, args.checkpoint, c_path,
        workflow=args.workflow, comfyui_host=args.comfyui_host, comfyui_port=args.comfyui_port,
        seed=args.seed, timeout=args.timeout, filename_prefix=f"apicaller/autoloop/{run_id}",
    )
    print(f"  C = {c_path.name}")

    print("[4/4] judging (A, B, C) ...")
    verdict = pipeline.judge_pair(a_path, b_path, c_path, judge_prompt)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))

    record = {
        "run_id": run_id,
        "provider": args.provider,
        "gen_model": args.gen_model or (gemini_client.DEFAULT_MODEL if args.provider == "gemini" else openai_client.DEFAULT_MODEL),
        "checkpoint": args.checkpoint,
        "judge_model": pipeline.JUDGE_MODEL,
        "source": str(a_path),
        "style_category": category,
        "style_item": item,
        "reference": str(b_path),
        "result": str(c_path),
        "judge": verdict,
        "expanded": False,
        "codes": [],
    }

    if verdict.get("verdict") != "bad":
        pipeline.append_log(LOOP_LOG, record)
        print("[done] verdict is not 'bad' -- nothing to expand, moving to next iteration")
        return

    n = args.expand_count
    print(f"[expand] verdict=bad -- expanding into {n} training triple(s) ...")
    start_code = next_code(MAPPING_CSV)
    codes = list(range(start_code, start_code + n))
    mapping_rows = []

    for i, code in enumerate(codes, 1):
        code_str = f"{code:05d}"
        print(f"  [{i}/{n}] code={code_str}")

        e_path = E_DIR / f"{code_str}.jpg"
        to_jpeg(b_path, e_path)

        lock_tmp = LOCK_TMP_DIR / f"{run_id}_lock_{i:02d}.png"
        await edit_image(args.provider, b_path, lock_edit_prompt, lock_tmp, args.gen_model)
        f_path = F_DIR / f"{code_str}.jpg"
        to_jpeg(lock_tmp, f_path)

        style_ref = random.choice(style_pool)
        d_path = D_DIR / f"{code_str}.jpg"
        run_pair(
            f_path, style_ref, args.checkpoint, d_path,
            workflow=args.workflow, comfyui_host=args.comfyui_host, comfyui_port=args.comfyui_port,
            seed=args.seed, timeout=args.timeout,
            filename_prefix=f"apicaller/autoloop/{run_id}/{code_str}",
        )

        # Written one row at a time (instead of batching all N until the loop
        # finishes) so a crash partway through an expansion only orphans the
        # one in-flight code, not every triple already written in this batch.
        row = {
            "code": code_str,
            "batch": f"autoloop_{run_id}",
            "ref_original": f"{b_path.name} (repeated, single fixed reference)",
            "src_original": f"comfy-random-style: {style_ref.name} applied onto res/{code_str}.jpg",
            "res_original": lock_tmp.name,
        }
        append_mapping(MAPPING_CSV, [row])
        mapping_rows.append(row)

    record["expanded"] = True
    record["codes"] = [r["code"] for r in mapping_rows]
    pipeline.append_log(LOOP_LOG, record)
    print(f"[done] expanded {n} triple(s) -> codes {mapping_rows[0]['code']}..{mapping_rows[-1]['code']}")


async def run(args: argparse.Namespace) -> None:
    categories = load_categories(args.style_yaml)
    categories = {k: v for k, v in categories.items() if v}
    style_template = args.style_template.read_text(encoding="utf-8")
    lock_edit_prompt = args.lock_edit_prompt.read_text(encoding="utf-8").strip()
    judge_prompt = (args.judge_prompt_file or pipeline.DEFAULT_JUDGE_PROMPT_PATH).read_text(encoding="utf-8").strip()
    style_pool = list_images(args.style_pool)
    if not style_pool:
        raise SystemExit(f"no images found in --style-pool {args.style_pool}")

    for d in (A_DIR, B_DIR, C_DIR, D_DIR, E_DIR, F_DIR, LOCK_TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)

    for n in range(1, args.iterations + 1):
        print(f"\n===== iteration {n}/{args.iterations} =====")
        await run_iteration(args, categories, judge_prompt, style_template, lock_edit_prompt, style_pool)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iterations", type=int, default=1, help="how many fresh A/B/C checks to run")
    parser.add_argument("--expand-count", type=int, default=30, help="N: how many training triples per 'bad' verdict")
    parser.add_argument("--provider", choices=["gemini", "openai"], default="gemini")
    parser.add_argument("--gen-model", default=None)
    parser.add_argument("--natural-dir", type=Path, default=DEFAULT_NATURAL_DIR)
    parser.add_argument("--style-yaml", type=Path, default=DEFAULT_STYLE_YAML)
    parser.add_argument("--style-template", type=Path, default=DEFAULT_STYLE_TEMPLATE)
    parser.add_argument("--lock-edit-prompt", type=Path, default=DEFAULT_LOCK_EDIT_PROMPT)
    parser.add_argument("--judge-prompt-file", type=Path, default=None)
    parser.add_argument("--style-pool", type=Path, default=DEFAULT_STYLE_POOL,
                         help="real-photo pool step 7 draws a random 'wrong' style reference from")
    parser.add_argument("--checkpoint", required=True,
                         help="lora_name as ComfyUI resolves it (relative to its own models/loras/ dir)")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--env-file", default=DEFAULT_ENV_PATH)
    parser.add_argument("--random-seed", type=int, default=None)
    args = parser.parse_args()

    load_dotenv(args.env_file)
    if args.random_seed is not None:
        random.seed(args.random_seed)

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
