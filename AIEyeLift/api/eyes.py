# Shared eye geometry for the API route: landmarks in original-image pixels, per-eye opening, both-eyes crop box, eye masks.
# Runs in the python3.8 env (onnxruntime); expects sRGB input images.
import os, sys
import cv2, numpy as np

INPAINTING = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'inpainting')
sys.path.insert(0, INPAINTING)
from DroopyEyelidUtils import align_utils, util_wrinkle, process_utils as pu  # noqa: E402
from DroopyEyelidUtils.onnx_model import OnnxKerasModel  # noqa: E402

SIZE = (1280, 960)  # aligned face: 960 wide x 1280 high
CROP_W, CROP_H = 2048, 1024  # gpt-image-2 input / output size (2:1, both eyes + brows)
T = pu.landmark92_tag2index
_models = {}


def models():
    if not _models:
        _models['rect'] = OnnxKerasModel(os.path.join(INPAINTING, 'models', 'facert_model.onnx'))
        _models['align'] = OnnxKerasModel(os.path.join(INPAINTING, 'models', 'align_weights_3548.onnx'))
    return _models['rect'], _models['align']


class Face:
    """92 landmarks in the aligned face (p) and in original pixels (q), plus the alignment transform."""

    def __init__(self, img):
        rect, align = models()
        _, pts = align_utils.infer_face_rect_51_align_points(rect, align, img, False)
        self.ti = util_wrinkle.get_transform_info(pts, True)
        _, self.p = align_utils.infer_face_rect_92_align_points(rect, align, util_wrinkle.transform_img(self.ti, img, SIZE), True)
        fwd = util_wrinkle.MakeCVForwardTransformMatrix(self.ti, 1.0, SIZE[1], SIZE[0])
        inv = cv2.invertAffineTransform(fwd[:2].astype(np.float64))
        self.q = cv2.transform(self.p[:, None, :].astype(np.float64), inv)[:, 0, :]
        self.shape = img.shape

    def pt(self, tag, orig=True):
        return (self.q if orig else self.p)[T[tag]]

    def opening(self, S):
        """Upper lid above the pupil centre, in iris radii (normal ~0.8-0.9)."""
        top, c = self.pt(f'LBF_3D_{S}_EYE_TOP', False), self.pt(f'LBF_3D_{S}_EYE_CENTER', False)
        il, ir = self.pt(f'LBF_3D_{S}_EYE_IRIS_LEFT', False), self.pt(f'LBF_3D_{S}_EYE_IRIS_RIGHT', False)
        return float((c[1] - top[1]) / (0.5 * (np.linalg.norm(il - c) + np.linalg.norm(ir - c))))

    def eye_width(self, S):
        return float(np.linalg.norm(self.pt(f'LBF_3D_{S}_EYE_OUTSIDE') - self.pt(f'LBF_3D_{S}_EYE_INSIDE_OUT')))

    def image_side(self, S):
        """'left' / 'right' as seen in the image (landmark LEFT/RIGHT is the subject's side)."""
        other = 'RIGHT' if S == 'LEFT' else 'LEFT'
        return 'left' if self.pt(f'LBF_3D_{S}_EYE_CENTER')[0] < self.pt(f'LBF_3D_{other}_EYE_CENTER')[0] else 'right'

    def crop_box(self):
        """Axis-aligned 2:1 box around both eyes and brows, in original pixels (may extend past the image)."""
        a, b = self.pt('LBF_3D_LEFT_EYE_OUTSIDE'), self.pt('LBF_3D_RIGHT_EYE_OUTSIDE')
        span = float(np.linalg.norm(a - b))
        c = (self.pt('LBF_3D_LEFT_EYE_CENTER') + self.pt('LBF_3D_RIGHT_EYE_CENTER')) / 2
        w = 1.6 * span; h = w * CROP_H / CROP_W
        x0, y0 = c[0] - w / 2, c[1] - 0.58 * h  # a little more room above the eyes for lids and brows
        return [int(round(x0)), int(round(y0)), int(round(w)), int(round(h))]

    def square_box(self):
        """Square box for the Qwen LoRA (ai-toolkit mis-resizes non-square controls): 1.25x the outer-corner span,
        so at 1024 px the eyes get the same pixel density as the 2:1 crop at 1312 px. Eyes sit a bit above centre."""
        a, b = self.pt('LBF_3D_LEFT_EYE_OUTSIDE'), self.pt('LBF_3D_RIGHT_EYE_OUTSIDE')
        s = 1.25 * float(np.linalg.norm(a - b))
        c = (self.pt('LBF_3D_LEFT_EYE_CENTER') + self.pt('LBF_3D_RIGHT_EYE_CENTER')) / 2
        return [int(round(c[0] - s / 2)), int(round(c[1] - 0.42 * s)), int(round(s)), int(round(s))]

    def eye_mask(self, eyes):
        """Soft 0..1 mask in original pixels over each listed eye ('LEFT'/'RIGHT'): the eye plus the upper lid,
        stopping 60% of the way to the brow (same shape as the training targets)."""
        m = np.zeros(SIZE, np.uint8); ws = []
        for S in eyes:
            out, ins = self.pt(f'LBF_3D_{S}_EYE_OUTSIDE', False), self.pt(f'LBF_3D_{S}_EYE_INSIDE_OUT', False)
            top, bot = self.pt(f'LBF_3D_{S}_EYE_TOP', False), self.pt(f'LBF_3D_{S}_EYE_BOTTOM', False)
            brow = self.pt(f'LBF_3D_{S}_BROW_BOTTOM_2', False)
            w = abs(ins[0] - out[0]); ws.append(w)
            yt = top[1] - 0.6 * max(top[1] - brow[1], 0.2 * w)
            yb = bot[1] + 0.15 * w
            cv2.ellipse(m, (int((out[0] + ins[0]) / 2), int((yt + yb) / 2)), (int(0.72 * w), int((yb - yt) / 2)), 0, 0, 360, 255, -1)
        if not ws:
            return np.zeros(self.shape[:2], np.float32)
        m = cv2.GaussianBlur(m, (0, 0), 0.06 * np.mean(ws))
        return util_wrinkle.transform_ori_img(self.ti, m, SIZE, self.shape)[..., 0].astype(np.float32) / 255.0


