import json
import os
import shutil
import threading, queue
import traceback
from io import BytesIO
from datetime import datetime

from botocore.exceptions import EndpointResolutionError

from .common import is_empty_str
from .logger import logger
from .s3_utils import s3proxy, get_bucket_name, guess_content_type_by_file_path


class S3Process(threading.Thread):
    def __init__(self, s3config, task_queue, t_id, request_id, s3_client_proxy=None):
        threading.Thread.__init__(self)
        self.s3config = s3config
        self.request_id = request_id  # for logging
        self.t_id = t_id
        self.queue = task_queue
        self.stop_event = threading.Event()
        self.s3_proxy_map = {}
        self.s3_client_proxy = s3_client_proxy

    def stop(self):
        # print ("{} [{}] PostProcessing[{}] stop".format(datetime.utcnow(), self.request_id, self.t_id))
        self.stop_event.set()

    def run(self):
        raise NotImplementedError

    def _get_s3_proxy(self, file_key):
        bucket_name = self._get_bucket_name(file_key)
        if bucket_name not in self.s3_proxy_map:
            self.s3_proxy_map[bucket_name] = s3proxy(self.s3config.access_key, self.s3config.secret_key, bucket_name,
                                                     self.s3config.testbed_mode, self.s3config.use_ta)
        return self.s3_proxy_map[bucket_name]

    def _get_bucket_name(self, file_key):
        return get_bucket_name(file_key)


class PostProcessing(S3Process):
    def __init__(self, s3config, task_queue, upload_size_queue, t_id, request_id, jpg_quality=90, s3_client_proxy=None):
        super(PostProcessing, self).__init__(s3config, task_queue, t_id, request_id, s3_client_proxy)
        self.jpg_quality = jpg_quality
        self.upload_size_queue = upload_size_queue

    def run(self):
        while True:  # avoid infinite blocking
            try:
                data = None
                data = self.queue.get(timeout=0.01)

                # got data
                before_upload_time = datetime.utcnow()

                img_buffer = data.img_buffer
                out_path = data.out_path
                file_tokens = out_path.split('/')
                file_key = out_path
                task_id = data.tsk_id
                enqueue_time = data.enq_time

                img_type = file_tokens[-1][-4:]
                img_result = {}
                bucket_name = None
                if self.s3_client_proxy is not None:
                    bucket_name = self._get_bucket_name(file_key)
                    img_result = self.s3_client_proxy.upload_file(BytesIO(img_buffer), file_key, img_type,
                                                                  bucket=bucket_name, task_id=task_id)
                else:  # Expect not to use this branch anymore
                    s3_proxy = self._get_s3_proxy(file_key)
                    s3_proxy.upload_file(file_data=BytesIO(img_buffer), file_key=file_key, file_type=img_type)
                self.upload_size_queue.put({"key": file_key, "size": len(img_buffer), "error": None})

                file_key_info = img_result.get("file_key_info", {})
                handled_file_size = file_key_info.get("size", None)

                after_upload_time = datetime.utcnow()
                logger.log(msg="Upload image", task_id=task_id, key=file_key, enqueue_time=enqueue_time,
                           start_time=before_upload_time, end_time=after_upload_time, status="running",
                           handled_file_size=handled_file_size, file_key_info=file_key_info,
                           bucket=bucket_name)
            except queue.Empty:
                # print ("{} PostProcessing[{}] Waiting data".format(datetime.utcnow(), self.t_id))
                pass
            except EndpointResolutionError:
                self.upload_size_queue.put({"key": file_key, "size": 0, "error": "Transfer accelerate not support"})
            except Exception as e:
                error_msg = f"{e}"
                self.upload_size_queue.put({"key": file_key, "size": len(img_buffer), "error": error_msg})
                logger.log(traceback.format_exc(), "Error", status="running")
            finally:
                if data is not None:
                    self.queue.task_done()
                if self.queue.empty() and self.stop_event.is_set():
                    break


