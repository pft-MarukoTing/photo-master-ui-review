from datetime import datetime

from . import common
from .common import get_property
from .s3_process import PostProcessing, DownloadProcessing, PostProcessingV2, DownloadProcessingV2
import queue
from easydict import EasyDict as edict
import os
from .aescipher import AESCipher
from .s3_utils import s3ClientProxy, get_bucket_name
from .logger import logger


_cipher = AESCipher()

class S3Access():
    def __init__(self, worker_num=2, jpg_quality=90, set_ACL_public=False):
        '''
            args:
                worker_num: default is 2, number of download (upload) worker
                set_ACL_public: set True to make ACL set as "public_read"
        '''
        bucket_name = os.environ['S3_BUCKET']
        dynamic_bucket_suffixes = common.get_property(os.environ, 'S3_DYNAMIC_BUCKET_SUFFIXES')
        dynamic_bucket_map = {}  # file key suffix to bucket name mapping. e.g. 'jp/' -> 'yce-jp'
        if dynamic_bucket_suffixes is not None:
            for suffix in dynamic_bucket_suffixes.split(","):
                stripped_suffix = suffix.strip()
                dynamic_bucket_map[stripped_suffix + "/"] = "yce-" + stripped_suffix
        s3Region = os.environ['S3_REGION']
        use_ta = (os.getenv('S3_USE_TRANSFER_ACCELERATION') is not None
                  and os.getenv('S3_USE_TRANSFER_ACCELERATION').lower() == "true")
        use_ssl = (not os.environ.get("S3_USE_SSL") or os.environ.get("S3_USE_SSL").lower() != "false")
        access_key = os.environ['AWS_ACCESS_KEY_ID']
        secret_key = _cipher.decrypt(os.environ['AWS_SECRET_ACCESS_KEY'])
        endpoint_url = os.getenv('S3_ENDPOINT_URL')
        custom_config = common.string_to_dict(os.getenv('S3_CUS_CONFIG'))
        url_download_max_bytes_str = get_property(os.environ, 'URL_DOWNLOAD_MAX_BYTES')
        url_download_max_bytes = int(url_download_max_bytes_str) if url_download_max_bytes_str is not None else None
        download_file_cache_enabled = (not os.environ.get("DOWNLOAD_FILE_CACHE_ENABLED")
                                       or os.environ.get("DOWNLOAD_FILE_CACHE_ENABLED").lower() != "false")

        self.s3config = edict({
            'access_key': access_key,
            'secret_key': secret_key,
            'bucket_name': bucket_name,
            'testbed_mode': set_ACL_public,
            'use_ta': use_ta,
            'dynamic_bucket_map': dynamic_bucket_map,
        })

        self.worker_num = worker_num
        self.jpg_quality = jpg_quality

        self.path = None
        self.task_id = None
        self.worker_num = worker_num

        self.upload_queue=None
        self.upload_size_queue=None
        self.upload_thread_list=None
        self.download_queue=None
        self.download_thread_list=None
        self.download_img_queue=None
        self.s3_client_proxy = s3ClientProxy(access_key=access_key,
                                             secret_key=secret_key,
                                             default_bucket=bucket_name,
                                             testbed_mode=set_ACL_public,
                                             use_accelerate=use_ta,
                                             region=s3Region,
                                             use_ssl=use_ssl,
                                             endpoint_url=endpoint_url,
                                             custom_config=custom_config,
                                             url_download_max_bytes=url_download_max_bytes,
                                             download_file_cache_enabled=download_file_cache_enabled)

        self._create_upload_thread(num=worker_num)
        self._create_download_thread(num=worker_num)

    def _create_upload_thread(self, num=2, request_id=None):
        self.upload_queue = queue.Queue()
        self.upload_size_queue = queue.Queue()
        self.upload_thread_list = []
        for i in range(self.worker_num):
            t = PostProcessing(s3config=self.s3config, task_queue=self.upload_queue,
                               upload_size_queue=self.upload_size_queue, t_id=i, request_id=request_id,
                               jpg_quality=self.jpg_quality, s3_client_proxy=self.s3_client_proxy)
            self.upload_thread_list.append(t)

    def _create_download_thread(self, num=2, request_id=None):
        self.download_queue = queue.Queue()
        self.download_img_queue = queue.Queue()
        self.download_thread_list = []
        for i in range(self.worker_num):
            t = DownloadProcessing(s3config=self.s3config, task_queue=self.download_queue,
                                   out_queue=self.download_img_queue, t_id=i, request_id=request_id,
                                   s3_client_proxy=self.s3_client_proxy)
            self.download_thread_list.append(t)

    def upload_start(self, task_id=None):
        self.task_id = task_id
        for t in self.upload_thread_list:
            t.start()

    def download_start(self, path=None, task_id=None):
        self.task_id = task_id
        if path:
            self.path = path
        for t in self.download_thread_list:
            t.start()

    def upload_images(self, list_img_buffers, list_keys):
        if self.upload_queue is not None:
            data_queue = self.upload_queue
        else:
            print("[Error] upload queue doesn't exist! you should call create_upload_thread() first!")
            return 

        for idx in range(len(list_img_buffers)):
            data = {}
            data['out_path'] = list_keys[idx]
            data['img_buffer'] = list_img_buffers[idx]
            data['domain'] = idx
            data['enq_time'] = datetime.utcnow()
            data['tsk_id'] = self.task_id
            data_queue.put(edict(data))

    def upload_wait(self):
        for t in self.upload_thread_list:
            t.stop()
        for t in self.upload_thread_list:
            t.join()
        size_ls = self.get_upload_size_info()
        # self._create_upload_thread(num=2)
        self._create_upload_thread(num=self.worker_num)
        return size_ls

    def download_images(self, list_keys):
        if self.download_queue is not None:
            data_queue = self.download_queue
        else:
            print("[Error] download queue doesn't exist! you should call create_download_thread() first!")
            return 
        for idx, key in enumerate(list_keys):
            data = {}
            data['key'] = key
            if self.path is not None:
                data['path'] = os.path.join(self.path, key.split("/")[-1])
            else:
                data['path'] = None
            data['enq_time'] = datetime.utcnow()
            data['tsk_id'] = self.task_id
            data_queue.put(edict(data))

    def download_wait(self, timeout=10.0):
        for t in self.download_thread_list:
            t.stop()
        for t in self.download_thread_list:
            t.join()
        images_ls = self.get_download_images()  
        self._create_download_thread(num=self.worker_num)
        return images_ls
        # self._create_download_thread(num=2)

    def get_download_images(self):
        download_img_info_ls = list(self.download_img_queue.queue)
        results = {}
        for item in download_img_info_ls:
            results[item["key"]] = {
                "result": item['result'],
                "error": item['error']
            }
        return results
    
    def get_upload_size_info(self):
        upload_size_ls = list(self.upload_size_queue.queue)
        results = {}
        for item in upload_size_ls:
            results[item["key"]] = {
                "size": item['size'],
                "error": item['error']
            }
        return results

    def upload_to_s3(self, candidate_img_buffers, key_ls):
        """
        Args:
            candidate_img_buffers: img_buffers to be upload, should be in BGR foramt
            session_id: use job id
        """
        key_ls = key_ls[:len(candidate_img_buffers)]
        self._create_upload_thread(num=2)
        self.upload_start()
        self.upload_images(candidate_img_buffers, key_ls)
        # print("(upload_to_s3) key_ls:", key_ls, flush=True)
        size_ls = self.upload_wait()
        return size_ls

    def download_from_s3(self, key_ls):
        """
        Args:
            key_ls: list of keys for downloading images from s3
        """
        self._create_download_thread()
        self.download_start()
        self.download_images(key_ls)
        images_ls = self.download_wait()
        # images_ls = self.get_download_images()            

        return images_ls  #list(out_queue.queue)