def upper_lid(f, S, orig=False):
    """Upper-lid contour from the outer to the inner corner (5 landmarks)."""
    return np.array([f.pt(f'LBF_3D_{S}_EYE_{t}', orig) for t in ('OUTSIDE', 'M1', 'TOP', 'M2', 'INSIDE_OUT')], np.float64)


def lid_mask(f, eyes, below=0.10, above=0.6):
    """Soft 0..1 mask in original pixels covering only the upper lid of each listed eye: from `below`*eye width under
    the source's upper-lid line (to take the new lash line) up to `above` of the way to the brow. The visible eyeball,
    iris and lower lid stay outside the mask, so they keep the source's own pixels."""
    m = np.zeros(SIZE, np.uint8); ws = []
    for S in eyes:
        lid = upper_lid(f, S)
        out, ins = lid[0], lid[-1]
        w = abs(ins[0] - out[0]); ws.append(w)
        brow = f.pt(f'LBF_3D_{S}_BROW_BOTTOM_2', False)
        top_y = lid[2][1] - above * max(lid[2][1] - brow[1], 0.2 * w)
        xs = np.linspace(min(out[0], ins[0]), max(out[0], ins[0]), 40)
        order = np.argsort(lid[:, 0])
        ys = np.interp(xs, lid[order, 0], lid[order, 1]) + below * w
        pad = 0.12 * w  # run a little past the corners so the lid fold blends out
        bottom = np.stack([np.concatenate([[xs[0] - pad], xs, [xs[-1] + pad]]),
                           np.concatenate([[ys[0] - 0.05 * w], ys, [ys[-1] - 0.05 * w]])], 1)
        arch = np.stack([xs[::-1], top_y + (np.abs(xs[::-1] - xs.mean()) / (xs[-1] - xs[0] + 1e-6)) ** 2 * 0.6 * (ys.min() - top_y)], 1)
        cv2.fillPoly(m, [np.concatenate([bottom, arch]).round().astype(np.int32)], 255)
    if not ws:
        return np.zeros(f.shape[:2], np.float32)
    m = cv2.GaussianBlur(m, (0, 0), 0.025 * np.mean(ws))
    return util_wrinkle.transform_ori_img(f.ti, m, SIZE, f.shape)[..., 0].astype(np.float32) / 255.0


def cut(img, box):
    """Crop box [x, y, w, h] from img, reflect-padding where the box leaves the image."""
    x, y, w, h = box
    H, W = img.shape[:2]
    pad = max(0, -x, -y, x + w - W, y + h - H)
    if pad:
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REFLECT_101); x += pad; y += pad
    return img[y:y + h, x:x + w].copy()


def gaze(f, S):
    """Horizontal iris position inside the eye, as a fraction of eye width (0 = centred between the corners)."""
    out, ins, c = f.pt(f'LBF_3D_{S}_EYE_OUTSIDE', False), f.pt(f'LBF_3D_{S}_EYE_INSIDE_OUT', False), f.pt(f'LBF_3D_{S}_EYE_CENTER', False)
    return float((c[0] - (out[0] + ins[0]) / 2) / abs(ins[0] - out[0]))


# ---- paste-back helpers shared by paste.py and compose.py ----

def register(gen, src_crop, lid):
    """Align the model crop to the source crop (affine ECC on grey, ignoring the edited lids) so nothing shifts."""
    g, s = (cv2.GaussianBlur(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY), (0, 0), 2).astype(np.float32) for x in (gen, src_crop))
    keep = (cv2.dilate((lid > 0.01).astype(np.uint8), np.ones((25, 25), np.uint8)) == 0).astype(np.uint8)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(s, g, warp, cv2.MOTION_AFFINE, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), keep, 5)
    except cv2.error:
        return gen
    return cv2.warpAffine(gen, warp, (gen.shape[1], gen.shape[0]), flags=cv2.INTER_CUBIC + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)


