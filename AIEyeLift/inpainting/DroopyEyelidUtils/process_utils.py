from . import util_wrinkle, file_utils, align_utils, util
import numpy as np
import cv2
import argparse
import time
import copy
from sklearn.cluster import KMeans
import sys
import pandas as pd
from scipy.spatial import Delaunay
import math
from scipy.interpolate import interp1d
import os

SB_Face_LandmarkType = [
	'SB_LEFT_BROW_LEFT',
	'SB_LEFT_BROW_TOP',
	'SB_LEFT_BROW_RIGHT',
	'SB_LEFT_BROW_BOTTOM',
	'SB_LEFT_EYE_LEFT', # 4
	'SB_LEFT_EYE_TOP',
	'SB_LEFT_EYE_RIGHT',
	'SB_LEFT_EYE_BOTTOM',
	'SB_LEFT_EYE_CENTER',
	'SB_RIGHT_BROW_LEFT',
	'SB_RIGHT_BROW_TOP',
	'SB_RIGHT_BROW_RIGHT',
	'SB_RIGHT_BROW_BOTTOM',
	'SB_RIGHT_EYE_LEFT',
	'SB_RIGHT_EYE_TOP',
	'SB_RIGHT_EYE_RIGHT', # 15
	'SB_RIGHT_EYE_BOTTOM',
	'SB_RIGHT_EYE_CENTER',
	'SB_NOSE_LEFT', # 18
	'SB_NOSE_TOP',
	'SB_NOSE_RIGHT', # 20
	'SB_NOSE_BOTTOM',
	'SB_MOUTH_LEFT_CORNER',
	'SB_MOUTH_TOP_LIP_1',
	'SB_MOUTH_TOP_LIP_2',
	'SB_MOUTH_RIGHT_CORNER',
	'SB_MOUTH_BOTTOM_LIP_1',
	'SB_MOUTH_BOTTOM_LIP_2',
	'SB_CHIN',
	'SB_LEFT_EAR_TOP',
	'SB_LEFT_EAR_BOTTOM',
	'SB_LEFT_FACE_SHAPE_1',
	'SB_LEFT_FACE_SHAPE_2',
	'SB_RIGHT_EAR_TOP',
	'SB_RIGHT_EAR_BOTTOM',
	'SB_RIGHT_FACE_SHAPE_1',
	'SB_RIGHT_FACE_SHAPE_2',
	'SB_MOUTH_INTERP_TOP_RIGHT',
	'SB_MOUTH_INTERP_TOP_LEFT',
	'SB_MOUTH_INTERP_BOTTOM_RIGHT',
	'SB_MOUTH_INTERP_BOTTOM_LEFT',
	'SB_MOUTH_INTERP_INNER_RIGHT',
	'SB_MOUTH_INTERP_INNER_LEFT',
	'SB_MOUTH_INTERP_UPPER_LEFT',
	'SB_MOUTH_INTERP_UPPER_RIGHT',
	'SB_MOUTH_INTERP_LOWER_LEFT',
	'SB_MOUTH_INTERP_LOWER_RIGHT',
	'SB_NOSE_BRIDGE_TOP',
	'SB_FOREHEAD_MIDDLE',
	'SB_FOREHEAD_LEFT',
	'SB_FOREHEAD_RIGHT',
]

LBF3DLandmarkType = [
	'LBF_3D_LEFT_BROW_1', # left most
	'LBF_3D_LEFT_BROW_2',
	'LBF_3D_LEFT_BROW_3',
	'LBF_3D_LEFT_BROW_4',
	'LBF_3D_LEFT_BROW_5', # 4

	'LBF_3D_LEFT_EYE_OUTSIDE',
	'LBF_3D_LEFT_EYE_TOP',
	'LBF_3D_LEFT_EYE_INSIDE_OUT', # right most
	'LBF_3D_LEFT_EYE_BOTTOM',
	'LBF_3D_LEFT_EYE_INSIDE_IN',
	'LBF_3D_LEFT_EYE_CENTER',
	'LBF_3D_LEFT_EYE_M1', # left top (clockwise)
	'LBF_3D_LEFT_EYE_M2',
	'LBF_3D_LEFT_EYE_M3',
	'LBF_3D_LEFT_EYE_M4', # 14

	'LBF_3D_RIGHT_BROW_1', # left most
	'LBF_3D_RIGHT_BROW_2',
	'LBF_3D_RIGHT_BROW_3',
	'LBF_3D_RIGHT_BROW_4',
	'LBF_3D_RIGHT_BROW_5', # 19

	'LBF_3D_RIGHT_EYE_INSIDE_OUT', # left most
	'LBF_3D_RIGHT_EYE_TOP',
	'LBF_3D_RIGHT_EYE_OUTSIDE',
	'LBF_3D_RIGHT_EYE_BOTTOM',
	'LBF_3D_RIGHT_EYE_INSIDE_IN',
	'LBF_3D_RIGHT_EYE_CENTER',
	'LBF_3D_RIGHT_EYE_M1', # left top (clockwise)
	'LBF_3D_RIGHT_EYE_M2',
	'LBF_3D_RIGHT_EYE_M3',
	'LBF_3D_RIGHT_EYE_M4', # 29

	'LBF_3D_NOSE_RIDGE_1', # top
	'LBF_3D_NOSE_RIDGE_2',
	'LBF_3D_NOSE_RIDGE_3',
	'LBF_3D_NOSE_RIDGE_4', # tip
	'LBF_3D_NOSE_BOTTOM_1', # bottom left
	'LBF_3D_NOSE_BOTTOM_3', # bottom
	'LBF_3D_NOSE_BOTTOM_5', # bottom right
	'LBF_3D_NOSE_LEFT', # left most           
	'LBF_3D_NOSE_RIGHT', # 38             

	'LBF_3D_MOUTH_LEFT_OUTER_CORNER',
	'LBF_3D_MOUTH_TOP_LIP_TOP_2',
	'LBF_3D_MOUTH_TOP_LIP_TOP_3',
	'LBF_3D_MOUTH_TOP_LIP_TOP_4',
	'LBF_3D_MOUTH_RIGHT_OUTER_CORNER',
	'LBF_3D_MOUTH_TOP_LIP_TOP_1',
	'LBF_3D_MOUTH_TOP_LIP_TOP_5',
	'LBF_3D_MOUTH_TOP_LIP_TOP_M1',
	'LBF_3D_MOUTH_TOP_LIP_TOP_M2',
	'LBF_3D_MOUTH_TOP_LIP_TOP_M3',
	'LBF_3D_MOUTH_TOP_LIP_TOP_M4', # 49             

	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_2', # bottom
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_1', # bottom left
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_3', # bottom right
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M1', # left
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M2',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M3',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M4', # 56 

	'LBF_3D_MOUTH_LEFT_INNER_CORNER',
	'LBF_3D_MOUTH_TOP_LIP_BOTTOM',
	'LBF_3D_MOUTH_RIGHT_INNER_CORNER',
	'LBF_3D_MOUTH_TOP_LIP_BOTTOM_M1',
	'LBF_3D_MOUTH_TOP_LIP_BOTTOM_M2',
	'LBF_3D_MOUTH_BOTTOM_LIP_TOP',
	'LBF_3D_MOUTH_BOTTOM_LIP_TOP_M1',
	'LBF_3D_MOUTH_BOTTOM_LIP_TOP_M2', # 64 

	'LBF_3D_LEFT_FACE_SHAPE_1', # left top (anti-clockwise)
	'LBF_3D_LEFT_FACE_SHAPE_2',
	'LBF_3D_LEFT_FACE_SHAPE_3',
	'LBF_3D_LEFT_FACE_SHAPE_4',
	'LBF_3D_LEFT_FACE_SHAPE_5',
	'LBF_3D_LEFT_FACE_SHAPE_6',
	'LBF_3D_LEFT_FACE_SHAPE_7',
	'LBF_3D_LEFT_FACE_SHAPE_8',
	'LBF_3D_CHIN',
	'LBF_3D_RIGHT_FACE_SHAPE_1',
	'LBF_3D_RIGHT_FACE_SHAPE_2',
	'LBF_3D_RIGHT_FACE_SHAPE_3',
	'LBF_3D_RIGHT_FACE_SHAPE_4',
	'LBF_3D_RIGHT_FACE_SHAPE_5',
	'LBF_3D_RIGHT_FACE_SHAPE_6',
	'LBF_3D_RIGHT_FACE_SHAPE_7',
	'LBF_3D_RIGHT_FACE_SHAPE_8', # 81

	'LBF_3D_LEFT_BROW_BOTTOM_2',
	'LBF_3D_RIGHT_BROW_BOTTOM_2',  # 83

	'LBF_3D_LEFT_EYE_IRIS_LEFT',
	'LBF_3D_LEFT_EYE_IRIS_RIGHT',
	'LBF_3D_RIGHT_EYE_IRIS_LEFT',
	'LBF_3D_RIGHT_EYE_IRIS_RIGHT',  # 87

	'UNKNOWN_A',
	'UNKNOWN_B',
	'UNKNOWN_C',
	'UNKNOWN_D', # 91
]

SB_LANDMARK_AMOUNT = len(SB_Face_LandmarkType) # 51
LBF_3D_LANDMARK_AMOUNT = len(LBF3DLandmarkType) # 92
landmark51_tag2index = {tag: index for index, tag in enumerate(SB_Face_LandmarkType)}
landmark92_tag2index = {tag: index for index, tag in enumerate(LBF3DLandmarkType)}

RED = [0,0,255]
GREEN = [0,255,0]
BLUE = [255,0,0]
GRAY = [128,128,128]
WHITE = [255,255,255]
BLACK = [0,0,0]

INPAINTING_RATIO = 0.7
ONE_MINUS_INPAINTING_RATIO = 1.0 - INPAINTING_RATIO

def int_to_odd(integer):
	return integer + (1 - integer & 1)

