import os

import boto3
from botocore.exceptions import ClientError

from aiserver_utils.aescipher import AESCipher


class SecretManager:
    """
    You can configure with following environment variables:

    :envvar:`ENGINE_ENV`:
        The environment of the engine. Default is ``prod``.
    :envvar:`AWS_PARAM_STORE_REGION`:
        AWS region. Default is ``us-west-2``.
    :envvar:`AWS_PARAM_STORE_ACCESS_KEY_ID`:
        AWS access key ID
    :envvar:`AWS_PARAM_STORE_SECRET_ACCESS_KEY`:
        Encrypted AWS secret access key
    """

    __ssm_client = None
    __cache = {}
    __KEY_FORMAT = "/{}/{}/{}/{}:Verified"
    __engine_env = os.getenv("ENGINE_ENV", "prod")

    def __init__(self):
        if not SecretManager.__ssm_client:
            region = os.getenv("AWS_PARAM_STORE_REGION", "us-west-2")
            access_key = os.getenv("AWS_PARAM_STORE_ACCESS_KEY_ID")
            secret_key = (
                AESCipher().decrypt(os.getenv("AWS_PARAM_STORE_SECRET_ACCESS_KEY")) if os.getenv("AWS_PARAM_STORE_SECRET_ACCESS_KEY") else None
            )
            SecretManager.__ssm_client = boto3.client(
                "ssm",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )

    def get_secret(self, app: str, service: str, name: str) -> str:
        """
        Get a secret value from AWS Parameter Store with given ``app``, ``service`` and ``name``.
        The secret is cached in memory for subsequent calls.
        If the secret you get is outdated, call the :meth:`clear` method to clear the cache.

        Parameters
        ----------
        app : str
            The name of the application.
        service : str
            The name of the service.
        name : str
            The name of the name.
        """
        key = SecretManager.__KEY_FORMAT.format(SecretManager.__engine_env, app, service, name)
        cache = SecretManager.__cache.get(key)
        if cache is not None:
            return cache
        try:
            secret = (
                SecretManager.__ssm_client.get_parameter(Name=key, WithDecryption=True)
                .get("Parameter", {})
                .get("Value")
            )
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ParameterNotFound", "ParameterVersionNotFound"):
                return None
            raise
        SecretManager.__cache[key] = secret
        return secret

    def clear(self, app: str, service: str, name: str):
        """
        Clear the cached secret value for the given ``app``, ``service``, and ``name``.

        Parameters
        ----------
        app : str
            The name of the application.
        service : str
            The name of the service.
        name : str
            The name of the name.
        """
        key = SecretManager.__KEY_FORMAT.format(SecretManager.__engine_env, app, service, name)
        if key in SecretManager.__cache:
            del SecretManager.__cache[key]
