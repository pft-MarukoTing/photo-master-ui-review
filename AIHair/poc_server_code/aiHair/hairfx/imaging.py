"""Image helpers the effects share: renders sized back to the photo and
their hair's colours matched to it, masks (grown, feathered, tidied; dilated,
eroded and closed by a disc, split into connected parts), overlays,
images.edit file tuples and review sheets. A mask is a numpy bool array,
height x width, True = in it; an image is PIL."""

import io
import math
import os
import threading

# --size match: the photo's aspect at about this many pixels.
_TARGET_PIXELS = 1536 * 1024
# aiHair: the lining up's fits — align.register's patches and transform,
# region_align's ECC and optical flow (regions, face_flow, shifted) — made on
# the photo and the render shrunk to at most this many pixels, what they
# find taken back to the full frame and applied there once (estimate_scale).
# 2 MP by default (2026-11-10); HAIRFX_ESTIMATE_MAX_PIXELS sets it, 0 fits
# them at the photo's own size, as before. At 9.8 MP, hairline's flow alone
# took 1.9 GB in each of its two child processes (DIS down to the full
# resolution); at 2 MP its two children took ~0.7 and ~0.55 GB whatever the
# photo, the results within 3 levels on 99% of the pixels up to 4 MP
# (output/mem_profile_20261110/cat2, fake renders).
ESTIMATE_MAX_PIXELS = int(os.environ.get("HAIRFX_ESTIMATE_MAX_PIXELS", "2000000") or 0)


def estimate_scale(width: int, height: int) -> float:
    """The scale (at most 1) the lining up's fits run at for a photo of
    `width` x `height`: down to ESTIMATE_MAX_PIXELS pixels, 1 where it has
    no more or that is 0."""
    if not ESTIMATE_MAX_PIXELS or width * height <= ESTIMATE_MAX_PIXELS:
        return 1.0
    return math.sqrt(ESTIMATE_MAX_PIXELS / (width * height))


def scaled_size(width: int, height: int, scale: float) -> tuple[int, int]:
    """(width, height) times `scale`, rounded, at least 1 px each."""
    return max(1, round(width * scale)), max(1, round(height * scale))


def shrunk(a, size: tuple[int, int]):
    """`a` — an image (PIL) or an array (height x width [x channels], any
    dtype OpenCV resizes) — area-averaged to `size` (width, height): a PIL
    image back for an image. A bool mask comes back a mask: True where more
    than half of what it averages was."""
    import cv2
    import numpy as np
    from PIL import Image

    if isinstance(a, Image.Image):
        mode = a.mode if a.mode in ("L", "RGB", "RGBA") else "RGB"
        return Image.fromarray(cv2.resize(np.asarray(a.convert(mode)), size,
                                          interpolation=cv2.INTER_AREA), mode)
    a = np.asarray(a)
    if a.dtype == bool:
        return cv2.resize(a.astype(np.uint8) * 255, size, interpolation=cv2.INTER_AREA) > 127
    return cv2.resize(a, size, interpolation=cv2.INTER_AREA)


def field_grown(field, size: tuple[int, int]):
    """An offset field (height x width x 2: dx, dy in its own pixels), found
    on a frame shrunk from one of `size` (width, height), as the full
    frame's: resized bilinearly, pixel centres kept (OpenCV's), and each
    offset scaled to the full frame's pixels."""
    import cv2
    import numpy as np

    height, width = field.shape[:2]
    out = cv2.resize(np.ascontiguousarray(field, np.float32), size,
                     interpolation=cv2.INTER_LINEAR)
    out[..., 0] *= size[0] / width
    out[..., 1] *= size[1] / height
    return out
