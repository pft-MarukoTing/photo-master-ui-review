from .nodes.crop_by_boxes import NODE_CLASS_MAPPINGS as _C1, NODE_DISPLAY_NAME_MAPPINGS as _D1
from .nodes.pad_to_square import NODE_CLASS_MAPPINGS as _C2, NODE_DISPLAY_NAME_MAPPINGS as _D2
from .nodes.resize_image import NODE_CLASS_MAPPINGS as _C3, NODE_DISPLAY_NAME_MAPPINGS as _D3
from .nodes.crop_square_by_boxes import NODE_CLASS_MAPPINGS as _C4, NODE_DISPLAY_NAME_MAPPINGS as _D4
from .nodes.paste_back import NODE_CLASS_MAPPINGS as _C5, NODE_DISPLAY_NAME_MAPPINGS as _D5
from .nodes.direct_paste_back import NODE_CLASS_MAPPINGS as _C6, NODE_DISPLAY_NAME_MAPPINGS as _D6
from .nodes.paste_back_matched import NODE_CLASS_MAPPINGS as _C7, NODE_DISPLAY_NAME_MAPPINGS as _D7
from .nodes.ssim_score import NODE_CLASS_MAPPINGS as _C8, NODE_DISPLAY_NAME_MAPPINGS as _D8

NODE_CLASS_MAPPINGS = {**_C1, **_C2, **_C3, **_C4, **_C5, **_C6, **_C7, **_C8}
NODE_DISPLAY_NAME_MAPPINGS = {**_D1, **_D2, **_D3, **_D4, **_D5, **_D6, **_D7, **_D8}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
