"""fluffy_composite.py's --align-regions: renders lined up with their photo
region by region, where one transform of the whole render (align.line_up)
can't follow them: on female_batchExpand04_004_F_2 a render moves the whole
top of the hair, parting and curls with it, up 9–14 px while the face moves
under 1 px, so the curls pasted from it don't meet the photo's below.

  face  the face's hull less the hair: a similarity transform fitted to the
        photo's and the render's landmarks;
  hair  dense optical flow (OpenCV's DIS), photo -> render, weighted by the
        photo's strand edges and smoothed over FLOW of the long side, faded
        out by the caller's `keep` (where the effect's own change is, so
        that stays);
  rest  outside both hair masks (grown by FEATHER) and the face: an affine
        transform by ECC, or none where it doesn't converge;

joined by their masks feathered over FEATHER, the rest filling in, and the
render resampled once.

regions is align's regions_flow's first step: the render lined up with the
photo region by region, the rest (outside both hair masks and the face) and
the face (with the bare forehead) by affine transforms (ECC), the hair by
optical flow (DIS), faded in from the forehead's edge and capped, as an
offset field for align to resample through.

face_flow is align's local_flow: after the face's parts are lined up, the
face moved onto the photo by optical flow (DIS), averaged over the photo's
edges so the brows' and eyelids' carry it onto the skin beside them.

shifted is the --blend of strayHairWithFace, rootRetouch_1 and
grayHairTwoStep (features/shifted.py): the same three regions, the face
the effect's own mask or FAN's hull, each moved by one translation (ECC's
MOTION_TRANSLATION), the hair's and the rest's fitted outside where the
effect changes the render; a region no shift brings closer to the photo,
or that moves past MAX_SHIFT, stays where it is.

OpenCV isn't in this env: the work (_JOB) runs in the face model's python
(--face-python, the face-align env's), as landmarks.py's does."""

import json
import subprocess
import tempfile
import threading
from pathlib import Path

# As shares of the photo's long side: the hair's flow smoothed over FLOW
# (a Gaussian's sigma), the regions' masks feathered over FEATHER.
FLOW = 0.015
FEATHER = 0.01
# shifted: a region's shift at most MAX_SHIFT of the long side; ECC fitted
# only to a region of at least MIN_PIXELS.
MAX_SHIFT = 0.03
MIN_PIXELS = 2000
# face_flow: the flow found on grey blurred by FACE_FLOW_BLUR, averaged over
# a Gaussian of FACE_FLOW_SIGMA, capped at FACE_FLOW_CAP, as shares of the
# long side (on subject20's 1108 px: 1.5, 5.5 and 10 px).
FACE_FLOW_BLUR = 0.0014
FACE_FLOW_SIGMA = 0.005
FACE_FLOW_CAP = 0.009
# regions: the hair's flow averaged over a Gaussian of REGIONS_HAIR_FLOW of
# the long side, weighted by the photo's edges where both show hair, eased
# into the rest's affine where few edges are near; the regions' masks
# feathered over FEATHER. On subject20's hairline_simple_lv1.5 renders,
# then lined up as local_flow, the hair's pixels off the photo's by >10
# fell from 6.3% to 4.3%, the head blend's seam band's from 10.8% to 9.5%,
# and the face's worst part from 0.7 to 0.5 px off (median of 10;
# output_subject20_only_region3, 2026-11-04).
REGIONS_HAIR_FLOW = 0.01
# regions: the hair's flow faded in from the forehead's edge over
# REGIONS_HAIR_FADE (a smoothstep), the face's affine under it at the edge
# eased into the rest's as it is, and kept within REGIONS_HAIR_CAP of the
# long side of that. Just above the hairline the render redraws the hair,
# and DIS matched the photo's wisps to strands that aren't theirs, 15-40 px
# off (subject25's result_0: the flow's median in the hair 0.3 px, its p99
# 60), dropping to the face's affine across FEATHER: the field folded (its
# gradient's p99 in the hair 1.19) and the hair swirled
# (output_region_flow's subject10, 25 and 27). Eased across a feather six
# times as wide, that p99 came to 0.67 and the swirls stayed; faded in so,
# to 0.26 (subject27's result_3: 0.97 -> 0.19, subject10's result_1: 0.31
# -> 0.17) and they were gone, the hair further than REGIONS_HAIR_FADE from
# the face within 0.1 of its mean |diff| from the photo before (2026-11-05).
# Capped off the face's affine alone, the hair far from the face was pulled
# to it where the rest moved apart from the face (subject16's result_3: 6.6
# px, p90 12.6), from 3.78 to 4.77 off the photo; DIS's flow and its
# reverse checked against each other (taken where they agree) left the
# hair bent where the render redrew it.
REGIONS_HAIR_FADE = 0.06
REGIONS_HAIR_CAP = 0.01
# One run at a time: each holds every render's flow fields.
_LOCK = threading.Lock()

