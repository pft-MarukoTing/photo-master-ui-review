# Iris map for the API crops (python3.8): each lifted eye's full iris as a filled white circle on black (centre and
# radius from the landmarks, including the part hidden under the lid), same 2048x1024 framing as crop/.
# argv: sRGB source dir, prep dir (meta/ in, iris/ out)
import os, sys, json
import cv2, numpy as np
from eyes import Face, CROP_W, CROP_H

SRC, PREP = sys.argv[1:3]
os.makedirs(f'{PREP}/iris', exist_ok=True)
for fn in sorted(os.listdir(f'{PREP}/meta')):
    meta = json.load(open(f'{PREP}/meta/{fn}'))
    f = Face(cv2.imread(f'{SRC}/{meta["name"]}'))
    x, y, w, h = meta['box']; k = CROP_W / w
    m = np.zeros((CROP_H, CROP_W, 3), np.uint8)
    for S in meta.get('lift_eyes', ['LEFT', 'RIGHT']):
        c = f.pt(f'LBF_3D_{S}_EYE_CENTER')
        r = 0.5 * (np.linalg.norm(f.pt(f'LBF_3D_{S}_EYE_IRIS_LEFT') - c) + np.linalg.norm(f.pt(f'LBF_3D_{S}_EYE_IRIS_RIGHT') - c))
        cv2.circle(m, (int(round((c[0] - x) * k)), int(round((c[1] - y) * k))), int(round(r * k)), (255, 255, 255), -1, cv2.LINE_AA)
    cv2.imwrite(f'{PREP}/iris/{os.path.splitext(fn)[0]}.png', m)
print(len(os.listdir(f'{PREP}/iris')))
