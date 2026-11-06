"""soft: HairFeatures' soft_1. Its switches, as HairFeatures set them: no
colour match; not lined up (it pastes its renders into the photo itself,
lined up already); no blend mask.

--- HairFeatures' features/soft_1.py ---

soft_1: soft reworked to match rst_pub_by_feature's 5_Soft (柔順), which
soft's renders differ from in three ways (output/soft against 5_Soft, the
28 photos of rst_pub_by_feature/0_Original):

  - soft takes the halo of fine flyaways off the hair's outline; 5_Soft
    leaves every one of them as it is: it only changes the hair's body, the
    hair the in-house hair model masks, which leaves the halo out.
  - soft's renders can re-comb the hair: locks moved, waves redrawn; 5_Soft
    keeps every strand's path.
  - soft's strands are crisper: gpt-image smooths the finest grain away
    (to 0.72 of the photo's, the median of the photos' hair) but keeps the
    contrast between strands and strand groups (0.77–0.99); 5_Soft keeps
    the grain (1.05) and lowers that contrast (to 0.74), which reads as a
    light blur. soft's renders also darken the hair (subject0's by 11 of
    255, subject24's by 14); 5_Soft's by about 3.

Per photo:

  1. render: the photo and _PROMPT — soft's, reworked: the hair's texture
     inside its body smoothed, the halo of flyaways kept, every strand's
     path locked, the contrast between strands lowered — for --n-images
     images (render_<i>). -o source=photo leaves this step out: the photo
     itself is softened, for no billed call, one result.
  2. hair mask: the photo's hair from the in-house hair model (hair_mask);
     each render lined up with the photo outside it (align.line_up).
  3. paste: the render pasted into the photo through the hair's body — the
     hair mask shrunk by _BODY_ERODE and feathered (body.jpg) — so the
     halo, the face and the background stay the photo's, pixel for pixel.
     With -o colour_lock (the default) the render's hair takes the photo's
     low frequencies first (_COLOUR_LOCK): its colour and the way light
     falls on it, the waves' highlights where they were. Where the render's
     strands don't line up with the photo's (_structure_guard: the
     strand-scale detail of the two correlates under -o guard), the
     render moved or redrew them, and the photo is kept there
     (guard_<i>.jpg, the photo kept in red).
  4. soften: in the hair's body, each band of detail (_BANDS) rescaled so
     its contrast is _REF_RATIOS of the photo's in that band — 5_Soft's, at
     -o soften=1 (the default): the grain kept, the strands softened. Where
     the render smoothed a band past what scaling it brings back (its
     grain, mostly), the photo's own band fills the rest.

Billed calls per photo: 1 render, none with -o source=photo. The results are
pasted into the photo, lined up already.
"""

from dataclasses import dataclass

from .. import align, hairseg, imaging
from ..common import _gaussian, _hair_body
from ..core import Feature, edit, option


# Bands of detail, by the Gaussian blurs (px on a 1024 px short side) that
# split them: finest grain, strands, strand groups, locks; the rest, the
# base, is left as it is. _REF_RATIOS is each band's contrast in 5_Soft
# over the photo's, in the hair's body, the median over the 28 photos —
# 1.05, 0.86, 0.74, 0.74, 0.86 measured, the grain's taken as 1.0, as 5_Soft
# sharpens a few small photos and the grain isn't to be added to.
_BANDS = (0.0, 0.7, 1.4, 2.8, 5.6, 11.2)

# Bands of detail, by the Gaussian blurs (px on a 1024 px short side) that
# split them: finest grain, strands, strand groups, locks; the rest, the
# base, is left as it is. _REF_RATIOS is each band's contrast in 5_Soft
# over the photo's, in the hair's body, the median over the 28 photos —
# 1.05, 0.86, 0.74, 0.74, 0.86 measured, the grain's taken as 1.0, as 5_Soft
# sharpens a few small photos and the grain isn't to be added to.
_REF_RATIOS = (1.0, 0.86, 0.74, 0.74, 0.86)

# A band is scaled by no less, and no more, than this: past them it is
# more likely the render's than a matter of contrast; what a band still
# lacks is filled from the photo's (_band_mix). The gains are fitted in
# this many passes (_softened).
_GAIN_RANGE = (0.4, 1.3)

