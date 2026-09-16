# APICaller

Generic text-to-image / image-edit / vision-judge calls via Gemini and
OpenAI, plus a ComfyUI nail-transfer pipeline built on top of them.
Provider-specific plumbing lives in `api_caller/`; nothing else hardcodes a
prompt or a provider call.

## Setup

Self-contained: its own conda env, its own `.env`.

```bash
bash setup.sh   # one-time, creates the `apicaller` conda env
conda activate apicaller
cp .env.example .env   # then fill in your own keys
```

## Credentials

Read by default from `./.env` (override with `--env-file`), git-ignored:

- `OPENAI_API_KEY` -- OpenAI image generation/edit/judge.
- `AIPOC_REFRESH_TOKEN` -- internal aipoc gateway, which fronts Gemini.

## Layout

- `api_caller/` -- the actual library. Every function here is a plain,
  reusable call (not CLI-only):
  - `gemini_client.py` -- `generate_image`, `edit_image`, `judge_images`, all
    via the aipoc gateway's `gmnImgEdit` effect (generation = no source
    image uploaded; judging = asking a naturally image-only model to answer
    in text, which the gateway reports as an error carrying that text --
    see the module docstring).
  - `openai_client.py` -- `generate_image`/`edit_image` (images API),
    `judge_images`/`judge_images_structured` (Responses API, the latter with
    a JSON-schema-constrained answer).
  - `comfy_client.py` -- synchronous ComfyUI HTTP client (upload/submit/poll),
    copied from `AINail/benchmark/comfy_client.py`.
- `pipeline.py` -- the main entry point. Generates two hand photos, runs them
  through ComfyUI's nail-transfer workflow, judges the result with both
  providers, and appends one record to `pipeline_log.jsonl`.
- `tools/` -- single-step ad-hoc scripts for testing one piece of the
  pipeline in isolation (not imported by `pipeline.py`, except
  `comfyui_run.py` which `pipeline.py` reuses for its ComfyUI step):
  - `cli.py` -- generate or edit one image from a prompt.
  - `comfyui_run.py` -- run one (source, reference) pair through ComfyUI.
  - `judge.py` -- free-text judging of an arbitrary set of images.
- `nail_prompt.txt` -- the default hand/nail-art generation prompt `pipeline.py`
  and `tools/cli.py` read when no `--prompt`/`--prompt-file` is given.

## Usage

```bash
# main pipeline: generate x2 -> ComfyUI nail transfer -> judge x2 -> log
python pipeline.py --provider gemini \
    --checkpoint nail_salon_lora/nail_salon_lora_20260826.safetensors \
    --comfyui-port 8450

# ad-hoc single-step tools
python tools/cli.py --provider both --prompt "a red bicycle on a beach" --out-dir ./out
python tools/cli.py --mode edit --provider gemini --image in.png --prompt "..." --out-dir ./out
python tools/comfyui_run.py --source a.png --reference b.png --checkpoint ... --comfyui-port 8450
python tools/judge.py --provider both --images source.png reference.png result.jpg
```

Run everything from the `APICaller/` directory (the `tools/` scripts add
their parent dir to `sys.path` so `api_caller` still resolves).
