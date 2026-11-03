# Stage A (python3.8): landmarks -> one both-eyes crop per image (2048x1024 PNG) + meta JSON for the API call and paste-back.
# argv: sRGB source dir, names file, output dir
import os, sys, json
import cv2, numpy as np
from eyes import Face, cut, lid_mask, CROP_W, CROP_H

SRC, LIST, O = sys.argv[1:4]
for d in ('crop', 'meta', 'mask'):
    os.makedirs(f'{O}/{d}', exist_ok=True)
for n in open(LIST).read().split():
    stem = os.path.splitext(n)[0]
    img = cv2.imread(f'{SRC}/{n}')
    try:
        f = Face(img)
    except Exception as e:
        print('no face', n, e); continue
    box = f.crop_box()
    crop = cv2.resize(cut(img, box), (CROP_W, CROP_H), interpolation=cv2.INTER_CUBIC if box[2] < CROP_W else cv2.INTER_AREA)
    cv2.imwrite(f'{O}/crop/{stem}.png', crop)
    # API edit mask: transparent (editable) over the upper lids only, a little wider than the paste-back mask
    m = cv2.resize(cut(lid_mask(f, ['LEFT', 'RIGHT']), box), (CROP_W, CROP_H), interpolation=cv2.INTER_LINEAR)
    m = cv2.dilate((m > 0.02).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    rgba = np.dstack([crop, (255 * (1 - m)).astype(np.uint8)])
    cv2.imwrite(f'{O}/mask/{stem}.png', rgba)
    ws ={S: f.eye_width(S) for S in ('LEFT', 'RIGHT')}
    meta = {'name': n, 'box': box, 'shape': list(img.shape[:2]),
            'eyes': {S: {'side': f.image_side(S), 'opening': round(f.opening(S), 3),
                         # big head turn: the far eye looks much narrower than the near one
                         'partial': ws[S] < 0.55 * max(ws.values())} for S in ('LEFT', 'RIGHT')}}
    json.dump(meta, open(f'{O}/meta/{stem}.json', 'w'), indent=1)
    print(n, box, {S: (e['side'], e['opening'], e['partial']) for S, e in meta['eyes'].items()})
