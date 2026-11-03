# Stage B (base env, needs `openai`): send each both-eyes crop to gpt-image-2 with the prompt for its severity.
# argv: prep dir (crop/ + meta/), output dir, prompts.json, [names...]   (OPENAI_API_KEY from the environment)
import os, sys, json, time, base64
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

PREP, O, PROMPTS = sys.argv[1:4]
ONLY = set(sys.argv[4:])
SEVERE = 0.30  # mean opening below this uses the severe prompt
prompts = json.load(open(PROMPTS))
client = OpenAI(max_retries=1, timeout=300)
os.makedirs(f'{O}/out', exist_ok=True)


def run(stem):
    meta = json.load(open(f'{PREP}/meta/{stem}.json'))
    lift = meta.get('lift_eyes', ['LEFT', 'RIGHT'])
    if not lift:
        return stem, 'skip', 0.0
    op = sum(meta['eyes'][S]['opening'] for S in lift) / len(lift)
    kind = 'severe' if op < SEVERE else 'normal'
    sides = sorted(meta['eyes'][S]['side'] for S in lift)
    which = 'both eyes' if len(sides) == 2 else f'only the eye on the {sides[0]} side of the image (leave the other eye exactly as it is)'
    target = prompts.get('target', 0.75)
    now = round(50 * (1 - op)); goal = max(round(50 * (1 - max(target, op + 0.08))), 0)  # % of iris height under the lid (opening is in iris radii)
    prompt = prompts[kind].format(which=which, now=now, goal=goal)
    t = time.time()
    extra = {}
    if prompts.get('use_mask') and os.path.exists(f'{PREP}/mask/{stem}.png'):
        extra['mask'] = open(f'{PREP}/mask/{stem}.png', 'rb')
    if prompts.get('input_fidelity'):
        extra['input_fidelity'] = prompts['input_fidelity']
    img = open(f'{PREP}/crop/{stem}.png', 'rb')
    if prompts.get('map_image'):  # one combined map as the second input image (red = upper lids, green = full irises)
        img = [img, open(f'{PREP}/map/{stem}.png', 'rb')]
    elif prompts.get('mask_image'):  # the editable area as a second input image (white = may change), referred to in the prompt
        img = [img, open(f'{PREP}/maskvis/{stem}.png', 'rb')]
        if prompts.get('iris_image'):  # each full iris as a white circle, the third input image
            img.append(open(f'{PREP}/iris/{stem}.png', 'rb'))
    r = client.images.edit(model='gpt-image-2', image=img, prompt=prompt, size='2048x1024', quality='high', **extra)
    open(f'{O}/out/{stem}.png', 'wb').write(base64.b64decode(r.data[0].b64_json))
    usage = r.usage.model_dump() if getattr(r, 'usage', None) else None
    json.dump({'kind': kind, 'prompt': prompt, 'seconds': round(time.time() - t, 1), 'usage': usage}, open(f'{O}/out/{stem}.json', 'w'), indent=1)
    return stem, kind, time.time() - t


stems = sorted(os.path.splitext(n)[0] for n in os.listdir(f'{PREP}/meta'))
stems = [s for s in stems if (not ONLY or s in ONLY) and not os.path.exists(f'{O}/out/{s}.png')]
with ThreadPoolExecutor(6) as ex:
    for fut in [ex.submit(run, s) for s in stems]:
        try:
            s, k, dt = fut.result(); print(s, k, f'{dt:.0f}s', flush=True)
        except Exception as e:
            print('FAIL', type(e).__name__, str(e)[:300], flush=True)
