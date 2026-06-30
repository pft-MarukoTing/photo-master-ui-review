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
        file_handler.setLevel(logging.ERROR)

        file_formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

    def debug(self, task_id, message):
        self.logger.debug(message, extra={'task_id': task_id})

    def info(self, task_id, message):
        self.logger.info(message, extra={'task_id': task_id})

    def warning(self, task_id, message):
        self.logger.warning(message, extra={'task_id': task_id})

    def error(self, task_id, message):
        self.logger.error(message, extra={'task_id': task_id})

    def critical(self, task_id, message):
        self.logger.critical(message, extra={'task_id': task_id})