# fit_render: a render shrunk to the photo's size is blurred first by a
# Gaussian of _SHRINK_BLUR * sqrt(f² - 1) of its px, f its size over the
# photo's — the detail between the photo's pixel and the render's, which
# area averaging alone folds back as stair-steps across fine strands; none
# at the photo's size, as skimage's (f - 1) / 2. On subject27's frizzCare_2
# renders (output_raw_check: 1136x1376 to 470x568, f 2.42, 0.97 px, 0.4 of
# the photo's) area averaging alone drew stair-steps across the diagonal
# strands that the renders hadn't; 0.4 of the photo's px took them off,
# the hair's RMS under 2 px 12.0 -> 10.6 (at 0.29 some were left; 0.5
# came out softer, 10.0).
_SHRINK_BLUR = 0.44
# Review sheets: every tile this tall, under a label band.
_SHEET_TILE_HEIGHT = 1024
# colour_matched: a colour matrix fitted _MATCH_PASSES times, on the
# _MATCH_KEEP of the hair's blocks that differ least — the means of blocks
# _MATCH_BLOCK of the long side square, those wholly in the hair, at least
# _MATCH_MIN_BLOCKS of them (smaller blocks where fewer) — where the photo
# and the render share at least _MATCH_MIN_HAIR of the picture's hair; taken
# in over _MATCH_FEATHER of the long side inside the hair's outline.
_MATCH_KEEP = 0.7
_MATCH_PASSES = 3
_MATCH_BLOCK = 0.005
_MATCH_MIN_BLOCKS = 30
_MATCH_MIN_HAIR = 0.002
_MATCH_FEATHER = 0.003
# colour_matched's shift_left_out: the hair its fit leaves out shifted by the
# change the kept hair round it takes, averaged over a Gaussian
# _MATCH_SPREAD of the long side across, the kept hair's median change mixed
# in at _MATCH_SPREAD_FLOOR, so it takes over where little is kept near.
_MATCH_SPREAD = 0.04
_MATCH_SPREAD_FLOOR = 0.02
# Its least squares, one thread at a time: called from several at once (main.py's
# photos, 4 workers), numpy's OpenBLAS (scipy-openblas 0.3.31) crashed the
# process with an access violation in lstsq, on every run. Its matrix products
# are einsum's, which doesn't go through BLAS.
_LSTSQ_LOCK = threading.Lock()
# whole_colour_matched (main.py --colour-match-area whole): the samples'
# _colour_match, fitted _WHOLE_PASSES times on every _WHOLE_STRIDE-th pixel
# of the _WHOLE_KEEP share that differ least.
_WHOLE_KEEP = 0.7
_WHOLE_PASSES = 3
_WHOLE_STRIDE = 7
# inset_weight (main.py --colour-match-area hair_inset, frizzCare_3's): the
# hair drawn in from its outline by _INSET_HAIR of the long side, and
# further, by _INSET_FACE, from the face's skin above the eyes (face_skin:
# the jaw's outline closed by the brows lifted to the hairline — the lowest
# row across them, up from their middle, that is _HAIRLINE_SOLID hair —
# plus _FACE_SKIN_ABOVE of the face's height), each blurred by half its
# reach.
_INSET_HAIR = 0.004
_INSET_FACE = 0.03
# inset_weight: the hair's blur, set so its outline takes this much of the
# matrix (a Gaussian's share past r / its z-score): at half the erosion,
# the outline took 0.02, and the photo's frizz the render combed away,
# background now, took all of it (frizzCare's subject10, 2026-11-09).
_INSET_EDGE = 0.25
_HAIRLINE_SOLID = 0.9
_FACE_SKIN_ABOVE = 0.05


def match_size(width: int, height: int) -> str:
    """A `WIDTHxHEIGHT` with the photo's aspect ratio at ~_TARGET_PIXELS,
    both edges multiples of 16 and the aspect clamped to the API's 1:3–3:1
    window, so the render lines up with the photo."""
    aspect = min(max(width / height, 1 / 3), 3)
    h = math.sqrt(_TARGET_PIXELS / aspect)
    w = h * aspect
    return f"{round(w / 16) * 16}x{round(h / 16) * 16}"


def area_resize(image, width: int, height: int):
    """`image` (RGB) shrunk to width x height by area averaging, as OpenCV's
    INTER_AREA does: each output pixel is the mean of the source area it
    covers, pixels it only partly covers weighted by the part covered.

    Not Pillow's BOX filter, which only looks at whether a source pixel's
    centre falls inside the output pixel: at a non-integer factor, as here
    (1536 -> 1024, 1024 -> 768, ...), it weights them unevenly — 6 -> 4
    gives an output pixel that is one source pixel, unaveraged, instead of
    2/3 of one and 1/3 of the next. Needs numpy (ImportError without it)."""
    import numpy as np
    from PIL import Image

    def shrink_axis(a, n_out: int, axis: int):
        n_in = a.shape[axis]
        # Output pixel k covers source [k*n_in/n_out, (k+1)*n_in/n_out); its
        # sum is the source's running total between those edges, with the
        # partly covered pixel at each edge counted by its covered part.
        k = np.arange(n_out + 1)
        whole, part = (k * n_in) // n_out, (k * n_in) % n_out / n_out
        shape = [1] * a.ndim
        shape[axis] = -1
        totals = np.concatenate([np.zeros_like(a.take([0], axis)),
                                 np.cumsum(a, axis=axis)], axis=axis)
        at_edge = (totals.take(whole, axis)
                   + part.reshape(shape) * a.take(np.minimum(whole, n_in - 1), axis))
        return np.diff(at_edge, axis=axis) * (n_out / n_in)

    a = np.asarray(image, dtype=np.float64)  # height x width x 3
    a = shrink_axis(shrink_axis(a, height, 0), width, 1)
    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8), "RGB")


