#!/usr/bin/env python3
"""nail_style_generate.py -- for each category in nail_style.yaml, pick one
random item and generate an image with that style locked in via
nail_prompt_uniform_styled.txt's {{STYLE}} placeholder.

Pass --category to restrict to one category and generate one image per
*item* in it instead (exhaustive, not a random pick) -- e.g. every item
under "貓眼 / 光澤類" rather than one random item per category.

Unlike nail_prompt_uniform.txt (where the model itself picks a style from a
short in-prompt list), this picks the style ourselves in Python from a much
larger structured catalog (nail_style.yaml, ~30 categories / ~640 items) and
substitutes it into the template before calling the API -- see the module
docstring discussion in nail_style.yaml's commit for why that catalog isn't
just handed to the model as prose.

Usage (run inside the `apicaller` conda env, from the APICaller/ directory):
    python tasks/AINail/tools/nail_style_generate.py --provider gemini --out-dir out/style_test
"""

import argparse
import asyncio
import random
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
APICALLER_ROOT = HERE.parent.parent
sys.path.insert(0, str(APICALLER_ROOT))
from api_caller import gemini_client, openai_client

DEFAULT_STYLE_YAML = HERE / "nail_style.yaml"
DEFAULT_TEMPLATE_PATH = HERE / "prompts/nail_prompt_uniform_styled.txt"
DEFAULT_ENV_PATH = APICALLER_ROOT / ".env"


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
    parser.add_argument("--category", default=None,
                         help="restrict to this one category (must match a nail_style.yaml key "
                              "exactly) and generate one image per item in it, instead of one "
                              "random item per category")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(args.env_file)

    if args.random_seed is not None:
        random.seed(args.random_seed)

    categories = load_categories(args.style_yaml)
    template = args.template.read_text(encoding="utf-8")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.category:
        if args.category not in categories:
            raise SystemExit(
                f"no such category {args.category!r} in {args.style_yaml}. "
                f"Available: {', '.join(categories)}"
            )
        jobs = [(args.category, item) for item in categories[args.category]]
        print(f"[info] category {args.category!r}: {len(jobs)} item(s)")
    else:
        jobs = [(category, random.choice(items)) for category, items in categories.items() if items]
        print(f"[info] {len(jobs)} categories in {args.style_yaml}, one random item each")

    for i, (category, item) in enumerate(jobs, 1):
        style_text = f"{category} - {item}"
        prompt = build_prompt(template, style_text)
        out_path = args.out_dir / f"{i:02d}_{_slug(category)}__{_slug(item)}.png"
        print(f"[{i}/{len(jobs)}] {category} -> {item}")
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
