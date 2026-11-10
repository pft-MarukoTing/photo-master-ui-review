"""frizzCare: HairFeatures' frizzCare_4 and the frizzCare_3 it extended, in
one module. Its switches, as HairFeatures set them for frizzCare_4: colour
match on, in the hair drawn in from its outline (hair_inset); lined up
--align global; --blend through the hair, off by default.

--- HairFeatures' features/frizz_care_4.py ---

frizzCare_4: frizzCare_3 with step 2's gain set per pixel by how much
fine detail there is round it: where what step 2 puts back is weak — the
render soft there — more is put back, and where it is strong, less.

The gain at each pixel is the one that brings the result's detail finer
than -o fine px — what step 1's blur left of it, plus the gain times what
step 2 puts back — to -o level % of the hair's mean grey there, each the
RMS on grey over a Gaussian window of -o window px round it (on a 1024 px
short side) weighted by the hair's body; times -o scale, within -o
min_gain and -o max_gain. It is weighed against the hair's own grey, not
in grey levels: on output_check/frizzCare_2's renders, subject8's (light
hair, its mean grey 120; soft to the eye) had more fine detail under 1 px
in grey levels than subject9's (dark, 45; crisp at the crown) — what step
2 puts back 5.1 against 3.2, medians in the body — but less of their
grey: the result's at gain 1 4.4–5.0% against 8.5–10.1%. Brought instead
to a ratio of the fine detail to the strands round it (what step 1 took
coarser than -o fine px), as first tried, the gain left a soft render
soft: subject8's medians 0.86–0.97, its strands as soft as its fine
detail.

-o fine is 2, not frizzCare_3's 1: what step 2 put back under 1 px
(added_<i>) showed aliasing. On those 111 renders, the result's fine
detail at gain 1 (the body's median) ran from 7.6% to 20.2% under 2 px
(10th to 90th percentile), from 4.1% to 12.9% under 1. -o level's 11 is
the median of the renders' 90th percentiles under 1 px (18.2 under 2),
and -o scale's 1.5, -o min_gain's 0.25 and -o max_gain's 1.5 were set
by hand (at first 1 and 2: subject9's crown as drawn, the rest twice;
then 0.25 and 3). The gains these gave, with the blur's term below (10th
percentile, median and 90th in the body): subject8's result_0 1.5 all
over; subject9's 0.58, 1.05, 1.50; subject4's 0.78, 1.03, 1.36;
subject3's 0.54, 1.50, 1.50. Over the 111 results, the gain's median was
1.5 in 37%, its 90th percentile in 75%, and its 10th 0.25 in 14%.

Before -o scale and the clip, the gain is two terms multiplied: the
back's, the target over the RMS of what step 2 puts back at 1 (T / F),
and the blur's, sqrt(1 - kappa² (1 - rho²)) - rho kappa, kappa the RMS
of the detail step 1's blur left over the target (K / T) and rho its
correlation with what step 2 puts back: 1 where the blur left none, less
the nearer K comes to T. So the gain goes as one over F, and with K
mostly where K nears T (d ln g / d ln K's median -0.34). On those 111
renders, in the body, K / T ran 0.19, 0.35 and 0.63 (10th percentile,
median and 90th), F / T 0.41, 0.86 and 1.62, rho 0.37, 0.47 and 0.59;
of the variance of the gain's log, the back's term took 49%, the blur's
18% and the two together 33% (where the one is low, mostly so is the
other). -o back_power and -o blur_power raise each term to a power
before -o scale.

By default the blur's term is left out (-o blur_power 0): the gain is
-o scale's 1.13 times the one that brings what step 2 puts back, alone, to
-o level. The blur's term couldn't tell strands from the outlines of
high-contrast curls: on subject7's and subject22's, what step 1's blur
left under 2 px was mostly the outlines of their highlights and shadows,
which it blurs along, not across, and the term took the gain down there;
subject22's results kept 0.49–0.53 of the render's detail under 2 px,
subject4's, 7's and 9's 0.96–1.00. Taken out of K, the part a step edge
of the coarse contrast round it would leave (68% of K² on subject7's,
21% on subject22's, whose curls' glints are thin lines) or weighted by
how parallel the detail runs, it still took subject22's down. Without
it, at -o scale 1.13 (the 111 results' median gain as with it, 1.22),
subject22's kept 0.60–0.67; with -o back_power 0.5 too (scale 1.17),
0.76–0.81, but the gain flatter over the hair (its median at 1.5 in 29%
of the results, against 38%). The gains at 1.13: subject8's 1.5 all
over; subject9's 0.86, 1.17, 1.50; subject4's 0.82, 1.01, 1.25;
subject22's 0.37, 0.55, 1.27; over the 111 results, the gain's median
was 1.5 in 38%, its 90th percentile in 74%, its 10th 0.25 in none.

debug/<photo>/gain_<i> shows the gain over the picture (frizzCare_3's
_GAIN_WHITE white), gain_back_<i> and gain_blur_<i> its two terms on the
same scale; info's result_<i> has the result's fine detail at gain 1
(fine_level, % of the grey), the gain and its terms (gain_back,
gain_blur), each's 10th percentile, median and 90th in the body.

Everything else is frizzCare_3's: the render (frizzCare_2's prompt), the
hair mask, step 1, the colour match (hair_inset), the lining up and
--blend. replay-frizzcare3.py --effect frizzCare_4 runs it on an earlier
run's frizzCare_2 renders, with no billed call.

Billed calls per photo: 1 render.

--- HairFeatures' features/frizz_care_3.py ---

frizzCare_3: frizzCare_2's render with its strands and strand groups
smoothed along the hair's flow, the finest grain kept. On some photos
frizzCare_2's renders look harder (剛硬) than the photo: their strand-scale
detail comes out more contrasted than the photo's (on subject0 the detail
under 3 px 1.26–1.56 times the photo's, on subject9 the 3–6 px band 1.07–
1.33 times; output/frizzCare_2's 4 renders each, in the hair's body,
measured as soft_1 measures its bands).

Two steps, in each render's hair body (px are sigmas on a 1024 px short
side):

  1. An oriented blur: a Gaussian of -o blur px along the hair's flow and
     a _ACROSS-th of that across it, the flow found per pixel from the
     structure tensor of the render's grey (its gradient after a Gaussian
     of _DERIVATIVE px, averaged over one of _FLOW_WINDOW px). Unlike
     soft_1's isotropic bands, it smooths along the strands more than
     across them.
  2. What step 1 took, the render less its blur, put back where it is
     finer than -o fine px (it less its Gaussian of that), -o gain times
     over: the finest grain and the hairs' thin edges, so the hair isn't
     painted over. What stays smoothed is what step 1 took between the two
     sizes; over 1, the finest grain comes out stronger than the render's.

Put back coarser than 3 px instead, as first tried, step 2 undid nearly all
step 1 did to the strands and strand groups (step 1 at 8 px: the 1.4–3 px
band at 0.89 of the render's, the 3–6 px at 0.98, on subject9's result_0).
Tried on frizzCare_2's renders, each band's RMS in the hair's body over the
render's, measured as soft_1 measures its bands. Step 1's size, with step 2
at 1 px (result_0 of subject9, 0_Original_subject9 and subject15, medians):

  step 1    0–0.7   0.7–1.4   1.4–3   3–6    6–12 px
  4 px      0.86    0.71      0.77    0.92   0.98
  8 px      0.84    0.60      0.50    0.76   0.92
  12 px     0.84    0.58      0.35    0.61   0.84
  16 px     0.84    0.58      0.30    0.50   0.76

Step 2's, with step 1 at 4 px (0_Original_subject9's result_1, subject15's
result_0):

  step 2              0–0.7       0.7–1.4     1.4–3       3–6
  finer than 1 px     0.82–0.87   0.71–0.72   0.76        0.91–0.92
  1 px, 1.5 times     1.18–1.27   0.93–0.95   0.80        0.92–0.93
  finer than 2 px     0.98–1.00   0.94–0.96   0.88–0.89   0.93–0.94
  finer than 3 px     1.00        0.99–1.00   0.96        0.95

Per photo:

  1. render: the photo and frizzCare_2's prompt (single_prompt's
     EFFECT_PROMPTS["frizzCare_2"]) for --n-images images (render_<i>).
  2. hair mask: each render's hair from the in-house hair model.
  3. smooth: in each render's hair body — its hair mask shrunk and
     feathered as soft_1's is (_hair_body), so the outline and the halo
     round it aren't blurred (body_<i>.jpg, the body in red) — steps 1 and
     2 above: base_<i> is step 1's blur, added_<i> what step 2 put back,
     _SHOWN times over on grey, gain_<i> how many times over it put it
     back at each pixel (the gain in the body, feathered to none off it;
     black none, white _GAIN_WHITE times; frizzCare_4's gain_back_<i> and
     gain_blur_<i>, its gain's two terms, alike). The gain and each band's
     RMS there over the render's go into info (gain, bands).

The results then go through Feature.finish as frizzCare_2's do, but
colour-matched in the hair drawn in from its outline and further from the
face's skin (features' COLOUR_MATCH_AREAS: hair_inset), then lined up
and, with --blend, pasted into the photo through the hair.

Billed calls per photo: 1 render; none in replay-frizzcare3.py, which runs
steps 2 and 3 (smooth) and Feature.finish on an earlier run's frizzCare_2
renders.
"""