# The jobs' start, in the face python: their arguments and helpers.
_COMMON = r'''
import json, sys
from pathlib import Path
import cv2
import numpy as np

tmp, k = Path(sys.argv[1]), json.loads(sys.argv[2])


def box(mask, pad):
    ys, xs = np.flatnonzero(mask.any(1)), np.flatnonzero(mask.any(0))
    if not len(ys):
        return None
    h, w = mask.shape
    return (slice(max(0, ys[0] - pad), min(h, ys[-1] + pad + 1)),
            slice(max(0, xs[0] - pad), min(w, xs[-1] + pad + 1)))


def blur_within(x, support, sigma):
    # cv2.GaussianBlur of an x that is 0 outside `support`, over its box only.
    out = np.zeros_like(x)
    b = box(support, (round(sigma * 8 + 1) | 1) // 2 + 1)
    if b is not None:
        out[b] = cv2.GaussianBlur(x[b], (0, 0), sigma)
    return out


def changed(a, b):
    d = cv2.absdiff(*(cv2.GaussianBlur(x, (0, 0), 1) for x in (a, b)))
    return d.max(axis=2).astype(np.float32)


'''

# Run in the face python: fluffy_composite.py's _align_regions, for each
# render in the folder given, the photo's arrays shared.
_JOB = _COMMON + r'''shared = np.load(tmp / "photo.npz")
photo = np.ascontiguousarray(shared["photo"][..., ::-1])  # BGR, as OpenCV has it
photo_hair, points, keep = shared["hair"], shared["points"], shared["keep"]
face = shared["face"] & ~photo_hair
h, w = photo_hair.shape
long_side = max(h, w)
ys, xs = np.mgrid[:h, :w].astype(np.float32)
grey0 = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
edges = np.hypot(cv2.Sobel(grey0, cv2.CV_32F, 1, 0), cv2.Sobel(grey0, cv2.CV_32F, 0, 1))
conf = edges * photo_hair
s = max(1.0, k["flow"] * long_side)
f = max(1.0, k["feather"] * long_side)
grow = max(1, round(k["feather"] * long_side))
disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2)
wf = blur_within(face.astype(np.float32), face, f)
wh = blur_within(photo_hair.astype(np.float32), photo_hair, f) * (1 - wf) * (1 - keep)
wr = np.clip(1 - wf - wh, 0, 1)


def field(m):  # photo -> render affine as an offset field
    return np.dstack([m[0, 0] * xs + m[0, 1] * ys + m[0, 2] - xs,
                      m[1, 0] * xs + m[1, 1] * ys + m[1, 2] - ys]).astype(np.float32)


reports = []
for i in range(k["n"]):
    job = np.load(tmp / f"render_{i}.npz")
    render = np.ascontiguousarray(job["render"][..., ::-1])
    render_hair, render_points = job["hair"], job["points"]
    grey1 = cv2.cvtColor(render, cv2.COLOR_BGR2GRAY)
    rest = ~cv2.dilate((photo_hair | render_hair).astype(np.uint8), disc).astype(bool) & ~face
    m_rest = np.eye(2, 3, dtype=np.float32)
    try:
        _, m_rest = cv2.findTransformECC(grey0.astype(np.float32), grey1.astype(np.float32), m_rest,
                                         cv2.MOTION_AFFINE,
                                         (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5),
                                         rest.astype(np.uint8), 5)
        rest_note = "ecc"
    except cv2.error:
        m_rest = np.eye(2, 3, dtype=np.float32)
        rest_note = "ecc didn't converge: none"
    m_face, _ = cv2.estimateAffinePartial2D(points.astype(np.float32), render_points.astype(np.float32))
    if m_face is None:
        m_face = m_rest
    flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(grey0, grey1, None)
    hair_d = (blur_within(flow * conf[..., None], photo_hair, s)
              / (blur_within(conf, photo_hair, s)[..., None] + 1e-3))
    d = wf[..., None] * field(m_face) + wh[..., None] * hair_d + wr[..., None] * field(m_rest)
    aligned = cv2.remap(render, xs + d[..., 0], ys + d[..., 1], cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
    before, after = changed(photo, render), changed(photo, aligned)
    report = {"rest": rest_note}
    for name, mask in (("face", face), ("hair", photo_hair & (keep < 0.5)), ("rest", rest)):
        if mask.any():
            report[f"{name} mean |diff|"] = [round(float(before[mask].mean()), 2),
                                            round(float(after[mask].mean()), 2)]
    np.save(tmp / f"aligned_{i}.npy", aligned[..., ::-1])
    reports.append(report)
    print(f"  regions render {i}: {report}", flush=True)
(tmp / "reports.json").write_text(json.dumps(reports), encoding="utf-8")
'''


