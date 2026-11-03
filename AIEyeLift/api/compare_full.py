# Full-image side-by-sides (python3.8): source | reference app | ours at the source's full resolution, nothing cropped.
# argv: source dir, reference dir, result dir (<stem>.jpg), output dir
import os, sys
import cv2, numpy as np

SRC, REF, RES, O = sys.argv[1:5]
os.makedirs(O, exist_ok=True)
for n in sorted(os.listdir(SRC)):
    stem = os.path.splitext(n)[0]
    if not os.path.exists(f'{RES}/{stem}.jpg'):
        continue
    src = cv2.imread(f'{SRC}/{n}')
    imgs = [src, cv2.imread(f'{REF}/{n}') if os.path.exists(f'{REF}/{n}') else np.zeros_like(src), cv2.imread(f'{RES}/{stem}.jpg')]
    imgs = [im if im.shape == src.shape else cv2.resize(im, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA) for im in imgs]
    s = max(2, src.shape[1] // 400)
    for im, lab in zip(imgs, ('source', 'reference', 'ours')):
        for c, th in (((0, 0, 0), 4 * s), ((255, 255, 255), s)):
            cv2.putText(im, lab, (10 * s, 30 * s), 0, s, c, th)
    gap = np.full((src.shape[0], 8, 3), 255, np.uint8)
    cv2.imwrite(f'{O}/{stem}.jpg', np.hstack([imgs[0], gap, imgs[1], gap, imgs[2]]), [cv2.IMWRITE_JPEG_QUALITY, 92])