def fit_render(image_bytes: bytes, size: tuple[int, int], keep_rendered_size: bool = False):
    """A render as an RGB image at the photo's `size` (width, height), so it
    lines up pixel-for-pixel with it — as rendered with `keep_rendered_size`
    — and a warning where its aspect is off the photo's, or None.

    A plain resize, not a crop: with --size match the render's aspect is
    within 16px rounding of the photo's, and the model fills that whole
    canvas with the photo's framing, so stretching back realigns it. A
    render larger than the photo on both sides is blurred by a Gaussian of
    _SHRINK_BLUR * sqrt(f² - 1) px, f its size over the photo's, so its
    finest strands don't fold back as stair-steps, and shrunk by area
    averaging (area_resize, as OpenCV's INTER_AREA); any other resize uses
    LANCZOS."""
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as rendered:
        rgb = rendered.convert("RGB")
    if keep_rendered_size:
        return rgb, None
    return fit_image(rgb, size)


def fit_image(rgb, size: tuple[int, int]):
    """fit_render's resize, of an RGB image already decoded (aiHair:
    frizzCare's smooth_at=render grows its smoothed render to the photo's
    size so, as the render would have been): `rgb` at `size`, and the
    warning where its aspect is off, or None."""
    from PIL import Image, ImageFilter

    width, height = size
    if rgb.size == (width, height):
        return rgb, None
    warning = None
    if abs(rgb.width / rgb.height - width / height) > 0.02 * width / height:
        warning = (f"render {rgb.width}x{rgb.height} has a different aspect from the photo "
                   f"{width}x{height}; it is stretched back to it")
    if rgb.width >= width and rgb.height >= height:
        f = math.sqrt(rgb.width * rgb.height / (width * height))
        sigma = _SHRINK_BLUR * math.sqrt(max(f * f - 1, 0))
        if sigma > 0:
            rgb = rgb.filter(ImageFilter.GaussianBlur(sigma))
        return area_resize(rgb, width, height), warning
    return rgb.resize((width, height), Image.Resampling.LANCZOS), warning


