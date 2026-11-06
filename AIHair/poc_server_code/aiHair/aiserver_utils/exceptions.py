class BaseException(Exception):
    def __init__(self, message="base exception", cause=None, *args, **kwargs):
        super().__init__(message)
        self.cause = cause



class FileTooLargeException(BaseException):
    def __init__(self, message="file too large", file_size=None, limit_size=None):
        super().__init__(message)
        self.file_size = file_size
        self.limit_size = limit_size

class FileNotFoundException(BaseException):
    def __init__(self, message="file not found", key=None, bucket=None, cause=None):
        super().__init__(message, cause)
        self.key = key
        self.bucket = bucket

class FileSizeNotAvailableException    (BaseException):
    def __init__(self, message="file size not available", key=None, bucket=None, cause=None):
        super().__init__(message, cause)
        self.key = key
        self.bucket = bucket
