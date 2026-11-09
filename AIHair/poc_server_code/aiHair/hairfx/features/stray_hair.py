"""strayHair: HairFeatures' strayHairWithFace, step 3's mask the face (its
_1's and _2's other masks left out). Its switches, as HairFeatures set
them: colour match on, in the hair; lined up --align global; --blend by
shifted_composite through the face it drew (hairfx/common.py), off by
default.

--- HairFeatures' features/stray_hair_with_face.py ---

strayHairWithFace: strayHair with the face found first. gpt-image draws
the photo's face as a mask, and the face dilated goes to gpt-image with the
photo, as a second image, for it to take the stray hair off in.

strayHair_use_mask.py's steps 1–2 (the face mask) with
strayHair_use_mask_gen_only.py's render (the mask as a second image):

  1. The render model draws the photo's face as a black-and-white mask
     (_FACE_PROMPT; intermediate face_render, at the photo's size): the
     face white, taking in any strands lying across it, so the mask follows
     the face's own outline, and everything else black. Its white pixels,
     tidied, are the face (face_mask). A render that isn't about black and
     white, or has no white, fails the photo.
  2. The face is dilated by about `grow` of its width (mask, drawn on the
     photo in overlay.jpg): out past its edge, to take in the strands along
     it, and over any hole the paint left at a strand.
  3. The render model is sent the photo as it is, step 2's mask after it as
     a second image, and _STRAY_PROMPT, for --n-images images (result_<i>).
     The mask goes as a second image, not as images.edit's mask: gpt-image
     blanks the area that mask leaves transparent before the model sees the
     photo, so the model drew the face black, or a new face, instead of
     taking the strands off it. As a second image it only guides the model,
     which sees the face whole, and may change pixels outside it too.

With --blend, Composited.finish colour-matches the renders and pastes them
into the photo (StrayHairWithFace.composite: shifted.shifted_composite,
which rootRetouch_1's and grayHairTwoStep's share), not lining them up
first:

  4. lining up by region (region_align.shifted): the face (face_mask), the
     hair (the photo's and the render's) and the rest each moved by one
     translation, ECC's, the hair's and the rest's fitted outside step 2's
     mask; a region no shift brings closer to the photo stays where it is
     (regions.jpg; <name>_lined_up where it moved).
  5. paste: through face_mask grown by fluffy's _ZONE_GROW and feathered by
     _ZONE_FEATHER of the long side, not cut to the hair; the render's hair
     pulled to the photo's near it (_matched_hair: fluffy's -o match_hair,
     the face left as the render drew it), then fluffy's -o only_changed
     pyramid paste.

strayHairWithFace_1 and _2 send step 3 another mask, and a _STRAY_PROMPT
that says what its white area is (_MASK_TEXT):

  _1  only the hair in step 2's dilated face: the face cut down to the
      hair mask (hair_mask; -o hair_source: the in-house hair model, or
      the render model painting the hair green, as hairline's step 2), so
      the model is shown the strands themselves. A photo with no hair in
      or along its face is skipped.
  _2  the face less its features: -o grow is 0 by default, so the face is
      step 1's as drawn; the render model draws the eyebrows, eyes, nose
      and mouth as a second black-and-white mask (_FEATURES_PROMPT;
      features_render, features_mask), and they are taken out of it (grown
      by -o features_grow of the face's width first), so the model is
      shown only the face's skin.

aiHair: step 1's face is, by default (-o face_source=skinmap), not drawn by
gpt-image but made from the skin-map model's skin (hairfx/skinmap.py:
WrinkleWorseEmulation's skin map, on the face FAN's 68 landmarks align) and
the face those landmarks outline, their convex hull (skinmap.face), for no
billed call; intermediates skinmap_prob, skinmap_crop.jpg (what the model
saw) and skinmap_face.jpg (the skin kept tinted, the landmarks' face in
yellow, the face in cyan). The skin alone leaves out the strands across
the face, and wanted growing twice as far; with the landmarks' face and the
hull it takes in about 98% of gpt-image's face (rst_pub_by_feature's 19
photos with one, 2026-11-09), so it is grown as gpt-image's is (-o grow).
Where the face model or the skin map fails (a face in profile, whose eyes
FAN finds too close together for the skin map's crop), step 1 falls back to
gpt-image's face, with a warning. -o face_source=gpt is HairFeatures' step
1. record.json's info.face_mask_model says which made face_mask.

Billed calls: one render per photo, step 3's (whose cost grows with
--n-images), and none with -o analyze_only=true, which stops after step 2;
-o face_source=gpt (or its fallback) adds step 1's. HairFeatures': two
renders per photo, step 1's and step 3's. _1 the same, and a third render
with -o hair_source=gpt; _2 three, with the features' render.
"""

