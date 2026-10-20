"""Microsoft Foundry MAI image-edit client used by PhotoAgent Step 5."""

import os

import requests

from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from photo_agent.providers.http import checked_json, retry_transient
from photo_agent.providers.image_edit import EditGeometry, run_image_edit


MAI_IMAGE_26_MODEL = "MAI-Image-2.6"
DEFAULT_MAI_GENERATIONS_URL = (
    "https://pf-app-api-dev.services.ai.azure.com"
    "/mai/v1/images/generations"
)


def _azure_api_key() -> str:
    key = os.getenv("AZURE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Environment variable AZURE_API_KEY is not set",
            RESULT_UNKNOWN_INTERNAL_ERROR,
        )
    return key


def _mai_edits_url() -> str:
    """Resolve a base, generations, or edits endpoint to the edits endpoint."""
    endpoint = (
        os.getenv("AZURE_MAI_ENDPOINT", "").strip().strip('"').strip("'")
        or DEFAULT_MAI_GENERATIONS_URL
    ).rstrip("/")
    generations_suffix = "/mai/v1/images/generations"
    edits_suffix = "/mai/v1/images/edits"
    if endpoint.endswith(generations_suffix):
        return endpoint[: -len(generations_suffix)] + edits_suffix
    if endpoint.endswith(edits_suffix):
        return endpoint
    return endpoint + edits_suffix


def _deployment_name(model: str) -> str:
    """Azure expects the deployment name, which may differ from the UI ID."""
    return os.getenv("AZURE_MAI_DEPLOYMENT", "").strip() or model


@retry_transient
def _post_image_edit(
    deployment: str,
    prompt_text: str,
    png_bytes: bytes,
    *,
    auto_aspect_ratio: bool,
) -> dict:
    response = requests.post(
        _mai_edits_url(),
        headers={"api-key": _azure_api_key()},
        files={"image": ("photo.png", png_bytes, "image/png")},
        data={
            "model": deployment,
            "prompt": prompt_text,
            "auto_aspect_ratio": "true" if auto_aspect_ratio else "false",
            "web_grounding": "false",
        },
        timeout=300,
    )
    return checked_json(response, "Image edit failed (MAI)")


def _effective_ratio_label(analysis: dict, image_width: int, image_height: int) -> str:
    label = str(
        analysis.get("recommended_aspect_ratio")
        or (analysis.get("crop_box") or {}).get("aspect_ratio")
        or "original"
    ).strip()
    return f"{image_width}:{image_height}" if label == "original" else label


def edit_photo(
    image_bytes: bytes,
    analysis: dict,
    quality: str = "medium",
    preset_key: str = "natural",
    revision_feedback: str = None,
    model: str = MAI_IMAGE_26_MODEL,
    aspect_ratio: str = "auto",
    single_pass_edit: bool = False,
):
    """Edit one source image through Azure MAI, matching other clients' result contract.

    MAI's edits endpoint does not accept the UI quality tiers, so ``quality``
    is accepted only to preserve PhotoAgent's shared editor-call signature.
    """
    del quality

    def resolve_geometry(framed_analysis, image_width, image_height):
        return EditGeometry(ratio_label=_effective_ratio_label(framed_analysis, image_width, image_height))

    def send(png_bytes, prompt_text, geometry):
        data = _post_image_edit(
            _deployment_name(model),
            prompt_text,
            png_bytes,
            # The edits API has no width/height fields. For Auto and named
            # ratios, let MAI select from the explicit framing/ratio
            # instruction in the prompt. Disable this only for "original",
            # where the input canvas is the authoritative aspect ratio.
            auto_aspect_ratio=aspect_ratio != "original",
        )
        image_b64 = (data.get("data") or [{}])[0].get("b64_json")
        if not image_b64:
            raise RuntimeError("Azure MAI response did not contain image data.")
        return image_b64, {"model": model, "usage": data.get("usage")}

    return run_image_edit(
        image_bytes, analysis, preset_key=preset_key, revision_feedback=revision_feedback,
        aspect_ratio=aspect_ratio, single_pass_edit=single_pass_edit,
        resolve_geometry=resolve_geometry, send=send,
    )
