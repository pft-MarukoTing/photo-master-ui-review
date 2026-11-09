"""fluffy: HairFeatures' fluffy. Its switches, as HairFeatures set them:
colour match on, in the hair; lined up --align global; --blend by its own
crown composite, off by default. What it shares with grayHair, rootRetouch
and strayHair (the face lock, the pyramid blend, the Lab colour helpers) is
in hairfx/common.py.

--- HairFeatures' features/fluffy.py ---

fluffy (蓬鬆): the roots on top of the head lifted off the scalp, so the
crown sits a little higher and rounder, as 3_Root Fluffy (髮根蓬鬆)'s Meitu
results do. run_Fluffy_samples.py's render and colour match; with --blend,
fluffy_composite.py's composite back into the photo, the render taken only
on top of the head and the face kept the photo's.

Per photo:

  1. render: the photo and the prompt — single-prompt_Fluffy.py's skeleton
     (_fluffy_prompt) round its CHANGE ONLY in English or Chinese (-o
     language), as of 2026-10-30 — for --n-images images (result_<i>).
     Feature.run then pulls their hair's colours back to the photo's by one
     3x3 colour matrix and offset (core._colour_match_outputs:
     run_Fluffy_samples.py's _colour_match, its after_correction/, kept to
     the hair; --no-colour-match skips it) and, without --blend, lines
     them up with it as every render is.

With --blend, hair_part.CompositeFeature.finish pastes them into the photo
instead (FluffyFeature.composite), as fluffy_composite.py does:

  2. face (-o align_regions, -o roots_only, -o lock_face, -o whole_cap):
     FAN's 68 landmarks of the photo (core._find_face; face.jpg).
     fluffy_composite.py finds them with the in-house 92-point model; its
     eyes, brows, jaw and chin are FAN's 36–47, 17–26, 0–16 and 8 here. With
     no face found, step 3's lining up by region and step 4's root zone, cap
     and face lock are left out (a warning), as there.
  3. hair masks and lining up: hairPart's (hair_part.lined_up). With -o
     align_regions, each render whose face FAN finds is lined up region by
     region first (region_align.lined_up, OpenCV in --face-python): the face
     by a similarity transform of the landmarks, the hair by optical flow,
     the rest by ECC; in the root zone from the eyes up (_ALIGN_ROOT_START,
     _ALIGN_ROOT_RAMP) the hair's flow fades out over _ALIGN_BAND x D below
     the photo's hair top, so the lift stays and only the drift is taken
     out. The rest are lined up as hairPart's are, outside the photo's and
     the render's hair grown by _GROW: fluffy_composite.py's one
     translation by phase correlation there (_shift_onto), which
     align.line_up's affine fit stands in for. Its --morph, --warp-photo,
     --close-parting and --roots-from-drift aren't here.
  4. paste: through the photo's and the render's hair grown by _GROW and
     feathered by _FEATHER, times
       - with -o only_changed, where the render reshaped the hair
         (_edit_zone: its coarse lightness off the photo's, and the hair it
         newly covers), the photo's own hair kept elsewhere; with -o
         whole_cap also all the hair in the cap, from the top of the brows
         up (_CAP_START, _CAP_RAMP), so the lift runs on from the roots in
         front; pasted by a Laplacian pyramid (_pyramid_blend), so colour
         blends over a wide band and strands over a narrow one;
       - with -o roots_only, the root zone (roots.jpg): none of it up to
         _ROOT_START x D from the top of the brows, along the face's own up
         (D: eyes to chin), all of it _ROOT_RAMP x D above that, so the
         lengths beside the face stay the photo's;
       - with -o lock_face, less the face (face_lock.jpg): the hull of the
         jaw and brows, and the forehead up to _FOREHEAD x D above the brows
         where the photo shows no hair, grown by _LOCK_GROW and feathered by
         _LOCK_FEATHER; it is kept the photo's exactly.
     With -o match_hair, the render's hair is pulled to the photo's first,
     place by place (_match_hair). blend_<name>.jpg shows what is taken from
     the render.

The options' defaults are pipeline_fluffy.py's fluffy_composite.py flags
as of 2026-11-02 (_COMPOSITE_FLAGS, "V14": --only-changed --match-hair
--roots-only --lock-face --align-regions --whole-cap) but -o align_regions,
off since 2026-11-02, so the renders are lined up by one transform, as
hairPart's, unless it is asked for. V11-2, the same without --whole-cap
(-o whole_cap=false), made demo_fluffy.zip: on 4 of its
photos, 3 renders each, no pixel below the root zone changed (0.16% at
most, the zone from FAN's landmarks here, not the in-house model's), and
inside it 7–58% of the photo's hair was left exactly as it is, a
different share in each render.

Billed calls per photo: 1 render.
"""

