# LoRA inference input (python3.8): the same 1024x1024 square eyes crop as training, plus the box for pasting back
# (paste.py reads meta/ + <gen>/out/ the same way as for the API crops).
# argv: sRGB source dir, output dir
import os, sys, json
import cv2
from eyes import Face, cut

SRC, O = sys.argv[1:3]
for d in ('crop', 'meta'):
    os.makedirs(f'{O}/{d}', exist_ok=True)
for n in sorted(os.listdir(SRC)):
    stem = os.path.splitext(n)[0]
    img = cv2.imread(f'{SRC}/{n}')
    try:
        f = Face(img)
    except Exception as e:
        print('no face', n, e); continue
    box = f.square_box()
    c = cut(img, box)
    cv2.imwrite(f'{O}/crop/{stem}.png', cv2.resize(c, (1024, 1024), interpolation=cv2.INTER_CUBIC if box[2] < 1024 else cv2.INTER_AREA))
    json.dump({'name': n, 'box': box, 'eyes': {S: {'side': f.image_side(S), 'opening': round(f.opening(S), 3)} for S in ('LEFT', 'RIGHT')}},
              open(f'{O}/meta/{stem}.json', 'w'), indent=1)
print(len(os.listdir(f'{O}/meta')))