def lined_up(photo, renders: list, photo_hair, render_hairs: list, points, render_points: list,
             face, keep, face_python: str) -> list:
    """Each of `renders` (PIL, at the photo's size) lined up with `photo`
    region by region: the face (`face`, a mask of its hull) by a similarity
    transform from the photo's landmarks `points` to the render's
    (`render_points`, one array per render, the same landmarks in the same
    order), the hair (`photo_hair`) by optical flow, faded out by `keep`
    (0–1), and the rest, outside `photo_hair` and the render's own hair
    (`render_hairs`), by ECC. Returns (render lined up, report) per render,
    the report each region's mean difference from the photo before and
    after. Raises RuntimeError if the face python fails."""
    import numpy as np
    from PIL import Image

    if not Path(face_python).is_file():
        raise RuntimeError(f"the face model's python, which has OpenCV, isn't there: "
                           f"{face_python}")
    with tempfile.TemporaryDirectory(prefix="region_align_") as tmp:
        tmp = Path(tmp)
        np.savez(tmp / "photo.npz", photo=np.asarray(photo.convert("RGB")), hair=photo_hair,
                 points=np.asarray(points, np.float32), face=face,
                 keep=np.asarray(keep, np.float32))
        for i, (render, hair, found) in enumerate(zip(renders, render_hairs, render_points)):
            np.savez(tmp / f"render_{i}.npz", render=np.asarray(render.convert("RGB")),
                     hair=hair, points=np.asarray(found, np.float32))
        settings = json.dumps({"n": len(renders), "flow": FLOW, "feather": FEATHER})
        with _LOCK:
            done = subprocess.run([face_python, "-c", _JOB, str(tmp), settings],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding="utf-8", errors="replace")
        if done.returncode:
            raise RuntimeError(f"lining up by region failed (exit {done.returncode}):\n"
                               f"{(done.stderr or '')[-2000:]}")
        reports = json.loads((tmp / "reports.json").read_text(encoding="utf-8"))
        return [(Image.fromarray(np.load(tmp / f"aligned_{i}.npy")), reports[i])
                for i in range(len(renders))]