class S3AccessV2:
    def __init__(self, worker_num=2, set_ACL_public=False):
        '''
            args:
                worker_num: default is 2, number of download (upload) worker
                set_ACL_public: set True to make ACL set as "public_read"
        '''

        # S3 related config
        bucket_name = os.environ['S3_BUCKET']
        s3_region = os.environ['S3_REGION']
        use_ta = (os.getenv('S3_USE_TRANSFER_ACCELERATION') is not None
                  and os.getenv('S3_USE_TRANSFER_ACCELERATION').lower() == "true")
        use_ssl = (not os.environ.get("S3_USE_SSL") or os.environ.get("S3_USE_SSL").lower() != "false")
        access_key = os.environ['AWS_ACCESS_KEY_ID']
        secret_key = _cipher.decrypt(os.environ['AWS_SECRET_ACCESS_KEY'])
        endpoint_url = os.getenv('S3_ENDPOINT_URL')
        custom_config = common.string_to_dict(os.getenv('S3_CUS_CONFIG'))
        local_storage_root = get_property(os.environ, 'S3_LOCAL_STORAGE_ROOT')
        url_download_max_bytes_str = get_property(os.environ, 'URL_DOWNLOAD_MAX_BYTES')
        url_download_max_bytes = int(url_download_max_bytes_str) if url_download_max_bytes_str is not None else None
        download_file_cache_enabled = (not os.environ.get("DOWNLOAD_FILE_CACHE_ENABLED")
                                       or os.environ.get("DOWNLOAD_FILE_CACHE_ENABLED").lower() != "false")

        # Thread related
        self.worker_num = worker_num

        self.upload_queue = None
        self.upload_result_queue = None
        self.upload_thread_list = None

        self.download_queue = None
        self.download_result_queue = None
        self.download_thread_list = None
        self.s3_client_proxy = s3ClientProxy(access_key=access_key,
                                             secret_key=secret_key,
                                             default_bucket=bucket_name,
                                             testbed_mode=set_ACL_public,
                                             use_accelerate=use_ta,
                                             region=s3_region,
                                             use_ssl=use_ssl,
                                             endpoint_url=endpoint_url,
                                             custom_config=custom_config,
                                             local_storage_root=local_storage_root,
                                             url_download_max_bytes=url_download_max_bytes,
                                             download_file_cache_enabled=download_file_cache_enabled)

        self._create_upload_thread()
        self._create_download_thread()
        self._start_upload_thread()
        self._start_download_thread()

    def _create_upload_thread(self):
        self.upload_queue = queue.Queue()
        self.upload_thread_list = []
        for i in range(self.worker_num):
            t = PostProcessingV2(self.upload_queue,  self.s3_client_proxy)
            self.upload_thread_list.append(t)

    def _create_download_thread(self):
        self.download_queue = queue.Queue()
        self.download_thread_list = []
        for i in range(self.worker_num):
            t = DownloadProcessingV2(self.download_queue, self.s3_client_proxy)
            self.download_thread_list.append(t)

    def _start_upload_thread(self):
        for t in self.upload_thread_list:
            t.start()

    def _start_download_thread(self):
        for t in self.download_thread_list:
            t.start()

    def upload_buffer(self, callback, file_buffer, key_path, task_id):
        self._check_upload_queue_exist()
        if callback is None or file_buffer is None or key_path is None or task_id is None:
            raise Exception("[Error] invalid input for upload_buffer")

        enqueue_time = datetime.utcnow()
        msg = f"[V2 Upload image for buffer] enqueue"
        logger.log(msg=msg, task_id=task_id, key=key_path, enqueue_time=enqueue_time, status="running")

        data = {
            'out_path': key_path,
            'img_buffer': file_buffer,
            'img_path': None,
            'enq_time': enqueue_time,
            'tsk_id': task_id,
            'callback': callback
        }
        self.upload_queue.put(edict(data))

    def upload_file(self, callback, file_path, key_path, task_id):
        self._check_upload_queue_exist()
        if callback is None or file_path is None or key_path is None or task_id is None:
            raise Exception("[Error] invalid input for upload_file")

        enqueue_time = datetime.utcnow()
        msg = f"[V2 Upload image for file] enqueue"
        logger.log(msg=msg, task_id=task_id, key=key_path, enqueue_time=enqueue_time, status="running")

        data = {
            'out_path': key_path,
            'img_buffer': None,
            'img_path': file_path,
            'enq_time': enqueue_time,
            'tsk_id': task_id,
            'callback': callback
        }
        self.upload_queue.put(edict(data))

    def _check_upload_queue_exist(self):
        if self.upload_queue is None:
            raise Exception("[Error] upload queue doesn't exist! Should init S3AccessV2 first!")

    def download_buffer(self, callback, key_path, task_id):
        self._check_download_queue_exist()
        if callback is None or key_path is None or task_id is None:
            raise Exception("[Error] invalid input for download_buffer")

        enqueue_time = datetime.utcnow()
        msg = f"[V2 Download image for buffer] enqueue"
        logger.log(msg=msg, task_id=task_id, key=key_path, enqueue_time=enqueue_time, status="running")

        data = {
            'key': key_path,
            'path': None,
            'enq_time': enqueue_time,
            'tsk_id': task_id,
            'callback': callback
        }
        self.download_queue.put(edict(data))

    def download_file(self, callback, key_path, file_path, task_id):
        self._check_download_queue_exist()
        if callback is None or key_path is None or file_path is None or task_id is None:
            raise Exception("[Error] invalid input for download_file")

        enqueue_time = datetime.utcnow()
        msg = f"[V2 Download image for file] enqueue"
        logger.log(msg=msg, task_id=task_id, key=key_path, enqueue_time=enqueue_time, status="running")

        data = {
            'key': key_path,
            'path': file_path,
            'enq_time': datetime.utcnow(),
            'tsk_id': task_id,
            'callback': callback
        }
        self.download_queue.put(edict(data))

    def _check_download_queue_exist(self):
        if self.download_queue is None:
            raise Exception("[Error] download queue doesn't exist! Should init S3AccessV2 first!")

    def terminate(self):
        for t in self.upload_thread_list:
            t.stop()
        for t in self.download_thread_list:
            t.stop()
        for t in self.upload_thread_list:
            t.join()
        for t in self.download_thread_list:
            t.join()

    def get_urls_for_file_keys(self, file_keys, presign_url_exp=None, presign_url_aug_params=None):
        """
        Get available urls for the file_keys.
        :param file_keys: List of file keys. File key can be:
            1. A URL (http:// or https://) provided by client directly.
                In this case, we can not control its expiration. Just return it directly.
            2. A S3 object key, which is uploaded by client using server file.getReqUrl API first.
                In this case, we will generate a presigned download URL for it, with expiration time presign_url_exp seconds.
        :param presign_url_exp: Expiration time in seconds for presigned URL. If None, the s3 default expiration time (3600 seconds) will be used.
        :param presign_url_aug_params: Augmentation parameters for presigned URL, e.g. {'ResponseContentType': 'image/jpeg'}.
            The underlying operation is S3 GetObject, so the aug_params can be parameter supported by S3 GetObject API, except 'Bucket' and 'Key' which are handled internally.
            ref: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/get_object.html
        :return: List of available URLs for the file keys.
        """
        urls = []
        for file_key in file_keys:
            url = self.get_url_for_file_key(file_key, presign_url_exp, presign_url_aug_params)
            urls.append(url)
        return urls

    def get_url_for_file_key(self, file_key, presign_url_exp=None, presign_url_aug_params=None):
        """
        Get available url for the file_key.
        :param file_key: A file key. File key can be:
            1. A URL (http:// or https://) provided by client directly.
                In this case, we can not control its expiration. Just return it directly.
            2. A S3 object key, which is uploaded by client using server file.getReqUrl API first.
                In this case, we will generate a presigned download URL for it, with expiration time presign_url_exp seconds.
        :param presign_url_exp: Expiration time in seconds for presigned URL. If None, the s3 default expiration time (3600 seconds) will be used.
        :param presign_url_aug_params: Augmentation parameters for presigned URL, e.g. {'ResponseContentType': 'image/jpeg'}.
            The underlying operation is S3 GetObject, so the aug_params can be parameter supported by S3 GetObject API, except 'Bucket' and 'Key' which are handled internally.
            ref: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/get_object.html
        :return: Available URL for the file key.
        """
        if self.s3_client_proxy.is_url_file_key(file_key):
            return file_key
        return self._get_presign_download_url(file_key, presign_url_exp, presign_url_aug_params)

    def _get_presign_download_url(self, file_key, expiration=None, aug_params=None):
        bucket = get_bucket_name(file_key)
        return self.s3_client_proxy.s3Client.generate_presigned_url(
            'get_object',
            Params={**(aug_params if aug_params else {}), 'Bucket': bucket, 'Key': file_key},
            ExpiresIn=expiration
        )
