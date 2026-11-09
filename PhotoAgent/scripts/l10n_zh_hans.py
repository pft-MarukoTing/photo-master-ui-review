"""Simplified Chinese, made from the Traditional Chinese source instead of by the Hub.

  python scripts/l10n_zh_hans.py "要轉的繁中"   # prints the Simplified

OpenCC tw2sp (characters plus mainland wording, 影片 → 视频) converts the text,
i18n/zh_Hans-overrides.json then replaces the words it leaves Taiwanese or gets
wrong, and 「」『』 become the mainland “” ‘’. scripts/translation_source.py
writes the result to i18n/intl_zh_Hans.arb, which is committed and never sent
to the Hub. Needs `pip install opencc==1.4.2` where that runs; prepare and the
deploy hosts only read the ARB file.
"""

import functools
import json
import sys

import l10n_arb

LOCALE = "zh_Hans"
OPENCC_VERSION = "1.4.2"
OVERRIDES = l10n_arb.ROOT / "i18n" / "zh_Hans-overrides.json"
ARB = l10n_arb.ROOT / "i18n" / f"intl_{LOCALE}.arb"
QUOTES = str.maketrans({"「": "“", "」": "”", "『": "‘", "』": "’"})


def available():
    try:
        import opencc  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=None)
def _converter():
    try:
        import opencc
    except ImportError:
        sys.exit(f"Simplified Chinese needs OpenCC: pip install opencc=={OPENCC_VERSION}")
    return opencc.OpenCC("tw2sp")


@functools.lru_cache(maxsize=None)
def phrases():
    """[(OpenCC's output, the wording we want)], longest first so a longer phrase wins."""
    found = json.loads(OVERRIDES.read_text("utf-8"))["phrases"]
    return sorted(found.items(), key=lambda pair: -len(pair[0]))


def convert(text):
    text = _converter().convert(text)
    for old, new in phrases():
        text = text.replace(old, new)
    return text.translate(QUOTES)


def arb(chinese):
    """intl_zh_Hans.arb as a dict, from intl_zh_Hant.arb's."""
    return {"@@locale": LOCALE, **{key: convert(text) for key, text in chinese.items() if not key.startswith("@")}}


if __name__ == "__main__":
    print(convert(" ".join(sys.argv[1:]) or sys.stdin.read()))