def fast_median_filter(image, kernel_size, shrink_scale=2):
	image_height, image_width = image.shape[:2]
	resized_image = cv2.resize(image, (image_width // shrink_scale, image_height // shrink_scale), interpolation=cv2.INTER_AREA)
	kernel_shrink_scale = shrink_scale ** 0.5
	shrink_kernel_size = int_to_odd(int(kernel_size / kernel_shrink_scale))
	smoothed_image = cv2.medianBlur(resized_image, shrink_kernel_size)
	smoothed_image = cv2.resize(smoothed_image, (image_width, image_height), interpolation=cv2.INTER_LINEAR)
	return smoothed_image

def fast_dilate(image, kernel_size, kernel_shape, iteration, shrink_scale=2):
	image_height, image_width = image.shape[:2]
	resized_image = cv2.resize(image, (image_width // shrink_scale, image_height // shrink_scale), interpolation=cv2.INTER_AREA)
	kernel_shrink_scale = shrink_scale ** 0.5
	shrink_iteration = int(iteration * 0.8)
	shrink_kernel_size = int_to_odd(int(kernel_size / kernel_shrink_scale))
	resized_kernel = cv2.getStructuringElement(kernel_shape, (shrink_kernel_size, shrink_kernel_size))
	dilated_resized_image = cv2.dilate(resized_image, resized_kernel, iterations=shrink_iteration)
	dilated_image = cv2.resize(dilated_resized_image, (image_width, image_height), interpolation=cv2.INTER_LINEAR)
	return dilated_image

def fast_erode(image, kernel_size, kernel_shape, iteration, shrink_scale=2):
	image_height, image_width = image.shape[:2]
	resized_image = cv2.resize(image, (image_width // shrink_scale, image_height // shrink_scale), interpolation=cv2.INTER_AREA)
	kernel_shrink_scale = shrink_scale ** 0.5
	shrink_iteration = int(iteration * 0.8)
	shrink_kernel_size = int_to_odd(int(kernel_size / kernel_shrink_scale))
	resized_kernel = cv2.getStructuringElement(kernel_shape, (shrink_kernel_size, shrink_kernel_size))
	eroded_resized_image = cv2.erode(resized_image, resized_kernel, iterations=shrink_iteration)
	eroded_image = cv2.resize(eroded_resized_image, (image_width, image_height), interpolation=cv2.INTER_LINEAR)
	return eroded_image

def fast_close(image, kernel_size, kernel_shape, dilate_iteration, erode_iteration, shrink_scale=2):
	image_height, image_width = image.shape[:2]
	resized_image = cv2.resize(image, (image_width // shrink_scale, image_height // shrink_scale), interpolation=cv2.INTER_AREA)
	kernel_shrink_scale = shrink_scale ** 0.5
	dilate_shrink_iteration = int(dilate_iteration * 0.8)
	erode_shrink_iteration = int(erode_iteration * 0.8)
	shrink_kernel_size = int_to_odd(int(kernel_size / kernel_shrink_scale))
	resized_kernel = cv2.getStructuringElement(kernel_shape, (shrink_kernel_size, shrink_kernel_size))
	resized_image = cv2.dilate(resized_image, resized_kernel, iterations=dilate_shrink_iteration)
	resized_image = cv2.erode(resized_image, resized_kernel, iterations=erode_shrink_iteration)
	resized_image = cv2.resize(resized_image, (image_width, image_height), interpolation=cv2.INTER_LINEAR)
	return resized_image

def fast_gaussian_blur(image, kernel_size, sigma, shrink_scale=2):
	image_height, image_width = image.shape[:2]
	resized_image = cv2.resize(image, (image_width // shrink_scale, image_height // shrink_scale), interpolation=cv2.INTER_AREA)
	shrink_sigma = sigma // shrink_scale
	kernel_shrink_scale = shrink_scale ** 0.5
	shrink_kernel_size = int_to_odd(int(kernel_size / kernel_shrink_scale))
	blurred_image = cv2.GaussianBlur(image, (shrink_kernel_size, shrink_kernel_size), shrink_sigma)
	image = cv2.resize(resized_image, (image_width, image_height), interpolation=cv2.INTER_LINEAR)
	return image

def fast_blur(image, kernel_size, shrink_scale=2):
	image_height, image_width = image.shape[:2]
	resized_image = cv2.resize(image, (image_width // shrink_scale, image_height // shrink_scale), interpolation=cv2.INTER_AREA)
	kernel_shrink_scale = shrink_scale ** 0.5
	shrink_kernel_size = int_to_odd(int(kernel_size / kernel_shrink_scale))
	blurred_image = cv2.blur(resized_image, (shrink_kernel_size, shrink_kernel_size))
	image = cv2.resize(blurred_image, (image_width, image_height), interpolation=cv2.INTER_LINEAR)
	return image

def edge_detection(image, mode, kernel_size=3):
	# Convert the image to grayscale
	gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
	if mode == 'sobel':
		# Apply the Sobel operator in the X direction
		sobel_x = cv2.Sobel(gray_image, cv2.CV_64F, 1, 0, ksize=kernel_size)

		# Apply the Sobel operator in the Y direction
		sobel_y = cv2.Sobel(gray_image, cv2.CV_64F, 0, 1, ksize=kernel_size)

		# Calculate the magnitude of the gradients
		magnitude = np.sqrt(sobel_x**2 + sobel_y**2)

		# Normalize to 0 to 255
		magnitude = np.uint8(255 * magnitude / np.max(magnitude))
		return magnitude
	elif mode == 'canny':
		# Convert the image to grayscale
		gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

		# Apply GaussianBlur to reduce noise
		smoothed_image = fast_gaussian_blur(gray_image, kernel_size, 0)

		# Apply Canny edge detector
		edges = cv2.Canny(smoothed_image, threshold1=50, threshold2=150)
		return edges
	elif mode == 'smooth_sobel':
		smoothed_image = fast_gaussian_blur(gray_image, kernel_size, 0)
		
		# Apply the Sobel operator in the X direction
		sobel_x = cv2.Sobel(smoothed_image, cv2.CV_64F, 1, 0, ksize=kernel_size)

		# Apply the Sobel operator in the Y direction
		sobel_y = cv2.Sobel(smoothed_image, cv2.CV_64F, 0, 1, ksize=kernel_size)

		# Calculate the magnitude of the gradients
		magnitude = np.sqrt(sobel_x**2 + sobel_y**2)

		# Normalize to 0 to 255
		magnitude = np.uint8(255 * magnitude / np.max(magnitude))
		return magnitude

def fast_get_thresholded_skin_map(skin_map_model, image, skin_map_threshold=0.5, kernel_size=5, iteration=10):
	image = image.astype(np.float32)
	height, width, channel = image.shape
	skin_map_input_size = (96, 128)

	resized_image = np.array([cv2.resize(image, skin_map_input_size, interpolation=cv2.INTER_AREA)]) / 255.0
	prediction = skin_map_model(resized_image).numpy()[0]
	skin_map = cv2.resize(prediction, (width, height), interpolation=cv2.INTER_LINEAR)
	skin_map = np.where(skin_map > skin_map_threshold, 1.0, 0.0)

	# eroded_skin_map for not high pass usage
	kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (kernel_size, kernel_size))
	eroded_skin_map = cv2.erode(skin_map, kernel, iterations = iteration)

	skin_map = np.expand_dims(skin_map, axis=-1)
	eroded_skin_map = np.expand_dims(eroded_skin_map, axis=-1)
	return skin_map, eroded_skin_map

def fast_get_thresholded_skin_map_with_transform(skin_map_model, image, face_landmarks, skin_map_threshold=0.5, kernel_size=5, iteration=10):
	def compute_rect_from_shape(shape):
	    lt, rb = np.min(shape, axis=0), np.max(shape, axis=0)
	    center = (lt + rb) * 0.5
	    length = np.max(rb - lt)

	    return np.asarray([center[0] - length / 2, center[1] - length / 2, length, length])

	image_height, image_width, image_channel = image.shape
	oh, ow = 128, 96
	bbox = compute_rect_from_shape(face_landmarks)
	length_ratio = .6
	face_length = (bbox[2] + bbox[3]) * length_ratio
	face_middle = (bbox[0] + bbox[2] / 2., bbox[1] + bbox[3] / 2.)
	#Face_middle use up point and low point
	mid_eye = (face_landmarks[6] + face_landmarks[13]) / 2

	chinnoseLength = np.sqrt(np.sum((face_landmarks[21] - face_landmarks[28]) * (face_landmarks[21] - face_landmarks[28])))
	up_direction = (face_landmarks[21] - face_landmarks[28]) / chinnoseLength
	noseLength = np.sqrt(np.sum((face_landmarks[21] - face_landmarks[47]) * (face_landmarks[21] - face_landmarks[47])))

	#use face width to calculate height
	face_width = np.sqrt(np.sum((face_landmarks[34] - face_landmarks[30]) * (face_landmarks[34]-face_landmarks[30])))
	up_point_by_width = mid_eye + (up_direction * face_width * 1.05)
	low_point_by_width = face_landmarks[28] - (up_direction * face_width * 0.5)
	mid_point_by_width = (up_point_by_width + low_point_by_width) / 2
	up_low_len_by_width = np.sqrt(np.sum((up_point_by_width - low_point_by_width) * (up_point_by_width-low_point_by_width)))

	up_point = up_point_by_width
	low_point = low_point_by_width
	mid_point = (up_point + low_point) / 2
	up_low_len = np.sqrt(np.sum((up_point - low_point) * (up_point - low_point)))

	#use eye to roll
	left_eye = np.mean(face_landmarks[(4, 6), :], axis=0)
	right_eye = np.mean(face_landmarks[(13, 15), :], axis=0)
	x1, y1 = left_eye[0], left_eye[1]
	x2, y2 = right_eye[0], right_eye[1]
	roll = 0.0 if x1 == x2 else np.arctan((y2 - y1) / (x2 - x1))

	rotation_angle = np.degrees(roll)
	transform_scale = oh / up_low_len * (1 - 0.05)
	transform_mat = cv2.getRotationMatrix2D(
	    (face_middle[0],mid_point[1]), rotation_angle, transform_scale
	)
	transform_mat[0, 2] += -face_middle[0] + ow / 2.
	transform_mat[1, 2] += -mid_point[1] + oh / 2.

	face = cv2.warpAffine(image, transform_mat[:2, :3], (ow, oh))

	face = face.astype(np.float32)
	prediction = skin_map_model(np.array([face]) / 255.0).numpy()[0]
	skin_map = np.where(prediction > skin_map_threshold, 1.0, 0.0)

	# Compute inverse transformation matrix
	M_inv = cv2.invertAffineTransform(transform_mat[:2, :3])

	# Apply warpAffine again with inverse transformation
	skin_map = cv2.warpAffine(skin_map, M_inv, (image_width, image_height))

	# eroded_skin_map for not high pass usage
	kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (kernel_size, kernel_size))
	eroded_skin_map = cv2.erode(skin_map, kernel, iterations = iteration)

	skin_map = np.expand_dims(skin_map, axis=-1)
	eroded_skin_map = np.expand_dims(eroded_skin_map, axis=-1)
	return skin_map, eroded_skin_map

def get_eye_align_points(align_points_92, side):
	points = []
	if side == 'left':
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M3']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M4']])
	elif side == 'right':
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M4']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M3']])
	return np.array(points).astype(np.int32)

def get_iris_align_points(align_points_92, side):
	points = []
	iris_radius = 0.0
	if side == 'left':
		points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']])
		iris_radius = min(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']]), 
						  util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']]))
	elif side == 'right':
		points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']])
		iris_radius = min(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']]), 
						  util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']]))
	return np.array(points).astype(np.int32), round(iris_radius)

def get_inpainting_points(align_points_92, side, replace=False, for_dataset=False):
	indices = get_inpainting_indices(side=side)
	points = align_points_92[indices]
	if replace:
		# testing
		points[1:4] = align_points_92[-6:-3] if side == 'left' else align_points_92[-3:]
	else:
		# training
		points[1:4] = INPAINTING_RATIO * points[1:4] + ONE_MINUS_INPAINTING_RATIO * points[5:8][::-1]
	if for_dataset:
		lower_points = []
		if side == 'left':
			lower_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M4']])
			lower_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
			lower_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M3']])
		elif side == 'right':
			lower_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M3']])
			lower_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])
			lower_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M4']])
		lower_points = np.array(lower_points)
		points = np.append(points, lower_points, axis=0)
	return points

def get_inpainting_points_v2(align_points_92, side):
	return align_points_92[get_inpainting_indices_v2(side=side)]

def get_inpainting_indices(side):
	indices = []
	if side == 'left':
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M2'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M1'])
	elif side == 'right':
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'])
	return np.array(indices).astype(np.int32)

def get_inpainting_indices_v2(side):
	indices = []
	if side == 'left':
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M2'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M1'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M4'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM'])
		indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M3'])
	elif side == 'right':
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M3'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM'])
		indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M4'])
	return np.array(indices).astype(np.int32)

def is_eye_closed(align_points_92, side):
	y_offset, criterion = 0.0, 0.0
	if side == 'left':
		y_offset = align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']][1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']][1]
		criterion = max(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M4']][1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']][1],
						align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M3']][1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']][1])
	elif side == 'right':
		y_offset = align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']][1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']][1]
		criterion = max(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M4']][1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']][1],
						align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M3']][1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']][1])
	return y_offset <= criterion

def is_upper_droopy_eyelid(align_points_92, side, threshold_ratio=0.8):
	if is_eye_closed(align_points_92, side):
		return True
	iris_radius, MRD = 0.0, 0.0
	if side == 'left':
		iris_radius = min(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']]), 
						  util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']]))
		eye_center_position = 0.25 * (align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']] + align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']] + 
									  align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']] + align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
		MRD = abs(eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']][1])
	elif side == 'right':
		iris_radius = min(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']]), 
						  util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']]))
		eye_center_position = 0.25 * (align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']] + align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']] + 
									  align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']] + align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])
		MRD = abs(eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']][1])
	# the less MRD means more serious droopy eyelid
	# the less threshold_ratio means more strict criterion
	return threshold_ratio > (MRD / iris_radius)

def get_adjustable_length(align_points_92, side):
	if is_eye_closed(align_points_92, side):
		return 0.0
	iris_radius, MRD = 0.0, 0.0
	if side == 'left':
		iris_radius = max(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']]), 
						  util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']]))
		eye_center_position = 0.25 * (align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']] + align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']] + 
									  align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']] + align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
		MRD = abs(eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']][1])
	elif side == 'right':
		iris_radius = max(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']]), 
						  util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']]))
		eye_center_position = 0.25 * (align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']] + align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']] + 
									  align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']] + align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])
		MRD = abs(eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']][1])
	return max(0.1 * iris_radius, iris_radius - MRD)

def get_eyes_area(image, align_points_92):
	args = parse_arguments('eyes_preprocess')
	left_eye_area = np.zeros_like(image)
	right_eye_area = np.zeros_like(image)
	left_eye_points = get_eye_align_points(align_points_92, side='left')
	right_eye_points = get_eye_align_points(align_points_92, side='right')
	
	left_eye_area = cv2.fillPoly(left_eye_area, [left_eye_points], color=WHITE)
	right_eye_area = cv2.fillPoly(right_eye_area, [right_eye_points], color=WHITE)
	left_eye_area = fast_erode(left_eye_area, args.kernel_size, args.kernel_shape, args.iteration, shrink_scale=2)
	right_eye_area = fast_erode(right_eye_area, args.kernel_size, args.kernel_shape, args.iteration, shrink_scale=2)
	return left_eye_area, right_eye_area

def get_iris_area(image, align_points_92):
	args = parse_arguments('eyes_preprocess')
	left_iris_area = np.zeros_like(image)
	right_iris_area = np.zeros_like(image)
	left_eye_points, left_iris_radius = get_iris_align_points(align_points_92, side='left')
	right_eye_points, right_iris_radius = get_iris_align_points(align_points_92, side='right')
	left_iris_area = cv2.circle(left_iris_area, center=tuple(left_eye_points[0]), radius=left_iris_radius, color=WHITE, thickness=-1)
	right_iris_area = cv2.circle(right_iris_area, center=tuple(right_eye_points[0]), radius=right_iris_radius, color=WHITE, thickness=-1)
	left_iris_area = fast_erode(left_iris_area, args.kernel_size, args.kernel_shape, args.iteration, shrink_scale=2)
	right_iris_area = fast_erode(right_iris_area, args.kernel_size, args.kernel_shape, args.iteration, shrink_scale=2)
	return left_iris_area, right_iris_area

def get_iris_outside_area(image, align_points_92):
	left_eye_area, right_eye_area = get_eyes_area(image, align_points_92)
	left_iris_area, right_iris_area = get_iris_area(image, align_points_92)
	left_iris_outside_area = np.where(left_iris_area, 0, left_eye_area)
	right_iris_outside_area = np.where(right_iris_area, 0, right_eye_area)
	return left_iris_outside_area, right_iris_outside_area

def parse_arguments(stage):
	parser = argparse.ArgumentParser()
	if stage == 'eyes_preprocess':
		parser.add_argument('--kernel_size', type=int, default=5)
		parser.add_argument('--kernel_shape', type=int, default=cv2.MORPH_CROSS)
		parser.add_argument('--iteration', type=int, default=5)
	return parser.parse_args(args=[])

def mirror_point_along_line(point, line):
	"""
	Mirrors a point along a line given in the form y = Ax + B.

	:param point: Tuple (x, y), the point to be mirrored.
	:param line: Tuple (A, B), coefficients of the line equation y = Ax + B.
	:return: Tuple (x', y'), the mirrored point.
	"""
	x1, y1 = point
	A, B = line
	
	# Intersection point
	x_i = (x1 - A * (B + A * x1 - y1)) / (1 + A**2)
	y_i = A * x_i + B
	
	# Mirrored point
	x2 = 2 * x_i - x1
	y2 = 2 * y_i - y1
	
	return (x2, y2)

def get_morphed_align_points(align_points_92, emulation_type):
	align_points_92_source = copy.deepcopy(align_points_92)
	align_points_92_target = copy.deepcopy(align_points_92)

	align_eyes_align_points_92, left_angle, right_angle = get_align_eyes_align_points(align_points_92)

	left_adjustable_length = get_adjustable_length(align_eyes_align_points_92, side='left')
	right_adjustable_length = get_adjustable_length(align_eyes_align_points_92, side='right')

	if emulation_type == 'better':
		left_adjustable_length *= -1
		right_adjustable_length *= -1
	elif emulation_type == 'worse':
		pass
	else:
		pass

	left_eye_center_position = 0.25 * (align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']] + align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']] + 
								  	   align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']] + align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
	right_eye_center_position = 0.25 * (align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']] + align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']] + 
								  		align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']] + align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])

	outer_ratio = 0.7182 # 0.8
	inner_ratio = 0.7107

	align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'], 1] += left_adjustable_length
	left_length_scale = align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'], 1] - left_eye_center_position[1]
	left_left_length = left_length_scale * outer_ratio
	left_right_length = left_length_scale * inner_ratio
	align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1'], 1] = left_left_length + left_eye_center_position[1]
	align_eyes_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2'], 1] = left_right_length + left_eye_center_position[1]

	align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'], 1] += right_adjustable_length
	right_length_scale = align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'], 1] - right_eye_center_position[1]
	right_left_length = right_length_scale * inner_ratio
	right_right_length = right_length_scale * outer_ratio
	align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'], 1] = right_right_length + right_eye_center_position[1]
	align_eyes_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'], 1] = right_left_length + right_eye_center_position[1]

	align_points_92_target = get_reverse_align_eyes_align_points(align_eyes_align_points_92, left_angle, right_angle)

	# add anchor points (will not be changed)
	left_source_new_points = get_inpainting_points(align_points_92_source, side='left')[1:4]
	right_source_new_points = get_inpainting_points(align_points_92_source, side='right')[1:4]
	align_points_92_source = np.append(align_points_92_source, left_source_new_points, axis=0)
	align_points_92_source = np.append(align_points_92_source, right_source_new_points, axis=0)
	align_points_92_target = np.append(align_points_92_target, left_source_new_points, axis=0)
	align_points_92_target = np.append(align_points_92_target, right_source_new_points, axis=0)
	return align_points_92_source, align_points_92_target

def bilinear_interpolate(image, coords):
	int_coords = np.int32(coords)
	x, y = int_coords
	dx, dy = coords - int_coords
	# 4 neighbor pixels
	p11 = image[y, x]
	p21 = image[y, x+1]
	p12 = image[y+1, x]
	p22 = image[y+1, x+1]
	bottom = p21.T * dx + p11.T * (1 - dx)
	top = p22.T * dx + p12.T * (1 - dx)
	inter = top * dy + bottom * (1 - dy)
	return inter.T

def bilinear_interpolate_batch(image, coords):
	int_coords = coords.astype(np.int32)
	x, y = int_coords[::, 0], int_coords[::, 1]
	delta_coords = coords - int_coords
	dx, dy = delta_coords[::, 0], delta_coords[::, 1]
	# 4 neighbor pixels
	p11 = image[y, x]
	p21 = image[y, x+1]
	p12 = image[y+1, x]
	p22 = image[y+1, x+1]
	bottom = p21.T * dx + p11.T * (1 - dx)
	top = p22.T * dx + p12.T * (1 - dx)
	inter = top * dy + bottom * (1 - dy)
	return inter.T

def get_reverse_morphed_align_points(original_align_points_92, align_points_92, intensity_abs_percentage):
	reverse_align_points_92 = copy.deepcopy(align_points_92)
	left_reverse_morphed_indices = [
		landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M1'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M2'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']
	]
	right_reverse_morphed_indices = [
		landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT'],
	]
	reverse_align_points_92[left_reverse_morphed_indices] = (1.0 - intensity_abs_percentage) * original_align_points_92[left_reverse_morphed_indices] + intensity_abs_percentage * align_points_92[left_reverse_morphed_indices]
	reverse_align_points_92[right_reverse_morphed_indices] = (1.0 - intensity_abs_percentage) * original_align_points_92[right_reverse_morphed_indices] + intensity_abs_percentage * align_points_92[right_reverse_morphed_indices]
	return reverse_align_points_92

def get_reverse_morphed_points(original_align_points_92, align_points_92, intensity_abs_percentage, side):
	reverse_align_points_92 = get_reverse_morphed_align_points(original_align_points_92, align_points_92, intensity_abs_percentage)

	fixed_points, src_points, dst_points, brow_points = [], [], [], []
	if side == 'left':
		fixed_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		fixed_points.append(INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']] + ONE_MINUS_INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']])
		fixed_points.append(INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']] + ONE_MINUS_INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']])
		fixed_points.append(INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']] + ONE_MINUS_INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']])
		fixed_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])

		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])

		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])

		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_BROW_1']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_BROW_BOTTOM_2']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_BROW_5']])
	elif side == 'right':
		fixed_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		fixed_points.append(INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']] + ONE_MINUS_INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']])
		fixed_points.append(INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']] + ONE_MINUS_INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']])
		fixed_points.append(INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']] + ONE_MINUS_INPAINTING_RATIO * reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']])
		fixed_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])

		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']])
		src_points.append(reverse_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])

		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']])
		dst_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])

		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_BROW_5']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_BROW_BOTTOM_2']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_BROW_1']])
	return np.array(fixed_points), np.array(src_points), np.array(dst_points), np.array(brow_points)

