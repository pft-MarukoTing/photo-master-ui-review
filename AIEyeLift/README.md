# AIEyeLift

Lifts droopy upper eyelids so the eyes look more open, benchmarked against a competitor app's eye-lift ("the reference" below). The before/after must look like the same photo with only the upper lids raised: gaze, iris position, iris colour and eye position never change.

**Status (2026-11-03): stopped.** Two approaches were built and compared on the same 37 photos (20 validation + 17 prompt-tuning photos):

- **A. gpt-image-2 prompt edit**: one API call per photo on a both-eyes crop, plus a map image (red = upper lids, green = iris circles), pasted back with the eye mask.
- **B. Qwen-Image-Edit-2509 + LoRA**: same pipeline shape as AIDoubleChin. Square 1024x1024 eyes crop, LoRA in ComfyUI, paste back with the eye mask.

B is the better candidate. At strength 1.0 it lifts about as much as the reference and preserves the eye almost as well. A lifts similar amounts but often moves or redraws the iris (gaze shift), and results differ between runs of the same photo.

| Path | Purpose |
|---|---|
| `api/eyes.py` | Shared helpers: DEWE ONNX landmarks (`Face`), crop boxes, eye/lid masks, opening measure, `eye_features` (pre-analysis), paste-back |
| `api/prep.py`, `api/prep_decide.py`, `api/prep_iris.py`, `api/prep_map.py` | A: 2048x1024 both-eyes crop + meta, keep/lift decision, iris circles, map image |
| `api/call_api.py` + `api/prompts_v*.json` | A: one gpt-image-2 call per photo (severe or normal prompt). `prompts_v8.json` is the final prompt; v1–v9 are kept as history |
| `api/paste.py` | A: paste the API crop back into the full photo (eye mask + tone fix) |
| `api/score_eyes.py` | Eye-preservation score vs the source (see Metrics) |
| `api/make_review.py`, `api/compare_full.py`, `api/input_sheets.py` | Review sheets: full photo and eye close-ups, one column per method |
| `api/prep_lora.py`, `api/prep_sq.py` | B: cut training pairs / inference inputs with the square eyes crop |
| `api/probe_keep.py` | Probe of the eye features used to set the pre-analysis keep thresholds |
| `benchmark/run_benchmark.py` | B: end-to-end on full photos through ComfyUI, several checkpoints/strengths side by side, every stage dumped (same procedure as AIDoubleChin) |
| `models/eye_lift_sq_20261102.safetensors` | B: the final LoRA (6000 steps) |
| `training/lora_eyelift_sq/eye_lift_sq_20261102_config.yaml` | B: ai-toolkit config (195 before/after pairs from the reference, 1024x1024, rank 16). Training images aren't in git |
| `workflows/eyeLift_workflow_20261102_api.json` | B: ComfyUI API workflow (AIDoubleChin graph; LoRA loader node "Eye Lift LoRA Loader") |
| `data/splits/` | Photo lists: `train.txt`, `tune.txt` + `tune_severe_noref.txt` (A prompt tuning), `val.txt` (final test only, never trained or tuned on) |
| `inpainting/DroopyEyelidUtils/`, `inpainting/models/{facert_model,align_weights_3548}.onnx` | Face detection and the eye landmarks used by `api/eyes.py`, taken from the earlier DEWE baseline |

Not in git: photos (`data/`), all outputs (`results/`), training crops, slides (`docs/*.pptx`). The rest of the earlier DEWE + CoordFill inpainting baseline and the rejected post-processing experiments (compositing, geometric warp) stay on the developer PC only.

## Environments

- `api/`, `benchmark/` (except `call_api.py`): conda `python3.8` (opencv, onnxruntime).
- `api/call_api.py`: any env with `openai`; reads `OPENAI_API_KEY` from the environment.
- Sources must be converted to sRGB first. Some are Display P3, and the reference outputs drop the profile.

## Pipeline

Both approaches share the pre-analysis (`run_benchmark.analyse`). Per eye:
- **keep:** aperture < 0.40 (closed or slit eye) or eye/cheek brightness < 0.15 (dark lenses);
- **lift:** everything else.

