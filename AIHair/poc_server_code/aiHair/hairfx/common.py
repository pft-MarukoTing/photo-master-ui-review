"""What more than one of aiHair's features shares, and blend.py's head
mask: the one-call base (SinglePromptFeature), the composite bases
(Composited, CompositeFeature, lined_up), the composite that pastes renders
back lined up region by region (shifted_composite, HairShifted: grayHair,
rootRetouch, strayHair), fluffy's face lock, pyramid blend and Lab colour
helpers they paste with, the Gaussian blur and the hair's body, and the
analyst's photo (_analysis_image).

Every definition is HairFeatures' own, as it was, from the module named in
the section it comes from below; only where it lives changed. The nine
features (features/) import from here, never from one another.

--- HairFeatures' features/shifted.py ---

The composite strayHairWithFace's, rootRetouch_1's and grayHairTwoStep's
--blend share (shifted_composite): each render lined up with the photo
region by region, the face, the hair and the rest each moved by one
translation (region_align.shifted), then pasted into it through a zone
grown by fluffy's _ZONE_GROW and feathered by _ZONE_FEATHER of the long
side, by fluffy's -o only_changed pyramid paste.

strayHairWithFace's zone is the face it drew, its renders' hair matched to
the photo's first (stray_hair_with_face._matched_hair). rootRetouch_1's and
grayHairTwoStep's (HairShifted) is the hair, the photo's and the render's,
their face for the lining up FAN's (the hull of the jaw and brows), their
renders' colours as they are: fluffy's -o match_hair would pull the hair
they recolour back to the photo's. Their face is kept the photo's, as
fluffy's -o lock_face keeps it (fluffy._face_lock), less the hair, the
photo's and the render's, so the strands they recolour over the face and
the forehead come through: grown 3% onto the skin and feathered, the hair
took in the render's forehead under the hairline, a band of another skin
tone (rootRetouch_1's 00000: 74% of the lock's skin, 6.2 darker in L).
Until 2026-11-03 those two were pasted through blend's hair mask, lined up
by --align first, and only with --blend; now by default (features'
BLENDED).
"""

import io
import math

from . import align, imaging, region_align
from .core import (BLEND_ALGORITHMS, Feature, _colour_match_outputs, _find_face,
                   colour_algorithm, edit, face_algorithm, hair_model, stage)


# The hair's body: the hair mask shrunk by a Gaussian of this share of the
# photo's short side (about twice as far in effect) and feathered by one of
# _BODY_FEATHER, so the paste's seam falls inside the hair.
_BODY_ERODE = 0.003

# The hair's body: the hair mask shrunk by a Gaussian of this share of the
# photo's short side (about twice as far in effect) and feathered by one of
# _BODY_FEATHER, so the paste's seam falls inside the hair.
_BODY_FEATHER = 0.003

# aiHair: how _gaussian runs — "opencv", the same kernel under 3 px
# (cv2.sepFilter2D) and the same three boxes from there (cv2.blur), the edges
# reflected alike (BORDER_REFLECT_101, np.pad's "reflect"); or "numpy",
# _gaussian_axis, as HairFeatures runs it. On subject1 (2.4 MP) each blur
# took 5-30x less time, the boxes' exactly the same, the kernels' within
# 1e-4 of a level; soft's post_process 3.8x faster (6x at 9.8 MP), its
# result at most 1 level off (output/soft_check, 2026-11-09). All nine
# features on subject1 and subject20 (hairline at both levels, fake
# renders): 19 of the 20 results the same to the pixel, soft's on subject1
# 1 level off on 0.0001% of them; soft 2.2-2.9x faster, frizzCare's smooth
# 2.5x, the rest as before (output/gaussian_check).
_GAUSSIAN_BY = "opencv"


def _gaussian(a, sigma: float):
    """`a` (height x width, or x channels; float) blurred by a Gaussian of
    `sigma` px, its edges reflected: a direct kernel under 3 px, three box
    passes (from running sums) from there; run as _GAUSSIAN_BY says."""
    import numpy as np

    out = np.asarray(a, np.float32)
    if sigma <= 0:
        return out
    if _GAUSSIAN_BY == "opencv" and (out.ndim == 2 or (out.ndim == 3 and out.shape[2] <= 4)):
        return _gaussian_opencv(out, sigma)
    for axis in (0, 1):
        out = _gaussian_axis(out, sigma, axis)
    return out