from dataclasses import dataclass

from .. import align, imaging, landmarks, region_align
from ..common import (_BROWS, _JAW, _LOCAL_SIZE, _MATCH_EDGE, _PYRAMID_LEVELS, _PYRAMID_SCALE,
                      _ZONE_FEATHER, _ZONE_GROW, CompositeFeature, _face_frame, _face_lock,
                      _filled_hull, _gaussian, _hair_pulled, _lab, _local_stats,
                      _pyramid_blend, _reach, _rgb, lined_up)
from ..core import _find_face, face_algorithm, option, stage


# fluffy_composite.py's defaults, as shares of the photo's long side: the
# hair grown by _GROW and feathered by _FEATHER.
_GROW = 0.012

# fluffy_composite.py's defaults, as shares of the photo's long side: the
# hair grown by _GROW and feathered by _FEATHER.
_FEATHER = 0.008

# As shares of D, the eyes-to-chin distance, along the face's own up: the
# root zone starts _ROOT_START from the top of the brows (below them,
# negative) and is whole _ROOT_RAMP above that; the locked forehead reaches
# _FOREHEAD above the brows. The lock is grown by _LOCK_GROW of the long
# side (past where the render moved the face's edges) and feathered by
# _LOCK_FEATHER.
_ROOT_START = -0.6

# As shares of D, the eyes-to-chin distance, along the face's own up: the
# root zone starts _ROOT_START from the top of the brows (below them,
# negative) and is whole _ROOT_RAMP above that; the locked forehead reaches
# _FOREHEAD above the brows. The lock is grown by _LOCK_GROW of the long
# side (past where the render moved the face's edges) and feathered by
# _LOCK_FEATHER.
_ROOT_RAMP = 0.5

# -o align_regions: the hair's flow is faded out in the root zone from
# _ALIGN_ROOT_START x D from the top of the brows, whole _ALIGN_ROOT_RAMP x D
# above that, over _ALIGN_BAND x D below the photo's hair top, where the
# lift is (up to ~0.16 D). As a share of the long side (0.06) the band was
# the whole cap on 004_F_2's small head (D 62 px), which then didn't move.
_ALIGN_ROOT_START = 0.0

# -o align_regions: the hair's flow is faded out in the root zone from
# _ALIGN_ROOT_START x D from the top of the brows, whole _ALIGN_ROOT_RAMP x D
# above that, over _ALIGN_BAND x D below the photo's hair top, where the
# lift is (up to ~0.16 D). As a share of the long side (0.06) the band was
# the whole cap on 004_F_2's small head (D 62 px), which then didn't move.
_ALIGN_ROOT_RAMP = 0.25
_ALIGN_BAND = 0.25

# -o whole_cap: the cap, the hair taken whole with -o only_changed, from
# _CAP_START x D above the top of the brows, whole _CAP_RAMP x D above that.
# _edit_zone kept the lifted rim of the crown and dropped the roots in front
# that lead up to it (fluffy_composite.py's V12-2: 0.85 against 0.44 of
# weight; subject6, 278_F_2 under 0.1), so the photo's flat front met a
# raised crown and the head looked misshapen.
_CAP_START = 0.0