import math
import os
from dataclasses import dataclass

from .. import hairseg, imaging
from ..common import SinglePromptFeature, _gaussian, _hair_body
from ..core import edit, option


# HairFeatures' EFFECT_PROMPTS['frizzCare_2'].
_PROMPT = ("Re-align the fine hairs in this photo's hair: inside every strand group, comb the single hairs straight along the group's own direction, so that they all lie parallel and none crosses another — the hair's surface clean and orderly, like the parallel fibres of a silk thread, instead of a tangle of crossing, kinked and fuzzy fine hairs. Edit ONLY the hair region and leave every other pixel exactly as it is; in the hair, the strand groups themselves, the waves, the outline, the light and the colour stay as they are. Where fuzz or flyaways are taken away at the hair's edges, the skin or background there blends seamlessly into its surroundings. This is a retouch of the hair of an existing photo, not a new image.\n"
    "\n"
    "CHANGE ONLY — the single hairs, strongly, so the change is clear at a glance:\n"
    "- Every crossing hair re-laid. Each fine hair that crosses the flow — at an angle, in an X over its neighbours, in a loop, kink or zigzag, or as a loose fibre lying across the strand groups — is laid back along the flow or taken away. None is left crossing, anywhere in the hair's body, from the roots to the ends.\n"
    "- Parallel and continuous. Inside each strand group every single hair runs as one long, unbroken line, parallel to its neighbours at an even spacing, following the group's direction and its curve through each wave or curl.\n"
    "- Crisp, not soft. Each single hair stays a sharp, clearly drawn line, as crisp as the sharpest hairs in this photo: the fine hairs are re-laid, not blurred, smoothed over, softened or merged into a plain, painted mass.\n"
    "- Frizz cleared. The fuzzy haze of short, broken fibres on the hair's surface, between strand groups and at the crown and parting is cleared away. At the hair's outer edge, thin fuzz and short flyaways are reduced, with a few fine wisps kept and the outline where it is.\n"
    "- Glints along the hairs. Broken specks of highlight join into thin, continuous highlight lines along the re-laid hairs, as bright as they are and where they are; no new shine.\n"
    "- Do not hold back. A result that looks like this photo with only a light smoothing, its crossing and fuzzy hairs still there, is wrong: the re-aligned fine hairs are the requested result.\n"
    "\n"
    "KEEP — the hair at every scale larger than single hairs; the combing happens inside each strand group only:\n"
    "- Strand groups and locks: each keeps its exact path, width, position and ends, the spacing between them, where they part and join, and where each wave or curl in them turns; none is moved, merged, split, straightened or re-curled.\n"
    "- Waves, curls and shape: their size, shape, direction, looseness and spacing, and the hair's volume, outline and silhouette.\n"
    "- Light and shading across the hair: the broad highlights, shadows and rim light, where they fall and how bright or dark they are, with no more contrast between them.\n"
    "- HAIR COLOUR LOCKED: every strand keeps its exact hue, lightness, saturation and warmth, and the highlights, lowlights, grey or white hairs and any dye stay where they are; do not warm, cool, brighten, darken, even out or intensify them.\n"
    "\n"
    "PRESERVE EXACTLY — do not change any of these in any way:\n"
    "- Face and identity: the same person, with the same face shape, jaw and chin, expression, gaze and head pose; the eyes, eyebrows, nose, lips, teeth and ears each keep their exact position, size, shape and colour and the spacing between them — not redrawn, moved, resized, reshaped or made more symmetrical.\n"
    "- Skin: tone, brightness, redness, texture, pores, freckles, wrinkles, moles and makeup; no smoothing, retouching or beautifying.\n"
    "- Where hair crosses the face, forehead, ears, neck or shoulders, the skin beside and between the strands stays exactly as it is, except where CHANGE ONLY takes hair away there.\n"
    "- Body and hands: the neck, shoulders, arms, hands and fingers keep their shape, pose and position.\n"
    "- Clothing: every garment's colour, pattern, texture, folds and edges.\n"
    "- Accessories: glasses, earrings, jewellery, hair clips, headbands, hats and anything else worn — their shape, colour and position, including where they sit in or on the hair.\n"
    "- Background: every object, surface, edge, pattern, light and shadow in it stays exactly where and as it is, with the same colour, brightness, sharpness, blur and grain — nothing added, removed, moved, extended, cleaned up or re-drawn except right at the hair's edge, where CHANGE ONLY takes hair away; any text, stickers, graphics, watermarks, borders, black bars or inset images stay as they are; do not remove them.\n"
    "- The hair's colour, whatever CHANGE ONLY says: hue, overall lightness, saturation, tone, colour placement and any dye, highlights, lowlights or balayage stay exactly as in this photo; the hair is not darkened, lightened, warmed or cooled.\n"
    "- The hair's cut, whatever CHANGE ONLY says: haircut, length, parting, hairline, bangs and layers.\n"
    "- Light and colour outside the hair region: light direction, shadows, exposure, white balance, contrast, sharpness, grain, compression texture and depth of field; no denoising, sharpening or re-drawn textures.\n"
    "- Framing: no zoom, crop, shift, rotation or reframing; outside the hair region the output lines up pixel-for-pixel with this photo.\n"
    "\n"
    "BEFORE YOU FINISH — look at the hair's body up close: where a fine hair still crosses the flow, or the single hairs inside a strand group are not parallel, re-lay them.\n"
    "\n"
    "OUTPUT: one photorealistic photo identical to this photo outside the hair region; in the hair, every fine hair re-laid parallel along its strand group's flow, crisp and continuous, with no crossing hairs or frizz left, and every strand group, lock, wave and curl, the light on the hair, and its colour and cut as in this photo — no collage, border, text or watermark.\n")

