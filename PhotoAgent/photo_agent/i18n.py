"""The language the user reads: Traditional Chinese (the source), or one of
the languages that has a catalog here (English, i18n_en).

The Chinese text is the source and the key. tr() returns it as it is when the
language is Chinese, so the Chinese pages never go through a catalog, and its
translation from the current language's catalog otherwise (the Chinese again
when the catalog has none).

The language travels with the work: the backend sets it per request from the
page's X-PhotoAgent-Lang header, put_job.enqueue writes it into a job's params
(only when it is not Chinese, so Chinese jobs stay as they were), and the
worker sets it again for that job. Text is written in the language of the
moment it was made: an analysis or a chat line made in Chinese stays Chinese.

Adding a language is adding a catalog module shaped like i18n_en and listing
it in CATALOGS: its CODE and NAME, LABELS (the page's button and panel names,
the same as the chat page's frontend-chat/src/i18n.ts), TEXT (every other
fixed sentence), FORMAT (how a sentence lists items, quotes a button, joins
sentences) and PATTERNS (how the code reads the model's replies in that
language; a pattern a catalog leaves out is simply not looked for).
"""

import contextlib
import contextvars
import re

from photo_agent import i18n_en

SOURCE = "zh-TW"
CATALOGS = {catalog.CODE: catalog for catalog in (i18n_en,)}
LANGS = (SOURCE, *CATALOGS)
HEADER = "X-PhotoAgent-Lang"

# FORMAT for the source language, the shape each catalog's FORMAT has.
SOURCE_FORMAT = {
    # Between items listed in a sentence.
    "list": "、",
    # Between two sentences.
    "sentence": "",
    # Around a button or panel name in a sentence.
    "quote": ("「", "」"),
    # The characters that end a sentence.
    "sentence_end": "。！!？?",
    # 個人化需求: between people, between one person's wishes, and what to
    # strip between a person's name and their wish.
    "wish_marks": ("；", "、", "，、, "),
    # Between a person's name and their wishes when the code writes them out.
    "name_mark": "",
    # How many characters a wish may run to and still be read back in a reply.
    "echo_scale": 1,
}

_LANG = contextvars.ContextVar("photoagent_lang", default=SOURCE)


def normalize(value):
    """One of LANGS for whatever a header or a job param says ("en-US" is en)."""
    if isinstance(value, str) and value.strip():
        wanted = value.strip().lower()
        for code in CATALOGS:
            if wanted == code.lower() or wanted.split("-")[0] == code.lower().split("-")[0]:
                return code
    return SOURCE


def current():
    return _LANG.get()


def is_source():
    """True when the user reads the source language, Chinese."""
    return _LANG.get() == SOURCE


def _catalog():
    return CATALOGS.get(_LANG.get())


def set_current(value):
    """Sets the language for the rest of this context (one request)."""
    _LANG.set(normalize(value))


@contextlib.contextmanager
def use(value):
    token = _LANG.set(normalize(value))
    try:
        yield
    finally:
        _LANG.reset(token)


def tr(text, **values):
    """`text` in the current language, with {name} filled from `values`."""
    catalog = _catalog()
    if catalog:
        text = catalog.TEXT.get(text, text)
    return text.format(**values) if values else text


def label(text):
    """A button or panel name as the user sees it."""
    catalog = _catalog()
    if catalog:
        return catalog.LABELS.get(text, catalog.TEXT.get(text, text))
    return text


def translations(text):
    """`text` and what each catalog makes of it: for a value the page may send
    back in any language (a tapped option), to know it whatever it reads."""
    return {text, *(c.LABELS.get(text, c.TEXT.get(text, text)) for c in CATALOGS.values())}


def translated_leads(template, tail="detail"):
    """Patterns for the start of `template` as each catalog writes it -- the
    part before {tail}, its other placeholders matching anything -- so text
    the code wrote in any language can have that start taken off again.
    The source (Chinese) is left to the code's own patterns."""
    out = []
    for catalog in CATALOGS.values():
        text = catalog.TEXT.get(template)
        if not text or "{" + tail + "}" not in text:
            continue
        lead = text.split("{" + tail + "}")[0]
        parts = re.split(r"\{\w+\}", lead)
        if lead.strip():
            out.append(re.compile(r"^\s*" + r"(.+?)".join(re.escape(p) for p in parts) + r"\s*", re.IGNORECASE))
    return out


