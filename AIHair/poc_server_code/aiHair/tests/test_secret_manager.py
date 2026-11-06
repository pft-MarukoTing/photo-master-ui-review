from unittest.mock import Mock

import pytest

from aiserver_utils.aescipher import AESCipher
from secret_manager.secret_manager import SecretManager


@pytest.mark.parametrize("engine_env,service", [
    ("yce_ssm/demo", "aihair"),
    ("yce_ssm/prod", "aihairb"),
    ("yce_ssm/prod", "aihair"),
])
def test_secret_manager_fetches_verified_gemini_parameter_and_caches_it(monkeypatch, capsys, caplog, engine_env, service):
    caplog.set_level("DEBUG")
    monkeypatch.setenv("AWS_PARAM_STORE_REGION", "us-west-2")
    monkeypatch.setenv("AWS_PARAM_STORE_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_PARAM_STORE_SECRET_ACCESS_KEY", AESCipher().encrypt("test-secret-key"))
    monkeypatch.setattr(SecretManager, "_SecretManager__engine_env", engine_env)
    monkeypatch.setattr(SecretManager, "_SecretManager__ssm_client", None)
    monkeypatch.setattr(SecretManager, "_SecretManager__cache", {})
    ssm_client = Mock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": "ssm-gemini-key"}}
    client_factory = Mock(return_value=ssm_client)
    monkeypatch.setattr("secret_manager.secret_manager.boto3.client", client_factory)

    manager = SecretManager()
    assert manager.get_secret("global", service, "gemini_api_key") == "ssm-gemini-key"
    assert manager.get_secret("global", service, "gemini_api_key") == "ssm-gemini-key"

    client_factory.assert_called_once_with(
        "ssm",
        region_name="us-west-2",
        aws_access_key_id="test-access-key",
        aws_secret_access_key="test-secret-key",
    )
    ssm_client.get_parameter.assert_called_once_with(
        Name=f"/{engine_env}/global/{service}/gemini_api_key:Verified",
        WithDecryption=True,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert caplog.text == ""
