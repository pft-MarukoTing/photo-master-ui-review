"""Lining a render up with the photo it was drawn from (register, line_up):
the model draws renders up to ~3% off the photo's grid, which shows
wherever a render is compared with the photo, pasted back into it or read
as a mask over it.

smoke_gptimage2_261008.py's method (the gpt-image integration repo), as
looping-prompt-hairline.py has it: patches of the photo outside the area the
edit changed are matched in the render by phase correlation, and an affine
transform is fitted to how far they moved.

That one transform is global: the model also moves the face's features a
little on their own (an eye a pixel or two, the mouth redrawn), which no
transform of the whole picture lines up. Given the photo's face (68
landmarks, landmarks.face_landmarks), line_up lines the face up locally
too (_line_up_local): the transform is fitted outside the face, each of
its parts — brows, eyes, nose, mouth, chin — is matched on its own, and
inside the face the render is moved by a field that takes each part's own
shift over it and fades into the transform at the face's outline. With the
face model's python too (local_flow), the face from just above the brows
down is then moved onto the photo by optical flow (region_align.face_flow),
and the render is resampled once for the three. With regions_flow the one
transform is replaced by a field lining the render up region by region —
the rest and the face by affine transforms, the hair by optical flow
faded in from the forehead's edge (region_align.regions) — the parts and
the flow taken as local_flow's on
top, the render still resampled once."""

import math

# A caller leaves out the area the edit changed and a band this many pixels
# wide around it (a Gaussian of this radius, thresholded at 1).
MASK_MARGIN = 8
# A pixel outside it is off when an R, G or B value differs by more than
# this (0–255) from the photo's.
_DRIFT_LEVEL = 10
# Square patches this wide, one every _PATCH_STEP, used where at least
# _PATCH_MIN_CLEAR of one lies outside the changed area, its grey levels
# spread by at least _PATCH_MIN_STD, and the phase correlation's peak
# reaches _PATCH_MIN_PEAK.
_PATCH = 128
_PATCH_STEP = 64
_PATCH_MIN_CLEAR = 0.7
_PATCH_MIN_STD = 6.0
_PATCH_MIN_PEAK = 0.08
_MIN_PATCHES = 6  # fewer usable patches: too few to fit to, left as drawn
_REGISTER_PASSES = 3
# A fit scaling or shearing by more than this, or shifting by more than this
# share of the width or height, is likely a bad match: left as drawn.
_MAX_WARP = 0.05
# A fit moving no pixel by more than this is left out: already lined up.
_MIN_WARP_PX = 0.5

# Local alignment. The face's outline: the jaw's landmarks and the brows'
# raised by this share of the brows' height above the chin, so it takes in
# the bottom of the forehead; feathered by this share of the face's width
# (the jaw's ends apart), the field fading out over about twice that each
# side of it.
_FACE_FOREHEAD = 0.15
_FACE_FEATHER = 0.03
# The transform is fitted outside the face (and the band it fades over)
# where at least this many of _PATCH's patches lie there; with fewer, a
# close-up mostly face and hair, the face is fitted to as well.
_MIN_PATCHES_OUTSIDE_FACE = 2 * _MIN_PATCHES
# The parts matched on their own, by their landmarks (face-landmarks.py's
# _PARTS; the chin is the jaw's middle five), each in its landmarks' box
# padded by _PART_PAD of its longer side each way, at least _PART_MIN px a
# side. A part is matched by phase correlation low-passed at _PART_LOWPASS
# of the Nyquist frequency: plain, the render's redrawn skin grain kept its
# peak at 0.06–0.14 though the parts agreed on their shifts; low-passed, it
# rose to 0.14–0.30, over the 0.03–0.08 of the correlation's next highest
# peak (what a wrong match reaches), and measured a known shift of the photo
# (1.3, -0.7 px, noise added) to 0.05 px on subject0 and subject1. Its shift
# counts where the peak reaches _PART_MIN_PEAK and it moved no further than
# _PART_MAX_SHIFT of the face's width; it is measured, the render moved by
# it, and measured again, once.
_LOCAL_PARTS = {"right_brow": range(17, 22), "left_brow": range(22, 27),
                "right_eye": range(36, 42), "left_eye": range(42, 48),
                "nose": range(27, 36), "mouth": range(48, 68), "chin": range(6, 11)}