class DownloadProcessing(S3Process):
    def __init__(self, s3config, task_queue, out_queue, t_id, request_id, s3_client_proxy=None):
        super(DownloadProcessing, self).__init__(s3config, task_queue, t_id, request_id, s3_client_proxy)
        self.out_queue = out_queue

    def run(self):
        while True:  # avoid infinite blocking
            try:
                data = None
                data = self.queue.get(timeout=0.01)

                # got data
                before_download_time = datetime.utcnow()

                key = data.key
                path = data.path
                task_id = data.tsk_id
                enqueue_time = data.enq_time

                # t_up = time.time()
                img_result = {}
                bucket_name = None
                if self.s3_client_proxy is not None:
                    bucket_name = self._get_bucket_name(key)
                    if path is None:
                        img_result = self.s3_client_proxy.download_fileobj(key, bucket_name, get_map_result=True)  # result is image
                        result = img_result.get("file_content")
                    else:
                        img_result = self.s3_client_proxy.download_file(key, path, bucket_name, get_map_result=True)  # result is path
                        result = img_result.get("file_path")
                else:  # Expect not to use this branch anymore
                    s3_proxy = self._get_s3_proxy(key)
                    if path is None:
                        result = s3_proxy.download_fileobj(key)  # result is image
                    else:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        result = s3_proxy.download_file(key, path)  # result is path
                self.out_queue.put({"key": key, "result": result, "error": None})

                file_key_info = img_result.get("file_key_info", {})
                handled_file_size = file_key_info.get("size", None)
                after_download_time = datetime.utcnow()
                logger.log(msg="Download image", task_id=task_id, key=key, enqueue_time=enqueue_time,
                           start_time=before_download_time, end_time=after_download_time, status="running",
                           handled_file_size=handled_file_size, file_key_info=file_key_info,
                           bucket=bucket_name)
            except queue.Empty:
                # print ("{} PostProcessing[{}] Waiting data".format(datetime.utcnow(), self.t_id))
                pass
            except EndpointResolutionError:
                self.out_queue.put({"key": key, "result": None, "error": "Transfer accelerate not support"})
            except Exception as e:
                error_msg = f"{e}"
                self.out_queue.put({"key": key, "result": None, "error": error_msg})
                logger.log(traceback.format_exc(), "Error", status="running")
            finally:
                if data is not None:
                    self.queue.task_done()
                if self.queue.empty() and self.stop_event.is_set():
                    break


class S3ProcessV2(threading.Thread):
    def __init__(self, task_queue, s3_client_proxy):
        threading.Thread.__init__(self)
        self.queue = task_queue
        self.stop_event = threading.Event()
        self.s3_client_proxy = s3_client_proxy

    def stop(self):
        self.stop_event.set()

    def run(self):
        raise NotImplementedError

    @staticmethod
    def _invoke_callback(is_upload, callback, result, task_id):
        result_to_log = result.copy()  # Prevent result to be modified in the callback
        if result_to_log.get("img_buffer") is not None:
            result_to_log["img_buffer"] = "<img_buffer>"

        action = ("Upload" if is_upload else "Download") + " image"
        target_format = "buffer" if result.get("img_buffer") is not None else "file"
        msg_prefix = f"[V2 {action} for {target_format}]"

        before_log_title = f"{msg_prefix} before callback"
        after_log_title = f"{msg_prefix} after callback"
        try:
            logger.log(msg=before_log_title, task_id=task_id, status="running",
                       callback_input=result_to_log)

            before_callback_time = datetime.utcnow()
            callback(result)
            after_callback_time = datetime.utcnow()

            logger.log(msg=f"{after_log_title} success", task_id=task_id,
                       start_time=before_callback_time, end_time=after_callback_time, status="running",
                       callback_input=result_to_log)
        except Exception as e:
            logger.log(f"{after_log_title} error", "Error", status="running", task_id=task_id,
                       callback_input=result_to_log, error=f"{e}", error_traceback=traceback.format_exc())


class PostProcessingV2(S3ProcessV2):
    def __init__(self, task_queue, s3_client_proxy):
        super(PostProcessingV2, self).__init__(task_queue, s3_client_proxy)

    def run(self):
        while True:  # avoid infinite blocking
            try:
                data = None
                data = self.queue.get(timeout=0.01)

                # got data & upload
                before_upload_time = datetime.utcnow()

                in_img_buffer = data.img_buffer
                img_buffer = data.img_buffer
                img_path = data.img_path
                out_path = data.out_path
                file_key = out_path
                task_id = data.tsk_id
                enqueue_time = data.enq_time
                callback = data.callback
                content_type = data.content_type

                msg_prefix = f"[V2 Upload image for " + ("buffer" if img_path is None else "file") + "]"

                final_content_type = content_type
                guess_content_type = None
                if content_type is None:
                    guess_content_type = guess_content_type_by_file_path(out_path)
                    final_content_type = guess_content_type

                if img_path is not None:
                    img_buffer = open(img_path, "rb").read()

                bucket_name = get_bucket_name(file_key)
                img_result = self.s3_client_proxy.upload_file(BytesIO(img_buffer), file_key, None, bucket=bucket_name,
                                                              task_id=task_id, content_type=final_content_type)
                result = {"task_id": task_id, "key": file_key, "img_buffer": in_img_buffer, "img_path": img_path, "error": None}

                file_key_info = img_result.get("file_key_info", {})
                handled_file_size = file_key_info.get("size", None)
                after_upload_time = datetime.utcnow()
                logger.log(msg=f"{msg_prefix} success", task_id=task_id, key=file_key, enqueue_time=enqueue_time,
                           start_time=before_upload_time, end_time=after_upload_time, status="running",
                           handled_file_size=handled_file_size, file_key_info=file_key_info, content_type=content_type,
                           guessed_content_type=guess_content_type, final_content_type=final_content_type,
                           bucket=bucket_name)

                self._invoke_callback(True, callback, result, task_id)
            except queue.Empty:
                # print("{} PostProcessingV2[{}] Waiting data".format(datetime.utcnow(), threading.current_thread().name))
                pass
            except EndpointResolutionError:
                logger.log(f"{msg_prefix} error", "Error", status="running",
                           task_id=task_id, error="Transfer accelerate not support", error_traceback=traceback.format_exc())

                result = {"task_id": task_id, "key": file_key, "img_buffer": in_img_buffer, "img_path": img_path,
                          "error": "Transfer accelerate not support"}
                self._invoke_callback(True, callback, result, task_id)
            except Exception as e:
                logger.log(f"{msg_prefix} error", "Error", status="running",
                           task_id=task_id, error=f"{e}", error_traceback=traceback.format_exc())

                result = {"task_id": task_id, "key": file_key, "img_buffer": in_img_buffer, "img_path": img_path, "error": f"{e}"}
                self._invoke_callback(True, callback, result, task_id)
            finally:
                if data is not None:
                    self.queue.task_done()
                if self.queue.empty() and self.stop_event.is_set():
                    break


