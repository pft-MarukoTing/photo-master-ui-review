# Stage C (python3.8): paste the model's both-eyes crop back into the full-resolution sRGB source, eye masks only,
# re-measure each eye and write before | reference app | ours review rows.
# argv: sRGB source dir, prep dir, model output dir (out/<stem>.png), output dir, [reference target dir]
import os, sys, csv, json
import cv2, numpy as np
from eyes import Face, cut, lid_mask, register, place, tone_fix, tile, CROP_W, CROP_H

SRC, PREP, GEN, O = sys.argv[1:5]
REF = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != '-' else None
MODE = sys.argv[6] if len(sys.argv) > 6 else 'eye'  # 'eye': whole eye + lid; 'eyereg': same after aligning the crop; 'lid': upper lid only
os.makedirs(f'{O}/result', exist_ok=True); os.makedirs(f'{O}/review', exist_ok=True)


rows, stats = [], []
for fn in sorted(os.listdir(f'{PREP}/meta')):
    meta = json.load(open(f'{PREP}/meta/{fn}')); n = meta['name']; stem = os.path.splitext(n)[0]
    if not os.path.exists(f'{GEN}/out/{stem}.png'):
        continue
    src = cv2.imread(f'{SRC}/{n}'); f = Face(src)
    lift = meta.get('lift_eyes', ['LEFT', 'RIGHT'])
    alpha = lid_mask(f, lift) if MODE == 'lid' else f.eye_mask(lift)
    out = cv2.imread(f'{GEN}/out/{stem}.png')
    if MODE in ('lid', 'eyereg'):
        out = register(out, cv2.resize(cut(src, meta['box']), (CROP_W, CROP_H), interpolation=cv2.INTER_AREA),
                       cv2.resize(cut(alpha, meta['box']), (CROP_W, CROP_H)))
    gen, valid = place(out, meta['box'], src.shape)
    gen = tone_fix(src, gen, valid, alpha)
    a = (alpha * valid)[..., None]
    res = np.clip(src * (1 - a) + gen * a + 0.5, 0, 255).astype(np.uint8)
    cv2.imwrite(f'{O}/result/{stem}.jpg', res, [cv2.IMWRITE_JPEG_QUALITY, 95])
    try:
        g = Face(res); after = {S: g.opening(S) for S in ('LEFT', 'RIGHT')}
    except Exception:
        after = {S: float('nan') for S in ('LEFT', 'RIGHT')}
    ref = cv2.imread(f'{REF}/{n}') if REF and os.path.exists(f'{REF}/{n}') else None
    if ref is not None and ref.shape != src.shape:
        ref = cv2.resize(ref, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA)
    rm = {}
    if ref is not None:
        try:
            r = Face(ref); rm = {S: r.opening(S) for S in ('LEFT', 'RIGHT')}
        except Exception:
            pass
    before = {S: f.opening(S) for S in ('LEFT', 'RIGHT')}
    js = json.load(open(f'{GEN}/out/{stem}.json')) if os.path.exists(f'{GEN}/out/{stem}.json') else {}
    stats.append([n, js.get('kind', ''), js.get('seconds', '')] + [round(v, 3) for S in ('LEFT', 'RIGHT') for v in (before[S], rm.get(S, float('nan')), after[S])])
    imgs = [src] + ([ref] if ref is not None else []) + [res]
    t = []
    for S in ('LEFT', 'RIGHT'):
        for im in imgs:
            t.append(cv2.copyMakeBorder(tile(im, f, S), 0, 0, 0, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    row = np.hstack(t)
    lab = f'{n} [{js.get("kind", "")}]  ' + '   '.join(f'{S[0]} {before[S]:.2f} ref {rm.get(S, float("nan")):.2f} ours {after[S]:.2f}' for S in ('LEFT', 'RIGHT'))
    for c, th in (((0, 0, 0), 3), ((0, 255, 0), 1)):
        cv2.putText(row, lab, (4, 182), 0, 0.45, c, th)
    rows.append(cv2.copyMakeBorder(row, 0, 3, 0, 0, cv2.BORDER_CONSTANT))
for k in range(0, len(rows), 6):
    cv2.imwrite(f'{O}/review/sheet_{k // 6 + 1:02d}.jpg', np.vstack(rows[k:k + 6]), [cv2.IMWRITE_JPEG_QUALITY, 90])
with open(f'{O}/scores.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['name', 'prompt', 'seconds', 'src_L', 'ref_L', 'ours_L', 'src_R', 'ref_R', 'ours_R']); w.writerows(stats)
print(len(stats))