# A band is scaled by no less, and no more, than this: past them it is
# more likely the render's than a matter of contrast; what a band still
# lacks is filled from the photo's (_band_mix). The gains are fitted in
# this many passes (_softened).
_GAIN_PASSES = 3

# The render's colour lock: its hair's detail on the photo's hair's
# frequencies below a Gaussian of this share of the short side, each taken
# from the hair alone, so the background doesn't bleed in.
_COLOUR_LOCK = 0.02

# The structure guard: the detail between Gaussians of _GUARD_BAND px (on a
# 1024 px short side) of the photo and the render, correlated over a
# Gaussian window of _GUARD_WINDOW px; under -o guard, the render is
# replaced by the photo, fully _GUARD_RAMP under it. Only where the photo
# has detail to compare: its band's RMS past _GUARD_DETAIL (0–255 levels).
# -o guard's default, 0.7, on output/soft's renders of the 28 photos: the
# photo kept in 4.8% of the hair's body (the median; 22% at most, where a
# render restyled the crown), their strands' direction 3.3° off the
# photo's, against 3.0° for 5_Soft (3.9° at 0.4, 0.6% kept). On soft_1's
# own renders of 6 of them (subject0, 4, 5, 18, 23, 26), which _PROMPT
# keeps closer: 1.7% kept, 2.7° off, against 3.5° for 5_Soft.
_GUARD_BAND = (1.0, 8.0)

# The structure guard: the detail between Gaussians of _GUARD_BAND px (on a
# 1024 px short side) of the photo and the render, correlated over a
# Gaussian window of _GUARD_WINDOW px; under -o guard, the render is
# replaced by the photo, fully _GUARD_RAMP under it. Only where the photo
# has detail to compare: its band's RMS past _GUARD_DETAIL (0–255 levels).
# -o guard's default, 0.7, on output/soft's renders of the 28 photos: the
# photo kept in 4.8% of the hair's body (the median; 22% at most, where a
# render restyled the crown), their strands' direction 3.3° off the
# photo's, against 3.0° for 5_Soft (3.9° at 0.4, 0.6% kept). On soft_1's
# own renders of 6 of them (subject0, 4, 5, 18, 23, 26), which _PROMPT
# keeps closer: 1.7% kept, 2.7° off, against 3.5° for 5_Soft.
_GUARD_WINDOW = 10.0
_GUARD_RAMP = 0.25
_GUARD_DETAIL = (1.5, 4.0)

# Where the guard keeps the photo, the switch is feathered by this (px on a
# 1024 px short side).
_GUARD_FEATHER = 4.0

# Lining a render up matches the photo outside its hair grown by this share
# of the long side, so the halo the render may change isn't matched.
_ALIGN_GROW = 0.01

