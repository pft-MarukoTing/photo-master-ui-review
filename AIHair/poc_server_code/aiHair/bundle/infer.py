"""The `infer` module hairfx expects from HairFeatures' bundle dir (on
sys.path via hairfx/core.py), for aiHair: the photo's boundary checks, the
OpenAI error mapping and the response parsing core.load_photo and core.edit
call. Errors are ai_hair.exceptions, so they carry an engine result code.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import urlparse

from ai_hair.exceptions import AiHairError, NonRetryableError, RetryableError
from engine_result_code import (
    RESULT_ERROR_DECODE_REF_IMAGE,
    RESULT_ERROR_DOWNLOAD_REF_IMAGE,
    RESULT_ERROR_MODERATION_BLOCKED,
    RESULT_INVALID_PARAMETER,
    RESULT_RESOURCE_EXHAUSTED,
    RESULT_UNKNOWN_INTERNAL_ERROR,
)

# The OpenAI client's timeout where AIHAIR_OPENAI_TIMEOUT_S doesn't set one.
_DEFAULT_TIMEOUT_S = 300
# The largest photo accepted, bytes (images.edit's own limit is 50 MB).
_MAX_PHOTO_BYTES = 50 * 1024 * 1024
_URL_TIMEOUT_S = 60


def _sniff(data: bytes) -> tuple[str, str] | None:
    """(extension, MIME type) for PNG, JPEG or WebP bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _download(url: str) -> bytes:
    import requests

    try:
        response = requests.get(url, timeout=_URL_TIMEOUT_S)
        response.raise_for_status()
    except Exception as e:  # noqa: BLE001 — reported with its code
        raise AiHairError(f"downloading {url} failed: {e}", RESULT_ERROR_DOWNLOAD_REF_IMAGE) from e
    return response.content


def _ref_to_openai_file(ref: str, index: int) -> tuple[str, bytes, str]:
    """`ref`, a local path or an https URL, as an images.edit file tuple
    (name, bytes, MIME type), once it is known to be real PNG, JPEG or WebP
    bytes."""
    if "://" in ref:
        if urlparse(ref).scheme != "https":
            raise NonRetryableError(f"only https URLs are accepted: {ref}")
        data = _download(ref)
    else:
        if not os.path.isfile(ref):
            raise AiHairError(f"photo not found: {ref}", RESULT_ERROR_DOWNLOAD_REF_IMAGE)
        if os.path.getsize(ref) > _MAX_PHOTO_BYTES:
            raise NonRetryableError(f"photo over {_MAX_PHOTO_BYTES} bytes: {ref}")
        with open(ref, "rb") as f:
            data = f.read()
    kind = _sniff(data)
    if kind is None:
        raise NonRetryableError(f"photo is not PNG, JPEG or WebP: {ref}",
                                RESULT_ERROR_DECODE_REF_IMAGE)
    ext, mime = kind
    return f"ref_{index}.{ext}", data, mime


_RETRYABLE_NAMES = {"APIConnectionError", "APITimeoutError", "InternalServerError",
                    "RateLimitError"}


def _raise_openai_error(e: Exception) -> Exception:
    """The AiHairError to raise for an OpenAI call's exception `e` (core.edit
    raises it `from e`): moderation blocks and rate limits get their own
    codes, other 4xx are the request's fault, the rest may pass on retry."""
    if isinstance(e, AiHairError):
        return e
    status = getattr(e, "status_code", None)
    code = getattr(e, "code", None)
    message = f"{type(e).__name__}: {e}"
    text = str(e).lower()
    if code == "moderation_blocked" or "moderation_blocked" in text or "safety system" in text:
        return NonRetryableError(message, RESULT_ERROR_MODERATION_BLOCKED)
    if type(e).__name__ == "RateLimitError" or status == 429:
        return RetryableError(message, RESULT_RESOURCE_EXHAUSTED)
    if type(e).__name__ in _RETRYABLE_NAMES or (isinstance(status, int) and status >= 500):
        return RetryableError(message, RESULT_UNKNOWN_INTERNAL_ERROR)
    if isinstance(status, int) and 400 <= status < 500:
        return NonRetryableError(message, RESULT_INVALID_PARAMETER)
    return AiHairError(message, RESULT_UNKNOWN_INTERNAL_ERROR)


def _extract_image_bytes(response) -> bytes | None:
    """The image in response.data[0]: its b64_json decoded, or its https url
    fetched; None where it has neither."""
    data = getattr(response, "data", None) or []
    if not data:
        return None
    item = data[0]
    b64 = getattr(item, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    url = getattr(item, "url", None)
    if url:
        if urlparse(url).scheme != "https":
            return None
        image = _download(url)
        return image if _sniff(image) else None
    return None
