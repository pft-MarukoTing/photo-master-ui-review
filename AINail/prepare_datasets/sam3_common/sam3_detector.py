"""
SAM3 text-prompted grounding detection, using the official facebookresearch/sam3 package.
Not specific to any one object class -- text_prompt selects what to detect (e.g. "hand", "nail").
"""
import argparse

import torch
from PIL import Image


def load_model(checkpoint_path: str, bpe_path: str = None, device: str = None):
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    kwargs = dict(
        checkpoint_path=checkpoint_path,
        load_from_HF=False,
        device=device,
        eval_mode=True,
        enable_inst_interactivity=False,
    )
    if bpe_path:
        kwargs["bpe_path"] = bpe_path

    model = build_sam3_image_model(**kwargs)
    # Sam3Processor always defaults device="cuda" regardless of what build_sam3_image_model
    # was given -- keep them in sync explicitly.
    processor = Sam3Processor(model, device=device)
    return model, processor


def detect(processor, pil_image: Image.Image, confidence_threshold: float = 0.2,
           text_prompt: str = "hand", max_detections: int = 2, return_scores: bool = False,
           return_masks: bool = False):
    """Returns a list of [x1, y1, x2, y2] pixel-coordinate boxes, sorted by score
    descending and truncated to max_detections (max_detections <= 0 means no limit).
    If return_scores=True, scores (list[float]) are appended to the return tuple. If
    return_masks=True, masks (list[np.ndarray], each (H, W) bool, same order as boxes) are
    appended after that -- i.e. the return shape is one of: boxes / (boxes, scores) /
    (boxes, masks) / (boxes, scores, masks)."""
    # sam3/perflib/fused.py's addmm_act (used by the ViT MLP blocks) always internally
    # casts its operands to bfloat16 to run a fused kernel, regardless of the model's
    # stored dtype -- this is only self-consistent when the whole forward pass runs
    # under bf16 autocast (the standard way FAIR ships these models), so the rest of the
    # model's fp32 layers get promoted to match instead of hard dtype-mismatching.
    device_type = "cuda" if str(processor.device).startswith("cuda") else "cpu"
    with torch.autocast(device_type=device_type, dtype=torch.bfloat16):
        state = processor.set_image(pil_image)
        processor.set_confidence_threshold(confidence_threshold, state)
        state = processor.set_text_prompt(prompt=text_prompt, state=state)

    boxes = state.get("boxes")
    scores = state.get("scores")
    masks = state.get("masks")

    def _empty():
        result = [[]]
        if return_scores:
            result.append([])
        if return_masks:
            result.append([])
        return tuple(result) if len(result) > 1 else result[0]

    if boxes is None or len(boxes) == 0:
        return _empty()

    # The official Sam3Processor._forward_grounding (unlike the ComfyUI-vendored copy's)
    # skips mask-based NMS, so heavily-overlapping duplicate detections (e.g. a low-score
    # "whole scene" box overlapping a high-score hand box) aren't suppressed. Replicate the
    # vendored copy's post-threshold NMS step (iou_threshold=0.5) here to match its output.
    if masks is not None and len(masks) > 1:
        from sam3.perflib.nms import nms_masks
        keep = nms_masks(pred_probs=scores, pred_masks=masks.squeeze(1).float(),
                          prob_threshold=0.0, iou_threshold=0.5)
        boxes = boxes[keep]
        scores = scores[keep]
        masks = masks[keep]

    if scores is not None and len(scores) > 0:
        sorted_indices = torch.argsort(scores, descending=True)
        boxes = boxes[sorted_indices]
        scores = scores[sorted_indices]
        if masks is not None:
            masks = masks[sorted_indices]

    if max_detections > 0 and len(boxes) > max_detections:
        boxes = boxes[:max_detections]
        if scores is not None:
            scores = scores[:max_detections]
        if masks is not None:
            masks = masks[:max_detections]

    boxes = boxes.float().cpu().tolist()
    result = [boxes]
    if return_scores:
        result.append(scores.float().cpu().tolist() if scores is not None else [])
    if return_masks:
        result.append([m.cpu().numpy() for m in masks.squeeze(1)] if masks is not None else [])
    return tuple(result) if len(result) > 1 else result[0]


def main():
    parser = argparse.ArgumentParser(description="Smoke test: SAM3 grounding detection")
    parser.add_argument("--image", required=True, help="Path to an image file")
    parser.add_argument("--checkpoint", required=True, help="Path to sam3.pt (user's ln -s target)")
    parser.add_argument("--bpe-path", default=None, help="Path to bpe_simple_vocab_16e6.txt.gz (optional)")
    parser.add_argument("--text-prompt", default="hand")
    parser.add_argument("--confidence-threshold", type=float, default=0.2)
    parser.add_argument("--max-detections", type=int, default=2)
    args = parser.parse_args()

    print("Loading SAM3 model...")
    _, processor = load_model(args.checkpoint, bpe_path=args.bpe_path)

    pil_image = Image.open(args.image).convert("RGB")
    print(f"Image size: {pil_image.size}")

    boxes, scores = detect(
        processor, pil_image,
        confidence_threshold=args.confidence_threshold,
        text_prompt=args.text_prompt,
        max_detections=args.max_detections,
        return_scores=True,
    )
    print(f"Detected {len(boxes)} box(es):")
    for box, score in zip(boxes, scores):
        print(f"  score={score:.4f}  box={box}")


if __name__ == "__main__":
    main()
