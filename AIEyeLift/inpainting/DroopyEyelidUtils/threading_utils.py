from . import file_utils, align_utils, util_wrinkle, process_utils
import threading
import time
import cv2
import numpy as np

class OutputJobProcessor(object):
	def __init__(self, quality=95):
		super(OutputJobProcessor, self).__init__()
		self.quality = quality

	def job(self, output_path, output_image):
		if output_path.endswith('.jpg'):
			cv2.imwrite(output_path, output_image, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
		else:
			cv2.imwrite(output_path, output_image)

	def process(self, args):
		output_path_list, output_image_list = args
		threads = []
		for output_path, output_image in zip(output_path_list, output_image_list):
			thread = threading.Thread(target=self.job, args=(output_path, output_image))
			threads.append(thread)
			thread.start()
		for thread in threads:
			thread.join()

