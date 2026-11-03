# What the API actually receives, per photo (python3.8): crop | combined map (2nd input image) | map over the crop |
# API mask parameter (transparent = may edit, shown white on a checkerboard).   argv: prep dir, output dir
import os, sys
import cv2, numpy as np

PREP, O = sys.argv[1:3]
os.makedirs(O, exist_ok=True)
W, H = 1024, 512
for n in sorted(os.listdir(f'{PREP}/map')):
    crop = cv2.resize(cv2.imread(f'{PREP}/crop/{n}'), (W, H), interpolation=cv2.INTER_AREA)
    m = cv2.resize(cv2.imread(f'{PREP}/map/{n}'), (W, H), interpolation=cv2.INTER_NEAREST)
    over = crop.copy(); on = m.max(2) > 0
    over[on] = (0.45 * crop[on] + 0.55 * m[on]).astype(np.uint8)
    a = cv2.resize(cv2.imread(f'{PREP}/mask/{n}', cv2.IMREAD_UNCHANGED)[..., 3], (W, H), interpolation=cv2.INTER_NEAREST)
    yy, xx = np.mgrid[0:H, 0:W]
    checker = np.where(((yy // 16 + xx // 16) % 2)[..., None] == 0, 90, 60).astype(np.uint8).repeat(3, 2)
    mask = np.where((a < 128)[..., None], 255, checker).astype(np.uint8)
    tiles = []
    for im, t in ((crop, '1 crop (1st input image)'), (m, '2 combined map (2nd input image)'),
                  (over, 'map over the crop'), (mask, 'API mask parameter (white = may edit)')):
        im = im.copy()
        for c, th in (((0, 0, 0), 4), ((255, 255, 255), 1)):
            cv2.putText(im, t, (10, 30), 0, 0.8, c, th)
        tiles.append(cv2.copyMakeBorder(im, 0, 4, 0, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    cv2.imwrite(f'{O}/{os.path.splitext(n)[0]}.jpg', np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]), [cv2.IMWRITE_JPEG_QUALITY, 92])
print(len(os.listdir(O)))