_PART_PAD = 0.5
_PART_MIN = 32
_PART_LOWPASS = 0.5
_PART_MIN_PEAK = 0.1
_PART_MAX_SHIFT = 0.04
# Between the parts, the field is their shifts weighted by 1 / (d² + s²), d
# the distance to each part's landmarks' box and s this share of the face's
# width: inside a part, its own shift; between two, a mix.
_PART_SOFTEN = 0.01
# local_flow: the flow taken from _FLOW_ABOVE of the eyes' distance above the
# brows' line (FAN's brow points, column by column) down, eased in over
# _FLOW_EASE of it. Above, a hairline effect grows new hair over the photo's
# bare forehead, which no flow matches: on subject20's hairline_simple_lv1.5
# renders, taken from 0.25 above, the flow ran to its cap there and pulled
# the brows with it. Taken so after the parts, it moved a brow the render
# drew arched over the photo's flatter one back towards it
# (output_subject20_only_local_flow, 2026-11-04: that brow's grey's ZNCC with
# the photo's 0.945 -> 0.957, every brow within 3 px up or down, from 9 of
# the 10, the face's pixels off by >10 about halved), its colour and
# thickness as the render drew them.
_FLOW_ABOVE = 0.08
_FLOW_EASE = 0.1
# local_flow leaves the flow out round a brow hair crosses, in the photo or
# in the render so lined up (_round_brows: the brow's landmarks' box grown
# by _BROW_CLEAR of the eyes' distance across and above, _BROW_CLEAR_BELOW
# below, eased in over _BROW_CLEAR_EASE), there the face as its parts line
# it up, and moves the rest of the face by it: the flow pulls the render's
# brow and strands onto the photo's, and where strands run across a brow in
# one and not the other it drags the brow with them. Left out over the
# whole face, the eyes, nose and mouth lost it too: on subject20, hair over
# both brows (the photo's left one under hair, strands from its temple
# reaching the right one, or the render's new hair), the face's worst part
# 0.9 px off the photo with the parts alone, 0.7 with the flow left out round
# the brows only. Over each brow's inner 1 - _BROW_TAIL (its tail meets the hair at
# the temple in many a photo), either the hair model's hair (the photo's or
# the render's) covers more than _BROW_COVERED of its body (_BROW_BODY of
# the eyes' distance below its landmarks' curve), or the skin just above it
# (_BROW_ABOVE) holds a strand: a dark thin line — the grey's Hessian's
# larger eigenvalue over _STRAND_RIDGE, blurred _STRAND_BLUR px first — at
# more than _STRAND_ANGLE degrees to the brow, at least _STRAND_LENGTH of
# the eyes' distance long and _STRAND_ELONGATION times as long as wide, and
# reaching within _STRAND_REACH of the brow (on subject20 strands from the
# hair at a temple hung over a brow and ended short of it). On
# output/hairline_simple_lv1.5's 15 photos and subject20 (2026-11-04) it
# found the 8 with hair over a brow (subject6, 16, 20, 21, 23, 25, 26 and
# 27) and none of the other 8 or their 31 renders. Taken along the brow alone, its
# contrast with the skin above flagged brows' tails by hair and missed a
# strand a few px wide or a brow under hair all along (subject26, 23).
_BROW_TAIL = 0.25
_BROW_BODY = (-0.02, 0.10)
_BROW_COVERED = 0.2
_BROW_ABOVE = (-0.25, -0.04)
_STRAND_BLUR = 1.2
_STRAND_RIDGE = 2.0
_STRAND_ANGLE = 35
_STRAND_LENGTH = 0.12
_STRAND_ELONGATION = 3.0
_STRAND_REACH = 0.10
_BROW_CLEAR = 0.3
_BROW_CLEAR_BELOW = 0.2
_BROW_CLEAR_EASE = 0.1
_BROWS = {"left": range(17, 22), "right": range(22, 27)}  # by the side of the image
# aiHair: _warp's, _sample's and _resampled's per-pixel work is done this
# many points at a time, a band of rows, not over the whole photo at once:
# the same values, its temporaries a fraction of the size (_sample's
# bicubic took 1.8 GB on a 9.8 MP photo).
_CHUNK = 1 << 18


def _difference(a, b):
    """Per-pixel difference of two same-size RGB images after a 1px Gaussian
    blur, which keeps resampling and compression noise from counting: the
    largest of the three channels' absolute differences. int16, in place
    (aiHair): the blurred images are 8-bit, so it is exactly the float64 it
    was, with a quarter of the memory (0.9 GB of float64 on a 9.8 MP
    photo)."""
    import numpy as np
    from PIL import ImageFilter

    a, b = (np.asarray(im.filter(ImageFilter.GaussianBlur(1)), dtype=np.int16)
            for im in (a, b))
    a -= b
    np.abs(a, out=a)
    return a.max(axis=2)


def _alignment(photo, regions, image) -> dict:
    """How well `image` lines up with the photo outside the hair, lower =
    better: "off", the share of pixels off by more than _DRIFT_LEVEL, and
    "outside", their mean difference."""
    _, outside = regions
    delta = _difference(photo, image)[outside]
    return {"off": float((delta > _DRIFT_LEVEL).mean()), "outside": float(delta.mean())}


def _alignment_line(before: dict, after: dict | None = None) -> str:
    """_alignment's numbers for the console: as drawn, or as drawn -> lined up."""
    if after is None:
        return (f"outside hair: {before['off']:.2%} of pixels off by >{_DRIFT_LEVEL}, "
                f"mean |Δ| {before['outside']:.2f}")
    return (f"outside hair: {before['off']:.2%} -> {after['off']:.2%} of pixels off "
            f"by >{_DRIFT_LEVEL}, mean |Δ| {before['outside']:.2f} -> {after['outside']:.2f}")


def _phase_shift(a, b, lowpass: float | None = None) -> tuple[float, float, float]:
    """Where the content of patch `a` sits in patch `b` (same size, grey):
    the (dx, dy) it moved by, to a fraction of a pixel, and how sharply it
    matched (the phase correlation's peak, 0–1). Each patch is windowed
    (Hann) first, so its edges don't match on their own. With `lowpass`,
    each frequency counts by a Gaussian of that share of the Nyquist
    frequency, so the finest grain, which a render redraws (skin's pores),
    doesn't drown the match."""
    import numpy as np

    window = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    cross = (np.fft.fft2((a - a.mean()) * window)
             * np.conj(np.fft.fft2((b - b.mean()) * window)))
    spectrum = cross / (np.abs(cross) + 1e-9)
    if lowpass is not None:
        fy = np.fft.fftfreq(a.shape[0])[:, None] / 0.5
        fx = np.fft.fftfreq(a.shape[1])[None, :] / 0.5
        weight = np.exp(-0.5 * (fx * fx + fy * fy) / (lowpass * lowpass))
        spectrum = spectrum * (weight / weight.mean())  # a perfect match still peaks at 1
    corr = np.fft.ifft2(spectrum).real
    h, w = corr.shape
    y, x = np.unravel_index(np.argmax(corr), corr.shape)
    peak = corr[y, x]

    def vertex(before, after):  # a parabola through the peak and its neighbours
        curve = before - 2 * peak + after
        return 0.0 if curve == 0 else 0.5 * (before - after) / curve

    dy = y + vertex(corr[(y - 1) % h, x], corr[(y + 1) % h, x])
    dx = x + vertex(corr[y, (x - 1) % w], corr[y, (x + 1) % w])
    # The peak lies at minus the shift, wrapped round the patch.
    dx = dx - w if dx > w / 2 else dx
    dy = dy - h if dy > h / 2 else dy
    return -dx, -dy, float(peak)


