import numpy as np
from skimage.metrics import structural_similarity
from skimage.transform import resize as sk_resize


class SSIMScore:
    """Compute whole-image SSIM between each crop_images[i] and generated_images[i].

    Inputs (collected via INPUT_IS_LIST):
      crop_images     : [N] one crop (post crop+resize) per hand
      generated_images : [N] one generated image per hand

    If a pair's spatial size differs, generated_images[i] is resized to
    crop_images[i]'s size before comparison.

    Output:
      ssim_scores : [N] one SSIM float per hand, same order as the inputs.
    """

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "crop_images": ("IMAGE",),
                "generated_images": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("FLOAT",)
    RETURN_NAMES = ("ssim_scores",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "compute"
    CATEGORY = "AINail/image"

    def compute(self, crop_images, generated_images):
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

        return (scores,)


NODE_CLASS_MAPPINGS = {
    "AINailSSIMScore": SSIMScore,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailSSIMScore": "SSIM Score (AINail)",
}
