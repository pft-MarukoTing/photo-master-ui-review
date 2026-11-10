"""What every hair effect shares: the Feature interface main.py calls, the
photo and run context it gets, the EffectResult it returns, and the billed
calls — gpt-image's images.edit and the analyst's responses.parse — each
recorded in that result with its list-price cost.

An effect is a Feature. main.py loads the photo (load_photo), builds the
effect's Options from its -o KEY=VALUE pairs (parse_options) and calls
Feature.run, which returns an EffectResult: the final images (outputs),
whatever the effect made on the way to them (intermediates: renders, masks,
overlays), every billed call and the effect's own notes (info). main.py
saves all of it. An effect makes one call or a pipeline of them as it
needs, in Feature.process; a step that fails ends it, and what the steps
before it made stays in the result. Then Feature.run pulls each output's
hair's colours back to the photo's, where the effect does that and the
context doesn't say not to (_colour_match_outputs); unless the context says not
to, lines each output up with the photo outside the hair, as
looping-prompt.py lines its renders up (_align_outputs) — "local", the face
lined up part by part as well, "local_flow", then by optical flow, by the
context's align_mode or the effect's own align_default ("regions_flow": the
rest, the face and the hair each their own way first, then as local_flow) —
and, where the
context asks for it and the effect has a blend mask, pastes each output into
the photo through it (_blend_outputs).
"""

import base64
import dataclasses
import io
import math
import os
import sys
import threading
import time
import typing
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import hairseg, landmarks
from .blend import GROW as _BLEND_GROW

ROOT = Path(__file__).resolve().parent.parent
# The bundle dir (infer, _render_errors) is a sibling skill dir, not an
# installable package, so it goes on sys.path, as the smoke scripts put it.
sys.path.insert(0, str(ROOT / "bundle"))

DEFAULT_MODEL = "gpt-image-2.5-sunburst"
DEFAULT_QUALITY = "medium"
QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
MAX_N_IMAGES = 10  # images.edit `n` limit
# The analyst's calls get this timeout instead of the render's: high-effort
# reasoning over a photo at its full size can take minutes.
ANALYST_TIMEOUT_S = 600

