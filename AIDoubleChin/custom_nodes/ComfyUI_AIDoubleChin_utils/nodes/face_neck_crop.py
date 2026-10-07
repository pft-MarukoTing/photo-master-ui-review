"""
AIDoubleChinFaceRegionCrop -- FaceRegionCrop (ComfyUI-FaceRestorer) with the
crop's must-cover box extended below the chin, so the double chin and upper
neck are inside the edited crop instead of on its feathered border.

Face detectors box the face from forehead to chin; aiSmile's padding_factor
pads that box equally on all sides, which leaves the area under the jaw cut
off or inside the paste-back feather. Here the selected face box gets its
height multiplied by (1 + neck_extend_ratio) downward before the square crop
is computed. Face landmarks/segmentation (FACE_METADATA, consumed by
FaceDetailRestorerwMetadata) are still computed from the ORIGINAL face box, so
the detail restorer's alignment is unaffected by the extension.

Reuses FaceDetailRestorer.get_all_faces_crop_region unchanged; ComfyUI-FaceRestorer
must be installed (it provides the FACE_PIPELINE input anyway).
"""

import importlib

import cv2
import numpy as np
import torch

# get_all_faces_crop_region step 1.5 swaps the provided rect for the best
# all_bboxs match at IoU >= 0.3; mirrored here to find the box to extend.
_MATCH_IOU = 0.3


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    iw = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    ih = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _restorer_class(pipeline):
    """FaceDetailRestorer from whichever ComfyUI-FaceRestorer copy built `pipeline`.

    ComfyUI imports custom node folders under their directory name, which has
    a hyphen and may differ per deployment, so the package is resolved from the
    pipeline object's own module at run time instead of a hard-coded import.
    """
    module = type(pipeline).__module__  # "<package>.core.face_model_pipe"
    package = module.rsplit(".core.", 1)[0]
    return importlib.import_module(f"{package}.core.post_blend").FaceDetailRestorer


def extend_face_rect(rect, img_h, neck_extend_ratio):
    """[x, y, w, h] with h grown downward by neck_extend_ratio, clamped to the image."""
    x, y, w, h = [int(v) for v in rect]
    new_h = int(round(h * (1.0 + neck_extend_ratio)))
    new_h = max(h, min(new_h, img_h - y))
    return [x, y, w, new_h]


def neck_crop(restorer, img_bgr, face_rect, all_bboxs, padding_factor, neck_extend_ratio,
              bbox_padding_mode="pad"):
    """Returns (crop_bgr, crop_info, face_metadata, face_rect, extended_rect)."""
    h_img = img_bgr.shape[0]
    face_rect = [int(v) for v in face_rect]

    # Same substitution get_all_faces_crop_region would do, done here so the
    # box we extend is the one it would actually have used.
    target_idx = None
    if all_bboxs:
        scores = [_iou(face_rect, (b["x"], b["y"], b["w"], b["h"])) for b in all_bboxs]
        best = int(np.argmax(scores))
        if scores[best] >= _MATCH_IOU:
            target_idx = best
            b = all_bboxs[best]
            face_rect = [b["x"], b["y"], b["w"], b["h"]]

    ext = extend_face_rect(face_rect, h_img, neck_extend_ratio)

    # Put the extended box into all_bboxs too: otherwise step 1.5 would match
    # it back to the unextended detector box (IoU ~0.6-0.8) and drop the neck,
    # and the neighbour-avoidance would treat the target as a neighbour.
    ext_bboxs = None
    if all_bboxs:
        ext_bboxs = [dict(b) for b in all_bboxs]
        if target_idx is not None:
            ext_bboxs[target_idx] = {"x": ext[0], "y": ext[1], "w": ext[2], "h": ext[3]}

    crop_bgr, crop_info, ext_meta = restorer.get_all_faces_crop_region(
        img_bgr, padding_factor, bbox_padding_mode=bbox_padding_mode, alignment_reinfine=False,
        face_rect_metadata={"face_rect_flag": True, "face_rect": ext}, all_bboxs=ext_bboxs)

    # Landmarks/segmentation from the real face box (full-image coordinates,
    # which is what FaceDetailRestorerwMetadata expects).
    meta = restorer.analyze_face(img_bgr, rect=face_rect)
    for key in ("aligned_rects", "face_rect_local", "face_rect_flag"):
        if key in ext_meta:
            meta[key] = ext_meta[key]
    return crop_bgr, crop_info, meta, face_rect, ext


class AIDoubleChinFaceRegionCrop:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pipeline": ("FACE_PIPELINE",),
                "image": ("IMAGE",),
                "face_rect_metadata": ("FACE_RECT_METADATA",),
                "padding_factor": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 1.0, "step": 0.05}),
                "neck_extend_ratio": ("FLOAT", {"default": 0.35, "min": 0.0, "max": 2.0, "step": 0.05,
                                                "tooltip": "extra height below the face box, as a fraction of its height"}),
                "bbox_padding_mode": (["shift", "pad"], {"default": "pad"}),
            },
            "optional": {
                "all_bboxs": ("FACE_BBOXS_XYWH",),
            },
        }

    RETURN_TYPES = ("IMAGE", "CROP_INFO", "FACE_METADATA")
    RETURN_NAMES = ("crop_image", "crop_info", "face_metadata")
    FUNCTION = "crop"
    CATEGORY = "AIDoubleChin"

    def crop(self, pipeline, image, face_rect_metadata, padding_factor, neck_extend_ratio,
             bbox_padding_mode, all_bboxs=None):
        if not face_rect_metadata.get("face_rect_flag", False):
            # FaceRectMetadataSwitch already raises ERROR_NO_FACE before this point.
            raise Exception("ERROR_NO_FACE")
        restorer = _restorer_class(pipeline)(pipeline)

        crops, infos, metas = [], [], []
        for i in range(image.shape[0]):
            img_bgr = cv2.cvtColor((image[i].cpu().numpy() * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
            crop_bgr, crop_info, meta, face_rect, ext = neck_crop(
                restorer, img_bgr, face_rect_metadata["face_rect"], all_bboxs,
                padding_factor, neck_extend_ratio, bbox_padding_mode)
            print(f"[AIDoubleChinFaceRegionCrop] face={face_rect} extended={ext} crop={crop_info}")
            crops.append(torch.from_numpy(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0))
            infos.append(crop_info)
            metas.append(meta)
        # Single-image batches in practice; resize to the first if a batch differs.
        h, w = crops[0].shape[:2]
        crops = [c if c.shape[:2] == (h, w) else torch.from_numpy(cv2.resize(c.numpy(), (w, h))) for c in crops]
        return (torch.stack(crops), infos, metas)


NODE_CLASS_MAPPINGS = {"AIDoubleChinFaceRegionCrop": AIDoubleChinFaceRegionCrop}
NODE_DISPLAY_NAME_MAPPINGS = {"AIDoubleChinFaceRegionCrop": "AIDoubleChin Face Region Crop (with neck)"}