from dataclasses import dataclass

from .. import imaging, landmarks, skinmap
from ..common import (_LOCAL_SIZE, _MATCH_EDGE, Composited, _gaussian, _hair_pulled,
                      shifted_composite)
from ..core import edit, option


# Steps 1 and 2 (_2's features): a render's pixel is white where its grey is
# over half. The render counts as a mask only where at least _FACE_BW_MIN of
# its pixels are about black or white (within _FACE_BW_TOLERANCE), as a
# render that drew the photo instead would threshold into noise. The white
# is tidied with a blur of this share of the photo's long side, thresholded
# at half, which drops specks and fills pinholes.
_FACE_BW_MIN = 0.90

# Steps 1 and 2 (_2's features): a render's pixel is white where its grey is
# over half. The render counts as a mask only where at least _FACE_BW_MIN of
# its pixels are about black or white (within _FACE_BW_TOLERANCE), as a
# render that drew the photo instead would threshold into noise. The white
# is tidied with a blur of this share of the photo's long side, thresholded
# at half, which drops specks and fills pinholes.
_FACE_BW_TOLERANCE = 40
_FACE_TIDY = 0.0015

# Step 2: the face is dilated by about this share of its width, which is
# measured between these percentiles of its pixels' columns, so a speck of
# paint off the face doesn't widen it.
_DEFAULT_GROW = 0.10

# Step 2: the face is dilated by about this share of its width, which is
# measured between these percentiles of its pixels' columns, so a speck of
# paint off the face doesn't widen it.
_WIDTH_PERCENTILES = (2, 98)

# Step 1: sent to the render model with the photo. The face takes in the strands
# lying across it, which are what step 3 takes off, so the mask holds them;
# bangs and face-framing locks are the hairstyle, which strayHair keeps, so
# they are left out.
_FACE_PROMPT = (
    "Make a black-and-white segmentation mask of the person's face in this photo: the face pure "
    "white (#FFFFFF) and everything else pure black (#000000). The mask has the same size and "
    "framing as this photo, so it lines up with it pixel-for-pixel.\n"
    "\n"
    "WHITE — the whole face, as one solid area:\n"
    "- From the hairline down to the chin and jaw, and from one side of the face to the other: "
    "the forehead, temples, eyebrows, eyes, nose, cheeks, mouth, lips and chin, with any beard, "
    "moustache or glasses on it.\n"
    "- Straight over any loose strands of hair lying across the face: the face under them is "
    "white as if they weren't there, so the mask follows the face's own outline.\n"
    "- Its edges exactly where the face ends: along the hairline, the sides of the face and the "
    "jaw.\n"
    "- Where there are several people, only the main person's face.\n"
    "\n"
    "BLACK — everything else:\n"
    "- The hair of the hairstyle: the hair on the head, bangs or fringe, and face-framing locks.\n"
    "- Ears, neck, body, hands, clothing, accessories and hats.\n"
    "- The background.\n"
    "\n"
    "OUTPUT: only the mask — flat pure white and pure black, with no grey, shading, outline or "
    "any of the photo's detail; no zoom, crop, shift, rotation or reframing; no collage, border, "
    "text or watermark."
)