def _patch_matches(photo_grey, render_grey, outside):
    """Patches of the photo outside the hair, matched in the render: their
    centres in the photo (N x 2), where each one's content sits in the
    render (N x 2), and how sharply each matched (N). One whose content
    moved by more than a third of its width can't be told from a wrong
    match, so it is left out."""
    import numpy as np

    centres, found, peaks = [], [], []
    for top, left in _patch_boxes(photo_grey, outside):
        box = (slice(top, top + _PATCH), slice(left, left + _PATCH))
        dx, dy, peak = _phase_shift(photo_grey[box], render_grey[box])
        if peak < _PATCH_MIN_PEAK or max(abs(dx), abs(dy)) > _PATCH / 3:
            continue
        x, y = left + _PATCH / 2, top + _PATCH / 2
        centres.append((x, y))
        found.append((x + dx, y + dy))
        peaks.append(peak)
    return np.array(centres).reshape(-1, 2), np.array(found).reshape(-1, 2), np.array(peaks)


def _patch_boxes(photo_grey, outside):
    """The patches _patch_matches matches, as (top, left): at least
    _PATCH_MIN_CLEAR of each outside the hair, its grey levels spread by at
    least _PATCH_MIN_STD."""
    height, width = photo_grey.shape
    for top in range(0, height - _PATCH + 1, _PATCH_STEP):
        for left in range(0, width - _PATCH + 1, _PATCH_STEP):
            box = (slice(top, top + _PATCH), slice(left, left + _PATCH))
            if outside[box].mean() >= _PATCH_MIN_CLEAR and photo_grey[box].std() >= _PATCH_MIN_STD:
                yield top, left


def _fit_affine(src, dst, weights):
    """The affine transform taking `src` points to `dst` (N x 2 each), by
    least squares weighted by `weights`, refitted without the points it
    misses by more than 2 px (or 2.5 x the median miss) until none are left
    out. Returns it as a 3 x 3 matrix, the RMS miss of the points kept, and
    how many were kept."""
    import numpy as np

    points = np.hstack([src, np.ones((len(src), 1))])
    keep = np.ones(len(src), dtype=bool)
    for _ in range(3):
        root_w = np.sqrt(weights[keep])[:, None]
        coef, *_ = np.linalg.lstsq(points[keep] * root_w, dst[keep] * root_w, rcond=None)
        miss = np.linalg.norm(points @ coef - dst, axis=1)
        inliers = miss < max(2.0, 2.5 * float(np.median(miss[keep])))
        if inliers.sum() < 3 or (inliers == keep).all():
            break
        keep = inliers
    return (np.vstack([coef.T, [0.0, 0.0, 1.0]]),
            float(np.sqrt(np.mean(miss[keep] ** 2))), int(keep.sum()))


def _largest_move(matrix, width: int, height: int) -> float:
    """How far, in pixels, the affine `matrix` moves the point it moves
    most in a width x height image — one of its corners."""
    import numpy as np

    corners = np.array([[0, 0, 1], [width, 0, 1], [0, height, 1], [width, height, 1]],
                       dtype=np.float64).T
    return float(np.linalg.norm((matrix @ corners - corners)[:2], axis=0).max())


