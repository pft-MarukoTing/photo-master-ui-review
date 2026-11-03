# Combined map for the API crops (python3.8): one image instead of maskvis/ + iris/ -- red = upper lids (may change),
# green = each lifted eye's full iris circle (drawn over the red), black = must stay as it is.
# argv: prep dir (maskvis/ + iris/ in, map/ out)
import os, sys
import cv2, numpy as np

PREP = sys.argv[1]
os.makedirs(f'{PREP}/map', exist_ok=True)
for n in sorted(os.listdir(f'{PREP}/maskvis')):
    lid = cv2.imread(f'{PREP}/maskvis/{n}', cv2.IMREAD_GRAYSCALE) > 127
    iris = cv2.imread(f'{PREP}/iris/{n}', cv2.IMREAD_GRAYSCALE) > 127
    m = np.zeros(lid.shape + (3,), np.uint8)
    m[lid] = (0, 0, 255); m[iris] = (0, 255, 0)
    cv2.imwrite(f'{PREP}/map/{n}', m)
print(len(os.listdir(f'{PREP}/map')))