def fmt(key):
    """One of SOURCE_FORMAT's settings for the current language."""
    catalog = _catalog()
    return catalog.FORMAT[key] if catalog else SOURCE_FORMAT[key]


def quote(text):
    """A button name quoted the way a sentence in this language quotes it."""
    left, right = fmt("quote")
    return f"{left}{label(text)}{right}"


def join(items):
    """Items listed in a sentence."""
    return fmt("list").join(items)


def sentences(*parts):
    """Sentences one after another, the empty ones left out."""
    return fmt("sentence").join(p for p in parts if p)


def pattern(key, default=None):
    """The current language's PATTERNS[key]; `default` (the Chinese one) in
    Chinese, and None when the catalog has no such pattern."""
    catalog = _catalog()
    return catalog.PATTERNS.get(key) if catalog else default


def patterns_everywhere(key):
    """PATTERNS[key] of every catalog that has one: for a line written in
    whatever language it was written in (an earlier turn)."""
    return [c.PATTERNS[key] for c in CATALOGS.values() if key in c.PATTERNS]


def language_name():
    """What a prompt calls the language its user-visible text is written in."""
    catalog = _catalog()
    return catalog.NAME if catalog else "Traditional Chinese (Taiwan)"


def _names():
    left, right = fmt("quote")
    return "; ".join(f"{zh} = {left}{name}{right}" for zh, name in _catalog().LABELS.items())


def language_note():
    """Added at the end of a chat prompt whose rules quote Chinese, when the
    user reads another language; "" in Chinese."""
    if is_source():
        return ""
    name = language_name()
    left, right = fmt("quote")
    return ("LANGUAGE\n"
            f"The user reads {name}. Write every user-visible string in plain everyday {name}, "
            "whatever language ANALYSIS, PLAN or the rules above are written in. The Chinese sentences "
            "quoted in the rules are examples of what to say and how it should sound: say the same "
            f"thing in natural {name}, never the Chinese. When a rule quotes a button or panel name "
            f"in 「」, the user sees it in {name}, so quote that name in {name}, as {left}…{right}. "
            f"The names: {_names()}. Fields that take a fixed value from a list (an enum "
            "in the schema) keep that value exactly as the schema lists it.")


def localized_prompt(prompt):
    """A chat prompt for the user's language. The prompts are written for
    Chinese replies; in another language their language rule names that
    language, and language_note() after them says how to read the Chinese
    they quote."""
    if is_source():
        return prompt
    prompt = prompt.replace("plain everyday words a Taiwanese user would say", "plain everyday words")
    return prompt.replace("Traditional Chinese (Taiwan)", language_name()) + "\n\n" + language_note()


# What the analysis-side prompts (the photo analysis, its reframing pass, the
# portrait checks, the output QA) add for a user who does not read Chinese.
# Their field descriptions and examples stay Chinese; the values the code
# branches on stay Chinese too, so the analysis is read the same way.
ANALYSIS_NOTE = """LANGUAGE
The user reads {name}. Write every user-visible free-text value in natural {name}, even where the field descriptions or examples above are written in Chinese. That includes every retouch_recipe item (the user-facing version of correction_plan: write it in {name} even when you write correction_plan in another language; an alignment item starts "Straighten:" instead of 「水平／透視校正：」), photo_summary, main_subject, detected_issues, subjects[].label and every reason. Exceptions -- these are fixed values the program reads, so write them exactly as listed, in Chinese: subject_category (人物 / 食物 / 產品 / 風景 / 建築 / 寵物 / 其他), scene_category (室內 / 戶外 / 街拍 / 商業 / 旅行 / 日常 / 其他), and 「不適用」 for a field that does not apply. A retouch_recipe item that reframes still starts with the exact prefix 「重新構圖：」, and everything after that prefix is {name} too, never Chinese (e.g. 「重新構圖：Trim the empty left edge so she sits on the right third.」). Keep JSON keys and English enum values (such as should_fix states or framing names) as specified."""


def localize_analysis(prompt):
    """An analysis-side prompt for the user's language: as it is in Chinese;
    otherwise its Traditional Chinese rule names the user's language, and
    ANALYSIS_NOTE follows it."""
    if is_source():
        return prompt
    name = language_name()
    for rule in ("must be", "in", "is", "are"):
        prompt = prompt.replace(f"{rule} Traditional Chinese", f"{rule} {name}")
    return prompt + "\n\n" + ANALYSIS_NOTE.format(name=name)
