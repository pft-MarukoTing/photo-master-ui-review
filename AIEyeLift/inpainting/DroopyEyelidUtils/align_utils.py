import cv2
import cv2 as cv
import numpy as np
import math
import time

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
	'LBF_3D_LEFT_BROW_1',
	'LBF_3D_LEFT_BROW_2',
	'LBF_3D_LEFT_BROW_3',
	'LBF_3D_LEFT_BROW_4',
	'LBF_3D_LEFT_BROW_5',

	'LBF_3D_LEFT_EYE_OUTSIDE',
	'LBF_3D_LEFT_EYE_TOP',
	'LBF_3D_LEFT_EYE_INSIDE_OUT',
	'LBF_3D_LEFT_EYE_BOTTOM',
	'LBF_3D_LEFT_EYE_INSIDE_IN',
	'LBF_3D_LEFT_EYE_CENTER',
	'LBF_3D_LEFT_EYE_M1',
	'LBF_3D_LEFT_EYE_M2',
	'LBF_3D_LEFT_EYE_M3',
	'LBF_3D_LEFT_EYE_M4',

	'LBF_3D_RIGHT_BROW_1',
	'LBF_3D_RIGHT_BROW_2',
	'LBF_3D_RIGHT_BROW_3',
	'LBF_3D_RIGHT_BROW_4',
	'LBF_3D_RIGHT_BROW_5',

	'LBF_3D_RIGHT_EYE_INSIDE_OUT',
	'LBF_3D_RIGHT_EYE_TOP',
	'LBF_3D_RIGHT_EYE_OUTSIDE',
	'LBF_3D_RIGHT_EYE_BOTTOM',
	'LBF_3D_RIGHT_EYE_INSIDE_IN',
	'LBF_3D_RIGHT_EYE_CENTER',
	'LBF_3D_RIGHT_EYE_M1',
	'LBF_3D_RIGHT_EYE_M2',
	'LBF_3D_RIGHT_EYE_M3',
	'LBF_3D_RIGHT_EYE_M4',

	'LBF_3D_NOSE_RIDGE_1',
	'LBF_3D_NOSE_RIDGE_2',
	'LBF_3D_NOSE_RIDGE_3',
	'LBF_3D_NOSE_RIDGE_4',
	'LBF_3D_NOSE_BOTTOM_1',
	'LBF_3D_NOSE_BOTTOM_3',
	'LBF_3D_NOSE_BOTTOM_5',
	'LBF_3D_NOSE_LEFT',            
	'LBF_3D_NOSE_RIGHT',               

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
	'LBF_3D_MOUTH_TOP_LIP_TOP_M4',

	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_2',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_1',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_3',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M1',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M2',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M3',
	'LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M4',

	'LBF_3D_MOUTH_LEFT_INNER_CORNER',
	'LBF_3D_MOUTH_TOP_LIP_BOTTOM',
	'LBF_3D_MOUTH_RIGHT_INNER_CORNER',
	'LBF_3D_MOUTH_TOP_LIP_BOTTOM_M1',
	'LBF_3D_MOUTH_TOP_LIP_BOTTOM_M2',
	'LBF_3D_MOUTH_BOTTOM_LIP_TOP',
	'LBF_3D_MOUTH_BOTTOM_LIP_TOP_M1',
	'LBF_3D_MOUTH_BOTTOM_LIP_TOP_M2',

	'LBF_3D_LEFT_FACE_SHAPE_1',
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
	'LBF_3D_RIGHT_FACE_SHAPE_8',

	'LBF_3D_LEFT_BROW_BOTTOM_2',
	'LBF_3D_RIGHT_BROW_BOTTOM_2',
]

SB_LANDMARK_AMOUNT = len(SB_Face_LandmarkType) # 51
LBF_3D_LANDMARK_AMOUNT = len(LBF3DLandmarkType) # 84
landmark51_tag2index = {tag: index for index, tag in enumerate(SB_Face_LandmarkType)}
landmark84_tag2index = {tag: index for index, tag in enumerate(LBF3DLandmarkType)}