class DownloadProcessingV2(S3ProcessV2):
    def __init__(self, task_queue, s3_client_proxy):
        super(DownloadProcessingV2, self).__init__(task_queue, s3_client_proxy)

    def run(self):
        while True:  # avoid infinite blocking
            try:
                data = None
                data = self.queue.get(timeout=0.01)

                # got data
                before_download_time = datetime.utcnow()

                key = data.key
                path = data.path
                task_id = data.tsk_id
                enqueue_time = data.enq_time
                callback = data.callback

                msg_prefix = f"[V2 Download image for " + ("buffer" if path is None else "file") + "]"

                bucket_name = get_bucket_name(key)
                if path is None:
                    img_result = self.s3_client_proxy.download_file_binary(key, bucket_name, task_id, get_map_result=True)  # result is image
                    img_buffer = img_result.get("file_content")
                    result = {"task_id": task_id, "key": key, "img_buffer": img_buffer, "img_path": None, "error": None}
                else:
                    img_result = self.s3_client_proxy.download_file(key, path, bucket_name, task_id, get_map_result=True)  # result is path
                    img_path = img_result.get("file_path")
                    result = {"task_id": task_id, "key": key, "img_buffer": None, "img_path": img_path, "error": None}

                file_key_info = img_result.get("file_key_info", {})
                handled_file_size = file_key_info.get("size", None)
                after_download_time = datetime.utcnow()
                logger.log(msg=f"{msg_prefix} success", task_id=task_id, key=key, enqueue_time=enqueue_time,
                           start_time=before_download_time, end_time=after_download_time, status="running",
                           handled_file_size=handled_file_size, file_key_info=file_key_info)

                self._invoke_callback(False, callback, result, task_id)
            except queue.Empty:
                # print("{} DownloadProcessingV2[{}] Waiting data".format(datetime.utcnow(), threading.current_thread().name))
                pass
            except EndpointResolutionError:
                logger.log(f"{msg_prefix} error", "Error", status="running",
                           task_id=task_id, error="Transfer accelerate not support", error_traceback=traceback.format_exc())

                result = {"task_id": task_id, "key": key, "img_buffer": None, "img_path": None,
                          "error": "Transfer accelerate not support"}
                self._invoke_callback(False, callback, result, task_id)
            except Exception as e:
                logger.log(f"{msg_prefix} error", "Error", status="running",
                           task_id=task_id, error=f"{e}", error_traceback=traceback.format_exc())

                result = {"task_id": task_id, "key": key, "img_buffer": None, "img_path": None, "error": f"{e}"}
                self._invoke_callback(False, callback, result, task_id)
            finally:
                if data is not None:
                    self.queue.task_done()
                if self.queue.empty() and self.stop_event.is_set():
                    break

    def _check_file_in_local_storage(self, key):
        # Check if key exists in local storage
        # Return: {"ls_enabled": bool, "exists": bool, "local_path": str}
        if is_empty_str(self.local_storage_root):
            return {"ls_enabled": False, "exists": False, "local_path": None}

        ls_enabled = True
        local_path = os.path.join(self.local_storage_root, key)
        exists = os.path.exists(local_path)
        return {"ls_enabled": ls_enabled, "exists": exists, "local_path": local_path}
