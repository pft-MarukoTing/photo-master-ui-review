# Feature probe for the keep-as-is pre-check (python3.8): per eye, numbers that might separate closed / sunglasses /
# iris-not-visible eyes from droopy-but-visible ones. argv: out csv, dir::label ...
import os, sys, csv
import cv2, numpy as np
from eyes import Face, SIZE, util_wrinkle

rows = []
for spec in sys.argv[2:]:
    d, lab = spec.split('::')
    names = sorted(os.listdir(d))
    if lab == 'train_lift':
        names = names[::10][:20]
    for n in names:
        img = cv2.imread(f'{d}/{n}')
        try:
            f = Face(img)
        except Exception:
            continue
        a = util_wrinkle.transform_img(f.ti, img, SIZE)
        g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hsv = cv2.cvtColor(a, cv2.COLOR_BGR2HSV).astype(np.float32)
        for S in ('LEFT', 'RIGHT'):
            P = lambda t: f.pt(f'LBF_3D_{S}_EYE_{t}', False)
            c = P('CENTER'); r = 0.5 * (np.linalg.norm(P('IRIS_LEFT') - c) + np.linalg.norm(P('IRIS_RIGHT') - c))
            w = abs(P('INSIDE_OUT')[0] - P('OUTSIDE')[0])
            up = np.array([P(t) for t in ('OUTSIDE', 'M1', 'TOP', 'M2', 'INSIDE_OUT')])
            lo = np.array([P(t) for t in ('OUTSIDE', 'M4', 'BOTTOM', 'M3', 'INSIDE_OUT')])
            ap = np.zeros(g.shape, np.uint8)
            cv2.fillPoly(ap, [np.concatenate([up, lo[::-1]]).round().astype(np.int32)], 1)
            disk = np.zeros(g.shape, np.uint8); cv2.circle(disk, (int(c[0]), int(c[1])), int(r), 1, -1)
            iris = (ap & disk) > 0; sclera = (ap > 0) & (disk == 0)
            vis = iris.sum() / max(disk.sum(), 1)
            aperture = (P('BOTTOM')[1] - P('TOP')[1]) / r
            # skin under the eye (cheek) and the whole eye box, for sunglasses
            cy = int(P('BOTTOM')[1] + 0.6 * w); x0, x1 = int(min(up[:, 0])), int(max(up[:, 0]))
            cheek = g[cy:cy + int(0.2 * w), x0:x1]
            box = g[int(P('TOP')[1] - 0.2 * w):int(P('BOTTOM')[1] + 0.1 * w), x0:x1]
            lum_ratio = box.mean() / max(cheek.mean(), 1)
            contrast = (g[sclera].mean() - g[iris].mean()) if iris.sum() > 5 and sclera.sum() > 5 else 0.0
            sat_iris = hsv[..., 1][iris].mean() if iris.sum() > 5 else 0.0
            tex = cv2.Laplacian(g, cv2.CV_32F)[ap > 0].std() if ap.sum() > 5 else 0.0
            rows.append([lab, n, S, round(f.opening(S), 3), round(aperture, 3), round(vis, 3), round(lum_ratio, 3), round(contrast, 1), round(sat_iris, 1), round(tex, 1)])
with open(sys.argv[1], 'w', newline='') as fh:
    csv.writer(fh).writerows([['set', 'name', 'eye', 'opening', 'aperture', 'iris_vis', 'lum_ratio', 'contrast', 'sat_iris', 'tex']] + rows)
for r in rows:
    print(*r)
