"""aiHair's batch runner: HairFeatures' main.py, run on aiHair's hairfx.
Apply one hair effect to a photo, or a folder of them, and save what it
returns: its results, and everything it made on the way, for debugging.

    python batch_main.py --input PATH_OR_URL_OR_DIR --feature NAME [--level N]
        [-o KEY=VALUE ...]
        [--limit N] [--n-images N] [--out DIR] [--size match|auto|WxH]
        [--keep-rendered-size] [--quality low|medium|high|xhigh|max|auto]
        [--model ID] [--align global|local|local_flow|regions_flow] [--no-align]
        [--blend|--no-blend [--blend-grow S]]
        [--no-colour-match] [--colour-match-area hair|whole|hair_inset|render_hair]
        [--workers N] [--overwrite] [--debug] [--dry-run]
    python batch_main.py --list

What aiHair adds to main.py:

  --feature NAME [--level N]
      one of aiHair's nine features (hairfx/features, by name, case-
      insensitive), in place of main.py's --effect, run as the server runs
      it, with main.py's default switches; hairline's --level 1 or 2 is its
      -o level, and its results go in <--out>/hairline_lv<level>/, so the
      two levels' don't overwrite each other. (The sections below are
      HairFeatures' main.py's: they still name its effects, frizzCare_4,
      hairline_simple_lv1 and the rest, which are aiHair's frizzCare,
      hairline and so on; hairfx/features/__init__.py maps them.)
  timing
      each photo's, as the server times a task: generate_sec
      (Feature.process: the OpenAI calls and the effect's steps round them),
      postprocess_sec (Feature.finish: the colour match, the lining up and
      the blend), api_sec (of generate, the wall time an OpenAI call was in
      flight), load_sec and total_sec; in its record.json and run.json
      entry, printed, and one JSON line per photo in <effect dir>/timing.jsonl.
  the hair model
      run in this process (hairfx/hairseg.py), from --seg-model (default
      models/model_input_none_tf115.hdf5, or HAIR_SEG_MODEL); --seg-python is
      kept as another name for it. The face model still runs in
      --face-python (HAIR_FACE_PYTHON).
  .env.openai.enc and .env.default
      read from aiHair's folder before anything else, the environment
      winning, then .env.openai.enc (the OpenAI key, encrypted, kept out of
      the image) over .env.default; OPENAI_API_KEY may be as the server
      takes it (encrypted with aiserver_utils.aescipher.AESCipher) or a
      plain sk-... key.

Each effect is a Feature (hairfx/core.py) in hairfx/features, and decides
for itself whether it is one images.edit call or a pipeline of calls;
--list shows them all, with each one's -o options:

  frizzCare, soft, plump, grayHair, rootRetouch, strayHair
      one call: the photo and the effect's best prompt so far
      (smoke-gptimage2-hairfeatures-curr-best-test.py's).
  frizzCare_1
      one call: frizzCare's prompt reworked to set it apart from soft, as
      1_Frizz care is from 5_Soft: the hair re-combed smooth, kinks and
      uneven waves relaxed, its strands kept crisp and its colour locked.
  rootRetouch_1
      one call: rootRetouch's prompt with the roots recoloured over the
      photo's strands, which stay where they are: the parting's gap, the
      strands over the forehead and a top cut off by the frame as drawn.
      By default (--no-blend keeps it as drawn), pasted back through its
      hair, grown and feathered, lined up region by region: the face
      (FAN's), the hair and the rest each by one translation (OpenCV, in
      --face-python); the face kept the photo's, as fluffy's -o lock_face
      keeps it, but for the hair.
  soften_261008
      one call: smoke_gptimage2_261008.py's soften prompt, as it is there.
  soft_1
      soft reworked to match rst_pub_by_feature's 5_Soft: its render, with
      a reworked prompt, pasted into the photo through the hair's body, so
      the halo of flyaways stays as it is; the photo kept where the render
      moved strands; and the strands softened to 5_Soft's contrast.
      -o source=photo softens the photo itself, for no billed call.
  frizzCare_3
      frizzCare_2's render smoothed along the hair's flow in its body: an
      oriented blur of 4 px along and 4/3 across (-o blur), then the part
      of what it took finer than 1 px put back (-o fine), once (-o gain),
      so the strands and strand groups soften and the finest grain stays;
      debug/<photo>/ keeps each step (base_<i>, added_<i>) and how many
      times over step 2 put the detail back at each pixel (gain_<i>).
  frizzCare_4
      frizzCare_3 with step 2's gain set per pixel: what it puts back
      finer than 2 px (-o fine), over a 16 px window round it (-o
      window), brought to 11% of the hair's mean grey there (-o level),
      that gain times 1.13 (-o scale) within x0.25 and x1.5 (-o
      min_gain, -o max_gain): more where the render is soft, less where
      it is detailed. -o blur_power 1 brings the result's instead, the
      detail step 1's blur left counted in.
  plumpFixHair
      one call: plump with the hair grown outward only, round its outline
      (by about rst_pub_by_feature's 2_Plump's amounts), the hair inside it,
      the face and the background as they are; the render is the result,
      nothing pasted back.
  plumpFixHair_1
      plumpFixHair asked to grow the hair about twice as far round the head,
      and only slightly below the shoulders.
  strayHairWithFace
      gpt-image draws the face as a mask, and the face dilated goes with
      the photo, as a second image, for gpt-image to take the stray hair
      off in (strayHair_use_mask.py's face, _gen_only.py's prompt). With
      --blend, a composite of its own: lined up region by region, the
      face, the hair and the rest each by one translation (OpenCV, in
      --face-python), and pasted through the face it drew, grown and
      feathered, the render's hair colour matched to the photo's near it.
  strayHairWithFace_1, strayHairWithFace_2
      strayHairWithFace with another mask as its second image: _1 only
      the hair in the dilated face (-o hair_source: the in-house hair
      model, or a render painting it green); _2 the face as drawn, less
      its eyebrows, eyes, nose and mouth, which gpt-image draws as a
      mask of their own.
  grayHairTwoStep
      GPT checks the photo for grey hair first; one with grey hair 明確 or
      可能 gets grayHair's render, one grey all over a render of its own
      back to the darker strands' colour, and one with none is skipped.
      By default (--no-blend keeps it as drawn), pasted back as
      rootRetouch_1's are.
  grayHairTwoStep_1
      grayHairTwoStep with every photo that goes on, grey hair 明確 or 可能
      or grey all over, given the render of a head grey all over.
  hairline, hairline_lv1 .. hairline_lv5
      hairline-mask-fill-seg-enhance.py's pipeline: GPT checks the forehead
      and marks the brows, gpt-image paints the hair, and the top of the
      forehead is filled with hair, per level, its new hairline then
      redrawn where pasting the fill back cut it off (-o repair, on by
      default); hairline does every level in -o levels, hairline_lv<k>
      level k alone.
  hairline_simple_lv0, hairline_simple_lv1, hairline_simple_lv1.5, hairline_simple_lv2
      hairline's check of the forehead, then one render with no mask, its
      prompt saying how far in words: lv1 the hairline fuller and a little
      lower, lv2 lower; lv0 lv1 with every word for how far "slightly",
      lv1.5 lv2 asking for about half as much, nothing from the brows down
      to change. Kept as drawn, but lv1's and lv1.5's, by default lined up
      --align regions_flow and pasted into the photo through the head mask.
  hairPart
      one call: single-prompt_HairPart.py's prompt, as
      run_HairPart_samples.py renders it, colour-matched over the whole
      picture as it does; by default (--no-blend keeps it as drawn) lined up
      --align regions_flow and pasted into the photo through the hair_brow
      mask: the hair and the forehead above the brows, the face below them
      kept the photo's.
  fluffy
      one call: single-prompt_Fluffy.py's prompt (-o language en or zh), as
      run_Fluffy_samples.py renders it. By default (--no-blend keeps it as
      drawn), fluffy_composite.py's composite with pipeline_fluffy.py's
      flags: lined up region by region (-o align_regions; OpenCV, in
      --face-python; -o align_regions=false: by one transform),
      the hair's colour matched place by place (-o match_hair), the render's
      hair taken on top of the head, from about the eyes up (-o
      roots_only), only where it reshaped the hair (-o only_changed) and
      the whole cap above the brows (-o whole_cap), the face kept the
      photo's (-o lock_face).

What goes where, <effect dir> being <--out>/<effect> (default output/):

  <photo>_<output>.png           the results, e.g. subject0_result_0.png
  debug/<photo>/<name>.png       with --debug, the effect's intermediates
                                 (renders as drawn, masks, overlays .jpg, the
                                 lining up's and the blend's stages)
  debug/<photo>/<step>_rendered_<i>.png
                                 with --debug, each render as the API sent it,
                                 at its own size, where it was resized to the
                                 photo's (e.g. edit_rendered_0 for result_0)
  debug/<photo>/record.json      the photo's settings, prompts, billed calls
                                 and their cost, their input's apart (each
                                 image sent with its tokens), the effect's
                                 notes, errors
  sheets/<photo>.jpg             BEFORE, key intermediates and the results
  run.json                       every photo saved here, by this run or an
                                 earlier one, and the settings and prompts
                                 each was run with (its runs entry)

Without --debug, debug/<photo>/ holds record.json alone (what a rerun
reads to tell a photo done), and the debug files named below aren't
saved; replay-blend.py, which takes each render as drawn from there,
can't replay a run made so whose results were colour-matched, lined up
or blended.

<photo> is the photo's file name less its extension, unless another photo
of that name has its results here already — from another --input folder,
say, as runs of several folders may share one --out — and then the name
of its folder and its own (e.g. 0_Original_subject0), or failing that
those and a hash of its path; the same photo gets the same <photo> on
every run.

The results of frizzCare(_1/_2/_3/_4), plump, rootRetouch(_1),
hairline_simple_lv0/1/1.5/2, strayHairWithFace(_1/_2), hairPart and
fluffy (not plumpFixHair(_1/_2/_3/_3.7)) first have their colours
pulled back to the photo's — hairPart's over the whole picture, as below
(--colour-match-area whole, its default), the rest's their hair's
pulled back to the photo's (imaging.colour_matched:
run_HairPart_samples.py's and run_Fluffy_samples.py's after_correction,
kept to the hair), as the model
drifts them even where told to keep them: one 3x3 colour matrix and
offset, fitted on the 70% of the photo's and the result's shared hair the
render changed least, in blocks 0.5% of the long side across (the
strands averaged out, as the render's don't fall where the photo's do),
so what the effect changed doesn't steer it, and
taken in their hair only (the hair model's, as for the lining up), so the
face, the lips and the background stay as drawn. For rootRetouch(_1), the
hair the fit leaves out (the recoloured roots, mostly) is shifted as the
kept hair round it is instead, so the matrix doesn't deepen their darks and
lights. The result as drawn is kept as
debug/<photo>/<name>_uncorrected.png; --no-colour-match leaves it so
(--list shows which effects do it). --colour-match-area whole fits and
takes the matrix over the whole picture instead, as run_Fluffy_samples.py's
and run_HairPart_samples.py's _colour_match do (imaging.whole_colour_matched:
every 7th pixel, refitted on the 70% the render changed least), the face,
the lips and the background with the hair; rootRetouch(_1)'s shift of the
hair the fit leaves out isn't taken there. frizzCare_2's, _3's and _4's, and
every effect's with --colour-match-area hair_inset, is fitted in the hair but
taken in the result's hair drawn in from its outline and further from the face's skin
above the eyes (imaging.inset_weight; FAN's landmarks, in --face-python),
so neither the skin round the hair nor that between a fringe's strands
takes it; --colour-match-area hair takes theirs as the rest's.

Every result the model drew whole is then lined up with the photo outside
the hair, as looping-prompt.py lines its renders up before review: the
photo's and the result's hair from the in-house hair model (--seg-model,
run in this process), the render as drawn kept as debug/<photo>/<name>_raw.png
where it moved. hairline's and soft_1's results, pasted into the photo
already, aren't, nor fluffy's, strayHairWithFace's,
rootRetouch_1's and grayHairTwoStep(_1)'s when blended (--blend; fluffy's and
those last two by default), which line them up themselves; nor are any with
--no-align or --keep-rendered-size.
That is one affine transform of the whole render (--align global, every
effect's default but hairline_simple_lv1/1.5's and hairPart's); with --align local it is
fitted outside the face, and inside it each of the brows, eyes, nose,
mouth and chin is matched on its own and moved into place, the face found
by FAN's 68 landmarks (face_alignment, run locally in --face-python;
debug/<photo>/face.jpg); with --align local_flow the face from just above
the brows down is then moved onto the photo by optical flow (OpenCV's DIS,
in --face-python), but round a
brow hair crosses in the photo or the render (there the face as local
lines it up; align's _BROW_TAIL note), the render resampled once, and with
--debug each stage kept in debug/<photo>/: <name>_render_only,
_global, _local, _local_flow and _flow.jpg (how far the flow moved each
pixel, white at 6 px); with --align regions_flow (hairline_simple_lv1/1.5's
and hairPart's default) the one transform is replaced by lining the render up region by
region — the rest (outside the hair and the face) and the face with the
bare forehead each by an affine transform (ECC), the hair by optical flow
faded in from the forehead's edge (region_align's REGIONS_HAIR_FADE note) —
then as local_flow, its first stage <name>_regions. The steps each result
was lined up by are in its record.json's info.aligned_by and run.json
(e.g. "regions + parts + flow (not round the left brow)", "global",
"as drawn").

With --blend, each result is then pasted into the photo through its
effect's blend mask, so it is the photo, pixel for pixel, outside it
(hairfx/blend.py; --list shows each effect's): frizzCare(_2/_3/_4),
plumpFixHair(_1/_2/_3/_3.7) and rootRetouch the photo's and the result's
hair; hairline_simple_lv0/2 and hairPart that and the forehead above the
lower brow (hair_brow); hairline_simple_lv1/1.5 that and the face with its
forehead (head, pasted by Laplacian pyramid);
fluffy, strayHairWithFace, rootRetouch_1 and grayHairTwoStep(_1) by
composites of their own (crown: the raised crown, the face kept; face: the
face it drew; hair_shifted: the hair, the face kept but for it, the renders
lined up region by region). Each result as
it was is kept as debug/<photo>/<name>_unblended.png, the mask as
blend_mask_<name>.png and blend_<name>.jpg. The other effects' results are
left as they are. hairPart's, fluffy's, rootRetouch_1's, grayHairTwoStep(_1)'s and
hairline_simple_lv1/1.5's are pasted so without --blend too, by default;
--no-blend keeps them as drawn.

A photo whose record.json shows it done, that photo with the same
settings and prompts, isn't run again unless --overwrite, so a rerun after a failure
only pays for what is missing. Photos run --workers at a time, and at most
--workers billed calls are in flight; one photo failing doesn't stop the
rest. Billed calls per photo are each effect's (--list); none with
--dry-run, which loads the photos and prints the prompts.

Run it with the python of the hairsmooth-gptimage conda env.
"""

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
# Before hairfx is imported: hairseg and landmarks read their paths
# (HAIR_SEG_MODEL, HAIR_FACE_PYTHON) when they are; the environment wins.
for _env in (".env.default", ".env"):
    load_dotenv(_ROOT / _env)

