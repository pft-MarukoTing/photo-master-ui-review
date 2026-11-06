"""Make the catalogs of the languages the Localization Hub translates.

  python scripts/l10n_prepare.py                # the doc repo's latest commit, or PHOTOAGENT_L10N_REF
  python scripts/l10n_prepare.py --arb-dir DIR  # intl_*.arb from a folder instead (fixtures, offline)
  python scripts/l10n_prepare.py --offline      # keep the files made last time, with a warning
  python scripts/l10n_prepare.py --none         # Chinese and English only

Both deploy entries run it first (run_backend.py, scripts/rebuild_and_run_docker.sh).
It takes the enabled languages of i18n/locales.json from the doc repo at one
commit (read with git show: the clone's working tree is left alone) and
writes these gitignored files, each naming that commit:
  frontend/generated/l10n.js              panel, loaded before frontend/i18n.js
  frontend-chat/src/generated/l10n.json   chat, bundled by scripts/build_chat_bundle.sh
  photo_agent/generated/l10n.json         backend, copied into the worker image

A string a language has not translated falls back to English, then Chinese,
and is only counted: new strings never fail it. It fails, writing nothing,
when an enabled language has no ARB file or incomplete metadata, an ARB file
is malformed, or a translation's placeholders or plurals differ from the
current English. Run from PhotoAgent/.
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

import l10n_arb
import l10n_doc

ROOT = l10n_arb.ROOT
LOCALES = ROOT / "i18n" / "locales.json"
SOURCE_JSON = ROOT / "i18n" / "translation-source.json"
OUTPUTS = {
    "panel": ROOT / "frontend/generated/l10n.js",
    "chat": ROOT / "frontend-chat/src/generated/l10n.json",
    "backend": ROOT / "photo_agent/generated/l10n.json",
}
REQUIRED = ("arb_locale", "name", "prompt_name", "format")
# photo_agent/i18n.py SOURCE_FORMAT's fields: (type, length for the tuples).
FORMAT_FIELDS = {"list": (str, None), "sentence": (str, None), "quote": (list, 2), "sentence_end": (str, None),
                 "wish_marks": (list, 3), "name_mark": (str, None), "echo_scale": (int, None)}
WHERES = ("panel", "chat", "backend labels", "backend text")


class Failed(Exception):
    """What stops the catalogs being made, one problem per line."""


def check_language(code, meta):
    problems = [f"{code}: no {field} in i18n/locales.json" for field in REQUIRED if not meta.get(field)]
    fmt = meta.get("format") or {}
    for field, (kind, length) in FORMAT_FIELDS.items():
        value = fmt.get(field)
        if not isinstance(value, kind) or (length and len(value) != length):
            problems.append(f"{code}: format.{field} should be a {kind.__name__}" + (f" of {length}" if length else ""))
    return problems


def balanced(text):
    depth = 0
    for char in text:
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


def plurals(text):
    """[(name, forms)] of every ICU plural in `text`, nested ones included."""
    found, i = [], 0
    while i < len(text):
        plural = l10n_arb.plural_at(text, i)
        if not plural:
            i += 1
            continue
        found.append(plural[:2])
        for body in plural[1].values():
            found += plurals(body)
        i = plural[2]
    return found


def mismatch(reference, text, backend):
    """Why `text` cannot stand in for `reference` in the code, or ""."""
    if not balanced(text):
        return "unbalanced braces"
    want, got = set(l10n_arb.placeholders(reference)), set(l10n_arb.placeholders(text))
    if want != got:
        return f"placeholders {sorted(got)}, the English has {sorted(want)}"
    counted = {name for name, _ in plurals(reference)}
    for name, forms in plurals(text):
        if name not in counted:
            return f"a plural on {{{name}}}, which the English does not count by"
        if "other" not in forms:
            return f"the plural on {{{name}}} has no other form"
    if backend:
        try:
            text.format(**{name: "" for name in got})
        except (KeyError, ValueError, IndexError) as error:
            return f"the backend cannot fill it ({error})"
    return ""


def current_english():
    """(arb-keys.json as the code is now, the English ARB it makes)."""
    entries = json.loads(SOURCE_JSON.read_text("utf-8"))["entries"]
    old_keys = json.loads(l10n_arb.KEYS.read_text("utf-8"))["keys"]
    keys_doc, source, _ = l10n_arb.build(entries, old_keys)
    return keys_doc, source


def translate(code, meta, arb, keys_doc, source, problems):
    """({where: {Chinese: text}}, report line) for one language."""
    per = {where: {} for where in WHERES}
    translated = gone = 0
    for key_id, text in arb.items():
        if key_id.startswith("@"):
            continue
        item = keys_doc["keys"].get(key_id)
        if not item:
            gone += 1
            continue
        if not isinstance(text, str):
            problems.append(f"{code}: {key_id} is not a string")
            continue
        if not text.strip():
            continue
        why = mismatch(source[key_id], text, any(w.startswith("backend") for w in item["where"]))
        if why:
            problems.append(f"{code}: {key_id} has {why}: {text!r}")
            continue
        translated += 1
        for where in item["where"]:
            per[where][item["zh"]] = text
    manual = meta.get("manual") or {}
    for zh, wheres in keys_doc["manual"].items():
        if zh not in manual:
            continue
        why = mismatch(zh.split("@@")[0], manual[zh], any(w.startswith("backend") for w in wheres))
        if why:
            problems.append(f"{code}: manual {zh!r} has {why}")
        for where in wheres:
            per[where][zh] = manual[zh]
    written = len(set(manual) & set(keys_doc["manual"]))
    line = (f"{code} {meta['name']}: {translated}/{len(keys_doc['keys'])} translated, "
            f"{written}/{len(keys_doc['manual'])} manual; the rest show in English")
    if gone:
        line += f"; {gone} ids no longer in the code ignored"
    return {where: dict(sorted(texts.items())) for where, texts in per.items()}, line


def generate(arbs, languages, keys_doc, source, version):
    """({surface: file text}, report lines) for the enabled `languages`, from
    `arbs` ({ARB locale: file text}). Raises Failed."""
    problems, report, made = [], [], {}
    for code, meta in sorted(languages.items()):
        if not meta.get("enabled"):
            continue
        found = check_language(code, meta)
        if found:
            problems += found
            continue
        name = f"intl_{meta['arb_locale']}.arb"
        if meta["arb_locale"] not in arbs:
            problems.append(f"{code}: no {name} in the doc repo")
            continue
        try:
            arb = json.loads(arbs[meta["arb_locale"]])
        except json.JSONDecodeError as error:
            problems.append(f"{code}: {name} is not JSON ({error})")
            continue
        if not isinstance(arb, dict) or arb.get("@@locale", meta["arb_locale"]) != meta["arb_locale"]:
            problems.append(f"{code}: {name} is not an ARB file for {meta['arb_locale']}")
            continue
        made[code], line = translate(code, meta, arb, keys_doc, source, problems)
        report.append(line)
    if problems:
        raise Failed("\n".join(problems))
    return render(made, languages, version), report


def render(made, languages, version):
    names = [{"code": code, "name": languages[code]["name"]} for code in made]
    page = lambda where: {"version": version, "languages": names,
                          "catalogs": {code: texts[where] for code, texts in made.items()}}
    backend = {"version": version, "languages": [
        {"code": code, "name": languages[code]["prompt_name"], "format": languages[code]["format"],
         "labels": texts["backend labels"], "text": texts["backend text"]} for code, texts in made.items()]}
    dump = lambda data: json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    return {
        "panel": "// Made by scripts/l10n_prepare.py; do not edit.\nwindow.PHOTOAGENT_L10N = " + dump(page("panel")).rstrip() + ";\n",
        "chat": dump(page("chat")),
        "backend": dump(backend),
    }


def doc_git(*args):
    return subprocess.run(["git", "-C", str(l10n_doc.DOC), *args], check=True, capture_output=True, text=True).stdout


def fetch(ref):
    """(commit, {ARB locale: file text}) of the doc repo at `ref`, else at its
    default branch's latest commit."""
    try:
        if not (l10n_doc.DOC / ".git").exists():
            subprocess.run(["git", "clone", "--quiet", l10n_doc.REMOTE, str(l10n_doc.DOC)],
                           check=True, capture_output=True, text=True)
        doc_git("fetch", "--quiet", "--prune", "origin")
        if ref:
            wanted = [f"refs/remotes/origin/{ref}", ref]
        else:
            if subprocess.run(["git", "-C", str(l10n_doc.DOC), "rev-parse", "--verify", "--quiet", "refs/remotes/origin/HEAD"],
                              capture_output=True).returncode:
                doc_git("remote", "set-head", "origin", "--auto")
            wanted = ["refs/remotes/origin/HEAD"]
    except subprocess.CalledProcessError as error:
        raise Failed(f"cannot read the doc repo {l10n_doc.REMOTE} into {l10n_doc.DOC}: "
                     f"{(error.stderr or '').strip() or error}")
    for name in wanted:
        try:
            commit = doc_git("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}").strip()
            break
        except subprocess.CalledProcessError:
            continue
    else:
        raise Failed(f"PHOTOAGENT_L10N_REF={ref} is not a commit of the doc repo")
    files = [n for n in doc_git("ls-tree", "--name-only", commit).split() if re.fullmatch(r"intl_\w+\.arb", n)]
    return commit, {n[len("intl_"):-len(".arb")]: doc_git("show", f"{commit}:{n}") for n in files}


