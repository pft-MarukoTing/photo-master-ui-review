import mimetypes
import re
import shutil
import traceback
from urllib.parse import urlparse

import boto3
import boto3.session
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

import time
import datetime
import os

from io import BytesIO
import numpy as np
import cv2
from requests import RequestException, HTTPError

from . import common
from .common import is_empty_str
from .exceptions import FileTooLargeException, FileSizeNotAvailableException, FileNotFoundException
from .logger import logger

# import logging
#
# boto3.set_stream_logger('botocore', logging.DEBUG) # Enable this statement to ensure Accelerate is enabled

# Init boto3 sdk first. Not cost time in the duration of S3Access
boto3.client('s3')

from io import BufferedReader
class NonCloseableBufferedReader(BufferedReader):
    def close(self):
        self.flush()
dynamic_bucket_map = None  # file key prefix to bucket name mapping. e.g. 'jp/' -> 'yce-jp'
dynamic_region_map = None  # file key prefix to region name mapping. e.g. 'jp/' -> 'ap-northeast-1'
bucket_name = os.environ['S3_BUCKET']
region_name = os.environ['S3_REGION']

# Should only insert, and also should only insert when fetch job, so we do not add lock to protect it for thread-safety.
_bucket_map_from_job_info = {}

def update_bucket_map(bucket_map_setting):
    if is_empty_str(bucket_map_setting):
        return
    global _bucket_map_from_job_info

    # e.g. bucket_map_setting = "jp:yce-jp,us:yce-us,eu:yce-eu"
    for setting in bucket_map_setting.split(","):
        tokens = setting.split(":")
        if len(tokens) == 2:
            file_key_prefix = tokens[0].strip() + "/"
            bucket = tokens[1].strip()
            if file_key_prefix not in _bucket_map_from_job_info: # only insert
                _bucket_map_from_job_info[file_key_prefix] = bucket

def get_bucket_name_from_bucket_map(file_key):
    # global _bucket_map_from_job_info
    # Do not iterate '_bucket_map_from_job_info' directly to prevent concurrency issue
    keys = list(_bucket_map_from_job_info.keys())
    for prefix in keys:
        if file_key.startswith(prefix):
            return _bucket_map_from_job_info[prefix]
    return None


def get_bucket_name(file_key):
    global dynamic_bucket_map
    global bucket_name

    # Use _bucket_map_from_job_info
    bucket_from_job_info = get_bucket_name_from_bucket_map(file_key)
    if bucket_from_job_info is not None:
        return bucket_from_job_info

    # Use configured bucket map in environment variable
    dynamic_bucket_suffixes = common.get_property(os.environ, 'S3_DYNAMIC_BUCKET_SUFFIXES')
    if dynamic_bucket_map is None:
        dynamic_bucket_map = {}
        if dynamic_bucket_suffixes is not None:
            for suffix in dynamic_bucket_suffixes.split(","):
                stripped_suffix = suffix.strip()
                dynamic_bucket_map[stripped_suffix + "/"] = "yce-" + stripped_suffix
    for suffix, bucket in dynamic_bucket_map.items():
        if file_key.startswith(suffix):
            return bucket

    # Default
    return bucket_name


