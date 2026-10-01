#!/usr/bin/env python3
"""judge.py -- send several images + a prompt to Gemini and/or OpenAI and get
back a text judgement. Generic: not specific to nail-transfer, but defaults
to a source/reference/result nail-transfer judging prompt when none is given.
Run from the APICaller/ directory.

Usage:
    conda activate apicaller
    python tasks/AINail/tools/judge.py --provider both \\
        --images out/gemini_run1_gemini.png out/gemini_run2_gemini.png out/gemini_pipeline_result.jpg
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from api_caller import gemini_client, openai_client

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".env"

DEFAULT_PROMPT = (
    "IMAGE 1 = SOURCE (原始手部照片，尚未套用新指甲風格前)。\n"
    "IMAGE 2 = REFERENCE (提供指甲風格的照片)。\n"
    "IMAGE 3 = RESULT (經過指甲風格移植後的輸出圖)。\n\n"
    "請用繁體中文評估 RESULT 是否符合以下兩個目標：\n"
    "1. 手的部分（手勢、姿勢、膚色、戒指/飾品、袖口服裝、背景）是否與 SOURCE 一致，沒有被改動。\n"
    "2. 指甲的部分（形狀、顏色、圖案、亮粉/金屬箔等元素）是否成功換成了 REFERENCE 的風格。\n\n"
    "請分別針對這兩點給出評語與 1-10 分的評分，並在最後給一句總結。"
)


def _load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return args.prompt or DEFAULT_PROMPT


async def _run(args: argparse.Namespace) -> None:
    prompt = _load_prompt(args)

    if args.provider in ("gemini", "both"):
        print("=" * 20, "[gemini judge]", "=" * 20)
        try:
            answer = await gemini_client.judge_images(args.images, prompt, model=args.gemini_model)
            print(answer)
        except Exception as e:
            print(f"[gemini] FAILED: {e}")
        print()

    if args.provider in ("openai", "both"):
        print("=" * 20, "[openai judge]", "=" * 20)
        try:
            answer = openai_client.judge_images(args.images, prompt, model=args.openai_model)
            print(answer)
        except Exception as e:
            print(f"[openai] FAILED: {e}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["gemini", "openai", "both"], default="both")
    parser.add_argument("--images", nargs="+", required=True, help="image paths in order, e.g. source reference result")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--gemini-model", default=gemini_client.DEFAULT_MODEL)
    parser.add_argument("--openai-model", default=openai_client.DEFAULT_JUDGE_MODEL)
    parser.add_argument("--env-file", default=DEFAULT_ENV_PATH)
    args = parser.parse_args()

    load_dotenv(args.env_file)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