# Sent to the render model with the photo and its mask, in that order. Stray hair is
# what strayHair takes off; bangs and face-framing locks are the hairstyle,
# which it keeps. On 2026-10-29 the face's features got what of each stays
# (position, size, shape, colour), where they were only named; the ears left
# to the black area, where the face mask puts them. What the mask's white
# area is, where the strands to take off lie, and what is kept in the white
# and black areas come from the effect's mask (_MASK_TEXT).
_STRAY_TEMPLATE = (
    "Two images are given. The first is the photo to edit. The second is a black-and-white mask "
    "with the same size and framing as the photo: its white area is {white}, where the stray "
    "hair is to be removed, and its black area is everything else. Remove the stray hair in the "
    "photo inside the mask's white area, and nothing else: the stray strands are the only thing "
    "that changes, and every other pixel of the photo, in the black area and in the white area "
    "alike, stays identical to the first image. This is a local retouch of the existing photo, "
    "not a new image: do not redraw, re-render or regenerate it.\n"
    "\n"
    "INSIDE THE MASK'S WHITE AREA — remove the stray hair:\n"
    "- Remove loose, stray strands and flyaways lying across {across}.\n"
    "- Where a strand is removed, show the skin under it, matching the skin around it in tone, "
    "texture, pores and light; only the strand's own narrow track changes, not the skin around "
    "it.\n"
    "- Where it is unclear whether a strand is stray or part of the hairstyle, leave it as it "
    "is.\n"
    "\n"
    "INSIDE THE MASK'S WHITE AREA — keep as it is, apart from the strands removed:\n"
    "{keep}"
    "\n"
    "IN THE MASK'S BLACK AREA — preserve exactly, every pixel as it is in the first image; "
    "nothing here changes in any way:\n"
    "{black}"
    "\n"
    "THE WHOLE PHOTO — preserve exactly:\n"
    "- Light and colour: light direction, shadows, highlights, exposure, white balance, "
    "contrast and saturation; no relighting or colour grading.\n"
    "- Image quality: sharpness, focus, depth of field, grain, noise and compression texture; "
    "no denoising, sharpening, upscaling or re-drawn textures.\n"
    "- Framing and geometry: the same composition, the person in the same place, size and "
    "pose; no zoom, crop, shift, rotation, warp or reframing. Outside the removed strands, the "
    "output lines up with the first image pixel-for-pixel.\n"
    "\n"
    "OUTPUT: only the edited photo, identical to the first image except that the stray hair "
    "inside the mask's white area is removed. The mask itself never shows in it: no white or "
    "black area, outline or tint where the mask is; no collage, border, text or watermark."
)

# _STRAY_TEMPLATE's lines, kept in the white or the black area by mask.
_IDENTITY = ("- The face and identity: the same person, with the same face shape, jaw and chin, "
             "expression, gaze and head pose")

# _STRAY_TEMPLATE's lines, kept in the white or the black area by mask.
_FACE = (_IDENTITY + "; the eyes, eyebrows, nose, lips and teeth each keep their exact "
         "position, size, shape and colour and the spacing between them — not redrawn, moved, "
         "resized, reshaped or made more symmetrical.\n")
_SKIN = ("- The skin: tone, brightness, redness, texture, pores, freckles, wrinkles, moles, "
         "blemishes and makeup; no smoothing, retouching or beautifying.\n")
_HAIRSTYLE = ("- The hairstyle: the hairline, bangs or fringe and face-framing locks, with their "
              "shape, texture and colour.\n")
_HAIR = ("- The hair: every strand of the main hair mass and its outline, and every flyaway "
         "along the outer silhouette or over the shoulders and background, in the same place, "
         "with the same texture, strand separation, curl or wave, volume, sheen and colour. "
         "Stray hair is removed only inside the white area, never here.\n")
_BODY = "- Ears, neck, body, hands, clothing, jewellery and accessories.\n"
_BACKGROUND = ("- The background, and any text, stickers, graphics, watermarks, borders, black "
               "bars or inset images; do not remove them.\n")
_ACROSS = ("the forehead, temples, eyebrows, eyes, nose, cheeks, mouth and chin, and along the "
           "edge of the face")