def get_bucket_name_and_region(file_key):
    global dynamic_bucket_map
    global dynamic_region_map

    if dynamic_bucket_map is None:
        # Note: S3_DYNAMIC_BUCKET_SUFFIXES contains the bucket name's 'suffix',
        # and the complete bucket name is 'yce-' + suffix.
        # But it is also the 'prefix' of the file key.
        dynamic_bucket_suffixes = common.get_property(os.environ, 'S3_DYNAMIC_BUCKET_SUFFIXES')

        dynamic_bucket_map = {}
        if dynamic_bucket_suffixes is not None:
            for suffix in dynamic_bucket_suffixes.split(","):
                stripped_suffix = suffix.strip()
                dynamic_bucket_map[stripped_suffix + "/"] = "yce-" + stripped_suffix

    if dynamic_region_map is None:
        dynamic_region_map_env_var = common.get_property(os.environ, 'S3_DYNAMIC_REGION_MAP')

        dynamic_region_map = {}
        if dynamic_region_map_env_var is not None:
            for region_mapping in dynamic_region_map_env_var.split(","):
                region_mapping_tokens = region_mapping.split(":")
                if len(region_mapping_tokens) == 2:
                    dynamic_region_map[region_mapping_tokens[0] + "/"] = region_mapping_tokens[1]

    bucket = bucket_name
    region = region_name
    for suffix, buck in dynamic_bucket_map.items():
        if file_key.startswith(suffix):
            bucket = buck
            break

    for suffix, reg in dynamic_region_map.items():
        if file_key.startswith(suffix):
            region = reg
            break

    return bucket, region


