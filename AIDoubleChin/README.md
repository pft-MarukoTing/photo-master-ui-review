# AIDoubleChin

Qwen-Image-Edit-2509 LoRA (trained with [ai-toolkit](https://github.com/ostris/ai-toolkit)) that removes a double chin from a single input image, same approach as AINail / AISmile.

| Path | Purpose |
|---|---|
| `datasets/prepare_pairs.py` | Pads before/after pairs to 1024x1024 and splits them into `train/` and `test/` |
| `datasets/test_split.txt` | Held-out test stems (never trained on; also used as in-training sample previews) |
| `models/double_chin_lora_20261001_config.yaml` | ai-toolkit training config (paths relative to the ai-toolkit root) |
| `workflows/doubleChin_workflow_20261001_api.json` | ComfyUI API workflow used for testing |
| `workflows/doubleChin_faceCrop_workflow_20261006_api.json` | Production-style workflow (aiSmile graph): face detect -> crop incl. neck -> LoRA -> paste back (no detail restorer, see below); no face = `ERROR_NO_FACE` |
| `workflows/doubleChin_faceCrop_workflow_20261014_api.json` | Same graph with `AIDoubleChinPasteBack` instead of `FaceRegionPasteBack`, so the source's full-resolution detail is kept (see 5.1) |
| `custom_nodes/ComfyUI_AIDoubleChin_utils` | `AIDoubleChinFaceRegionCrop`: aiSmile's FaceRegionCrop with the face box extended below the chin (`neck_extend_ratio`, default 0.35); `AIDoubleChinPasteBack`: paste-back of only the edited area, the rest stays the source pixels (see 5.1) |
| `custom_nodes/ComfyUI-FaceRestorer` | Copy of aiSmile's FaceRestorer (face crop + paste back + its ONNX models), unchanged; its detail restorer node is not used |
| `benchmark/run_benchmark.py` | Scores checkpoints on the test split against ground truth (uses `comfy_client.py` + `eval_metrics.py` next to it) |
| `benchmark/run_inference.py` | Runs any folder of images through checkpoints, no ground truth needed |
| `comfyUI_setup.sh` | Downloads the base models the workflow needs into ComfyUI's `models/` |
| `datasets/requirements.txt` | Deps for dataset prep + benchmark (training uses ai-toolkit's own requirements) |

## 1. Prepare the dataset

`datasets/before/` and `datasets/after/` hold the raw pairs, matched by filename stem.

```bash
cd datasets
pip install -r requirements.txt
python prepare_pairs.py before after --output-dir prepared --test-list test_split.txt
# -> prepared/train/{control,target}  (195 pairs)
#    prepared/test/{control,target}   (10 pairs)
```

## 2. Train (ai-toolkit server)

```bash
# copy the data so the config's relative paths resolve
mkdir -p <ai-toolkit>/datasets/double_chin
cp -r prepared/train prepared/test <ai-toolkit>/datasets/double_chin/
cp models/double_chin_lora_20261001_config.yaml <ai-toolkit>/config/

cd <ai-toolkit>          # must run from here: config paths are relative to it
python run.py config/double_chin_lora_20261001_config.yaml
```

Outputs land in `<ai-toolkit>/output/double_chin_exp_20261001/`:
- `double_chin_exp_20261001_<step>.safetensors` every 100 steps (last 40 kept), plus the final `double_chin_exp_20261001.safetensors`
- `samples/` -- the held-out test previews rendered every 100 steps (validation). Use these to shortlist checkpoints, e.g. where the chin is clean but identity/background haven't started drifting.

To use the ai-toolkit web UI instead, paste the YAML into a new job; the UI rewrites `training_folder` / `sqlite_db_path` itself, but the dataset paths must still exist relative to the ai-toolkit root (or change them to absolute paths).

## 3. Test (ComfyUI server)

Copy the shortlisted checkpoints into ComfyUI's `models/loras/aiDoubleChin/`, start ComfyUI, then:

```bash
cd benchmark
python run_benchmark.py \
    --benchmark-dir ../datasets/prepared/test \
    --checkpoint aiDoubleChin/double_chin_exp_20261001_000003000.safetensors \
                 aiDoubleChin/double_chin_exp_20261001.safetensors \
    --comfyui-port 8188
```

The workflow needs these base models in ComfyUI (`qwen_image_edit_2509_fp8_e4m3fn_scaled`, `qwen_image_vae`, `qwen_2.5_vl_7b_fp8_scaled`, `Qwen-Image-Lightning-4steps-V2.0`).

Results in `output_test_<timestamp>/`:
- `compare/<stem>.jpg` -- input | each checkpoint | ground truth, side by side
- `summary.txt` -- mean/worst metrics per checkpoint:
  - `edit_psnr` / `edit_ssim`: chin region vs ground truth. Compare with `input_edit_psnr` (the do-nothing baseline); a useful checkpoint should beat it.
  - `background_psnr` / `background_ssim`: everything outside the edit; drops mean the LoRA is altering face/background.
- `results.json` -- per-image numbers

## 4. Try any images (ComfyUI server)

```bash
cd benchmark
python run_inference.py \
    --input-dir /path/to/photos \
    --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \
    --comfyui-port 8188
```

Any size/aspect works (`.jpg/.jpeg/.png/.webp/.bmp/.tif`, `.heic/.heif` with `pillow-heif` installed, EXIF orientation applied). Each image is padded to 1024x1024 like the training data, and the result is un-padded and resized back to the input's resolution. Pass several `--checkpoint`s to compare them.

Results in `output_<input-dir-name>_<timestamp>/`:
- `result/<checkpoint>/<stem>.jpg` -- generated image at the original resolution (`square/` holds the raw 1024 output)
- `compare/<stem>.jpg` -- input | each checkpoint, side by side
- `padded/<stem>.jpg` -- the exact input sent to ComfyUI

## 5. Face-crop workflow

`doubleChin_faceCrop_workflow_20261006_api.json` is the face-crop graph (the engine ships it with `AIDoubleChinPasteBack` as the paste-back, i.e. the 20261014 workflow, see 5.1): instead of padding the whole photo to 1024, it detects the face, crops a square around it (box extended downward by `neck_extend_ratio` x face height so the double chin/neck sit inside the crop, not on the paste-back feather), runs the LoRA on that ~1MP crop and pastes it back at full resolution. aiSmile's detail restorer is deliberately left out: in its eye zone it replaces texture to remove wrinkles (meant for generated smile lines), which flattened under-eye lines and crow's feet here while adding nothing elsewhere. It needs the node packs aiSmile uses (ComfyUI_facer, ComfyUI-KJNodes, comfyui-various) plus the two in `custom_nodes/`.

```bash
python run_benchmark.py --benchmark-dir ../datasets/prepared/test \
    --workflow ../workflows/doubleChin_faceCrop_workflow_20261014_api.json \
    --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \
    --face-onnx-dir <ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/ONNX

python run_inference.py --input-dir /path/to/photos --no-pad \
    --workflow ../workflows/doubleChin_faceCrop_workflow_20261014_api.json \
    --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \
    --face-onnx-dir <ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/ONNX
```

`--no-pad` sends the photo at its own size (the workflow crops itself); `--face-onnx-dir` overrides the production model path stored in the workflow.

To inspect every stage (source -> crop -> LoRA result -> pasted back = output) plus diff maps of what each stage changed:

```bash
python run_stages.py --input-dir /path/to/photos \
    --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \
    --face-onnx-dir <ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/ONNX
# optional: --neck-extend-ratio 0.5 to try another crop extension without editing the workflow
```

Results in `output_stages_<input-dir-name>_<timestamp>/<checkpoint>/`: `<stem>/1_source.jpg ... 4_output.jpg` at full resolution (5 stages with a restorer workflow) and `sheets/<stem>.jpg` (layout described at the top of `run_stages.py`).

### 5.1 Keeping the source's resolution (`AIDoubleChinPasteBack`)

The crop is resized to ~1MP, VAE-encoded, sampled in 4 steps and resized back, so with `FaceRegionPasteBack` everything inside it (eyelashes, pores, hair) comes back softer, more so for large inputs where the crop is shrunk 2-3x. `AIDoubleChinPasteBack` (20261014 workflow) has a `mode` input:

| mode | what is pasted |
|---|---|
| `crop` | the whole crop, border-feathered (= `FaceRegionPasteBack`, baseline) |
| `mask` (workflow default, shipped) | only where the LoRA changed the image: below the nose (landmarks from the crop node's `face_metadata`) and where the low-pass result differs from the source by more than `change_threshold`; the rest stays the source pixels |

The edited chin/neck area itself is still the LoRA's upscaled result. Re-adding the source's texture inside it (low frequencies from the LoRA, high frequencies from the source) was tried and dropped: it risks tracing the removed fold back in.

Comparison: one LoRA run per image, both modes pasted from the same result; `sheets/<stem>_modes.jpg` shows 1:1 windows of the upper face and the edit, and `<stem>/4_output_<mode>.jpg` the full-resolution results:

```bash
python run_stages.py --input-dir /path/to/photos \
    --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \
    --face-onnx-dir <ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/ONNX \
    --paste-modes crop mask
```
