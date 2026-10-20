"""Step 5's image edit, shared by every provider.

Every editor runs the same steps: apply the framing size and the user's ratio
choice to a private copy of the analysis, settle one output ratio, fit the crop
plan to it, build the editor prompt, send, and hold the output to that ratio.
A provider differs only in which ratios / sizes it can request
(`resolve_geometry`) and in the request itself (`send`).
"""

import copy
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from image_tools import (
    apply_aspect_ratio_preference,
    apply_framing_size,
    ensure_png_bytes,
    get_image_dimensions,
    output_geometry_warnings,
    resolve_crop_box_with_report,
)
from photo_agent.edit_prompt import assemble_edit_prompt


@dataclass(frozen=True)
class EditGeometry:
    # The output ratio: written into the prompt context and what the returned
    # raster is checked against.
    ratio_label: str
    # The ratio the crop rectangle is fitted to, when that is not ratio_label
    # (expand mode keeps the rectangle's own shape). None means ratio_label.
    crop_ratio_label: Optional[str] = None
    # Whether the output is reached by extending past the source frame.
    expanding: bool = False
    # Provider request parameters (size, imageConfig, ...), passed to `send`.
    request: dict = field(default_factory=dict)


def run_image_edit(
    image_bytes: bytes,
    analysis: dict,
    *,
    preset_key: str,
    revision_feedback: Optional[str],
    aspect_ratio: str,
    single_pass_edit: bool,
    resolve_geometry: Callable[[dict, int, int], EditGeometry],
    send: Callable[[bytes, str, EditGeometry], Tuple[str, dict]],
):
    """Returns (result, prompt) -- the contract of every provider's edit_photo.

    resolve_geometry(framed_analysis, width, height) -> EditGeometry
    send(png_bytes, prompt_text, geometry) -> (image_b64, usage)
    """
    img_w, img_h = get_image_dimensions(image_bytes)

    # Let the selected framing size (close/closer) drive the crop geometry, not
    # just the style-hint text. For closer this rewrites crop_box onto a single
    # subject and clears the other protected regions so the resolver does not
    # re-expand the frame back over everyone. wide/wider/unset pass through.
    size = (analysis.get("style_options") or {}).get("size")
    framed_analysis = apply_framing_size(analysis, size)
    # User's explicit aspect-ratio choice overrides the analysis's own
    # recommendation ("original" only -- "auto" passes through unchanged).
    # The effective output ratio is written into this working copy so crop
    # geometry and prompt text agree with the API request. Do not mutate the
    # shared analysis object: it is reused by QA and by independent RUNs.
    framed_analysis = copy.deepcopy(apply_aspect_ratio_preference(framed_analysis, aspect_ratio))
    # Resolve one effective ratio and reuse it for crop and output canvas.
    geometry = resolve_geometry(framed_analysis, img_w, img_h)
    crop_ratio_label = geometry.crop_ratio_label or geometry.ratio_label
    framed_analysis["recommended_aspect_ratio"] = geometry.ratio_label
    if framed_analysis.get("crop_box"):
        framed_analysis["crop_box"]["aspect_ratio"] = crop_ratio_label
    crop_geometry_ctx = dict(framed_analysis)
    crop_geometry_ctx["recommended_aspect_ratio"] = crop_ratio_label
    crop_plan = resolve_crop_box_with_report(
        crop_geometry_ctx.get("crop_box"), crop_geometry_ctx, img_w, img_h,
    )["cropBox"]

    prompt_text, resolution_metadata, prompt_strategy = assemble_edit_prompt(
        framed_analysis, crop_plan, preset_key=preset_key, aspect_ratio=aspect_ratio,
        source_size=(img_w, img_h), single_pass_edit=single_pass_edit,
        revision_feedback=revision_feedback,
    )

    # Converted once, outside the retry-wrapped call, so a retry doesn't
    # needlessly re-encode the same bytes.
    image_b64, usage = send(ensure_png_bytes(image_bytes), prompt_text, geometry)

    return {
        "image_b64": image_b64,
        "usage": usage,
        "geometry_warnings": output_geometry_warnings(
            image_b64, geometry.ratio_label, crop_plan, img_w, img_h,
            expanding=geometry.expanding,
        ),
        "prompt_resolution": resolution_metadata,
        "prompt_strategy": prompt_strategy,
    }, prompt_text