# soft's prompt (single_prompt.EFFECT_PROMPTS["soft"]) reworked for 5_Soft:
# the halo of flyaways, which soft's asks to reduce, kept; every strand's
# path locked (STRAND PATHS LOCKED); the strands softened by their contrast,
# not their grain; and no hair added or removed. On 2026-10-29 the face's
# features, the body, clothing and accessories, and the background spelled
# out, each with what of it stays (single_prompt's note).
_PROMPT = (
    "Edit ONLY the inside of the hair mass in this photo: apply the hair retouch below to it and leave every other pixel exactly as it is. No hair is added or removed anywhere. This is a local retouch of the hair of an existing photo, not a new image.\n"
    "\n"
    "CHANGE ONLY — the inside of the hair mass:\n"
    "- Surface texture, strong but localized. Smooth rough, stringy or frizzy patches inside the hair mass — crown, fringe and lengths — into softly connected sections; keep the hair's fine natural grain, with no plastic or airbrushed finish.\n"
    "- Strand definition, soft. Lower the contrast between neighbouring strands and strand groups, so the hair reads as one soft, silky mass rather than crisp, separately drawn strands, as through a light diffusion filter; keep the fine grain, add no gloss, and do not sharpen.\n"
    "- STRAND PATHS LOCKED. Every strand, lock, wave and curl keeps its exact path: its direction, curvature, where it starts and ends, and where each wave or curl turns. Join neighbouring fibres only along the path they already share; do not re-comb, re-part, move, merge, straighten or re-curl any lock, and do not draw new strands.\n"
    "- HAIR COLOUR LOCKED. Every strand keeps its exact hue, lightness, saturation and warmth, at the roots, lengths and ends alike; smoothing must not darken, lighten, warm, cool, enrich, mute or even out the colour, and the light and dark strands, grey or white hairs, dye, highlights, lowlights and ombré or balayage transitions stay exactly where they are.\n"
    "\n"
    "PRESERVE EXACTLY — do not change any of these in any way:\n"
    "- The hair's outline: every fine halo hair, flyaway and loose strand along the outer silhouette, at the crown and over the background, shoulders or face stays exactly where and as it is, as frizzy as it is — the retouch works inside the hair mass, never at its edge.\n"
    "- The hair's shape: haircut, length, parting, hairline, bangs, layers, face-framing strands, volume, overall silhouette, and curl or wave size, pattern and direction.\n"
    "- The hair's colour: hue, lightness, saturation, warmth, tone and colour placement, and any dye, highlights, lowlights, balayage, ombré, grey or white hairs, stay exactly as in this photo everywhere in the hair — CHANGE ONLY changes the hair's texture, never its colour.\n"
    "- The hair's light: light direction, rim light, and where its highlights and shadows fall, with no added gloss.\n"
    "- Face and identity: the same person, with the same face shape, jaw and chin, expression, gaze and head pose; the eyes, eyebrows, nose, lips, teeth and ears each keep their exact position, size, shape and colour and the spacing between them — not redrawn, moved, resized, reshaped or made more symmetrical.\n"
    "- Skin: tone, brightness, redness, texture, pores, freckles, wrinkles, moles and makeup; no smoothing, retouching or beautifying. Where hair crosses the face, forehead, ears, neck or shoulders, the skin beside and between the strands stays exactly as it is.\n"
    "- Body, hands, clothing and accessories: the neck, shoulders, arms, hands and fingers keep their shape, pose and position; every garment keeps its colour, pattern, texture, folds and edges; glasses, earrings, jewellery, hair clips, headbands, hats and anything else worn keep their shape, colour and position, including where they sit in or on the hair.\n"
    "- Background: every object, surface, edge, pattern, light and shadow in it stays exactly where and as it is, with the same colour, brightness, sharpness, blur and grain — nothing added, removed, moved, extended, cleaned up or re-drawn; any text, stickers, graphics, watermarks, borders, black bars or inset images stay as they are; do not remove them.\n"
    "- Light and colour outside the hair: light direction, shadows, exposure, white balance, contrast, sharpness, grain, compression texture and depth of field; no denoising, sharpening or re-drawn textures.\n"
    "- Framing: no zoom, crop, shift, rotation or reframing; outside the hair the output lines up pixel-for-pixel with this photo.\n"
    "\n"
    "OUTPUT: one photorealistic photo identical to this photo outside the inside of the hair mass, its hair the same colour, shape and strand paths as in this photo — no collage, border, text or watermark."
)


@dataclass
class SoftOptions:
    source: str = option("render", "what is softened: gpt-image's render of the photo "
                         "(step 1), or the photo itself, for no billed call",
                         choices=("render", "photo"))
    soften: float = option(1.0, "how far each band's contrast goes to 5_Soft's ratio to the "
                           "photo's: 0 leaves it, 1 is 5_Soft's, past 1 softer still")
    guard: float = option(0.7, "the correlation with the photo's strands under which the "
                          "render is replaced by the photo; 0 turns the guard off")
    colour_lock: bool = option(True, "give the render's hair the photo's low frequencies: "
                               "its colour and light")

    def __post_init__(self):
        if not 0 <= self.soften <= 2:
            raise ValueError(f"option soften: {self.soften} isn't 0–2")
        if not 0 <= self.guard < 1:
            raise ValueError(f"option guard: {self.guard} isn't 0 to under 1")


