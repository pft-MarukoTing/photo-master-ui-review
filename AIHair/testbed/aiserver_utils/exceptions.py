class BaseException(Exception):
    pass

class FileTooLargeException(BaseException):
    def __init__(self, message="file too large", file_size=None, limit_size=None):
        super().__init__(message)
        self.file_size = file_size
        self.limit_size = limit_size
