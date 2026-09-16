"""Generic text-to-image generation via the OpenAI images API."""

from __future__ import annotations

import base64
import mimetypes
import os

DEFAULT_MODEL = "gpt-image-1"
DEFAULT_SIZE = "1024x1024"
DEFAULT_JUDGE_MODEL = "gpt-5.5"


def generate_image(
    prompt: str,
    out_path: str,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    api_key: str | None = None,
) -> str:
    """Generate one image from a text prompt and save it to out_path.

    api_key defaults to the OPENAI_API_KEY environment variable.
    """
    from openai import OpenAI

    api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)
    result = client.images.generate(model=model, prompt=prompt, size=size, n=1)
    b64 = result.data[0].b64_json

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))
    return out_path


def edit_image(
    image_path: str,
    prompt: str,
    out_path: str,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    api_key: str | None = None,
) -> str:
    """Edit an existing image with a text prompt and save the result to out_path.

    api_key defaults to the OPENAI_API_KEY environment variable.
    """
    from openai import OpenAI

    api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)
    with open(image_path, "rb") as f:
        result = client.images.edit(model=model, image=f, prompt=prompt, size=size, n=1)
    b64 = result.data[0].b64_json

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))
    return out_path


def _image_data_url(image_path: str) -> str:
    content_type = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{content_type};base64,{b64}"


def _responses_create(image_paths: list[str], prompt: str, model: str, api_key: str | None, text_format: dict | None):
    from openai import OpenAI

    api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)
    content = [{"type": "input_text", "text": prompt}]
    for path in image_paths:
        content.append({"type": "input_image", "image_url": _image_data_url(path)})

    kwargs = {"model": model, "input": [{"role": "user", "content": content}]}
    if text_format:
        kwargs["text"] = {"format": text_format}
    return client.responses.create(**kwargs)


def judge_images(
    image_paths: list[str],
    prompt: str,
    model: str = DEFAULT_JUDGE_MODEL,
    api_key: str | None = None,
) -> str:
    """Send several images plus a text prompt and get back a free-text answer.

    Images are attached in order (e.g. refer to them in the prompt as
    "IMAGE 1 = ..."). api_key defaults to the OPENAI_API_KEY environment
    variable.
    """
    result = _responses_create(image_paths, prompt, model, api_key, text_format=None)
    return result.output_text


def judge_images_structured(
    image_paths: list[str],
    prompt: str,
    schema: dict,
    model: str = DEFAULT_JUDGE_MODEL,
    api_key: str | None = None,
) -> dict:
    """Same as judge_images, but constrains the answer to a JSON schema
    (Responses API strict structured output) and returns the parsed dict.
    """
    import json

    result = _responses_create(image_paths, prompt, model, api_key, text_format=schema)
    return json.loads(result.output_text)