def place(gen, box, shape):
    """Resize the model crop to its box and put it on a canvas the size of the source (outside the box: NaN-free zeros)."""
    x, y, w, h = box
    g = cv2.resize(gen, (w, h), interpolation=cv2.INTER_AREA if w < gen.shape[1] else cv2.INTER_CUBIC)
    canvas = np.zeros(shape, np.uint8); valid = np.zeros(shape[:2], np.float32)
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, shape[1]), min(y + h, shape[0])
    canvas[y0:y1, x0:x1] = g[y0 - y:y1 - y, x0 - x:x1 - x]; valid[y0:y1, x0:x1] = 1
    return canvas, valid


def tone_fix(src, gen, valid, mask):
    """Remove any overall colour/brightness drift of the model output: low-pass difference measured on a ring just outside the eyes."""
    ring = (cv2.dilate((mask > 0.01).astype(np.uint8), np.ones((41, 41), np.uint8)) > 0) & (mask <= 0.01) & (valid > 0)
    if ring.sum() < 100:
        return gen
    d = (src.astype(np.float32) - gen.astype(np.float32))[ring].mean(0)
    return np.clip(gen.astype(np.float32) + d, 0, 255)


def tile(img, f, S):
    """Eye close-up in the aligned face, same framing as the training review sheets."""
    a = util_wrinkle.transform_img(f.ti, img, SIZE)
    out, ins, top = f.pt(f'LBF_3D_{S}_EYE_OUTSIDE', False), f.pt(f'LBF_3D_{S}_EYE_INSIDE_OUT', False), f.pt(f'LBF_3D_{S}_EYE_TOP', False)
    w = abs(ins[0] - out[0]); cx = (out[0] + ins[0]) / 2
    x0, x1 = int(cx - .8 * w), int(cx + .8 * w); y0 = int(top[1] - .5 * w); y1 = y0 + int(1.0 * w)
    return cv2.resize(a[max(y0, 0):y1, max(x0, 0):x1], (300, 190), interpolation=cv2.INTER_CUBIC)


def eye_features(f, img, S):
    """Pre-analysis numbers for one eye (aligned face): opening, aperture (lid to lid, iris radii), iris_vis (share of the
    iris disk inside the eye opening), lum_ratio (eye box / cheek brightness, low behind dark lenses), contrast (white of
    the eye minus iris brightness) and tex (detail inside the opening, low behind lenses)."""
    a = util_wrinkle.transform_img(f.ti, img, SIZE)
    g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    P = lambda t: f.pt(f'LBF_3D_{S}_EYE_{t}', False)
    c = P('CENTER'); r = 0.5 * (np.linalg.norm(P('IRIS_LEFT') - c) + np.linalg.norm(P('IRIS_RIGHT') - c))
    w = abs(P('INSIDE_OUT')[0] - P('OUTSIDE')[0])
    # lid points sorted left to right: the landmark model numbers M1..M4 in mirror order on the two eyes
    up = np.array(sorted([P(t) for t in ('OUTSIDE', 'M1', 'TOP', 'M2', 'INSIDE_OUT')], key=lambda q: q[0]))
    lo = np.array(sorted([P(t) for t in ('OUTSIDE', 'M4', 'BOTTOM', 'M3', 'INSIDE_OUT')], key=lambda q: q[0]))
    ap = np.zeros(g.shape, np.uint8)
    cv2.fillPoly(ap, [np.concatenate([up, lo[::-1]]).round().astype(np.int32)], 1)
    disk = np.zeros(g.shape, np.uint8); cv2.circle(disk, (int(c[0]), int(c[1])), int(r), 1, -1)
    iris = (ap & disk) > 0; sclera = (ap > 0) & (disk == 0)
    x0, x1 = int(min(up[:, 0])), int(max(up[:, 0])); cy = int(P('BOTTOM')[1] + 0.6 * w)
    cheek = g[cy:cy + int(0.2 * w), x0:x1]
    box = g[int(P('TOP')[1] - 0.2 * w):int(P('BOTTOM')[1] + 0.1 * w), x0:x1]
    return {'opening': round(f.opening(S), 3),
            'aperture': round(float((P('BOTTOM')[1] - P('TOP')[1]) / r), 3),
            'iris_vis': round(float(iris.sum() / max(disk.sum(), 1)), 3),
            'lum_ratio': round(float(box.mean() / max(cheek.mean(), 1)), 3) if cheek.size and box.size else None,
            'contrast': round(float(g[sclera].mean() - g[iris].mean()), 1) if iris.sum() > 5 and sclera.sum() > 5 else None,
            'tex': round(float(cv2.Laplacian(g, cv2.CV_32F)[ap > 0].std()), 1) if ap.sum() > 5 else None}