def custom_loss_fn(y_true, y_pred, sample_weight=None):
	# ignore sample weight currently
	return K.mean(K.square(y_true - y_pred), axis=-1)

def draw_points(image, points, radius=3, color=[0,0,255], thickness=-1):
	for point in points:
		image = cv.circle(image, tuple(point.astype(np.int32)), radius=radius, color=color, thickness=thickness)
	return image
	
def draw_points_with_sequence(image, points, radius=3, color=[0,0,255], thickness=-1):
	for index, point in enumerate(points):
		weight = (index + 1) / len(points)
		c = [int(weight * c) for c in color]
		image = cv.circle(image, tuple(point.astype(np.int32)), radius=radius, color=c, thickness=thickness)
	return image

def draw_lines(image, points, color=[255,255,255], thickness=1, line_type=cv.LINE_AA):
	for index in range(len(points) - 1):
		start_point, end_point = points[index], points[index+1]
		image = cv.line(image, tuple(start_point.astype(np.int32)), tuple(end_point.astype(np.int32)), color, thickness=thickness, lineType=line_type)
	return image

def draw_lines_xy(image, points_x, points_y, color=[255,255,255], thickness=1, line_type=cv.LINE_AA):
	for index in range(len(points_x) - 1):
		start_point, end_point = np.array([points_x[index], points_y[index]]), np.array([points_x[index+1], points_y[index+1]])
		image = cv.line(image, tuple(start_point.astype(np.int32)), tuple(end_point.astype(np.int32)), color, thickness=thickness, lineType=line_type)
	return image

def draw_rectangle(image, rect, color=[255,0,0], thickness=2):
	# rect [x, y, width, height]
	rect = np.array(rect).astype(np.int32)
	start_point = tuple(rect[0:2])
	end_point = tuple(start_point + rect[2:])
	image = cv2.rectangle(image, start_point, end_point, color, thickness)
	return image

def union_rect(r1, r2):
	return (
		min(r1[0], r2[0]),
		min(r1[1], r2[1]),
		max(r1[2] + r1[0], r2[2] + r2[0]) - min(r1[0], r2[0]),
		max(r1[3] + r1[1], r2[3] + r2[1]) - min(r1[1], r2[1]),
	)


def intersect_rect(r1, r2):
	return (
		max(r1[0], r2[0]),
		max(r1[1], r2[1]),
		max(min(r1[2] + r1[0], r2[2] + r2[0]) - max(r1[0], r2[0]), 0),
		max(min(r1[3] + r1[1], r2[3] + r2[1]) - max(r1[1], r2[1]), 0),
	)


def iou(r1, r2):
	ir, ur = intersect_rect(r1, r2), union_rect(r1, r2)
	ira = ir[2] * ir[3]
	ura = ur[2] * ur[3]
	return ira / ura if ura > 0 else 0

def align_preprocess(img, bbox, dst_size):
	oh, ow = dst_size
	length_ratio = .6
	face_length = (bbox[2] + bbox[3]) * length_ratio
	face_middle = (bbox[0] + bbox[2] / 2., bbox[1] + bbox[3] / 2.)

	transform_scale = ow / face_length
	transform_mat = cv.getRotationMatrix2D(tuple(face_middle), 0., transform_scale)
	transform_mat[0, 2] += -face_middle[0] + ow / 2.
	transform_mat[1, 2] += -face_middle[1] + oh / 2.

	def normalize(img):
		if len(img.shape) == 2:
			return np.expand_dims(img.astype(np.float32) * 2.0 / 255.0 - 1.0, axis=-1)
		else:
			return img.astype(np.float32) * 2.0 / 255.0 - 1.0

	return (normalize(cv.warpAffine(img, transform_mat[:2, :3], (ow, oh))),
			np.vstack((transform_mat, np.asarray([0., 0., 1.]))))

