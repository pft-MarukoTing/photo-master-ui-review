"""hairpart: HairFeatures' hairPart. Its switches, as HairFeatures set them:
colour match on, in the hair; lined up --align global; --blend by its own
parting composite, off by default.

--- HairFeatures' features/hair_part.py ---

hairPart (補髮縫): the scalp that shows along the parting filled with the
hair beside it, as 6_Hair part's Meitu results do. run_HairPart_samples.py's
render and colour match; with --blend, hairpart_composite.py's paste back
into the photo, which keeps the render only where it filled the parting.

Per photo:

  1. render: the photo and _PROMPT — single-prompt_HairPart.py's own
     skeleton with its CHANGE ONLY (_hair_part_prompt, _HAIR_PART_DIRECTION),
     as of 2026-10-30 — for --n-images images (result_<i>). Feature.run
     then pulls their hair's colours back to the photo's by one 3x3 colour
     matrix and offset (core._colour_match_outputs:
     run_HairPart_samples.py's _colour_match, its after_correction/, kept
     to the hair; --no-colour-match skips it) and, without --blend, lines
     them up with it as every render is.

With --blend, CompositeFeature.finish pastes them into the photo instead
(HairPartFeature.composite), as hairpart_composite.py does:

  2. hair masks and lining up (lined_up): the photo's hair and each
     render's, from the in-house hair model; each render lined up with the
     photo outside both, grown by _REGION_GROW (align.line_up), and its hair
     found again where it moved. hairpart_composite.py shifts the render by
     one translation found by phase correlation outside the hair
     (composite_common._shift_onto); line_up fits an affine transform to
     the shifts of patches there, the same way, so it stands in for it.
  3. the parting (_edit_area): where the render really changed the photo —
     blurred over _DETECT_BLUR, off by more than -o level in a channel — in
     the render's hair inside the photo's hair closed over _CLOSE
     (envelope.jpg), which bridges the parting and a bare crown but not the
     forehead or the background, so the outline and the hairline stay the
     photo's; closed and grown by _GROW, specks under _MIN_AREA of the photo
     dropped. gpt-image re-tones all the hair it returns, so only this is
     taken from the render.
  4. colour, locally (_colour_fixed): the render's low-frequency offset from
     the photo, measured on the photo's hair outside the parting
     (normalised convolution over _COLOUR), is taken out, so the fill
     matches the hair round it.
  5. paste: the render into the photo through the parting feathered over
     _FEATHER, and over _EDGE at the hair's edge (blend_<name>.jpg); the
     photo, pixel for pixel, everywhere else.

single-prompt_HairPart.py's own --composite hair pastes the render's whole
hair (the photo's hair mask closed over 3% and grown) instead of step 3's
parting; the demo (demo_hairpart.zip) is step 3's.

Billed calls per photo: 1 render.
"""

from dataclasses import dataclass

from .. import align, imaging
from ..common import CompositeFeature, _gaussian, _reach, lined_up
from ..core import option, stage


# hairpart_composite.py's defaults, as shares of the photo's long side but
# the level: the render counts as changed where off by more than _LEVEL
# (0–255, the largest of R, G and B) after a blur of _DETECT_BLUR; closed
# and grown by _GROW, specks under _MIN_AREA of the photo dropped; inside
# the photo's hair closed over _CLOSE; feathered by _FEATHER, and by _EDGE
# where it meets the hair's edge; its colour fixed over _COLOUR. Lining up
# leaves out the photo's and the render's hair grown by _REGION_GROW.
_LEVEL = 30.0

# hairpart_composite.py's defaults, as shares of the photo's long side but
# the level: the render counts as changed where off by more than _LEVEL
# (0–255, the largest of R, G and B) after a blur of _DETECT_BLUR; closed
# and grown by _GROW, specks under _MIN_AREA of the photo dropped; inside
# the photo's hair closed over _CLOSE; feathered by _FEATHER, and by _EDGE
# where it meets the hair's edge; its colour fixed over _COLOUR. Lining up
# leaves out the photo's and the render's hair grown by _REGION_GROW.
_DETECT_BLUR = 0.003
_MIN_AREA = 0.0005
_GROW = 0.01
_CLOSE = 0.08
_FEATHER = 0.012
_EDGE = 0.002
_COLOUR = 0.02
_REGION_GROW = 0.012

