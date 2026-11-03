# Eye-preservation score for results vs their sources (python3.8). Per eye, in the source's alignment:
#   shift  - how far the source's visible lower iris moved in the result (template match), in iris radii
#   change - mean abs difference of that lower-iris patch after undoing the shift (0-255)
#   color  - mean abs a*/b* (Lab) difference of that patch: iris colour change
#   rest   - mean abs difference around the eye (corners, lower lid, skin) outside the upper-lid area: anything else that moved
#   gain   - opening(result) - opening(source)
# The lower half of the iris is never covered by the upper lid, so a good eyelid lift leaves it untouched.
# argv: source dir, out csv, result_dir[::label] ...    (result files: <stem>.jpg, or the original name)
import os, sys, csv
import cv2, numpy as np
from eyes import Face, lid_mask, util_wrinkle, SIZE

KEYS = ['shift', 'change', 'color', 'rest', 'gain']


def eye_scores(src, res, f=None):
    """{'LEFT'|'RIGHT': {shift, change, color, rest, gain}} for one result; eyes too closed to measure are left out."""
    f = f or Face(src)
    if res.shape != src.shape:
        res = cv2.resize(res, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA)
    A, B = (util_wrinkle.transform_img(f.ti, x, SIZE) for x in (src, res))
    a, b = (cv2.cvtColor(x, cv2.COLOR_BGR2GRAY).astype(np.float32) for x in (A, B))
    la, lb = (cv2.cvtColor(x, cv2.COLOR_BGR2LAB)[..., 1:].astype(np.float32) for x in (A, B))
    sg, rg = (cv2.cvtColor(x, cv2.COLOR_BGR2GRAY).astype(np.float32) for x in (src, res))
    try:
        g = Face(res)
    except Exception:
        g = None
    out = {}
    for S in ('LEFT', 'RIGHT'):
        c = f.pt(f'LBF_3D_{S}_EYE_CENTER', False)
        r = 0.5 * (np.linalg.norm(f.pt(f'LBF_3D_{S}_EYE_IRIS_LEFT', False) - c) + np.linalg.norm(f.pt(f'LBF_3D_{S}_EYE_IRIS_RIGHT', False) - c))
        bot = f.pt(f'LBF_3D_{S}_EYE_BOTTOM', False)[1]
        x0, x1 = int(c[0] - 0.8 * r), int(c[0] + 0.8 * r)
        y0, y1 = int(c[1]), int(min(c[1] + 0.8 * r, bot - 1))
        if y1 - y0 < 3:
            continue
        tpl = a[y0:y1, x0:x1]
        m = int(0.6 * r) + 2
        win = b[y0 - m:y1 + m, x0 - m:x1 + m]
        _, _, loc, _ = cv2.minMaxLoc(cv2.matchTemplate(win, tpl, cv2.TM_SQDIFF))
        dx, dy = loc[0] - m, loc[1] - m
        py, px = y0 - m + loc[1], x0 - m + loc[0]
        change = float(np.abs(win[loc[1]:loc[1] + tpl.shape[0], loc[0]:loc[0] + tpl.shape[1]] - tpl).mean())
        color = float(np.abs(lb[py:py + tpl.shape[0], px:px + tpl.shape[1]] - la[y0:y1, x0:x1]).mean())
        # around the eye in the original image, minus the upper lid (which is supposed to change)
        o_, i_ = f.pt(f'LBF_3D_{S}_EYE_OUTSIDE'), f.pt(f'LBF_3D_{S}_EYE_INSIDE_OUT')
        w = abs(i_[0] - o_[0]); ey = f.pt(f'LBF_3D_{S}_EYE_CENTER')[1]
        bx0, bx1 = int(min(o_[0], i_[0]) - 0.3 * w), int(max(o_[0], i_[0]) + 0.3 * w)
        by0, by1 = int(ey - 0.6 * w), int(ey + 0.45 * w)
        keep = cv2.dilate((lid_mask(f, [S]) > 0.02).astype(np.uint8), np.ones((5, 5), np.uint8))[by0:by1, bx0:bx1] == 0
        rest = float(np.abs(rg[by0:by1, bx0:bx1] - sg[by0:by1, bx0:bx1])[keep].mean())
        gain = (g.opening(S) - f.opening(S)) if g is not None else float('nan')
        out[S] = {'shift': round(float(np.hypot(dx, dy) / r), 3), 'change': round(change, 2), 'color': round(color, 2),
                  'rest': round(rest, 2), 'gain': round(gain, 3)}
    return out


def summary_line(label, scores):
    """scores: list of per-eye dicts."""
    import statistics as st
    v = {k: [s[k] for s in scores if s[k] == s[k]] for k in KEYS}
    return (f'{label:14s} eyes {len(scores):3d} | shift {st.mean(v["shift"]):.3f} max {max(v["shift"]):.3f} | '
            f'change {st.mean(v["change"]):5.2f} max {max(v["change"]):5.2f} | color {st.mean(v["color"]):4.2f} max {max(v["color"]):5.2f} | '
            f'rest {st.mean(v["rest"]):4.2f} max {max(v["rest"]):5.2f} | gain mean {st.mean(v["gain"]):+.3f} min {min(v["gain"]):+.3f} max {max(v["gain"]):+.3f}')


if __name__ == '__main__':
    SRC, OUT = sys.argv[1:3]
    rows, by = [], {}
    for spec in sys.argv[3:]:
        d, label = (spec.split('::') + [os.path.basename(os.path.dirname(spec.rstrip('/')))])[:2]
        for n in sorted(os.listdir(SRC)):
            stem = os.path.splitext(n)[0]
            cand = [p for p in (f'{d}/{stem}.jpg', f'{d}/{n}') if os.path.exists(p)]
            if not cand:
                continue
            for S, s in eye_scores(cv2.imread(f'{SRC}/{n}'), cv2.imread(cand[0])).items():
                rows.append([label, n, S] + [s[k] for k in KEYS]); by.setdefault(label, []).append(s)
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['run', 'name', 'eye'] + KEYS); w.writerows(rows)
    for k, v in by.items():
        print(summary_line(k, v))