def get_morphed_points(align_points_92, side, emulation_type):
	_, morphed_align_points = get_morphed_align_points(align_points_92, emulation_type)

	fixed_points, src_points, dst_points, brow_points = [], [], [], []
	if side == 'left':
		fixed_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		fixed_points.append(ONE_MINUS_INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']] + INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']])
		fixed_points.append(ONE_MINUS_INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']] + INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']])
		fixed_points.append(ONE_MINUS_INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']] + INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']])
		fixed_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])

		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])

		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])

		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_BROW_1']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_BROW_BOTTOM_2']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_BROW_5']])
	elif side == 'right':
		fixed_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		fixed_points.append(ONE_MINUS_INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']] + INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']])
		fixed_points.append(ONE_MINUS_INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']] + INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']])
		fixed_points.append(ONE_MINUS_INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']] + INPAINTING_RATIO * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']])
		fixed_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])

		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']])
		src_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])

		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']])
		dst_points.append(morphed_align_points[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])

		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_BROW_5']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_BROW_BOTTOM_2']])
		brow_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_BROW_1']])
	return np.array(fixed_points), np.array(src_points), np.array(dst_points), np.array(brow_points)

def form_curve(points, min_x=None, max_x=None, return_type=np.float32):
	points_x, points_y = points[:, 0], points[:, 1]
	# Fit a polynomial of degree 2 (quadratic) through the points
	coefficients = np.polyfit(points_x, points_y, 2)
	# Create a polynomial object
	polynomial = np.poly1d(coefficients)
	# Generate x values for plotting the curve
	if min_x == None:
		min_x = min(points_x)
	if max_x == None:
		max_x = max(points_x)
	sample_size = round(max_x - min_x) + 1
	x_curve = np.linspace(min_x, max_x, sample_size)
	# Calculate corresponding y values
	y_curve = polynomial(x_curve)
	return x_curve.astype(return_type), y_curve.astype(return_type), sample_size

def rotate_curve(curve, angle, pivot=(0.0, 0.0)):
	"""Rotates a curve around a pivot point by a given angle."""
	cos_theta = np.cos(angle)
	sin_theta = np.sin(angle)
	px, py = pivot
	rotated_curve = [[cos_theta * (x - px) - sin_theta * (y - py) + px, sin_theta * (x - px) + cos_theta * (y - py) + py] for x, y in curve]
	return rotated_curve

def interpolate_curves(curve1, curve2, mode):
	if mode == 'no_rotation':
		"""
		Interpolates between two 2D curves, weighting by x-position and considering y-differences.
		
		:param curve1: List of (x, y) points for the first curve.
		:param curve2: List of (x, y) points for the second curve.
		:param num_points: Number of points to generate for the interpolated curve.
		:param k: Scaling factor for the y-difference influence.
		:return: List of interpolated (x, y) points.
		"""
		# Extract x-coordinates and determine the range
		x1 = [p[0] for p in curve1]
		y1 = [p[1] for p in curve1]
		x2 = [p[0] for p in curve2]
		y2 = [p[1] for p in curve2]

		min_x = min(min(x1), min(x2))
		max_x = max(max(x1), max(x2))
		sample_size = round(max_x - min_x) + 1
		
		# Interpolation target x-coordinates
		x_interp = np.linspace(min_x, max_x, sample_size)
		
		# Interpolate y-coordinates for both curves
		y1_interp = interp1d(x1, y1, kind='linear', fill_value="extrapolate")
		y2_interp = interp1d(x2, y2, kind='linear', fill_value="extrapolate")

		# Calculate y-coordinates of the interpolated curve
		interpolated_curve = []
		for x in x_interp:
			# Compute weight alpha based on x position
			y1 = y1_interp(x)
			y2 = y2_interp(x)
			
			# Adjust alpha by y-difference
			alpha = (x - min_x) / (max_x - min_x)
			
			# Interpolate y-coordinate
			y_interp = (1 - alpha) * y1 + alpha * y2
			
			interpolated_curve.append((x, y_interp))
	elif mode == 'single_rotation':
		"""Interpolates between two curves by aligning them horizontally with a single rotation."""
		# Calculate rotation angle based on the vertical and horizontal difference
		angle = -np.arctan2(curve1[-1][1] - curve2[0][1], curve1[-1][0] - curve2[0][0])

		# Rotate both curves to align horizontally
		curve1_rot = rotate_curve(curve1, angle, curve1[0])
		curve2_rot = rotate_curve(curve2, angle, curve1[0])
		
		# Interpolation in the rotated space
		x1 = [p[0] for p in curve1_rot]
		y1 = [p[1] for p in curve1_rot]
		x2 = [p[0] for p in curve2_rot]
		y2 = [p[1] for p in curve2_rot]
		min_x = min(min(x1), min(x2))
		max_x = max(max(x1), max(x2))
		sample_size = round(max_x - min_x) + 1
		x_interp = np.linspace(min_x, max_x, sample_size)
		
		y1_interp = interp1d(x1, y1, kind='linear', fill_value="extrapolate")
		y2_interp = interp1d(x2, y2, kind='linear', fill_value="extrapolate")
		interpolated_y_curve = []
		for x in x_interp:
			# Compute weight alpha based on x position
			alpha = (x - min_x) / (max_x - min_x)
			
			# Interpolate y-coordinate
			y1 = y1_interp(x)
			y2 = y2_interp(x)
			y_interp = (1 - alpha) * y1 + alpha * y2
			interpolated_y_curve.append(y_interp)

		# Combine x and y and rotate back to original orientation
		interpolated_curve_rot = list(zip(x_interp, interpolated_y_curve))
		interpolated_curve = rotate_curve(interpolated_curve_rot, -angle, curve1[0])

	return np.array(interpolated_curve)

def curve_interpolation(x_curve, y_curve, y_curve_, mode):
	curve1 = list(zip(x_curve, y_curve))
	curve2 = list(zip(x_curve, y_curve_))
	interpolated_curve = interpolate_curves(curve1, curve2, mode)
	interpolated_x_curve, interpolated_y_curve = interpolated_curve[:, 0], interpolated_curve[:, 1]
	return interpolated_x_curve, interpolated_y_curve

def form_curve_v2(points, min_x=None, max_x=None, return_type=np.float32, mode='single_rotation', side='left'):
	# mode should be one of ['no_rotation', 'single_rotation']
	if side == 'right':
		points = points[::-1]
	polynomial_size = len(points) - 2

	if polynomial_size == 1:
		return form_curve(points, min_x, max_x, return_type)

	x_curve_list = []
	y_curve_list = []

	for index in range(polynomial_size):
		points_slice = points[index:index+3]
		x_curve, y_curve, _ = form_curve(points_slice, min_x, max_x, return_type)
		x_curve_list.append(x_curve)
		y_curve_list.append(y_curve)

	interpolated_x_curve_list = [-1]
	interpolated_y_curve_list = [-1]

	for index in range(1, polynomial_size):
		min_x = min(x_curve_list[index])
		max_x = max(x_curve_list[index-1])
		sample_size = round(max_x - min_x) + 1
		x_curve = np.linspace(min_x, max_x, sample_size)
		interpolated_x_curve, interpolated_y_curve = curve_interpolation(x_curve, y_curve_list[index-1][-sample_size:], y_curve_list[index][:sample_size], mode)
		interpolated_x_curve_list.append(interpolated_x_curve)
		interpolated_y_curve_list.append(interpolated_y_curve)

	min_x = min(x_curve_list[0])
	max_x = min(x_curve_list[1])
	sample_size = round(max_x - min_x) + 1
	interpolated_x_curve_list[0] = np.linspace(min_x, max_x, sample_size)
	interpolated_y_curve_list[0] = y_curve_list[0][:sample_size]

	min_x = max(x_curve_list[-2])
	max_x = max(x_curve_list[-1])
	sample_size = round(max_x - min_x) + 1
	interpolated_x_curve_list.append(np.linspace(min_x, max_x, sample_size))
	interpolated_y_curve_list.append(y_curve_list[-1][-sample_size:])

	x_curve = np.concatenate(interpolated_x_curve_list)
	y_curve = np.concatenate(interpolated_y_curve_list)

	x_curve, y_curve = remove_duplicate_x(x_curve, y_curve)

	return x_curve.astype(return_type), y_curve.astype(return_type), sample_size

def remove_duplicate_x(x, y):
	"""
	Removes duplicate x-values by keeping the first occurrence.
	If duplicates exist, their y-values can also be averaged.
	
	:param x: Array of x-values.
	:param y: Array of y-values corresponding to x.
	:return: Arrays of unique x and adjusted y values.
	"""
	unique_x, indices = np.unique(x, return_index=True)
	unique_y = y[indices]
	return np.array(unique_x), np.array(unique_y)

def find_nearest_index(array, value):
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return idx

def resample_curve(x_curve, y_curve, num_points):
	"""
	Resamples a curve to have the specified number of points.
	
	:param curve: List of (x, y) points for the curve.
	:param num_points: Desired number of points in the resampled curve.
	:return: Resampled curve as a list of (x, y) points.
	"""
	# Define uniform x-values
	x_resampled = np.linspace(x_curve.min(), x_curve.max(), num_points)
	
	# Interpolate y-values
	y_interp = interp1d(x_curve, y_curve, kind='linear', fill_value="extrapolate")(x_resampled)
	
	return x_resampled, y_interp

def warp_image_batch(src_image, dst_image, points_x, 
					 src_start_points_y, src_end_points_y, \
					 dst_start_points_y, dst_end_points_y):
	points_x = points_x.astype(np.int32)
	points_x = [point_x for point_x in range(np.min(points_x), np.max(points_x))]
	for index in range(len(points_x)):
		dst_x = points_x[index]
		dst_length = abs(dst_end_points_y[index] - dst_start_points_y[index])
		src_length = abs(src_end_points_y[index] - src_start_points_y[index])
		start_points_y = dst_start_points_y[index]
		end_points_y = dst_end_points_y[index]

		points_y = np.array([point_y for point_y in range(round(start_points_y), round(end_points_y))])
		dst_end_ratio_batch = np.abs(points_y - dst_start_points_y[index]) / dst_length
		src_y_batch = dst_end_ratio_batch * src_length + src_start_points_y[index]
		index_batch = np.zeros((len(src_y_batch), 2))
		index_batch[::, 0] = points_x[index]
		index_batch[::, 1] = src_y_batch
		dst_image[points_y.astype(np.int32), dst_x] = bilinear_interpolate_batch(src_image, index_batch)
	return dst_image