@dataclass
class StrayHairOptions:
    grow: float = option(_DEFAULT_GROW, "how far step 2 dilates the face out past its edge, "
                                        "as a share of its width")
    analyze_only: bool = option(False, "stop after step 2: the face and its mask, no step 3 "
                                       "render")
    face_source: str = option("skinmap", "where step 1's face comes from: skinmap, the "
                                         "skin-map model's skin and FAN's landmarks' face "
                                         "(hairfx/skinmap.py; no billed call, gpt where it "
                                         "fails); gpt, the render model drawing it as a mask "
                                         "(a billed call)",
                              choices=("skinmap", "gpt"))

    def __post_init__(self):
        if self.grow < 0:
            raise ValueError(f"option grow must be 0 or more; got {self.grow}")


def _white(render, size: tuple[int, int]):
    """A mask render's white pixels (grey over half), tidied by _FACE_TIDY
    of the photo's long side — empty where there is no white — and the
    share of the render that is about black or white (within
    _FACE_BW_TOLERANCE)."""
    import numpy as np

    grey = np.asarray(render.convert("L"), np.int16)
    bw = float(((grey <= _FACE_BW_TOLERANCE) | (grey >= 255 - _FACE_BW_TOLERANCE)).mean())
    return imaging.tidied(grey >= 128, _FACE_TIDY * max(size)), bw


def _drawn_mask(ctx, result, photo, step: str, prompt: str):
    """The white of a black-and-white mask the render model draws of `photo`
    with `prompt` (intermediate <step>_render, at the photo's size). Raises
    where the render isn't about black and white (_FACE_BW_MIN), or has no
    white."""
    render = edit(ctx, result, step, [photo.file], prompt, photo.size)[0]
    result.intermediates[f"{step}_render"] = render
    region, bw = _white(render, photo.size)
    result.info[f"{step}_bw_share"] = bw
    if bw < _FACE_BW_MIN:
        raise RuntimeError(f"{ctx.render.model} drew no black-and-white {step} mask, only "
                           f"{bw:.0%} of it black or white")
    if not region.any():
        raise RuntimeError(f"{ctx.render.model}'s {step} mask has no white in it")
    return region


def _skin_face(ctx, result, photo):
    """-o face_source=skinmap's step 1: the face's skin from the skin-map model
    (hairfx/skinmap.py) on the face FAN's 68 landmarks align, tidied as a
    drawn mask is (_FACE_TIDY), made the whole face with the face the
    landmarks outline (skinmap.face); intermediates skinmap_prob (its
    probability on the photo), skinmap_crop.jpg (the aligned crop the model
    saw, the skin tinted) and skinmap_face.jpg (the skin kept tinted, the
    landmarks' face outlined in yellow, the face in cyan). Raises where FAN
    finds no face, or the model no skin."""
    import numpy as np

    found = landmarks.face_landmarks([photo.image], ctx.face_python)[0]
    if found is None:
        raise RuntimeError("no face found for the skin map")
    result.info["face"] = {"score": round(found["score"], 4),
                           "bbox": [round(v, 1) for v in found["bbox"]]}
    skin, prob, crop, crop_prob = skinmap.skin_map(photo.image, found["points"])
    result.intermediates["skinmap_prob"] = imaging.as_image(
        np.dstack([np.round(prob * 255).astype(np.uint8)] * 3))
    result.intermediates["skinmap_crop.jpg"] = imaging.as_image(
        imaging.tinted(imaging.as_image(crop), crop_prob >= skinmap.THRESHOLD))
    skin = imaging.tidied(skin, _FACE_TIDY * max(photo.size))
    if not skin.any():
        raise RuntimeError("the skin-map model found no skin")
    face, outline, kept = skinmap.face(skin, found["points"])
    result.info["skin_share_of_face"] = float((kept & face).sum() / max(1, face.sum()))
    drawn = imaging.tinted(photo.image, kept)
    width = max(2, round(0.003 * max(photo.size)))
    for region, colour in ((outline, (255, 220, 0)), (face, (40, 200, 255))):
        drawn[_edge(region, width)] = colour
    result.intermediates["skinmap_face.jpg"] = imaging.as_image(drawn)
    return face


