"""The ARB files the Localization Hub translates, and the key map behind them.

The code keys its text by the Chinese; the Hub, like Perfect Corp's other
projects, keys it by English ids. i18n/arb-keys.json holds that map: one id
per Chinese key (two or more when the catalogs give one Chinese different
English), kept once given so the Hub keeps its translations. Text with no
letters (、 。 {label}： …) is not sent: the Hub would copy it as it is, so
each language writes it by hand.

  intl_en.arb       the source the Hub translates, with descriptions
  intl_zh_Hant.arb  the Chinese, for reference

The key map is tracked (scripts/translation_source.py refreshes it); the ARB
files are made when they are sent (scripts/l10n_doc.py push), not kept here.
"""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEYS = ROOT / "i18n" / "arb-keys.json"
SOURCE_LOCALE = "en"
CHINESE_LOCALE = "zh_Hant"

SURFACE = {"panel": "panel", "chat": "chat", "backend labels": "backend", "backend text": "backend"}
KEYS_ABOUT = [
    "The English id each Chinese key has in the ARB files of the doc repo. Ids are kept once given: the Hub stores translations by id.",
    "`where` is the catalog the id's text goes back into: panel = frontend/i18n-*.js, chat = frontend-chat/src/i18n/*.ts, backend = photo_agent LABELS / TEXT.",
    "`manual`: text with no letters, written by hand for each language (i18n/locales.json) instead of being translated.",
    "Refreshed by scripts/translation_source.py; do not edit by hand.",
]

_PLURAL_HEAD = re.compile(r"\{(\w+),\s*plural\s*,")
_PLURAL_FORM = re.compile(r"\s*(=\d+|zero|one|two|few|many|other)\s*\{")
_PLURAL_END = re.compile(r"\s*\}")


def plural_at(text, start):
    """(name, {form: text}, end) for an ICU plural starting at text[start], else None."""
    head = _PLURAL_HEAD.match(text, start)
    if not head:
        return None
    forms, j = {}, head.end()
    while form := _PLURAL_FORM.match(text, j):
        k, depth = form.end(), 1
        while k < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            k += 1
        forms[form.group(1)] = text[form.end():k - 1]
        j = k
    end = _PLURAL_END.match(text, j)
    return (head.group(1), forms, end.end()) if end and forms else None


def placeholders(text):
    """{name: "int" | "String"} in order of use; a name a plural counts by is an int."""
    found = {}
    i = 0
    while i < len(text):
        plural = plural_at(text, i)
        if plural:
            name, forms, i = plural
            found[name] = "int"
            for body in forms.values():
                for inner, kind in placeholders(body).items():
                    found.setdefault(inner, kind)
            continue
        simple = re.match(r"\{(\w+)\}", text[i:])
        if simple:
            found.setdefault(simple.group(1), "String")
            i += simple.end()
        else:
            i += 1
    return found


def plain(text):
    """`text` with each plural as its `other` form and the placeholders taken out."""
    out, i = "", 0
    while i < len(text):
        plural = plural_at(text, i)
        if plural:
            out += plain(plural[1].get("other", ""))
            i = plural[2]
        else:
            out += text[i]
            i += 1
    return re.sub(r"\{\w+\}", " ", out)


def is_manual(zh):
    """True for text with no letters outside its placeholders."""
    return not re.search(r"[^\W\d_]", re.sub(r"\{\w+\}", "", zh.split("@@")[0]))


def is_short(english):
    """True for three words or fewer: the English most likely to read two ways alone."""
    return len(re.findall(r"[A-Za-z]+", plain(english))) <= 3


def _slug(english):
    words = re.findall(r"[a-z0-9]+", plain(english).lower().replace("'", "").replace("’", ""))
    return "_".join(words[:5]) or "text"


def _groups(entries):
    """(zh, english, wheres) for every text that goes to the Hub."""
    for zh, entry in entries.items():
        if is_manual(zh):
            continue
        if isinstance(entry["en"], str):
            yield zh, entry["en"], list(entry["where"])
            continue
        by_english = {}
        for where, english in entry["en"].items():
            by_english.setdefault(english, []).append(where)
        for english, wheres in by_english.items():
            yield zh, english, wheres


def _new_id(keys, zh, english, wheres):
    surfaces = {SURFACE[w] for w in wheres}
    base = f"{surfaces.pop() if len(surfaces) == 1 else 'common'}_{_slug(english)}"
    key_id, n = base, 2
    while key_id in keys:
        key_id, n = f"{base}_{n}", n + 1
    keys[key_id] = {"zh": zh, "where": wheres, "en": english}


def assign_ids(entries, old_keys):
    """{id: {"zh", "where", "en"}} for `entries`, reusing the ids `old_keys` gave."""
    by_zh = {}
    for key_id, item in old_keys.items():
        by_zh.setdefault(item["zh"], []).append((key_id, set(item["where"])))
    keys, fresh = {}, []
    for zh, english, wheres in _groups(entries):
        options = [o for o in by_zh.get(zh, []) if o[0] not in keys]
        if options:
            key_id = max(options, key=lambda o: len(o[1] & set(wheres)))[0]
            keys[key_id] = {"zh": zh, "where": wheres, "en": english}
        else:
            fresh.append((zh, english, wheres))
    for group in fresh:
        _new_id(keys, *group)
    return dict(sorted(keys.items()))


def _description(entry, english):
    """What a translator needs beyond the English: "" when it reads clearly alone."""
    def adds(text):
        return text and re.sub(r"\W", "", text.lower()) != re.sub(r"\W", "", english.lower())

    parts = []
    if adds(entry.get("note")):
        parts.append(entry["note"].rstrip("."))
    if adds(entry.get("en_full")):
        parts.append(f"Full meaning: {entry['en_full']}")
    if entry.get("max"):
        parts.append(f"Room on screen: {entry['max']}")
    if re.search(r"</?\w+>", english):
        parts.append("Keep the HTML tags as they are")
    return ". ".join(parts) + "." if parts else ""


def build(entries, old_keys):
    """(arb-keys.json, intl_en.arb, intl_zh_Hant.arb) as dicts."""
    keys = assign_ids(entries, old_keys)
    source = {"@@locale": SOURCE_LOCALE}
    chinese = {"@@locale": CHINESE_LOCALE}
    for key_id, item in keys.items():
        zh, english = item["zh"], item.pop("en")
        meta = {}
        description = _description(entries[zh], english)
        if description:
            meta["description"] = description
        found = placeholders(english)
        if found:
            meta["placeholders"] = {name: {"type": kind} for name, kind in found.items()}
        source[key_id] = english
        if meta:
            source[f"@{key_id}"] = meta
        chinese[key_id] = zh.split("@@")[0]
    manual = {zh: list(entries[zh]["where"]) for zh in entries if is_manual(zh)}
    return {"_about": KEYS_ABOUT, "keys": keys, "manual": manual}, source, chinese


def dump(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _build(entries):
    old_keys = json.loads(KEYS.read_text("utf-8"))["keys"] if KEYS.exists() else {}
    return build(entries, old_keys)


def keys_file(entries):
    """The text of i18n/arb-keys.json for `entries`."""
    return dump(_build(entries)[0])


def arb_files(entries):
    """{file name: text} of the two ARB files `entries` make."""
    _, source, chinese = _build(entries)
    return {f"intl_{SOURCE_LOCALE}.arb": dump(source), f"intl_{CHINESE_LOCALE}.arb": dump(chinese)}