def get_transformed_shape(mat, shape):
	return np.dot(mat[0:2, 0:2], shape.transpose()).transpose() + mat[0:2, 2].transpose()

def align_face(model, face_rect, img, infer_size=(256, 256)):
	face, transform = align_preprocess(img, face_rect, infer_size)
	transform = np.linalg.inv(transform)
	points = model(face[np.newaxis, :])[0].numpy().reshape(-1, 2) * np.asarray(infer_size)
	points_on_original = get_transformed_shape(transform, points)
	return points_on_original

def detect_face(model, src_img):
	GROUP_THRESHOLD = .25
	SCORE_THREHSOLD = .7
	anchor_list_dict = {
		32: [[-248, -248, 263, 263], [-120, -120, 135, 135]],
		16: [[-56, -56, 71, 71], [-24, -24, 39, 39]],
		8: [[-8, -8, 23, 23], [0, 0, 15, 15]],
	}

	img = src_img[np.newaxis, :src_img.shape[0] // 32 * 32, :src_img.shape[1] // 32 * 32, ::-1]
	tensors = model(img)
	tensors = sorted(tensors, key=lambda s: (s.shape[1], s.shape[3]))
	result_tuple, best_detect = [], []
	face_count = 0
	for idx in range(0, len(tensors), len(tensors) // 3):
		_, h, w, c = tensors[idx].shape
		stride = img.shape[1] / h

		score = tensors[idx].numpy()
		curr_shape = score.shape
		score = np.exp(np.swapaxes(score.reshape(*curr_shape[:-1], 2, 2), -2, -1))
		score /= np.sum(score, axis=-1)[..., np.newaxis]
		score = np.swapaxes(score, -2, -1).reshape(*curr_shape)[0]
		score = score[..., 2:]
		indices = [
			np.unravel_index(index,
							 score.shape) for index, value in enumerate(
								 score.reshape(-1)
							 ) if value > SCORE_THREHSOLD
		]
		for max_index in indices:
			max_score = score[max_index]
			anchor_start = anchor_list_dict[stride][max_index[2]]
			anchor = np.array(anchor_start) + np.array([max_index[1] * stride, max_index[0] * stride,
														max_index[1] * stride, max_index[0] * stride])
			box_offset = tensors[idx + 1][0, max_index[0], max_index[1], max_index[2] * 4:]
			width = anchor[2] - anchor[0] + 1
			height = anchor[3] - anchor[1] + 1
			anchor_cx = anchor[0] + 0.5 * (width - 1.)
			anchor_cy = anchor[1] + 0.5 * (height - 1.)
			anchor_cx += box_offset[0] * width
			anchor_cy += box_offset[1] * height
			width *= math.exp(box_offset[2])
			height *= math.exp(box_offset[3])

			result_tuple.append(((anchor_cx - .5 * (width - 1.),
								anchor_cy - .5 * (height - 1.),
								width, height), max_score))
			face_count += 1

	sorted_tuple = sorted(result_tuple, key=lambda t: t[1], reverse=True)
	passed_tuple = []
	while sorted_tuple:
		curr_tuple = sorted_tuple[0]
		passed_tuple.append(curr_tuple)
		curr_rect = curr_tuple[0]
		del sorted_tuple[0]
		sorted_tuple = [t for t in sorted_tuple if iou(curr_rect, t[0]) < GROUP_THRESHOLD]

	if passed_tuple:
		best_detect = passed_tuple[0][0]

	return best_detect, len(passed_tuple), passed_tuple

def face_alignment(rect_model, align_model, img, infer_size=(256, 256), fake_face_rect=False):
	ret_info = {'rect': (), 'face_count': None, 'all_rect': None, 'align': None}
	if fake_face_rect:
		height, width, channel = img.shape
		min_side = min(height, width)
		x = (width - min_side) / 2
		y = (height - min_side) / 2
		ret_info['rect'] = (x, y, min_side, min_side)
	elif type(rect_model) in (list, tuple):
		ret_info['rect'] = tuple(rect_model)
	else:
		ret_info['rect'], ret_info['face_count'], ret_info['all_rect']  = tuple(detect_face(rect_model, img))
	if len(ret_info['rect']) >= 4:
		ret_info['align'] = align_face(align_model, ret_info['rect'], img, infer_size)
	return ret_info

def get_level_anchor_factor(p1, p2):
	if p2[1] - p1[1] == 0.0 and p2[0] - p1[0] == 0.0:
		cos = 1.0
		sin = 0.0
	else:
		a = p2[1] - p1[1]
		b = p2[0] - p1[0]
		cos = math.cos(math.atan2(-a, b))
		sin = math.sin(math.atan2(-a, b))
	return sin, cos

def level_anchor_transform(sin, cos, p3):
	return [cos * p3[0] - sin * p3[1], sin * p3[0] + cos * p3[1]]

def level_anchor_inverse_transform(sin, cos, p3):
	return [cos * p3[0] + sin * p3[1], -sin * p3[0] + cos * p3[1]]

def FindEllipseSamplesByRatio(horizontal_vertex, vertical_vertex, p_ratios, ratio_count):
	ret_samples = [[0.0, 0.0] for i in range(ratio_count)]

	ellipse_a = abs(horizontal_vertex[0] - vertical_vertex[0])
	ellipse_b = vertical_vertex[1] - horizontal_vertex[1]
	ellipse_y0 = horizontal_vertex[1]

	for i in range(ratio_count):
		ret_samples[i] = horizontal_vertex * p_ratios[i] + vertical_vertex * (1.0 - p_ratios[i])
		if ellipse_a > 0.0:
			weight = 1.0 - p_ratios[i] * p_ratios[i]
			ret_samples[i][1] = ellipse_y0 + (weight ** 0.5) * ellipse_b
	return ret_samples

def FindForeheadPoints(face_rect, align_points):
	forehead_points = [[0.0, 0.0] for i in range(3)] # mid, left, right

	mid_eye = (align_points[landmark51_tag2index['SB_NOSE_BRIDGE_TOP']] * 2.0 + align_points[landmark51_tag2index['SB_NOSE_TOP']]) / 3.0
	face_rect_y = face_rect[1] # x, y, width, height
	mid_forehead = [mid_eye[0], min(face_rect_y, mid_eye[1])]
	mid_eye_forehead_offset = align_points[landmark51_tag2index['SB_NOSE_BRIDGE_TOP']] - align_points[landmark51_tag2index['SB_NOSE_BOTTOM']]
	offset_norm = (mid_eye_forehead_offset[0] * mid_eye_forehead_offset[0] + mid_eye_forehead_offset[1] * mid_eye_forehead_offset[1]) ** 0.5
	if offset_norm > 0.0:
		mid_eye_forehead_offset /= offset_norm
		nose_bridge_top = align_points[landmark51_tag2index['SB_NOSE_BRIDGE_TOP']]
		nose_bottom = align_points[landmark51_tag2index['SB_NOSE_BOTTOM']]
		left_eye_center = (align_points[landmark51_tag2index['SB_LEFT_EYE_LEFT']] + align_points[landmark51_tag2index['SB_LEFT_EYE_RIGHT']]) * 0.5
		right_eye_center = (align_points[landmark51_tag2index['SB_RIGHT_EYE_LEFT']] + align_points[landmark51_tag2index['SB_RIGHT_EYE_RIGHT']]) * 0.5

		offset1 = nose_bridge_top - nose_bottom
		offset2 = left_eye_center - right_eye_center
		delta = offset1[0] * offset2[1] - offset1[1] * offset2[0]
		delta_t = offset2[1] * (right_eye_center[0] - nose_bottom[0]) - \
				  offset2[0] * (right_eye_center[1] - nose_bottom[1])
		if delta > 1e-5 or delta < -1e-5:
			t = delta_t / delta
			predicted_nose_bridge_top = nose_bottom + offset1 * t
			if (predicted_nose_bridge_top[1] - nose_bottom[1]) / (nose_bridge_top[1] - nose_bottom[1]) > 1.0:
				nose_bridge_top = predicted_nose_bridge_top

		chin = align_points[landmark51_tag2index['SB_CHIN']]
		nose_bridge_length = math.dist(nose_bridge_top, nose_bottom)
		nose_bottom_chin_length = math.dist(nose_bottom, chin)
		length_ratio = nose_bridge_length / nose_bottom_chin_length
		is_tilt_up = length_ratio < 1.0
		if not is_tilt_up: length_ratio = 1.0 / length_ratio
		estimate_tilt_theta = min(math.pi / 4.0, math.acos(min(1.0, length_ratio + 1.0 / 3.0)))
		if not is_tilt_up: estimate_tilt_theta *= -1.0
		ratio = 0.95

		tan_tilt = math.tan(estimate_tilt_theta)
		adjust_ratio = (1.0 - tan_tilt / 4.0) / (1.0 + tan_tilt / 4.0)
		mid_eye_forehead_length = (adjust_ratio * nose_bottom_chin_length + (1.0 / 6.0 + 0.5 * adjust_ratio) * nose_bridge_length) * ratio
		
		mid_forehead = mid_eye + mid_eye_forehead_offset * mid_eye_forehead_length
	
	forehead_points[0] = mid_forehead
	left_mid_eye = ((align_points[landmark51_tag2index['SB_LEFT_EYE_LEFT']] + align_points[landmark51_tag2index['SB_LEFT_EYE_RIGHT']]) * 0.5 + \
					(align_points[landmark51_tag2index['SB_LEFT_EYE_TOP']] + align_points[landmark51_tag2index['SB_LEFT_EYE_BOTTOM']]) * 0.5) * 0.5
	right_mid_eye = ((align_points[landmark51_tag2index['SB_RIGHT_EYE_LEFT']] + align_points[landmark51_tag2index['SB_RIGHT_EYE_RIGHT']]) * 0.5 + \
					 (align_points[landmark51_tag2index['SB_RIGHT_EYE_TOP']] + align_points[landmark51_tag2index['SB_RIGHT_EYE_BOTTOM']]) * 0.5) * 0.5

	sin, cos = get_level_anchor_factor(left_mid_eye, right_mid_eye)
	transformed_mid_forehead = np.array(level_anchor_transform(sin, cos, mid_forehead))
	left_ear_top = align_points[landmark51_tag2index['SB_LEFT_EAR_TOP']]
	transformed_left_ear_top = np.array(level_anchor_transform(sin, cos, left_ear_top))
	right_ear_top = align_points[landmark51_tag2index['SB_RIGHT_EAR_TOP']]
	transformed_right_ear_top = np.array(level_anchor_transform(sin, cos, right_ear_top))
	
	forehead_sample_ratios = [0.75]
	forehead_sample_count = len(forehead_sample_ratios)
	left_transformed_foreheads = FindEllipseSamplesByRatio(transformed_left_ear_top, transformed_mid_forehead, forehead_sample_ratios, forehead_sample_count)
	right_transformed_foreheads = FindEllipseSamplesByRatio(transformed_right_ear_top, transformed_mid_forehead, forehead_sample_ratios, forehead_sample_count)

	forehead_points[1] = level_anchor_inverse_transform(sin, cos, left_transformed_foreheads[-1])
	forehead_points[2] = level_anchor_inverse_transform(sin, cos, right_transformed_foreheads[-1])

	return forehead_points

def convert_84_align_points_to_51_align_points(raw_84_align_points, image_height, image_width, face_rect):
	converted_51_align_points = np.array([[0.0, 0.0] for i in range(SB_LANDMARK_AMOUNT)])
	# x, y

	# ConvertFaceShape
	# left eyebrow
	converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_BROW_1']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_BROW_3']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_BROW_5']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_BROW_BOTTOM_2']]

	# left eye
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_EYE_OUTSIDE']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_EYE_TOP']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_EYE_INSIDE_IN']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_EYE_BOTTOM']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_CENTER']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_EYE_BOTTOM']]

	# right eyebrow
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_BROW_1']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_BROW_3']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_BROW_5']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_BROW_BOTTOM_2']]

	# left eye
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_EYE_INSIDE_IN']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_EYE_TOP']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_EYE_OUTSIDE']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_EYE_BOTTOM']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_CENTER']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_EYE_CENTER']]

	# nose
	converted_51_align_points[landmark51_tag2index['SB_NOSE_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_NOSE_LEFT']]
	converted_51_align_points[landmark51_tag2index['SB_NOSE_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_NOSE_RIDGE_4']]
	converted_51_align_points[landmark51_tag2index['SB_NOSE_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_NOSE_RIGHT']]
	converted_51_align_points[landmark51_tag2index['SB_NOSE_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_NOSE_BOTTOM_3']]

	# mouth
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_LEFT_CORNER']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_LEFT_OUTER_CORNER']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_TOP_LIP_1']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_TOP_LIP_TOP_3']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_TOP_LIP_2']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_TOP_LIP_BOTTOM']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_RIGHT_CORNER']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_RIGHT_OUTER_CORNER']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_BOTTOM_LIP_1']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_TOP']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_BOTTOM_LIP_2']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_2']]

	# face shape
	converted_51_align_points[landmark51_tag2index['SB_CHIN']] = raw_84_align_points[landmark84_tag2index['LBF_3D_CHIN']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EAR_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_FACE_SHAPE_1']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EAR_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_FACE_SHAPE_3']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_FACE_SHAPE_1']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_FACE_SHAPE_5']]
	converted_51_align_points[landmark51_tag2index['SB_LEFT_FACE_SHAPE_2']] = raw_84_align_points[landmark84_tag2index['LBF_3D_LEFT_FACE_SHAPE_7']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EAR_TOP']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_FACE_SHAPE_8']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EAR_BOTTOM']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_FACE_SHAPE_6']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_FACE_SHAPE_1']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_FACE_SHAPE_4']]
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_FACE_SHAPE_2']] = raw_84_align_points[landmark84_tag2index['LBF_3D_RIGHT_FACE_SHAPE_2']]

	need_check_boundary_num = 37

	# CheckAlignPointsBoundary
	for i in range(need_check_boundary_num):
		converted_51_align_points[i] = [min(max(converted_51_align_points[i][0], 0.0), float(image_width) - 1.0),
										min(max(converted_51_align_points[i][1], 0.0), float(image_height) - 1.0)]

	# CalculateIrisRadius
	# Not implement python version yet because need to inference with another iris detector
	# and the eyes' center points are not used currently

	# adjust eyebrow top point
	converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_TOP']] = \
		 (converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_TOP']] + \
		  converted_51_align_points[landmark51_tag2index['SB_LEFT_BROW_BOTTOM']]) * 0.5
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_TOP']] = \
		 (converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_TOP']] + \
		  converted_51_align_points[landmark51_tag2index['SB_RIGHT_BROW_BOTTOM']]) * 0.5

	# MappingMouthPoints
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_TOP_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_TOP_LIP_TOP_4']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_TOP_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_TOP_LIP_TOP_2']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_INNER_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_RIGHT_INNER_CORNER']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_INNER_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_LEFT_INNER_CORNER']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_UPPER_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_TOP_LIP_TOP_1']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_UPPER_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_TOP_LIP_TOP_5']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_LOWER_LEFT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_1']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_LOWER_RIGHT']] = raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_3']]
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_BOTTOM_LEFT']] = \
		 (raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M2']] + \
		  raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_2']]) * 0.5
	converted_51_align_points[landmark51_tag2index['SB_MOUTH_INTERP_BOTTOM_RIGHT']] = \
		 (raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_M3']] + \
		  raw_84_align_points[landmark84_tag2index['LBF_3D_MOUTH_BOTTOM_LIP_BOTTOM_2']]) * 0.5

	# calculate remain points : SB_NOSE_BRIDGE_TOP, SB_FOREHEAD_MIDDLE, SB_FOREHEAD_LEFT, SB_FOREHEAD_RIGHT
	LBF_L_NOSE_RIDGE_1 = 30
	converted_51_align_points[landmark51_tag2index['SB_NOSE_BRIDGE_TOP']] = raw_84_align_points[LBF_L_NOSE_RIDGE_1]

	forehead_points = FindForeheadPoints(face_rect, converted_51_align_points)
	converted_51_align_points[landmark51_tag2index['SB_FOREHEAD_MIDDLE']] = forehead_points[0]
	converted_51_align_points[landmark51_tag2index['SB_FOREHEAD_LEFT']] = forehead_points[1]
	converted_51_align_points[landmark51_tag2index['SB_FOREHEAD_RIGHT']] = forehead_points[2]

	# adjustment due to no iris detector
	converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_CENTER']] = (converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_LEFT']] + \
																			 converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_TOP']] + \
																			 converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_RIGHT']] + \
																			 converted_51_align_points[landmark51_tag2index['SB_LEFT_EYE_BOTTOM']]) / 4
	converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_CENTER']] = (converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_LEFT']] + \
																			  converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_TOP']] + \
																			  converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_RIGHT']] + \
																			  converted_51_align_points[landmark51_tag2index['SB_RIGHT_EYE_BOTTOM']]) / 4

	return converted_51_align_points