def _edge(region, width: int):
    """`region`'s pixels within about `width` of its edge."""
    import numpy as np
    from PIL import ImageFilter

    inner = np.asarray(imaging.mask_image(region).filter(ImageFilter.GaussianBlur(width / 2))) >= 251
    return region & ~inner


def _face_width(face) -> float:
    """The face's width in px: between _WIDTH_PERCENTILES of its pixels'
    columns."""
    import numpy as np

    low, high = np.percentile(np.nonzero(face)[1], _WIDTH_PERCENTILES)
    return float(high - low)


def _overlay(upright, region, face):
    """Step 2 drawn on the photo, for the sheet: the mask in red, and the
    face step 1 found outlined in cyan."""
    tinted = imaging.tinted(upright, region)
    # The outline: the face's pixels within about `width` of its edge.
    tinted[_edge(face, max(2, round(0.003 * max(upright.size))))] = (40, 200, 255)
    return imaging.as_image(tinted)


def _mask_image(region) -> tuple[str, bytes, str]:
    """`region` as images.edit's second image: an RGB PNG the photo's size,
    pure white over `region`, where the model may change the photo, and
    pure black elsewhere."""
    return imaging.edit_file(imaging.mask_image(region).convert("RGB"), "mask.png")


# With --blend (StrayHairWithFace.composite): the render's hair pulled to
# the photo's (fluffy's -o match_hair) where the photo's hair, blurred as
# fluffy._local_stats blurs it, covers at least _NEAR_HAIR, about 1.3 sigma
# off its edge, fading to none about 3 sigma off; further out the photo has
# no hair to pull it to, and those stats go to 0 (Lab a and b of 0 on
# OpenCV's scale, deep blue: subject22's jaw, which the render's hair mask
# took in and the photo's didn't, came out a blue blot).
_NEAR_HAIR = 0.1