# Step 1's Gaussian across the flow is a _ACROSS-th of the one along it.
_ACROSS = 3.0

# The flow: the structure tensor of the grey blurred by a Gaussian of
# _DERIVATIVE px, averaged over one of _FLOW_WINDOW px.
_DERIVATIVE = 1.0

# The flow: the structure tensor of the grey blurred by a Gaussian of
# _DERIVATIVE px, averaged over one of _FLOW_WINDOW px.
_FLOW_WINDOW = 4.0

# added_<i> shows what step 2 put back this many times over, on grey.
_SHOWN = 4

# gain_<i> is white where step 2 put the detail back this many times over
# (frizzCare_4's gain_back_<i> and gain_blur_<i> where its terms are this).
_GAIN_WHITE = 2.0

# The bands measured in info, by the Gaussians that split them.
_BANDS = (0.0, 0.7, 1.4, 3.0, 6.0)

# aiHair: how step 1 (_line_blur) takes the image at each sample along the
# flow — "remap", by cv2.remap (_remapped), or "numpy", by _sampled, as
# HairFeatures does. On subject1, 7, 20, 22 and 25 and subject1 upscaled 2x
# (9.8 MP), remap took step 1 from 1.1-75 s to 0.07-5 s and smoothed()
# 2.7-5.1x faster (2.7-4.5x on one OpenCV thread); the results, as saved
# (uint8), at most 1 level off numpy's, on 0.05-1.5% of the pixels
# (output/remap_check, 2026-11-09).
_LINE_SAMPLER = "remap"


