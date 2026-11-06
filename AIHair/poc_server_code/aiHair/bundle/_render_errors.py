"""The `_render_errors` module hairfx/core.py imports from HairFeatures'
bundle dir, for aiHair."""

from ai_hair.exceptions import RetryableError
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR


def no_image_returned(response_text: str = "") -> Exception:
    """The error core.edit raises when the API sent no image back."""
    return RetryableError(f"render returned no image: {response_text}",
                          RESULT_UNKNOWN_INTERNAL_ERROR)