def image_reverse_morphing_batch(original_image, image, original_align_points_92, align_points_92, intensity, blending_ratio=-20, debug=False, white_line_ratio_factor=2.0):
	intensity = np.clip(intensity, -100, 0)
	intensity_abs_percentage = float(abs(intensity)) / 100.0
	left_lower_points, left_src_points, left_upper_points, left_brow_points = get_reverse_morphed_points(original_align_points_92, align_points_92, intensity_abs_percentage, side='left')
	right_lower_points, right_src_points, right_upper_points, right_brow_points = get_reverse_morphed_points(original_align_points_92, align_points_92, intensity_abs_percentage, side='right')
	
	left_fixed_points_x_curve, left_fixed_points_y_curve, _ = form_curve_v2(left_lower_points)
	left_src_points_x_curve, left_src_points_y_curve, _ = form_curve_v2(left_src_points)
	left_dst_points_x_curve, left_dst_points_y_curve, _ = form_curve_v2(left_upper_points)
	left_brow_points_x_curve, left_brow_points_y_curve, _ = form_curve_v2(left_brow_points, min_x=min(left_fixed_points_x_curve), max_x=max(left_fixed_points_x_curve))

	left_fixed_points_x_curve, left_fixed_points_y_curve = resample_curve(left_fixed_points_x_curve, left_fixed_points_y_curve, len(left_brow_points_x_curve))
	left_src_points_x_curve, left_src_points_y_curve = resample_curve(left_src_points_x_curve, left_src_points_y_curve, len(left_brow_points_x_curve))
	left_dst_points_x_curve, left_dst_points_y_curve = resample_curve(left_dst_points_x_curve, left_dst_points_y_curve, len(left_brow_points_x_curve))

	right_fixed_points_x_curve, right_fixed_points_y_curve, _ = form_curve_v2(right_lower_points, side='right')
	right_src_points_x_curve, right_src_points_y_curve, _ = form_curve_v2(right_src_points, side='right')
	right_dst_points_x_curve, right_dst_points_y_curve, _ = form_curve_v2(right_upper_points, side='right')
	right_brow_points_x_curve, right_brow_points_y_curve, _ = form_curve_v2(right_brow_points, min_x=min(right_fixed_points_x_curve), max_x=max(right_fixed_points_x_curve), side='right')
	
	right_fixed_points_x_curve, right_fixed_points_y_curve = resample_curve(right_fixed_points_x_curve, right_fixed_points_y_curve, len(right_brow_points_x_curve))
	right_src_points_x_curve, right_src_points_y_curve = resample_curve(right_src_points_x_curve, right_src_points_y_curve, len(right_brow_points_x_curve))
	right_dst_points_x_curve, right_dst_points_y_curve = resample_curve(right_dst_points_x_curve, right_dst_points_y_curve, len(right_brow_points_x_curve))

	left_white_line_ratio = calcualte_white_line_ratio(left_brow_points_y_curve, left_dst_points_y_curve, left_src_points_y_curve, white_line_ratio_factor)
	left_brow_points_y_curve = left_white_line_ratio * left_brow_points_y_curve + (1.0 - left_white_line_ratio) * left_dst_points_y_curve
	right_white_line_ratio = calcualte_white_line_ratio(right_brow_points_y_curve, right_dst_points_y_curve, right_src_points_y_curve, white_line_ratio_factor)
	right_brow_points_y_curve = right_white_line_ratio * right_brow_points_y_curve + (1.0 - right_white_line_ratio) * right_dst_points_y_curve

	if debug:   
		debug_image = align_utils.draw_lines_xy(image.copy(), left_fixed_points_x_curve, left_fixed_points_y_curve, color=RED, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_src_points_x_curve, left_src_points_y_curve, color=GREEN, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_dst_points_x_curve, left_dst_points_y_curve, color=BLUE, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_brow_points_x_curve, left_brow_points_y_curve, color=WHITE, thickness=1, line_type=cv2.LINE_AA)

		debug_image = align_utils.draw_lines_xy(debug_image, right_fixed_points_x_curve, right_fixed_points_y_curve, color=RED, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_src_points_x_curve, right_src_points_y_curve, color=GREEN, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_dst_points_x_curve, right_dst_points_y_curve, color=BLUE, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_brow_points_x_curve, right_brow_points_y_curve, color=WHITE, thickness=1, line_type=cv2.LINE_AA)
		return debug_image

	reverse_morphed_image = np.zeros_like(image)
	reverse_morphed_image = warp_image_batch(image, reverse_morphed_image, left_dst_points_x_curve, left_dst_points_y_curve, left_fixed_points_y_curve, left_src_points_y_curve, left_fixed_points_y_curve)
	reverse_morphed_image = warp_image_batch(image, reverse_morphed_image, left_brow_points_x_curve, left_brow_points_y_curve, left_dst_points_y_curve, left_brow_points_y_curve, left_src_points_y_curve)
	reverse_morphed_image = warp_image_batch(image, reverse_morphed_image, right_dst_points_x_curve, right_dst_points_y_curve, right_fixed_points_y_curve, right_src_points_y_curve, right_fixed_points_y_curve)
	reverse_morphed_image = warp_image_batch(image, reverse_morphed_image, right_brow_points_x_curve, right_brow_points_y_curve, right_dst_points_y_curve, right_brow_points_y_curve, right_src_points_y_curve)
	reverse_morphed_image = np.where(reverse_morphed_image, reverse_morphed_image, image)

	if intensity > blending_ratio:
		blending_weight = intensity / blending_ratio
		reverse_morphed_image = np.clip(blending_weight * reverse_morphed_image.astype(np.float32) + (1.0 - blending_weight) * original_image.astype(np.float32), 0.0, 255.0).astype(np.uint8)
	return reverse_morphed_image

def image_intensity_composite(original_image, image, lift_curves, intensity, white_line_ratio_factor=2.0, lash_margin_ratio=0.04, feather_ratio=0.04, blending_ratio=20):
	# original_image: aligned source, image: intensity 100 result, lift_curves: from image_morphing_batch(return_lift_curves=True)
	# The upper eyelid of the intensity 100 result is warped down to the lid interpolated between the source (0) and
	# intensity 100 lids, and below that lid the intensity 100 result is kept unwarped (the eye is uncovered, not stretched).
	# Outside the inpainted area the 100 result's lid band is the source band compressed, so this equals warping the source
	# lid up; inside it (lid edge, outer corner circle) the inpainted lid and corner move with the lid instead of being blended.
	# Below blending_ratio the result fades in from the source, so intensity 0 returns the source.
	intensity = float(np.clip(intensity, 0, 100))
	intensity_percentage = intensity / 100.0
	warped_image = image.copy()
	warped_alpha = np.zeros(image.shape[:2], dtype=np.float32)
	points_y = np.arange(image.shape[0], dtype=np.float32)[::, None]
	for side in ['left', 'right']:
		points_x_curve, brow_points_y_curve, src_points_y_curve, dst_points_y_curve, fixed_points_y_curve = lift_curves[side]
		lid_points_y_curve = (1.0 - intensity_percentage) * src_points_y_curve + intensity_percentage * dst_points_y_curve
		# The brow curve the intensity 100 warp used
		white_line_ratio = calcualte_white_line_ratio(brow_points_y_curve, dst_points_y_curve, src_points_y_curve, white_line_ratio_factor)
		brow_points_y_curve = white_line_ratio * brow_points_y_curve + (1.0 - white_line_ratio) * dst_points_y_curve

		# The lashes and lid shadow hang below the landmark lid curve, so the moved band reaches that far below it
		eye_width = np.max(points_x_curve) - np.min(points_x_curve)
		lash_margin = np.minimum(lash_margin_ratio * eye_width, 0.5 * np.maximum(fixed_points_y_curve - src_points_y_curve, 0.0))
		cut_points_y_curve = lid_points_y_curve + lash_margin
		# A hard cut at the lash line looks pasted and steps where the lid rises steeply at the corners, so the band is moved
		# feather further down and faded into the unwarped 100 result over [cut - feather, cut + feather]
		feather = np.maximum(np.minimum(feather_ratio * eye_width, 0.5 * (fixed_points_y_curve - cut_points_y_curve)), 1.0)
		warped_image = warp_image_batch(image, warped_image, points_x_curve, brow_points_y_curve, dst_points_y_curve + lash_margin + feather, brow_points_y_curve, cut_points_y_curve + feather)

		points_x = np.arange(int(np.min(points_x_curve)), int(np.max(points_x_curve)))
		length = len(points_x)
		alpha = np.clip((cut_points_y_curve[None, :length] + feather[None, :length] - points_y) / (2.0 * feather[None, :length]), 0.0, 1.0)
		warped_alpha[::, points_x] = alpha * (points_y >= brow_points_y_curve[None, :length])

	warped_alpha = warped_alpha[..., None]
	composite_image = warped_alpha * warped_image.astype(np.float32) + (1.0 - warped_alpha) * image.astype(np.float32)
	if intensity < blending_ratio:
		blending_weight = intensity / blending_ratio
		composite_image = blending_weight * composite_image + (1.0 - blending_weight) * original_image.astype(np.float32)
	return np.clip(composite_image, 0.0, 255.0).astype(np.uint8)

def calcualte_white_line_ratio(white_line_y, blue_line_y, green_line_y, white_line_ratio_factor):
	ratio = 1.0
	white_blue_min_y_distance = np.min(blue_line_y - white_line_y)
	blue_green_max_y_distance = np.max(green_line_y - blue_line_y)
	if white_blue_min_y_distance < 0.0:
		return ratio
	offset = white_line_ratio_factor * blue_green_max_y_distance
	if white_blue_min_y_distance > offset:
		white_blue_min_y_distance_index = np.argmin(blue_line_y - white_line_y)
		blue_green_max_y_distance_index = np.argmax(green_line_y - blue_line_y)
		white_y_index = white_line_y[white_blue_min_y_distance_index]
		blue_y_index = blue_line_y[blue_green_max_y_distance_index]
		target_y_index = white_y_index + white_blue_min_y_distance - offset
		ratio = (blue_y_index - target_y_index) / (blue_y_index - white_y_index)
	return ratio