def read_dir(folder):
    return {p.name[len("intl_"):-len(".arb")]: p.read_text("utf-8") for p in sorted(folder.glob("intl_*.arb"))}


def warn_if_stale():
    """translation_source.py --check, as a warning: strings it has not picked
    up have no id yet, so other languages show them in English."""
    result = subprocess.run([sys.executable, str(ROOT / "scripts/translation_source.py"), "--check"],
                            capture_output=True, text=True)
    if result.returncode:
        detail = [l for l in result.stdout.splitlines() if l.startswith("out of step")] or [result.stderr.strip()]
        print("l10n: warning: run scripts/translation_source.py and commit its files;", *detail, flush=True)


def write(texts):
    """Writes the files whose text changed; returns them."""
    changed = []
    for surface, text in texts.items():
        path = OUTPUTS[surface]
        if path.exists() and path.read_text("utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        with open(temp, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temp, path)
        changed.append(path)
    return changed


def keep_last():
    missing = [str(p.relative_to(ROOT)) for p in OUTPUTS.values() if not p.exists()]
    if missing:
        sys.exit(f"l10n: --offline, but nothing was made before: {', '.join(missing)}")
    version = json.loads(OUTPUTS["backend"].read_text("utf-8"))["version"]
    print(f"l10n: warning: --offline keeps the catalogs made last time ({version}), not checked against the doc repo")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--arb-dir", type=pathlib.Path, help="read intl_*.arb from this folder, not the doc repo")
    mode.add_argument("--offline", action="store_true", help="keep the files made last time")
    mode.add_argument("--none", action="store_true", help="Chinese and English only")
    args = parser.parse_args()
    if args.offline:
        keep_last()
        return
    languages = json.loads(LOCALES.read_text("utf-8"))["languages"]
    try:
        if args.none:
            version = {"doc_commit": None, "ref": "none"}
            texts, report = generate({}, {}, None, None, version)
        else:
            warn_if_stale()
            keys_doc, source = current_english()
            if args.arb_dir:
                arbs, version = read_dir(args.arb_dir), {"doc_commit": None, "ref": str(args.arb_dir)}
            else:
                ref = os.environ.get("PHOTOAGENT_L10N_REF", "").strip() or None
                commit, arbs = fetch(ref)
                version = {"doc_commit": commit, "ref": ref or "latest"}
            texts, report = generate(arbs, languages, keys_doc, source, version)
    except Failed as error:
        sys.exit("l10n: cannot make the other languages' catalogs; nothing was written.\n  "
                 + str(error).replace("\n", "\n  "))
    changed = write(texts)
    print(f"l10n: doc commit {version['doc_commit'] or '-'} ({version['ref']})", *report, sep="\n  ")
    print(f"l10n: {len(changed)} file(s) updated" if changed else "l10n: catalogs already up to date", flush=True)


if __name__ == "__main__":
    main()
