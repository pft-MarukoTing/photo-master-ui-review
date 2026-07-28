"""S3 artifact transfer helpers for the photoAgent server."""

import os
import threading
import time

from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR

# How long upload_file/download_file wait for the async S3 accessor's
# callback before giving up. 30s was too tight under real load -- a transfer
# that's merely slow (busy shared machine, S3 latency) gets misreported as a
# hard failure, and the callback still lands seconds later logging "Invalid
# task_id in callback!!" for an entry we'd already discarded.
S3_TRANSFER_WAIT_SECONDS = 90


class S3ArtifactStore:
    def __init__(self, s3_accessor, logger, monitor_dst_dir='./tmp'):
        self.s3_accessor = s3_accessor
        self.logger = logger
        self.monitor_dst_dir = monitor_dst_dir
        self.s3_result = {}
        self.s3_result_lock = threading.Lock()

    def s3_callback(self, response):
        task_id = response['task_id']
        del response['task_id']
        with self.s3_result_lock:
            if task_id not in self.s3_result:
                self.logger.error(task_id, 'Invalid task_id in callback!!')
            else:
                if 'status' not in response:
                    response['status'] = 'done'
                if 'end_ts' not in response:
                    response['end_ts'] = time.time()
                self.s3_result[task_id].update(response)

    def upload_file(self, filepath, dstKey):
        start_ts = time.time()
        task_id = '%s-%s' % (dstKey, start_ts)
        with self.s3_result_lock:
            self.s3_result[task_id] = {
                'status': 'uploading',
                'task_id': task_id,
                'start_ts': start_ts,
            }
        try:
            self.s3_accessor.upload_file(self.s3_callback, filepath, dstKey, task_id)
        except Exception:
            with self.s3_result_lock:
                self.s3_result.pop(task_id, None)
            raise

        wait_start = time.time()
        max_wait_time = S3_TRANSFER_WAIT_SECONDS
        error = ''
        while True:
            with self.s3_result_lock:
                status = self.s3_result[task_id]['status']
            if status != 'uploading':
                break
            if time.time() - wait_start > max_wait_time:
                error = 'upload %s timeout' % dstKey
                break
            time.sleep(0.1)

        with self.s3_result_lock:
            upload_result = self.s3_result.pop(task_id)

        if error:
            raise RuntimeError(error, RESULT_UNKNOWN_INTERNAL_ERROR)
        if upload_result.get('error'):
            raise RuntimeError('upload %s error: %s' % (dstKey, upload_result['error']), RESULT_UNKNOWN_INTERNAL_ERROR)

    def download_file(self, key, folder_name=None):
        folderpath = os.path.join(self.monitor_dst_dir, folder_name if folder_name is not None else 'server_output')
        os.makedirs(folderpath, exist_ok=True)
        filepath = os.path.join(folderpath, key.split('/')[-1])
        start_ts = time.time()
        task_id = '%s-%s' % (key, start_ts)
        with self.s3_result_lock:
            self.s3_result[task_id] = {
                'status': 'downloading',
                'task_id': task_id,
                'start_ts': start_ts,
            }
        try:
            self.s3_accessor.download_file(self.s3_callback, key, filepath, task_id)
        except Exception:
            with self.s3_result_lock:
                self.s3_result.pop(task_id, None)
            raise

        error = ''
        error_code = None
        wait_start = time.time()
        max_wait_time = S3_TRANSFER_WAIT_SECONDS
        while True:
            with self.s3_result_lock:
                status = self.s3_result[task_id]['status']
            if status != 'downloading':
                break
            if time.time() - wait_start > max_wait_time:
                error = 'download %s timeout' % task_id
                error_code = RESULT_UNKNOWN_INTERNAL_ERROR
                break
            time.sleep(0.1)

        with self.s3_result_lock:
            download_result = self.s3_result.pop(task_id)

        if not error and download_result.get('error'):
            error = 'download %s error: %s' % (task_id, download_result['error'])
            error_code = RESULT_UNKNOWN_INTERNAL_ERROR

        if error:
            raise RuntimeError(error, error_code)

        return download_result.get('img_path', filepath)

    def s3_create_presigned_url(self, object_name: str, action: str) -> str:
        s3_client = self.s3_accessor.s3_client_proxy.s3Client
        return s3_client.generate_presigned_url(action, Params={
            'Bucket': self.s3_accessor.s3_client_proxy.default_bucket,
            'Key': object_name,
        })
