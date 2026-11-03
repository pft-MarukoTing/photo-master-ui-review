# Pull the LoRA run's preview samples from the H200 and build a review sheet:
# one row per validation crop, columns = input | step 0 (base model) | earlier step | latest step.
# argv: run name, local results dir, local dataset test/control dir
import os, sys, re, subprocess
import cv2, numpy as np

RUN, O, CTRL = sys.argv[1:4]
SAMPLES = ['55_43658_Original', '16_50448_Original', '22_45120_Original', '49_44980_Original', '36_39845_Original', '38_22097_Original']
LABELS = ['severe (reference lifted)', 'moderate', 'mild-mod', 'mild', 'severe (reference failed)', 'closed: keep as is']
os.makedirs(f'{O}/samples', exist_ok=True)
subprocess.run(['scp', '-q', f'h200:/data/home/rdmt/jerry_chen/ai-toolkit/output/{RUN}/samples/*.jpg', f'{O}/samples/'], check=True)
by = {}
for f in os.listdir(f'{O}/samples'):
    m = re.match(r'\d+__(\d+)_(\d+)\.jpg$', f)
    if m:
        by.setdefault(int(m.group(2)), {})[int(m.group(1))] = f
steps = sorted({s for d in by.values() for s in d})
pick = sorted({steps[0], steps[len(steps) // 2], steps[-1]})
rows = []
for i, stem in enumerate(SAMPLES):
    tiles = [(cv2.imread(f'{CTRL}/{stem}.jpg'), 'input')] + [(cv2.imread(f'{O}/samples/{by[i][s]}'), f'step {s}') for s in pick if s in by.get(i, {})]
    t = []
    for im, lab in tiles:
        im = cv2.resize(im, (round(im.shape[1] * 420 / im.shape[0]), 420), interpolation=cv2.INTER_AREA)
        for c, th in (((0, 0, 0), 3), ((0, 255, 0), 1)):
            cv2.putText(im, lab + ('   ' + stem + ' - ' + LABELS[i] if lab == 'input' else ''), (6, 22), 0, 0.55, c, th)
        t.append(cv2.copyMakeBorder(im, 0, 0, 0, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    rows.append(cv2.copyMakeBorder(np.hstack(t), 0, 4, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
w = max(r.shape[1] for r in rows)
rows = [cv2.copyMakeBorder(r, 0, 0, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255)) for r in rows]
cv2.imwrite(f'{O}/latest_sheet.jpg', np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
print('steps', steps, '-> sheet columns', pick)
