# Stage A2 (python3.8, after prep.py): the same keep/lift pre-analysis as the LoRA pipeline (benchmark/run_benchmark.py
# analyse) written into meta/ as lift_eyes + severity, and maskvis/ (white = upper lids, the editable area) from mask/.
# Then prep_iris.py + prep_map.py build the map image sent as the API's second input.
# argv: sRGB source dir, prep dir
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'benchmark'))
import cv2, numpy as np
from eyes import Face
from run_benchmark import analyse

SRC, PREP = sys.argv[1:3]
os.makedirs(f'{PREP}/maskvis', exist_ok=True)
for fn in sorted(os.listdir(f'{PREP}/meta')):
    meta = json.load(open(f'{PREP}/meta/{fn}'))
    src = cv2.imread(f'{SRC}/{meta["name"]}')
    a = analyse(src, Face(src))
    meta['lift_eyes'] = a['lift_eyes']; meta['severity'] = a['severity']
    json.dump(meta, open(f'{PREP}/meta/{fn}', 'w'), indent=1)
    stem = os.path.splitext(fn)[0]
    alpha = cv2.imread(f'{PREP}/mask/{stem}.png', cv2.IMREAD_UNCHANGED)[..., 3]
    cv2.imwrite(f'{PREP}/maskvis/{stem}.png', np.where(alpha == 0, 255, 0).astype(np.uint8))
    print(meta['name'], a['lift_eyes'], a['severity'])
