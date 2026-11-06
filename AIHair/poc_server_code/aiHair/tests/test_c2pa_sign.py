import pytest

from ai_hair import c2pa_sign
from ai_hair.exceptions import C2PAKeyMaterialError, C2PASignFailedError, C2PAUnsupportedError


@pytest.mark.parametrize("exception_class,expected_code", [
    (C2PAKeyMaterialError, "error_c2pa_key_missing"),
    (C2PASignFailedError, "error_c2pa_sign_failed"),
    (C2PAUnsupportedError, "error_c2pa_unsupported"),
])
def test_c2pa_errors_preserve_worker_codes(exception_class, expected_code):
    assert exception_class("cannot sign").args == ("cannot sign", expected_code)
    assert exception_class("cannot sign", code="custom_code").args == ("cannot sign", "custom_code")


@pytest.mark.parametrize("filename,mime", [
    ("song.mp3", "audio/mpeg"),
    ("song.wav", "audio/wav"),
    ("song.flac", "audio/flac"),
    ("song.m4a", "audio/mp4"),
    ("cover.jpg", "image/jpeg"),
    ("cover.png", "image/png"),
    ("video.mp4", "video/mp4"),
])
def test_sign_file_dispatches_supported_media_to_signer(monkeypatch, filename, mime):
    calls = []

    def sign(data, media_type):
        calls.append((data, media_type))
        return b"signed"

    monkeypatch.setattr(c2pa_sign, "_sign", sign)

    assert c2pa_sign.sign_file(b"media", filename) == b"signed"
    assert calls == [(b"media", mime)]


def test_nonmedia_and_disabled_signing_do_not_require_keys(monkeypatch):
    monkeypatch.setattr(c2pa_sign, "_sign", lambda *_: pytest.fail("must not sign"))

    assert c2pa_sign.sign_file(b"{}", "metadata.json") == b"{}"
    assert c2pa_sign.sign_upload_buffer(b"audio", "song.mp3", c2pa_sign=False) == b"audio"


@pytest.mark.parametrize("filename", ["song", "song.ogg"])
def test_unsupported_media_raises_typed_error(filename):
    with pytest.raises(C2PAUnsupportedError):
        c2pa_sign.sign_file(b"media", filename)


def test_missing_key_material_raises_worker_compatible_error(tmp_path, monkeypatch):
    monkeypatch.setattr(c2pa_sign, "_cert_data", None)
    monkeypatch.setattr(c2pa_sign, "_key_data", None)
    monkeypatch.setattr(c2pa_sign, "_CERT_PATH", tmp_path / "cert.pem")
    monkeypatch.setattr(c2pa_sign, "_KEY_PATH", tmp_path / "key.pem")

    with pytest.raises(C2PAKeyMaterialError, match="C2PA signing key material missing") as raised:
        c2pa_sign.sign_file(b"audio", "song.mp3")

    assert raised.value.args[1] == "error_c2pa_key_missing"
    assert str(tmp_path / "cert.pem") in raised.value.args[0]
    assert str(tmp_path / "key.pem") in raised.value.args[0]
