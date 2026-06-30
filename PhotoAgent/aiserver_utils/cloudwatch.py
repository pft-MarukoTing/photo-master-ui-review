import traceback
from datetime import datetime

import boto3

import os

from . import common
from .aescipher import AESCipher
from .logger import logger


# Init boto3 sdk first. Not cost time in the duration of CloudwatchAccess init
# Region is required but not important. No connection will be made.
boto3.client('cloudwatch', region_name='us-west-2')

_cipher = AESCipher()

class CloudwatchAccess:
    def __init__(self):
        cloudwatch_region = common.get_property(os.environ, 'CLOUDWATCH_REGION')
        if cloudwatch_region is None:
            cloudwatch_region = 'us-west-2'
        access_key = common.get_property(os.environ, 'AWS_ACCESS_KEY_ID')
        secret_key_enc = common.get_property(os.environ, 'AWS_SECRET_ACCESS_KEY')
        secret_key = _cipher.decrypt(secret_key_enc) if secret_key_enc is not None else None
        self._task_resp_time_metric_name = common.get_property(os.environ, 'CLOUDWATCH_TASK_RESPONSE_TIME_METRIC_NAME')
        self._is_task_resp_time_enabled = (self._task_resp_time_metric_name is not None
                                           and access_key is not None and secret_key is not None)
        self._to_init_client = self._is_task_resp_time_enabled

        if not self._is_task_resp_time_enabled:
            logger.log("CloudWatch related environment variables not set, "
                       "such as 'CLOUDWATCH_REGION', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', "
                       "'CLOUDWATCH_TASK_RESPONSE_TIME_METRIC_NAME'. "
                       "'put_task_response_time' is disabled.", status="running")


        if self._to_init_client:
            self.cloudwatch_client = boto3.client(
                    'cloudwatch',
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                    region_name=cloudwatch_region
            )
        else:
            self.cloudwatch_client = None


    def put_task_response_time(self, task_id, response_time):
        if not self._is_task_resp_time_enabled:
            return

        namespace = "engine-res-time"
        unit = "Milliseconds"
        resolution = 1
        metric_data = {
            'MetricName': self._task_resp_time_metric_name,
            'Value': response_time,
            'Unit': unit,
            'StorageResolution': resolution
        }
        try:
            start_time = datetime.utcnow()
            self.cloudwatch_client.put_metric_data(
                Namespace=namespace,
                MetricData=[metric_data]
            )

            end_time = datetime.utcnow()
            logger.log("put task response time", task_id=task_id, status="running", start_time=start_time,
                       end_time=end_time, metric_namespace=namespace, metric_data=metric_data)
        except Exception as e:
            logger.log(f"{e}", "Error", status="running", task_id=task_id)
            logger.log(traceback.format_exc(), "Error", status="running", task_id=task_id)