# -o whole_cap: the cap, the hair taken whole with -o only_changed, from
# _CAP_START x D above the top of the brows, whole _CAP_RAMP x D above that.
# _edit_zone kept the lifted rim of the crown and dropped the roots in front
# that lead up to it (fluffy_composite.py's V12-2: 0.85 against 0.44 of
# weight; subject6, 278_F_2 under 0.1), so the photo's flat front met a
# raised crown and the head looked misshapen.
_CAP_RAMP = 0.25

# -o only_changed (_edit_zone), as shares of the long side but the level:
# lightness blurred over _ZONE_BLUR and off the photo's by more than
# _ZONE_LEVEL (Lab L, 0–255; the hair's mean offset taken out) counts as
# reshaped; lines thinner than _ZONE_OPEN dropped (from the newly covered
# hair only those thinner than _ZONE_NEW_OPEN: the mask's jitter); grown by
# _ZONE_GROW, feathered by _ZONE_FEATHER. Pasted by Laplacian pyramid, at
# most _PYRAMID_LEVELS levels and fewer on small photos, the coarsest band
# no wider than about _PYRAMID_SCALE, or its blend spills past the paste.
_ZONE_BLUR = 0.01

# -o only_changed (_edit_zone), as shares of the long side but the level:
# lightness blurred over _ZONE_BLUR and off the photo's by more than
# _ZONE_LEVEL (Lab L, 0–255; the hair's mean offset taken out) counts as
# reshaped; lines thinner than _ZONE_OPEN dropped (from the newly covered
# hair only those thinner than _ZONE_NEW_OPEN: the mask's jitter); grown by
# _ZONE_GROW, feathered by _ZONE_FEATHER. Pasted by Laplacian pyramid, at
# most _PYRAMID_LEVELS levels and fewer on small photos, the coarsest band
# no wider than about _PYRAMID_SCALE, or its blend spills past the paste.
_ZONE_LEVEL = 8
_ZONE_OPEN = 0.015
_ZONE_NEW_OPEN = 0.004

# single-prompt_Fluffy.py's CHANGE ONLY, in Chinese and English (its notes
# say how each line came about).
_FLUFFY_DIRECTION_ZH = (
    "讓頭頂、髮旋及髮縫兩側的髮根從頭皮自然立起，形成明顯的髮根蓬鬆感，使頭頂輪廓變得圓潤飽滿、比原本略高；"
    "蓬起的高度平均分布在整個頭頂，髮縫兩側抬起的高度相同，頭頂外輪廓順著頭型形成一條連續、平滑的圓弧，"
    "頭頂中央最高、往太陽穴與耳上逐漸自然收回；髮縫處不可隆起成尖角、山脊或帳篷狀，輪廓上不可有凸塊、凹陷、平台或斷層；"
    "立起的只有離頭皮最近的一段髮根，髮絲順原有髮流方向先微微向上再自然落下，與下方原本的髮絲連續銜接。"
    "只抬高原有髮絲，不填補稀疏處或透出的頭皮：髮量、髮密度、髮際線與髮縫的寬度和透出的頭皮都維持原樣；"
    "頭頂原有的細碎毛、飛毛保留，隨髮根一起抬起，不抹平、不移除。"
    "髮中、髮尾、髮長、兩側與後方的輪廓、捲度、髮絲質感、瀏海、髮縫位置與走向、髮色及受光完全不變；"
    "不加毛躁、不增加光澤。"
)

