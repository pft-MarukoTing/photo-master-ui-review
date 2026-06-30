"""ComfyUI-SAM3 entry point.

Vendored copy of PozzettiAndrea/ComfyUI-SAM3 used by aiNail. Bypasses the
upstream `comfy_env` pixi-based isolation: kowloon container already has
torch/transformers/etc., so we import node modules directly via the
standard ComfyUI mapping pattern.
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']
