# LoRA dataset (python3.8): cut source/target pairs with the same square eyes crop as inference, ai-toolkit layout.
# argv: train_pairs dir, val source dir, sample names (comma-separated, from val), output dir, caption
import os, sys, csv
import cv2
from eyes import Face, cut

W, H = 1024, 1024  # square: ai-toolkit distorts non-square control images (see eyes.square_box)
TP, VAL, SAMPLES, O, CAPTION = sys.argv[1:6]
for d in ('train/control', 'train/target', 'test/control'):
    os.makedirs(f'{O}/{d}', exist_ok=True)


def save(img, box, path):
    c = cut(img, box)
    cv2.imwrite(path, cv2.resize(c, (W, H), interpolation=cv2.INTER_CUBIC if box[2] < W else cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 95])


n_ok = 0
for r in csv.DictReader(open(f'{TP}/pairs.csv')):
    n = r['name']; stem = os.path.splitext(n)[0]
    src = cv2.imread(f'{TP}/source/{n}'); tgt = cv2.imread(f'{TP}/target/{n}')
    try:
        box = Face(src).square_box()
    except Exception as e:
        print('no face', n, e); continue
    save(src, box, f'{O}/train/control/{stem}.jpg')
    save(tgt, box, f'{O}/train/target/{stem}.jpg')
    open(f'{O}/train/target/{stem}.txt', 'w').write(CAPTION)
    n_ok += 1
for n in SAMPLES.split(','):
    img = cv2.imread(f'{VAL}/{n}')
    save(img, Face(img).square_box(), f'{O}/test/control/{os.path.splitext(n)[0]}.jpg')
print(n_ok)