# single-prompt_Fluffy.py's CHANGE ONLY, in Chinese and English (its notes
# say how each line came about).
_FLUFFY_DIRECTION_EN = (
    "Lift the roots on the top of the head, the crown and both sides of the parting naturally off the scalp for clearly visible root volume, so the top of the head looks rounder and fuller and sits slightly higher than before. Spread the lift evenly over the whole top of the head, both sides of the parting rising by the same amount, so the top outline becomes one smooth, continuous, rounded arc that follows the shape of the skull: highest over the crown, easing off gradually toward the temples and ears; no peak, point, ridge or tent shape at the parting, and no bumps, dents, flat spots or steps along the outline. Only the short section of each strand closest to the scalp lifts: the strands rise slightly along their existing direction, then fall naturally and run on continuously into the hair below. Lift the existing strands only; do not fill sparse areas or scalp that shows through: hair amount, density, the hairline, the parting's width and any visible scalp stay as they are. Keep the crown's own flyaways and wispy strands, lifted together with the roots; do not smooth or remove them. Mid-lengths, ends, length, the outline at the sides and back, curl, strand texture, bangs, the parting's place and direction, hair colour and light stay exactly the same; add no frizz and no shine."
)
_FLUFFY_DIRECTIONS = {"en": _FLUFFY_DIRECTION_EN, "zh": _FLUFFY_DIRECTION_ZH}


def _fluffy_prompt(direction: str) -> str:
    """single-prompt_Fluffy.py's _fluffy_prompt: its skeleton, the editable
    area narrowed to the roots on top of the head, round `direction`."""
    return (
        "Edit ONLY the roots of the hair on top of the head in this photo: lift them off the scalp for root volume, as described below, and leave every other pixel exactly as it is. The editable area is the hair closest to the scalp on the crown, the top of the head and both sides of the parting, plus the outline of the top of the head, which may rise a little where the roots lift, evenly, as one smooth rounded arc. This is a local retouch of an existing photo, not a new image. If the roots are already full and lifted, change very little.\n"
        "\n"
        "CHANGE ONLY — the roots on top of the head:\n"
        f"{direction}\n"
        "\n"
        "PRESERVE EXACTLY — do not change any of these in any way:\n"
        "- Face and identity: face shape, eyes, eyebrows, nose, lips, teeth, ears, expression, gaze and head pose.\n"
        "- Skin, forehead included: tone, brightness, redness, texture, pores, freckles, wrinkles, moles and makeup; no smoothing, retouching or beautifying.\n"
        "- The hairline, the bangs and the parting's place, direction and width; no new parting.\n"
        "- Hair density, everywhere, the root zone included: sparse or thinning hair and any scalp that shows through it stay as they are; lift the existing strands only, never add strands or fill gaps.\n"
        "- The crown's own flyaways and wispy strands: keep each one, lifted with the roots; do not smooth, tidy or remove them.\n"
        "- All hair below the root zone: mid-lengths, ends, length, layers, face-framing strands, flyaways, strand texture and detail, the outline at the sides and back, and curl or wave size and direction stay exactly as in this photo, pixel for pixel; do not redraw those strands, and add no volume, thickness or strands there.\n"
        "- Hair colour, everywhere: hue, lightness, saturation, grey or white hair, dye, highlights and roots keep their exact shade; no darkening, tinting or recolouring.\n"
        "- The hair's light: light direction and where its highlights and shadows fall; no added gloss or shine.\n"
        "- The lifted roots match the hair right beside them in colour, strand thickness, direction, texture and light, and run on into the hair below without a seam; no flat block, blur or smear.\n"
        "- Body, hands, clothing and accessories.\n"
        "- Background, and every overlay on the photo — text, buttons, app UI elements, stickers, graphics, watermarks, borders, black bars or inset images, corners included — except the thin band right above the head that the raised crown now covers; keep each exactly where and as it is, do not remove, crop or redraw it.\n"
        "- Light and colour outside the root zone: light direction, shadows, exposure, white balance, contrast, sharpness, grain, compression texture and depth of field; no denoising, sharpening or re-drawn textures.\n"
        "- Framing: no zoom, crop, shift, rotation or reframing; outside the root zone and the raised crown, the output lines up pixel-for-pixel with this photo.\n"
        "\n"
        "OUTPUT: one photorealistic photo identical to this photo outside the roots on top of the head — no collage, border, text or watermark."
    )