def _gaussian_opencv(a, sigma: float):
    """_gaussian by OpenCV (aiHair): _gaussian_axis' kernel or boxes, down
    the rows and then along them, its edges."""
    import cv2
    import numpy as np

    src = np.ascontiguousarray(a, np.float32)
    if sigma < 3:
        r = max(1, int(np.ceil(3 * sigma)))
        x = np.arange(-r, r + 1, dtype=np.float32)
        kernel = np.exp(-0.5 * (x / sigma) ** 2)
        kernel /= kernel.sum()
        out = cv2.sepFilter2D(src, cv2.CV_32F, kernel, kernel,
                              borderType=cv2.BORDER_REFLECT_101)
    else:
        # Three boxes of an odd width whose variances add up to sigma's.
        width = int(np.sqrt(4 * sigma * sigma + 1))
        width += width % 2 == 0
        out = src
        for ksize in ((1, width),) * 3 + ((width, 1),) * 3:
            out = cv2.blur(out, ksize, borderType=cv2.BORDER_REFLECT_101)
    return out.reshape(a.shape)


def _gaussian_axis(a, sigma: float, axis: int):
    import numpy as np

    a = np.moveaxis(a, axis, 0)
    n = a.shape[0]
    pad = [(0, 0)] * a.ndim
    if sigma < 3:
        r = max(1, int(np.ceil(3 * sigma)))
        x = np.arange(-r, r + 1, dtype=np.float32)
        kernel = np.exp(-0.5 * (x / sigma) ** 2)
        kernel /= kernel.sum()
        pad[0] = (r, r)
        padded = np.pad(a, pad, mode="reflect")
        out = np.zeros_like(a)
        for i, w in enumerate(kernel):
            out += w * padded[i:i + n]
        return np.moveaxis(out, 0, axis)
    # Three boxes of an odd width whose variances add up to sigma's.
    width = int(np.sqrt(4 * sigma * sigma + 1))
    width += width % 2 == 0
    r = width // 2
    pad[0] = (r + 1, r)
    out = a
    for _ in range(3):
        sums = np.cumsum(np.pad(out, pad, mode="reflect"), axis=0, dtype=np.float64)
        out = ((sums[width:width + n] - sums[:n]) / width).astype(np.float32)
    return np.moveaxis(out, 0, axis)


def _hair_body(hair, short: int):
    """The hair's body as a 0–1 float array: `hair` shrunk by _BODY_ERODE
    and feathered by _BODY_FEATHER, kept inside `hair`."""
    body = ~imaging.grown(~hair, _BODY_ERODE * short)
    return imaging.feathered(body, _BODY_FEATHER * short) * hair


class SinglePromptFeature(Feature):
    """One images.edit call: the photo and the effect's prompt."""

    default_n_images = 4  # the smoke script's

    def __init__(self, name: str, prompt: str,
                 description: str = "the photo and its best prompt so far"):
        super().__init__(name)
        self.prompt = prompt
        self.description = f"{description}: 1 render call per photo"

    def prompts(self, opts) -> dict[str, str]:
        return {"edit": self.prompt}

    def process(self, ctx, photo, opts, result) -> None:
        result.step = "edit"
        renders = edit(ctx, result, "edit", [photo.file], self.prompt, photo.size,
                       n=self.n_images(ctx.render), keep_size=ctx.render.keep_rendered_size)
        for i, image in enumerate(renders):
            result.outputs[f"result_{i}"] = image


# Steps 1 and 2 see the photo at most this long on its long side; step 2's
# grid has a line every _GRID_STEP of its 0–1000 axes.
_ANALYSIS_LONG_SIDE = 2048

# Steps 1 and 2 see the photo at most this long on its long side; step 2's
# grid has a line every _GRID_STEP of its 0–1000 axes.
_GRID_STEP = 100