def colour_matched(render, photo, photo_hair, render_hair, shift_left_out: bool = False,
                   weight=None):
    """`render` (RGB, at the photo's size) with its hair's colours pulled
    back to `photo`'s, and how much of it each pixel took (0–1), or None
    where they share under _MATCH_MIN_HAIR of the picture's hair:
    run_Fluffy_samples.py's and run_HairPart_samples.py's _colour_match,
    kept to the hair (`photo_hair`, `render_hair`: the in-house hair model's).
    gpt-image drifts the hair's colours — more saturated, darker — even
    where told to keep them, so a 3x3 colour matrix plus offset (render ->
    photo) is fitted by least squares on the hair both have, where the edit
    left it alone, and taken in the hair either has, faded in over
    _MATCH_FEATHER of the long side inside its outline. The rest — face,
    lips, background — stays as rendered: fitted and applied over the whole
    render, as the samples do, the matrix, fitted mostly on near-grey
    background, skin and hair, swung the lips' red by its cross terms (on
    frizzCare, the lips ended further off the photo's in 29% of results);
    fitted on the hair and taken past its outline, it whitened the
    background there. The hair the edit left alone is found by refitting,
    _MATCH_PASSES times, on the _MATCH_KEEP of it that differs least, so
    what the edit changed (a raised crown, a filled parting) doesn't steer
    the fit.

    It is fitted on block means, _MATCH_BLOCK of the long side square, not
    pixels: the render's strands don't fall where the photo's do (it redraws
    them, and lining up leaves a few pixels), so pixel by pixel the
    difference is mostly one strand against another, and the least-changed
    pick didn't tell the hair the edit recoloured from the rest. A block
    averages the strands out to the hair's colour there. Only blocks wholly
    in the shared hair count, so none takes in background or skin.

    With `shift_left_out`, the hair the fit leaves out — what the edit
    recoloured, with the rest that differs most — doesn't take the matrix
    but the change the kept hair round it takes, as a shift
    (_left_out_shift): the matrix's gain, over 1 where the render's hair is
    flatter than the photo's, made rootRetouch's recoloured roots' darks
    darker and lights lighter, and pulled them back to the photo's colour by
    their own colour. On rootRetouch_1's 96 results, the L* spread of the
    hair the render changed most went from 1.09x as drawn (median; 1.21x
    at the 90th percentile) to 1.02x (1.06x), and the share of the edit's
    contrast with the rest undone from 0.07 to 0.04 (0.33 to 0.26).

    Where `weight` (0–1, the image's size) is given, each pixel takes that
    much of the matrix instead (inset_weight's)."""
    import numpy as np
    from PIL import Image

    shared = photo_hair & render_hair
    if shared.sum() < _MATCH_MIN_HAIR * shared.size:
        return None
    rgb = np.asarray(render.convert("RGB"), np.float32)
    photo_rgb = np.asarray(photo.convert("RGB"), np.float32)
    size = max(1, round(_MATCH_BLOCK * max(shared.shape)))
    while True:
        inside = _block_means(shared, size) >= 0.999
        if size == 1 or inside.sum() >= _MATCH_MIN_BLOCKS:
            break
        size //= 2
    out, ref = _block_means(rgb, size)[inside], _block_means(photo_rgb, size)[inside]
    x = np.c_[out, np.ones(len(out), np.float32)]
    keep = np.ones(len(x), bool)
    for _ in range(_MATCH_PASSES):
        with _LSTSQ_LOCK:
            m, *_ = np.linalg.lstsq(x[keep], ref[keep], rcond=None)
        residual = np.abs(np.einsum("nk,kc->nc", x, m) - ref).sum(1)
        cut = np.quantile(residual, _MATCH_KEEP)
        keep = residual <= cut
    if weight is None:
        # 0.5 on the outline, so 0 there and outside it, 1 a feather's twice in.
        weight = np.clip(2 * feathered(photo_hair | render_hair,
                                       _MATCH_FEATHER * max(shared.shape)) - 1, 0, 1)
    near = box(weight > 0)
    matched = rgb.copy()
    part = rgb[near]
    full = np.concatenate([part, np.ones(part.shape[:2] + (1,), np.float32)], axis=2)
    a = weight[near][..., None]
    target = np.clip(np.einsum("hwk,kc->hwc", full, m), 0, 255)
    if shift_left_out:
        taken, shift = _left_out_shift(rgb, photo_rgb, shared, size, m, cut)
        t = taken[near][..., None]
        target = part + t * (target - part) + (1 - t) * shift[near]
    matched[near] = part * (1 - a) + target * a
    return Image.fromarray(np.clip(matched, 0, 255).round().astype(np.uint8)), weight


def whole_colour_matched(render, photo):
    """`render` (RGB, at the photo's size) with its colours pulled back to
    `photo`'s over the whole picture, as run_Fluffy_samples.py's and
    run_HairPart_samples.py's _colour_match (their after_correction) do:
    one 3x3 colour matrix plus offset (render -> photo), fitted by least
    squares on every _WHOLE_STRIDE-th pixel, refitted _WHOLE_PASSES times on
    the _WHOLE_KEEP of the pixels that differ least, so what the edit
    changed doesn't steer it, and taken over every pixel — the face, the
    lips and the background with the hair, which colour_matched leaves as
    drawn. The samples' arithmetic, its matrix products einsum's and its
    least squares one thread at a time (_LSTSQ_LOCK). Returns it and how
    much of the matrix each pixel took: all of it."""
    import numpy as np
    from PIL import Image

    out = np.asarray(render.convert("RGB"), np.float32).reshape(-1, 3)
    ref = np.asarray(photo.convert("RGB"), np.float32).reshape(-1, 3)
    x = np.c_[out, np.ones(len(out), np.float32)]
    keep = np.ones(len(x), bool)
    for _ in range(_WHOLE_PASSES):
        with _LSTSQ_LOCK:
            m, *_ = np.linalg.lstsq(x[keep][::_WHOLE_STRIDE], ref[keep][::_WHOLE_STRIDE],
                                    rcond=None)
        residual = np.abs(np.einsum("nk,kc->nc", x, m) - ref).sum(1)
        keep = residual <= np.quantile(residual, _WHOLE_KEEP)
    matched = np.clip(np.einsum("nk,kc->nc", x, m), 0, 255).round().astype(np.uint8)
    return (Image.fromarray(matched.reshape(render.height, render.width, 3)),
            np.ones((render.height, render.width), np.float32))