def image_morphing_batch(image, align_points_92, emulation_type, morphing=True, corner=True, debug=False, white_line_ratio_factor=2.0, left_good_eye_curve=None, right_good_eye_curve=None, balance=True, return_lift_curves=False):
	left_lower_points, left_src_points, left_upper_points, left_brow_points = get_morphed_points(align_points_92, side='left', emulation_type=emulation_type)
	right_lower_points, right_src_points, right_upper_points, right_brow_points = get_morphed_points(align_points_92, side='right', emulation_type=emulation_type)

	left_src_points_x_curve, left_src_points_y_curve, _ = form_curve_v2(left_src_points)
	right_src_points_x_curve, right_src_points_y_curve, _ = form_curve_v2(right_src_points, side='right')

	if not morphing:
		return image, [left_src_points_x_curve, left_src_points_y_curve, right_src_points_x_curve, right_src_points_y_curve]

	left_fixed_points_x_curve, left_fixed_points_y_curve, _ = form_curve_v2(left_lower_points)
	left_dst_points_x_curve, left_dst_points_y_curve = None, None
	left_brow_points_x_curve, left_brow_points_y_curve = None, None
	right_fixed_points_x_curve, right_fixed_points_y_curve, _ = form_curve_v2(right_lower_points, side='right')
	right_dst_points_x_curve, right_dst_points_y_curve = None, None
	right_brow_points_x_curve, right_brow_points_y_curve = None, None

	if left_good_eye_curve is not None:
		left_dst_points_x_curve, left_dst_points_y_curve = left_good_eye_curve[::, 0], left_good_eye_curve[::, 1]
		left_brow_points_x_curve, left_brow_points_y_curve, _ = form_curve_v2(left_brow_points, min_x=min(left_good_eye_curve[::, 0]), max_x=max(left_good_eye_curve[::, 0]))
	else:
		left_dst_points_x_curve, left_dst_points_y_curve, _ = form_curve_v2(left_upper_points)
		left_brow_points_x_curve, left_brow_points_y_curve, _ = form_curve_v2(left_brow_points, min_x=min(left_fixed_points_x_curve), max_x=max(left_fixed_points_x_curve))

	if right_good_eye_curve is not None:
		right_dst_points_x_curve, right_dst_points_y_curve = right_good_eye_curve[::, 0], right_good_eye_curve[::, 1]
		right_brow_points_x_curve, right_brow_points_y_curve, _ = form_curve_v2(right_brow_points, min_x=min(right_good_eye_curve[::, 0]), max_x=max(right_good_eye_curve[::, 0]), side='right')
	else:
		right_dst_points_x_curve, right_dst_points_y_curve, _ = form_curve_v2(right_upper_points, side='right')
		right_brow_points_x_curve, right_brow_points_y_curve, _ = form_curve_v2(right_brow_points, min_x=min(right_fixed_points_x_curve), max_x=max(right_fixed_points_x_curve), side='right')

	if balance:
		# left eye
		left_dst_points_curve = list(zip(left_dst_points_x_curve, left_dst_points_y_curve))
		left_angle = -np.arctan2(left_dst_points_curve[-1][1] - left_dst_points_curve[0][1], left_dst_points_curve[-1][0] - left_dst_points_curve[0][0])
		left_dst_points_curve = np.array(rotate_curve(left_dst_points_curve, left_angle, left_dst_points_curve[0]))
		left_amount = np.max(left_dst_points_curve[::, 1]) - np.min(left_dst_points_curve[::, 1])

		# right eye
		right_dst_points_curve = list(zip(right_dst_points_x_curve, right_dst_points_y_curve))
		right_angle = -np.arctan2(right_dst_points_curve[-1][1] - right_dst_points_curve[0][1], right_dst_points_curve[-1][0] - right_dst_points_curve[0][0])
		right_dst_points_curve = np.array(rotate_curve(right_dst_points_curve, right_angle, right_dst_points_curve[0]))
		right_amount = np.max(right_dst_points_curve[::, 1]) - np.min(right_dst_points_curve[::, 1])

		balance_amount = 0.5 * (left_amount + right_amount)
		left_dst_points_curve[::, 1] = (left_dst_points_curve[::, 1] - left_dst_points_curve[0][1]) / left_amount * balance_amount + left_dst_points_curve[0][1]
		right_dst_points_curve[::, 1] = (right_dst_points_curve[::, 1] - right_dst_points_curve[0][1]) / right_amount * balance_amount + right_dst_points_curve[0][1]

		left_dst_points_curve = np.array(rotate_curve(left_dst_points_curve, -left_angle, left_dst_points_curve[0]))
		right_dst_points_curve = np.array(rotate_curve(right_dst_points_curve, -right_angle, right_dst_points_curve[0]))

		left_dst_points_x_curve, left_dst_points_y_curve = left_dst_points_curve[::, 0], left_dst_points_curve[::, 1]
		right_dst_points_x_curve, right_dst_points_y_curve = right_dst_points_curve[::, 0], right_dst_points_curve[::, 1]

	no_corner_left_fixed_points_y_curve = left_fixed_points_y_curve.copy()
	no_corner_right_fixed_points_y_curve = right_fixed_points_y_curve.copy()

	if corner:
		x_offset_ratio, y_offset_ratio = 0.7, 0.3
		left_corner_point = np.array([x_offset_ratio * left_lower_points[3][0] + (1.0 - x_offset_ratio) * left_lower_points[4][0], 
									  y_offset_ratio * left_lower_points[3][1] + (1.0 - y_offset_ratio) * left_lower_points[4][1]])
		
		left_corner_start_index = find_nearest_index(left_fixed_points_x_curve, left_lower_points[3][0])
		left_corner_mid_index = find_nearest_index(left_fixed_points_x_curve, left_corner_point[0])
		left_corner_end_index = find_nearest_index(left_fixed_points_x_curve, left_lower_points[4][0])

		left_corner_start_step = (left_corner_point[1] - left_lower_points[3][1]) / (left_corner_point[0] - left_lower_points[3][0])
		for index in range(left_corner_start_index, left_corner_mid_index + 1):
			left_fixed_points_y_curve[index] = left_fixed_points_y_curve[index - 1] + left_corner_start_step

		left_corner_end_step = (left_lower_points[4][1] - left_corner_point[1]) / (left_lower_points[4][0] - left_corner_point[0])
		for index in range(left_corner_mid_index, left_corner_end_index + 1):
			left_fixed_points_y_curve[index] = left_fixed_points_y_curve[index - 1] + left_corner_end_step

		right_corner_point = np.array([x_offset_ratio * right_lower_points[3][0] + (1.0 - x_offset_ratio) * right_lower_points[4][0], 
									   y_offset_ratio * right_lower_points[3][1] + (1.0 - y_offset_ratio) * right_lower_points[4][1]])
		
		right_corner_start_index = find_nearest_index(right_fixed_points_x_curve, right_lower_points[4][0])
		right_corner_mid_index = find_nearest_index(right_fixed_points_x_curve, right_corner_point[0])
		right_corner_end_index = find_nearest_index(right_fixed_points_x_curve, right_lower_points[3][0])

		right_corner_start_step = (right_lower_points[4][1] - right_corner_point[1]) / (right_lower_points[4][0] - right_corner_point[0])
		for index in range(right_corner_start_index, right_corner_mid_index + 1):
			right_fixed_points_y_curve[index + 1] = right_fixed_points_y_curve[index] + right_corner_start_step

		right_corner_end_step = (right_corner_point[1] - right_lower_points[3][1]) / (right_corner_point[0] - right_lower_points[3][0])
		for index in range(right_corner_mid_index, right_corner_end_index + 1):
			right_fixed_points_y_curve[index + 1] = right_fixed_points_y_curve[index] + right_corner_end_step

	if left_good_eye_curve is not None:
		fixed_points_sample_size = round(left_fixed_points_x_curve[0] - left_dst_points_x_curve[0]) + 1
		left_fixed_points_x_curve_new = np.linspace(left_dst_points_x_curve[0], left_fixed_points_x_curve[0], fixed_points_sample_size)
		left_fixed_points_y_curve_new = np.linspace(left_dst_points_y_curve[0], left_fixed_points_y_curve[0], fixed_points_sample_size)
		left_fixed_points_x_curve = np.concatenate([left_fixed_points_x_curve_new, left_fixed_points_x_curve])
		left_fixed_points_y_curve = np.concatenate([left_fixed_points_y_curve_new, left_fixed_points_y_curve])
		left_fixed_points_x_curve, left_fixed_points_y_curve = remove_duplicate_x(left_fixed_points_x_curve, left_fixed_points_y_curve)

		src_points_sample_size = round(left_src_points_x_curve[0] - left_dst_points_x_curve[0]) + 1
		left_src_points_x_curve_new = np.linspace(left_dst_points_x_curve[0], left_src_points_x_curve[0], src_points_sample_size)
		left_src_points_y_curve_new = np.linspace(left_dst_points_y_curve[0], left_src_points_y_curve[0], src_points_sample_size)
		left_src_points_x_curve = np.concatenate([left_src_points_x_curve_new, left_src_points_x_curve])
		left_src_points_y_curve = np.concatenate([left_src_points_y_curve_new, left_src_points_y_curve])
		left_src_points_x_curve, left_src_points_y_curve = remove_duplicate_x(left_src_points_x_curve, left_src_points_y_curve)

	if right_good_eye_curve is not None:
		fixed_points_sample_size = round(right_dst_points_x_curve[-1] - right_fixed_points_x_curve[-1]) + 1
		right_fixed_points_x_curve_new = np.linspace(right_fixed_points_x_curve[-1], right_dst_points_x_curve[-1], fixed_points_sample_size)
		right_fixed_points_y_curve_new = np.linspace(right_fixed_points_y_curve[-1], right_dst_points_y_curve[-1], fixed_points_sample_size)
		right_fixed_points_x_curve = np.concatenate([right_fixed_points_x_curve, right_fixed_points_x_curve_new])
		right_fixed_points_y_curve = np.concatenate([right_fixed_points_y_curve, right_fixed_points_y_curve_new])
		right_fixed_points_x_curve, right_fixed_points_y_curve = remove_duplicate_x(right_fixed_points_x_curve, right_fixed_points_y_curve)

		src_points_sample_size = round(right_dst_points_x_curve[-1] - right_src_points_x_curve[-1]) + 1
		right_src_points_x_curve_new = np.linspace(right_src_points_x_curve[-1], right_dst_points_x_curve[-1], src_points_sample_size)
		right_src_points_y_curve_new = np.linspace(right_src_points_y_curve[-1], right_dst_points_y_curve[-1], src_points_sample_size)
		right_src_points_x_curve = np.concatenate([right_src_points_x_curve, right_src_points_x_curve_new])
		right_src_points_y_curve = np.concatenate([right_src_points_y_curve, right_src_points_y_curve_new])
		right_src_points_x_curve, right_src_points_y_curve = remove_duplicate_x(right_src_points_x_curve, right_src_points_y_curve)

	left_fixed_points_x_curve, left_fixed_points_y_curve = resample_curve(left_fixed_points_x_curve, left_fixed_points_y_curve, len(left_brow_points_x_curve))
	left_src_points_x_curve, left_src_points_y_curve = resample_curve(left_src_points_x_curve, left_src_points_y_curve, len(left_brow_points_x_curve))
	left_dst_points_x_curve, left_dst_points_y_curve = resample_curve(left_dst_points_x_curve, left_dst_points_y_curve, len(left_brow_points_x_curve))

	right_fixed_points_x_curve, right_fixed_points_y_curve = resample_curve(right_fixed_points_x_curve, right_fixed_points_y_curve, len(right_brow_points_x_curve))
	right_src_points_x_curve, right_src_points_y_curve = resample_curve(right_src_points_x_curve, right_src_points_y_curve, len(right_brow_points_x_curve))
	right_dst_points_x_curve, right_dst_points_y_curve = resample_curve(right_dst_points_x_curve, right_dst_points_y_curve, len(right_brow_points_x_curve))

	left_dst_points_y_curve = np.minimum(left_dst_points_y_curve, left_src_points_y_curve)
	right_dst_points_y_curve = np.minimum(right_dst_points_y_curve, right_src_points_y_curve)

	left_fixed_points_x_curve[0] = left_src_points_x_curve[0] = left_dst_points_x_curve[0] = left_brow_points_x_curve[0]
	left_fixed_points_y_curve[0] = left_src_points_y_curve[0] = left_dst_points_y_curve[0]
	left_fixed_points_x_curve[-1] = left_src_points_x_curve[-1] = left_dst_points_x_curve[-1] = left_brow_points_x_curve[-1]
	left_fixed_points_y_curve[-1] = left_src_points_y_curve[-1] = left_dst_points_y_curve[-1]
	right_fixed_points_x_curve[0] = right_src_points_x_curve[0] = right_dst_points_x_curve[0] = right_brow_points_x_curve[0]
	right_fixed_points_y_curve[0] = right_src_points_y_curve[0] = right_dst_points_y_curve[0]
	right_fixed_points_x_curve[-1] = right_src_points_x_curve[-1] = right_dst_points_x_curve[-1] = right_brow_points_x_curve[-1]
	right_fixed_points_y_curve[-1] = right_src_points_y_curve[-1] = right_dst_points_y_curve[-1]

	# For image_intensity_composite: the brow curve before the white line ratio, which depends on the lid target
	lift_curves = {
		'left': (left_brow_points_x_curve.copy(), left_brow_points_y_curve.copy(), left_src_points_y_curve.copy(), left_dst_points_y_curve.copy(), left_fixed_points_y_curve.copy()),
		'right': (right_brow_points_x_curve.copy(), right_brow_points_y_curve.copy(), right_src_points_y_curve.copy(), right_dst_points_y_curve.copy(), right_fixed_points_y_curve.copy()),
	}

	left_white_line_ratio = calcualte_white_line_ratio(left_brow_points_y_curve, left_dst_points_y_curve, left_src_points_y_curve, white_line_ratio_factor)
	left_brow_points_y_curve = left_white_line_ratio * left_brow_points_y_curve + (1.0 - left_white_line_ratio) * left_dst_points_y_curve
	right_white_line_ratio = calcualte_white_line_ratio(right_brow_points_y_curve, right_dst_points_y_curve, right_src_points_y_curve, white_line_ratio_factor)
	right_brow_points_y_curve = right_white_line_ratio * right_brow_points_y_curve + (1.0 - right_white_line_ratio) * right_dst_points_y_curve

	if debug:
		debug_image = align_utils.draw_lines_xy(image.copy(), left_fixed_points_x_curve, left_fixed_points_y_curve, color=RED, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_src_points_x_curve, left_src_points_y_curve, color=GREEN, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_dst_points_x_curve, left_dst_points_y_curve, color=BLUE, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_brow_points_x_curve, left_brow_points_y_curve, color=WHITE, thickness=1, line_type=cv2.LINE_AA)

		debug_image = align_utils.draw_lines_xy(debug_image, right_fixed_points_x_curve, right_fixed_points_y_curve, color=RED, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_src_points_x_curve, right_src_points_y_curve, color=GREEN, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_dst_points_x_curve, right_dst_points_y_curve, color=BLUE, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_brow_points_x_curve, right_brow_points_y_curve, color=WHITE, thickness=1, line_type=cv2.LINE_AA)
		
		# For training data inpainting mask
		left_src_curve = np.array(list(zip(left_src_points_x_curve, left_src_points_y_curve)))
		left_lower_curve = np.array(list(zip(left_fixed_points_x_curve, no_corner_left_fixed_points_y_curve)))
		right_src_curve = np.array(list(zip(right_src_points_x_curve, right_src_points_y_curve)))
		right_lower_curve = np.array(list(zip(right_fixed_points_x_curve, no_corner_right_fixed_points_y_curve)))
		return debug_image, [left_src_curve, left_lower_curve, right_src_curve, right_lower_curve]

	morphed_image = np.zeros_like(image)
	morphed_image = warp_image_batch(image, morphed_image, left_brow_points_x_curve, left_brow_points_y_curve, left_src_points_y_curve, left_brow_points_y_curve, left_dst_points_y_curve)
	morphed_image = warp_image_batch(image, morphed_image, left_src_points_x_curve, left_src_points_y_curve, left_fixed_points_y_curve, left_dst_points_y_curve, left_fixed_points_y_curve)
	morphed_image = warp_image_batch(image, morphed_image, right_brow_points_x_curve, right_brow_points_y_curve, right_src_points_y_curve, right_brow_points_y_curve, right_dst_points_y_curve)
	morphed_image = warp_image_batch(image, morphed_image, right_src_points_x_curve, right_src_points_y_curve, right_fixed_points_y_curve, right_dst_points_y_curve, right_fixed_points_y_curve)
	morphed_image = np.where(morphed_image, morphed_image, image)

	# For testing data inpainting mask
	left_upper_curve = np.array(list(zip(left_dst_points_x_curve, left_dst_points_y_curve)))
	left_lower_curve = np.array(list(zip(left_fixed_points_x_curve, left_fixed_points_y_curve)))
	right_upper_curve = np.array(list(zip(right_dst_points_x_curve, right_dst_points_y_curve)))
	right_lower_curve = np.array(list(zip(right_fixed_points_x_curve, right_fixed_points_y_curve)))
	if return_lift_curves:
		return morphed_image, [left_upper_curve, left_lower_curve, right_upper_curve, right_lower_curve], lift_curves
	return morphed_image, [left_upper_curve, left_lower_curve, right_upper_curve, right_lower_curve]

def get_inpainting_image(image, left_upper_curve, left_lower_curve, right_upper_curve, right_lower_curve):
	left_upper_points_x_curve, left_upper_points_y_curve = left_upper_curve[::, 0], left_upper_curve[::, 1]
	left_lower_points_x_curve, left_lower_points_y_curve = left_lower_curve[::, 0], left_lower_curve[::, 1]
	left_lower_points_x_curve, left_lower_points_y_curve = resample_curve(left_lower_points_x_curve, left_lower_points_y_curve, len(left_upper_points_x_curve))
	
	right_upper_points_x_curve, right_upper_points_y_curve = right_upper_curve[::, 0], right_upper_curve[::, 1]
	right_lower_points_x_curve, right_lower_points_y_curve = right_lower_curve[::, 0], right_lower_curve[::, 1]
	right_lower_points_x_curve, right_lower_points_y_curve = resample_curve(right_lower_points_x_curve, right_lower_points_y_curve, len(right_upper_points_x_curve))

	left_upper_points_x_curve, left_upper_points_y_curve = left_upper_points_x_curve.astype(np.int32), left_upper_points_y_curve.astype(np.int32)
	left_lower_points_x_curve, left_lower_points_y_curve = left_lower_points_x_curve.astype(np.int32), left_lower_points_y_curve.astype(np.int32)
	right_upper_points_x_curve, right_upper_points_y_curve = right_upper_points_x_curve.astype(np.int32), right_upper_points_y_curve.astype(np.int32)
	right_lower_points_x_curve, right_lower_points_y_curve = right_lower_points_x_curve.astype(np.int32), right_lower_points_y_curve.astype(np.int32)

	inpainting_image = image.copy()
	left_points_x = left_upper_points_x_curve
	for i, x in enumerate(left_points_x):
		inpainting_image[np.min(left_upper_points_y_curve[i]):np.max(left_lower_points_y_curve[i])+1, x] = WHITE
		if i > 0 and np.sum(inpainting_image[:, x-1]) == 0:
			inpainting_image[np.min(left_upper_points_y_curve[i]):np.max(left_lower_points_y_curve[i])+1, x-1] = WHITE

	right_points_x = right_upper_points_x_curve
	for i, x in enumerate(right_points_x):
		inpainting_image[np.min(right_upper_points_y_curve[i]):np.max(right_lower_points_y_curve[i])+1, x] = WHITE
		if i > 0 and np.sum(inpainting_image[:, x-1]) == 0:
			inpainting_image[np.min(right_upper_points_y_curve[i]):np.max(right_lower_points_y_curve[i])+1, x-1] = WHITE

	return inpainting_image