# single-prompt_HairPart.py's _HAIR_PART_DIRECTION, its default CHANGE ONLY.
_HAIR_PART_DIRECTION = (
    "Fill the scalp that shows along the parting with the neighbouring hair from both sides, so the parting becomes clearly narrower and no longer shows scalp. The added strands grow from the roots on both sides and follow the existing direction of the hair, matching the hair beside them in colour, strand thickness, texture and light. The parting's position and direction, the hairstyle's silhouette, the hair volume, the hairline, the bangs and all hair away from the parting stay exactly as they are."
)


def _hair_part_prompt(direction: str) -> str:
    """single-prompt_HairPart.py's _hair_part_prompt: its skeleton, editing
    only the parting strip, round `direction`."""
    return (
        "Edit ONLY the parting of the hair in this photo: fill the scalp that shows along the parting with hair, as described below, and leave every other pixel exactly as it is — every sticker, icon, emoji, button, text and watermark laid over the photo included, however small, faint or close to the edge. This is a local retouch of the narrow strip along the parting of an existing photo, not a new image. If the parting shows little or no scalp, change very little.\n"
        "\n"
        "CHANGE ONLY — the parting:\n"
        f"{direction}\n"
        "\n"
        "PRESERVE EXACTLY — do not change any of these in any way:\n"
        "- Face and identity: face shape, eyes, eyebrows, nose, lips, teeth, ears, expression, gaze and head pose.\n"
        "- Skin, forehead included: tone, brightness, redness, texture, pores, freckles, wrinkles, moles and makeup; no smoothing, retouching or beautifying.\n"
        "- The parting's place: it is filled where it is, not moved, straightened or redrawn, and no new parting is added.\n"
        "- All hair away from the parting: haircut, length, hairline, bangs, layers, face-framing strands, flyaways, volume, overall silhouette, curl or wave, colour, highlights and where its light and shadows fall stay exactly as in this photo.\n"
        "- Hair colour, everywhere: grey, white, silver, faded or dyed hair keeps its exact shade and brightness; no darkening, tinting, recolouring, colour correction or making it look fuller or richer.\n"
        "- The hair that fills the parting matches the hair right beside it in colour, strand thickness, direction, texture and light; no flat block, blur or smear.\n"
        "- Body, hands, clothing and accessories.\n"
        "- Background.\n"
        "- Every overlay on the photo is part of it and stays: stickers, emoji, small round icons and buttons, app UI elements, text, captions, watermarks, logos, graphics, borders, rounded corners, black bars and inset images. Keep each exactly where and as it is, at its size, colour and transparency, even when it is tiny, semi-transparent or sits in a corner or at the edge; do not remove, erase, clean up, crop or redraw it.\n"
        "- Light and colour outside the parting: light direction, shadows, exposure, white balance, contrast, sharpness, grain, compression texture and depth of field; no denoising, sharpening or re-drawn textures.\n"
        "- Framing: no zoom, crop, shift, rotation or reframing; outside the parting the output lines up pixel-for-pixel with this photo.\n"
        "\n"
        "OUTPUT: one photorealistic photo identical to this photo outside the parting, its overlays included — add no new collage, border, text or watermark."
    )


_PROMPT = _hair_part_prompt(_HAIR_PART_DIRECTION)


@dataclass
class HairPartOptions:
    level: float = option(_LEVEL, "with --blend, how far off the photo (0–255, in a channel, "
                          "after a light blur) the render has to be for a pixel to count as "
                          "filled; lower takes in more of the hair the render only re-toned")

    def __post_init__(self):
        if not 0 < self.level < 255:
            raise ValueError(f"option level: {self.level} isn't between 0 and 255")


def _edit_area(photo, render, inner, long_side: int, level: float):
    """Step 3: where `render` changed `photo` (float RGB) in `inner`: off by
    more than `level` in a channel after a blur of _DETECT_BLUR, closed over
    _GROW, specks under _MIN_AREA of the photo dropped, grown by _GROW."""
    import numpy as np

    sigma = max(1.0, _DETECT_BLUR * long_side)
    changed = np.zeros(inner.shape, bool)
    near = imaging.box(inner, _reach(sigma))
    if near is None:
        return changed
    changed[near] = np.abs(_gaussian(photo[near], sigma)
                           - _gaussian(render[near], sigma)).max(axis=2) > level
    grow = max(1, round(_GROW * long_side))
    area = imaging.closed(changed & inner, grow)
    labels, sizes = imaging.components(area)
    keep = sizes >= _MIN_AREA * area.size
    keep[0] = False
    return imaging.dilated(keep[labels], grow) & inner


