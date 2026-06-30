"""
SAM3 model cache for subprocess worker.

aiNail vendor patch: switched from build_sam3_video_model (Pozzetti default,
returns 0 detections for our hand images) to build_sam3_image_model.

Key insight: Pozzetti's build_sam3_image_model already calls its own
_load_checkpoint which handles:
  - detector.* -> * (strip prefix)
  - tracker.* -> inst_interactive_predictor.model.*
  - nn.MultiheadAttention in_proj_weight/bias -> split q/k/v
So we just pass checkpoint_path and let it load -- no manual meta-device
construction or remap needed (earlier attempt did meta+manual load_state_dict
which bypassed Pozzetti's loader and left 675 keys missing -> crash on
device transfer).
"""
import logging
import torch

log = logging.getLogger("sam3")

_cache = {"config_hash": None, "model": None}


class _ImagePredictorShim:
    """Adapter: SAM3UnifiedModel.__init__ extracts `.model` from its
    video_predictor argument. Image model has no predictor wrapper, so we
    expose it via a thin shim. ModelPatcher delegation in SAM3UnifiedModel
    falls through __getattr__ to this shim -- but no caller in segmentation.py
    invokes video-only methods (start_session / close_session / etc), so it
    never matters."""
    def __init__(self, model):
        self.model = model


def get_or_build_model(config):
    """Get cached model or build from config dict.

    Args:
        config: dict with keys: precision, compile, checkpoint_path, bpe_path

    Returns:
        SAM3UnifiedModel (ModelPatcher subclass)
    """
    config_hash = hash(frozenset(config.items()))
    if _cache["config_hash"] == config_hash and _cache["model"] is not None:
        log.info("SAM3 model cache hit")
        return _cache["model"]

    log.info("SAM3 model cache miss -- building IMAGE model (aiNail patch)...")

    from .sam3_model_patcher import SAM3UnifiedModel
    from .sam3.utils import Sam3Processor
    from .sam3 import build_sam3_image_model
    import comfy.model_management

    load_device = comfy.model_management.get_torch_device()
    offload_device = comfy.model_management.unet_offload_device()

    checkpoint_path = config["checkpoint_path"]
    bpe_path = config["bpe_path"]

    # Resolve dtype
    dtype_str = config["dtype"]
    dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[dtype_str]

    log.info(f"Loading image model from: {checkpoint_path}")

    # Direct build + load. build_sam3_image_model -> _load_checkpoint handles
    # detector.*/tracker.* remap + MHA q/k/v split internally.
    # Model goes to CPU; ModelPatcher will push to GPU on first inference.
    model = build_sam3_image_model(
        bpe_path=bpe_path,
        device="cpu",
        eval_mode=True,
        checkpoint_path=str(checkpoint_path),
        load_from_HF=False,
        enable_inst_interactivity=True,
    )

    # Set attention dtype to match the weights we are about to cast.
    from .sam3.attention import set_sam3_dtype
    set_sam3_dtype(dtype if dtype != torch.float32 else None)

    # Selective weight casting: backbone + inst_interactive_predictor.
    # Image model exposes .backbone and .inst_interactive_predictor directly
    # (vs video model's nested .detector.backbone / .detector.inst_interactive_predictor).
    if dtype != torch.float32:
        for param in model.backbone.parameters():
            param.data = param.data.to(dtype=dtype)
        if getattr(model, "inst_interactive_predictor", None) is not None:
            for param in model.inst_interactive_predictor.parameters():
                param.data = param.data.to(dtype=dtype)

    # Processor wraps the image model directly.
    processor = Sam3Processor(
        model=model,
        resolution=1008,
        device=str(load_device),
        confidence_threshold=0.2,
    )

    # SAM3UnifiedModel expects a video_predictor (with .model attribute) -- we
    # wrap the image model in a shim so ModelPatcher integration still works.
    predictor_shim = _ImagePredictorShim(model)

    unified_model = SAM3UnifiedModel(
        video_predictor=predictor_shim,
        processor=processor,
        load_device=load_device,
        offload_device=offload_device,
        dtype=dtype,
    )

    log.info(f"Image model ready ({unified_model.model_size() / 1024 / 1024:.1f} MB)")

    _cache["config_hash"] = config_hash
    _cache["model"] = unified_model
    return unified_model