@dataclass
class FluffyOptions:
    language: str = option("en", "the language of the prompt's CHANGE ONLY",
                           choices=("en", "zh"))
    align_regions: bool = option(False, "with --blend, line each render up region by region — "
                                 "the face by its landmarks, the hair by optical flow (the "
                                 "lift on top kept), the rest by ECC — instead of one "
                                 "transform (needs the face; OpenCV, run in --face-python)")
    roots_only: bool = option(True, "with --blend, take the render only on top of the head, "
                              "from about the eyes up, the lengths beside the face kept the "
                              "photo's (needs the face)")
    lock_face: bool = option(True, "with --blend, keep the face, and the forehead where it "
                             "shows no hair, exactly the photo's (needs the face)")
    only_changed: bool = option(True, "with --blend, inside the hair too, take the render only "
                                "where it reshaped the hair (the raised crown), the photo's "
                                "hair elsewhere, by a Laplacian pyramid blend")
    whole_cap: bool = option(True, "with --blend and -o only_changed, take all the render's "
                             "hair above the brows (the cap from the hairline up) too, not only "
                             "where it reshaped it, so the lift runs on from the roots in front "
                             "(needs the face)")
    match_hair: bool = option(True, "with --blend, pull the render's hair colour, place by "
                              "place, and its strand detail back to the photo's before pasting")


def _root_zone(shape, points, start: float = _ROOT_START, ramp: float = _ROOT_RAMP):
    """The root zone, 0–1 (height x width): 0 up to `start` x D from the
    top of the brows (below them, negative), 1 from `ramp` x D above that,
    measured along the face's own up — the hair on top of the head, not the
    lengths beside the face."""
    import numpy as np

    up, eyes, d, brow_top = _face_frame(points)
    ys, xs = np.mgrid[:shape[0], :shape[1]].astype(np.float32)
    height = (xs - eyes[0]) * up[0] + (ys - eyes[1]) * up[1]
    return np.clip((height - brow_top - start * d) / (ramp * d), 0, 1)


def _by_region(ctx, photo, renders: dict, points):
    """-o align_regions, as hair_part.lined_up's `first`: each render (name
    -> image) whose face FAN finds lined up region by region
    (region_align.lined_up), the hair's flow kept out of the lift: faded
    out, in the root zone from _ALIGN_ROOT_START, over _ALIGN_BAND x D below
    the photo's hair top."""
    import numpy as np

    def first(photo_hair, hair):
        names = list(renders)
        found = dict(zip(names, landmarks.face_landmarks([renders[n] for n in names],
                                                         ctx.face_python)))
        todo = [name for name in names if found[name] is not None]
        if not todo:
            return {}
        height = photo_hair.shape[0]
        top = np.where(photo_hair.any(axis=0), photo_hair.argmax(axis=0), height)
        rows = np.arange(height, dtype=np.float32)[:, None]
        keep = (np.clip(1 - (rows - top[None]) / (_ALIGN_BAND * _face_frame(points)[2]), 0, 1)
                * _root_zone(photo_hair.shape, points, _ALIGN_ROOT_START, _ALIGN_ROOT_RAMP))
        lined = region_align.lined_up(
            photo.image, [renders[n] for n in todo], photo_hair, [hair[n] for n in todo], points,
            [found[n]["points"] for n in todo], _filled_hull(photo_hair.shape, points[_JAW + _BROWS]),
            keep, ctx.face_python)
        return {name: (image, ["lined up by region (face: landmarks, hair: optical flow, rest: "
                               f"ECC); mean |diff| before -> after: {report}"])
                for name, (image, report) in zip(todo, lined)}

    return first


