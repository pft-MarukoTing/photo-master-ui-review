import time
import os


def is_empty_str(s):
    return s is None or str(s).strip() == ""


def get_property(env, key_or_key_list, default=None, allow_empty=False):
    if not isinstance(key_or_key_list, list):
        if _is_valid_key(env, key_or_key_list, allow_empty):
            return env[key_or_key_list]
        else:
            return default

    for key in key_or_key_list:
        if _is_valid_key(env, key, allow_empty):
            return env[key]
    return default


def _is_valid_key(env, key, allow_empty):
    return key in env and env[key] is not None and (len(env[key]) != 0 or allow_empty)


def current_milli_timestamp():
    return int(time.time() * 1000)

def string_to_dict(input_string):
    if input_string is None:
        return None
    pairs = input_string.split(',')
    
    result = {}
    for pair in pairs:
        key, value = pair.split('=')
        result[key] = value
    
    return result


def get_hostname_with_process_id():
    return f"{os.environ.get('HOSTNAME', '')}_{os.getpid()}"


def get_hostname():
    return os.environ.get('HOSTNAME', '')


def yce_epoc_to_unix(yce_epoc):
    # YCE epoch starts from 2000-01-01 00:00:00 UTC
    YCE_EPOC = 1669593600000; # 2022-11-28T00:00:00 GMT
    return yce_epoc + YCE_EPOC
