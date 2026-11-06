import load_env
import os
import time
from sys import argv
from aiserver_utils.s3_utils import s3ClientProxy
from aiserver_utils.aescipher import AESCipher
from aiserver_utils.common import get_property

def get_bucket_name(file_key):
    """Get bucket name based on file key prefix"""
    bucket_name = os.environ['S3_BUCKET']
    dynamic_bucket_suffixes = get_property(os.environ, 'S3_DYNAMIC_BUCKET_SUFFIXES')
    
    if dynamic_bucket_suffixes is not None:
        for suffix in dynamic_bucket_suffixes.split(","):
            stripped_suffix = suffix.strip()
            if file_key.startswith(stripped_suffix + "/"):
                return "yce-" + stripped_suffix
    
    return bucket_name

def upload_file(filepath, dstKey):
    """Simple synchronous upload without threads"""
    print(f'[upload_file] Starting upload of {filepath} to S3: {dstKey}')
    
    # Get S3 configuration
    access_key = os.environ['AWS_ACCESS_KEY_ID']
    secret_key = AESCipher().decrypt(os.environ['AWS_SECRET_ACCESS_KEY'])
    bucket_name = get_bucket_name(dstKey)
    s3_region = os.environ['S3_REGION']
    use_ta = (os.getenv('S3_USE_TRANSFER_ACCELERATION') is not None
              and os.getenv('S3_USE_TRANSFER_ACCELERATION').lower() == "true")
    use_ssl = (not os.environ.get("S3_USE_SSL") or os.environ.get("S3_USE_SSL").lower() != "false")
    endpoint_url = os.getenv('S3_ENDPOINT_URL')
    
    # Create S3 client
    s3_client = s3ClientProxy(
        access_key=access_key,
        secret_key=secret_key,
        default_bucket=bucket_name,
        testbed_mode=False,
        use_accelerate=use_ta,
        region=s3_region,
        use_ssl=use_ssl,
        endpoint_url=endpoint_url
    )
    
    # Read file and upload
    start_time = time.time()
    try:
        with open(filepath, 'rb') as f:
            # Upload file directly with file object
            s3_client.upload_file(f, dstKey, None, bucket=bucket_name)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f'[upload_file] Successfully uploaded {filepath} to S3: {dstKey} (took {duration:.2f}s)')
        
    except Exception as e:
        print(f'[upload_file] Failed to upload {filepath} to S3: {str(e)}')
        raise e

if __name__ == "__main__":
    filepath = argv[1]
    s3_filepath = argv[2]
    try:
        upload_file(filepath, s3_filepath)
    except Exception as e:
        print(f'[upload_file] Failed to upload {filepath} to S3: {str(e)}')
        raise e