def _flow(grey, k: float):
    """Unit vectors along the hair's flow and across it, per pixel (each an
    x and a y array), from the structure tensor of `grey`."""
    import numpy as np

    gy, gx = np.gradient(_gaussian(grey, _DERIVATIVE * k))
    jxx, jxy, jyy = (_gaussian(a, _FLOW_WINDOW * k) for a in (gx * gx, gx * gy, gy * gy))
    phi = 0.5 * np.arctan2(2 * jxy, jxx - jyy)  # the gradient's: across the flow
    nx, ny = np.cos(phi), np.sin(phi)
    return (-ny, nx), (nx, ny)


def _sampled(img, xs, ys):
    """`img` (height x width x channels) at (`xs`, `ys`), bilinearly, its
    edges held."""
    import numpy as np

    h, w = img.shape[:2]
    flat = img.reshape(h * w, -1)
    xs, ys = np.clip(xs, 0, w - 1.001), np.clip(ys, 0, h - 1.001)
    x0, y0 = xs.astype(np.int32), ys.astype(np.int32)
    fx, fy = (xs - x0).ravel()[:, None], (ys - y0).ravel()[:, None]
    i = (y0 * w + x0).ravel()
    top = np.take(flat, i, axis=0) * (1 - fx) + np.take(flat, i + 1, axis=0) * fx
    bottom = np.take(flat, i + w, axis=0) * (1 - fx) + np.take(flat, i + w + 1, axis=0) * fx
    return (top * (1 - fy) + bottom * fy).reshape(img.shape)