# Run in the face python: shifted's work, for each render in the folder
# given, the photo's arrays shared.
_SHIFT_JOB = _COMMON + r'''def translated(image, d):  # out(x) = image(x + d)
    m = np.float32([[1, 0, d[0]], [0, 1, d[1]]])
    return cv2.warpAffine(image, m, (image.shape[1], image.shape[0]),
                          flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def ecc(grey0, grey1, mask):
    if mask.sum() < k["min_pixels"]:
        return None, f"{int(mask.sum())} px to fit to, under {k['min_pixels']}"
    m = np.eye(2, 3, dtype=np.float32)
    try:
        _, m = cv2.findTransformECC(grey0, grey1, m, cv2.MOTION_TRANSLATION,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5),
                                    mask.astype(np.uint8), 5)
    except cv2.error:
        return None, "ECC didn't converge"
    return (float(m[0, 2]), float(m[1, 2])), "ECC"


shared = np.load(tmp / "photo.npz")
photo = np.ascontiguousarray(shared["photo"][..., ::-1])  # BGR, as OpenCV has it
photo_hair, face, keep_out = shared["hair"], shared["face"], shared["keep_out"]
h, w = photo_hair.shape
long_side = max(h, w)
ys, xs = np.mgrid[:h, :w].astype(np.float32)
grey0 = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY).astype(np.float32)
f = max(1.0, k["feather"] * long_side)
grow = max(1, round(k["feather"] * long_side))
disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2)
wf = blur_within(face.astype(np.float32), face, f)
wh = blur_within(photo_hair.astype(np.float32), photo_hair, f) * (1 - wf)
wr = np.clip(1 - wf - wh, 0, 1)
np.save(tmp / "weights.npy", np.dstack([wf, wh, wr]))
near_face = cv2.dilate(face.astype(np.uint8), disc).astype(bool)

reports = []
for i in range(k["n"]):
    job = np.load(tmp / f"render_{i}.npz")
    render = np.ascontiguousarray(job["render"][..., ::-1])
    render_hair = job["hair"]
    grey1 = cv2.cvtColor(render, cv2.COLOR_BGR2GRAY).astype(np.float32)
    either = cv2.dilate((photo_hair | render_hair).astype(np.uint8), disc).astype(bool)
    regions = {"face": face & ~photo_hair & ~render_hair,
               "hair": photo_hair & render_hair & ~keep_out,
               "rest": ~either & ~near_face & ~keep_out}
    before = changed(photo, render)
    report, shifts = {}, {}
    for name, mask in regions.items():
        d, how = ecc(grey0, grey1, mask)
        entry = {"how": how}
        if d is None:
            d = (0.0, 0.0)
        else:
            entry["shift"] = [round(d[0], 2), round(d[1], 2)]
            if np.hypot(*d) > k["max_shift"] * long_side:
                d, entry["kept"] = (0.0, 0.0), f"none: past {k['max_shift']:.0%} of the long side"
            else:
                b = float(before[mask].mean())
                a = float(changed(photo, translated(render, d))[mask].mean())
                entry["mean |diff|"] = [round(b, 2), round(a, 2)]
                if a >= b:
                    d, entry["kept"] = (0.0, 0.0), "none: no closer to the photo"
                else:
                    entry["kept"] = "shifted"
        shifts[name] = d
        report[name] = entry
    moved = max(np.hypot(*d) for d in shifts.values()) >= 0.5
    if moved:
        dd = (wf[..., None] * np.float32(shifts["face"]) + wh[..., None] * np.float32(shifts["hair"])
              + wr[..., None] * np.float32(shifts["rest"]))
        aligned = cv2.remap(render, xs + dd[..., 0], ys + dd[..., 1], cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REPLICATE)
        np.save(tmp / f"aligned_{i}.npy", aligned[..., ::-1])
    report["moved"] = bool(moved)
    reports.append(report)
    print(f"  shifts render {i}: {report}", flush=True)
(tmp / "reports.json").write_text(json.dumps(reports), encoding="utf-8")
'''


