#!/usr/bin/env python3
"""nail_style_generate.py -- for each category in nail_style.yaml, pick one
random item and generate an image with that style locked in via
nail_prompt_uniform_styled.txt's {{STYLE}} placeholder.

Unlike nail_prompt_uniform.txt (where the model itself picks a style from a
short in-prompt list), this picks the style ourselves in Python from a much
larger structured catalog (nail_style.yaml, ~30 categories / ~640 items) and
substitutes it into the template before calling the API -- see the module
docstring discussion in nail_style.yaml's commit for why that catalog isn't
just handed to the model as prose.

Usage (run inside the `apicaller` conda env, from the APICaller/ directory):
    python tools/nail_style_generate.py --provider gemini --out-dir out/style_test
"""

import argparse
import asyncio
import random
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_caller import gemini_client, openai_client

HERE = Path(__file__).resolve().parent.parent
DEFAULT_STYLE_YAML = HERE / "nail_style.yaml"
DEFAULT_TEMPLATE_PATH = HERE / "nail_prompt_uniform_styled.txt"
DEFAULT_ENV_PATH = HERE / ".env"


def _slug(text: str) -> str:
    text = re.sub(r"[^\w一-鿿]+", "_", text).strip("_")
    return text or "x"


def load_categories(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(template: str, style: str) -> str:
    return template.replace("{{STYLE}}", style)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--style-yaml", type=Path, default=DEFAULT_STYLE_YAML)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE_PATH)
    parser.add_argument("--provider", choices=["gemini", "openai"], default="gemini")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--env-file", default=DEFAULT_ENV_PATH)
    parser.add_argument("--random-seed", type=int, default=None,
                         help="seed Python's random module for reproducible item picks")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(args.env_file)

    if args.random_seed is not None:
        random.seed(args.random_seed)

    categories = load_categories(args.style_yaml)
    template = args.template.read_text(encoding="utf-8")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[info] {len(categories)} categories in {args.style_yaml}")

    for i, (category, items) in enumerate(categories.items(), 1):
        if not items:
            continue
        item = random.choice(items)
        style_text = f"{category} - {item}"
        prompt = build_prompt(template, style_text)
        out_path = args.out_dir / f"{i:02d}_{_slug(category)}__{_slug(item)}.png"
        print(f"[{i}/{len(categories)}] {category} -> {item}")
        try:
            if args.provider == "gemini":
                await gemini_client.generate_image(prompt, str(out_path))
            else:
                openai_client.generate_image(prompt, str(out_path))
            print(f"  done -> {out_path}")
        except Exception as e:
            print(f"  FAILED: {e}")

    print(f"[done] -> {args.out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
