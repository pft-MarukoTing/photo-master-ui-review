"""An effect's output pasted into the photo through a blend mask (main.py
--blend, core's _blend_outputs): the output where the effect changes the
photo, the photo, pixel for pixel, everywhere else, so what the render
model redrew outside that area (skin, clothing, the background) doesn't
come through.

The masks, by the effect's blend_mask (features/__init__.py's
BLEND_MASKS):

  hair       the photo's hair and the output's, from the in-house hair
             model: the hair as it was and as the effect left it.
  hair_brow  hair, and the forehead above the brows (above_brows), where
             a hairline grows down onto it.
  hair_face  hair, and the face the effect drew as a mask of its own
             (intermediate face_mask: strayHairWithFace's step 1), where
             stray hair is taken off it.
  head       hair, and the face with its forehead (align.face_weight's
             outline and above_brows), where an effect redraws both:
             grown and feathered as shifted's composite does (its
             _zone_weight; not by --blend-grow) and pasted by its
             Laplacian pyramid (shifted._paste), so the render's skin
             tone meets the photo's neck and background over a wide band
             (hairline_simple_lv1 and _lv1.5 by default).

The hair and the face are grown by GROW of the photo's long side (main.py
--blend-grow), to take in the fine hairs round the hair's outline that the
hair model leaves out, and feathered by _FEATHER, so the seam fades over
about four times that. The halo of frizz round the outline reaches past
GROW: on subject0 frizzCare_2's render takes it off, and the hair mask
pastes it back. The forehead isn't grown: it fades out above the brows'
line, so the brows stay the photo's (a render of subject25 redrew the
whole face, and pasted down to the brows' mean height, its brows came
through)."""

MASKS = ("hair", "hair_brow", "hair_face", "head")
# The masks grown by GROW (main.py --blend-grow).
GROWN = ("hair", "hair_brow", "hair_face")
GROW = 0.006
_FEATHER = 0.003


def above_brows(face, hair, margin: float = 0.0):
    """The forehead above the brows, as a mask the size of `hair` (the
    photo's and the output's): between the temples (the jaw's ends, its
    landmarks 0 and 16), from the top of the hair in each column down to
    `margin` px above the lower brow's top (of the two, the one lower in the
    photo, each at its highest landmark). Not from the photo's top: that
    would paste the render's background above the head."""
    import numpy as np

    points = face["points"]
    level = max(points[17:22, 1].min(), points[22:27, 1].min()) - margin
    left, right = sorted((points[0, 0], points[16, 0]))
    height, width = hair.shape
    xs = np.arange(width) + 0.5
    rows = np.arange(height)[:, None]
    top = np.where(hair.any(axis=0), hair.argmax(axis=0), height)  # each column's top hair
    return (rows >= top) & (rows + 0.5 < level) & ((xs >= left) & (xs <= right))


def weight(kind: str, photo_hair, output_hair, face=None, face_mask=None, grow: float = GROW):
    """How much of the output each pixel takes, 0–1 (float), through blend
    mask `kind`: the photo's and the output's hair (`photo_hair`,
    `output_hair`), for hair_face with `face_mask`, grown by `grow` of the
    long side and feathered; for hair_brow with the forehead above the
    brows of `face` (the photo's landmarks), faded out above them. Also what
    the mask lacked — no face, no face mask — with the hair alone taken in
    its place, or None."""
    import numpy as np

    from . import imaging

    area, lacking, forehead = photo_hair | output_hair, None, None
    long_side = max(area.shape)
    feather = _FEATHER * long_side
    if kind == "hair_brow":
        if face is None:
            lacking = "no face found, so no forehead above the brows: the hair alone"
        else:
            # Its edge pulled up by twice the feather, so it has faded out by the brows.
            forehead = imaging.feathered(above_brows(face, area, 2 * feather), feather)
    elif kind == "hair_face":
        if face_mask is None:
            lacking = "no face_mask from the effect: the hair alone"
        else:
            area = area | face_mask
    elif kind == "head":
        # common's: it imports core, which imports this module.
        from . import align
        from .common import _zone_weight

        if face is None:
            lacking = "no face found: the hair alone"
        else:
            height, width = area.shape
            face_area = align.face_weight(face, (width, height))[0] > 0.5
            area = area | face_area | above_brows(face, area)
        return _zone_weight(area, long_side), lacking
    out = imaging.feathered(imaging.grown(area, grow * long_side), feather)
    return (out if forehead is None else np.maximum(out, forehead)), lacking


def paste(photo, output, weight, kind: str = "hair"):
    """`output` pasted into `photo` (same size) by `weight` (0–1); for the
    head mask by shifted's Laplacian pyramid."""
    import numpy as np

    from . import imaging

    if kind == "head":
        from .common import _paste

        return _paste(photo, output, None, None, weight)

    a = weight[..., None]
    return imaging.as_image(np.asarray(photo.convert("RGB"), np.float32) * (1 - a)
                            + np.asarray(output.convert("RGB"), np.float32) * a)
