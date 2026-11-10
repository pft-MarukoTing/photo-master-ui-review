"""C2PA signing utilities for engines (sign assets before upload or before zipping)."""

import mimetypes
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .exceptions import C2PAKeyMaterialError, C2PASignFailedError, C2PAUnsupportedError

_C2PA_KEY_DIR = Path("/workspace/c2pa")
_CERT_PATH = _C2PA_KEY_DIR / "cert.pem"
_KEY_PATH = _C2PA_KEY_DIR / "key.pem"

_KEY_MATERIAL_LOCK = threading.Lock()
_cert_data: Optional[bytes] = None
_key_data: Optional[bytes] = None

# Extensions c2pa-python Builder can embed a manifest in (0.37.12).
_EXT_TO_MIME = {
    # image
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "heic": "image/heic",
    "heif": "image/heif",
    "avif": "image/avif",
    "dng": "image/dng",
    "jxl": "image/jxl",
    "svg": "image/svg+xml",
    # video
    "mp4": "video/mp4",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    # audio
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    # document / archive
    "zip": "application/x-zip",
    "epub": "application/epub+zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "docm": "application/vnd.ms-word.document.macroenabled.12",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pptm": "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "odt": "application/vnd.oasis.opendocument.text",
    "ott": "application/vnd.oasis.opendocument.text-template",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "ots": "application/vnd.oasis.opendocument.spreadsheet-template",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "otp": "application/vnd.oasis.opendocument.presentation-template",
    "odg": "application/vnd.oasis.opendocument.graphics",
    "otg": "application/vnd.oasis.opendocument.graphics-template",
    "oxps": "application/oxps",
}

_DIGITAL_SOURCE_TYPE = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
_METADATA_CONTEXT = {"issuer": "https://perfectcorp.com/c2pa/v1"}


def _load_key_material() -> tuple[bytes, bytes]:
    global _cert_data, _key_data
    with _KEY_MATERIAL_LOCK:
        if _cert_data is None or _key_data is None:
            missing = [p for p in (_CERT_PATH, _KEY_PATH) if not p.is_file()]
            if missing:
                paths = ", ".join(str(p) for p in missing)
                raise C2PAKeyMaterialError(f"C2PA signing key material missing: {paths}")
            try:
                _cert_data = _CERT_PATH.read_bytes()
                _key_data = _KEY_PATH.read_bytes()
            except OSError as e:
                raise C2PAKeyMaterialError(
                    f"C2PA signing key material not readable under {_C2PA_KEY_DIR}: {e}"
                ) from e
        return _cert_data, _key_data


def _extension(filename: str) -> str:
    path = urlparse(filename).path if "://" in filename else filename
    if "." not in path:
        return ""
    return path.rsplit(".", 1)[-1].lower()


def _is_media(filename: str) -> bool:
    guessed, _ = mimetypes.guess_type(filename, strict=False)
    if not guessed:
        return False
    return guessed.startswith(("image/", "video/", "audio/"))


def mime_from_filename(filename: str) -> Optional[str]:
    ext = _extension(filename)
    if not ext:
        raise C2PAUnsupportedError(f"C2PA unsupported format (no extension): {filename}")
    if ext in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext]
    if _is_media(filename):
        raise C2PAUnsupportedError(f"C2PA unsupported media format: {filename}")
    return None


def _utc_generated_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_manifest(generated_at: str) -> dict:
    return {
        "claim_generator_info": [
            {
                "name": "Perfect Corp.",
                "version": "1.0.0",
            }
        ],
        "assertions": [
            {
                "label": "c2pa.actions.v2",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "when": generated_at,
                            "digitalSourceType": _DIGITAL_SOURCE_TYPE,
                        }
                    ]
                },
            }
        ],
    }


def _signing_context():
    from c2pa import Context, Settings

    settings = Settings()
    settings.set("sign.reject_self_signed_certs", "false")
    settings.set("sign.add_timestamp", "false")
    settings.set("builder.thumbnail.enabled", "false")
    return Context(settings)


def _sign(data: bytes, mime: str) -> bytes:
    cert_data, key_data = _load_key_material()
    try:
        from c2pa import Builder, C2paSignerInfo, C2paSigningAlg, Signer
    except Exception as e:
        raise C2PASignFailedError(f"C2PA sign failed: {e}") from e

    generated_at = _utc_generated_at()
    manifest = _build_manifest(generated_at)
    signer_info = C2paSignerInfo(
        alg=C2paSigningAlg.PS256,
        sign_cert=cert_data,
        private_key=key_data,
        ta_url=None,
    )

    source = BytesIO(data)
    dest = BytesIO()
    try:
        with _signing_context() as ctx:
            with Signer.from_info(signer_info) as signer:
                with Builder(manifest, ctx) as builder:
                    builder.sign(signer, mime, source, dest)
    except C2PAKeyMaterialError:
        raise
    except Exception as e:
        raise C2PASignFailedError(f"C2PA sign failed (mime={mime}): {e}") from e

    signed = dest.getvalue()
    if not signed:
        raise C2PASignFailedError(f"C2PA sign produced empty output (mime={mime})")
    return signed


def sign_file(data: bytes, filename: str) -> bytes:
    """Sign bytes when filename extension is C2PA-supported; skip text/other; error on unsupported media."""
    mime = mime_from_filename(filename)
    if mime is None:
        return data
    return _sign(data, mime)


def sign_upload_buffer(data: bytes, key: str, c2pa_sign: bool = True) -> bytes:
    if not c2pa_sign:
        return data
    return sign_file(data, key)
