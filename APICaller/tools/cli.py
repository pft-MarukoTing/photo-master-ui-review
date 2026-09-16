"""Generic text-to-image / image-edit CLI: one prompt in, an image out, via
Gemini and/or OpenAI. See ../README.md for setup. Run from the APICaller/
directory (needs the api_caller package on sys.path).

Usage:
    conda activate apicaller
    python tools/cli.py --provider both --prompt "a red bicycle on a beach" --out-dir ./out
    python tools/cli.py --provider gemini --prompt-file my_prompt.txt --out-dir ./out
    python tools/cli.py --provider openai --prompt "..." --model gpt-image-1 --size 1024x1024

    # edit mode: --image points at an existing file to edit instead of generating from scratch
    python tools/cli.py --mode edit --provider gemini --image in.png --prompt "..." --out-dir ./out
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_caller import gemini_client, openai_client

DEFAULT_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def _load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    if args.prompt:
        return args.prompt
    raise SystemExit("Provide --prompt or --prompt-file")


async def _run(args: argparse.Namespace) -> None:
    prompt = _load_prompt(args)
    if args.mode == "edit" and not args.image:
        raise SystemExit("--mode edit requires --image")

    if args.provider in ("gemini", "both"):
        out = os.path.join(args.out_dir, f"{args.out_name}_gemini.png")
        print(f"[gemini] {args.mode} -> {out}")
        try:
            if args.mode == "edit":
                await gemini_client.edit_image(args.image, prompt, out, model=args.gemini_model)
            else:
                await gemini_client.generate_image(prompt, out, model=args.gemini_model)
            print(f"[gemini] done -> {out}")
        except Exception as e:
            print(f"[gemini] FAILED: {e}")

    if args.provider in ("openai", "both"):
        out = os.path.join(args.out_dir, f"{args.out_name}_openai.png")
        print(f"[openai] {args.mode} -> {out}")
        try:
            if args.mode == "edit":
                openai_client.edit_image(args.image, prompt, out, model=args.openai_model, size=args.size)
            else:
                openai_client.generate_image(prompt, out, model=args.openai_model, size=args.size)
            print(f"[openai] done -> {out}")
        except Exception as e:
            print(f"[openai] FAILED: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["gemini", "openai", "both"], default="both")
    parser.add_argument("--mode", choices=["generate", "edit"], default="generate")
    parser.add_argument("--image", default=None, help="Source image path (required for --mode edit)")
    parser.add_argument("--prompt", default=None, help="Prompt text")
    parser.add_argument("--prompt-file", default=None, help="Path to a file containing the prompt")
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--out-name", default="image", help="Output file basename (provider suffix added)")
    parser.add_argument("--gemini-model", default=gemini_client.DEFAULT_MODEL)
    parser.add_argument("--openai-model", default=openai_client.DEFAULT_MODEL)
    parser.add_argument("--size", default=openai_client.DEFAULT_SIZE, help="OpenAI image size")
    parser.add_argument("--env-file", default=DEFAULT_ENV_PATH, help="Path to a .env file with API credentials")
    args = parser.parse_args()

    load_dotenv(args.env_file)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
