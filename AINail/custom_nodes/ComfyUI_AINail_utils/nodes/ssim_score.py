import numpy as np
from skimage.metrics import structural_similarity

from .image_ops import resize_via_pil

# Downsizing to this max side (with win_size scaled down to match) was
# benchmarked against 4 real crop/generated pairs spanning a true match, a
# clearly wrong match, and two "should pass" pairs (including one right at
# the ssim_threshold boundary, 0.789). At max_side=256 with the default
# win_size=7, the boundary case flipped from pass to fail (0.789 -> 0.756).
# Scaling win_size down to 3 alongside the resize brought every case back
# within ~0.005 of its full-resolution score (pass/fail unchanged in all 4),
# while running ~40x faster with no extra cost from the smaller win_size.
# These two constants are coupled — don't change one without re-validating
# the other against real borderline examples.
_SSIM_MAX_SIDE = 256
_SSIM_WIN_SIZE = 3


def _resize_to(np_img, target_h, target_w):
    """Lanczos-resize a [H, W, C] float32 image in [0, 1] to an exact size."""
    if np_img.shape[:2] == (target_h, target_w):
        return np_img
    return resize_via_pil(np.clip(np_img, 0, 1), target_h, target_w)


def _safe_win_size(win_size, h, w):
    """Clamp win_size to a valid odd value that fits within (h, w)."""
    max_win = min(h, w)
    if max_win >= win_size:
        return win_size
    return max_win if max_win % 2 == 1 else max(1, max_win - 1)


class SSIMScore:
    """Compute whole-image SSIM between each crop_images[i] and generated_images[i].

    Inputs (collected via INPUT_IS_LIST):
      crop_images      : [N] one crop (post crop+resize) per hand
      generated_images : [N] one generated image per hand
      ssim_threshold    : minimum acceptable SSIM per pair

    If a pair's spatial size differs, generated_images[i] is resized to
    crop_images[i]'s size before comparison. Both frames are then downsized
    to `_SSIM_MAX_SIDE` (with `_SSIM_WIN_SIZE` accordingly) before computing
    SSIM, for a ~40x speedup validated to not change the pass/fail outcome
    (see the comment above those constants).

    Outputs:
      ssim_scores : [N] one SSIM float per hand, same order as the inputs.
      images      : [N] crop_images if any ssim_scores[i] < ssim_threshold
                    (generation judged bad somewhere), otherwise generated_images
                    (every pair passed the threshold).
      status      : "Success" if every pair's score >= ssim_threshold, else "Internal Error".
                    OUTPUT_NODE=True so this always lands in /history's outputs for this
                    node_id (keyed "text"), whether or not `status` is wired downstream —
                    same mechanism SaveImage uses for its "images" entry.
    """

    INPUT_IS_LIST = True
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "crop_images": ("IMAGE",),
                "generated_images": ("IMAGE",),
                "ssim_threshold": ("FLOAT", {"default": 0.76, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("FLOAT", "IMAGE", "STRING")
    RETURN_NAMES = ("ssim_scores", "images", "status")
    OUTPUT_IS_LIST = (True, True, False)
    FUNCTION = "compute"
    CATEGORY = "AINail/image"

    def compute(self, crop_images, generated_images, ssim_threshold):
        threshold = ssim_threshold[0]

        scores = []
        for crop, gen in zip(crop_images, generated_images):
            crop_np = crop.clamp(0, 1).cpu().numpy().astype(np.float32)   # [B, H, W, C]
            gen_np  = gen.clamp(0, 1).cpu().numpy().astype(np.float32)    # [B, H, W, C]

            n = min(crop_np.shape[0], gen_np.shape[0])
            frame_scores = []
            for k in range(n):
                c_frame = crop_np[k]
                g_frame = gen_np[k]
                if g_frame.shape[:2] != c_frame.shape[:2]:
                    g_frame = _resize_to(g_frame, *c_frame.shape[:2])

                h, w = c_frame.shape[:2]
                scale = _SSIM_MAX_SIDE / max(h, w)
                if scale < 1.0:
                    new_h = max(1, int(round(h * scale)))
                    new_w = max(1, int(round(w * scale)))
                    c_frame = _resize_to(c_frame, new_h, new_w)
                    g_frame = _resize_to(g_frame, new_h, new_w)
                    h, w = new_h, new_w

                score = structural_similarity(
                    c_frame, g_frame, channel_axis=-1, data_range=1.0,
                    win_size=_safe_win_size(_SSIM_WIN_SIZE, h, w),
                )
                frame_scores.append(float(score))

            scores.append(sum(frame_scores) / len(frame_scores))

        if all(s >= threshold for s in scores):
            status = "Success"
            images = generated_images
        else:
            status = "Internal Error"
            images = crop_images

        return {"ui": {"text": [status]}, "result": (scores, images, status)}


NODE_CLASS_MAPPINGS = {
    "AINailSSIMScore": SSIMScore,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailSSIMScore": "SSIM Score (AINail)",
}
