import sys
import logging
import time

class ServerLogger:

    def __init__(self):
        self.logger = logging.getLogger('server_logger')

    def initialize(self):
        self.logger.setLevel(logging.DEBUG)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_formatter = logging.Formatter(
            '{"time":"%(asctime)s", "level": "%(levelname)s", "task_id": "%(task_id)s", "message": "%(message)s"}',
            '%Y-%m-%dT%H:%M:%SZ'
        )
        stream_formatter.converter = time.gmtime
        stream_handler.setFormatter(stream_formatter)
        self.logger.addHandler(stream_handler)

        file_handler = logging.FileHandler('server_error.log')
        file_handler.setLevel(logging.ERROR)  # 只记录 ERROR 级别及以上的日志消息

        file_formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

    def debug(self, task_id, message):
        d = {'task_id': task_id}
        self.logger.debug(message, extra=d)

    def info(self, task_id, message):
        d = {'task_id': task_id}
        self.logger.info(message, extra=d)

    def warning(self, task_id, message):
        d = {'task_id': task_id}
        self.logger.warning(message, extra=d)

    def error(self, task_id, message):
        d = {'task_id': task_id}
        self.logger.error(message, extra=d)

    def critical(self, task_id, message):
        d = {'task_id': task_id}
        self.logger.critical(message, extra=d)