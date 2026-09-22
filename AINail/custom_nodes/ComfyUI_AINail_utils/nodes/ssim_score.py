import numpy as np
from skimage.metrics import structural_similarity
from skimage.transform import resize as sk_resize


class SSIMScore:
    """Compute whole-image SSIM between each crop_images[i] and generated_images[i].

    Inputs (collected via INPUT_IS_LIST):
      crop_images      : [N] one crop (post crop+resize) per hand
      generated_images : [N] one generated image per hand
      ssim_threshold    : minimum acceptable SSIM per pair

    If a pair's spatial size differs, generated_images[i] is resized to
    crop_images[i]'s size before comparison.

    Outputs:
      ssim_scores : [N] one SSIM float per hand, same order as the inputs.
      images      : [N] crop_images if any ssim_scores[i] < ssim_threshold
                    (generation judged bad somewhere), otherwise generated_images
                    (every pair passed the threshold).
    """

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "crop_images": ("IMAGE",),
                "generated_images": ("IMAGE",),
                "ssim_threshold": ("FLOAT", {"default": 0.76, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("FLOAT", "IMAGE")
    RETURN_NAMES = ("ssim_scores", "images")
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "compute"
    CATEGORY = "AINail/image"

    def compute(self, crop_images, generated_images, ssim_threshold):
        threshold = ssim_threshold[0]

        scores = []
        for crop, gen in zip(crop_images, generated_images):
            crop_np = crop.clamp(0, 1).cpu().numpy().astype(np.float64)   # [B, H, W, C]
            gen_np  = gen.clamp(0, 1).cpu().numpy().astype(np.float64)    # [B, H, W, C]

            n = min(crop_np.shape[0], gen_np.shape[0])
            frame_scores = []
            for k in range(n):
                c_frame = crop_np[k]
                g_frame = gen_np[k]
                if g_frame.shape[:2] != c_frame.shape[:2]:
                    g_frame = sk_resize(
                        g_frame, c_frame.shape[:2], anti_aliasing=True,
                        preserve_range=True,
                    )
                score = structural_similarity(
                    c_frame, g_frame, channel_axis=-1, data_range=1.0,
                )
                frame_scores.append(float(score))

            scores.append(sum(frame_scores) / len(frame_scores))

        images = crop_images if any(s < threshold for s in scores) else generated_images
        return (scores, images)


NODE_CLASS_MAPPINGS = {
    "AINailSSIMScore": SSIMScore,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailSSIMScore": "SSIM Score (AINail)",
}