def face_skin(hair, points):
    """The face's skin from the eyes up, as a mask: not `hair`, inside the
    jaw's outline (FAN's 68 `points`: 0–16) closed by the brows (17–26)
    lifted, on the face's axis (the chin to the brows' middle), to the
    hairline plus _FACE_SKIN_ABOVE of the face's height, at or above the
    eyes' bottom. The hairline is the lowest row up from the brows'
    middle, across them, that is _HAIRLINE_SOLID `hair`; with none, 0.4 of
    the face's height up."""
    import numpy as np
    from PIL import Image, ImageDraw

    height, width = hair.shape
    points = np.asarray(points, np.float32)
    mid = (points[21] + points[22]) / 2
    up = mid - points[8]
    face_height = float(np.hypot(*up))
    up /= face_height
    side = np.array([-up[1], up[0]], np.float32)
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    t = (xs - mid[0]) * up[0] + (ys - mid[1]) * up[1]  # up from the brows' middle
    s = (xs - mid[0]) * side[0] + (ys - mid[1]) * side[1]
    ends = sorted(float((p - mid) @ side) for p in (points[17], points[26]))
    rows, top = t.astype(np.int32), int(face_height)
    across = (s >= ends[0]) & (s <= ends[1]) & (rows >= 0) & (rows <= top)
    count = np.bincount(rows[across], minlength=top + 1)
    cover = np.bincount(rows[across], weights=hair[across].astype(np.float64),
                        minlength=top + 1) / np.maximum(count, 1)
    solid = cover >= _HAIRLINE_SOLID
    hairline = int(np.argmax(solid)) if solid.any() else 0.4 * face_height
    # The brows lifted onto one line across the face, so the jaw and they
    # close the outline.
    brows = points[17:27]
    lifted = brows + up * (hairline + _FACE_SKIN_ABOVE * face_height
                           - (brows - mid) @ up)[:, None]
    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).polygon([tuple(p) for p in np.r_[points[0:17], lifted[::-1]]],
                                   fill=255)
    eyes = min(float((p - mid) @ up) for p in points[36:48])
    return (np.asarray(canvas) > 0) & ~hair & (t >= eyes)


def inset_weight(hair, skin=None):
    """colour_matched's weight for --colour-match-area hair_inset: `hair`
    (the output's: core._colour_match_outputs) eroded by _INSET_HAIR of the
    long side and blurred so its outline takes _INSET_EDGE, times `hair` (0
    off it), times, where `skin` (face_skin) is given, the share
    at least _INSET_FACE from it, blurred by half that. The skin the hair
    mask takes in round its outline, and between a fringe's strands, takes
    none of the matrix, and the hair's outline away from the face (the
    crown's against the background) is drawn in as the rest of it is."""
    from statistics import NormalDist

    long_side = max(hair.shape)
    r = max(1, round(_INSET_HAIR * long_side))
    sigma = r / NormalDist().inv_cdf(1 - _INSET_EDGE)
    weight = feathered(~dilated(~hair, r), sigma) * hair
    if skin is not None and skin.any():
        r = max(1, round(_INSET_FACE * long_side))
        weight = weight * feathered(~dilated(skin, r), r / 2)
    return weight