def _warp(image, matrix, size: tuple[int, int]):
    """`image` resampled (bicubic) onto a `size` canvas, each output pixel p
    taken from `image` at matrix @ p. Where that falls off `image`, its
    nearest edge pixel is used (it is padded by repeating its edges), so no
    black comes in. Returns the warped image and the share of its pixels
    that fell off `image`."""
    import numpy as np
    from PIL import Image

    width, height = size
    xs, ys = np.arange(width) + 0.5, np.arange(height) + 0.5
    # Where each output pixel comes from, a band of rows at a time (_CHUNK).
    off, lo_x, lo_y, hi_x, hi_y = 0, math.inf, math.inf, -math.inf, -math.inf
    step = max(1, _CHUNK // max(1, width))
    for top in range(0, height, step):
        y = ys[top:top + step, None]
        src_x = matrix[0, 0] * xs + matrix[0, 1] * y + matrix[0, 2]
        src_y = matrix[1, 0] * xs + matrix[1, 1] * y + matrix[1, 2]
        off += int(((src_x < 0) | (src_x > image.width) | (src_y < 0)
                    | (src_y > image.height)).sum())
        lo_x, hi_x = min(lo_x, src_x.min()), max(hi_x, src_x.max())
        lo_y, hi_y = min(lo_y, src_y.min()), max(hi_y, src_y.max())
    pad = 2 + math.ceil(max(0.0, -lo_x, -lo_y, hi_x - image.width, hi_y - image.height))
    padded = Image.fromarray(np.pad(np.asarray(image), ((pad, pad), (pad, pad), (0, 0)),
                                    mode="edge"))
    coeffs = (matrix[0, 0], matrix[0, 1], matrix[0, 2] + pad,
              matrix[1, 0], matrix[1, 1], matrix[1, 2] + pad)
    warped = padded.transform(size, Image.Transform.AFFINE, coeffs,
                              resample=Image.Resampling.BICUBIC)
    return warped, off / (width * height)


def register(photo, resized, rendered, regions):
    """The render lined up with the photo outside the hair, or None where it
    is better left as drawn; and lines saying what was done, or why not.
    `resized` is the render at the photo's size, the one matched; `rendered`
    the one warped. `regions` is (hair, outside) boolean arrays.

    The transform is fitted (_fit_affine) to the shifts of patches outside
    the hair (_patch_matches); the render is warped by it and fitted again,
    up to _REGISTER_PASSES times. It is left as drawn with too few patches,
    past _MAX_WARP, within _MIN_WARP_PX, or where the warp leaves no fewer
    pixels outside the hair off than before."""
    _, aligned, lines = _register(photo, resized, rendered, regions)
    return aligned, lines


def _register(photo, resized, rendered, regions):
    """register's work: the transform it warped `rendered` by (3 x 3, the
    photo's pixels to `rendered`'s), the render lined up and register's
    lines; the transform and render None where it is left as drawn."""
    import numpy as np

    width, height = photo.size
    photo_grey = np.asarray(photo.convert("L"), dtype=np.float64)
    before = _alignment(photo, regions, resized)
    total, current, fit = np.eye(3), resized, None
    for _ in range(_REGISTER_PASSES):
        src, dst, peaks = _patch_matches(
            photo_grey, np.asarray(current.convert("L"), dtype=np.float64), regions[1])
        if len(src) < _MIN_PATCHES:
            if fit is None:
                return None, None, [f"left as drawn: {len(src)} patch(es) outside the hair "
                                    f"to match, under {_MIN_PATCHES}", _alignment_line(before)]
            break  # keep what the passes before found
        step, miss, used = _fit_affine(src, dst, peaks)
        total, fit = total @ step, (miss, used)
        if _largest_move(step, width, height) < 0.1:
            break
        current, _ = _warp(resized, total, (width, height))

    centre = np.array([width / 2, height / 2, 1.0])
    off_centre = (total @ centre - centre)[:2]
    moved = _largest_move(total, width, height)
    found = (f"the render was x{total[0, 0]:.4f} as wide and x{total[1, 1]:.4f} as "
             f"tall, ({off_centre[0]:+.1f}, {off_centre[1]:+.1f}) px off at the "
             f"centre, up to {moved:.1f} px at a corner; {fit[1]} patches, "
             f"{fit[0]:.2f} px miss")
    if (max(abs(total[0, 0] - 1), abs(total[1, 1] - 1), abs(total[0, 1]), abs(total[1, 0]))
            > _MAX_WARP or abs(off_centre[0]) > _MAX_WARP * width
            or abs(off_centre[1]) > _MAX_WARP * height):
        return None, None, [f"left as drawn: the fit is past ±{_MAX_WARP:.0%}, more likely "
                            f"a wrong match than a misfit ({found})", _alignment_line(before)]
    if moved < _MIN_WARP_PX:
        return None, None, [f"already lined up: {found}", _alignment_line(before)]

    to_rendered = np.diag([rendered.width / width, rendered.height / height, 1.0])
    matrix = to_rendered @ total
    aligned, edge = _warp(rendered, matrix, (width, height))
    after = _alignment(photo, regions, aligned)
    if after["off"] >= before["off"]:
        return None, None, [f"left as drawn: warped, it was no closer to the photo "
                            f"outside the hair ({found})", _alignment_line(before, after)]
    return matrix, aligned, [f"lined up: {found}"
                             + (f"; edge repeated over {edge:.2%} of it" if edge >= 0.00005
                                else ""),
                             _alignment_line(before, after)]


def line_up(photo, render, hair, face=None, flow_python=None, stages=None, hairs=None):
    """`render` lined up with `photo` outside `hair` (a boolean array, the
    area the edit may change) and a band MASK_MARGIN wide round it, or None
    where it is better left as drawn; and register's lines.
    looping-prompt.py's _line_up, which lines each render up with BEFORE
    outside BEFORE's and TARGET's hair before it is reviewed, of images in
    hand: a render not at the photo's size is resized to it (LANCZOS)
    first, and then is both the image matched and the one warped.

    With `face`, the photo's face (landmarks.face_landmarks), the face is
    lined up locally as well (_line_up_local); with `flow_python` too, the
    face model's python, which has OpenCV, then by optical flow
    (local_flow); with `hairs` too (the photo's hair and the render's,
    apart), lined up region by region (_by_regions) before the parts, in
    place of the one transform (regions_flow). `stages`, a dict, where
    given, gets what the face's lining up went through: the steps taken
    ("steps": "regions", "global" or "as drawn", then "parts" and "flow"
    where kept), the render lined up by the transform or the regions
    ("global"), then by the parts ("local"), and how far the flow moved each
    pixel ("flow", px; local_flow and regions_flow only)."""
    import numpy as np
    from PIL import Image, ImageFilter

    photo = photo.convert("RGB")
    resized = render.convert("RGB")
    if resized.size != photo.size:
        resized = resized.resize(photo.size, Image.Resampling.LANCZOS)
    near = np.asarray(Image.fromarray(hair.astype(np.uint8) * 255)
                      .filter(ImageFilter.GaussianBlur(MASK_MARGIN))) >= 1
    if near.all():
        return None, ["left as drawn: nothing outside the hair to line it up on"]
    if face is None:
        return register(photo, resized, resized, (hair, ~near))
    return _line_up_local(photo, resized, hair, near, face, flow_python, stages, hairs)


def face_weight(face, size: tuple[int, int]):
    """Where the local alignment moves the render on its own, 0–1 (height x
    width, float): 1 inside the face's outline (the jaw, and the brows
    raised by _FACE_FOREHEAD), fading to 0 across it over about twice
    _FACE_FEATHER of the face's width each way; and that width."""
    import numpy as np
    from PIL import Image, ImageDraw

    from . import imaging

    points = face["points"]
    jaw, brows = points[0:17], points[17:27]
    width = float(np.linalg.norm(jaw[16] - jaw[0]))
    up = brows.mean(axis=0) - jaw[8]
    raised = brows + _FACE_FOREHEAD * up
    outline = Image.new("L", size, 0)
    ImageDraw.Draw(outline).polygon([tuple(p) for p in np.vstack([jaw, raised[::-1]])], fill=255)
    return imaging.feathered(np.asarray(outline) > 127, _FACE_FEATHER * width), width


def _sample(a, xs, ys):
    """`a` (height x width x channels, float) at the points `xs`, `ys` (pixel
    coordinates, a pixel's centre at +0.5), interpolated as PIL's BICUBIC
    transform does, its edges repeated as _warp's padding does. Taken a band
    of `xs`' rows at a time (_CHUNK points; aiHair): each point's value is
    its own, so they are the same, and the 16 neighbours' float64 copies
    a band's size, not the photo's."""
    import numpy as np

    xs, ys = np.broadcast_arrays(xs, ys)
    if xs.ndim < 2 or xs.size <= _CHUNK:
        return _sample_block(a, xs, ys)
    step = max(1, _CHUNK // (xs.size // xs.shape[0]))
    out = None
    for top in range(0, xs.shape[0], step):
        block = _sample_block(a, xs[top:top + step], ys[top:top + step])
        if out is None:
            out = np.empty(xs.shape + block.shape[xs.ndim:], block.dtype)
        out[top:top + step] = block
    return out


def _sample_block(a, xs, ys):
    """_sample's work on one band of points."""
    import numpy as np

    h, w = a.shape[:2]
    u, v = xs - 0.5, ys - 0.5
    iu, iv = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    du, dv = (u - iu)[..., None], (v - iv)[..., None]

    def cubic(v1, v2, v3, v4, d):  # PIL's BICUBIC macro (Geometry.c)
        return v2 + d * ((v3 - v1) + d * ((2 * (v1 - v2) + v3 - v4) + d * (v2 - v1 - v3 + v4)))

    rows = []
    for j in range(-1, 3):
        y = np.clip(iv + j, 0, h - 1)
        rows.append(cubic(*(a[y, np.clip(iu + i, 0, w - 1)] for i in range(-1, 3)), du))
    return cubic(*rows, dv)


def _part_shifts(photo_grey, image_grey, face, width: float):
    """How far each of _LOCAL_PARTS moved in `image_grey` from where it is in
    `photo_grey`: name -> (dx, dy, peak, its landmarks' box x0, y0, x1, y1)
    for each part whose shift counts (_PART_MIN_PEAK, _PART_MAX_SHIFT), and
    a note on each that doesn't."""
    import numpy as np

    height, w = photo_grey.shape
    shifts, dropped = {}, []
    for name, indices in _LOCAL_PARTS.items():
        points = face["points"][list(indices)]
        (x0, y0), (x1, y1) = points.min(axis=0), points.max(axis=0)
        pad = _PART_PAD * max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        half_w = max(_PART_MIN, x1 - x0 + 2 * pad) / 2
        half_h = max(_PART_MIN, y1 - y0 + 2 * pad) / 2
        left, right = max(0, round(cx - half_w)), min(w, round(cx + half_w))
        top, bottom = max(0, round(cy - half_h)), min(height, round(cy + half_h))
        if right - left < _PART_MIN or bottom - top < _PART_MIN:
            dropped.append(f"{name} off the photo")
            continue
        a = photo_grey[top:bottom, left:right]
        if a.std() < _PATCH_MIN_STD:
            dropped.append(f"{name} too flat to match")
            continue
        dx, dy, peak = _phase_shift(a, image_grey[top:bottom, left:right], _PART_LOWPASS)
        # Once more, on the render moved by what was found.
        xs, ys = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
        moved = _sample(image_grey[..., None], xs + dx, ys + dy)[..., 0]
        rx, ry, peak = _phase_shift(a, moved, _PART_LOWPASS)
        dx, dy = dx + rx, dy + ry
        if peak < _PART_MIN_PEAK or math.hypot(dx, dy) > _PART_MAX_SHIFT * width:
            dropped.append(f"{name} ({dx:+.1f}, {dy:+.1f}) px, peak {peak:.2f}")
            continue
        shifts[name] = (dx, dy, peak, (x0, y0, x1, y1))
    return shifts, dropped


def _face_field(shifts, weight, box, width: float):
    """The local alignment's field over `box` (left, top, right, bottom) of
    the photo: per pixel, how far from it (dx, dy, height x width x 2) the
    render, lined up outside the face, has its content — the parts'
    `shifts` mixed by _PART_SOFTEN's weights, times the face's `weight`."""
    import numpy as np

    left, top, right, bottom = box
    xs, ys = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
    total = np.zeros(xs.shape + (2,))
    weights = np.zeros(xs.shape)
    for dx, dy, _, (x0, y0, x1, y1) in shifts.values():
        d2 = (np.maximum(np.maximum(x0 - xs, xs - x1), 0) ** 2
              + np.maximum(np.maximum(y0 - ys, ys - y1), 0) ** 2)
        w = 1 / (d2 + (_PART_SOFTEN * width) ** 2)
        total += w[..., None] * (dx, dy)
        weights += w
    return total / weights[..., None] * weight[top:bottom, left:right, None]


def _off_share(photo, image, region) -> float:
    """The share of `region`'s pixels off by more than _DRIFT_LEVEL."""
    return float((_difference(photo, image)[region] > _DRIFT_LEVEL).mean())


def _brow_curve(points):
    """The polyline through a brow's landmarks, sorted along x, every px: its
    points (N x 2), unit normals pointing down (N x 2) and how far along it
    each lies (0–1)."""
    import numpy as np

    p = points[np.argsort(points[:, 0])]
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
    ts = np.arange(0.0, cum[-1], 1.0) if cum[-1] >= 1 else np.zeros(1)
    xs, ys = np.interp(ts, cum, p[:, 0]), np.interp(ts, cum, p[:, 1])
    tx, ty = np.gradient(xs), np.gradient(ys)
    size = np.hypot(tx, ty) + 1e-9
    normals = np.stack([-ty / size, tx / size], axis=1)
    normals[normals[:, 1] < 0] *= -1
    return np.stack([xs, ys], axis=1), normals, ts / max(cum[-1], 1.0)


def _dark_lines(image):
    """Dark thin lines in `image` (PIL): the grey's Hessian's larger
    eigenvalue (over 0 along a dark line), on it blurred _STRAND_BLUR px, and
    the direction each runs in (radians)."""
    import numpy as np
    from PIL import ImageFilter

    grey = np.asarray(image.convert("L").filter(ImageFilter.GaussianBlur(_STRAND_BLUR)),
                      dtype=np.float64)
    gy, gx = np.gradient(grey)
    gyy, _ = np.gradient(gy)
    gxy, gxx = np.gradient(gx)
    big = (gxx + gyy) / 2 + np.sqrt(((gxx - gyy) / 2) ** 2 + gxy ** 2)
    return big, 0.5 * np.arctan2(2 * gxy, gxx - gyy) + np.pi / 2


def _strand(big, line, region, reach, direction: float, eyes: float) -> bool:
    """Whether `region` (bool) holds a strand: _dark_lines' `big` and `line`
    there joined into components at more than _STRAND_ANGLE to `direction`,
    one at least _STRAND_LENGTH of `eyes` long and _STRAND_ELONGATION times
    as long as wide, with a pixel in `reach` (bool: just above the brow)."""
    import numpy as np

    from . import imaging

    off = np.abs((line - direction + np.pi / 2) % np.pi - np.pi / 2)
    labels, sizes = imaging.components(region & (big > _STRAND_RIDGE)
                                       & (off > np.radians(_STRAND_ANGLE)))
    for k in np.nonzero(sizes >= _STRAND_LENGTH * eyes)[0]:
        if k == 0:
            continue
        ys, xs = np.nonzero(labels == k)
        if not reach[ys, xs].any():
            continue
        small, large = np.linalg.eigvalsh(np.cov(np.stack([xs, ys]).astype(np.float64)))
        length, width = 4 * math.sqrt(max(large, 0.0)), 4 * math.sqrt(max(small, 1e-6))
        if length >= _STRAND_LENGTH * eyes and length >= _STRAND_ELONGATION * width:
            return True
    return False


def _broken_brows(photo, image, hair, face) -> dict:
    """The brows of the photo's face that hair crosses in `photo` or in
    `image` (PIL, lined up with it), by the side of the image ("left",
    "right") -> why: the hair model's (`hair`, bool: the photo's and the
    image's) over the brow's body, or a strand in the skin above it
    (_BROW_TAIL's note)."""
    import numpy as np
    from PIL import Image, ImageDraw

    points = face["points"]
    eyes = float(np.linalg.norm(points[36:42].mean(axis=0) - points[42:48].mean(axis=0)))
    # The work in the brows' neighbourhood only.
    left, top = np.maximum(np.floor(points[17:27].min(axis=0) - 0.5 * eyes), 0).astype(int)
    right, bottom = np.ceil(points[17:27].max(axis=0) + 0.5 * eyes).astype(int)
    right, bottom = min(right, photo.width), min(bottom, photo.height)
    box = (left, top, right, bottom)
    near = hair[top:bottom, left:right]
    lines = {"photo": _dark_lines(photo.crop(box)), "render": _dark_lines(image.crop(box))}
    broken = {}
    for side, indices in _BROWS.items():
        pos, normals, along = _brow_curve(points[list(indices)] - (left, top))
        # A brow's tail is its outer end: the left one's at its start.
        inner = (1 - along if side == "left" else along) < 1 - _BROW_TAIL
        if inner.sum() < 2:
            continue
        pos, normals = pos[inner], normals[inner]

        def at(lo, hi, n):
            offs = np.linspace(lo * eyes, hi * eyes, n)
            xs = np.clip(np.rint(pos[:, 0, None] + normals[:, 0, None] * offs), 0,
                         near.shape[1] - 1).astype(int)
            ys = np.clip(np.rint(pos[:, 1, None] + normals[:, 1, None] * offs), 0,
                         near.shape[0] - 1).astype(int)
            return ys, xs

        covered = float(near[at(*_BROW_BODY, 8)].mean())
        if covered > _BROW_COVERED:
            broken[side] = f"hair over {covered:.0%} of its body (the hair model's)"
            continue
        def between(lo, hi):  # the band from lo to hi of `eyes` along the normals
            upper, lower = pos + normals * lo * eyes, pos + normals * hi * eyes
            mask = Image.new("L", near.shape[::-1], 0)
            ImageDraw.Draw(mask).polygon(
                [tuple(p) for p in upper] + [tuple(p) for p in lower[::-1]], fill=255)
            return np.asarray(mask) > 0

        region = between(*_BROW_ABOVE) & ~near
        reach = between(-_STRAND_REACH, _BROW_ABOVE[1])
        chord = pos[-1] - pos[0]
        direction = math.atan2(chord[1], chord[0])
        for which, (big, line) in lines.items():
            if _strand(big, line, region, reach, direction, eyes):
                broken[side] = f"a strand across it in the {which}"
                break
    return broken


def _round_brows(face, shape, sides):
    """How much of local_flow's flow each pixel loses for `sides`' brows
    (_BROWS' keys), 0–1 (height x width): 1 in each one's landmarks' box
    grown by _BROW_CLEAR of the eyes' distance across and above and
    _BROW_CLEAR_BELOW below, eased out over _BROW_CLEAR_EASE."""
    import numpy as np

    points = face["points"]
    eyes = float(np.linalg.norm(points[36:42].mean(axis=0) - points[42:48].mean(axis=0)))
    ys, xs = np.mgrid[:shape[0], :shape[1]] + 0.5
    out = np.zeros(shape)
    for side in sides:
        p = points[list(_BROWS[side])]
        x0, y0 = p.min(axis=0) - (_BROW_CLEAR * eyes, _BROW_CLEAR * eyes)
        x1, y1 = p.max(axis=0) + (_BROW_CLEAR * eyes, _BROW_CLEAR_BELOW * eyes)
        # How far outside the box, eased out from its edge.
        d = np.hypot(np.maximum(np.maximum(x0 - xs, xs - x1), 0),
                     np.maximum(np.maximum(y0 - ys, ys - y1), 0))
        out = np.maximum(out, np.clip(1 - d / (_BROW_CLEAR_EASE * eyes), 0, 1))
    return out


def _flow_weight(face, weight):
    """Where local_flow takes the flow, 0–1: the face's `weight` from
    _FLOW_ABOVE of the eyes' distance above the brows' line down, eased in
    over _FLOW_EASE of it."""
    import numpy as np

    points = face["points"]
    brows = points[17:27]
    order = np.argsort(brows[:, 0])
    eyes = float(np.linalg.norm(points[36:42].mean(axis=0) - points[42:48].mean(axis=0)))
    ys, xs = np.mgrid[:weight.shape[0], :weight.shape[1]]
    line = np.interp(xs, brows[order, 0], brows[order, 1]) - _FLOW_ABOVE * eyes
    return weight * np.clip((ys - line) / (_FLOW_EASE * eyes) + 0.5, 0, 1)


def _resampled(resized, base, to_render, box, inside, qx, qy):
    """`base` with the pixels `inside` (a mask over `box`: left, top, right,
    bottom) taken from `resized` at to_render(qx, qy), (qx, qy) being where
    each one's content sits in the photo's frame as the transform or the
    regions line it up: the render resampled from as drawn, once. A band
    of the box's rows at a time (_CHUNK; aiHair): to_render and _sample
    take each point on its own, so the pixels are the same."""
    import numpy as np
    from PIL import Image

    left, top, right, bottom = box
    image = np.asarray(resized, np.float32)
    out = np.array(base)
    region = out[top:bottom, left:right]
    step = max(1, _CHUNK // max(1, right - left))
    for row in range(0, bottom - top, step):
        rows = slice(row, row + step)
        src_x, src_y = to_render(qx[rows], qy[rows])
        moved = _sample(image, src_x, src_y)
        here = inside[rows]
        region[rows][here] = np.clip(np.rint(moved[here]), 0, 255)
    return Image.fromarray(out)


def _by_regions(photo, resized, hairs, face, flow_python):
    """regions_flow's first step: `resized` lined up region by region
    (region_align.regions), the face the one align.face_weight outlines with
    the bare forehead above it (blend.above_brows less the photo's hair),
    the hair's flow faded in from its part above the brows' top.
    Returns (where each point of the photo's frame sits in the render: a
    function of x and y, the render so lined up, the lines on it), or
    (None, None, the line) where it fails."""
    import numpy as np

    from . import blend, region_align

    photo_hair, render_hair = hairs
    area = ((face_weight(face, photo.size)[0] > 0.5)
            | (blend.above_brows(face, photo_hair) & ~photo_hair))
    rows = np.arange(photo.height)[:, None] + 0.5
    forehead = area & (rows < face["points"][17:27, 1].min())
    try:
        field, report = region_align.regions(photo, resized, photo_hair, render_hair, area,
                                             forehead, flow_python)
    except RuntimeError as e:
        return None, None, [f"by region failed, one transform instead — {e}"]
    field = field.astype(np.float64)

    def to_render(qx, qy):
        at = _sample(field, qx, qy)
        return qx + at[..., 0], qy + at[..., 1]

    height, width = field.shape[:2]
    xs, ys = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    aligned = _resampled(resized, resized, to_render, (0, 0, width, height),
                         np.ones((height, width), bool), xs, ys)
    diffs = ", ".join(f"{k.split()[0]} {v[0]} -> {v[1]}" for k, v in report.items()
                      if k.endswith("|diff|"))
    return to_render, aligned, [
        f"lined up by region: the rest by an affine ({report['rest']}), the face by another "
        f"({report['face']}), the hair by {report['hair']}; mean |diff| from the photo: "
        f"{diffs}"]


def _line_up_local(photo, resized, hair, near, face, flow_python=None, stages=None,
                   hairs=None):
    """line_up with the face lined up on its own: the transform fitted
    outside the hair and the face (register), unless too few patches lie
    there (_MIN_PATCHES_OUTSIDE_FACE) — or with `hairs` and `flow_python`,
    the regions (_by_regions) in its place; then each part of the face matched in
    the render so lined up (_part_shifts), inside the face the transform
    and the parts' field (_face_field) taking each pixel; with
    `flow_python`, then the flow (region_align.face_flow) over _flow_weight,
    found on the render so lined up. The render is resampled once, for all
    of them. The face is kept as the transform has it where no part can be
    matched, or where moving it leaves no fewer pixels of the face outside
    the hair off than before; the flow is left out so too, and round a
    brow hair crosses in the photo or the render (_broken_brows,
    _round_brows). `stages`: line_up's."""
    import numpy as np

    weight, width = face_weight(face, photo.size)
    reach = weight > 0.004
    photo_grey = np.asarray(photo.convert("L"), dtype=np.float64)
    to_render, aligned, lines = None, None, []
    if hairs is not None and flow_python is not None:
        to_render, aligned, lines = _by_regions(photo, resized, hairs, face, flow_python)
    steps = ["regions"] if to_render is not None else []
    if to_render is None:
        outside = ~near & ~reach
        clear = sum(1 for _ in _patch_boxes(photo_grey, outside))
        if clear >= _MIN_PATCHES_OUTSIDE_FACE:
            lines.append(f"fitted outside the face: {clear} patches there")
        else:
            outside = ~near
            lines.append(f"fitted with the face: {clear} patches outside it, under "
                         f"{_MIN_PATCHES_OUTSIDE_FACE}")
        matrix, aligned, global_lines = _register(photo, resized, resized, (hair, outside))
        lines += global_lines
        m = matrix if matrix is not None else np.eye(3)

        def to_render(qx, qy):
            return m[0, 0] * qx + m[0, 1] * qy + m[0, 2], m[1, 0] * qx + m[1, 1] * qy + m[1, 2]

        steps.append("global" if aligned is not None else "as drawn")
    base = aligned if aligned is not None else resized
    if stages is not None:
        stages["steps"], stages["global"] = steps, base

    shifts, dropped = _part_shifts(photo_grey, np.asarray(base.convert("L"), dtype=np.float64),
                                   face, width)
    found = ", ".join(f"{name} ({dx:+.1f}, {dy:+.1f})" for name, (dx, dy, _, _) in shifts.items())
    lines.append("face: " + (found + " px" if found else "no part matched")
                 + (f"; left out: {', '.join(dropped)}" if dropped else ""))
    check = (weight > 0.5) & ~near
    # The parts' field over the whole photo where it is kept (0 elsewhere),
    # the render as it and the transform line it up, and what line_up returns.
    field = np.zeros(weight.shape + (2,))
    out, kept = base, aligned
    if shifts:
        rows, cols = np.nonzero(reach)
        box = (cols.min(), rows.min(), cols.max() + 1, rows.max() + 1)
        left, top, right, bottom = box
        part = _face_field(shifts, weight, box, width)
        xs, ys = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
        local = _resampled(resized, base, to_render, box, reach[top:bottom, left:right],
                           xs + part[..., 0], ys + part[..., 1])
        largest = float(np.abs(part).max())
        if not check.any():
            lines.append("face kept as fitted: none of it outside the hair to check against")
        else:
            before, after = _off_share(photo, base, check), _off_share(photo, local, check)
            if after >= before:
                lines.append(f"face kept as fitted: moved (up to {largest:.1f} px), it was no "
                             f"closer to the photo ({before:.2%} -> {after:.2%} of its pixels "
                             f"outside the hair off by >{_DRIFT_LEVEL})")
            else:
                lines.append(f"face lined up: moved up to {largest:.1f} px, {before:.2%} -> "
                             f"{after:.2%} of its pixels outside the hair off by "
                             f">{_DRIFT_LEVEL}")
                field[top:bottom, left:right] = part
                out = kept = local
                steps.append("parts")
    if stages is not None:
        stages["local"] = out
    if flow_python is None or not check.any():
        return kept, lines
    from . import region_align

    area = _flow_weight(face, weight)
    broken = _broken_brows(photo, out, hair, face)
    if broken:
        area = area * (1 - _round_brows(face, area.shape, broken))
        lines.append("flow left out round " + "; ".join(
            f"the brow on the image's {side}, {why}" for side, why in broken.items())
            + ": there the face as its parts line it up")
        if not (area > 0.004).any():
            return kept, lines
    try:
        flow = region_align.face_flow(photo, out, hair, area, flow_python)
    except RuntimeError as e:
        lines.append(f"flow left out: it failed — {e}")
        return kept, lines
    size = np.hypot(flow[..., 0], flow[..., 1])
    if stages is not None:
        stages["flow"] = size
    rows, cols = np.nonzero(area > 0.004)
    box = (cols.min(), rows.min(), cols.max() + 1, rows.max() + 1)
    left, top, right, bottom = box
    xs, ys = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
    qx, qy = xs + flow[top:bottom, left:right, 0], ys + flow[top:bottom, left:right, 1]
    # Through the flow to where the render, lined up, has each pixel's
    # content, then through the parts' field there.
    at = _sample(field, qx, qy)
    flowed = _resampled(resized, out, to_render, box, (area > 0.004)[top:bottom, left:right],
                        qx + at[..., 0], qy + at[..., 1])
    before, after = _off_share(photo, out, check), _off_share(photo, flowed, check)
    if after >= before:
        lines.append(f"flow left out: it moved the face up to {size.max():.1f} px and it was "
                     f"no closer to the photo ({before:.2%} -> {after:.2%} of its pixels "
                     f"outside the hair off by >{_DRIFT_LEVEL})")
        return kept, lines
    lines.append(f"face moved by its flow: up to {size.max():.1f} px, {before:.2%} -> "
                 f"{after:.2%} of its pixels outside the hair off by >{_DRIFT_LEVEL}")
    steps.append("flow" + (f" (not round the {'+'.join(broken)} brow{'s' * (len(broken) > 1)})"
                           if broken else ""))
    return flowed, lines