A photo with no eye to lift is returned unchanged without calling the model. Severity (the lifted eyes' mean opening < 0.30 = severe) picks A's prompt.

### A. API

```bash
python api/prep.py <srgb dir> <names.txt> <prep dir>
python api/prep_decide.py <srgb dir> <prep dir>
python api/prep_iris.py <srgb dir> <prep dir>
python api/prep_map.py <prep dir>
python api/call_api.py <prep dir> <run dir> api/prompts_v8.json          # openai env
python api/paste.py <srgb dir> <prep dir> <run dir> <run dir> [reference dir]  # -> <run dir>/result/
```

Cost is about $0.17 per photo (2048x1024 high-quality output + 1 map image) and about 65 s per call.

### B. Qwen + LoRA

Train: copy `training/lora_eyelift_sq/{train,test}` (made by `api/prep_lora.py`) to `<ai-toolkit>/datasets/eye_lift_sq/`, then run `python run.py config/eye_lift_sq_20261102_config.yaml` from the ai-toolkit root (about 7 h on one H200).

Test: put the LoRA in ComfyUI's `models/loras/aiEyeLift/`, start ComfyUI, then:

```bash
cd benchmark
python run_benchmark.py --source-dir <srgb dir> --target-dir <reference dir> --comfyui-port 8191 \
    --checkpoint aiEyeLift/eye_lift_sq_20261102.safetensors aiEyeLift/eye_lift_sq_20261102.safetensors@2.0
```

`@<strength>` scales the LoRA weights; `x<passes>` feeds the output back in. Each run writes:
- `stages/<checkpoint>/<photo>/`: `0_analysis` … `5_diff`;
- `sheets/`, `compare/`, `eyes/`;
- `summary.txt`: AIDoubleChin metrics against the reference, plus eye preservation.

## Metrics (`api/score_eyes.py`)

Per eye, against the source:

| Metric | What it measures | Good value |
|---|---|---|
| `shift` | How far the lower half of the iris moved, in iris radii (gaze/position change) | ~0 |
| `change` | Pixel change of the lower iris after undoing the shift | small |
| `color` | Iris colour change (Lab a*/b*) | small |
| `rest` | Change around the eye outside the upper lid | small |
| `gain` | Opening after − before | positive |

**Opening** = (iris centre y − upper-lid top y) / iris radius. The droopy threshold is 0.30; the original target was 0.75.

## Final results (37 photos, 72 lifted eyes)

| | shift | change | color | rest | gain |
|---|---|---|---|---|---|
| Reference app | 0.012 | 3.42 | 1.40 | 2.74 | +0.075 |
| A, API v8 | 0.099 (max 0.66) | 6.52 | 1.73 | 2.24 | +0.085 |
| B, LoRA strength 1.0 | 0.015 | 4.02 | 1.27 | 1.88 | +0.071 |
| B, LoRA strength 2.0 | 0.021 | 4.75 | 1.59 | 2.27 | +0.100 |
| B, LoRA 1.0, two passes | 0.031 | 6.79 | 1.64 | 2.81 | +0.125 |

- **A:** the iris moves on some photos (gaze change, eyes look redrawn). Two runs of the same photo differ (mean gain spread 0.035). Passing the lid map as the API `mask` parameter was worse (shift 0.30); a map image is better.
- **B, strength 1.0:** about the same lift and eye preservation as the reference; closest to it on 23/37 photos.
- **B, strength 2.0:** a stronger lift with a single VAE pass, with fewer side effects than two passes.
- **B, two passes** (the output fed back in): the strongest lift. On severe eyes it was judged visually better than strength 2.0. Each pass adds a VAE round trip and a resample, so the result gets blurrier and smoother, the iris edge and the lines around the eye get harder, and iris colour drifts.
- **Severe eyes stay far from wide open with every setting.** For example, 0.21 → 0.29/0.34/0.37 (strength 1.0 / 2.0 / two passes) against the 0.75 target. The LoRA learned the reference's roughly constant +0.1 lift, and its training pairs contain no severe eye opened wide.

Review material is on the developer PC under `results/` (not in git):
- `results/final_20261103/`:
  - `compare/`: full photos, source | reference | A | B 1.0 | B 2.0 | B two passes;
  - `eyes/`: close-ups;
  - one folder per method;
  - `scores.csv`, `scores_summary.txt`.
- `results/lora/eye_lift_sq_20261102/output_strength2_20261103_115518/`: B intermediate stages.
- `results/api_tune/`, `results/api_val/`: A inputs and raw outputs.

## Next steps (if the project resumes)

1. **Prettier / healthier eyes:** beyond opening the lid, make the eye area look more attractive and rested, e.g. a cleaner crease, less puffiness or hooding, brighter and healthier-looking lid skin. This needs targets that show the look (curated or generated pairs) and a caption describing it. It also needs a decision on how far beyond "the same photo" the edit may go.
2. **More natural lid shape:** the lifted lid can show corners or kinks, especially at the outer corner and where the new lid line meets the original. Aim for a smooth, anatomically natural arc. Options:
   - filter or repair training targets with a lid-curvature check on the landmarks;
   - add curvature to the benchmark scores;
   - soften the paste-back mask at the eye corners.
3. **Severe eyes to wide open:** two passes gave the best severe results but with side effects (blur, harder edges, iris colour drift). Two ways to keep its lift without them:
   - **One pass that lifts as much:** build targets where droopy eyes reach about 0.75 (e.g. repeated passes, then artifact clean-up and manual selection), and retrain with these pairs and a separate severe caption.
   - **Keep the passes, limit the damage:** pass the latent between passes instead of decoding to pixels, or after the last pass re-paste only the upper-lid area and keep the source's iris and pixels everywhere else.
4. **Shipping B:** package like AIDoubleChin (custom nodes for crop/paste-back, engine, kowloon tag). The crop, pre-analysis and paste-back currently run in Python outside ComfyUI.
5. **Pre-analysis misses:** squinting eyes with no visible iris colour (e.g. 49_19667) pass the keep rules. The LoRA barely changes them, so the results still look natural.