import config  # noqa: E402
from ai_hair.features import LEVELLED, feature_options  # noqa: E402
from ai_hair.timing import CallTimer, TaskTiming, TimedClient, export_timing  # noqa: E402
from hairfx import blend, core, hairseg, imaging, landmarks  # noqa: E402
from hairfx.features import EFFECTS  # noqa: E402

# Photos, and billed calls, in flight at once.
_DEFAULT_WORKERS = 4
# Photos picked up from an --input folder: the formats images.edit accepts.
# An intermediate named with one of them is saved in that format.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _input_refs(input_arg: str, limit: int | None) -> list[str]:
    """--input as the photos to run. A folder gives its first `limit`
    PNG/WebP/JPEG files (all of them without --limit), by name with numbers
    in numeric order, so subject2 comes before subject10. A file or URL is
    the one photo."""
    folder = Path(input_arg)
    if not folder.is_dir():
        return [input_arg]

    def natural(p: Path):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]

    photos = sorted((p for p in folder.iterdir()
                     if p.is_file() and p.suffix.lower() in _IMAGE_EXTS), key=natural)
    return [str(p) for p in photos[:limit]]


def _print_effects() -> None:
    """--list: every effect, what it does and its options (an effect with
    the same options as one listed before it says so)."""
    shown = {}  # Options class -> the first effect listed with it
    for name, feature in EFFECTS.items():
        print(f"{name}  ({feature.default_n_images} image(s) per {feature.n_images_per} "
              "by default)")
        print(f"    {feature.description}")
        if feature.blend_mask:
            print(f"    --blend mask: {feature.blend_mask}"
                  + ("  (on by default, --no-blend keeps the results as drawn)"
                     if feature.blend_default else ""))
        if feature.align_default != "global":
            print(f"    lined up --align {feature.align_default} by default")
        if feature.colour_match and feature.colour_match_area != "hair":
            print("    colours pulled back to the photo's, --colour-match-area "
                  f"{feature.colour_match_area} (--colour-match-area hair takes it as "
                  "the rest's, --no-colour-match skips it)")
        elif feature.colour_match:
            print("    hair's colours pulled back to the photo's (--no-colour-match skips "
                  "it)")
        if feature.colour_shift:
            print("    the hair the colour match's fit leaves out (what the effect "
                  "recoloured) shifted as the hair round it")
        if not dataclasses.fields(feature.Options):
            continue
        if feature.Options in shown:
            print(f"    options: as {shown[feature.Options]}'s")
            continue
        shown[feature.Options] = name
        for line in core.describe_options(feature):
            print(f"    {line}")


