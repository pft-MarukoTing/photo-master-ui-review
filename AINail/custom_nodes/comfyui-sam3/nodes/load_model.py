"""
LoadSAM3Model node - Returns model config for subprocess-based loading.

Actual model construction happens inside consumer nodes (in the isolation
env subprocess), following the Trellis2 pattern.  This node resolves
precision, downloads the checkpoint if needed, and returns a JSON-safe
config dict.
"""
import logging
from pathlib import Path

log = logging.getLogger("sam3")

import os
import torch
import folder_paths
from folder_paths import base_path as comfy_base_path
from comfy_api.latest import io

# Register model folder with ComfyUI's folder_paths system
_sam3_models_dir = os.path.join(folder_paths.models_dir, "sam3")
os.makedirs(_sam3_models_dir, exist_ok=True)
folder_paths.add_model_folder_path("sam3", _sam3_models_dir)

class LoadSAM3Model(io.ComfyNode):
    """
    Node to load SAM3 model configuration.

    aiNail vendor patch: model file is pre-pulled by pull_models.sh into
    one of ComfyUI's registered 'sam3' folders (typically
    /workspace/models/comfy/sam3/ via SRCtrl extra_model_paths.yaml).
    Upstream HuggingFace auto-download removed -- POC server is offline.

    Prefers sam3.pt (FAIR image-model checkpoint, matches SRCtrl SAM3ImageModel
    which we verified detects hands at score 0.85+). Falls back to .safetensors
    if .pt isn't present (kept for compatibility -- but note the original
    Pozzetti .safetensors is a VIDEO model checkpoint and doesn't work with
    our image-model build path in _model_cache.py).
    """

    MODEL_DIR = "models/sam3"
    MODEL_FILENAME_CANDIDATES = ["sam3.pt", "sam3.safetensors"]
    # Kept for backward-compat references; first candidate is the preferred one.
    MODEL_FILENAME = MODEL_FILENAME_CANDIDATES[0]

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="LoadSAM3Model",
            display_name="(Down)Load SAM3 Model",
            category="SAM3",
            inputs=[
                io.Combo.Input("precision", options=["auto", "bf16", "fp16", "fp32"],
                               default="auto", optional=True,
                               tooltip="Model precision. auto: best for your GPU (bf16 on Ampere+, fp16 on Volta/Turing, fp32 on older)."),
                io.Boolean.Input("compile", default=False, optional=True,
                                 tooltip="Enable torch.compile for faster inference. Model loading takes longer (pre-compiles all code paths), but inference is significantly faster on every run."),
            ],
            outputs=[
                io.Custom("SAM3_MODEL_CONFIG").Output(display_name="sam3_model_config"),
            ],
        )

    @classmethod
    def execute(cls, precision="auto", compile=False):
        import comfy.utils
        import comfy.model_management

        load_device = comfy.model_management.get_torch_device()

        # aiNail vendor patch: resolve via folder_paths so SRCtrl-registered
        # /workspace/models/comfy/sam3/ (from comfy_reference_data.yml ->
        # generated extra_model_paths.yaml) is searched as well as the default
        # ComfyUI dir registered above. Try .pt first (image-model checkpoint),
        # then .safetensors.
        checkpoint_path = None
        for cand in cls.MODEL_FILENAME_CANDIDATES:
            cp = folder_paths.get_full_path("sam3", cand)
            if cp is not None and Path(cp).exists():
                checkpoint_path = cp
                log.info(f"[SAM3] Using checkpoint: {checkpoint_path}")
                break
        if checkpoint_path is None:
            raise FileNotFoundError(
                f"[SAM3] None of {cls.MODEL_FILENAME_CANDIDATES} found in any registered "
                f"'sam3' folder. Ensure pull_models.sh populated $PWD/hub/models/comfy/sam3/."
            )

        # BPE path for tokenizer
        bpe_path = str(Path(__file__).parent / "sam3" / "bpe_simple_vocab_16e6.txt.gz")

        # Resolve dtype
        if precision == "auto":
            if comfy.model_management.should_use_bf16(load_device):
                dtype = torch.bfloat16
            elif comfy.model_management.should_use_fp16(load_device):
                dtype = torch.float16
            else:
                dtype = torch.float32
        else:
            dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[precision]

        # Store dtype as string for JSON-safe IPC across isolation boundary
        dtype_str = {torch.bfloat16: "bf16", torch.float16: "fp16", torch.float32: "fp32"}[dtype]
        log.info(f"Resolved precision: {precision} -> {dtype_str}")

        config = {
            "checkpoint_path": str(checkpoint_path),
            "bpe_path": bpe_path,
            "precision": precision,
            "dtype": dtype_str,
            "compile": compile,
        }
        return io.NodeOutput(config)


NODE_CLASS_MAPPINGS = {
    "LoadSAM3Model": LoadSAM3Model
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadSAM3Model": "(Down)Load SAM3 Model"
}
