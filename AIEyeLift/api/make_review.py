# Visual review set (python3.8): for each photo, full-image and eye close-up sheets with one column per method,
# plus a plain folder per column (same file names) for flipping between images in a viewer.
# argv: output dir, source dir, label::result_dir ...   (result files: <stem>.jpg or the source's name; missing = black)
import os, sys, shutil
import cv2, numpy as np
from eyes import Face, tile

O, SRC = sys.argv[1:3]
COLS = [s.split('::') for s in sys.argv[3:]]
SHEET_H = 1600
for d in ['compare', 'eyes', 'source'] + [lab for lab, _ in COLS]:
    os.makedirs(f'{O}/{d}', exist_ok=True)


def find(d, n):
    stem = os.path.splitext(n)[0]
    for p in (f'{d}/{stem}.jpg', f'{d}/{n}', f'{d}/{stem}.png'):
        if os.path.exists(p):
            return p
    return None


def label(img, text):
    img = img.copy(); s = max(1, img.shape[1] // 500)
    for c, th in (((0, 0, 0), 4 * s), ((255, 255, 255), s)):
        cv2.putText(img, text, (10 * s, 30 * s), 0, 0.8 * s, c, th)
    return img


for n in sorted(os.listdir(SRC)):
    stem = os.path.splitext(n)[0]
    src = cv2.imread(f'{SRC}/{n}')
    shutil.copy(f'{SRC}/{n}', f'{O}/source/{stem}.jpg' if n.lower().endswith('.jpg') else f'{O}/source/{n}')
    cols = [('source', src)]
    for lab, d in COLS:
        p = find(d, n)
        im = cv2.imread(p) if p else None
        if im is not None:
            if im.shape != src.shape:
                im = cv2.resize(im, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA)
            cv2.imwrite(f'{O}/{lab}/{stem}.jpg', im, [cv2.IMWRITE_JPEG_QUALITY, 95])
        cols.append((lab, im))
    h, w = src.shape[:2]; k = min(1.0, SHEET_H / h); size = (round(w * k), round(h * k))
    gap = np.full((size[1], 8, 3), 255, np.uint8); row = []
    for lab, im in cols:
        im = np.zeros((size[1], size[0], 3), np.uint8) if im is None else cv2.resize(im, size, interpolation=cv2.INTER_AREA)
        row += [label(im, lab), gap]
    cv2.imwrite(f'{O}/compare/{stem}.jpg', np.hstack(row[:-1]), [cv2.IMWRITE_JPEG_QUALITY, 92])
    f = Face(src); rows = []
    for S in ('LEFT', 'RIGHT'):
        t = []
        for lab, im in cols:
            x = np.zeros((190, 300, 3), np.uint8) if im is None else tile(im, f, S)
            x = cv2.resize(x, (450, 285), interpolation=cv2.INTER_CUBIC)
            for c, th in (((0, 0, 0), 3), ((0, 255, 0), 1)):
                cv2.putText(x, f'{S[0]} {lab}', (6, 274), 0, 0.6, c, th)
            t.append(cv2.copyMakeBorder(x, 0, 2, 0, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
        rows.append(np.hstack(t))
    cv2.imwrite(f'{O}/eyes/{stem}.jpg', np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 92])
print(O, len(os.listdir(f'{O}/compare')))