def _smoothstep(x, lo: float, hi: float):
    import numpy as np

    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def _colour_locked(photo, render, hair, short: int):
    """`render` (float RGB) with its hair's frequencies under _COLOUR_LOCK
    replaced by the photo's, each low-passed over `hair` alone."""
    import numpy as np

    m = hair.astype(np.float32)[..., None]
    sigma = _COLOUR_LOCK * short
    weight = _gaussian(m, sigma) + 1e-4
    low_photo = _gaussian(photo * m, sigma) / weight
    low_render = _gaussian(render * m, sigma) / weight
    return np.clip(render + (low_photo - low_render) * m, 0, 255)


def _structure_guard(photo, render, guard: float, k: float):
    """Where to keep the photo instead of `render` (both float RGB), 0–1:
    where their strand-scale detail (_GUARD_BAND) correlates under `guard`
    over _GUARD_WINDOW, the render's strands having moved or been redrawn,
    as far as the photo has detail there to tell (_GUARD_DETAIL); and the
    correlation itself."""
    import numpy as np

    def detail(rgb):
        grey = rgb.mean(axis=2)
        return _gaussian(grey, _GUARD_BAND[0] * k) - _gaussian(grey, _GUARD_BAND[1] * k)

    a, b = detail(photo), detail(render)
    w = _GUARD_WINDOW * k
    ea, eb = _gaussian(a * a, w), _gaussian(b * b, w)
    corr = _gaussian(a * b, w) / np.sqrt(ea * eb + 1e-3)
    keep_photo = ((1 - _smoothstep(corr, guard - _GUARD_RAMP, guard))
                  * _smoothstep(np.sqrt(ea), *_GUARD_DETAIL))
    return np.clip(_gaussian(keep_photo, _GUARD_FEATHER * k) * 1.3, 0, 1), corr


def _bands(rgb, k: float):
    """`rgb` (float) split into _BANDS' bands of detail, finest first, and
    the base under them; they add up to it."""
    levels = [rgb] + [_gaussian(rgb, s * k) for s in _BANDS[1:]]
    return [levels[i] - levels[i + 1] for i in range(len(levels) - 1)], levels[-1]


def _band_mix(target: float, own: float, photo: float, shared: float):
    """The gain for a band of RMS `own`, and how much of the photo's band
    (RMS `photo`, mean product with it `shared`) to add, for an RMS of
    `target`: the gain alone within _GAIN_RANGE; past its top, the gain at
    the top and the photo's band filling the rest."""
    import math

    lo, hi = _GAIN_RANGE
    if target <= hi * own or photo <= 1e-3:
        return min(max(target / max(own, 1e-3), lo), hi), 0.0
    # |hi*b + h*p|^2 = target^2, solved for h >= 0.
    b = hi * shared
    c = (hi * own) ** 2 - target ** 2
    return hi, (-b + math.sqrt(max(b * b - photo * photo * c, 0.0))) / (photo * photo)


def _softened(photo, image, body, soften: float, k: float):
    """`image` (float RGB) with each band of detail rescaled in the hair's
    `body` so its contrast there is _REF_RATIOS of the photo's, as far as
    `soften` goes; and each band's gain and photo fill. A band is scaled
    within _GAIN_RANGE (_band_mix); what it still lacks — the grain and
    strands a render smoothed away — is filled from the photo's own band.
    The bands overlap, so a band rescaled spills into its neighbours when
    the result is split again: the targets are refitted _GAIN_PASSES times
    to the bands of the result."""
    import numpy as np

    inside = body > 0.5
    if not inside.any():
        return image, []

    def rms(grey_bands):
        return np.array([np.sqrt((b[inside] ** 2).mean()) for b in grey_bands])

    photo_bands, _ = _bands(photo, k)
    bands, base = _bands(image, k)
    grey_photo = [b.mean(axis=2) for b in photo_bands]
    grey = [b.mean(axis=2) for b in bands]
    grey_base = base.mean(axis=2)
    own, from_photo = rms(grey), rms(grey_photo)
    shared = np.array([(b[inside] * p[inside]).mean() for b, p in zip(grey, grey_photo)])
    targets = from_photo * (1 - soften * (1 - np.array(_REF_RATIOS)))
    aims = targets.copy()
    for _ in range(_GAIN_PASSES):
        mix = [_band_mix(*args) for args in zip(aims, own, from_photo, shared)]
        now = rms(_bands(grey_base + sum(g * b + h * p for (g, h), b, p
                                         in zip(mix, grey, grey_photo)), k)[0])
        aims *= targets / np.maximum(now, 1e-3)
    mix = [_band_mix(*args) for args in zip(aims, own, from_photo, shared)]
    out = base + sum(g * b + h * p for (g, h), b, p in zip(mix, bands, photo_bands))
    a = body[..., None]
    return image * (1 - a) + out * a, [[round(float(g), 3), round(float(h), 3)] for g, h in mix]