def _block_means(a, size: int):
    """`a` (height x width [x channels]) averaged over size x size blocks,
    the last part-blocks of each side left out."""
    import numpy as np

    height, width = a.shape[0] // size * size, a.shape[1] // size * size
    a = a[:height, :width].astype(np.float32)
    return a.reshape(height // size, size, width // size, size, *a.shape[2:]).mean(axis=(1, 3))


def _left_out_shift(rgb, photo_rgb, shared, size: int, m, cut: float):
    """colour_matched's shift_left_out, per pixel: how much of the matrix
    `m` it takes (0–1), and the shift (height x width x 3, added) it takes
    for the rest. A size-square block at least half in `shared` is kept,
    and takes the matrix, where its means over that hair are within `cut`
    of m's fit, as colour_matched's fit kept its blocks; one with less goes
    as its neighbours, and the change from one to the other is eased over a
    block. The shift is the change m makes to the kept blocks' means,
    averaged round each over _MATCH_SPREAD, so a block left out takes the
    change of the hair round it, not one set by its own colour."""
    import numpy as np

    share = _block_means(shared, size)
    known = share >= 0.5
    covered = shared[..., None].astype(np.float32)
    per = np.maximum(share, 1e-6)[..., None]
    out = _block_means(rgb * covered, size) / per
    fit = np.einsum("hwk,kc->hwc", np.concatenate(
        [out, np.ones(out.shape[:2] + (1,), np.float32)], axis=2), m)
    residual = np.abs(fit - _block_means(photo_rgb * covered, size) / per).sum(axis=2)
    kept = known & (residual <= cut)
    taken = kept.astype(np.float32)
    reach = _cell_blurred(known.astype(np.float32), 1.5)
    taken = np.where(known, taken, np.where(
        reach > 1e-3, _cell_blurred(taken, 1.5) / np.maximum(reach, 1e-3), 1.0))
    taken = _cell_blurred(taken, 1.0) / _cell_blurred(np.ones_like(taken), 1.0)
    change = np.clip(fit, 0, 255) - out
    weights = kept[..., None].astype(np.float32)
    spread = _MATCH_SPREAD * max(shared.shape) / size
    floor = _MATCH_SPREAD_FLOOR
    shift = ((_cell_blurred(change * weights, spread) + floor * np.median(change[kept], axis=0))
             / (_cell_blurred(weights, spread) + floor))
    return _cell_upsampled(taken, size, shared.shape), _cell_upsampled(shift, size, shared.shape)


def _cell_blurred(grid, sigma: float):
    """`grid` (cells, height x width [x channels]) blurred by a Gaussian of
    `sigma` cells that sums to 1, nothing past its edges: divided by the
    blur of the cells that count, a mean of them."""
    import numpy as np

    def kernel(n):
        i = np.arange(n, dtype=np.float32)
        return (np.exp(-0.5 * ((i[:, None] - i[None, :]) / sigma) ** 2)
                / (sigma * math.sqrt(2 * math.pi))).astype(np.float32)

    a = grid.astype(np.float32)
    rows = np.einsum("ij,jk...->ik...", kernel(a.shape[0]), a)
    return np.einsum("lk,ik...->il...", kernel(a.shape[1]), rows)


def _cell_upsampled(grid, size: int, shape):
    """`grid`'s cells (size x size blocks, as _block_means makes them) as
    pixels, `shape` (height, width): bilinear between the blocks' centres,
    held past the outer ones."""
    import numpy as np

    def along(n, cells):
        t = np.clip((np.arange(n) + 0.5) / size - 0.5, 0, cells - 1)
        low = np.floor(t).astype(int)
        return low, np.minimum(low + 1, cells - 1), (t - low).astype(np.float32)

    low, high, f = along(shape[0], grid.shape[0])
    f = f.reshape(-1, *[1] * (grid.ndim - 1))
    rows = grid[low] * (1 - f) + grid[high] * f
    low, high, f = along(shape[1], grid.shape[1])
    f = f.reshape(1, -1, *[1] * (grid.ndim - 2))
    return rows[:, low] * (1 - f) + rows[:, high] * f


def region(image):
    """`image`'s white as a mask: grey over half."""
    import numpy as np

    return np.asarray(image.convert("L")) > 127


def mask_image(region):
    """`region` as a black-and-white image, white = in it."""
    import numpy as np
    from PIL import Image

    return Image.fromarray(region.astype(np.uint8) * 255, "L")


def grown(region, radius: float):
    """`region` grown by a Gaussian of `radius` px (at least 1) thresholded
    low, which reaches about twice `radius` past a straight edge."""
    import numpy as np
    from PIL import ImageFilter

    blurred = mask_image(region).filter(ImageFilter.GaussianBlur(max(1.0, radius)))
    return np.asarray(blurred) >= 4


def feathered(region, radius: float):
    """`region` as a 0–1 float array blurred by a Gaussian of `radius` px
    (at least 1): 0.5 on its edge, fading over about twice `radius` each
    way."""
    import numpy as np
    from PIL import ImageFilter

    blurred = mask_image(region).filter(ImageFilter.GaussianBlur(max(1.0, radius)))
    return np.asarray(blurred, np.float32) / 255


def tidied(region, radius: float):
    """`region` blurred by a Gaussian of `radius` px (at least 1) and
    thresholded at half, which drops specks and fills pinholes."""
    import numpy as np
    from PIL import ImageFilter

    blurred = mask_image(region).filter(ImageFilter.GaussianBlur(max(1.0, radius)))
    return np.asarray(blurred) >= 128


def box(region, pad: int = 0):
    """`region`'s bounding box grown by `pad` px each way, within the image,
    as (rows, columns) slices; None where `region` is empty."""
    import numpy as np

    rows, cols = np.flatnonzero(region.any(axis=1)), np.flatnonzero(region.any(axis=0))
    if not len(rows):
        return None
    height, width = region.shape
    return (slice(max(0, rows[0] - pad), min(height, rows[-1] + 1 + pad)),
            slice(max(0, cols[0] - pad), min(width, cols[-1] + 1 + pad)))


def _fast_length(n: int) -> int:
    """The least 2^a 3^b 5^c of at least `n`: a length numpy's FFT is quick at."""
    best = 1 << max(0, n - 1).bit_length()
    five = 1
    while five < best:
        three = five
        while three < best:
            p = three
            while p < n:
                p *= 2
            best = min(best, p)
            three *= 3
        five *= 5
    return best


def disc(radius: int):
    """A disc `radius` px round, as a (2 radius + 1)-square bool array:
    OpenCV's MORPH_ELLIPSE structuring element of that size."""
    import numpy as np

    dy = np.arange(-radius, radius + 1)
    half = np.rint(np.sqrt(np.maximum(radius * radius - dy * dy, 0)))
    return np.abs(dy)[None, :] <= half[:, None]


def dilated(region, radius: int):
    """`region` dilated by disc(`radius`), exactly (cv2.dilate with it), as
    an FFT convolution over its bounding box, so a wide disc costs no more
    than a narrow one. Outside the image counts as out of `region`."""
    import numpy as np

    r = int(round(radius))
    if r <= 0:
        return region.copy()
    out = np.zeros(region.shape, bool)
    crop = box(region)
    if crop is None:
        return out
    a = region[crop].astype(np.float64)
    shape = (_fast_length(a.shape[0] + 2 * r), _fast_length(a.shape[1] + 2 * r))
    full = np.fft.irfft2(np.fft.rfft2(a, shape) * np.fft.rfft2(disc(r).astype(np.float64), shape),
                         shape)
    # full[i, j] is the image's pixel (top - r + i, left - r + j).
    top, left = crop[0].start - r, crop[1].start - r
    rows, cols = box(region, r)
    out[rows, cols] = full[rows.start - top:rows.stop - top, cols.start - left:cols.stop - left] > 0.5
    return out


def eroded(region, radius: int):
    """`region` eroded by disc(`radius`): what dilated takes back. Outside
    the image counts as in `region`, so its edge doesn't eat into it."""
    import numpy as np

    r = int(round(radius))
    if r <= 0:
        return region.copy()
    out = np.zeros(region.shape, bool)
    crop = box(region, r)
    if crop is None:
        return out
    out[crop] = region[crop] & ~dilated(~region[crop], r)
    return out


def closed(region, radius: int):
    """`region` closed by disc(`radius`), dilated and then eroded: the gaps
    and notches narrower than the disc filled, its outline otherwise as it
    is."""
    return eroded(dilated(region, radius), radius)


def components(region, connectivity: int = 8):
    """`region`'s connected parts, as OpenCV's connectedComponents finds
    them: a label per pixel (height x width, 0 outside `region`, 1 to n in
    it) and each label's size in pixels (n + 1 of them, the first 0).
    `connectivity` 8 joins pixels that touch at a corner, 4 only at a side.
    Worked out on the runs of each row, joined by union-find, so Python
    loops over runs, not pixels."""
    import numpy as np

    height, width = region.shape
    edges = np.diff(np.pad(region.astype(np.int8), ((0, 0), (1, 1))), axis=1)
    rows, starts = np.nonzero(edges == 1)
    ends = np.nonzero(edges == -1)[1]  # one past each run's last pixel, in the same order
    first = np.searchsorted(rows, np.arange(height + 1)).tolist()  # each row's first run
    s, e = starts.tolist(), ends.tolist()
    parent = list(range(len(s)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reach = 1 if connectivity == 8 else 0
    for y in range(1, height):
        k = first[y - 1]
        for j in range(first[y], first[y + 1]):
            # The row above's runs that end before run j can reach it reach
            # no later run either.
            while k < first[y] and e[k] + reach <= s[j]:
                k += 1
            i = k
            while i < first[y] and s[i] < e[j] + reach:
                a, b = root(i), root(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)
                i += 1
    _, run_label = np.unique(np.array([root(i) for i in range(len(s))], np.int64),
                             return_inverse=True)
    run_label = (run_label.reshape(-1) + 1).astype(np.int32)
    sizes = np.bincount(run_label, weights=ends - starts, minlength=1).astype(np.int64)
    labels = np.zeros((height, width + 1), np.int32)
    np.add.at(labels, (rows, starts), run_label)
    np.add.at(labels, (rows, ends), -run_label)
    return np.cumsum(labels, axis=1, dtype=np.int32)[:, :width], sizes


def tinted(image, region):
    """`image` with `region` tinted red, for an overlay: an RGB float array,
    for the effect to draw on before it is an image (as_image). float32,
    made in place (aiHair: `region` times a Python float made it float64,
    with its temporaries 0.9 GB on a 9.8 MP photo)."""
    import numpy as np

    rgb = np.array(image.convert("RGB"), np.float32)
    a = region.astype(np.float32)[..., None]
    a *= np.float32(0.45)
    rgb *= 1 - a
    rgb += np.array([255, 40, 40], np.float32) * a
    return rgb


def as_image(rgb):
    """An RGB float array as an image, rounded and clipped to 0–255."""
    import numpy as np
    from PIL import Image

    return Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8))


def edit_file(image, name: str, **save) -> tuple[str, bytes, str]:
    """`image` as an images.edit file tuple, in the format `name`'s
    extension says (PNG unless .jpg), saved with `save`'s options."""
    fmt, mime = (("JPEG", "image/jpeg") if name.lower().endswith((".jpg", ".jpeg"))
                 else ("PNG", "image/png"))
    buf = io.BytesIO()
    image.save(buf, format=fmt, **save)
    return name, buf.getvalue(), mime


def label_font(size: int):
    """A TrueType font for the sheets' labels (Windows' Arial, else DejaVu),
    falling back to Pillow's built-in one."""
    from PIL import ImageFont

    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def sheet(rows: list[tuple[str, list[tuple[str, object]]]]):
    """A review sheet: a row per (name, [(role, image), ...]), every tile
    _SHEET_TILE_HEIGHT tall under a band reading "<name>: <role>", on a dark
    ground with white-on-dark labels, so it reads as a sheet, not a photo.
    looping-prompt-hairline.py's _review_sheet, of images in hand."""
    from PIL import Image, ImageDraw, ImageOps

    tile_h = _SHEET_TILE_HEIGHT
    band, gap = tile_h // 12, tile_h // 32
    tiled = []  # per row: [(label, tile image), ...]
    for name, tiles in rows:
        row = []
        for role, im in tiles:
            im = ImageOps.exif_transpose(im).convert("RGB")
            w = round(im.width * tile_h / im.height)
            row.append((f"{name}: {role}", im.resize((w, tile_h), Image.Resampling.LANCZOS)))
        tiled.append(row)
    width = max(sum(t.width for _, t in row) + gap * (len(row) + 1) for row in tiled)
    height = len(tiled) * (band + tile_h) + gap * (len(tiled) + 1)
    out = Image.new("RGB", (width, height), (40, 40, 40))
    draw = ImageDraw.Draw(out)
    y = gap
    for row in tiled:
        x = gap
        for text, tile in row:
            size = band * 2 // 3  # shrunk until it fits a narrow tile
            while size > 10 and draw.textlength(text, font=label_font(size)) > tile.width - 8:
                size -= 2
            draw.text((x + tile.width / 2, y + band / 2), text,
                      fill="white", font=label_font(size), anchor="mm")
            out.paste(tile, (x, y + band))
            x += tile.width + gap
        y += band + tile_h + gap
    return out