def get_eyes_light_spot_segment(image, align_points, mode='HSV'):
	left_eye_area, right_eye_area = get_eyes_area(image, align_points)

	image = np.where(left_eye_area, image, np.where(right_eye_area, image, 0))
	if mode == 'GRAY':
		converted_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
	elif mode == 'YUV':
		converted_image = cv2.cvtColor(image, cv2.COLOR_BGR2YUV)[..., 0]
	elif mode == 'Lab':
		converted_image = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)[..., 0]
	elif mode == 'HSL':
		converted_image = cv2.cvtColor(image, cv2.COLOR_BGR2HLS)[..., 1]
	elif mode == 'HSV':
		converted_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[..., 2]
	# blurred_image = cv2.GaussianBlur(converted_image, (5, 5), 0)
	_, thresholded_image = cv2.threshold(converted_image, 32, 255, cv2.THRESH_TOZERO)
	# kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
	# cleaned_image = cv2.morphologyEx(thresholded_image, cv2.MORPH_OPEN, kernel)
	# contours, _ = cv2.findContours(cleaned_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
	# for contour in contours:
	#     (x, y, w, h) = cv2.boundingRect(contour)
	#     image = cv2.rectangle(image, (x, y), (x + w, y + h), (0, 255, 0), 2)
	# return image
	thresholded_image = np.expand_dims(thresholded_image, axis=-1)
	return thresholded_image

def enhance_eyes_light_spot(original_image, image, original_align_points_92, align_points_92):
	image_copy = image.copy()
	original_eyes_light_spot_segment = get_eyes_light_spot_segment(original_image, original_align_points_92)
	eyes_light_spot_segment = get_eyes_light_spot_segment(image, align_points_92)
	image = np.where(original_eyes_light_spot_segment > eyes_light_spot_segment, original_image, image)
	left_iris_outside_area, right_iris_outside_area = get_iris_outside_area(original_image, original_align_points_92)
	original_iris_outside_area = np.maximum(left_iris_outside_area, right_iris_outside_area)
	image = np.where(original_iris_outside_area, np.clip(0.5 * (image.astype(np.float32) + image_copy.astype(np.float32)), 0.0, 255.0).astype(np.uint8), image)
	return image

def process_inpainting_points_to_mask(mask, inpainting_points, mode, center=None):
	# inpainting_points is from get_inpainting_points_v2(align_points_92, side)

	# if side == 'left':
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M2'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M1'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M4'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM'])
	# 	indices.append(landmark92_tag2index['LBF_3D_LEFT_EYE_M3'])
	# elif side == 'right':
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M3'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM'])
	# 	indices.append(landmark92_tag2index['LBF_3D_RIGHT_EYE_M4'])

	iris_radius = 0.5 * (util.PointDistance(inpainting_points[2], inpainting_points[1]) + util.PointDistance(inpainting_points[2], inpainting_points[3]))
	if center is not None:
		inpainting_points[0] = center
	radius_ratio = 0.75 if mode == 'training' else 0.9
	# The right eye mask arrives as a flipped view; OpenCV >= 4.x rejects non-contiguous arrays
	mask = np.ascontiguousarray(mask)
	mask = cv2.circle(mask, center=tuple(np.round(inpainting_points[0]).astype(np.int32)), radius=int(round(iris_radius * radius_ratio)), color=WHITE, thickness=-1)
	return mask if isinstance(mask, (np.ndarray, np.generic)) else mask.get()

def suppress_boundary(image, result, mask):
	if np.sum(mask) == 0.0:
		return image
	binarize_mask = np.where(mask, 1.0, 0.0)
	ksize = int(np.min(binarize_mask.shape[:2]) * 0.02)
	mask_tmp = fast_blur(binarize_mask, ksize, shrink_scale=4)
	result = image + (result.astype(np.float32) - image) * mask_tmp
	result = result.astype(np.uint8)
	return result

def split_left_right_mask(align_points, mask):
	split_x_coordinate = int(align_points[landmark51_tag2index['SB_NOSE_BRIDGE_TOP']][0])
	left_mask = copy.deepcopy(mask)
	left_mask[::, split_x_coordinate:, ::] = 0.0

	right_mask = copy.deepcopy(mask)
	right_mask[::, :split_x_coordinate, ::] = 0.0
	return left_mask, right_mask

def write_csv(columns_title, output_path, args):
	df = pd.DataFrame([], columns=columns_title)
	args = np.array(args)
	for index, column in enumerate(columns_title):
		df[column] = args[:, index]
	df.to_csv(output_path)

