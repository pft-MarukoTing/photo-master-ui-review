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

## Available models

Confirmed working as of 2026-09-16 (OpenAI via `client.models.list()` against
the key in `.env`; Gemini via the aipoc gateway, which has no list-models
endpoint of its own, empirically probed one generate call per candidate name).

**OpenAI** (`api_caller/openai_client.py`'s `model=` argument), newest first:

- `gpt-image-2.5-sunburst` -- latest preview tier
- `gpt-image-2.5-flare` -- latest preview tier, alternate variant
- `gpt-image-2` -- current flagship
- `gpt-image-1.5` -- mid tier
- `gpt-image-1` -- **current `api_caller` default**
- `gpt-image-1-mini` -- lightest/cheapest tier

**Gemini** (`api_caller/gemini_client.py`'s `model=` argument), via the aipoc
gateway's `gmnImgEdit` effect, newest first:

- `gemini-3.1-flash-image-preview` -- **current `api_caller` default**
- `gemini-3.1-flash-image` -- "Nano Banana 2"
- `gemini-3.1-flash-lite-image` -- "Nano Banana 2 Lite", lighter tier
- `gemini-2.5-flash-image` -- previous generation ("Nano Banana")
- `gemini-2.5-flash-image-preview` -- previous generation, preview variant

Override per call with `tools/cli.py --gemini-model ...` / `--openai-model
...`, or pass `model=` directly to `generate_image`/`edit_image`/`judge_images`.

### Judge models

`judge_images`/`judge_images_structured` take a text/vision model, not an
image-generation one -- a different `model=` argument than the table above.

**OpenAI** (`openai_client.judge_images_structured`'s `model=`), via the
Responses API, vision-capable and confirmed present on this key:

- `gpt-5.5` / `gpt-5.4` / `gpt-5.2` -- latest flagship tier
- `gpt-5.4-mini` / `gpt-5-mini` -- cheaper mid tier
- `o3-pro` / `o3` / `o1-pro` -- reasoning models (slower, more scrutiny)
- `gpt-4.1` -- **current `api_caller` default**
- `gpt-4o` -- alternate mid tier
- `gpt-4.1-mini` / `gpt-4o-mini` / `gpt-5-nano` -- lightest/cheapest tier

**Gemini** (`gemini_client.judge_images`'s `model=`): must be one of the same
five image-capable models listed above, not a text-only Gemini model --
`judge_images` works by asking an image-generation model to answer in text
instead (see the module docstring), so it needs a model that effect actually
accepts. `gemini-3.1-flash-image-preview` is the current default.

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
- `tools/cli.py` -- generate or edit one image from a prompt via either
  provider.
- `tasks/AINail/` -- the nail-transfer pipeline and tooling built on top of
  `api_caller/`.

## Usage

```bash
# ad-hoc single-step tool
python tools/cli.py --provider both --prompt "a red bicycle on a beach" --out-dir ./out
python tools/cli.py --mode edit --provider gemini --image in.png --prompt "..." --out-dir ./out
```

Run everything from the `APICaller/` directory.