def format_timestamp_to_prefix(time_stamp):
    time_tokens = time_stamp.split('_')
    _y_m_d = time_tokens[0]
    _h = time_tokens[1]
    _m = '%02d' % ((int(time_tokens[2])//15)*15)
    prefix = _y_m_d + _h +_m

    return prefix


def read_image(image_stream, max_size=4096):
    import PIL
    from PIL import Image, ImageCms, UnidentifiedImageError
    img = None
    try:
        with Image.open(image_stream) as pil_image:
            exif = pil_image.getexif()

            if pil_image.info is not None and pil_image.mode is not None and 'RGB' in pil_image.mode and pil_image.info.get('icc_profile'):
                icc = pil_image.info.get('icc_profile')
                icc_file = BytesIO(icc)
                icc_profile = ImageCms.ImageCmsProfile(icc_file)
                srgb_profile = ImageCms.createProfile('sRGB')
                image = ImageCms.profileToProfile(pil_image, icc_profile, srgb_profile)
                if image is not None:
                    pil_image = image

            if isinstance(pil_image, PIL.PngImagePlugin.PngImageFile):
                pixels = None
            elif isinstance(pil_image, PIL.GifImagePlugin.GifImageFile):
                pil_image.seek(0)
                pixels = np.array(pil_image.convert('RGB'))
            else:
                pixels = np.array(pil_image)

            if pixels is not None and len(pixels.shape) == 2:
                pixels = np.stack((pixels,) * 3, axis=-1)

            if pixels is not None and len(pixels.shape) == 3:
                if pixels.shape[2] == 4:
                    img = np.dstack((pixels[:, :, [2, 1, 0]], pixels[:, :, 3]))
                elif pixels.shape[2] == 3:
                    img = pixels[:, :, [2, 1, 0]]

        if img is None:
            image_stream.seek(0)
            file_bytes = np.asarray(bytearray(image_stream.read()), dtype=np.uint8)
            pixels = cv2.imdecode(file_bytes, cv2.IMREAD_UNCHANGED)
            if pixels is not None and len(pixels.shape) == 3 and (pixels.shape[2] == 3 or pixels.shape[2] == 4):
                img = pixels

    except UnidentifiedImageError as e:
        return None

    if img is not None and (img.shape[0] > max_size or img.shape[1] > max_size):
        scale = max_size / max(img.shape[0], img.shape[1])
        img = cv2.resize(img, (round(img.shape[1] * scale), round(img.shape[0] * scale)))

    return img

def guess_content_type_by_file_path(file_path):
    """Guesses the MIME type based on the file path/extension."""
    no_query_string = file_path.split('?', 1)[0]
    mime_type, _ = mimetypes.guess_type(no_query_string)
    return mime_type

class s3proxy():
    def __init__(self, access_key, secret_key, bucket_name, testbed_mode=None, use_accelerate=False, use_ssl=False):
        # print("[S3 init]", flush=True)
        self.ACCESS_KEY = access_key
        self.SECRET_KEY = secret_key
        self.BUCKET_NAME = bucket_name
        self.session = boto3.session.Session()
        # connect to s3
        s3 = self.session.resource('s3', aws_access_key_id=self.ACCESS_KEY,
                                   aws_secret_access_key=self.SECRET_KEY,
                                   config=Config(s3={'use_accelerate_endpoint': use_accelerate}),
                                   use_ssl=use_ssl)
                             #  region_name=self.config.s3Region)
        self.bucket = s3.Bucket(self.BUCKET_NAME)
        self.testbed_mode = testbed_mode


    def upload_file(self, file_data, file_key, file_type, retry=5):
        """Upload a file to an S3 bucket

        :param file_name: File to upload
        :param bucket: Bucket to upload to
        :param object_name: S3 object name. If not specified then file_name is used
        :return: True if file was uploaded, else False
        """
        for i in range(0, retry):
            try:
                now = datetime.datetime.now()
                expires = now + datetime.timedelta(minutes=5)

                content_type = 'application/octet-stream'
                if file_type == '.jpg':
                    content_type = 'image/jpeg'
                elif file_type == '.png':
                    content_type = 'image/png'
                #with open(file_name, 'rb') as f:
                object_upload = self.bucket.Object(file_key)
                extraArgs={}
                if self.testbed_mode=="true":
                    extraArgs={
                        'ACL': 'public-read',
                        'ContentType': content_type,
                        # https://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.21
                        'Expires': expires, # no longer cacheable   
                    }
                    ## workaround boto s3 bug, ref: https://github.com/boto/s3transfer/issues/80#issuecomment-482534256
                buffer = NonCloseableBufferedReader(file_data)
                object_upload.upload_fileobj(buffer, ExtraArgs=extraArgs)
                # object_upload.upload_fileobj(buffer)
                buffer.detach()
                t3 = time.time()
                #print ("{} s3 upload time={:.3f}".format(datetime.datetime.utcnow(), t3-t2))

                return True

            except ClientError as e:
                if i == retry - 1:
                    raise e
                pass

            time.sleep(0.5)


    def download_fileobj(self, key):
        data = BytesIO()
        # self.s3_client.download_fileobj(self.BUCKET_NAME, key, data)
        self.bucket.download_fileobj(key, data)

        input = read_image(data, max_size=4096)
        return input

    def download_file(self, key, path):
        self.bucket.download_file(key, path)
        return path


    def delete_object(self, prefix, retry=5):
        for i in range(0, retry):
            try:
                # Here we create a new session per thread
                session = boto3.session.Session()
                # connect to s3
                s3 = session.resource('s3', aws_access_key_id=self.ACCESS_KEY,
                                aws_secret_access_key=self.SECRET_KEY)
                bucket = s3.Bucket(self.BUCKET_NAME)

                # delete objects
                for obj in bucket.objects.filter(Prefix=prefix):
                    s3.Object(bucket.name, obj.key).delete()

                return True

            except ClientError as e:
                pass

            time.sleep(0.5)

    def list_object(self, prefix, retry=5):
        for i in range(0, retry):
            try:
                obj_list = []
                # Here we create a new session per thread
                client = boto3.client('s3', aws_access_key_id=self.ACCESS_KEY,
                            aws_secret_access_key=self.SECRET_KEY)
        
                paginator = client.get_paginator('list_objects_v2')
                for result in paginator.paginate(Bucket=self.BUCKET_NAME, Prefix=prefix, Delimiter='/'):
                #result = paginator.paginate(Bucket=self.BUCKET_NAME, Prefix=prefix, Delimiter='/')
                    #for prefix in result.search('CommonPrefixes'):
                    for prefix in result.get('CommonPrefixes', []):
                        #print(prefix.get('Prefix'))
                        obj_list.append(prefix.get('Prefix'))
                return obj_list

            except ClientError as e:
                pass

            time.sleep(0.5)
        return []
    
    def get_content_length(self, key):
        client = boto3.client('s3', aws_access_key_id=self.ACCESS_KEY,
                              aws_secret_access_key=self.SECRET_KEY)
        metadata = client.head_object(Bucket=self.BUCKET_NAME, Key=key)
        size = metadata['ContentLength']
        return size


class s3ClientProxy():
    def __init__(self, access_key, secret_key, default_bucket, testbed_mode=None, use_accelerate=False, region=None,
                 use_ssl=False, endpoint_url=None, custom_config=None, local_storage_root=None,
                 url_download_max_bytes=None, download_file_cache_enabled=True):
        self.default_bucket = default_bucket
        if custom_config is None:
            custom_config = {}
        custom_config['use_accelerate_endpoint'] = use_accelerate
        self.s3Client = boto3.client('s3', aws_access_key_id=access_key,
                                     aws_secret_access_key=secret_key,
                                     endpoint_url=endpoint_url,
                                     config=Config(s3=custom_config),
                                     region_name=region,
                                     use_ssl=use_ssl)
        self.testbed_mode = testbed_mode
        self.local_storage_root = local_storage_root
        self.url_download_max_bytes = url_download_max_bytes
        self.download_file_cache_enabled = download_file_cache_enabled
        self.msg_prefix = "[S3ClientProxy]"
        logger.log(f"{self.msg_prefix} init done", local_storage_enabled=self._local_storage_enabled(),
                   local_storage_root=local_storage_root, download_file_cache_enabled=download_file_cache_enabled)

    def upload_file(self, file_data, file_key, file_type, bucket=None, retry=5, task_id=None, content_type=None):
        file_key_info = {}
        for i in range(0, retry):
            try:
                before_upload_time = datetime.datetime.utcnow()
                now = datetime.datetime.now()
                expires = now + datetime.timedelta(minutes=5)

                extraArgs={}
                if content_type is not None:
                    extraArgs['ContentType'] = content_type
                if self.testbed_mode=="true":
                    extraArgs={
                        'ACL': 'public-read',
                        'ContentType': content_type,
                        # https://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.21
                        'Expires': expires, # no longer cacheable
                    }
                bucket_to_use = bucket if bucket is not None else self.default_bucket

                ## workaround boto s3 bug, ref: https://github.com/boto/s3transfer/issues/80#issuecomment-482534256
                buffer = NonCloseableBufferedReader(file_data)
                self.s3Client.put_object(Body=buffer, Key=file_key, Bucket=bucket_to_use, **extraArgs)
                buffer.detach()

                try:
                    file_key_info = self._check_upload_file_data_info(file_data)
                    handled_file_size = file_key_info.get("size")
                    after_upload_time = datetime.datetime.utcnow()
                    logger.log(msg=f"{self.msg_prefix} upload_file - success", task_id=task_id, key=file_key,
                               bucket=bucket,
                               status="running", start_time=before_upload_time, end_time=after_upload_time,
                               handled_file_size=handled_file_size, file_key_info=file_key_info)
                except Exception as e:
                    # Do not break the flow if logging fails
                    logger.log(f"{self.msg_prefix} upload_file - log upload success error", task_id=task_id,
                               key=file_key, bucket=bucket, status="running",
                               error=f"{e}", error_traceback=traceback.format_exc())

                return {"file_key_info": file_key_info}

            except ClientError as e:
                if i == retry - 1:
                    raise e
                pass

            time.sleep(0.5)

    def download_fileobj(self, key, bucket=None, task_id=None, get_map_result=False):
        before_download_time = datetime.datetime.utcnow()

        download_result = self._download_file_key(key, bucket)
        file_key_info = download_result["file_key_info"]
        file_data = download_result["file_data"]
        handled_file_size = download_result["handled_file_size"]
        fileobj = read_image(BytesIO(file_data), max_size=4096)

        after_download_time = datetime.datetime.utcnow()
        logger.log(msg=f"{self.msg_prefix} download_fileobj - success", task_id=task_id, key=key, bucket=bucket,
                   status="running", start_time=before_download_time, end_time=after_download_time,
                   handled_file_size=handled_file_size,
                   from_local=file_key_info["exists"], local_path=file_key_info["local_path"], file_key_info=file_key_info)
        if get_map_result:
            return {"file_content": fileobj, "file_key_info": file_key_info}
        return fileobj

    def download_file_binary(self, key, bucket=None, task_id=None, get_map_result=False):
        before_download_time = datetime.datetime.utcnow()

        download_result = self._download_file_key(key, bucket)
        file_key_info = download_result["file_key_info"]
        file_content = download_result["file_data"]
        handled_file_size = download_result["handled_file_size"]

        after_download_time = datetime.datetime.utcnow()
        logger.log(msg=f"{self.msg_prefix} download_file_binary - success", task_id=task_id, key=key, bucket=bucket,
                   status="running", start_time=before_download_time, end_time=after_download_time,
                   handled_file_size=handled_file_size,
                   from_local=file_key_info["exists"], local_path=file_key_info["local_path"], file_key_info=file_key_info)
        if get_map_result:
            return {"file_content": file_content, "file_key_info": file_key_info}
        return file_content

    def download_file(self, key, path, bucket=None, task_id=None, get_map_result=False):
        before_download_time = datetime.datetime.utcnow()

        download_result = self._download_file_key(key, bucket, target_path=path)
        file_key_info = download_result["file_key_info"]
        handled_file_size = download_result["handled_file_size"]

        after_download_time = datetime.datetime.utcnow()
        logger.log(msg=f"{self.msg_prefix} download_file - success", task_id=task_id, key=key, bucket=bucket,
                   status="running", start_time=before_download_time, end_time=after_download_time,
                   handled_file_size=handled_file_size,
                   from_local=file_key_info["exists"], local_path=file_key_info["local_path"], file_key_info=file_key_info)
        if get_map_result:
            return {"file_path": path, "file_key_info": file_key_info}
        return path

    def _check_file_key_info(self, key):
        # Check file key information
        # - is_url: is it a URL?
        #   Will download from URL or from s3 if file not exists in local storage
        # - exists: is it already in local storage?
        #   Read from local storage if exists
        # - local_path: The local path of the file
        #   If ls_enabled, always return the local path no matter the file exists or not
        # - ls_enabled: is local storage enabled?
        #   If enabled and file not exists and download_file_cache_enabled, will download to local storage
        # - others: for debug purpose
        # Return: {"is_url": bool, "exists": bool, "local_path": str, "ls_enabled": bool}
        is_url = self.is_url_file_key(key)
        ls_enabled = self._local_storage_enabled()
        local_path = None
        exists = False
        if ls_enabled:
            if is_url:
                relative_path = self._url_to_local_path(key)
                local_path = os.path.join(self.local_storage_root, relative_path)
            else:
                local_path = os.path.join(self.local_storage_root, key)
            exists = os.path.exists(local_path)

        size = None
        create_time = None
        modify_time = None
        try:
            check_more_file_stat = exists and local_path is not None
            size = os.path.getsize(local_path) if check_more_file_stat else None
            create_time_ts = os.path.getctime(local_path) if check_more_file_stat else None
            create_time = datetime.datetime.fromtimestamp(create_time_ts).isoformat() if create_time_ts is not None else None
            modify_time_ts = os.path.getmtime(local_path) if check_more_file_stat else None
            modify_time = datetime.datetime.fromtimestamp(modify_time_ts).isoformat() if modify_time_ts is not None else None
        except Exception as e:
            logger.log(f"{self.msg_prefix} _check_file_key_info - get file stat error", key=key, local_path=local_path,
                       error=f"{e}", error_traceback=traceback.format_exc())
        return {"is_url": is_url, "exists": exists, "local_path": local_path, "ls_enabled": ls_enabled,
                "size": size, "create_time": create_time, "modify_time": modify_time}

    def _check_upload_file_data_info(self, file_data):
        size = None
        try:
            size = file_data.getbuffer().nbytes
        except Exception as e:
            logger.log(f"{self.msg_prefix} _check_upload_file_data_info - get file data size error",
                       error=f"{e}", error_traceback=traceback.format_exc())
        return {"size": size}

    def _local_storage_enabled(self):
        return not is_empty_str(self.local_storage_root)

    @staticmethod
    def is_url_file_key(file_key):
        return file_key.startswith("http://") or file_key.startswith("https://")

    @staticmethod
    def _url_to_local_path(url):
        # url -> safe local path
        # First remove the scheme & query string
        # Then replace all non-alphanumeric characters with '_'
        # Each string split by '/' is treated as a folder
        # Sample:
        # - url: https://yce-us-cdn.perfectcorp.com/demo/static/ai_server_util_test/sample.jpg?xxx&ooo
        # - local path: yce-us-cdn.perfectcorp.com/demo/static/ai_server_util_test/sample.jpg
        parsed_url = urlparse(url)
        path = parsed_url.netloc + parsed_url.path
        safe_path = re.sub(r'[^a-zA-Z0-9./_-]', '_', path)
        safe_path = safe_path.strip('/')
        return safe_path

    def _download_from_url(self, url, file_path=None, task_id=None):
        """
        Download from URL to buffer (if file_path is None) or to file (if file_path is provided).
        Checks file size before and during download using url_download_max_bytes.
        """
        try:
            max_bytes = self.url_download_max_bytes

            response = requests.get(url, stream=True, timeout=(10, 60))
            response.raise_for_status()

            # Check Content-Length from GET response if available
            content_length = response.headers.get('Content-Length')
            if content_length is not None and max_bytes is not None:
                if int(content_length) > max_bytes:
                    response.close()  # Close the connection since we won't use it
                    raise FileTooLargeException(f"file too large checked by Content-Length",
                                                file_size=int(content_length), limit_size=max_bytes)

            downloaded_size = 0

            if file_path is not None:
                # Download to file
                with open(file_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=4096):
                        if chunk:
                            downloaded_size += len(chunk)
                            if max_bytes is not None and downloaded_size > max_bytes:
                                raise FileTooLargeException(f"file too large during download",
                                                            file_size=downloaded_size, limit_size=max_bytes)
                            file.write(chunk)
                return None  # File written to disk
            else:
                # Download to buffer
                data = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    if chunk:
                        downloaded_size += len(chunk)
                        if max_bytes is not None and downloaded_size > max_bytes:
                            raise FileTooLargeException(f"file too large during download",
                                                        file_size=downloaded_size, limit_size=max_bytes)
                        data.extend(chunk)
                return bytes(data)

        except Exception as e:
            # Clean up incomplete file if downloading to file
            if file_path is not None and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

            logger.log(f"{self.msg_prefix} _download_from_url - download error", task_id=task_id,
                       url=url, file_path=file_path, status="running", error=f"{e}", error_traceback=traceback.format_exc())
            raise

    def _download_file_key(self, key, bucket=None, target_path=None, task_id=None):
        file_key_info = self._check_file_key_info(key)
        if target_path is not None:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
        file_data = None
        if file_key_info["exists"]:
            if target_path is not None:
                # Copy local file to the specified path
                shutil.copy(file_key_info["local_path"], target_path)
            else:
                with open(file_key_info["local_path"], "rb") as f:
                    file_data = f.read()

        # Not exists in local storage or local storage not enabled
        elif file_key_info["is_url"]:
            file_data = self._download_from_url(key, file_path=target_path, task_id=task_id)
        else:
            bucket_to_use = bucket if bucket is not None else self.default_bucket
            if target_path is not None:
                # Download from s3 to the specified path
                self.s3Client.download_file(Bucket=bucket_to_use, Key=key, Filename=target_path)
            else:
                response = self.s3Client.get_object(Bucket=bucket_to_use, Key=key)
                file_data = response['Body'].read()

        if self.download_file_cache_enabled and file_key_info["ls_enabled"] and not file_key_info["exists"]:
            try:
                os.makedirs(os.path.dirname(file_key_info["local_path"]), exist_ok=True)
                if target_path is not None:
                    shutil.copy(target_path, file_key_info["local_path"])
                else:
                    with open(file_key_info["local_path"], "wb") as f:
                        f.write(file_data)
            except Exception as e:
                logger.log(f"{self.msg_prefix} _download_file_key - save to local storage error", task_id=task_id,
                           key=key, bucket=bucket, status="running", file_key_info=file_key_info,
                           error=f"{e}", error_traceback=traceback.format_exc())

        handled_file_size = len(file_data) if file_data is not None else None
        if target_path is not None:
            try:
                handled_file_size = os.path.getsize(target_path)
            except Exception as e:  # Should not happen
                logger.log(f"{self.msg_prefix} _download_file_key - get downloaded file size error", task_id=task_id,
                           key=key, bucket=bucket, target_path=target_path,
                           error=f"{e}", error_traceback=traceback.format_exc())

        if file_key_info.get("size") is None:
            file_key_info["size"] = handled_file_size

        return {"file_key_info": file_key_info, "file_data": file_data, "handled_file_size": handled_file_size}

    def get_file_size(self, key, bucket=None, task_id=None):
        is_url = self.is_url_file_key(key)
        try:
            if is_url:
                try:
                    response = requests.head(key, allow_redirects=True, timeout=10)
                    response.raise_for_status()
                    content_length = response.headers.get('Content-Length')
                    if content_length is not None:
                        return int(content_length)
                    # Fallback to GET method if HEAD does not provide Content-Length
                except (HTTPError, RequestException):
                    pass  # Fallback to GET method

                try:
                    headers = {"Range": f"bytes=0-0"}
                    response = requests.get(key, allow_redirects=True, timeout=10, headers=headers, stream=True)
                    response.close()  # We do not need the body
                    response.raise_for_status()
                    content_range = response.headers.get('Content-Range')
                    if response.status_code == 206 and content_range is not None:
                        # Content-Range: bytes 0-0/<file_size>
                        match = re.match(r'bytes \d+-\d+/(\d+)', content_range)
                        if match:
                            return int(match.group(1))
                    elif response.status_code == 200:
                        content_length = response.headers.get('Content-Length')
                        if content_length is not None:
                            return int(content_length)
                    raise FileSizeNotAvailableException(key=key)
                except FileSizeNotAvailableException:
                    raise
                except (HTTPError, RequestException) as e:
                    # The url GET request failed
                    raise FileNotFoundException(key=key, cause=e)
            else: # file key
                bucket_to_use = bucket if bucket is not None else self.default_bucket
                try:
                    metadata = self.s3Client.head_object(Bucket=bucket_to_use, Key=key)
                    size = metadata.get('ContentLength')
                    if size is not None:
                        return size
                    else: # Should not happen
                        raise FileSizeNotAvailableException(key=key, bucket=bucket_to_use)
                except ClientError as e:
                    raise FileNotFoundException(key=key, bucket=bucket_to_use, cause=e)
        except (FileNotFoundException, FileSizeNotAvailableException) as e:
            logger.log(f"{self.msg_prefix} get_file_size - error", task_id=task_id, key=key, bucket=bucket,
                       status="running", error=f"{e}", error_traceback=traceback.format_exc())
            raise
        except Exception as e:
            logger.log(f"{self.msg_prefix} get_file_size - unexpected error", task_id=task_id, key=key, bucket=bucket,
                       status="running", error=f"{e}", error_traceback=traceback.format_exc())
            raise
