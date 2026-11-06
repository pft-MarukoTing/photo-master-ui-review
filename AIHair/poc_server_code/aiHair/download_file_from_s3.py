import load_env

import os
from aiserver_utils.s3 import S3Access
from aiserver_utils.s3_utils import s3proxy
from sys import argv

s3_accessor = S3Access()
MONITOR_DST_DIR = './tmp'

def download_file(filepath):
    backend_downloaddir = os.path.join(MONITOR_DST_DIR, 'backend_download')
    os.makedirs(backend_downloaddir, exist_ok=True)

    s3config = s3_accessor.s3config
    s3downloader = s3proxy(s3config.access_key, s3config.secret_key, s3config.bucket_name, s3config.testbed_mode, s3config.use_ta)
    try:
        downloaded_path = s3downloader.download_file(filepath, os.path.join(backend_downloaddir, os.path.split(filepath)[-1]))
        if not downloaded_path:
            raise Exception('download failed')
        else:
            filepath = downloaded_path
    except Exception as e:
        print('[download_file] file %s not found on s3: %s' % (filepath, str(e)))

    print(f"download_file done for {filepath}")

filepath = argv[1]
download_file(filepath)