def post_process(photo, render, hair, opts):
    """Steps 3 and 4 for one render: `render` (PIL, lined up with `photo`,
    or None to soften the photo itself) pasted into `photo` through the
    hair's body, from `hair` (the photo's hair mask), and softened. Returns
    the result, where the photo was kept for the render (0–1, None without
    a render or a guard) and notes."""
    import numpy as np

    p = np.asarray(photo.convert("RGB"), np.float32)
    short = min(photo.size)
    k = short / 1024
    body = _hair_body(hair, short)
    notes, keep_photo, image = {}, None, p
    if render is not None:
        r = np.asarray(render.convert("RGB"), np.float32)
        if opts.colour_lock:
            r = _colour_locked(p, r, hair, short)
        alpha = body
        if opts.guard > 0:
            keep_photo, corr = _structure_guard(p, r, opts.guard, k)
            inside = body > 0.5
            if inside.any():
                notes["strand_correlation"] = round(float(np.median(corr[inside])), 3)
                notes["photo_kept"] = round(float((keep_photo[inside] > 0.5).mean()), 4)
            alpha = body * (1 - keep_photo)
        image = p * (1 - alpha[..., None]) + r * alpha[..., None]
    image, notes["band_gains"] = _softened(p, image, body, opts.soften, k)
    return imaging.as_image(image), keep_photo, notes


class SoftFeature(Feature):
    description = ("soft reworked to match 5_Soft: the render pasted into the hair's body "
                   "(the halo kept), the photo kept where the render moved strands, and the "
                   "strands softened by 5_Soft's contrast ratios; 1 render call per photo "
                   "(none with -o source=photo)")
    Options = SoftOptions
    default_n_images = 4  # soft's

    def __init__(self):
        super().__init__("soft")

    def align_outputs(self, opts) -> bool:
        # Pasted into the photo, lined up already (process).
        return False

    def prompts(self, opts) -> dict[str, str]:
        return {"edit": _PROMPT} if opts.source == "render" else {}

    def process(self, ctx, photo, opts, result) -> None:
        renders = []
        if opts.source == "render":
            result.step = "render"
            renders = edit(ctx, result, "edit", [photo.file], _PROMPT, photo.size,
                           n=self.n_images(ctx.render))

        result.step = "hair mask"
        hair = hairseg.hair_masks([photo.image], ctx.seg_python)[0]
        if not hair.any():
            raise RuntimeError("the hair model found no hair")
        result.info["hair_share"] = float(hair.mean())
        result.intermediates["hair_mask"] = imaging.mask_image(hair)
        body = _hair_body(hair, min(photo.size)) > 0.5
        result.intermediates["body.jpg"] = imaging.as_image(imaging.tinted(photo.image, body))

        result.step = "paste and soften"
        if not renders:
            result.outputs["result_0"], _, result.info["result_0"] = post_process(
                photo.image, None, hair, opts)
            result.sheet.append(["input", "body.jpg", "result_0"])
            return
        near = imaging.grown(hair, _ALIGN_GROW * max(photo.size))
        for i, raw in enumerate(renders):
            result.intermediates[f"render_{i}"] = raw
            aligned, lines = align.line_up(photo.image, raw, near)
            image, keep_photo, notes = post_process(
                photo.image, aligned if aligned is not None else raw, hair, opts)
            result.outputs[f"result_{i}"] = image
            result.info[f"result_{i}"] = {**notes, "alignment": lines}
            row = ["input", f"render_{i}", f"result_{i}"]
            if keep_photo is not None:
                result.intermediates[f"guard_{i}.jpg"] = imaging.as_image(
                    imaging.tinted(photo.image, (keep_photo > 0.5) & body))
                row.insert(2, f"guard_{i}.jpg")
            result.sheet.append(row)