def _match_hair(photo, render, photo_hair, render_hair, long_side: int):
    """-o match_hair: `render` (float RGB) with its hair pulled to the
    photo's, place by place — each Lab channel's local mean and spread
    (over _LOCAL_SIZE) of the render's hair moved to those of the photo's
    hair nearby, as a head of hair is seldom one colour (dark roots, blonde
    lengths) — and its strands sharpened back to the photo's fine detail
    (_hair_pulled). The background the render redrew round the hair, which
    the paste takes with it, is moved to the photo's background nearby,
    mean only. Applied through the render's hair feathered by _MATCH_EDGE."""
    import numpy as np

    shared = photo_hair & render_hair
    if shared.sum() < 100:
        return render
    fixed = _hair_pulled(photo, render, photo_hair, render_hair, long_side)
    lp, lr = _lab(photo), _lab(render)
    sigma = _LOCAL_SIZE * long_side
    mbp, _ = _local_stats(lp, ~(photo_hair | render_hair), sigma)
    mbr, _ = _local_stats(lr, ~render_hair, sigma)
    background = _rgb(np.clip(lr - mbr + mbp, 0, 255))
    a = _gaussian(render_hair.astype(np.float32), max(1.0, _MATCH_EDGE * long_side))[..., None]
    return np.clip(fixed * a + background * (1 - a), 0, 255)


def _opened(region, size: float):
    """`region` opened (eroded, then dilated) by an OpenCV ellipse `size` px
    across (at least 3, odd): lines thinner than it dropped."""
    radius = (max(3, round(size)) | 1) // 2
    return imaging.dilated(imaging.eroded(region, radius), radius)


def _edit_zone(photo, render, photo_hair, render_hair, near_hair, long_side: int):
    """-o only_changed: where the render reshaped the hair, 0–1: its coarse
    lightness (blurred over _ZONE_BLUR, the hair's mean offset taken out)
    off the photo's by more than _ZONE_LEVEL, and the hair it newly covers
    (the raised crown), both in `near_hair`; thin lines (edges a hair off)
    opened away, then grown by _ZONE_GROW and feathered by _ZONE_FEATHER.
    Elsewhere the render only redrew the hair — smoother, a little off in
    colour — so the photo's own is kept there."""
    import numpy as np

    lp, lr = _lab(photo)[..., 0], _lab(render)[..., 0]
    shared = photo_hair & render_hair
    offset = float((lr[shared] - lp[shared]).mean()) if shared.any() else 0.0
    s = max(2.0, _ZONE_BLUR * long_side)
    coarse = np.zeros_like(lp)
    box = imaging.box(near_hair, _reach(s))
    if box is not None:
        coarse[box] = np.abs(_gaussian(lr[box], s) - _gaussian(lp[box], s) - offset)
    # The newly covered band only loses the mask's own jitter: opened as
    # wide as the lightness change, a lift thinner than that went too.
    zone = (_opened((coarse > _ZONE_LEVEL) & near_hair, _ZONE_OPEN * long_side)
            | _opened(render_hair & ~photo_hair & near_hair, _ZONE_NEW_OPEN * long_side))
    zone = imaging.dilated(zone, round(_ZONE_GROW * long_side))
    return _gaussian(zone.astype(np.float32), max(1.0, _ZONE_FEATHER * long_side))