def _rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _photo_id(ref: str) -> str:
    """`ref` as the one photo it is, however it is given: a URL as it is, a
    path made absolute and its case and slashes evened out, so that
    a\\b.jpg, a/b.jpg and the full path are one photo."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", ref, re.IGNORECASE):
        return ref
    return os.path.normcase(os.path.abspath(ref))


def _record(effect_dir: Path, name: str) -> dict | None:
    """debug/<name>/record.json in `effect_dir`, or None where there's none
    or it can't be read."""
    try:
        return json.loads((effect_dir / "debug" / name / "record.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record_photo(record: dict) -> str:
    """The photo a record is of, as _photo_id gives it: its "source" (since
    2026-10-29), or else its "photo" as that run was given it."""
    return record.get("source") or _photo_id(record.get("photo", ""))


def _output_name(effect_dir: Path, ref: str, taken: set[str]) -> str:
    """The name `ref`'s results go under in `effect_dir` (<photo> in the
    module's docstring): its stem, unless another photo's results are
    there under it, or `taken` (this run's other photos' names) has it;
    then its folder's name and its stem, and then those and a hash of its
    path."""
    path, own = Path(ref), _photo_id(ref)
    folder = path.parent.name or "input"
    digest = hashlib.sha1(own.encode("utf-8")).hexdigest()[:8]
    for name in (path.stem, f"{folder}_{path.stem}", f"{folder}_{path.stem}_{digest}"):
        if name in taken:
            continue
        record = _record(effect_dir, name)
        if record is None or _record_photo(record) == own:
            return name
    raise RuntimeError(f"no free name for {ref} in {effect_dir}")


def _done(effect_dir: Path, name: str, ref: str, settings: dict,
          prompts: dict) -> dict | None:
    """The record.json under `name` where it shows `ref` done — that photo,
    no error, the same settings and prompts, and every output on disk —
    else None."""
    record = _record(effect_dir, name)
    if record is None or _record_photo(record) != _photo_id(ref):
        return None
    if (record.get("error") or record.get("settings") != settings
            or record.get("prompts") != prompts):
        return None
    if not all((effect_dir / p).is_file() for p in record.get("outputs", {}).values()):
        return None
    return record


def _sheet(result: core.EffectResult, photo: core.Photo):
    """The photo's review sheet, from result.sheet's rows (by default one:
    the photo and every output); names the effect didn't get to are left
    out. None where no row has anything but the photo."""
    images = {**result.intermediates, **result.outputs}
    rows = result.sheet or [["input", *result.outputs]]
    tiled = []
    for row in rows:
        tiles = [("BEFORE", photo.image) if name == "input" else (Path(name).stem, images[name])
                 for name in row if name == "input" or name in images]
        if any(role != "BEFORE" for role, _ in tiles):
            tiled.append((Path(photo.ref).name, tiles))
    return imaging.sheet(tiled) if tiled else None


def _save(result: core.EffectResult, photo: core.Photo | None, effect_dir: Path,
          stem: str, settings: dict, prompts: dict, debug: bool = True,
          timing: dict | None = None) -> dict:
    """Everything in `result` saved under `effect_dir`, by the photo's name
    there, `stem` (see the module's docstring), its intermediates only with
    `debug` (--debug); returns the photo's record, as written to its
    record.json."""
    debug_dir = effect_dir / "debug" / stem
    debug_dir.mkdir(parents=True, exist_ok=True)
    record = {"effect": result.effect, "photo": result.photo,
              "source": _photo_id(result.photo), "settings": settings,
              "prompts": prompts, "outputs": {}, "intermediates": {}}
    for name, image in result.outputs.items():
        path = effect_dir / f"{stem}_{name}.png"
        image.save(path)
        record["outputs"][name] = _rel(path, effect_dir)
    for name, image in result.intermediates.items() if debug else ():
        path = debug_dir / (name if Path(name).suffix.lower() in _IMAGE_EXTS else f"{name}.png")
        if path.suffix.lower() in (".jpg", ".jpeg"):
            image.convert("RGB").save(path, quality=90)
        else:
            image.save(path)
        record["intermediates"][name] = _rel(path, debug_dir)
    sheet = _sheet(result, photo) if photo is not None else None
    if sheet is not None:
        path = effect_dir / "sheets" / f"{stem}.jpg"
        path.parent.mkdir(exist_ok=True)
        sheet.save(path, quality=90)
        record["sheet"] = _rel(path, effect_dir)
    record.update(calls=[dataclasses.asdict(c) for c in result.calls], cost_usd=result.cost,
                  input_cost_usd=result.input_cost, info=result.info, warnings=result.warnings,
                  skipped=result.skipped, error=result.error, timing=timing)
    # default=str: an effect's info is meant to be JSON-able, but a stray
    # Path or numpy value shouldn't lose the record.
    (debug_dir / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return record


def _timed_run(ctx: core.Context, feature: core.Feature, photo: core.Photo, opts,
               timing: TaskTiming) -> core.EffectResult:
    """Feature.run, as it is, with its two halves timed into `timing` as the
    server times them (ai_hair/pipeline.py): process as generate_sec, finish
    as postprocess_sec, and the OpenAI calls (a TimedClient in a copy of
    `ctx`, its call slots shared with every photo's) as api_sec; and each
    step: the feature's own in process (process_steps) and the
    post-processing's (postprocess_steps: the face, the colour match, the
    lining up, the blend and the hair model inside them, with the algorithm
    each ran; postprocess_breakdown, each kind's own seconds)."""
    timer = CallTimer()
    own = copy.copy(ctx)
    own.client = TimedClient(ctx.client, timer)
    result = core.EffectResult(effect=feature.name, photo=photo.ref)
    try:
        with timing.section("generate"):
            try:
                feature.process(own, photo, opts, result)
            except core.Skip as e:
                result.skipped = str(e)
            except Exception as e:  # noqa: BLE001 — keep what the steps before it made
                where = f"{result.step} failed — " if result.step else ""
                result.error = f"{where}{type(e).__name__}: {e}"
            timing["process_steps"] = core.process_steps(result, time.perf_counter())
        with timing.section("postprocess"):
            feature.finish(own, photo, opts, result)
    finally:
        timing["api_sec"] = round(timer.busy_seconds(), 3)
        timing["api_calls"] = timer.summary()
        steps = result.info.pop("postprocess_steps", [])
        timing["postprocess_steps"] = steps
        timing["postprocess_breakdown"] = core.breakdown(steps)
    return result


def _run_photo(ctx: core.Context, feature: core.Feature, opts, ref: str, effect_dir: Path,
               name: str, settings: dict, prompts: dict, debug: bool) -> dict:
    """The effect on one photo, its results saved under `name` (its
    intermediates with `debug`), timed (_timed_run) into its record and
    <effect dir>/timing.jsonl; returns its record."""
    timing = TaskTiming(photo=ref, name=name, effect=feature.name)
    try:
        with timing.section("load"):
            photo = core.load_photo(ctx.infer, ref)
    except Exception as e:  # noqa: BLE001 — skip this photo, keep the run
        result = core.EffectResult(effect=feature.name, photo=ref,
                                   error=f"loading the photo failed — {type(e).__name__}: {e}")
        return _save(result, None, effect_dir, name, settings, prompts, debug,
                     _closed(timing, result, effect_dir))
    result = _timed_run(ctx, feature, photo, opts, timing)
    return _save(result, photo, effect_dir, name, settings, prompts, debug,
                 _closed(timing, result, effect_dir))


def _closed(timing: TaskTiming, result: core.EffectResult, effect_dir: Path) -> dict:
    """`timing` closed with the photo's outcome, and its line written to
    <effect dir>/timing.jsonl; returns it as a plain dict for record.json."""
    timing["status"] = "error" if result.error else "skipped" if result.skipped else "success"
    timing["n_results"] = len(result.outputs)
    timing["cost_usd"] = round(result.cost, 6)
    record = dict(timing.close())
    try:
        export_timing(record, str(effect_dir / "timing.jsonl"))
    except OSError as e:
        result.warnings.append(f"timing.jsonl not written — {e}")
    return record


# A photo's entry in run.json: these of its record, its name and its run.
_RUN_PHOTO_KEYS = ("photo", "outputs", "sheet", "cost_usd", "skipped", "error", "timing")


def _run_entry(record: dict, name: str) -> dict:
    """A photo's entry in run.json: _RUN_PHOTO_KEYS of its record, its name,
    and how each output was lined up, where it was (info.aligned_by)."""
    by = (record.get("info") or {}).get("aligned_by")
    return {**{key: record.get(key) for key in _RUN_PHOTO_KEYS}, "name": name,
            **({"aligned_by": by} if by else {})}


def _save_run(path: Path, effect: str, input_arg: str, settings: dict, prompts: dict,
              photos: list[dict]) -> None:
    """run.json at `path`, this run's `photos` merged into what is there:
    every photo saved in the effect's folder, by this run or an earlier
    one, each once (by _photo_id, this run's entry replacing an earlier
    one's) and in the order first saved, with "run", its entry in "runs" —
    the settings and prompts it was run with, and the --input folders run
    with them. Read afresh on each save, so another run into the same folder
    loses as little as can be, and written whole to a temporary file first,
    so it is never read half-written. A run.json from before 2026-10-29,
    one run's settings and prompts at its top, is read as that run."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    runs, entries = old.get("runs"), old.get("photos", [])
    if runs is None:
        runs = ([{"inputs": [], "settings": old["settings"], "prompts": old.get("prompts")}]
                if "settings" in old else [])
        entries = [dict(p, run=0) for p in entries] if runs else []
    this = next((i for i, r in enumerate(runs)
                 if r.get("settings") == settings and r.get("prompts") == prompts), None)
    if this is None:
        runs.append({"inputs": [], "settings": settings, "prompts": prompts})
        this = len(runs) - 1
    if input_arg not in runs[this]["inputs"]:
        runs[this]["inputs"].append(input_arg)
    merged = {_photo_id(p["photo"]): p for p in entries if p.get("photo")}
    for p in photos:
        merged[_photo_id(p["photo"])] = dict(p, run=this)
    # Only the runs some photo still points to, renumbered.
    used = sorted({p["run"] for p in merged.values()} | {this})
    renumber = {old_i: new_i for new_i, old_i in enumerate(used)}
    run = {"effect": effect, "runs": [runs[i] for i in used],
           "photos": [dict(p, run=renumber[p["run"]]) for p in merged.values()]}
    run["cost_usd"] = sum(p.get("cost_usd") or 0.0 for p in run["photos"])
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _call_line(call: dict) -> str:
    """One billed call of a record, for the console: its cost, its input's
    with the tokens billed — for an images.edit call, each image it sent
    with its tokens for each image rendered, so a mask's show beside the
    photo's — and its output's."""
    line = call["step"] + (f" ${call['cost']:.4f}" if call["cost"] is not None else "")
    if call.get("input_cost") is None:
        return line
    usage = call["usage"]
    details = usage["input_tokens_details"]
    if "image_tokens" in details:
        tokens = f"text {details['text_tokens']} + image {details['image_tokens']} tokens"
        sent = call.get("sent")
        if sent and sent["renders"]:
            photo, *others = sent["images"]
            tokens += (f"; for {'each of ' if sent['renders'] > 1 else ''}{sent['renders']} "
                       f"image{'s' * (sent['renders'] > 1)} rendered: {photo['name']} "
                       f"{photo['tokens']}" + "".join(
                           f", {s['name']} {s['tokens']} ({s['tokens'] / photo['tokens']:.0%} "
                           f"of {photo['name']}'s)" for s in others))
    else:
        cached = details.get("cached_tokens") or 0
        written = details.get("cache_write_tokens") or 0
        tokens = (f"{usage['input_tokens']} tokens" + (f", {cached} cached" if cached else "")
                  + (f", {written} written to the cache" if written else ""))
    return (f"{line}: input ${call['input_cost']:.4f} ({tokens}), output "
            f"${call['cost'] - call['input_cost']:.4f} ({usage['output_tokens']} tokens)")


def _timing_line(timing: dict) -> str:
    """A photo's timing, for the console."""
    parts = []
    for key in ("load", "generate", "postprocess", "total"):
        if f"{key}_sec" in timing:
            parts.append(f"{key} {timing[f'{key}_sec']:.2f}s"
                         + (f" (api {timing['api_sec']:.2f}s)"
                            if key == "generate" and "api_sec" in timing else ""))
    return ", ".join(parts)


# The post-processing's kinds, in the order the console shows them.
_KINDS = ("face", "hair_masks", "colour_match", "align", "blend")


def _steps_lines(timing: dict) -> list[str]:
    """A photo's steps, for the console: the feature's own, each kind's own
    post-processing time, then each post-processing step, indented by how
    deep it ran, with its algorithm (cut short; record.json and
    timing.jsonl have it whole)."""
    lines = []
    if timing.get("process_steps"):
        lines.append("process:   " + ", ".join(f"{s['step']} {s['sec']:.2f}s"
                                              for s in timing["process_steps"]))
    own = timing.get("postprocess_breakdown") or {}
    if own:
        kinds = [k for k in _KINDS if k in own] + [k for k in own if k not in _KINDS]
        lines.append("postproc:  " + ", ".join(f"{k} {own[k]:.2f}s" for k in kinds)
                     + "  (each kind's own time)")
    for s in timing.get("postprocess_steps") or []:
        algorithm = s.get("algorithm") or ""
        if len(algorithm) > 90:
            algorithm = algorithm[:89] + "…"
        lines.append(f"           {'  ' * s['depth']}{s['step']:<{16 - 2 * s['depth']}} "
                     f"{s.get('sec', 0):7.2f}s  {algorithm}")
    return lines


def _report(record: dict, name: str, k: int, total: int) -> str:
    """One photo's record, its results saved under `name`, for the
    console."""
    lines = [f"\n=== [{k}/{total}] {Path(record['photo']).name} ==="]
    outputs = list(record.get("outputs", {}).values())
    if outputs:
        lines.append(f"outputs:   {len(outputs)}  {outputs[0]}"
                     + (f"  ...  {outputs[-1]}" if len(outputs) > 1 else ""))
    if record.get("intermediates"):
        lines.append(f"debug:     debug/{name}/  "
                     + ", ".join(record["intermediates"]))
    if record.get("sheet"):
        lines.append(f"sheet:     {record['sheet']}")
    face_mask_model = (record.get("info") or {}).get("face_mask_model")
    if face_mask_model:
        mask = (record.get("intermediates") or {}).get("face_mask")
        lines.append(f"face mask: {face_mask_model}"
                     + (f"  debug/{name}/{mask}" if mask else ""))
    calls = record.get("calls", [])
    if calls:
        lines.append("calls:     " + "\n           ".join(map(_call_line, calls)))
        input_cost = record.get("input_cost_usd")
        lines.append(f"           total ${record['cost_usd']:.4f}"
                     + (f", input ${input_cost:.4f} ({input_cost / record['cost_usd']:.0%})"
                        if input_cost else ""))
    timing = record.get("timing") or {}
    if timing:
        lines.append("timing:    " + _timing_line(timing))
        lines += _steps_lines(timing)
    lines += [f"warning:   {w}" for w in record.get("warnings", [])]
    for key in ("skipped", "error"):
        if record.get(key):
            lines.append(f"{key}:{' ' * (10 - len(key))}{record[key]}")
    return "\n".join(lines)


def _openai_api_key() -> str:
    """OPENAI_API_KEY: a plain sk-... key as it is, else decrypted with
    AESCipher, as the server takes it. ValueError where it is unset or
    can't be decrypted."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise ValueError("OPENAI_API_KEY is unset. Put it in .env.default or .env, or export it.")
    if key.startswith("sk-"):
        return key
    from aiserver_utils.aescipher import AESCipher

    try:
        return AESCipher().decrypt(key)
    except Exception as e:  # noqa: BLE001 — said plainly instead
        raise ValueError("OPENAI_API_KEY is neither a plain sk-... key nor one encrypted with "
                         f"aiserver_utils.aescipher.AESCipher ({type(e).__name__})") from e


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", metavar="PATH_OR_URL_OR_DIR",
                        help="the photo to apply the effect to (local path or https URL; "
                             "PNG/WebP/JPEG), or a folder of them")
    parser.add_argument("--feature", metavar="NAME",
                        help="one of aiHair's features, run as the server runs it: "
                             + ", ".join(EFFECTS) + " (case-insensitive)")
    parser.add_argument("--level", type=int, default=None, metavar="N",
                        help="with --feature hairline: 1 (the default; HairFeatures' "
                             "hairline_simple_lv1) or 2 (its hairline_simple_lv1.5); the same "
                             "as -o level=N")
    parser.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE",
                        help="one of the effect's own options (--list shows them); "
                             "repeat for more")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="with a folder --input, only its first N photos by name")
    parser.add_argument("--n-images", type=int, default=None, metavar="N",
                        help=f"images per photo (per level, for hairline), 1–{core.MAX_N_IMAGES}; "
                             "default: the effect's own (--list)")
    parser.add_argument("--out", default=None, metavar="DIR",
                        help="output folder; each effect's go in <DIR>/<effect>/ "
                             "(default: output/)")
    parser.add_argument("--size", default="match",
                        help="render size: 'match' (the photo's aspect, ~1.5 MP), 'auto', or "
                             "WIDTHxHEIGHT (multiples of 16). Renders are resized back to the "
                             "photo's size, unless --keep-rendered-size; one shrunk is blurred "
                             "first, more the more it shrinks, so fine strands don't alias.")
    parser.add_argument("--keep-rendered-size", action="store_true",
                        help="keep each result at the size the model rendered it, instead of "
                             "the photo's (not hairline's, which are pasted into the photo); "
                             "such results aren't lined up")
    parser.add_argument("--no-align", action="store_true",
                        help="keep each result as the model drew it, instead of lined up with "
                             "the photo outside the hair")
    parser.add_argument("--align", default=None,
                        choices=("global", "local", "local_flow", "regions_flow"),
                        help="how results are lined up: one transform of the whole render "
                             "(global), or that fitted outside the face and the face's brows, "
                             "eyes, nose, mouth and chin each moved into place (local; the "
                             "face model runs in --face-python), then the face from just "
                             "above the brows down by optical flow (local_flow; OpenCV, in "
                             "--face-python), or with the one transform replaced by the "
                             "rest and the face each by an affine and the hair by optical "
                             "flow (regions_flow). Default: the effect's own, regions_flow "
                             "for hairline_simple_lv1/1.5, global for the rest")
    parser.add_argument("--blend", action=argparse.BooleanOptionalAction, default=None,
                        help="paste each result into the photo through its effect's blend mask "
                             "(--list), the photo kept outside it; effects with none are left "
                             "as they are. Default: the effect's own, on for rootRetouch_1, "
                             "grayHairTwoStep(_1) and hairline_simple_lv1/1.5, off for the "
                             "rest; "
                             "--no-blend keeps every result as drawn")
    parser.add_argument("--blend-grow", type=float, default=blend.GROW, metavar="S",
                        help="with --blend, how far the mask is grown past the hair (and the "
                             "rest), as a share of the photo's long side; the halo of frizz "
                             f"round the hair reaches past the default (default {blend.GROW})")
    parser.add_argument("--no-colour-match", action="store_true",
                        help="keep each result's colours as the model drew them, instead of "
                             "pulled back to the photo's (the effects that do it: --list)")
    parser.add_argument("--colour-match-area", default=None,
                        choices=("hair", "whole", "hair_inset", "render_hair"),
                        help="where the colour match's matrix is fitted and taken: the hair; "
                             "the whole picture, as run_Fluffy_samples.py's and "
                             "run_HairPart_samples.py's _colour_match (no colour shift "
                             "there); or hair_inset, fitted in the hair and taken in the "
                             "result's hair drawn in from its outline and further from the "
                             "face's skin "
                             "(the face model runs in --face-python); or render_hair, "
                             "fitted as the hair, taken in the result's hair only, not "
                             "the photo's it took off; default: the effect's own, the "
                             "hair but frizzCare_2's, _3's and _4's hair_inset and "
                             "strayHair's render_hair (--list)")
    parser.add_argument("--face-python", default=str(landmarks.FACE_PYTHON), metavar="PATH",
                        help="the python the face model (face_alignment, torch) and the "
                             "OpenCV steps run in: this one (the default, HAIR_FACE_PYTHON "
                             "unset) runs the model in this process, loaded once; another "
                             f"runs it there each call (default: {landmarks.FACE_PYTHON})")
    parser.add_argument("--seg-model", "--seg-python", dest="seg_python",
                        default=str(hairseg.SEG_MODEL), metavar="PATH",
                        help="the in-house hair model (.hdf5), run in this process, for the "
                             "colour match, lining results up, the blends and hairline's "
                             f"-o hair_source=model (default: {hairseg.SEG_MODEL})")
    default_model = config.AIHAIR_RENDER_MODEL or core.DEFAULT_MODEL
    default_quality = config.AIHAIR_RENDER_QUALITY or core.DEFAULT_QUALITY
    parser.add_argument("--quality", default=default_quality, choices=core.QUALITIES,
                        help=f"render quality (default {default_quality}, the server's: "
                             "AIHAIR_RENDER_QUALITY, else hairfx's)")
    parser.add_argument("--model", default=default_model,
                        help=f"the render model (default {default_model}, the server's: "
                             "AIHAIR_RENDER_MODEL, else hairfx's)")
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS, metavar="N",
                        help=f"photos, and billed calls, in flight at once "
                             f"(default {_DEFAULT_WORKERS})")
    parser.add_argument("--overwrite", action="store_true",
                        help="run every photo again, even one done with these settings")
    parser.add_argument("--debug", action="store_true",
                        help="save each effect's intermediates (renders as drawn, each "
                             "at the API's size too, masks, "
                             "overlays, the lining up's and the blend's stages) into "
                             "debug/<photo>/; without it only record.json goes there, and "
                             "replay-blend.py can't replay the run")
    parser.add_argument("--dry-run", action="store_true",
                        help="load the photos and print the prompts; no API call")
    parser.add_argument("--list", action="store_true",
                        help="list the effects and their options, and exit")
    args = parser.parse_args()
    if args.list:
        _print_effects()
        return 0
    if not args.input or not args.feature:
        parser.error("--input and --feature are required (or --list)")
    # hairfx's own names (EFFECTS: the nine), matched ignoring case.
    name = {n.lower(): n for n in EFFECTS}.get(args.feature.strip().lower())
    if name is None:
        parser.error(f"--feature must be one of {', '.join(EFFECTS)}; got {args.feature!r}")
    if args.level is not None:
        if args.level not in LEVELLED.get(name, ()):
            parser.error(f"--level is for {', '.join(LEVELLED)} only, "
                         + ", ".join(f"{k}: {', '.join(map(str, v))}"
                                     for k, v in LEVELLED.items())
                         + f"; got {args.level} for {name}")
        if any(o.partition("=")[0].strip() == "level" for o in args.option):
            parser.error("give the level once: --level or -o level, not both")
        args.option = feature_options(name, args.level) + args.option
    args.effect = name
    if args.n_images is not None and not 1 <= args.n_images <= core.MAX_N_IMAGES:
        parser.error(f"--n-images must be 1–{core.MAX_N_IMAGES}; got {args.n_images}")
    for flag in ("limit", "workers"):
        value = getattr(args, flag)
        if value is not None and value < 1:
            parser.error(f"--{flag} must be at least 1; got {value}")
    feature = EFFECTS[args.effect]
    try:
        opts = core.parse_options(feature, args.option)
    except ValueError as e:
        parser.error(str(e))

    inputs = _input_refs(args.input, args.limit)
    if not inputs:
        print(f"no PNG/WebP/JPEG photos in {args.input}")
        return 2
    # A levelled feature's results by level (hairline_lv1, hairline_lv2), so a
    # run of one level doesn't take the other's records for its own.
    run_name = args.effect + (f"_lv{opts.level}" if args.effect in LEVELLED else "")
    effect_dir = (Path(args.out) if args.out else _ROOT / "output") / run_name
    # Each photo's name in effect_dir, its stem unless that is another
    # photo's there or in this run (a.jpg, a.png): _output_name.
    names = {}
    for ref in inputs:
        names[ref] = _output_name(effect_dir, ref, set(names.values()))
    renamed = [(ref, n) for ref, n in names.items() if n != Path(ref).stem]
    render = core.RenderSettings(model=args.model, quality=args.quality, size=args.size,
                                 n_images=args.n_images,
                                 keep_rendered_size=args.keep_rendered_size)
    # The client and infer come in once the dry run is past.
    ctx = core.Context(None, None, render, workers=args.workers, align=not args.no_align,
                       seg_python=args.seg_python, align_mode=args.align,
                       blend=args.blend, blend_grow=args.blend_grow,
                       face_python=args.face_python, colour_match=not args.no_colour_match,
                       colour_match_area=args.colour_match_area)
    aligns, blends = feature.aligns(ctx, opts), feature.blends(ctx, opts)
    how = feature.align_how(ctx) if aligns else None
    colours = feature.colour_matches(ctx, opts)
    colour_area = feature.colour_area(ctx)
    if aligns and not args.dry_run and not Path(args.seg_python).is_file():
        parser.error(f"lining the results up needs the hair model (--seg-model), which isn't "
                     f"there: {args.seg_python}; give it, or --no-align")
    if blends and not args.dry_run and not Path(args.seg_python).is_file():
        parser.error(f"the blend needs the hair model (--seg-model), which isn't there: "
                     f"{args.seg_python}; give it, or --no-blend")
    needs_face = ((how in ("local", "local_flow", "regions_flow"))
                  or (blends and feature.blend_face(opts))
                  or (colours and colour_area == "hair_inset"))
    face_ok, face_why = landmarks.available(args.face_python)
    if needs_face and not args.dry_run and not face_ok:
        parser.error(f"--align other than global, the hair_brow and head blends and the "
                     f"hair_inset colour match need the face model: {face_why}")
    prompts = feature.prompts(opts)
    # What a photo's results depend on, kept in its record.json: a photo is
    # run again where any of it changes.
    # --align other than global and --blend only where on, so a run without
    # them matches the records of runs from before they were added.
    settings = {**dataclasses.asdict(render), "n_images": feature.n_images(render),
                "align": how if how in ("local", "local_flow", "regions_flow") else aligns,
                "options": dataclasses.asdict(opts)}
    if blends:
        settings["blend"] = feature.blend_mask
        if args.blend_grow != blend.GROW and feature.blend_mask in blend.GROWN:
            settings["blend_grow"] = args.blend_grow
    if colours:
        # The area only where it isn't the hair, so a hair run matches the
        # records before.
        settings["colour_match"] = True if colour_area == "hair" else colour_area

    print(f"effect:    {args.effect}  ({feature.description})")
    defaults = dataclasses.asdict(feature.Options())
    changed = {k: v for k, v in settings["options"].items() if v != defaults[k]}
    if changed:
        print("options:   " + "  ".join(f"{k}={v}" for k, v in changed.items())
              + "  (the rest at their defaults, --list)")
    print(f"render:    {args.model}  quality {args.quality}  size {args.size}  "
          f"{settings['n_images']} image(s) per {feature.n_images_per}"
          + ("  kept at the rendered size" if args.keep_rendered_size else ""))
    if feature.colour_match:
        print("colour:    " + ("results' colours pulled back to the photo's over the whole "
                               "picture (one 3x3 colour matrix, fitted on the 70% of its "
                               "pixels the render changed least, as run_Fluffy_samples.py's "
                               "_colour_match)"
                               if colours and colour_area == "whole" else
                               "results' hair's colours pulled back to the photo's (one 3x3 "
                               "colour matrix, fitted on the hair where the render changed it "
                               "least, taken in it drawn in from its outline and the face's "
                               f"skin; hair model {args.seg_python}, face model in "
                               f"{args.face_python})"
                               if colours and colour_area == "hair_inset" else
                               "results' hair's colours pulled back to the photo's (one 3x3 "
                               "colour matrix, fitted on the hair where the render changed it "
                               "least" + ("; the rest shifted as the hair round it"
                                          if feature.colour_shift else "")
                               + f"; hair model {args.seg_python})" if colours else
                               "off (--no-colour-match)" if args.no_colour_match else
                               "off (--keep-rendered-size)"))
    own = " (the effect's default)" if args.align is None else ""
    print("align:     " + ("results lined up with the photo region by region (the rest and "
                           "the face by affine transforms, the hair by optical flow faded in "
                           "from the forehead), then the "
                           "face part by part and by optical flow from just above the brows "
                           f"down (hair model {args.seg_python}, face model and OpenCV in "
                           f"{args.face_python}){own}" if how == "regions_flow" else
                           "results lined up with the photo outside the hair, the face part "
                           "by part, then by optical flow from just above the brows down "
                           f"(hair model {args.seg_python}, face model and OpenCV in "
                           f"{args.face_python}){own}" if how == "local_flow" else
                           "results lined up with the photo outside the hair, the face part "
                           f"by part (hair model {args.seg_python}, face model in "
                           f"{args.face_python})" if how == "local" else
                           "results lined up with the photo outside the hair (hair model "
                           f"{args.seg_python})" if aligns else
                           "off (--no-align)" if args.no_align else
                           "off (--keep-rendered-size)" if args.keep_rendered_size else
                           "not needed: the effect pastes its renders into the photo"))
    if args.blend is not None or feature.blend_default:
        print("blend:     " + (("results pasted into the photo through the head mask: the "
                                "hair and the face with its forehead, grown and feathered, by "
                                "Laplacian pyramid"
                                if feature.blend_mask == "head" else
                                f"results pasted into the photo through the {feature.blend_mask} "
                                f"mask, grown by {args.blend_grow} of the long side"
                                if feature.blend_mask in blend.MASKS else
                                f"results pasted into the photo by {args.effect}'s own "
                                f"{feature.blend_mask} composite")
                               + (" (the effect's default; --no-blend keeps them as drawn)"
                                  if args.blend is None else "") if blends else
                               "off (--no-blend)" if args.blend is False else
                               "off (--keep-rendered-size)" if feature.blend_mask else
                               f"off: {args.effect} has no blend mask"))
    print(f"photos:    {len(inputs)}, {args.workers} at a time")
    print(f"out:       {effect_dir}")
    print("debug:     " + ("intermediates saved into debug/<photo>/" if args.debug else
                           "record.json alone in debug/<photo>/ (--debug saves the "
                           "intermediates)"))
    if renamed:
        print(f"names:     {len(renamed)} photo(s) saved under another name, as another photo "
              "of their name is there: " + ", ".join(f"{Path(r).name} -> {n}"
                                                    for r, n in renamed))
    models = [args.model] + [v for k, v in settings["options"].items() if k == "analyst_model"]
    for model in models:
        if model not in core.IMAGE_PRICES_PER_M and model not in core.ANALYST_PRICES_PER_M:
            print(f"warning: no price table for {model!r}; its cost is left out of the totals")

    import infer  # bundle/infer.py (on sys.path via hairfx.core): photo checks, response parsing

    if args.dry_run:
        for ref in inputs:
            try:
                width, height = core.load_photo(infer, ref).size
                print(f"  {ref}  [{width}x{height}]")
            except Exception as e:  # noqa: BLE001 — list the rest
                print(f"  {ref}  can't load: {type(e).__name__}: {e}")
        for step, prompt in prompts.items():
            print(f"\n=== PROMPT, {step} (dry run, not sent) ===")
            print(prompt)
        return 0

    try:
        api_key = _openai_api_key()
    except ValueError as e:
        print(e)
        return 2
    from openai import OpenAI

    effect_dir.mkdir(parents=True, exist_ok=True)
    # max_retries=0: a slow call fails at the timeout instead of silently
    # re-billing through SDK auto-retries (same policy as infer.py).
    timeout_s = float(config.AIHAIR_OPENAI_TIMEOUT_S or infer._DEFAULT_TIMEOUT_S)
    ctx.client = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)
    ctx.infer = infer
    # The hair model loaded once before the photos, as the server loads it at
    # start, so no photo's timing carries it. Failing here is only a warning:
    # an effect that needs it fails with the reason itself.
    if Path(args.seg_python).is_file():
        try:
            started = time.perf_counter()
            hairseg.preload(args.seg_python)
            print(f"hair model: loaded in {time.perf_counter() - started:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"warning: the hair model didn't load — {type(e).__name__}: {e}")
    # The face model too, where this effect needs it and it runs in this
    # process (hairfx/landmarks.py).
    if needs_face:
        try:
            started = time.perf_counter()
            if landmarks.preload(args.face_python):
                print(f"face model: loaded in {time.perf_counter() - started:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"warning: the face model didn't load — {type(e).__name__}: {e}")

    records, todo = {}, []
    for ref in inputs:
        done = (None if args.overwrite
                else _done(effect_dir, names[ref], ref, settings, prompts))
        if done is None:
            todo.append(ref)
        else:
            records[ref] = done
    if records:
        print(f"\n{len(records)} photo(s) done with these settings already, so not run again "
              "(--overwrite to redo): " + ", ".join(Path(r).name for r in records))

    def save_run() -> None:
        # In --input's order, whatever order they finished in, merged into
        # the photos earlier runs saved here.
        _save_run(effect_dir / "run.json", args.effect, args.input, settings, prompts,
                  [_run_entry(records[ref], names[ref]) for ref in inputs if ref in records])

    lock = threading.Lock()
    run_cost = run_input_cost = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_photo, ctx, feature, opts, ref, effect_dir, names[ref],
                               settings, prompts, args.debug): ref for ref in todo}
        for k, future in enumerate(as_completed(futures), 1):
            ref = futures[future]
            try:
                record = future.result()
            except Exception as e:  # noqa: BLE001 — saving failed; keep the run
                record = {"photo": ref, "cost_usd": None,
                          "error": f"saving failed — {type(e).__name__}: {e}"}
            records[ref] = record
            run_cost += record.get("cost_usd") or 0.0
            run_input_cost += record.get("input_cost_usd") or 0.0
            with lock:
                print(_report(record, names[ref], k, len(todo)), flush=True)
            save_run()
    save_run()

    failed = [r for r in records.values() if r.get("error")]
    skipped = [r for r in records.values() if r.get("skipped")]
    print(f"\n=== {len(inputs)} photo(s): {len(todo)} run, {len(skipped)} skipped by the "
          f"effect, {len(failed)} failed ===")
    for r in failed:
        print(f"  failed   {Path(r['photo']).name}: {r['error']}")
    for r in skipped:
        print(f"  skipped  {Path(r['photo']).name}: {r['skipped']}")
    timed = [r["timing"] for ref in todo if (r := records.get(ref)) and r.get("timing")]
    if timed:
        print("timing this run (per photo, mean / max):  " + ",  ".join(
            f"{key} {sum(t.get(f'{key}_sec', 0) for t in timed) / len(timed):.2f}s / "
            f"{max(t.get(f'{key}_sec', 0) for t in timed):.2f}s"
            for key in ("generate", "api", "postprocess", "total")))
        kinds = {k for t in timed for k in (t.get("postprocess_breakdown") or {})}
        if kinds:
            order = [k for k in _KINDS if k in kinds] + sorted(kinds - set(_KINDS))
            print("postprocess by kind (per photo, mean / max):  " + ",  ".join(
                f"{k} {sum((t.get('postprocess_breakdown') or {}).get(k, 0) for t in timed) / len(timed):.2f}s / "
                f"{max((t.get('postprocess_breakdown') or {}).get(k, 0) for t in timed):.2f}s"
                for k in order))
        print(f"timing log: {(effect_dir / 'timing.jsonl').resolve()}")
    print(f"cost (list price) this run: ${run_cost:.4f}"
          + (f", input ${run_input_cost:.4f} ({run_input_cost / run_cost:.0%})"
             if run_input_cost else ""))
    print(f"saved: {(effect_dir / 'run.json').resolve()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
