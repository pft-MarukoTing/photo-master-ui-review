from .nodes.loader import NODE_CLASS_MAPPINGS as _C1, NODE_DISPLAY_NAME_MAPPINGS as _D1
from .nodes.point_infer import NODE_CLASS_MAPPINGS as _C2, NODE_DISPLAY_NAME_MAPPINGS as _D2
from .nodes.prep_patch import NODE_CLASS_MAPPINGS as _C3, NODE_DISPLAY_NAME_MAPPINGS as _D3
from .nodes.patch_infer import NODE_CLASS_MAPPINGS as _C4, NODE_DISPLAY_NAME_MAPPINGS as _D4
from .nodes.decode import NODE_CLASS_MAPPINGS as _C5, NODE_DISPLAY_NAME_MAPPINGS as _D5

NODE_CLASS_MAPPINGS = {**_C1, **_C2, **_C3, **_C4, **_C5}
NODE_DISPLAY_NAME_MAPPINGS = {**_D1, **_D2, **_D3, **_D4, **_D5}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