def get_align_eyes_align_points(align_points_92):
	aligned_align_points_92 = copy.deepcopy(align_points_92)
	# left eye
	left_angle = -np.arctan2(aligned_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']][1] - aligned_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']][1], 
							 aligned_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']][0] - aligned_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']][0])
	left_eye_indices = [
		landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'], 
		landmark92_tag2index['LBF_3D_LEFT_EYE_M1'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M2'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M3'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M4'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT'],
	]
	aligned_align_points_92[left_eye_indices] = rotate_curve(aligned_align_points_92[left_eye_indices], left_angle, aligned_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])

	# right eye
	right_angle = -np.arctan2(aligned_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']][1] - aligned_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']][1], 
							  aligned_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']][0] - aligned_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']][0])
	right_eye_indices = [
		landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'], 
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M4'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M3'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT'],
	]
	aligned_align_points_92[right_eye_indices] = rotate_curve(aligned_align_points_92[right_eye_indices], right_angle, aligned_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']])
	return aligned_align_points_92, left_angle, right_angle

def get_reverse_align_eyes_align_points(align_points_92, left_angle, right_angle):
	reverse_aligned_align_points_92 = copy.deepcopy(align_points_92)
	# left eye
	left_eye_indices = [
		landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'], 
		landmark92_tag2index['LBF_3D_LEFT_EYE_M1'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_TOP'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M2'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M3'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_M4'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER'],
		landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT'],
	]
	reverse_aligned_align_points_92[left_eye_indices] = rotate_curve(reverse_aligned_align_points_92[left_eye_indices], -left_angle, reverse_aligned_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])

	# right eye
	right_eye_indices = [
		landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'], 
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M2'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M1'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M4'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_M3'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER'],
		landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT'],
	]
	reverse_aligned_align_points_92[right_eye_indices] = rotate_curve(reverse_aligned_align_points_92[right_eye_indices], -right_angle, reverse_aligned_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']])
	return reverse_aligned_align_points_92

def calculate_good_eyes_info(align_points_92, image, align_eyes=True):
	is_left_eye_closed, is_left_upper_droopy_eyelid = True, True
	left_iris_radius, left_MRD, left_left_length, left_right_length = -1.0, -1.0, -1.0, -1.0
	is_right_eye_closed, is_right_upper_droopy_eyelid = True, True
	right_iris_radius, right_MRD, right_left_length, right_right_length = -1.0, -1.0, -1.0, -1.0

	left_angle, right_angle = 0.0, 0.0
	if align_eyes:
		align_points_92, left_angle, right_angle = get_align_eyes_align_points(align_points_92)

	is_left_eye_closed = is_eye_closed(align_points_92, side='left')
	is_left_upper_droopy_eyelid = is_upper_droopy_eyelid(align_points_92, side='left')
	left_iris_radius = min(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']]), 
						   util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']]))
	left_eye_center_position = 0.25 * (align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']] + align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']] + 
									   align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']] + align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
	left_MRD = abs(left_eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']][1])
	left_left_length = abs(left_eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']][1])
	left_right_length = abs(left_eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']][1])

	is_right_eye_closed = is_eye_closed(align_points_92, side='right')
	is_right_upper_droopy_eyelid = is_upper_droopy_eyelid(align_points_92, side='right')
	right_iris_radius = min(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']]), 
							util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']]))
	right_eye_center_position = 0.25 * (align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']] + align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']] + 
								  		align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']] + align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])
	right_MRD = abs(right_eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']][1])
	right_left_length = abs(right_eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']][1])
	right_right_length = abs(right_eye_center_position[1] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']][1])

	debug_image_before = align_utils.draw_points(image.copy(), align_points_92)
	if align_eyes:
		align_points_92 = get_reverse_align_eyes_align_points(align_points_92, left_angle, right_angle)
	debug_image_after = align_utils.draw_points(image.copy(), align_points_92)

	return [is_left_eye_closed, is_left_upper_droopy_eyelid, left_iris_radius, left_MRD, left_left_length, left_right_length, \
			is_right_eye_closed, is_right_upper_droopy_eyelid, right_iris_radius, right_MRD, right_left_length, right_right_length], debug_image_before, debug_image_after

def get_good_eyes_curve(good_eyes_curve):
	left_curve, right_curve = good_eyes_curve
	left_angle = -np.arctan2(left_curve[-1][1] - left_curve[0][1], left_curve[-1][0] - left_curve[0][0])
	right_angle = -np.arctan2(right_curve[-1][1] - right_curve[0][1], right_curve[-1][0] - right_curve[0][0])
	rotated_left_curve = rotate_curve(left_curve, left_angle, left_curve[0])
	rotated_right_curve = rotate_curve(right_curve, right_angle, right_curve[0])
	return rotated_left_curve, rotated_right_curve

def get_good_eyes_curve_combine(good_eyes_curve_list):
	left_good_eye_curve_list, right_good_eye_curve_list = good_eyes_curve_list
	left_good_eye_curve_list.sort(key=lambda curve: len(curve), reverse=True)
	right_good_eye_curve_list.sort(key=lambda curve: len(curve), reverse=True)
	if len(left_good_eye_curve_list) <= 1:
		return left_good_eye_curve_list[0], right_good_eye_curve_list[0]

	longest_left_good_eye_curve_x_range = np.max(left_good_eye_curve_list[0][::, 0]) - np.min(left_good_eye_curve_list[0][::, 0])
	longest_right_good_eye_curve_x_range = np.max(right_good_eye_curve_list[0][::, 0]) - np.min(right_good_eye_curve_list[0][::, 0])
	resized_left_good_eye_curve_list, resized_right_good_eye_curve_list = [], []
	for (left_good_eye_curve, right_good_eye_curve) in zip(left_good_eye_curve_list[1:], right_good_eye_curve_list[1:]):
		left_good_eye_curve_x_range = np.max(left_good_eye_curve[::, 0]) - np.min(left_good_eye_curve[::, 0])
		left_resize_scale = longest_left_good_eye_curve_x_range / left_good_eye_curve_x_range
		resized_left_good_eye_curve = (left_good_eye_curve - left_good_eye_curve[0]) * left_resize_scale + left_good_eye_curve_list[0][0]
		resized_left_good_eye_x_curve, resized_left_good_eye_y_curve = resample_curve(resized_left_good_eye_curve[::, 0], resized_left_good_eye_curve[::, 1], len(left_good_eye_curve_list[0]))
		resized_left_good_eye_curve = list(zip(resized_left_good_eye_x_curve, resized_left_good_eye_y_curve))
		resized_left_good_eye_curve_list.append(resized_left_good_eye_curve)
		right_good_eye_curve_x_range = np.max(right_good_eye_curve[::, 0]) - np.min(right_good_eye_curve[::, 0])
		right_resize_scale = longest_right_good_eye_curve_x_range / right_good_eye_curve_x_range
		resized_right_good_eye_curve = (right_good_eye_curve - right_good_eye_curve[0]) * right_resize_scale + right_good_eye_curve_list[0][0]
		resized_right_good_eye_x_curve, resized_right_good_eye_y_curve = resample_curve(resized_right_good_eye_curve[::, 0], resized_right_good_eye_curve[::, 1], len(right_good_eye_curve_list[0]))
		resized_right_good_eye_curve = list(zip(resized_right_good_eye_x_curve, resized_right_good_eye_y_curve))
		resized_right_good_eye_curve_list.append(resized_right_good_eye_curve)

	resized_left_good_eye_curve_list.append(left_good_eye_curve_list[0])
	resized_right_good_eye_curve_list.append(right_good_eye_curve_list[0])
	combined_left_good_eye_curve = list(zip(left_good_eye_curve_list[0][::, 0], np.mean(resized_left_good_eye_curve_list, axis=0)[::, 1]))
	combined_right_good_eye_curve = list(zip(right_good_eye_curve_list[0][::, 0], np.mean(resized_right_good_eye_curve_list, axis=0)[::, 1]))
	return combined_left_good_eye_curve, combined_right_good_eye_curve 

def get_customized_good_eye_curve(good_eyes_curve, input_eyes_curve, eye_width_iris_radius_ratio_list, good_eye_width_ratio_constant, side):
	# good eyes
	rotated_good_eyes_left_curve, rotated_good_eyes_right_curve = good_eyes_curve.copy()

	if side == 'left':
		right_anchor = rotated_good_eyes_right_curve[0]
		rotated_good_eyes_right_curve = np.zeros_like(rotated_good_eyes_left_curve)
		rotated_good_eyes_right_curve[::, 0] = right_anchor[0] + (rotated_good_eyes_left_curve[::, 0] - rotated_good_eyes_left_curve[-1][0])[::-1] * -1
		rotated_good_eyes_right_curve[::, 1] = rotated_good_eyes_left_curve[::, 1][::-1]
	elif side == 'right':
		left_anchor = rotated_good_eyes_left_curve[-1]
		rotated_good_eyes_left_curve = np.zeros_like(rotated_good_eyes_right_curve)
		rotated_good_eyes_left_curve[::, 0] = left_anchor[0] + (rotated_good_eyes_right_curve[::, 0] - rotated_good_eyes_right_curve[0][0])[::-1] * -1
		rotated_good_eyes_left_curve[::, 1] = rotated_good_eyes_right_curve[::, 1][::-1]

	left_good_eyes_x_range = np.max(rotated_good_eyes_left_curve[::, 0]) - np.min(rotated_good_eyes_left_curve[::, 0])
	right_good_eyes_x_range = np.max(rotated_good_eyes_right_curve[::, 0]) - np.min(rotated_good_eyes_right_curve[::, 0])

	# input eyes
	input_left_curve, input_right_curve = input_eyes_curve.copy()
	input_left_angle = -np.arctan2(input_left_curve[-1][1] - input_left_curve[0][1], input_left_curve[-1][0] - input_left_curve[0][0])
	input_right_angle = -np.arctan2(input_right_curve[-1][1] - input_right_curve[0][1], input_right_curve[-1][0] - input_right_curve[0][0])
	rotated_input_left_curve = np.array(rotate_curve(input_left_curve, input_left_angle, input_left_curve[0]))
	rotated_input_right_curve = np.array(rotate_curve(input_right_curve, input_right_angle, input_right_curve[0]))
	left_input_x_range = np.max(rotated_input_left_curve[::, 0]) - np.min(rotated_input_left_curve[::, 0])
	right_input_x_range = np.max(rotated_input_right_curve[::, 0]) - np.min(rotated_input_right_curve[::, 0])

	# width
	left_eye_width_iris_radius_ratio, right_eye_width_iris_radius_ratio = eye_width_iris_radius_ratio_list
	left_scale_width = left_input_x_range / left_good_eyes_x_range
	if left_eye_width_iris_radius_ratio < good_eye_width_ratio_constant:
		left_scale_width *= good_eye_width_ratio_constant / left_eye_width_iris_radius_ratio
	right_scale_width = right_input_x_range / right_good_eyes_x_range
	if right_eye_width_iris_radius_ratio < good_eye_width_ratio_constant:
		right_scale_width *= good_eye_width_ratio_constant / right_eye_width_iris_radius_ratio

	rotated_good_eyes_left_curve = (rotated_good_eyes_left_curve - rotated_good_eyes_left_curve[-1]) * left_scale_width + rotated_input_left_curve[-1]
	rotated_good_eyes_right_curve = (rotated_good_eyes_right_curve - rotated_good_eyes_right_curve[0]) * right_scale_width + rotated_input_right_curve[0]

	rotated_customized_left_curve = np.array(rotate_curve(rotated_good_eyes_left_curve, -input_left_angle, rotated_good_eyes_left_curve[0]))
	rotated_customized_right_curve = np.array(rotate_curve(rotated_good_eyes_right_curve, -input_right_angle, rotated_good_eyes_right_curve[0]))

	customized_left_curve = np.zeros_like(input_left_curve)
	customized_right_curve = np.zeros_like(input_right_curve)
	customized_left_curve[::, 0], customized_left_curve[::, 1] = resample_curve(rotated_customized_left_curve[::, 0], rotated_customized_left_curve[::, 1], len(input_left_curve))
	customized_right_curve[::, 0], customized_right_curve[::, 1] = resample_curve(rotated_customized_right_curve[::, 0], rotated_customized_right_curve[::, 1], len(input_right_curve))
	return customized_left_curve, customized_right_curve

def get_eye_width_info(align_points_92):
	modified_align_points_92 = align_points_92.copy()
	left_eye_indices = [landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE'],
						landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT'],
						landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER'],
						landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT'],
						landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']
					   ]
	left_eye_points = modified_align_points_92[left_eye_indices]
	left_angle = -np.arctan2(left_eye_points[-1][1] - left_eye_points[0][1], left_eye_points[-1][0] - left_eye_points[0][0])
	rotated_left_eye_points = rotate_curve(left_eye_points, left_angle, left_eye_points[0])

	right_eye_indices = [landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN'],
						 landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT'],
						 landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER'],
						 landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT'],
						 landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE'],
					    ]
	right_eye_points = modified_align_points_92[right_eye_indices]
	right_angle = -np.arctan2(right_eye_points[-1][1] - right_eye_points[0][1], right_eye_points[-1][0] - right_eye_points[0][0])
	rotated_right_eye_points = rotate_curve(right_eye_points, right_angle, right_eye_points[0])

	left_iris_radius = max(util.PointDistance(rotated_left_eye_points[2], rotated_left_eye_points[1]), util.PointDistance(rotated_left_eye_points[2], rotated_left_eye_points[3]))
	right_iris_radius = max(util.PointDistance(rotated_right_eye_points[2], rotated_right_eye_points[1]), util.PointDistance(rotated_right_eye_points[2], rotated_right_eye_points[3]))
	
	left_eye_width = util.PointDistance(rotated_left_eye_points[0], rotated_left_eye_points[-1])
	right_eye_width = util.PointDistance(rotated_right_eye_points[0], rotated_right_eye_points[-1])

	left_eye_width_iris_radius_ratio = left_eye_width / left_iris_radius
	right_eye_width_iris_radius_ratio = right_eye_width / right_iris_radius
	return left_eye_width_iris_radius_ratio, right_eye_width_iris_radius_ratio

def manual_modify_align_points(image_name, align_points_92):
	modified_align_points_92 = align_points_92.copy()
	if image_name == '83a9f75cac2b7e7ea3ece9bfed429edc.jpg':
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']][1] -= 2
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M2']][1] -= 5
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']][0] += 16
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']][1] -= 3
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']][1] -= 3
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']][0] -= 4
	elif image_name == '876cc2ef7bfcb1cd6a549feed02e7c82.jpg':
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']][1] += 2
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_TOP']][1] += 3
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']][0] += 13
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']][1] += 7
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']][0] += 15
		modified_align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']][1] -= 3
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M1']][1] += 4
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_TOP']][1] += 3
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']][1] += 2
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']][0] -= 17
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']][1] += 10
		modified_align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']][0] -= 5
	return modified_align_points_92

def manual_modify_curve(image_name):
	modified = False
	curves = None
	if image_name == 'Bilateral-endoscopic-forehead-lift-_before.jpg':
		modified = True
		supplements_path = 'special_case_supplements'
		left_red_image = cv2.imread(os.path.join(supplements_path, image_name + '_left_red.png'))
		left_green_image = cv2.imread(os.path.join(supplements_path, image_name + '_left_green.png'))
		left_blue_image = cv2.imread(os.path.join(supplements_path, image_name + '_left_blue.png'))
		right_red_image = cv2.imread(os.path.join(supplements_path, image_name + '_right_red.png'))
		right_green_image = cv2.imread(os.path.join(supplements_path, image_name + '_right_green.png'))
		right_blue_image = cv2.imread(os.path.join(supplements_path, image_name + '_right_blue.png'))

		left_red_y_curve, left_red_x_curve, _ = np.where(left_red_image)
		left_green_y_curve, left_green_x_curve, _ = np.where(left_green_image)
		left_blue_y_curve, left_blue_x_curve, _ = np.where(left_blue_image)
		right_red_y_curve, right_red_x_curve, _ = np.where(right_red_image)
		right_green_y_curve, right_green_x_curve, _ = np.where(right_green_image)
		right_blue_y_curve, right_blue_x_curve, _ = np.where(right_blue_image)

		left_red_x_curve, left_red_y_curve = remove_duplicate_x(left_red_x_curve, left_red_y_curve)
		left_green_x_curve, left_green_y_curve = remove_duplicate_x(left_green_x_curve, left_green_y_curve)
		left_blue_x_curve, left_blue_y_curve = remove_duplicate_x(left_blue_x_curve, left_blue_y_curve)
		right_red_x_curve, right_red_y_curve = remove_duplicate_x(right_red_x_curve, right_red_y_curve)
		right_green_x_curve, right_green_y_curve = remove_duplicate_x(right_green_x_curve, right_green_y_curve)
		right_blue_x_curve, right_blue_y_curve = remove_duplicate_x(right_blue_x_curve, right_blue_y_curve)

		left_red_curve = np.array(list(zip(left_red_x_curve, left_red_y_curve)))
		left_green_curve = np.array(list(zip(left_green_x_curve, left_green_y_curve)))
		left_blue_curve = np.array(list(zip(left_blue_x_curve, left_blue_y_curve)))
		right_red_curve = np.array(list(zip(right_red_x_curve, right_red_y_curve)))
		right_green_curve = np.array(list(zip(right_green_x_curve, right_green_y_curve)))
		right_blue_curve = np.array(list(zip(right_blue_x_curve, right_blue_y_curve)))

		curves = [left_red_curve, left_green_curve, left_blue_curve, right_red_curve, right_green_curve, right_blue_curve]
	return modified, curves

def manual_modify_morphing(image, curves, align_points_92, emulation_type, morphing=True, corner=True, debug=False, white_line_ratio_factor=2.0, left_good_eye_curve=None, right_good_eye_curve=None, balance=True):
	left_fixed_curve, left_src_curve, left_dst_curve, right_fixed_curve, right_src_curve, right_dst_curve = curves
	left_fixed_points_x_curve, left_fixed_points_y_curve = left_fixed_curve[::, 0], left_fixed_curve[::, 1]
	left_src_points_x_curve, left_src_points_y_curve = left_src_curve[::, 0], left_src_curve[::, 1]
	left_dst_points_x_curve, left_dst_points_y_curve = left_dst_curve[::, 0], left_dst_curve[::, 1]
	right_fixed_points_x_curve, right_fixed_points_y_curve = right_fixed_curve[::, 0], right_fixed_curve[::, 1]
	right_src_points_x_curve, right_src_points_y_curve = right_src_curve[::, 0], right_src_curve[::, 1]
	right_dst_points_x_curve, right_dst_points_y_curve = right_dst_curve[::, 0], right_dst_curve[::, 1]

	_, _, _, left_brow_points = get_morphed_points(align_points_92, side='left', emulation_type=emulation_type)
	_, _, _, right_brow_points = get_morphed_points(align_points_92, side='right', emulation_type=emulation_type)

	if not morphing:
		return image, [left_src_points_x_curve, left_src_points_y_curve, right_src_points_x_curve, right_src_points_y_curve]

	if left_good_eye_curve is not None:
		left_dst_points_x_curve, left_dst_points_y_curve = left_good_eye_curve[::, 0], left_good_eye_curve[::, 1]
		left_brow_points_x_curve, left_brow_points_y_curve, _ = form_curve_v2(left_brow_points, min_x=min(left_good_eye_curve[::, 0]), max_x=max(left_good_eye_curve[::, 0]))
	else:
		left_dst_points_x_curve, left_dst_points_y_curve, _ = form_curve_v2(left_upper_points)
		left_brow_points_x_curve, left_brow_points_y_curve, _ = form_curve_v2(left_brow_points, min_x=min(left_fixed_points_x_curve), max_x=max(left_fixed_points_x_curve))

	if right_good_eye_curve is not None:
		right_dst_points_x_curve, right_dst_points_y_curve = right_good_eye_curve[::, 0], right_good_eye_curve[::, 1]
		right_brow_points_x_curve, right_brow_points_y_curve, _ = form_curve_v2(right_brow_points, min_x=min(right_good_eye_curve[::, 0]), max_x=max(right_good_eye_curve[::, 0]), side='right')
	else:
		right_dst_points_x_curve, right_dst_points_y_curve, _ = form_curve_v2(right_upper_points, side='right')
		right_brow_points_x_curve, right_brow_points_y_curve, _ = form_curve_v2(right_brow_points, min_x=min(right_fixed_points_x_curve), max_x=max(right_fixed_points_x_curve), side='right')

	if balance:
		# left eye
		left_dst_points_curve = list(zip(left_dst_points_x_curve, left_dst_points_y_curve))
		left_angle = -np.arctan2(left_dst_points_curve[-1][1] - left_dst_points_curve[0][1], left_dst_points_curve[-1][0] - left_dst_points_curve[0][0])
		left_dst_points_curve = np.array(rotate_curve(left_dst_points_curve, left_angle, left_dst_points_curve[0]))
		left_amount = np.max(left_dst_points_curve[::, 1]) - np.min(left_dst_points_curve[::, 1])

		# right eye
		right_dst_points_curve = list(zip(right_dst_points_x_curve, right_dst_points_y_curve))
		right_angle = -np.arctan2(right_dst_points_curve[-1][1] - right_dst_points_curve[0][1], right_dst_points_curve[-1][0] - right_dst_points_curve[0][0])
		right_dst_points_curve = np.array(rotate_curve(right_dst_points_curve, right_angle, right_dst_points_curve[0]))
		right_amount = np.max(right_dst_points_curve[::, 1]) - np.min(right_dst_points_curve[::, 1])

		balance_amount = 0.5 * (left_amount + right_amount)
		left_dst_points_curve[::, 1] = (left_dst_points_curve[::, 1] - left_dst_points_curve[0][1]) / left_amount * balance_amount + left_dst_points_curve[0][1]
		right_dst_points_curve[::, 1] = (right_dst_points_curve[::, 1] - right_dst_points_curve[0][1]) / right_amount * balance_amount + right_dst_points_curve[0][1]

		left_dst_points_curve = np.array(rotate_curve(left_dst_points_curve, -left_angle, left_dst_points_curve[0]))
		right_dst_points_curve = np.array(rotate_curve(right_dst_points_curve, -right_angle, right_dst_points_curve[0]))

		left_dst_points_x_curve, left_dst_points_y_curve = left_dst_points_curve[::, 0], left_dst_points_curve[::, 1]
		right_dst_points_x_curve, right_dst_points_y_curve = right_dst_points_curve[::, 0], right_dst_points_curve[::, 1]

	no_corner_left_fixed_points_y_curve = left_fixed_points_y_curve.copy()
	no_corner_right_fixed_points_y_curve = right_fixed_points_y_curve.copy()

	if left_good_eye_curve is not None:
		fixed_points_sample_size = round(left_fixed_points_x_curve[0] - left_dst_points_x_curve[0]) + 1
		left_fixed_points_x_curve_new = np.linspace(left_dst_points_x_curve[0], left_fixed_points_x_curve[0], fixed_points_sample_size)
		left_fixed_points_y_curve_new = np.linspace(left_dst_points_y_curve[0], left_fixed_points_y_curve[0], fixed_points_sample_size)
		left_fixed_points_x_curve = np.concatenate([left_fixed_points_x_curve_new, left_fixed_points_x_curve])
		left_fixed_points_y_curve = np.concatenate([left_fixed_points_y_curve_new, left_fixed_points_y_curve])
		left_fixed_points_x_curve, left_fixed_points_y_curve = remove_duplicate_x(left_fixed_points_x_curve, left_fixed_points_y_curve)

		src_points_sample_size = round(left_src_points_x_curve[0] - left_dst_points_x_curve[0]) + 1
		left_src_points_x_curve_new = np.linspace(left_dst_points_x_curve[0], left_src_points_x_curve[0], src_points_sample_size)
		left_src_points_y_curve_new = np.linspace(left_dst_points_y_curve[0], left_src_points_y_curve[0], src_points_sample_size)
		left_src_points_x_curve = np.concatenate([left_src_points_x_curve_new, left_src_points_x_curve])
		left_src_points_y_curve = np.concatenate([left_src_points_y_curve_new, left_src_points_y_curve])
		left_src_points_x_curve, left_src_points_y_curve = remove_duplicate_x(left_src_points_x_curve, left_src_points_y_curve)

	if right_good_eye_curve is not None:
		fixed_points_sample_size = round(right_dst_points_x_curve[-1] - right_fixed_points_x_curve[-1]) + 1
		right_fixed_points_x_curve_new = np.linspace(right_fixed_points_x_curve[-1], right_dst_points_x_curve[-1], fixed_points_sample_size)
		right_fixed_points_y_curve_new = np.linspace(right_fixed_points_y_curve[-1], right_dst_points_y_curve[-1], fixed_points_sample_size)
		right_fixed_points_x_curve = np.concatenate([right_fixed_points_x_curve, right_fixed_points_x_curve_new])
		right_fixed_points_y_curve = np.concatenate([right_fixed_points_y_curve, right_fixed_points_y_curve_new])
		right_fixed_points_x_curve, right_fixed_points_y_curve = remove_duplicate_x(right_fixed_points_x_curve, right_fixed_points_y_curve)

		src_points_sample_size = round(right_dst_points_x_curve[-1] - right_src_points_x_curve[-1]) + 1
		right_src_points_x_curve_new = np.linspace(right_src_points_x_curve[-1], right_dst_points_x_curve[-1], src_points_sample_size)
		right_src_points_y_curve_new = np.linspace(right_src_points_y_curve[-1], right_dst_points_y_curve[-1], src_points_sample_size)
		right_src_points_x_curve = np.concatenate([right_src_points_x_curve, right_src_points_x_curve_new])
		right_src_points_y_curve = np.concatenate([right_src_points_y_curve, right_src_points_y_curve_new])
		right_src_points_x_curve, right_src_points_y_curve = remove_duplicate_x(right_src_points_x_curve, right_src_points_y_curve)

	left_fixed_points_x_curve, left_fixed_points_y_curve = resample_curve(left_fixed_points_x_curve, left_fixed_points_y_curve, len(left_brow_points_x_curve))
	left_src_points_x_curve, left_src_points_y_curve = resample_curve(left_src_points_x_curve, left_src_points_y_curve, len(left_brow_points_x_curve))
	left_dst_points_x_curve, left_dst_points_y_curve = resample_curve(left_dst_points_x_curve, left_dst_points_y_curve, len(left_brow_points_x_curve))

	right_fixed_points_x_curve, right_fixed_points_y_curve = resample_curve(right_fixed_points_x_curve, right_fixed_points_y_curve, len(right_brow_points_x_curve))
	right_src_points_x_curve, right_src_points_y_curve = resample_curve(right_src_points_x_curve, right_src_points_y_curve, len(right_brow_points_x_curve))
	right_dst_points_x_curve, right_dst_points_y_curve = resample_curve(right_dst_points_x_curve, right_dst_points_y_curve, len(right_brow_points_x_curve))

	left_dst_points_y_curve = np.minimum(left_dst_points_y_curve, left_src_points_y_curve)
	right_dst_points_y_curve = np.minimum(right_dst_points_y_curve, right_src_points_y_curve)

	left_fixed_points_x_curve[0] = left_src_points_x_curve[0] = left_dst_points_x_curve[0] = left_brow_points_x_curve[0]
	left_fixed_points_y_curve[0] = left_src_points_y_curve[0] = left_dst_points_y_curve[0]
	left_fixed_points_x_curve[-1] = left_src_points_x_curve[-1] = left_dst_points_x_curve[-1] = left_brow_points_x_curve[-1]
	left_fixed_points_y_curve[-1] = left_src_points_y_curve[-1] = left_dst_points_y_curve[-1]
	right_fixed_points_x_curve[0] = right_src_points_x_curve[0] = right_dst_points_x_curve[0] = right_brow_points_x_curve[0]
	right_fixed_points_y_curve[0] = right_src_points_y_curve[0] = right_dst_points_y_curve[0]
	right_fixed_points_x_curve[-1] = right_src_points_x_curve[-1] = right_dst_points_x_curve[-1] = right_brow_points_x_curve[-1]
	right_fixed_points_y_curve[-1] = right_src_points_y_curve[-1] = right_dst_points_y_curve[-1]

	left_white_line_ratio = calcualte_white_line_ratio(left_brow_points_y_curve, left_dst_points_y_curve, left_src_points_y_curve, white_line_ratio_factor)
	left_brow_points_y_curve = left_white_line_ratio * left_brow_points_y_curve + (1.0 - left_white_line_ratio) * left_dst_points_y_curve
	right_white_line_ratio = calcualte_white_line_ratio(right_brow_points_y_curve, right_dst_points_y_curve, right_src_points_y_curve, white_line_ratio_factor)
	right_brow_points_y_curve = right_white_line_ratio * right_brow_points_y_curve + (1.0 - right_white_line_ratio) * right_dst_points_y_curve

	if debug:
		debug_image = align_utils.draw_lines_xy(image.copy(), left_fixed_points_x_curve, left_fixed_points_y_curve, color=RED, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_src_points_x_curve, left_src_points_y_curve, color=GREEN, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_dst_points_x_curve, left_dst_points_y_curve, color=BLUE, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, left_brow_points_x_curve, left_brow_points_y_curve, color=WHITE, thickness=1, line_type=cv2.LINE_AA)

		debug_image = align_utils.draw_lines_xy(debug_image, right_fixed_points_x_curve, right_fixed_points_y_curve, color=RED, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_src_points_x_curve, right_src_points_y_curve, color=GREEN, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_dst_points_x_curve, right_dst_points_y_curve, color=BLUE, thickness=1, line_type=cv2.LINE_AA)
		debug_image = align_utils.draw_lines_xy(debug_image, right_brow_points_x_curve, right_brow_points_y_curve, color=WHITE, thickness=1, line_type=cv2.LINE_AA)
		
		# For training data inpainting mask
		left_src_curve = np.array(list(zip(left_src_points_x_curve, left_src_points_y_curve)))
		left_lower_curve = np.array(list(zip(left_fixed_points_x_curve, no_corner_left_fixed_points_y_curve)))
		right_src_curve = np.array(list(zip(right_src_points_x_curve, right_src_points_y_curve)))
		right_lower_curve = np.array(list(zip(right_fixed_points_x_curve, no_corner_right_fixed_points_y_curve)))
		return debug_image, [left_src_curve, left_lower_curve, right_src_curve, right_lower_curve]

	morphed_image = np.zeros_like(image)
	morphed_image = warp_image_batch(image, morphed_image, left_brow_points_x_curve, left_brow_points_y_curve, left_src_points_y_curve, left_brow_points_y_curve, left_dst_points_y_curve)
	morphed_image = warp_image_batch(image, morphed_image, left_src_points_x_curve, left_src_points_y_curve, left_fixed_points_y_curve, left_dst_points_y_curve, left_fixed_points_y_curve)
	morphed_image = warp_image_batch(image, morphed_image, right_brow_points_x_curve, right_brow_points_y_curve, right_src_points_y_curve, right_brow_points_y_curve, right_dst_points_y_curve)
	morphed_image = warp_image_batch(image, morphed_image, right_src_points_x_curve, right_src_points_y_curve, right_fixed_points_y_curve, right_dst_points_y_curve, right_fixed_points_y_curve)
	morphed_image = np.where(morphed_image, morphed_image, image)

	# For testing data inpainting mask
	left_upper_curve = np.array(list(zip(left_dst_points_x_curve, left_dst_points_y_curve)))
	left_lower_curve = np.array(list(zip(left_fixed_points_x_curve, left_fixed_points_y_curve)))
	right_upper_curve = np.array(list(zip(right_dst_points_x_curve, right_dst_points_y_curve)))
	right_lower_curve = np.array(list(zip(right_fixed_points_x_curve, right_fixed_points_y_curve)))
	return morphed_image, [left_upper_curve, left_lower_curve, right_upper_curve, right_lower_curve]

def get_lower_mask_points(align_points_92):
	left_lower_mask_points = []

	left_most_point = 2 * align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']] - align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M1']]
	left_lower_mask_points.append(left_most_point)
	left_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']])
	left_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M4']])
	left_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']])
	left_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M3']])
	left_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']])
	left_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_INSIDE_OUT']])
	left_iris_radius = max(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_LEFT']]), 
						   util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_IRIS_RIGHT']]))

	left_eye_M3_shift = align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M3']].copy() + np.array([0.0, left_iris_radius])
	left_eye_BOTTOM_shift = align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_BOTTOM']].copy() + np.array([0.0, left_iris_radius])
	left_eye_M4_shift = align_points_92[landmark92_tag2index['LBF_3D_LEFT_EYE_M4']].copy() + np.array([0.0, left_iris_radius])
	left_lower_mask_points.append(left_eye_M3_shift)
	left_lower_mask_points.append(left_eye_BOTTOM_shift)
	left_lower_mask_points.append(left_eye_M4_shift)

	right_lower_mask_points = []

	right_most_point = 2 * align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']] - align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M2']]
	right_lower_mask_points.append(right_most_point)
	right_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']])
	right_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M3']])
	right_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']])
	right_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M4']])
	right_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']])
	right_lower_mask_points.append(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_INSIDE_OUT']])
	right_iris_radius = max(util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_LEFT']]), 
							util.PointDistance(align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_CENTER']], align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_IRIS_RIGHT']]))
	right_eye_M4_shift = align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M4']].copy() + np.array([0.0, right_iris_radius])
	right_eye_BOTTOM_shift = align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']].copy() + np.array([0.0, right_iris_radius])
	right_eye_M3_shift = align_points_92[landmark92_tag2index['LBF_3D_RIGHT_EYE_M3']].copy() + np.array([0.0, right_iris_radius])
	right_lower_mask_points.append(right_eye_M4_shift)
	right_lower_mask_points.append(right_eye_BOTTOM_shift)
	right_lower_mask_points.append(right_eye_M3_shift)
	return np.array(left_lower_mask_points), np.array(right_lower_mask_points)

def process_lower_points_to_mask(image, left_lower_mask_points, right_lower_mask_points):
	copy_image = image.copy()

	# [Left part]
	left_left_coefficients = np.polyfit(left_lower_mask_points[0:2, 0], left_lower_mask_points[0:2, 1], 1)
	# Create a polynomial object
	left_left_polynomial = np.poly1d(left_left_coefficients)
	# Generate x values for plotting the curve
	left_left_min_x = left_lower_mask_points[0][0]
	left_left_max_x = left_lower_mask_points[1][0]
	left_left_sample_size = round(left_left_max_x - left_left_min_x) + 1
	left_left_x_curve = np.linspace(left_left_min_x, left_left_max_x, left_left_sample_size)
	# Calculate corresponding y values
	left_left_y_curve = left_left_polynomial(left_left_x_curve)

	left_right_coefficients = np.polyfit(left_lower_mask_points[5:7, 0], left_lower_mask_points[5:7, 1], 1)
	# Create a polynomial object
	left_right_polynomial = np.poly1d(left_right_coefficients)
	# Generate x values for plotting the curve
	left_right_min_x = left_lower_mask_points[5][0]
	left_right_max_x = left_lower_mask_points[6][0]
	left_right_sample_size = round(left_right_max_x - left_right_min_x) + 1
	left_right_x_curve = np.linspace(left_right_min_x, left_right_max_x, left_right_sample_size)
	# Calculate corresponding y values
	left_right_y_curve = left_right_polynomial(left_right_x_curve)

	left_lower_mask_upper_x_curve, left_lower_mask_upper_y_curve, _ = form_curve_v2(left_lower_mask_points[1:6])
	left_lower_mask_upper_x_curve = np.concatenate([left_left_x_curve, left_lower_mask_upper_x_curve, left_right_x_curve])
	left_lower_mask_upper_y_curve = np.concatenate([left_left_y_curve, left_lower_mask_upper_y_curve, left_right_y_curve])
	left_lower_mask_upper_x_curve, left_lower_mask_upper_y_curve = remove_duplicate_x(left_lower_mask_upper_x_curve, left_lower_mask_upper_y_curve)

	left_lower_mask_lower_curve_indices = [0, 9, 8, 7, 6]
	left_lower_mask_lower_x_curve, left_lower_mask_lower_y_curve, _ = form_curve_v2(left_lower_mask_points[left_lower_mask_lower_curve_indices])
	left_lower_mask_lower_x_curve, left_lower_mask_lower_y_curve = resample_curve(left_lower_mask_lower_x_curve, left_lower_mask_lower_y_curve, len(left_lower_mask_upper_x_curve))

	left_lower_mask_upper_x_curve, left_lower_mask_upper_y_curve = left_lower_mask_upper_x_curve.astype(np.int32), left_lower_mask_upper_y_curve.astype(np.int32)
	left_lower_mask_lower_x_curve, left_lower_mask_lower_y_curve = left_lower_mask_lower_x_curve.astype(np.int32), left_lower_mask_lower_y_curve.astype(np.int32)

	# [Fill left mask]
	for i, x in enumerate(left_lower_mask_upper_x_curve):
		copy_image[np.min(left_lower_mask_upper_y_curve[i]):np.max(left_lower_mask_lower_y_curve[i])+1, x] = WHITE
		if i > 0 and np.sum(copy_image[:, x-1]) == 0:
			copy_image[np.min(left_lower_mask_upper_y_curve[i]):np.max(left_lower_mask_lower_y_curve[i])+1, x-1] = WHITE

	# [Right part]
	right_right_coefficients = np.polyfit(right_lower_mask_points[0:2, 0], right_lower_mask_points[0:2, 1], 1)
	# Create a polynomial object
	right_right_polynomial = np.poly1d(right_right_coefficients)
	# Generate x values for plotting the curve
	right_right_min_x = right_lower_mask_points[1][0]
	right_right_max_x = right_lower_mask_points[0][0]
	right_right_sample_size = round(right_right_max_x - right_right_min_x) + 1
	right_right_x_curve = np.linspace(right_right_min_x, right_right_max_x, right_right_sample_size)
	# Calculate corresponding y values
	right_right_y_curve = right_right_polynomial(right_right_x_curve)

	right_left_coefficients = np.polyfit(right_lower_mask_points[5:7, 0], right_lower_mask_points[5:7, 1], 1)
	# Create a polynomial object
	right_left_polynomial = np.poly1d(right_left_coefficients)
	# Generate x values for plotting the curve
	right_left_min_x = right_lower_mask_points[6][0]
	right_left_max_x = right_lower_mask_points[5][0]
	right_left_sample_size = round(right_left_max_x - right_left_min_x) + 1
	right_left_x_curve = np.linspace(right_left_min_x, right_left_max_x, right_left_sample_size)
	# Calculate corresponding y values
	right_left_y_curve = right_left_polynomial(right_left_x_curve)

	right_lower_mask_upper_x_curve, right_lower_mask_upper_y_curve, _ = form_curve_v2(right_lower_mask_points[1:6], side='right')
	right_lower_mask_upper_x_curve = np.concatenate([right_right_x_curve, right_lower_mask_upper_x_curve, right_left_x_curve])
	right_lower_mask_upper_y_curve = np.concatenate([right_right_y_curve, right_lower_mask_upper_y_curve, right_left_y_curve])
	right_lower_mask_upper_x_curve, right_lower_mask_upper_y_curve = remove_duplicate_x(right_lower_mask_upper_x_curve, right_lower_mask_upper_y_curve)

	right_lower_mask_lower_curve_indices = [0, 9, 8, 7, 6]
	right_lower_mask_lower_x_curve, right_lower_mask_lower_y_curve, _ = form_curve_v2(right_lower_mask_points[right_lower_mask_lower_curve_indices], side='right')
	right_lower_mask_lower_x_curve, right_lower_mask_lower_y_curve = resample_curve(right_lower_mask_lower_x_curve, right_lower_mask_lower_y_curve, len(right_lower_mask_upper_x_curve))

	right_lower_mask_upper_x_curve, right_lower_mask_upper_y_curve = right_lower_mask_upper_x_curve.astype(np.int32), right_lower_mask_upper_y_curve.astype(np.int32)
	right_lower_mask_lower_x_curve, right_lower_mask_lower_y_curve = right_lower_mask_lower_x_curve.astype(np.int32), right_lower_mask_lower_y_curve.astype(np.int32)

	# [Fill left mask]
	for i, x in enumerate(right_lower_mask_upper_x_curve):
		copy_image[np.min(right_lower_mask_upper_y_curve[i]):np.max(right_lower_mask_lower_y_curve[i])+1, x] = WHITE
		if i > 0 and np.sum(copy_image[:, x-1]) == 0:
			copy_image[np.min(right_lower_mask_upper_y_curve[i]):np.max(right_lower_mask_lower_y_curve[i])+1, x-1] = WHITE
	return copy_image