def _colour_fixed(photo, render, ref, sigma: float, mean):
    """Step 4: `render` (float RGB) plus its offset from `photo`, blurred
    over the `ref` pixels only (normalised convolution, a Gaussian of
    `sigma`); `mean`, the offset over the whole photo's ref (these may be a
    crop of it), where no ref pixel is near."""
    import numpy as np

    m = ref.astype(np.float32)[..., None]
    eps = 1e-3
    num = _gaussian((photo - render) * m, sigma) + eps * mean
    den = _gaussian(m, sigma) + eps
    return render + num / den


def _paste(photo, render, photo_hair, render_hair, envelope, level: float):
    """Steps 3–5 for one render, lined up with `photo` (PIL): the result and
    the weight the render was pasted by (0–1)."""
    import numpy as np

    p = np.asarray(photo.convert("RGB"), np.float32)
    r = np.asarray(render.convert("RGB"), np.float32)
    long_side = max(photo.size)
    inner = render_hair & envelope
    area = _edit_area(p, r, inner, long_side, level)
    weight = np.zeros(area.shape, np.float32)
    out = p.copy()
    if area.any():
        feather = max(1.0, _FEATHER * long_side / 2)
        colour = max(1.0, _COLOUR * long_side)
        edge = max(1.0, _EDGE * long_side)
        ref = photo_hair & ~area
        mean = (p[ref] - r[ref]).mean(axis=0) if ref.any() else np.zeros(3, np.float32)
        # The weight is 0 past the feather's reach of the area, so the blurs
        # run over that box and their own reach round it only.
        box = imaging.box(area, _reach(feather))
        src = imaging.box(area, _reach(feather) + _reach(max(feather, colour, edge)))
        inside = tuple(slice(b.start - a.start, b.stop - a.start) for a, b in zip(src, box))
        wt = (_gaussian(area[src].astype(np.float32), feather)
              * _gaussian(inner[src].astype(np.float32), edge))
        weight[box] = wt[inside]
        fixed = _colour_fixed(p[src], r[src], ref[src], colour, mean)[inside]
        w = weight[box][..., None]
        out[box] = fixed * w + p[box] * (1 - w)
    return imaging.as_image(out), weight


class HairPartFeature(CompositeFeature):
    description = ("the scalp along the parting filled with the hair beside it (補髮縫): 1 "
                   "render call per photo, colour-matched to the photo; with --blend, pasted "
                   "into it only where it filled the parting")
    Options = HairPartOptions
    colour_match = True
    blend_mask = "parting"

    def __init__(self):
        super().__init__("hairpart")

    def prompts(self, opts) -> dict[str, str]:
        return {"edit": _PROMPT}

    def composite(self, ctx, photo, opts, result) -> dict:
        """Steps 2–5."""
        photo_hair, lined = lined_up(ctx, photo, result.outputs, _REGION_GROW, result)
        envelope = imaging.closed(photo_hair, round(_CLOSE * max(photo.size)))
        result.intermediates["envelope.jpg"] = imaging.as_image(
            imaging.tinted(photo.image, envelope))
        pasted = {}
        for name, (render, render_hair) in lined.items():
            with stage(result, "blend", "hairpart._paste: the render taken only where it "
                                        "filled the parting, its colour fixed to the hair "
                                        "round it", name=f"paste {name}"):
                image, weight = _paste(photo.image, render, photo_hair, render_hair, envelope,
                                       opts.level)
            # What the paste dropped: the photo's hair kept where the render
            # had re-toned or moved it (hairpart_composite.py's
            # hair_off_pct_before).
            kept = photo_hair & (weight < 0.01)
            off = align._difference(photo.image.convert("RGB"), render)[kept] > align._DRIFT_LEVEL
            pasted[name] = (image, weight, {
                "filled": round(float((weight >= 0.5).mean()), 4),
                "hair_redrawn_dropped": round(float(off.mean()), 4) if kept.any() else 0.0,
            })
        return pasted