def _paste(photo, render, photo_hair, render_hair, roots, cap, lock, opts):
    """Step 5 for one render, lined up with `photo` (PIL): the result and
    the weight the render was pasted by (0–1)."""
    import numpy as np

    p = np.asarray(photo.convert("RGB"), np.float32)
    r = np.asarray(render.convert("RGB"), np.float32)
    long_side = max(photo.size)
    hair = photo_hair | render_hair
    region = imaging.dilated(hair, max(1, round(_GROW * long_side)))
    weight = np.maximum(_gaussian(region.astype(np.float32),
                                  max(1, round(_FEATHER * long_side)) / 2), hair)
    if opts.only_changed:
        zone = _edit_zone(p, r, photo_hair, render_hair, region, long_side)
        if cap is not None:
            zone = np.maximum(zone, _gaussian(region.astype(np.float32),
                                              max(1.0, _ZONE_FEATHER * long_side)) * cap)
        weight = weight * zone
    if roots is not None:
        weight = weight * roots
    if lock is not None:
        weight = weight * (1 - lock)
    if opts.match_hair:
        r = _match_hair(p, r, photo_hair, render_hair, long_side)
    if opts.only_changed:
        levels = int(np.clip(np.log2(max(2.0, _PYRAMID_SCALE * long_side)), 1, _PYRAMID_LEVELS))
        out = _pyramid_blend(p, r, weight, levels)
        # Exactly the photo past the pyramid's spill, eased in, not cut.
        spill = _gaussian(imaging.dilated(weight > 0.01, 2 ** levels).astype(np.float32),
                          2 ** levels / 2)[..., None]
        out = p + (out - p) * spill
    else:
        w = weight[..., None]
        out = r * w + p * (1 - w)
    if lock is not None:
        # The pyramid's coarse bands spill into the face too: the photo's
        # there, exactly.
        out = out + (p - out) * lock[..., None]
    return imaging.as_image(out), weight


class FluffyFeature(CompositeFeature):
    description = ("the roots on top of the head lifted (蓬鬆): 1 render call per photo, "
                   "colour-matched to the photo; with --blend, pasted into it on top of the head "
                   "where it reshaped the hair, the face kept the photo's")
    Options = FluffyOptions
    colour_match = True
    blend_mask = "crown"

    def __init__(self):
        super().__init__("fluffy")

    def prompts(self, opts) -> dict[str, str]:
        return {"edit": _fluffy_prompt(_FLUFFY_DIRECTIONS[opts.language])}

    def blend_face(self, opts) -> bool:
        return (opts.align_regions or opts.roots_only or opts.lock_face
                or (opts.only_changed and opts.whole_cap))

    def composite(self, ctx, photo, opts, result) -> dict:
        """Steps 2–4."""
        face = None
        if self.blend_face(opts):
            with stage(result, "face", face_algorithm(ctx)):
                face = _find_face(ctx, photo, result)
        first = (_by_region(ctx, photo, result.outputs, face["points"])
                 if opts.align_regions and face is not None else None)
        photo_hair, lined = lined_up(ctx, photo, result.outputs, _GROW, result, first)
        long_side = max(photo.size)
        roots = cap = lock = None
        if face is not None and opts.roots_only:
            roots = _root_zone(photo_hair.shape, face["points"])
            result.intermediates["roots.jpg"] = imaging.as_image(
                imaging.tinted(photo.image, roots > 0.5))
        if face is not None and opts.only_changed and opts.whole_cap:
            cap = _root_zone(photo_hair.shape, face["points"], _CAP_START, _CAP_RAMP)
        if face is not None and opts.lock_face:
            lock = _face_lock(photo_hair, face["points"], long_side)
            result.intermediates["face_lock.jpg"] = imaging.as_image(
                imaging.tinted(photo.image, lock > 0.5))
        pasted = {}
        for name, (render, render_hair) in lined.items():
            with stage(result, "blend", "fluffy._paste: the render's hair on top of the head "
                                        "(by -o roots_only, only_changed, whole_cap), the face "
                                        "kept (-o lock_face), by Laplacian pyramid",
                       name=f"paste {name}"):
                image, weight = _paste(photo.image, render, photo_hair, render_hair, roots, cap,
                                       lock, opts)
            # What the paste dropped: the render's pixels off the photo's
            # where the photo is kept (fluffy_composite.py's outside_off_pct).
            kept = weight < 0.01
            off = align._difference(photo.image.convert("RGB"), render)[kept] > align._DRIFT_LEVEL
            pasted[name] = (image, weight, {
                "pasted": round(float((weight >= 0.5).mean()), 4),
                "render_off_dropped": round(float(off.mean()), 4) if kept.any() else 0.0,
            })
        return pasted