def shifted(photo, renders: list, photo_hair, render_hairs: list, face, keep_out,
            face_python: str):
    """Each of `renders` (PIL, at the photo's size) lined up with `photo`
    region by region, each by one translation (ECC's MOTION_TRANSLATION):
    the face (`face`, a mask) fitted on its skin, the hair (`photo_hair` and
    the render's own, `render_hairs`) and the rest, outside both hair masks
    grown by FEATHER and the face, those two fitted outside `keep_out`
    (where the effect changes the render). A region no shift brings closer
    to the photo, or that would move past MAX_SHIFT of the long side, stays
    where it is; the three are joined as lined_up's are. Returns, per
    render, (the render lined up, or None where no region moved half a
    pixel; a report of each region's shift and its mean difference from the
    photo before and after), and the regions' weights (height x width x 3:
    face, hair, rest). Raises RuntimeError if the face python fails."""
    import numpy as np
    from PIL import Image

    if not Path(face_python).is_file():
        raise RuntimeError(f"the face model's python, which has OpenCV, isn't there: "
                           f"{face_python}")
    with tempfile.TemporaryDirectory(prefix="region_shift_") as tmp:
        tmp = Path(tmp)
        np.savez(tmp / "photo.npz", photo=np.asarray(photo.convert("RGB")), hair=photo_hair,
                 face=face, keep_out=keep_out)
        for i, (render, hair) in enumerate(zip(renders, render_hairs)):
            np.savez(tmp / f"render_{i}.npz", render=np.asarray(render.convert("RGB")), hair=hair)
        settings = json.dumps({"n": len(renders), "feather": FEATHER, "max_shift": MAX_SHIFT,
                               "min_pixels": MIN_PIXELS})
        with _LOCK:
            done = subprocess.run([face_python, "-c", _SHIFT_JOB, str(tmp), settings],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding="utf-8", errors="replace")
        if done.returncode:
            raise RuntimeError(f"lining up by region failed (exit {done.returncode}):\n"
                               f"{(done.stderr or '')[-2000:]}")
        reports = json.loads((tmp / "reports.json").read_text(encoding="utf-8"))
        out = []
        for i, report in enumerate(reports):
            path = tmp / f"aligned_{i}.npy"
            out.append((Image.fromarray(np.load(path)) if path.is_file() else None, report))
        return out, np.load(tmp / "weights.npy")


# Run in the face python: regions' work, for the render in the folder.
_REGIONS_JOB = _COMMON + r'''shared = np.load(tmp / "photo.npz")
photo = np.ascontiguousarray(shared["photo"][..., ::-1])  # BGR, as OpenCV has it
render = np.ascontiguousarray(shared["render"][..., ::-1])
photo_hair, render_hair, face = shared["hair"], shared["render_hair"], shared["face"]
forehead = shared["forehead"]
h, w = photo_hair.shape
long_side = max(h, w)
ys, xs = np.mgrid[:h, :w].astype(np.float32)
grey0 = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
grey1 = cv2.cvtColor(render, cv2.COLOR_BGR2GRAY)
f = max(1.0, k["feather"] * long_side)
grow = max(1, round(k["feather"] * long_side))
disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2)
wf = blur_within(face.astype(np.float32), face, f)
wh = blur_within(photo_hair.astype(np.float32), photo_hair, f) * (1 - wf)
wr = np.clip(1 - wf - wh, 0, 1)
criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)


def field(m):  # photo -> render affine as an offset field
    return np.dstack([m[0, 0] * xs + m[0, 1] * ys + m[0, 2] - xs,
                      m[1, 0] * xs + m[1, 1] * ys + m[1, 2] - ys]).astype(np.float32)


def ecc(mask, start):
    if mask.sum() < k["min_pixels"]:
        return start, f"{int(mask.sum())} px to fit to, under {k['min_pixels']}: none"
    try:
        _, m = cv2.findTransformECC(grey0.astype(np.float32), grey1.astype(np.float32),
                                    start.copy(), cv2.MOTION_AFFINE, criteria,
                                    mask.astype(np.uint8), 5)
        return m, "ECC"
    except cv2.error:
        return start, "ECC didn't converge: none"


either = cv2.dilate((photo_hair | render_hair).astype(np.uint8), disc).astype(bool)
rest = ~either & ~cv2.dilate(face.astype(np.uint8), disc).astype(bool)
m_rest, rest_how = ecc(rest, np.eye(2, 3, dtype=np.float32))
skin = face & ~photo_hair & ~render_hair
m_face, face_how = ecc(skin, m_rest)
dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
dis.setFinestScale(0)  # down to the full resolution
flow = dis.calc(grey0, grey1, None)
edges = np.hypot(cv2.Sobel(grey0.astype(np.float32), cv2.CV_32F, 1, 0),
                 cv2.Sobel(grey0.astype(np.float32), cv2.CV_32F, 0, 1))
conf = edges * (photo_hair & render_hair)
s = max(1.0, k["hair_flow"] * long_side)
num = blur_within(flow * conf[..., None], photo_hair, s)
den = blur_within(conf, photo_hair, s)[..., None]
tau = 0.5 * float(np.median(den[photo_hair])) if photo_hair.any() else 1.0
d_rest, d_face = field(m_rest), field(m_face)
# The hair at the hairline, which the render redraws, moved as the face is:
# under the hair's flow the face's affine at the forehead's edge, eased into
# the rest's over hair_fade as the flow, kept within hair_cap of it, is
# faded in.
if forehead.any():
    fade = max(1.0, k["hair_fade"] * long_side)
    t = np.clip(cv2.distanceTransform((~forehead).astype(np.uint8), cv2.DIST_L2, 5) / fade, 0, 1)
    g = (t * t * (3 - 2 * t))[..., None]
    hair_how = f"faded in over {fade:.0f} px from the forehead"
else:
    g = np.ones((h, w, 1), np.float32)
    hair_how = "with no bare forehead to fade it in from"
under = d_face + g * (d_rest - d_face)
beyond = (num + tau * d_rest) / (den + tau) - under
size = np.hypot(beyond[..., 0], beyond[..., 1])[..., None]
cap = max(1.0, k["hair_cap"] * long_side)
beyond = beyond * np.minimum(1, cap / np.maximum(size, 1e-6))
capped = float((size[..., 0] > cap)[photo_hair].mean()) if photo_hair.any() else 0.0
hair_how = f"flow {hair_how}, capped at {cap:.0f} px (on {capped:.1%} of the hair)"
d = wf[..., None] * d_face + wh[..., None] * (under + g * beyond) + wr[..., None] * d_rest
aligned = cv2.remap(render, xs + d[..., 0], ys + d[..., 1], cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
before, after = changed(photo, render), changed(photo, aligned)
report = {"rest": rest_how, "face": face_how, "hair": hair_how}
for name, mask in (("rest", rest), ("face", skin), ("hair", photo_hair & render_hair)):
    if mask.any():
        report[f"{name} mean |diff|"] = [round(float(before[mask].mean()), 2),
                                        round(float(after[mask].mean()), 2)]
np.save(tmp / "field.npy", d.astype(np.float32))
(tmp / "report.json").write_text(json.dumps(report), encoding="utf-8")
'''