# List prices, USD per 1M tokens, for each call's cost. The image models'
# rates match gpt-image-2's; text output isn't billed.
# https://developers.openai.com/api/docs/models/gpt-image-2.5-flare
# https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst
IMAGE_PRICES_PER_M = {
    "gpt-image-2.5-flare": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    "gpt-image-2.5-sunburst": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    "gpt-image-2": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
}
# The analysts'; reasoning tokens bill as output. gpt-5.6-luna's page gives
# no cache write rate, only that writes bill at 1.25x input.
# https://developers.openai.com/api/docs/models/gpt-5.6-luna
# https://developers.openai.com/api/docs/models/gpt-6-luna
# https://developers.openai.com/api/docs/models/gpt-6-sol
ANALYST_PRICES_PER_M = {
    "gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02, "cache_write": 0.25, "output": 1.20},
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
    "gpt-6-sol": {"input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 10.00},
}


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (no python-dotenv dependency): KEY=VALUE lines,
    `#` comments and blanks skipped, existing env wins."""
    if not path.is_file():
        return
    # Explicit utf-8: the .env banner has box-drawing chars, and Windows'
    # locale default (e.g. cp950) can't decode them.
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class Photo:
    """The user's photo as every effect gets it. `file` is it as an
    images.edit file tuple: its bytes as they are on disk, turned upright
    only where its rotation is an EXIF tag, so the model sees it the way the
    user does and the masks an effect makes line up with it. `image` is it
    decoded and upright (PIL): what every render is sized back to."""

    ref: str
    file: tuple[str, bytes, str]
    image: Any

    @property
    def stem(self) -> str:
        return Path(self.ref).stem

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


def load_photo(infer, ref: str) -> Photo:
    """`ref`, a local path or https URL, as a Photo, after infer's boundary
    checks (exists, https-only, real PNG/WebP/JPEG bytes)."""
    from PIL import Image, ImageOps

    name, data, mime = infer._ref_to_openai_file(ref, 0)
    im = Image.open(io.BytesIO(data))
    if im.getexif().get(0x0112, 1) == 1:
        im.load()
        return Photo(ref, (name, data, mime), im)
    upright = ImageOps.exif_transpose(im)
    fmt, ext = ("JPEG", "jpg") if mime == "image/jpeg" else ("PNG", "png")
    buf = io.BytesIO()
    upright.save(buf, format=fmt, quality=95)
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    return Photo(ref, (f"ref_0.{ext}", buf.getvalue(), mime), upright)


def capped_photo(photo: Photo, max_pixels: int | None) -> Photo:
    """`photo` shrunk, its aspect kept, to at most `max_pixels` pixels (area
    averaging, OpenCV's INTER_AREA), its file re-encoded to match (JPEG at
    95 where it was a JPEG, else PNG); as it is where it has no more, or
    `max_pixels` is 0 or None (aiHair: every step's time and memory go with
    the photo's pixels, a 9.8 MP photo's task 2-3x a 4 MP one's, while the
    render is ~1.6 MP whatever the photo)."""
    import math

    import cv2
    import numpy as np
    from PIL import Image

    width, height = photo.size
    if not max_pixels or width * height <= max_pixels:
        return photo
    scale = math.sqrt(max_pixels / (width * height))
    size = (max(1, math.floor(width * scale)), max(1, math.floor(height * scale)))
    image = photo.image
    if image.mode not in ("L", "RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands()
                              or "transparency" in image.info else "RGB")
    small = Image.fromarray(cv2.resize(np.asarray(image), size, interpolation=cv2.INTER_AREA),
                            image.mode)
    fmt, ext = ("JPEG", "jpg") if photo.file[2] == "image/jpeg" else ("PNG", "png")
    if fmt == "JPEG" and small.mode == "RGBA":
        small = small.convert("RGB")
    buf = io.BytesIO()
    small.save(buf, format=fmt, quality=95)
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    return Photo(photo.ref, (f"ref_0.{ext}", buf.getvalue(), mime), small)


@dataclass
class RenderSettings:
    """main.py's render flags, for every images.edit call an effect makes.
    n_images None leaves it to the effect (Feature.default_n_images)."""

    model: str = DEFAULT_MODEL
    quality: str = DEFAULT_QUALITY
    size: str = "match"  # 'match', 'auto' or WIDTHxHEIGHT
    n_images: int | None = None
    keep_rendered_size: bool = False


@dataclass
class Context:
    """What an effect runs with: the OpenAI client (max_retries=0, so a slow
    call fails at the timeout instead of silently re-billing), the bundle's
    infer module (error mapping, response parsing), the render settings, how
    many billed calls may be in flight at once, whether Feature.run lines the
    outputs up with the photo (align) and how (align_mode: "global",
    "local" — the face part by part — "local_flow" — then by optical flow —
    or "regions_flow" — the rest, the face and the hair lined up each their
    own way first, then as local_flow;
    None, local_align's "local" where that is set, else each effect's own:
    Feature.align_default) and
    pastes them into it through the effect's blend mask (blend, the mask
    grown by blend_grow of the long side; None, each effect's own:
    Feature.blend_default), the pythons the hair model and the face model
    run in, and whether it pulls the outputs' hair's colours back to the
    photo's for the effects that do (colour_match), in the hair or over the
    whole picture (colour_match_area: "hair", "whole" or "hair_inset";
    None, each effect's own: Feature.colour_match_area)."""

    client: Any
    infer: Any
    render: RenderSettings
    workers: int = 1
    align: bool = True
    seg_python: str = str(hairseg.SEG_PYTHON)
    local_align: bool = False
    align_mode: str | None = None
    blend: bool | None = None
    blend_grow: float = _BLEND_GROW
    face_python: str = str(landmarks.FACE_PYTHON)
    colour_match: bool = True
    colour_match_area: str | None = None

    def __post_init__(self):
        # Every billed call takes a slot, so at most `workers` are in flight
        # however the effects fan out (hairline renders its levels at once).
        self.slots = threading.Semaphore(self.workers)

    @property
    def analyst(self):
        return self.client.with_options(timeout=ANALYST_TIMEOUT_S)


@dataclass
class Call:
    """One billed call an effect made: its step, model, list-price cost and
    its input's (None where the model has no price table or the API
    reported no usage), the API's usage as reported, and for an images.edit
    call what it sent (_sent): the images it rendered, its input billed for
    each, and each image it sent with its image tokens."""

    step: str
    model: str
    cost: float | None
    usage: dict | None
    input_cost: float | None = None
    sent: dict | None = None


@dataclass
class EffectResult:
    """What Feature.run returns for one photo, for main.py to save.

    outputs        name -> final image (PIL), e.g. "result_0"; main.py saves
                   each as <effect>/<photo>_<name>.png.
    intermediates  name -> an image the effect made on the way (a render, a
                   mask, an overlay), for debugging; saved in
                   <effect>/debug/<photo>/ as <name> where it ends in an
                   image extension (.jpg), else <name>.png.
    sheet          rows of names from the two above, or "input" for the
                   photo, for the photo's review sheet; empty, it is one
                   row: the photo and every output.
    calls          every billed call, in the order made.
    info           anything else worth keeping, JSON-able: an analyst's
                   answers, shares of the photo, alignment notes.
    step           the step running, named by the effect, so that an error
                   says where it happened.
    skipped        why the effect left the photo as it is (Skip), or None.
    error          why it failed, or None; what it made before stays.
    warnings       what is worth a look but didn't stop it.
    debug          whether the intermediates made only to be looked at (the
                   overlays, the masks, the renders as they were) are made
                   (keep): main.py's, which saves them with --debug and puts
                   them in its sheets, are; aiHair's server sets it False,
                   so a task doesn't make or hold them (a 9.8 MP photo's
                   overlays took 0.3-0.9 GB). Those later steps read
                   (face_mask, <name>_unblended) are kept either way.
    """

    effect: str
    photo: str
    outputs: dict[str, Any] = field(default_factory=dict)
    intermediates: dict[str, Any] = field(default_factory=dict)
    sheet: list[list[str]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    step: str | None = None
    skipped: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    debug: bool = True

    def keep(self, name: str, make) -> None:
        """intermediates[name] = make(), where the result keeps what is made
        only to be looked at (debug); make() isn't called where it doesn't."""
        if self.debug:
            self.intermediates[name] = make()

    @property
    def cost(self) -> float:
        """The calls' list-price cost, those with no known cost left out."""
        return sum(c.cost or 0.0 for c in self.calls)

    @property
    def input_cost(self) -> float:
        """The calls' inputs' list-price cost, those with no known cost left
        out."""
        return sum(c.input_cost or 0.0 for c in self.calls)

    def __setattr__(self, name, value):
        # aiHair: each step a feature names (result.step) is marked with the
        # time it began, for process_steps.
        if name == "step" and value is not None:
            self.__dict__.setdefault("_step_marks", []).append((value, time.perf_counter()))
        object.__setattr__(self, name, value)


# ---------------------------------------------------------------------------
# aiHair: what each step took, for the timing records.
#
# process_steps: the feature's own steps (Feature.process), from the marks
# result.step leaves: the render calls and what the feature does round them
# (frizzCare's smoothing, soft's paste), each until the next step began.
# stage: one post-processing step (Feature.finish and the composites), its
# kind, the algorithm it runs and its time, into
# result.info["postprocess_steps"], nested steps (the hair model inside the
# colour match, the lining up inside a composite) a level deeper;
# breakdown: each kind's own time, a step's less the steps inside it.
# ---------------------------------------------------------------------------

def process_steps(result: "EffectResult", end: float) -> list[dict]:
    """The steps result.step named before `end` (time.perf_counter), each
    with the seconds until the next began (the last until `end`)."""
    marks = [m for m in result.__dict__.get("_step_marks", []) if m[1] <= end]
    return [{"step": name, "sec": round((marks[i + 1][1] if i + 1 < len(marks) else end)
                                        - start, 3)}
            for i, (name, start) in enumerate(marks)]


@contextmanager
def stage(result: "EffectResult", kind: str, algorithm: str | None = None,
          name: str | None = None):
    """Time the block as one post-processing step of `kind` ("face",
    "colour_match", "align", "blend", "hair_masks"; a paste is a blend) running
    `algorithm`, into result.info["postprocess_steps"] in the order the steps
    began, a step begun inside another one level deeper. Yields its record,
    for notes on what it did."""
    steps = result.info.setdefault("postprocess_steps", [])
    depth = result.__dict__.get("_stage_depth", 0)
    entry = {"step": name or kind, "kind": kind, "algorithm": algorithm, "depth": depth}
    steps.append(entry)
    result.__dict__["_stage_depth"] = depth + 1
    start = time.perf_counter()
    try:
        yield entry
    finally:
        entry["sec"] = round(time.perf_counter() - start, 3)
        result.__dict__["_stage_depth"] = depth


def breakdown(steps: list[dict] | None) -> dict[str, float]:
    """Each kind's own seconds over `steps` (stage's records): a step's less
    those of the steps one level inside it, so the kinds add up to the
    post-processing's time and the hair model inside the colour match counts
    as the hair model's."""
    steps = steps or []
    own = {}
    for i, step in enumerate(steps):
        inside = 0.0
        for later in steps[i + 1:]:
            if later["depth"] <= step["depth"]:
                break
            if later["depth"] == step["depth"] + 1:
                inside += later.get("sec", 0.0)
        own[step["kind"]] = round(own.get(step["kind"], 0.0)
                                  + max(step.get("sec", 0.0) - inside, 0.0), 3)
    return own


def colour_algorithm(area: str, shift: bool) -> str:
    """What the colour match runs, by its area (Feature.colour_area)."""
    text = {
        "hair": "imaging.colour_matched: one 3x3 colour matrix and offset, fitted on the 70% of "
                "the photo's and the output's shared hair the render changed least (blocks "
                "0.5% of the long side), taken in their hair",
        "whole": "imaging.whole_colour_matched: one 3x3 colour matrix, fitted on every 7th "
                 "pixel then refitted on the 70% the render changed least, taken over the "
                 "whole picture",
        "hair_inset": "imaging.colour_matched: one 3x3 colour matrix fitted in the hair, taken "
                      "in the output's hair drawn in from its outline (0.25 on it) and the "
                      "face's skin (imaging.inset_weight)",
        "render_hair": "imaging.colour_matched: one 3x3 colour matrix fitted in the shared "
                       "hair, taken in the output's hair only, not the photo's the render "
                       "took off",
    }.get(area, area)
    if shift and area != "whole":
        text += "; the hair the fit leaves out shifted as the hair round it (shift_left_out)"
    return text


ALIGN_ALGORITHMS = {
    "global": "align.line_up: one affine transform of the whole render, fitted outside the "
              "photo's and the output's hair",
    "local": "align.line_up: one affine transform outside the hair, then the brows, eyes, "
             "nose, mouth and chin each matched and moved (FAN landmarks)",
    "local_flow": "align.line_up: as local, then the face from just above the brows down by "
                  "optical flow (OpenCV DIS, in the face python)",
    "regions_flow": "align.line_up with region_align: the rest and the face each by an affine "
                    "transform (ECC), the hair by optical flow faded in from the forehead; then "
                    "the face part by part and by optical flow (OpenCV DIS, in the face python)",
}

BLEND_ALGORITHMS = {
    "hair": "blend.weight hair: the photo's and the output's hair, grown by --blend-grow of the "
            "long side and feathered; alpha paste",
    "hair_brow": "blend.weight hair_brow: the hair and the forehead above the lower brow (FAN), "
                 "grown and feathered; alpha paste",
    "hair_face": "blend.weight hair_face: the hair and the feature's face_mask, grown and "
                 "feathered; alpha paste",
    "head": "blend.weight head: the hair and the face with its forehead (FAN), zone-feathered; "
            "Laplacian pyramid paste (common._paste)",
    "hair_shifted": "common.shifted_composite: each render lined up region by region (the face, "
                    "the hair and the rest each by one translation, OpenCV), pasted through the "
                    "hair (the photo's and the render's) by Laplacian pyramid, the face kept "
                    "the photo's but for the hair",
    "face": "common.shifted_composite: each render lined up region by region, pasted through "
            "the face it drew, grown and feathered, its hair's colour matched to the photo's "
            "near it",
    "crown": "fluffy's composite: lined up (common.lined_up), its hair's colour matched place "
             "by place, pasted on top of the head where it reshaped the hair, the face kept "
             "the photo's (by its -o options)",
}


def face_algorithm(ctx: "Context") -> str:
    return ("landmarks.face_landmarks: FAN's 68 landmarks (face_alignment, sfd detector), "
            + ("in this process" if landmarks.in_process(ctx.face_python)
               else f"in {ctx.face_python}"))


def hair_model(ctx: "Context", result: "EffectResult", images: list, what: str) -> list:
    """hairseg.hair_masks on `images`, timed as a hair_masks step."""
    with stage(result, "hair_masks", f"hairseg.hair_masks: the in-house hair model "
                                     f"({Path(ctx.seg_python).name}) on {what}"):
        return hairseg.hair_masks(images, ctx.seg_python)


class Skip(Exception):
    """Raised in Feature.process for a photo the effect leaves as it is —
    hairline's with its forehead covered, say. Not a failure: its message
    goes in the result's `skipped`."""


@dataclass
class NoOptions:
    """The Options of an effect with none of its own."""


def option(default, help: str, choices: tuple | None = None):
    """A field of an effect's Options dataclass: its default, the help
    main.py --list shows, and the values it may take, if only some."""
    return field(default=default, metadata={"help": help, "choices": choices})


class Feature(ABC):
    """One hair effect. main.py calls `run` once per photo; a subclass
    implements `process`, which makes one images.edit call or a pipeline of
    calls as the effect needs and puts what it makes into the result as it
    goes.

    Options is a dataclass of the effect's own settings: option() fields of
    bool, int, float or str, each set with -o KEY=VALUE, which may be
    checked in its __post_init__ (ValueError). default_n_images is how many
    images the effect makes per n_images_per (a photo, or a level for
    hairline) where --n-images isn't given. blend_mask is the mask main.py
    --blend pastes its outputs into the photo through (one of blend.MASKS,
    or the name of a composite of the effect's own, whose finish then does
    it: hairPart's, fluffy's), None for an effect whose outputs aren't
    (each feature's class sets it), and blend_default whether
    they are pasted so where the context doesn't say (Context.blend: main.py
    --blend or --no-blend; each feature's class sets it).
    align_default is how they are lined up where the context doesn't say
    (Context.align_mode: main.py --align; each feature's class sets it).
    colour_match says whether run pulls its outputs' hair's colours back to
    the photo's before anything else (each feature's class
    sets it), colour_match_area where that match's matrix is fitted and
    taken: "hair", "whole" (the whole picture), "hair_inset" (the hair
    drawn in from its outline and the face's skin) or "render_hair" (fitted
    as "hair", taken in the output's hair only: strayHair's, which takes
    hair off),
    unless the context says (Context.colour_match_area), and colour_shift
    whether the hair that match's fit leaves out — what the effect
    recoloured — is shifted as the hair round it instead."""

    description = ""
    Options: type = NoOptions
    default_n_images = 1
    n_images_per = "photo"
    blend_mask: str | None = None
    blend_default = False
    align_default = "global"
    colour_match = False
    colour_match_area = "hair"
    colour_shift = False

    def __init__(self, name: str):
        self.name = name

    def check(self, opts) -> None:
        """Raise ValueError where `opts` don't suit this effect, before any
        call; checks of the Options alone go in their __post_init__."""

    @abstractmethod
    def prompts(self, opts) -> dict[str, str]:
        """Every prompt the effect may send with `opts`, by step: printed on
        a dry run and kept with the results."""

    @abstractmethod
    def process(self, ctx: Context, photo: Photo, opts, result: EffectResult) -> None:
        """Apply the effect to `photo`, putting outputs, intermediates, info
        and sheet rows into `result` as they are made, and naming each step
        in result.step before it runs. Raise Skip to leave the photo as it
        is, or any exception to fail; what is in `result` by then is kept."""

    def n_images(self, render: RenderSettings) -> int:
        return render.n_images or self.default_n_images

    def align_outputs(self, opts) -> bool:
        """Whether `run` lines the outputs up with the photo: yes for renders
        the model drew whole; an effect that pastes its renders into the
        photo itself, lined up already, says no."""
        return True

    def aligns(self, ctx: Context, opts) -> bool:
        """Whether `run` will line this effect's outputs up: the context
        asks for it, the effect's outputs want it, and they are at the
        photo's size (--keep-rendered-size keeps them as rendered)."""
        return ctx.align and self.align_outputs(opts) and not ctx.render.keep_rendered_size

    def align_how(self, ctx: Context) -> str:
        """How `run` lines this effect's outputs up, where it does (aligns):
        the context's align_mode (main.py --align), else "local" where the
        context's local_align asks for it, else the effect's own
        (align_default): "global", "local", "local_flow" or "regions_flow"."""
        return ctx.align_mode or ("local" if ctx.local_align else self.align_default)

    def blends(self, ctx: Context, opts) -> bool:
        """Whether `run` will paste this effect's outputs into the photo: the
        context asks for it (where it doesn't say, the effect's own:
        blend_default), the effect has a blend mask, and they are at the
        photo's size."""
        wanted = self.blend_default if ctx.blend is None else ctx.blend
        return wanted and self.blend_mask is not None and not ctx.render.keep_rendered_size

    def blend_face(self, opts) -> bool:
        """Whether this effect's blend needs the photo's face (its
        landmarks): the hair_brow mask's forehead above the brows, the head
        mask's face."""
        return self.blend_mask in ("hair_brow", "head")

    def colour_matches(self, ctx: Context, opts) -> bool:
        """Whether `run` will pull this effect's outputs' hair's colours
        back to the photo's: the effect does it, the context doesn't say not to
        (main.py --no-colour-match), and they are at the photo's size.
        """
        return ctx.colour_match and self.colour_match and not ctx.render.keep_rendered_size

    def colour_area(self, ctx: Context) -> str:
        """Where `run` fits and takes this effect's colour match: the
        context's colour_match_area (main.py --colour-match-area), else the
        effect's own."""
        return ctx.colour_match_area or self.colour_match_area

    def run(self, ctx: Context, photo: Photo, opts) -> EffectResult:
        """`process` for one photo, its Skip or failure caught into the
        result, so a run goes on to the next photo; then `finish`."""
        result = EffectResult(effect=self.name, photo=photo.ref)
        try:
            self.process(ctx, photo, opts, result)
        except Skip as e:
            result.skipped = str(e)
        except Exception as e:  # noqa: BLE001 — keep what the steps before it made
            where = f"{result.step} failed — " if result.step else ""
            result.error = f"{where}{type(e).__name__}: {e}"
        self.finish(ctx, photo, opts, result)
        return result

    def finish(self, ctx: Context, photo: Photo, opts, result: EffectResult) -> None:
        """The outputs `process` made colour-matched to the photo
        (_colour_match_outputs), where `colour_matches`, lined up with it
        (_align_outputs, with the hair masks the colour match found), where
        `aligns`, and then pasted into it through
        the effect's blend mask (_blend_outputs), where `blends`. The face's
        landmarks are found first where any needs them: the hair_inset
        colour match, the local alignment (align_how "local" or
        "local_flow") and the blend (blend_face).
        replay-blend.py calls it on an earlier run's renders."""
        if not result.outputs:
            return
        colours, area = self.colour_matches(ctx, opts), self.colour_area(ctx)
        aligns, blends = self.aligns(ctx, opts), self.blends(ctx, opts)
        local = aligns and self.align_how(ctx) != "global"
        face = None
        if ((colours and area == "hair_inset") or local
                or (blends and self.blend_face(opts))):
            with stage(result, "face", face_algorithm(ctx)):
                face = _find_face(ctx, photo, result)
        hair = None
        if colours:
            with stage(result, "colour_match", colour_algorithm(area, self.colour_shift)):
                hair = _colour_match_outputs(ctx, photo, result, self.colour_shift, area,
                                             face)
        if aligns:
            how = self.align_how(ctx)
            with stage(result, "align", ALIGN_ALGORITHMS.get(how, how)) as step:
                _align_outputs(ctx, photo, result, face if local else None, hair,
                               flow=how in ("local_flow", "regions_flow"),
                               regions=how == "regions_flow")
                step["aligned_by"] = result.info.get("aligned_by")
        if blends:
            with stage(result, "blend", BLEND_ALGORITHMS.get(self.blend_mask, self.blend_mask)):
                _blend_outputs(ctx, self.blend_mask, photo, result, face)


def _find_face(ctx: Context, photo: Photo, result: EffectResult):
    """The photo's face (landmarks.face_landmarks), drawn into intermediates
    (face.jpg: where the local alignment moves the render on its own, and
    the landmarks), or None with a warning where there is none or the face
    model fails."""
    from . import align, imaging

    try:
        face = landmarks.face_landmarks([photo.image], ctx.face_python)[0]
    except Exception as e:  # noqa: BLE001 — align and blend without it
        result.warnings.append(f"face landmarks failed — {type(e).__name__}: {e}")
        return None
    if face is None:
        result.warnings.append("no face found: the local alignment and the blend go on "
                               "without it")
        return None
    result.info["face"] = {"score": round(face["score"], 4),
                           "bbox": [round(v, 1) for v in face["bbox"]]}

    def overlay():
        weight, _ = align.face_weight(face, photo.size)
        rgb = imaging.tinted(photo.image, weight > 0.5)
        radius = max(2, round(0.003 * max(photo.size)))
        height, width = weight.shape
        for x, y in face["points"]:
            x, y = int(round(x)), int(round(y))
            rgb[max(0, y - radius):min(height, y + radius + 1),
                max(0, x - radius):min(width, x + radius + 1)] = (40, 200, 255)
        return imaging.as_image(rgb)

    result.keep("face.jpg", overlay)
    return face


def _hair_masks(ctx: Context, photo: Photo, result: EffectResult):
    """The photo's hair and each output's (name -> mask), from the in-house
    hair model."""
    names = list(result.outputs)
    photo_hair, *hair = hair_model(ctx, result, [photo.image] + [result.outputs[n] for n in names],
                                   f"the photo and {len(names)} output(s)")
    return photo_hair, dict(zip(names, hair))


def _colour_match_outputs(ctx: Context, photo: Photo, result: EffectResult,
                          shift_left_out: bool = False, area: str = "hair", face=None):
    """Each output's hair's colours pulled back to the photo's
    (imaging.colour_matched: run_HairPart_samples.py's and
    run_Fluffy_samples.py's after_correction, kept to the hair), as gpt-image
    drifts them — more saturated or darker hair — even where told to keep
    them: one colour matrix, fitted on the hair both have where the render
    changed the photo least, so what the effect changed doesn't steer it,
    and taken in the hair either has only, so the face, the lips and the
    background stay as drawn; with `shift_left_out` (rootRetouch's), the
    hair the fit leaves out is shifted as the hair round it instead
    (imaging.colour_matched). With `area` "whole" (Feature.colour_area), the
    samples' own: one matrix fitted and taken over the whole picture
    (imaging.whole_colour_matched), no hair model, `shift_left_out` not
    taken. With `area` "hair_inset" (frizzCare_3's), fitted as in the hair
    but taken in the output's hair drawn in from its outline and, with
    `face` (the photo's, _find_face), further from the face's skin above
    the eyes
    (imaging.inset_weight, face_skin), so neither the skin the hair mask
    takes in round its outline nor that between a fringe's strands takes
    it. The output as drawn is kept as <name>_uncorrected, and how far
    each moved (the mean difference where it took the matrix, 0–255), or
    why it didn't, goes into info["colour_match"]. The hair masks, the
    photo's and each output's (name -> mask), for _align_outputs; None with
    "whole", and where the hair model fails, which is a warning, the outputs
    left as drawn."""
    import numpy as np

    from . import imaging

    try:
        if area == "whole":
            photo_hair = hair = None
            matched = {name: imaging.whole_colour_matched(image, photo.image)
                       for name, image in result.outputs.items()}
        else:
            photo_hair, hair = _hair_masks(ctx, photo, result)
            weights = {name: None for name in hair}
            if area == "hair_inset":
                for name, render_hair in hair.items():
                    either = photo_hair | render_hair
                    skin = (imaging.face_skin(either, face["points"]) if face is not None
                            else None)
                    # The output's hair, not either's: the photo's frizz
                    # the render combed away, background now, took the
                    # hair's matrix (frizzCare's subject10, 2026-11-09).
                    weights[name] = imaging.inset_weight(render_hair, skin)
            elif area == "render_hair":
                # strayHair's: the photo's hair the render took off (strands
                # across the face, skin now) would take the hair's matrix,
                # a pale or dark streak where each strand was.
                for name, render_hair in hair.items():
                    weights[name] = np.clip(2 * imaging.feathered(
                        render_hair, imaging._MATCH_FEATHER * max(render_hair.shape)) - 1, 0, 1)
            matched = {name: imaging.colour_matched(image, photo.image, photo_hair, hair[name],
                                                    shift_left_out, weights[name])
                       for name, image in result.outputs.items()}
    except Exception as e:  # noqa: BLE001 — the renders are paid for; keep them as drawn
        result.warnings.append(f"colour match failed, outputs left as drawn — "
                               f"{type(e).__name__}: {e}")
        return None
    notes = result.info["colour_match"] = {}
    for name, done in matched.items():
        if done is None:
            notes[name] = "too little hair in both the photo and the output: left as drawn"
            continue
        image, weight = done
        drawn = result.outputs[name]
        change = np.abs(np.asarray(image, np.float32)
                        - np.asarray(drawn.convert("RGB"), np.float32)).mean(axis=2)
        notes[name] = round(float(change[weight > 0].mean()), 2)
        result.keep(f"{name}_uncorrected", lambda: drawn)
        result.outputs[name] = image
    return (photo_hair, hair) if hair is not None else None


def _align_outputs(ctx: Context, photo: Photo, result: EffectResult, face=None,
                   hair=None, flow: bool = False, regions: bool = False) -> None:
    """Each output lined up with the photo outside the hair (align.line_up),
    as looping-prompt.py lines each render up with BEFORE outside BEFORE's
    and TARGET's hair: here the photo's hair and the output's own, from the
    in-house hair model (or `hair`, found already: the photo's and each
    output's, name -> mask), so the hair an effect changes or grows isn't
    read as the render sliding; with `face`, the photo's, the face lined up
    part by part as well, and with `flow` then by optical flow (local_flow:
    align.line_up's flow_python, the face model's), with `regions` too lined
    up region by region first (regions_flow: line_up's hairs). A lined-up
    output replaces the render, which is kept as <name>_raw; one better left
    as drawn stays as it is. The steps each was lined up by go into
    info["aligned_by"] (e.g. "regions + parts + flow"). With `flow`, each
    output's stages are kept too, in a sheet row of their own:
    <name>_render_only (as the model drew it, before the colour match),
    <name>_global or, with `regions`, <name>_regions (lined up by the one
    transform or the regions),
    <name>_local (then the face's parts), <name>_local_flow (then the flow:
    the output, before any blend) and <name>_flow.jpg (how far the flow
    moved each pixel, white at _FLOW_SHOWN px and more). The masks go into
    intermediates (align_hair_*) and what was done into info["alignment"].
    Failing is a warning, the outputs left as drawn."""
    from . import align, imaging

    try:
        photo_hair, hair = hair or _hair_masks(ctx, photo, result)
        result.keep("align_hair_input", lambda: imaging.mask_image(photo_hair))
        notes = result.info["alignment"] = {}
        by = result.info["aligned_by"] = {}
        flow_python = ctx.face_python if flow and face is not None else None
        for name, output_hair in hair.items():
            result.keep(f"align_hair_{name}", lambda: imaging.mask_image(output_hair))
            stages = {}
            drawn = result.outputs[name]
            aligned, notes[name] = align.line_up(
                photo.image, drawn, photo_hair | output_hair, face, flow_python, stages,
                (photo_hair, output_hair) if regions and flow_python else None)
            by[name] = (" + ".join(stages["steps"]) if stages.get("steps") else
                        "global" if aligned is not None else "as drawn")
            if aligned is not None:
                result.keep(f"{name}_raw", lambda: drawn)
                result.outputs[name] = aligned
            if flow_python and "global" in stages and result.debug:
                kept = result.intermediates
                first = f"{name}_{'regions' if stages['steps'][0] == 'regions' else 'global'}"
                kept[f"{name}_render_only"] = kept.get(f"{name}_uncorrected", drawn)
                kept[first] = stages["global"]
                kept[f"{name}_local"] = stages["local"]
                kept[f"{name}_local_flow"] = result.outputs[name]
                row = ["input", f"{name}_render_only", first, f"{name}_local",
                       f"{name}_local_flow"]
                if "flow" in stages:
                    kept[f"{name}_flow.jpg"] = imaging.as_image(
                        (stages["flow"] / _FLOW_SHOWN).clip(0, 1) * 255).convert("L")
                    row.append(f"{name}_flow.jpg")
                result.sheet.append(row)
    except Exception as e:  # noqa: BLE001 — the renders are paid for; keep them as drawn
        result.warnings.append(f"align failed, outputs left as drawn — {type(e).__name__}: {e}")


# <name>_flow.jpg's white: how far the flow moved a pixel, px.
_FLOW_SHOWN = 6.0


def _blend_outputs(ctx: Context, kind: str, photo: Photo, result: EffectResult,
                   face=None) -> None:
    """Each output pasted into the photo through blend mask `kind`
    (blend.weight): its hair, from the in-house hair model, run on the
    outputs as lined up, the photo's, and for hair_brow the forehead above
    `face`'s brows, for hair_face the effect's face_mask, for head `face`
    with its forehead (pasted by Laplacian pyramid). The output as it
    was is kept as <name>_unblended, the mask as blend_mask_<name> (how much
    of the output each pixel takes) and blend_<name>.jpg (the photo, red
    where it takes more than half), each in a sheet row of its own. Failing
    is a warning, the outputs left unblended."""
    from . import blend, imaging

    names = list(result.outputs)
    try:
        photo_hair, *hair = hair_model(ctx, result,
                                       [photo.image] + [result.outputs[n] for n in names],
                                       f"the photo and {len(names)} output(s), as lined up")
        face_mask = result.intermediates.get("face_mask")
        face_mask = imaging.region(face_mask) if face_mask is not None else None
        notes = {"mask": kind}
        blended = {}
        for name, output_hair in zip(names, hair):
            weight, lacking = blend.weight(kind, photo_hair, output_hair, face, face_mask,
                                           ctx.blend_grow)
            warning = f"blend {kind}: {lacking}"
            if lacking and warning not in result.warnings:
                result.warnings.append(warning)
            notes[name] = {"share": round(float((weight > 0.5).mean()), 4)}
            blended[name] = (blend.paste(photo.image, result.outputs[name], weight, kind),
                             weight)
    except Exception as e:  # noqa: BLE001 — keep the outputs as they are
        result.warnings.append(f"blend failed, outputs left unblended — {type(e).__name__}: "
                               f"{e}")
        return
    result.info["blend"] = notes
    for name, (image, weight) in blended.items():
        result.intermediates[f"{name}_unblended"] = result.outputs[name]
        result.keep(f"blend_mask_{name}", lambda: imaging.as_image(weight * 255).convert("L"))
        result.keep(f"blend_{name}.jpg", lambda: imaging.as_image(
            imaging.tinted(photo.image, weight > 0.5)))
        result.outputs[name] = image
        result.sheet.append(["input", f"{name}_unblended", f"blend_{name}.jpg", name])


_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def parse_options(feature: Feature, pairs: list[str]):
    """-o's KEY=VALUE `pairs` as `feature`'s Options, each value read as its
    field's type (a bool as true/false, yes/no, on/off or 1/0); a dash in a
    key reads as an underscore. Raises ValueError for an unknown key or a
    bad value, and for the Options' and feature.check's own checks."""
    cls = feature.Options
    fields = {f.name: f for f in dataclasses.fields(cls)}
    types = typing.get_type_hints(cls)
    values = {}
    for pair in pairs:
        key, sep, text = pair.partition("=")
        key = key.strip().replace("-", "_")
        if not sep or key not in fields:
            raise ValueError(f"{feature.name} has no option {pair!r}; its options: "
                             f"{', '.join(fields) or 'none'} (give -o KEY=VALUE)")
        values[key] = _option_value(key, text.strip(), types[key],
                                    fields[key].metadata.get("choices"))
    opts = cls(**values)
    feature.check(opts)
    return opts


def _option_value(key: str, text: str, kind: type, choices: tuple | None):
    if kind is bool:
        if text.lower() not in _TRUE | _FALSE:
            raise ValueError(f"option {key}: {text!r} is neither true nor false")
        value = text.lower() in _TRUE
    else:
        try:
            value = kind(text)
        except ValueError:
            raise ValueError(f"option {key}: {text!r} isn't {kind.__name__}") from None
    if choices and value not in choices:
        raise ValueError(f"option {key}: {value!r} isn't one of {', '.join(map(str, choices))}")
    return value


def describe_options(feature: Feature) -> list[str]:
    """One line per option of `feature`, with its default and help, for
    main.py --list."""
    lines = []
    for f in dataclasses.fields(feature.Options):
        default = str(f.default).lower() if isinstance(f.default, bool) else f.default
        choices = f.metadata.get("choices")
        lines.append(f"-o {f.name}={default}  " + f.metadata.get("help", "")
                     + (f" [{'|'.join(map(str, choices))}]" if choices else ""))
    return lines


def image_cost(usage, model: str) -> tuple[float, float] | None:
    """List-price cost of one images.edit call's input and of its output,
    None if unknown."""
    prices = IMAGE_PRICES_PER_M.get(model)
    if usage is None or prices is None:
        return None
    details = usage.input_tokens_details
    out_details = usage.output_tokens_details
    image_out = out_details.image_tokens if out_details else usage.output_tokens
    return ((details.text_tokens * prices["text_in"]
             + details.image_tokens * prices["image_in"]) / 1e6,
            image_out * prices["image_out"] / 1e6)


def analyst_cost(usage, model: str) -> tuple[float, float] | None:
    """List-price cost of one analyst call's input and of its output, None
    if unknown."""
    prices = ANALYST_PRICES_PER_M.get(model)
    if usage is None or prices is None:
        return None
    details = usage.input_tokens_details
    cached = details.cached_tokens
    # Not in the SDK's model yet, but the API reports it.
    written = getattr(details, "cache_write_tokens", 0) or 0
    return (((usage.input_tokens - cached - written) * prices["input"]
             + cached * prices["cached_input"] + written * prices["cache_write"]) / 1e6,
            usage.output_tokens * prices["output"] / 1e6)


def image_tokens(width: int, height: int) -> int:
    """The image tokens images.edit bills for an image of width x height it
    is sent, for each image it renders: its size alone counts, not what is
    in it. Under 1024 on its long side it is scaled up to that, at most 2x;
    then it is 32 px patches, at most 1536, shrunk where more as
    GPT-4.1-mini's images are. gpt-image-2.5-sunburst's usage matched it in
    all 627 calls in the project's records on 2026-11-02, photos from
    296x571 to 3120x4160, strayHairWithFace's mask.png beside the photo
    among them."""
    if max(width, height) < 1024:
        scale = min(1024 / max(width, height), 2)
        width, height = int(width * scale), int(height * scale)
    if math.ceil(width / 32) * math.ceil(height / 32) > 1536:
        scale = math.sqrt(1536 * 32 * 32 / (width * height))
        width, height = width * scale, height * scale
        scale = min(math.floor(width / 32) / (width / 32), math.floor(height / 32) / (height / 32))
        width, height = int(width * scale), int(height * scale)
    return math.ceil(width / 32) * math.ceil(height / 32)


def _sent(images: list, mask, usage, renders: int) -> dict:
    """What one images.edit call sent, for its Call: `renders`, the images
    it rendered — its whole input, text and images, is billed once for
    each — and each image it sent, `mask` (images.edit's) last, with its
    name, size and image tokens for each image rendered: image_tokens',
    the mask's the image tokens reported less the images', as images.edit's
    mask has no rule measured."""
    from PIL import Image

    sent = []
    for name, data, _ in images + ([mask] if mask is not None else []):
        width, height = Image.open(io.BytesIO(data)).size
        sent.append({"name": name, "size": [width, height], "tokens": image_tokens(width, height)})
    if mask is not None:
        billed = usage.input_tokens_details.image_tokens if usage is not None and renders else None
        sent[-1].update(mask=True, tokens=None if billed is None else
                        round(billed / renders) - sum(s["tokens"] for s in sent[:-1]))
    return {"renders": renders, "images": sent}


def edit(ctx: Context, result: EffectResult, step: str, images: list, prompt: str,
         size: tuple[int, int], n: int = 1, mask=None, keep_size: bool = False) -> list:
    """One images.edit call to the render model: `images` (images.edit file
    tuples, the photo first) and `prompt`, for `n` images, rendered at
    --size ('match': the aspect of the photo, which is `size`), with `mask`
    as images.edit's mask where given. The call goes into result.calls as
    `step`. Returns the images (PIL, RGB) at the photo's size — as rendered
    with `keep_size` — and raises if the API sent none. Each one resized
    to the photo's is kept as the API sent it, at its own size, in
    result.intermediates as <step>_rendered_<i> (saved with --debug), i
    counting on from an earlier call's of the same step."""
    from PIL import Image
    from _render_errors import no_image_returned

    from . import imaging

    infer, render = ctx.infer, ctx.render
    extra = {"mask": mask} if mask is not None else {}
    with ctx.slots:
        try:
            response = ctx.client.images.edit(
                model=render.model,
                image=images,
                prompt=prompt,
                size=imaging.match_size(*size) if render.size == "match" else render.size,
                quality=render.quality,
                n=n,
                **extra,
            )
        except Exception as e:
            raise infer._raise_openai_error(e) from e
    usage = response.usage
    costs = image_cost(usage, render.model)
    result.calls.append(Call(step, render.model, sum(costs) if costs else None,
                             usage.model_dump() if usage else None, costs[0] if costs else None,
                             _sent(images, mask, usage, len(response.data or []))))
    out = []
    first = sum(key.startswith(f"{step}_rendered_") for key in result.intermediates)
    # infer._extract_image_bytes reads data[0] (b64 or https URL, validated);
    # it is handed one item at a time so every image gets the same checks.
    for item in response.data or []:
        image_bytes = infer._extract_image_bytes(SimpleNamespace(data=[item]))
        if image_bytes is None:
            continue
        image, warning = imaging.fit_render(image_bytes, size, keep_size)
        if warning:
            result.warnings.append(f"{step}: {warning}")
        if result.debug:  # the render decoded again, only to be looked at
            with Image.open(io.BytesIO(image_bytes)) as drawn:
                if drawn.size != image.size:
                    result.intermediates[f"{step}_rendered_{first + len(out)}"] = (
                        drawn.convert("RGB"))
        out.append(image)
    if not out:
        raise no_image_returned(response_text="OpenAI response carried no image data")
    if len(out) < n:
        result.warnings.append(f"{step}: asked for {n} images, got {len(out)}")
    return out


def ask(ctx: Context, result: EffectResult, step: str, image: bytes, prompt: str, schema,
        model: str, reasoning: str, detail: str):
    """One analyst call: `image` (JPEG bytes) and `prompt` to `model`,
    answered in `schema`'s fields (responses.parse), with the analyst's
    timeout. The call goes into result.calls as `step`. Returns the answer;
    raises if there is none."""
    with ctx.slots:
        response = ctx.analyst.responses.parse(
            model=model,
            input=[{"role": "user", "content": [
                {"type": "input_image",
                 "image_url": f"data:image/jpeg;base64,{base64.b64encode(image).decode()}",
                 "detail": detail},
                {"type": "input_text", "text": prompt},
            ]}],
            reasoning={"effort": reasoning},
            text_format=schema,
        )
    usage = response.usage
    costs = analyst_cost(usage, model)
    result.calls.append(Call(step, model, sum(costs) if costs else None,
                             usage.model_dump() if usage else None, costs[0] if costs else None))
    if response.output_parsed is None:
        raise RuntimeError(f"no structured answer; raw output: {response.output_text}")
    return response.output_parsed


def majority(flags) -> bool:
    """More than half of `flags` true; a tie isn't."""
    flags = list(flags)
    return sum(map(bool, flags)) > len(flags) / 2


def ask_majority(ctx: Context, result: EffectResult, step: str, image: bytes, prompt: str,
                 schema, model: str, reasoning: str, detail: str, n: int, skip_reason):
    """`ask` up to `n` times, until more than half of `n` answers go on
    (`skip_reason` None for them) or that can no longer happen: a vote, as
    the analyst reads a photo near a threshold one way one time and the other
    way the next (probe-detect-consistency.py). The asks go one after
    another, so each after the first reads the image and prompt the first
    wrote to the prompt cache (0.1x the input rate, the first paying 1.25x).
    The answers go into result.info[step]: the answer, or with n > 1 a list
    of them, and how many went on into result.info[f"{step}_votes"].
    Returns (answers, None) where they go on, else (answers, why not): the
    answer's reason, or with n > 1 how many didn't and their reasons."""
    answers, reasons = [], []
    while len(answers) < n:
        answers.append(ask(ctx, result, step, image, prompt, schema, model, reasoning, detail))
        reason = skip_reason(answers[-1])
        if reason:
            reasons.append(reason)
        if len(answers) - len(reasons) > n / 2 or len(reasons) >= n / 2:
            break
    go_on = len(answers) - len(reasons)
    if n == 1:
        result.info[step] = answers[0].model_dump()
        return answers, reasons[0] if reasons else None
    result.info[step] = [a.model_dump() for a in answers]
    result.info[f"{step}_votes"] = f"{go_on} of {len(answers)} asked go on (up to {n})"
    if go_on > n / 2:
        return answers, None
    return answers, (f"{len(reasons)} of {len(answers)} asks: "
                     + "; ".join(dict.fromkeys(reasons)))
