"""hairpart: HairFeatures' hairPart's render, finished as hairline's are.
Its switches: colour match on, over the whole picture; lined up --align
regions_flow; pasted into the photo through the hair_brow mask by default
(--no-blend keeps the renders whole, lined up).

hairPart (補髮縫): the scalp that shows along the parting filled with the
hair beside it, as 6_Hair part's Meitu results do.

Per photo:

  1. render: the photo and _PROMPT — single-prompt_HairPart.py's own
     skeleton with its CHANGE ONLY (_hair_part_prompt, _HAIR_PART_DIRECTION),
     as of 2026-10-30 — for --n-images images (result_<i>).
  2. colour (core._colour_match_outputs, area "whole"): one 3x3 colour
     matrix and offset fitted over the whole picture, on the 70% that
     differs least (imaging.whole_colour_matched): run_HairPart_samples.py's
     _colour_match, its after_correction/, which hairpart_composite.py
     composited from. Fitted on the hair alone (area "hair"), where the
     photo's thin hair shows much bright scalp and the render filled it in,
     the matrix's gain stretched the render's hair's contrast and turned
     the faint gaps it left into bright patches: the hair's lightness
     spread (p90-p10) x1.52 on female_batch02_351_FT, x1.29-1.32 on
     female_batchExpand04_066_FT_1/068_FT_1/068_FT_2; whole, x0.90-1.05 on
     all 44 photos of sample_for_HairPart and rst_pub_by_feature
     (output/hairpart_mask_compare, 2026-11-10).
  3. lining up (--align regions_flow, hairline's): region by region, then
     the face part by part and by optical flow (core._align_outputs).
  4. paste (blend hair_brow): the photo's and the render's hair, grown and
     feathered, and the forehead above the brows (FAN's), from the render;
     the face from the brows down, the body and the background the photo's.
     The forehead comes with the hair, so a filled parting runs on into it
     with no seam at the hairline; with no face found, the hair alone.
  5. mask check (HairPartFeature.finish): a render more than _MASK_FAIL of
     whose hair lies outside the photo's hair closed over _CLOSE is kept
     whole instead (hairpart_composite.py's --mask-fail): the photo's hair
     mask missed part of the head, and the paste would stop mid-parting.

Until 2026-11-10 its --blend (off by default) was hairpart_composite.py's
parting composite instead: the render taken only where it changed the photo
inside the photo's hair closed over 8% of the long side. It cut the render's
strands off at the hairline (subject25), and where the hair model missed
most of the head — a small top-down shot, female_batch08_261_FT_4: 6.8% of
the photo — kept almost nothing of the fill; hairpart_composite.py wrote the
render as is there (--mask-fail), as step 5 does. The old module is in
output/hairpart_mask_compare/backup_before_hairpart/.

Billed calls per photo: 1 render.
"""

from .. import imaging
from ..common import SinglePromptFeature
from ..core import hair_model, stage

# Step 5, hairpart_composite.py's --mask-fail and --close: a render more than
# _MASK_FAIL of whose hair (as lined up) lies outside the photo's hair closed
# over _CLOSE of the long side is kept whole instead of pasted, as there.
# On sample_for_HairPart's 21 photos, 3 renders each (2026-11-10), that was
# female_batch08_261_FT_4's three (42–62%: on its small top-down head the
# hair model found 6.8% of the photo); the other 20 photos' were 0–5%.
_MASK_FAIL = 0.15
_CLOSE = 0.08

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


class HairPartFeature(SinglePromptFeature):
    """hairpart: one render; colour-matched over the whole picture, lined up
    region by region and pasted into the photo through the hair_brow mask,
    by default (steps 2–4)."""

    default_n_images = 3  # the demos' r1–r3
    colour_match = True
    colour_match_area = "whole"
    blend_mask = "hair_brow"
    blend_default = True
    align_default = "regions_flow"

    def __init__(self):
        super().__init__("hairpart", _PROMPT)
        self.description = (
            "the scalp along the parting filled with the hair beside it (補髮縫): 1 render "
            "call per photo, colour-matched to the photo over the whole picture, lined up "
            "region by region and pasted into it through the hair and the forehead above the "
            "brows, the face below them kept the photo's (kept whole where the hair model "
            "missed part of the head)")

    def finish(self, ctx, photo, opts, result) -> None:
        """Feature.finish (steps 2–4), then step 5: each pasted output whose
        hair, as lined up, lies more than _MASK_FAIL outside the photo's
        hair closed over _CLOSE — the photo's hair mask missed part of the
        head, so the paste would stop mid-parting — is kept whole (lined up
        and colour-matched), with a warning; info["blend"][name] notes its
        share outside either way."""
        import numpy as np

        super().finish(ctx, photo, opts, result)
        notes = result.info.get("blend")
        names = [name for name in result.outputs if f"{name}_unblended" in result.intermediates]
        if not notes or not names:  # --no-blend, or the blend failed (a warning already)
            return
        with stage(result, "blend", f"hairpart mask check: an output with more than "
                                    f"{_MASK_FAIL:.0%} of its hair outside the photo's hair "
                                    f"closed over {_CLOSE:g} of the long side kept whole"):
            unblended = [result.intermediates[f"{name}_unblended"] for name in names]
            photo_hair, *hairs = hair_model(ctx, result, [photo.image, *unblended],
                                            f"the photo and {len(names)} output(s), as lined up")
            envelope = imaging.closed(photo_hair, max(1, round(_CLOSE * max(photo.size))))
            for name, image, hair in zip(names, unblended, hairs):
                outside = 1 - float((hair & envelope).sum()) / max(int(hair.sum()), 1)
                note = notes.setdefault(name, {})
                note["hair_outside_photo"] = round(outside, 3)
                if outside <= _MASK_FAIL:
                    continue
                note["kept_whole"] = True
                result.outputs[name] = image
                whole = np.ones(photo_hair.shape, bool)
                result.intermediates[f"blend_mask_{name}"] = imaging.mask_image(whole)
                result.intermediates[f"blend_{name}.jpg"] = imaging.as_image(
                    imaging.tinted(photo.image, whole))
                result.warnings.append(
                    f"{name}: {outside:.0%} of its hair is outside the photo's (the hair model "
                    f"missed part of the head): kept whole, not pasted")