def regions(photo, render, photo_hair, render_hair, face, forehead, face_python: str):
    """`render` (PIL, at the photo's size) lined up with `photo` region by
    region, as an offset field: where in the render each pixel's content of
    the photo sits (height x width x 2, dx and dy). The rest, outside both
    hair masks (`photo_hair`, `render_hair`) grown by FEATHER and outside
    `face` (a mask: the face with the bare forehead), by an affine transform
    (ECC); the face by another, fitted on its skin in both, from the rest's;
    the hair (the photo's) by optical flow (DIS), averaged over
    REGIONS_HAIR_FLOW weighted by the photo's edges where both show hair and
    eased into the rest's affine where few are near, faded in from the
    edge of `forehead` (a mask: the face's part above the brows) over
    REGIONS_HAIR_FADE, the face's affine under it there eased into the
    rest's, and kept within REGIONS_HAIR_CAP of that; joined by their masks
    feathered over FEATHER, the
    face first, then the hair. Also a report: how each affine was found, how
    the hair's flow was capped and faded in, and each region's mean
    difference from the photo before and after. Raises RuntimeError if the
    face python fails."""
    import numpy as np

    if not Path(face_python).is_file():
        raise RuntimeError(f"the face model's python, which has OpenCV, isn't there: "
                           f"{face_python}")
    with tempfile.TemporaryDirectory(prefix="regions_") as tmp:
        tmp = Path(tmp)
        np.savez(tmp / "photo.npz", photo=np.asarray(photo.convert("RGB")),
                 render=np.asarray(render.convert("RGB")), hair=np.asarray(photo_hair, bool),
                 render_hair=np.asarray(render_hair, bool), face=np.asarray(face, bool),
                 forehead=np.asarray(forehead, bool))
        settings = json.dumps({"feather": FEATHER, "hair_flow": REGIONS_HAIR_FLOW,
                               "hair_fade": REGIONS_HAIR_FADE, "hair_cap": REGIONS_HAIR_CAP,
                               "min_pixels": MIN_PIXELS})
        with _LOCK:
            done = subprocess.run([face_python, "-c", _REGIONS_JOB, str(tmp), settings],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding="utf-8", errors="replace")
        if done.returncode:
            raise RuntimeError(f"lining up by region failed (exit {done.returncode}):\n"
                               f"{(done.stderr or '')[-2000:]}")
        return (np.load(tmp / "field.npy"),
                json.loads((tmp / "report.json").read_text(encoding="utf-8")))