def _remapped(img, xs, ys):
    """`img` at (`xs`, `ys`) as _sampled takes it, by cv2.remap (aiHair):
    bilinear, a px's fraction in 32nds (OpenCV's INTER_LINEAR), the edges
    replicated."""
    import cv2
    import numpy as np

    out = cv2.remap(img, np.asarray(xs, np.float32), np.asarray(ys, np.float32),
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out.reshape(img.shape)


def _line_blur(img, v, sigma: float):
    """`img` blurred by a 1D Gaussian of `sigma` px along `v`, per pixel:
    sampled every px, every 2 px from 4 px up, every 3 from 12, each sample
    taken as _LINE_SAMPLER says."""
    import numpy as np

    sample = _remapped if _LINE_SAMPLER == "remap" else _sampled
    reach = max(1, math.ceil(3 * sigma))
    step = 1 if sigma < 4 else 2 if sigma < 12 else 3
    ys, xs = np.mgrid[0:img.shape[0], 0:img.shape[1]].astype(np.float32)
    out, total = np.zeros_like(img), 0.0
    for t in range(-(reach // step) * step, reach + 1, step):
        w = math.exp(-0.5 * (t / sigma) ** 2)
        out += w * (img if t == 0 else sample(img, xs + t * v[0], ys + t * v[1]))
        total += w
    return out / total


def _bands(rgb, inside, k: float) -> list:
    """The RMS where `inside` of `rgb`'s bands (_BANDS), grey."""
    import numpy as np

    grey = rgb.mean(axis=2)
    levels = [grey] + [_gaussian(grey, s * k) for s in _BANDS[1:]]
    return [float(np.sqrt(((levels[j] - levels[j + 1])[inside] ** 2).mean()))
            for j in range(len(_BANDS) - 1)]


def smoothed(image, hair, blur: float, fine: float, gain=1.0):
    """`image` (PIL) smoothed in the body of `hair` (its hair mask): step 1
    an oriented blur of `blur` px along the flow, step 2 what it took
    finer than `fine` px put back, `gain` times over — or, where `gain` is
    a function (frizzCare_4's), the gain it returns, one or per pixel, with
    notes, given step 1's blur over the crop the blurs run over (float
    RGB), what step 2 would put back there at 1, the body there and the
    short side over 1024. Returns the result, step 1's blur and what step
    2 put back (float RGB, at the image's size, the last 0 off the body),
    the body (0–1), how many times over step 2 put the detail back at each
    pixel (the gain times the body) and notes: the gain (or the
    function's, any maps in them, per pixel over the crop, put on the
    image times the body as the gain is) and each band's RMS in the body
    over `image`'s."""
    import numpy as np

    short = min(image.size)
    k = short / 1024
    rgb = np.asarray(image.convert("RGB"), np.float32)
    body = _hair_body(hair, short)
    base, added = rgb.copy(), np.zeros_like(rgb)
    ys, xs = np.nonzero(body > 0)
    if not len(ys):
        return rgb, base, added, body, np.zeros_like(body), {}
    # The blurs run over the body's box and their reach round it.
    reach = math.ceil(6 * blur * k)
    box = (slice(max(0, ys.min() - reach), ys.max() + reach + 1),
           slice(max(0, xs.min() - reach), xs.max() + reach + 1))
    crop, b = rgb[box], body[box][..., None]
    along, across = _flow(crop.mean(axis=2), k)
    blurred = _line_blur(_line_blur(crop, across, blur * k / _ACROSS), along, blur * k)
    took = crop - blurred
    back = took - _gaussian(took, fine * k)
    inside = body[box] > 0.5
    if callable(gain):
        gain, notes = gain(blurred, back, body[box], k)
    else:
        notes = {"gain": round(gain, 3)}
    taken = np.zeros_like(body)
    taken[box] = gain * body[box]
    for name, m in list(notes.get("maps", {}).items()):
        notes["maps"][name] = np.zeros_like(body)
        notes["maps"][name][box] = m * body[box]
    back = back * (gain[..., None] if np.ndim(gain) else gain)
    base[box] = crop + (blurred - crop) * b
    added[box] = back * b
    out = rgb.copy()
    out[box] = base[box] + added[box]

    labels = [f"{lo:g}-{hi:g}" for lo, hi in zip(_BANDS, _BANDS[1:])]
    notes["bands"] = {label: round(o / r, 3) if r else None for label, o, r
                      in zip(labels, _bands(out[box], inside, k), _bands(crop, inside, k))}
    return out, base, added, body, taken, notes


@dataclass
class FrizzCareOptions:
    smooth_at: str = option(
        os.environ.get("HAIRFX_FRIZZ_SMOOTH_AT", "photo"),
        "aiHair: the size the render is smoothed at: the photo's (it grown to it first), or "
        "the render's own where that is smaller (~1.6 MP; the hair model and the smoothing "
        "run there, and the result is grown to the photo's as a render is); "
        "HAIRFX_FRIZZ_SMOOTH_AT sets the default",
        choices=("photo", "render"))
    blur: float = option(4.0, "frizzCare_3's -o blur: step 1's Gaussian along the hair's "
                         "flow, px on a 1024 px short side")
    fine: float = option(2.0, "frizzCare_3's -o fine: step 2 puts back the detail step 1 "
                         "took that is finer than this many px (frizzCare_3's 1 showed "
                         "aliasing in added_<i>)")
    window: float = option(16.0, "the fine detail is measured round each pixel over a "
                           "Gaussian of this many px (1024 px short side)")
    level: float = option(11.0, "the fine detail each pixel's gain brings what step 2 "
                          "puts back to round it (with -o blur_power over 0, the result's): "
                          "its RMS as a % of the hair's mean grey there")
    scale: float = option(1.13, "each pixel's gain that brings it to -o level, times this")
    min_gain: float = option(0.25, "the least gain step 2 takes, where the render has more "
                             "than -o level")
    max_gain: float = option(1.5, "the most gain step 2 takes, where the render is soft")
    back_power: float = option(1.0, "the gain's back term (the target over what step 2 puts "
                               "back) is raised to this")
    blur_power: float = option(0.0, "the gain's blur term (what step 1's blur left of the "
                               "target) is raised to this; 0 leaves it out (it took the "
                               "outlines of high-contrast curls for strands)")

    def __post_init__(self):
        for key in ("blur", "fine", "window", "level", "scale"):
            if getattr(self, key) <= 0:
                raise ValueError(f"option {key}: {getattr(self, key)} isn't over 0")
        for key in ("back_power", "blur_power"):
            if getattr(self, key) < 0:
                raise ValueError(f"option {key}: {getattr(self, key)} is under 0")
        if not 0 <= self.min_gain <= self.max_gain:
            raise ValueError(f"options min_gain, max_gain: {self.min_gain}, "
                             f"{self.max_gain} aren't 0 <= min_gain <= max_gain")


def _local_mean(a, body, sigma: float):
    """`a`'s mean round each pixel, over a Gaussian of `sigma` px weighted
    by `body`."""
    import numpy as np

    return _gaussian(body * a, sigma) / np.maximum(_gaussian(body, sigma), 1e-3)


def _spread(a, inside) -> dict:
    """`a`'s 10th percentile, median and 90th where `inside`."""
    import numpy as np

    return dict(zip(("p10", "median", "p90"),
                    (round(float(v), 3) for v in np.percentile(a[inside], (10, 50, 90)))))


def local_gain(blurred, back, body, k: float, opts: FrizzCareOptions):
    """Step 2's gain at each pixel of the crop: the g that brings the RMS
    round it of kept + g * fine — kept the fine detail step 1 left (the
    grey of `blurred`, its blur, less its Gaussian of -o fine px), fine
    that of `back` (what step 2 would put back at 1) — to -o level % of
    the hair's mean grey there, each over -o window px (times `k`, the
    short side over 1024) weighted by `body`; its two terms raised to -o
    back_power and -o blur_power, times -o scale, within -o min_gain and
    -o max_gain. Returns it and notes: the result's fine
    detail at 1 there (% of the mean grey), the gain and its two terms
    before -o scale (gain_back, the target over fine's RMS; gain_blur,
    the rest), each's _spread where the body is over half, and the terms
    per pixel (maps)."""
    import numpy as np

    grey = blurred.mean(axis=2)
    kept = grey - _gaussian(grey, opts.fine * k)
    fine = back.mean(axis=2)
    sigma = opts.window * k
    kk, ff, kf = (_local_mean(a, body, sigma) for a in (kept * kept, fine * fine, kept * fine))
    mean = np.maximum(_local_mean(grey, body, sigma), 1.0)
    target = opts.level / 100 * mean
    # ff g² + 2 kf g + kk = target²; where no gain reaches it (there is more
    # at any), the least.
    root = np.sqrt(np.maximum(kf * kf + ff * (target * target - kk), 0))
    g = (root - kf) / np.maximum(ff, 1e-6)
    # g is the back's term, the target over fine's RMS, times the blur's
    # (under 0 where the blur left more than the target: the least gain).
    by_back = target / np.sqrt(np.maximum(ff, 1e-6))
    terms = {"gain_back": by_back, "gain_blur": g / by_back}
    gain = np.clip(opts.scale * by_back ** opts.back_power
                   * np.maximum(terms["gain_blur"], 0) ** opts.blur_power,
                   opts.min_gain, opts.max_gain)
    inside = body > 0.5
    if not inside.any():
        return gain, {"maps": terms}
    at_one = 100 * np.sqrt(np.maximum(kk + ff + 2 * kf, 0)) / mean
    return gain, {"fine_level": _spread(at_one, inside), "gain": _spread(gain, inside),
                  **{key: _spread(a, inside) for key, a in terms.items()}, "maps": terms}


class FrizzCareFeature(SinglePromptFeature):
    """frizzCare: HairFeatures' frizzCare_4, merged with the frizzCare_3 it
    extended: frizzCare_2's render (_PROMPT), smoothed along the hair's flow
    in its body (step 1), the finest detail put back (step 2) with a gain
    set per pixel (local_gain); then colour-matched in the hair drawn in
    from its outline (hair_inset) and lined up; --blend pastes it through
    the hair."""

    Options = FrizzCareOptions
    colour_match = True
    colour_match_area = "hair_inset"
    blend_mask = "hair"

    def __init__(self):
        super().__init__("frizzCare", _PROMPT,
                         "frizzCare_2's render smoothed along the hair's flow in its body "
                         "(-o blur), the finest detail put back (-o fine) with step 2's gain set "
                         "per pixel, what it puts back round it brought to -o level % of the "
                         "hair's grey")

    def process(self, ctx, photo, opts, result) -> None:
        result.step = "edit"
        # smooth_at=render: the renders as drawn, each grown to the photo's
        # size after it is smoothed (smooth's fit_to).
        at_render = opts.smooth_at == "render" and not ctx.render.keep_rendered_size
        renders = edit(ctx, result, "edit", [photo.file], self.prompt, photo.size,
                       n=self.n_images(ctx.render),
                       keep_size=ctx.render.keep_rendered_size or at_render)
        self.smooth(ctx, renders, opts, result, fit_to=photo.size if at_render else None)

    def step2_gain(self, opts):
        """smoothed's gain: local_gain's, per pixel."""
        return lambda blurred, back, body, k: local_gain(blurred, back, body, k, opts)

    def smooth(self, ctx, renders, opts, result, fit_to=None) -> None:
        """Steps 2 and 3 on `renders`, frizzCare_2's, into `result`. With
        `fit_to` (the photo's size; renders as drawn), a render with fewer
        pixels than that is smoothed at its own size and the result grown to
        it (imaging.fit_image, as edit grows a render); any other is grown
        to it first."""
        grow = []
        if fit_to is not None:
            fitted = []
            for render in renders:
                small = render.width * render.height < fit_to[0] * fit_to[1]
                if not small:
                    render, warning = imaging.fit_image(render, fit_to)
                    if warning:
                        result.warnings.append(f"edit: {warning}")
                fitted.append(render)
                grow.append(small)
            renders = fitted
        result.step = "hair mask"
        hair = hairseg.hair_masks(renders, ctx.seg_python)

        result.step = "smooth"
        for i, (render, render_hair) in enumerate(zip(renders, hair)):
            result.keep(f"render_{i}", lambda: render)
            rgb, base, added, body, taken, notes = smoothed(
                render, render_hair, opts.blur, opts.fine, self.step2_gain(opts))
            # The steps and maps, only to be looked at (result.debug).
            result.keep(f"base_{i}", lambda: imaging.as_image(base))
            result.keep(f"added_{i}", lambda: imaging.as_image(128 + _SHOWN * added))
            result.keep(f"gain_{i}", lambda: imaging.as_image(
                taken * 255 / _GAIN_WHITE).convert("L"))
            maps = notes.pop("maps", {})
            for name in maps:
                result.keep(f"{name}_{i}", lambda: imaging.as_image(
                    maps[name] * 255 / _GAIN_WHITE).convert("L"))
            result.keep(f"body_{i}.jpg", lambda: imaging.as_image(
                imaging.tinted(render, body > 0.5)))
            del base, added, body, taken, maps
            result.info[f"result_{i}"] = notes
            output = imaging.as_image(rgb)
            del rgb
            if grow and grow[i]:
                output, warning = imaging.fit_image(output, fit_to)
                if warning:
                    result.warnings.append(f"edit: {warning}")
                notes["smoothed_at"] = list(render.size)
            result.outputs[f"result_{i}"] = output
            result.sheet.append(["input", f"render_{i}", f"base_{i}", f"added_{i}",
                                 f"result_{i}"])