def _analysis_image(upright, grid: bool, long_side: int = _ANALYSIS_LONG_SIDE) -> bytes:
    """`upright` (the photo as the render model gets it) as JPEG bytes for
    steps 1 and 2, at most `long_side` on its long side; with `grid`, under
    step 2's coordinate grid: a thin yellow line every _GRID_STEP of the
    0–1000 axes, each labelled at the top and left edges. A grid to read
    positions off gives the model's points a scale to land on, instead of a
    guess at the image's proportions."""
    from PIL import Image, ImageDraw

    im = upright.convert("RGB")
    scale = min(1.0, long_side / max(im.size))
    if scale < 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)),
                       Image.Resampling.LANCZOS)
    if grid:
        overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = imaging.label_font(max(12, max(im.size) // 64))
        width = max(1, max(im.size) // 1200)
        label = {"fill": (255, 230, 0, 235), "font": font,
                 "stroke_width": 2, "stroke_fill": (0, 0, 0, 235)}
        for v in range(_GRID_STEP, 1000, _GRID_STEP):
            x, y = v * im.width / 1000, v * im.height / 1000
            draw.line([(x, 0), (x, im.height)], fill=(255, 230, 0, 110), width=width)
            draw.line([(0, y), (im.width, y)], fill=(255, 230, 0, 110), width=width)
            draw.text((x + 4, 4), str(v), **label)
            draw.text((4, y + 4), str(v), **label)
        im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


class Composited(Feature):
    """An effect whose --blend is a composite of its own (`composite`),
    named by its blend_mask, instead of one of blend.MASKS. Without --blend
    its outputs are colour-matched and lined up as every render is
    (Feature.finish); with it, colour-matched and then composited, which
    lines them up itself. It leaves `process` to the effect, so put before
    another Feature class in an effect's bases it takes over only its
    --blend (shifted.HairShifted)."""

    def aligns(self, ctx, opts) -> bool:
        # With --blend the composite lines the outputs up itself.
        return not self.blends(ctx, opts) and super().aligns(ctx, opts)

    def composite(self, ctx, photo, opts, result) -> dict:
        """The outputs, colour-matched, pasted into the photo: {name: (the
        result, the weight the output was pasted by (0–1), notes)}; what it
        makes on the way goes into `result` as it goes."""
        raise NotImplementedError

    def composite_kind(self, opts) -> str:
        """The composite `opts` run, by its BLEND_ALGORITHMS name, as
        info["blend"]["mask"] says: blend_mask, unless the effect picks one
        by its options (fluffy's -o paste)."""
        return self.blend_mask

    def finish(self, ctx, photo, opts, result) -> None:
        """Feature.finish without --blend. With it, the outputs colour-matched
        (where colour_matches) and composited, each as it was kept as
        <name>_unblended, its weight as blend_mask_<name> and blend_<name>.jpg
        (the photo, red where the output takes more than half), in a sheet
        row of its own, as core._blend_outputs keeps them. Failing is a
        warning, the outputs left unblended."""
        if not self.blends(ctx, opts):
            super().finish(ctx, photo, opts, result)
            return
        if not result.outputs:
            return
        if self.colour_matches(ctx, opts):
            area = self.colour_area(ctx)
            face = None
            if area == "hair_inset":
                # aiHair: as Feature.finish, the face found first, so the
                # hair_inset match keeps off the forehead's skin (without
                # it, rootRetouch's 816a32e9 kept an arc round the forehead,
                # 2026-11-11). The composite finds it again from the cache.
                with stage(result, "face", face_algorithm(ctx)):
                    face = _find_face(ctx, photo, result)
            with stage(result, "colour_match", colour_algorithm(area, self.colour_shift)):
                _colour_match_outputs(ctx, photo, result, self.colour_shift, area, face)
        kind = self.composite_kind(opts)
        try:
            # aiHair: the composite lines the renders up itself, so its
            # align steps are inside this blend step.
            with stage(result, "blend", BLEND_ALGORITHMS.get(kind, kind)):
                pasted = self.composite(ctx, photo, opts, result)
        except Exception as e:  # noqa: BLE001 — the renders are paid for; keep them
            result.warnings.append(f"{kind} composite failed, outputs left unblended "
                                   f"— {type(e).__name__}: {e}")
            return
        notes = result.info["blend"] = {"mask": kind}
        for name, (image, weight, note) in pasted.items():
            result.intermediates[f"{name}_unblended"] = result.outputs[name]
            result.keep(f"blend_mask_{name}",
                        lambda: imaging.as_image(weight * 255).convert("L"))
            result.keep(f"blend_{name}.jpg", lambda: imaging.as_image(
                imaging.tinted(photo.image, weight > 0.5)))
            result.outputs[name] = image
            notes[name] = note
            result.sheet.append(["input", f"{name}_unblended", f"blend_{name}.jpg", name])


class CompositeFeature(Composited):
    """A Composited effect rendered in one images.edit call."""

    default_n_images = 3  # the demos' r1–r3

    def process(self, ctx, photo, opts, result) -> None:
        result.step = "edit"
        renders = edit(ctx, result, "edit", [photo.file], self.prompts(opts)["edit"], photo.size,
                       n=self.n_images(ctx.render), keep_size=ctx.render.keep_rendered_size)
        for i, image in enumerate(renders):
            result.outputs[f"result_{i}"] = image


def lined_up(ctx, photo, renders: dict, grow: float, result, first=None):
    """The photo's hair, and per render (name -> image, at the photo's size)
    that render lined up with the photo outside both their hair grown by
    `grow` of the long side (align.line_up), and its own hair, found again
    on it as lined up where it moved. `first`, if given, is tried before
    line_up: called with the photo's hair and the renders' (name -> mask), it
    returns {name: (the render lined up, notes)} for those it lines up, and
    line_up takes the rest. hair_input, hair_<name>, <name>_lined_up (where
    it moved) and info["alignment"] go into `result`."""
    names = list(renders)
    photo_hair, *masks = hair_model(ctx, result, [photo.image, *renders.values()],
                                    f"the photo and {len(names)} render(s)")
    if not photo_hair.any():
        raise RuntimeError("the hair model found no hair in the photo")
    result.keep("hair_input", lambda: imaging.mask_image(photo_hair))
    hair = dict(zip(names, masks))
    radius = round(grow * max(photo.size))
    notes = result.info.setdefault("alignment", {})
    done = first(photo_hair, hair) if first is not None else {}
    lined, moved = {}, []
    with stage(result, "align", "align.line_up: one affine transform of each render, fitted "
                                "outside the photo's and its hair grown by "
                                f"{grow:g} of the long side"
                                + ("; region_align.lined_up's where it lined one up first"
                                   if first is not None else "")):
        for name in names:
            render, render_hair = renders[name], hair[name]
            if name in done:
                aligned, notes[name] = done[name]
            else:
                aligned, notes[name] = align.line_up(
                    photo.image, render, imaging.dilated(photo_hair | render_hair, radius))
            if aligned is not None:
                render = aligned
                result.keep(f"{name}_lined_up", lambda: render)
                moved.append(name)
            lined[name] = [render, render_hair]
    if moved:
        for name, render_hair in zip(moved, hair_model(ctx, result, [lined[n][0] for n in moved],
                                                       f"{len(moved)} render(s) as lined up")):
            lined[name][1] = render_hair
    for name, (_, render_hair) in lined.items():
        result.keep(f"hair_{name}", lambda: imaging.mask_image(render_hair))
    return photo_hair, {name: tuple(pair) for name, pair in lined.items()}


def _reach(sigma: float) -> int:
    """How far past a pixel _gaussian at `sigma` takes it in, with a spare."""
    return math.ceil(4 * sigma) + 1


# As shares of D, the eyes-to-chin distance, along the face's own up: the
# root zone starts _ROOT_START from the top of the brows (below them,
# negative) and is whole _ROOT_RAMP above that; the locked forehead reaches
# _FOREHEAD above the brows. The lock is grown by _LOCK_GROW of the long
# side (past where the render moved the face's edges) and feathered by
# _LOCK_FEATHER.
_FOREHEAD = 0.6
_LOCK_GROW = 0.008
_LOCK_FEATHER = 0.004

# -o match_hair: the neighbourhood the hair's colour is matched over (share
# of the long side, a Gaussian's sigma), the most unsharp-mask amount added
# back to its strands, and the feather of the render's hair it is applied
# through (share of the long side).
_LOCAL_SIZE = 0.03

# -o match_hair: the neighbourhood the hair's colour is matched over (share
# of the long side, a Gaussian's sigma), the most unsharp-mask amount added
# back to its strands, and the feather of the render's hair it is applied
# through (share of the long side).
_DETAIL_MAX = 1.0
_MATCH_EDGE = 0.004

# -o only_changed (_edit_zone), as shares of the long side but the level:
# lightness blurred over _ZONE_BLUR and off the photo's by more than
# _ZONE_LEVEL (Lab L, 0–255; the hair's mean offset taken out) counts as
# reshaped; lines thinner than _ZONE_OPEN dropped (from the newly covered
# hair only those thinner than _ZONE_NEW_OPEN: the mask's jitter); grown by
# _ZONE_GROW, feathered by _ZONE_FEATHER. Pasted by Laplacian pyramid, at
# most _PYRAMID_LEVELS levels and fewer on small photos, the coarsest band
# no wider than about _PYRAMID_SCALE, or its blend spills past the paste.
_ZONE_GROW = 0.03
_ZONE_FEATHER = 0.015
_PYRAMID_LEVELS = 5
_PYRAMID_SCALE = 0.015

# FAN's 68 landmarks.
_JAW = list(range(0, 17))

# FAN's 68 landmarks.
_BROWS = list(range(17, 27))
_EYES = list(range(36, 48))
_CHIN = 8


def _face_frame(points):
    """The face's own up (unit, image coordinates), the eyes' centre, D
    (eyes to chin) and the top of the brows as a height along up from the
    eyes."""
    import numpy as np

    eyes = points[_EYES].mean(axis=0)
    up = eyes - points[_CHIN]
    d = float(np.hypot(*up))
    up = up / d
    return up, eyes, d, float(((points[_BROWS] - eyes) @ up).max())


def _hull(points) -> list:
    """The convex hull of `points` (N x 2), rounded to pixels, in order
    round it (Andrew's monotone chain)."""
    import numpy as np

    pts = sorted(set(map(tuple, np.rint(points).astype(int).tolist())))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _filled_hull(shape, points):
    """The convex hull of `points`, filled, as a mask (height x width)."""
    import numpy as np
    from PIL import Image, ImageDraw

    canvas = Image.new("L", (shape[1], shape[0]), 0)
    hull = _hull(points)
    if len(hull) >= 3:
        ImageDraw.Draw(canvas).polygon(hull, fill=255)
    return np.asarray(canvas) > 127


def _face_lock(photo_hair, points, long_side: int):
    """The face kept the photo's, 0–1 (height x width): the hull of the jaw
    and brows, and the forehead up to _FOREHEAD x D above the brows where
    the photo shows no hair; only the skin joined to the face (on a tilted
    head the forehead's hull leans past the temple, onto the background);
    grown by _LOCK_GROW and feathered by _LOCK_FEATHER, so the render's
    edges, moved a few pixels, don't come through round it."""
    import numpy as np

    up, _, d, _ = _face_frame(points)
    brows = points[_BROWS]
    face = _filled_hull(photo_hair.shape, points[_JAW + _BROWS])
    forehead = _filled_hull(photo_hair.shape, np.vstack([brows, brows + up * _FOREHEAD * d]))
    lock = face | (forehead & ~photo_hair)
    labels, _ = imaging.components(lock, connectivity=4)
    lock &= np.isin(labels, np.unique(labels[face]))
    lock = imaging.dilated(lock, round(_LOCK_GROW * long_side))
    return _gaussian(lock.astype(np.float32), max(1.0, _LOCK_FEATHER * long_side))


# sRGB (D65) to XYZ, and the white point, for _lab and _rgb.
_RGB_TO_XYZ = ((0.412453, 0.357580, 0.180423),
               (0.212671, 0.715160, 0.072169),
               (0.019334, 0.119193, 0.950227))

# sRGB (D65) to XYZ, and the white point, for _lab and _rgb.
_WHITE = (0.950456, 1.0, 1.088754)


def _lab(rgb):
    """RGB (float, 0–255) as CIE Lab, scaled as OpenCV's 8-bit Lab is: L
    0–255, a and b offset by 128."""
    import numpy as np

    c = rgb / 255
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    # einsum, not @: from main.py's worker threads, BLAS can crash (imaging's
    # _LSTSQ_LOCK).
    xyz = (np.einsum("...c,dc->...d", linear, np.array(_RGB_TO_XYZ, np.float32))
           / np.array(_WHITE, np.float32))
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([(116 * f[..., 1] - 16) * 255 / 100,
                     500 * (f[..., 0] - f[..., 1]) + 128,
                     200 * (f[..., 1] - f[..., 2]) + 128], axis=-1).astype(np.float32)


def _rgb(lab):
    """_lab's Lab back to RGB (float, 0–255)."""
    import numpy as np

    fy = (lab[..., 0] * 100 / 255 + 16) / 116
    f = np.stack([fy + (lab[..., 1] - 128) / 500, fy, fy - (lab[..., 2] - 128) / 200], axis=-1)
    xyz = np.where(f > 0.206893, f ** 3, (f - 16 / 116) / 7.787) * np.array(_WHITE, np.float32)
    to_rgb = np.linalg.inv(np.array(_RGB_TO_XYZ)).astype(np.float32)
    linear = np.clip(np.einsum("...c,dc->...d", xyz, to_rgb), 0, 1)
    c = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    return (c * 255).astype(np.float32)


def _local_stats(lab, mask, sigma: float):
    """Per pixel, the mean and spread of each Lab channel over `mask`'s
    pixels nearby (Gaussian-weighted, `sigma` px), filled in past the mask's
    edge from the nearest of it. Worked out at a quarter of the size: the
    scale is coarse."""
    import numpy as np
    from PIL import Image

    height, width = mask.shape
    small = (max(1, width // 4), max(1, height // 4))

    def down(a):
        return np.asarray(Image.fromarray(np.ascontiguousarray(a, np.float32))
                          .resize(small, Image.Resampling.BOX))

    def up(a):
        return np.dstack([np.asarray(Image.fromarray(np.ascontiguousarray(a[..., c]))
                                     .resize((width, height), Image.Resampling.BILINEAR))
                          for c in range(a.shape[2])])

    x = np.dstack([down(lab[..., c]) for c in range(3)])
    m = down(mask.astype(np.float32))[..., None]
    s = max(1.0, sigma / 4)
    den = _gaussian(m, s) + 1e-4
    mean = _gaussian(x * m, s) / den
    var = _gaussian(x * x * m, s) / den - mean ** 2
    return up(mean), up(np.sqrt(np.maximum(var, 0)))


def _hair_pulled(photo, render, photo_hair, render_hair, long_side: int, near=None):
    """_match_hair's hair: `render` (float RGB) with each Lab channel's
    local mean and spread (over _LOCAL_SIZE) of its hair moved to those of
    the photo's hair nearby, and its strands sharpened back to the photo's
    fine detail (unsharp mask, by how much the photo's exceeds the render's,
    at most _DETAIL_MAX); with `near` (0–1, height x width x 1), only that
    share of the move at each pixel, before the sharpening."""
    import numpy as np

    shared = photo_hair & render_hair
    lp, lr = _lab(photo), _lab(render)
    sigma = _LOCAL_SIZE * long_side
    mp, sp = _local_stats(lp, photo_hair, sigma)
    mr, sr = _local_stats(lr, render_hair, sigma)
    fixed = _rgb(np.clip((lr - mr) * np.clip((sp + 1) / (sr + 1), 0.7, 1.5) + mp, 0, 255))
    if near is not None:
        fixed = render + (fixed - render) * near
    grey = np.array([0.299, 0.587, 0.114], np.float32)

    def detail(rgb):
        g = np.einsum("...c,c->...", rgb, grey)
        return float(np.abs(g - _gaussian(g, 2))[shared].mean())

    amount = float(np.clip(detail(photo) / max(detail(fixed), 1e-3) - 1, 0, _DETAIL_MAX))
    return fixed + amount * (fixed - _gaussian(fixed, 1.5))


def _five_tap(a):
    """`a` blurred by OpenCV's pyramid kernel, (1 4 6 4 1) / 16 each way,
    its edges reflected as OpenCV's are (BORDER_REFLECT_101). The taps
    are views of the padded array and summed in place, in the order the
    sum of copies they were took them (aiHair): the same values, two
    arrays of `a`'s size where it was ten."""
    import numpy as np

    kernel = np.array([1, 4, 6, 4, 1], np.float32) / 16
    for axis in (0, 1):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (2, 2)
        padded = np.pad(a, pad, mode="reflect")
        n = a.shape[axis]
        taps = [padded[(slice(None),) * axis + (slice(i, i + n),)] for i in range(len(kernel))]
        out = kernel[0] * taps[0]
        part = np.empty_like(out)
        for w, tap in zip(kernel[1:], taps[1:]):
            np.multiply(w, tap, out=part)
            out += part
        a = out
    return a


def _pyramid_blend(a, b, weight, levels: int):
    """`b` over `a` (float RGB) by `weight` (0–1), one Laplacian-pyramid
    band at a time with the weight taken down the pyramid too: colour and
    light blend over a wide band, fine strands over a narrow one, so
    neither steps where they meet. (aiHair: each level down a copy, not a
    view keeping the blur a level up alive, and each level up summed in
    place in the order the expression took it: the same values, a third of
    the memory.)"""
    import numpy as np

    def down(x):  # cv2.pyrDown
        return np.ascontiguousarray(_five_tap(x)[::2, ::2])

    def up(x, shape):  # cv2.pyrUp to `shape`
        out = np.zeros(shape[:2] + x.shape[2:], np.float32)
        out[::2, ::2] = x
        out = _five_tap(out)
        out *= 4
        return out

    ga, gb, gw = [a], [b], [weight.astype(np.float32)]
    for _ in range(levels):
        ga.append(down(ga[-1]))
        gb.append(down(gb[-1]))
        gw.append(down(gw[-1]))
    out = ga[-1] * (1 - gw[-1][..., None]) + gb[-1] * gw[-1][..., None]
    for i in range(levels - 1, -1, -1):
        shape = ga[i].shape
        w = gw[i][..., None]
        # up(out) + (ga[i] - up(ga[i + 1])) * (1 - w) + (gb[i] - up(gb[i + 1])) * w
        total = up(out, shape)
        band = up(ga[i + 1], shape)
        np.subtract(ga[i], band, out=band)
        band *= 1 - w
        total += band
        band = up(gb[i + 1], shape)
        np.subtract(gb[i], band, out=band)
        band *= w
        total += band
        out = total
    return np.clip(out, 0, 255, out=out)


# HairShifted's lock eased off the hair over a Gaussian of this share of the
# long side, from the hair grown by twice it, so the strands, however thin,
# stay the render's (95% and more) and the whole ease lies on the skin.
# Eased outward from the hair's own edge over 0.004, the lock came in by half
# at its first pixel and was done 4-5 px on, a line where rootRetouch_1's
# darker, denser fringe met the photo's forehead (subject25).
_LOCK_HAIR_FEATHER = 0.01


def _zone_weight(zone, long_side: int):
    """The paste's weight, 0–1: `zone` grown by _ZONE_GROW and feathered by
    _ZONE_FEATHER of the long side."""
    import numpy as np

    grown = imaging.dilated(zone, round(_ZONE_GROW * long_side))
    return _gaussian(grown.astype(np.float32), max(1.0, _ZONE_FEATHER * long_side))


def _paste(photo, render, photo_hair, render_hair, weight, match=None, lock=None):
    """One render, lined up with `photo` (PIL), pasted into it by `weight`
    (0–1) as fluffy's -o only_changed pastes, by a Laplacian pyramid
    (fluffy._pyramid_blend), `match` taken on it first where given, and the
    photo's taken back by `lock` (0–1) where given. Returns the result."""
    import numpy as np

    p = np.asarray(photo.convert("RGB"), np.float32)
    r = np.asarray(render.convert("RGB"), np.float32)
    long_side = max(photo.size)
    if match is not None:
        r = match(p, r, photo_hair, render_hair, long_side)
    levels = int(np.clip(np.log2(max(2.0, _PYRAMID_SCALE * long_side)), 1, _PYRAMID_LEVELS))
    out = _pyramid_blend(p, r, weight, levels)
    # Exactly the photo past the pyramid's spill, eased in, not cut.
    spill = _gaussian(imaging.dilated(weight > 0.01, 2 ** levels).astype(np.float32),
                      2 ** levels / 2)[..., None]
    out = p + (out - p) * spill
    if lock is not None:
        # The pyramid's coarse bands spill into the lock too: the photo's
        # there, as fluffy's paste keeps its face.
        out = out + (p - out) * lock[..., None]
    return imaging.as_image(out)


def _regions(upright, weights):
    """The lining up's regions drawn on the photo (regions.jpg): the face
    red, the hair green and the rest blue, each where it weighs most."""
    import numpy as np

    colours = np.float32([(255, 60, 60), (60, 220, 60), (60, 120, 255)])
    base = np.asarray(upright.convert("RGB"), np.float32)
    return imaging.as_image(0.5 * base + 0.5 * colours[np.argmax(weights, axis=2)])


def shifted_composite(ctx, photo, result, face, keep_out, zone_of, match=None,
                      lock_of=None) -> dict:
    """The outputs lined up with the photo region by region
    (region_align.shifted: the face `face`, the hair's and the rest's fitted
    outside `keep_out`; regions.jpg, <name>_lined_up where it moved, its
    hair found again) and pasted into it (_paste) through zone_of(the
    photo's hair, the render's) grown and feathered (_zone_weight), `match`
    (the photo and the render as float RGB, their hair, the long side ->
    the render) taken on each first where given, and less lock_of(the
    photo's hair, the render's) (0–1, kept the photo's; face_lock_<name>.jpg
    where over half) where given: {name: (the result, its weight, notes)},
    as a Composited effect's composite returns."""
    names = list(result.outputs)
    renders = [result.outputs[name] for name in names]
    photo_hair, *hairs = hair_model(ctx, result, [photo.image, *renders],
                                    f"the photo and {len(renders)} render(s)")
    result.keep("hair_input", lambda: imaging.mask_image(photo_hair))
    with stage(result, "align", "region_align.shifted: the face, the hair and the rest each "
                                "lined up by one translation (OpenCV, in the face python)"):
        lined, weights = region_align.shifted(photo.image, renders, photo_hair, hairs, face,
                                              keep_out, ctx.face_python,
                                              weights=result.debug)
    result.keep("regions.jpg", lambda: _regions(photo.image, weights))
    moved = [i for i, (image, _) in enumerate(lined) if image is not None]
    if moved:
        for i, hair in zip(moved, hair_model(ctx, result, [lined[i][0] for i in moved],
                                             f"{len(moved)} render(s) as lined up")):
            hairs[i] = hair
    long_side = max(photo.size)
    notes = result.info.setdefault("alignment", {})
    pasted = {}
    for i, name in enumerate(names):
        render, notes[name] = lined[i]
        if render is None:
            render = renders[i]
        else:
            result.keep(f"{name}_lined_up", lambda: render)
        result.keep(f"hair_{name}", lambda: imaging.mask_image(hairs[i]))
        weight = _zone_weight(zone_of(photo_hair, hairs[i]), long_side)
        lock = lock_of(photo_hair, hairs[i]) if lock_of is not None else None
        if lock is not None:
            weight = weight * (1 - lock)
            result.keep(f"face_lock_{name}.jpg", lambda: imaging.as_image(
                imaging.tinted(photo.image, lock > 0.5)))
        with stage(result, "blend", "common._paste: the render pasted through the zone (grown, "
                                    "feathered, less the face lock) by Laplacian pyramid"
                                    + (", its hair's colour matched first" if match else ""),
                   name=f"paste {name}"):
            image = _paste(photo.image, render, photo_hair, hairs[i], weight, match, lock)
        note = {"pasted": round(float((weight >= 0.5).mean()), 4)}
        if result.debug:  # aiHair: a note only, which the server doesn't read
            # What the paste dropped: the render's pixels off the photo's
            # where the photo is kept.
            kept = weight < 0.01
            off = (align._difference(photo.image.convert("RGB"), render)[kept]
                   > align._DRIFT_LEVEL)
            note["render_off_dropped"] = round(float(off.mean()), 4) if kept.any() else 0.0
        pasted[name] = (image, weight, note)
    return pasted


class HairShifted(Composited):
    """--blend's composite pasting the renders through the hair, the
    photo's and the render's (shifted_composite), lined up by region with
    the face FAN's (the hull of its jaw and brows), the face kept the
    photo's but for the hair (_lock); with no face found (a warning), no
    face region and no lock. Put before an effect's own Feature class in
    its bases, it takes over only its --blend."""

    def blend_face(self, opts) -> bool:
        return True

    def composite(self, ctx, photo, opts, result) -> dict:
        return hair_shifted_composite(ctx, photo, result)


def hair_shifted_composite(ctx, photo, result) -> dict:
    """HairShifted's composite, as Composited.composite returns it; fluffy's
    -o paste=shifted too: the face found (FAN; the hull of its jaw and
    brows), the renders lined up and pasted through the hair, the photo's
    and the render's (shifted_composite), the face kept the photo's but for
    the hair (_lock); with no face found, no face region and no lock."""
    import numpy as np

    shape = (photo.size[1], photo.size[0])
    with stage(result, "face", face_algorithm(ctx)):
        found = _find_face(ctx, photo, result)
    face, lock_of = np.zeros(shape, bool), None
    if found is not None:
        points, long_side = found["points"], max(photo.size)
        face = _filled_hull(shape, points[_JAW + _BROWS])

        def lock_of(photo_hair, render_hair):
            return _lock(photo_hair, render_hair, points, long_side)

    return shifted_composite(ctx, photo, result, face, np.zeros(shape, bool),
                             lambda photo_hair, render_hair: photo_hair | render_hair,
                             lock_of=lock_of)


def _lock(photo_hair, render_hair, points, long_side: int):
    """HairShifted's lock, 0–1: fluffy's face lock (the face's hull and the
    bare forehead above the brows, grown and feathered: fluffy._face_lock)
    less the photo's and the render's hair, grown by twice
    _LOCK_HAIR_FEATHER and eased off it over _LOCK_HAIR_FEATHER, the
    strands themselves out of it and the ease on the skin beside them."""
    import numpy as np

    sigma = max(1.0, _LOCK_HAIR_FEATHER * long_side)
    hair = imaging.dilated(photo_hair | render_hair, round(2 * sigma))
    hair = _gaussian(hair.astype(np.float32), sigma)
    return _face_lock(photo_hair, points, long_side) * (1 - hair)