def infer_face_rect_51_align_points(rect_model, align_model, image, fake_face_rect=False):
	image_size = [256, 256] # rect model input size
	scale = 1.0
	MAX_IMAGE_SIDE = 320    
	height, width, channel = image.shape

	if height > MAX_IMAGE_SIDE or width > MAX_IMAGE_SIDE:
		if height > width:
			scale = MAX_IMAGE_SIDE / height
		else:
			scale = MAX_IMAGE_SIDE / width
	target_shape = [round(image.shape[i] * scale) for i in reversed(range(2))]
	resized_image = cv.resize(image, tuple(target_shape), cv.INTER_AREA)

	resized_height, resized_width, channel = resized_image.shape
	face_info = face_alignment(rect_model, align_model, resized_image, image_size, fake_face_rect)
	target_rect = face_info['rect']
	points = convert_84_align_points_to_51_align_points(face_info['align'], resized_height, resized_width, target_rect)
	orginal_scale_rect = np.array([element / scale for element in target_rect])
	orginal_scale_points = np.array([[point[0] / scale, point[1] / scale] for point in points])
	return orginal_scale_rect, orginal_scale_points

def infer_face_rect_92_align_points(rect_model, align_model, image, fake_face_rect=False):
	image_size = [256, 256] # rect model input size
	scale = 1.0
	MAX_IMAGE_SIDE = 320    
	height, width, channel = image.shape

	if height > MAX_IMAGE_SIDE or width > MAX_IMAGE_SIDE:
		if height > width:
			scale = MAX_IMAGE_SIDE / height
		else:
			scale = MAX_IMAGE_SIDE / width
	target_shape = [round(image.shape[i] * scale) for i in reversed(range(2))]
	resized_image = cv.resize(image, tuple(target_shape), cv.INTER_AREA)

	resized_height, resized_width, channel = resized_image.shape
	face_info = face_alignment(rect_model, align_model, resized_image, image_size, fake_face_rect)
	target_rect = face_info['rect']
	points = face_info['align']
	for i in range(len(points)):
		points[i] = [min(max(points[i][0], 0.0), float(resized_width) - 1.0),
					 min(max(points[i][1], 0.0), float(resized_height) - 1.0)]
	orginal_scale_rect = np.array([element / scale for element in target_rect])
	orginal_scale_points = np.array([[point[0] / scale, point[1] / scale] for point in points])
	return orginal_scale_rect, orginal_scale_points