# Run in the face python: face_flow's work.
_FLOW_JOB = _COMMON + r'''shared = np.load(tmp / "photo.npz")
photo = np.ascontiguousarray(shared["photo"][..., ::-1])  # BGR, as OpenCV has it
image = np.ascontiguousarray(shared["image"][..., ::-1])
hair, weight = shared["hair"], shared["weight"]
long_side = max(weight.shape)


def grey(bgr):
    return cv2.GaussianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (0, 0),
                            max(0.5, k["blur"] * long_side))


grey0 = grey(photo)
edges = np.hypot(cv2.Sobel(grey0.astype(np.float32), cv2.CV_32F, 1, 0),
                 cv2.Sobel(grey0.astype(np.float32), cv2.CV_32F, 0, 1))
dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
dis.setFinestScale(0)  # down to the full resolution
flow = dis.calc(grey0, grey(image), None)
sigma = max(1.0, k["sigma"] * long_side)
conf = edges * ~hair * (weight > 0.004)
num = cv2.GaussianBlur(flow * conf[..., None], (0, 0), sigma)
den = cv2.GaussianBlur(conf, (0, 0), sigma)[..., None]
d = num / (den + 1e-3)
# Where few edges are near, the flow fades out instead of being carried far.
inside = weight > 0.5
tau = 0.5 * float(np.median(den[inside])) if inside.any() else 0.0
d = d * (den / (den + tau + 1e-6))
size = np.hypot(d[..., 0], d[..., 1])[..., None]
cap = max(1.0, k["cap"] * long_side)
d = d * np.minimum(1, cap / np.maximum(size, 1e-6)) * weight[..., None]
np.save(tmp / "flow.npy", d.astype(np.float32))
'''


def face_flow(photo, image, hair, weight, face_python: str):
    """How far each pixel's content of `photo` sits in `image` (PIL, at the
    photo's size and lined up with it already), height x width x 2 (dx,
    dy), where `weight` (0–1) takes it: DIS optical flow, photo -> image,
    on their grey blurred by FACE_FLOW_BLUR, averaged over a Gaussian of
    FACE_FLOW_SIGMA weighted by the photo's edges outside `hair` (a mask:
    the photo's and the image's), faded out where few edges are near,
    capped at FACE_FLOW_CAP, times `weight`. Raises RuntimeError if the face
    python fails."""
    import numpy as np

    if not Path(face_python).is_file():
        raise RuntimeError(f"the face model's python, which has OpenCV, isn't there: "
                           f"{face_python}")
    with tempfile.TemporaryDirectory(prefix="face_flow_") as tmp:
        tmp = Path(tmp)
        np.savez(tmp / "photo.npz", photo=np.asarray(photo.convert("RGB")),
                 image=np.asarray(image.convert("RGB")), hair=np.asarray(hair, bool),
                 weight=np.asarray(weight, np.float32))
        settings = json.dumps({"blur": FACE_FLOW_BLUR, "sigma": FACE_FLOW_SIGMA,
                               "cap": FACE_FLOW_CAP})
        with _LOCK:
            done = subprocess.run([face_python, "-c", _FLOW_JOB, str(tmp), settings],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding="utf-8", errors="replace")
        if done.returncode:
            raise RuntimeError(f"the face's flow failed (exit {done.returncode}):\n"
                               f"{(done.stderr or '')[-2000:]}")
        return np.load(tmp / "flow.npy")