def _near_hair(photo_hair, sigma: float):
    """How near the photo's hair each pixel is, 0–1: its mask blurred over
    `sigma` at a quarter of the size, as fluffy._local_stats weighs it, over
    _NEAR_HAIR taken as 1."""
    import numpy as np
    from PIL import Image

    height, width = photo_hair.shape
    small = (max(1, width // 4), max(1, height // 4))
    m = np.asarray(Image.fromarray(photo_hair.astype(np.float32))
                   .resize(small, Image.Resampling.BOX))
    m = _gaussian(m, max(1.0, sigma / 4))
    m = np.asarray(Image.fromarray(np.ascontiguousarray(m, np.float32))
                   .resize((width, height), Image.Resampling.BILINEAR))
    return np.clip(m / _NEAR_HAIR, 0, 1)


def _matched_hair(photo, render, photo_hair, render_hair, long_side: int):
    """fluffy's -o match_hair for a paste that takes in the face: the
    render's (float RGB) hair pulled to the photo's nearby and its strands
    sharpened (fluffy._hair_pulled), near the photo's hair only
    (_near_hair), through the render's hair feathered by fluffy's
    _MATCH_EDGE; the rest, the face with it, as the render drew it, where
    fluffy._match_hair moves it to the photo's colour nearby, for the
    background round the crown — the face kept the render's shapes in the
    photo's skin tone and lip colour."""
    import numpy as np

    if (photo_hair & render_hair).sum() < 100:
        return render
    near = _near_hair(photo_hair, _LOCAL_SIZE * long_side)[..., None]
    pulled = _hair_pulled(photo, render, photo_hair, render_hair, long_side, near)
    a = _gaussian(render_hair.astype(np.float32), max(1.0, _MATCH_EDGE * long_side))[..., None]
    return np.clip(pulled * a + render * (1 - a), 0, 255)


# The stray-hair prompt with its mask the face (HairFeatures'
# _MASK_TEXT["face"]; its "hair" and "skin" masks, strayHairWithFace_1's
# and _2's, are left out).
_STRAY_PROMPT = _STRAY_TEMPLATE.format(
    white="the person's face and a narrow band around it", across=_ACROSS,
    keep=_FACE + _SKIN + _HAIRSTYLE, black=_HAIR + _BODY + _BACKGROUND)


class StrayHairFeature(Composited):
    """strayHair: HairFeatures' strayHairWithFace, step 3's mask the face
    dilated."""

    Options = StrayHairOptions
    default_n_images = 4  # strayHair_use_mask_gen_only.py's
    colour_match = True
    blend_mask = "face"

    def __init__(self):
        super().__init__("strayHair")
        self.description = ("strayHair inside the face: the face from the skin-map model and "
                            "FAN's landmarks (-o face_source=gpt: gpt-image draws it as a "
                            "mask), dilated it goes with the photo as a second image; 1 render "
                            "call per photo (2 with gpt's face)")

    def prompts(self, opts) -> dict[str, str]:
        out = {"face": _FACE_PROMPT} if opts.face_source == "gpt" else {}
        if not opts.analyze_only:
            out["stray"] = _STRAY_PROMPT
        return out

    def process(self, ctx, photo, opts, result) -> None:
        size = photo.size
        n = self.n_images(ctx.render)
        result.sheet = [["input", "overlay.jpg", *(f"result_{i}" for i in range(n))]]

        result.step = "step 1 (face)"
        source = opts.face_source
        if source == "skinmap":
            try:
                face = _skin_face(ctx, result, photo)
                result.info["face_mask_model"] = (f"face-model ({skinmap.SKIN_MAP_MODEL.name}, "
                                                  "aligned by FAN's 68 landmarks)")
            except Exception as e:  # noqa: BLE001 — gpt-image's face instead
                result.warnings.append(f"skin-map face failed — {type(e).__name__}: {e}; "
                                       f"the face drawn by {ctx.render.model} instead")
                source = "gpt"
        if source == "gpt":
            face = _drawn_mask(ctx, result, photo, "face", _FACE_PROMPT)
            result.info["face_mask_model"] = (f"GPT ({ctx.render.model})"
                                              + (" (skin map's fallback)"
                                                 if opts.face_source != "gpt" else ""))
        result.info["face_share"] = float(face.mean())
        result.intermediates["face_mask"] = imaging.mask_image(face)

        result.step = "step 2 (mask)"
        result.info["face_width_px"] = width = _face_width(face)
        # strayHair_use_mask.py's _dilated(face, grow * width): a Gaussian of
        # half the distance reaches about the distance past the edge. With
        # grow 0, the face as drawn.
        region = imaging.grown(face, opts.grow * width / 2) if opts.grow > 0 else face
        result.info["mask_share"] = float(region.mean())
        result.intermediates["mask"] = imaging.mask_image(region)
        result.intermediates["overlay.jpg"] = _overlay(photo.image, region, face)
        if not region.any():
            raise RuntimeError("the features cover the whole face: no skin left in the mask")
        if opts.analyze_only:
            return

        result.step = "step 3 (stray hair)"
        renders = edit(ctx, result, "stray", [photo.file, _mask_image(region)],
                       _STRAY_PROMPT, size, n=n, keep_size=ctx.render.keep_rendered_size)
        for i, image in enumerate(renders):
            result.outputs[f"result_{i}"] = image

    def composite(self, ctx, photo, opts, result) -> dict:
        """Steps 4 and 5, with --blend (shifted_composite)."""
        if "face_mask" not in result.intermediates:
            raise RuntimeError("no face_mask from step 1 to paste through")
        face = imaging.region(result.intermediates["face_mask"])
        # Step 2's mask, where the render was asked to change the photo.
        width = _face_width(face)
        region = imaging.grown(face, opts.grow * width / 2) if opts.grow > 0 else face
        return shifted_composite(ctx, photo, result, face, region, lambda *_: face,
                                 _matched_hair)
