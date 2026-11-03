import numpy as np
import os
from os import path
import sys
import shutil
import time
from DroopyEyelidUtils import util
import argparse
from collections import defaultdict
from collections import namedtuple
from collections import Counter
import math
import itertools
import matplotlib
from datetime import datetime
from skimage import transform as sktf
from skimage.filters import gaussian
from numpy.linalg import inv as matinv
import cv2
import struct
import random
import matplotlib.image as plt
import csv
import copy
from math import *

def get_transform_info(align_points, xy_to_yx=False):
    if xy_to_yx:
        align_points = align_points[..., ::-1]
    SB_LEFT_EYE_LEFT = 4
    SB_LEFT_EYE_RIGHT = 6
    SB_RIGHT_EYE_LEFT = 13
    SB_RIGHT_EYE_RIGHT = 15
    left_sagging_transform, left_region_size = get_sagging_transform(align_points[SB_LEFT_EYE_LEFT], align_points[SB_LEFT_EYE_RIGHT])
    right_sagging_transform, right_region_size = get_sagging_transform(align_points[SB_RIGHT_EYE_LEFT], align_points[SB_RIGHT_EYE_RIGHT])
    return left_sagging_transform, left_region_size, right_sagging_transform, right_region_size

def get_sagging_transform(left_corner, right_corner):
    center = (left_corner + right_corner) * 0.5
    distance = util.PointDistance(left_corner, right_corner)
    rotate_sin, rotate_cos = util.GetRotateFactor(left_corner, right_corner)
    rotate_angle = atan2(rotate_sin, rotate_cos)

    return util.Transform_(distance * 1.5 / 2.0,
                          distance * 1.5 / 2.0,
                          center,
                          rotate_angle), [int(distance * 1.5), int(distance * 1.5)]

def sagging_transform_to_wrinkle_transform(sagging_transform):
    assert(sagging_transform.scale_x == sagging_transform.scale_y)
    return util.Transform(sagging_transform.scale_x,
                          sagging_transform.center,
                          sagging_transform.angle)

def get_emulation_transform_info(align_points, xy_to_yx=False, target_size=None):
    if xy_to_yx:
        align_points = align_points[..., ::-1]
    SB_LEFT_EYE_LEFT = 4
    SB_LEFT_EYE_RIGHT = 6
    SB_RIGHT_EYE_LEFT = 13
    SB_RIGHT_EYE_RIGHT = 15
    left_sagging_transform, left_region_size = get_sagging_emulation_transform(align_points[SB_LEFT_EYE_LEFT], align_points[SB_LEFT_EYE_RIGHT], target_size)
    right_sagging_transform, right_region_size = get_sagging_emulation_transform(align_points[SB_RIGHT_EYE_LEFT], align_points[SB_RIGHT_EYE_RIGHT], target_size)
    return left_sagging_transform, left_region_size, right_sagging_transform, right_region_size

def get_sagging_emulation_transform(left_corner, right_corner, target_size):
    center = (left_corner + right_corner) * 0.5
    distance = util.PointDistance(left_corner, right_corner)
    rotate_sin, rotate_cos = util.GetRotateFactor(left_corner, right_corner)
    rotate_angle = atan2(rotate_sin, rotate_cos)
    if target_size == None:
        target_size = distance * 2.5
    return util.Transform_(target_size / 2.0,
                          target_size / 2.0,
                          center,
                          rotate_angle), [int(target_size), int(target_size)]

def read_bin_info(info_path):
    f = open(info_path, "rb").read()
    face_bbox = np.asarray(struct.unpack('i' * 4, f[0 : 16]))
    pts = np.asarray(struct.unpack('f' * 102, f[16 : 424])).reshape(-1, 2)

    return face_bbox, pts[:, [1, 0]]
    
def MakeTranslateMatrix(trans_x, trans_y):
    return np.array(((1.0, 0.0, trans_x), \
                     (0.0, 1.0, trans_y), \
                     (0.0, 0.0, 1.0    )), dtype='float32')
                     
def MakeScaleMatrix(scale_x, scale_y):
    return np.array(((scale_x,     0.0, 0.0), \
                     (0.0    , scale_y, 0.0), \
                     (0.0    ,     0.0, 1.0)), dtype='float32')

def MakeRotateMatrix(angle):
    cos_theta = np.cos(angle)
    sin_theta = np.sin(angle)
    return np.array(((cos_theta, -sin_theta, 0.0), \
                     (sin_theta,  cos_theta, 0.0), \
                     (0.0     ,         0.0, 1.0)), dtype='float32')

# Make transform matrix for openCV functions.
def MakeCVForwardTransformMatrix(transform, shrink_ratio, dst_width, dst_height):
    translate_matrix = MakeTranslateMatrix(-transform.center[1], -transform.center[0])
    scale_matrix = MakeScaleMatrix(1.0 / transform.scale_x, 1.0 / transform.scale_y)
    rotate_matrix = MakeRotateMatrix(transform.angle)
    
    transform_matrix = np.dot(scale_matrix, translate_matrix)
    transform_matrix = np.dot(rotate_matrix, transform_matrix)
    
    max_size = max(dst_width, dst_height)
    min_size = min(dst_width, dst_height)
    post_scale = (min_size / 2.0) * shrink_ratio
    post_transform_matrix = np.array(((post_scale,        0.0,  dst_width * 0.5), \
                                      (0.0       , post_scale, dst_height * 0.5), \
                                      (0.0       ,        0.0,              1.0)), dtype='float32')
    
    transform_matrix = np.dot(post_transform_matrix, transform_matrix)
    
    return transform_matrix


def transform_points(transform, points, target_length, shrink_ratio=1.0):
    # assumed points.shape is [len, 2]
    points = np.expand_dims(points, axis=1)
    forward_matrix = MakeCVForwardTransformMatrix(transform, shrink_ratio, target_length[1], target_length[0])
    # after the transform function points.shape is [len, 1, 3]
    transformed_points = cv2.transform(points, forward_matrix)[:, 0, :2]
    # fit in legal range
    transformed_points[..., 0] = np.minimum(transformed_points[..., 0], target_length[1])
    transformed_points[..., 0] = np.maximum(transformed_points[..., 0], 0)
    transformed_points[..., 1] = np.minimum(transformed_points[..., 1], target_length[0])
    transformed_points[..., 1] = np.maximum(transformed_points[..., 1], 0)
    return transformed_points
    
def transform_img(transform, src_image, target_length, shrink_ratio=1.0):
    forward_matrix = MakeCVForwardTransformMatrix(transform, shrink_ratio, target_length[1], target_length[0])

    # cv2 use forward matrix to transform.
    dst_image = cv2.warpAffine(src_image, forward_matrix[0:2, :], (target_length[1], target_length[0])) 
    if (len(dst_image.shape) == 2):
        dst_image = dst_image.reshape(dst_image.shape + (1,))

    return dst_image

def transform_ori_img(transform, src_image, target_size, ori_size, shrink_ratio=1.0):
    forward_matrix = MakeCVForwardTransformMatrix(transform, shrink_ratio, target_size[1], target_size[0])
    backward_matrix = matinv(forward_matrix)
    
    dst_image = cv2.warpAffine(src_image, backward_matrix[0:2, :], (ori_size[1], ori_size[0])) 
    if (len(dst_image.shape) == 2):
        dst_image = dst_image.reshape(dst_image.shape + (1,))
        
    return dst_image

class SaggingData:
    def __init__(self, img_path, pr_path, pts, sag_type):
        self.img_path = img_path
        self.img = None
        self.pr_path = pr_path
        self.pts = pts
        self.type = sag_type # "sag_type_eye" "sag_type_low_face"

        self.left_transform, self.left_region_size = self.get_eye_transform(pts[4], pts[6])
        self.right_transform, self.right_region_size = self.get_eye_transform(pts[13], pts[15])

        eye_left_point = pts[4]
        eye_right_point = pts[15]
        eye_rotate_sin, eye_rotate_cos = util.GetRotateFactor(eye_left_point, eye_right_point)
        eye_rotate_angle = atan2(eye_rotate_sin, eye_rotate_cos)

        left_contour_point = pts[31]
        right_contour_point = pts[35]
        contour_distance = util.PointDistance(left_contour_point, right_contour_point)

        nose_bottom_point = pts[21]
        chin_point = pts[28]
        center_point = (nose_bottom_point + chin_point) / 2.0
        nose_chin_distance = util.PointDistance(nose_bottom_point, chin_point)

        # in MakeCVForwardTransformMatrix, the reference side length is the shorter side
        # contour_distance * 1.2 / 2.0 is for longer side reference, so we need to add a compensate term from longer side to shorter side
        self.lower_transform = util.Transform_(contour_distance * 1.2 / (2.0 * 1.5),
                                              nose_chin_distance * 1.2 / (2.0),
                                              center_point,
                                              eye_rotate_angle)

        self.low_region_size = [contour_distance * 1.2, contour_distance * 1.2]

    def get_eye_transform(self, left_corner, right_corner):
        center = (left_corner + right_corner) * 0.5
        distance = util.PointDistance(left_corner, right_corner)
        rotate_sin, rotate_cos = util.GetRotateFactor(left_corner, right_corner)
        rotate_angle = atan2(rotate_sin, rotate_cos)

        return util.Transform_(distance * 1.5 / 2.0,
                              distance * 1.5 / 2.0,
                              center,
                              rotate_angle), [distance * 1.5, distance * 1.5]
            
    def get_img(self):
        if (self.img is None):
            self.img = util.get_img(self.img_path, False)
    
        return self.img

    def get_pr(self):
        fp = open(self.pr_path, "r")
        lines = fp.readlines()        
        fp.close()
        return float(lines[0]), float(lines[1]), float(lines[2]), float(lines[3]), float(lines[4])
        
    def del_all_images(self):
        self.img = None

PerturbParam = namedtuple('PerturbParam', ['transform', 'flip', 'blur'])
NormalizeParam = namedtuple('NormalizeParam', ['scale', 'shift'])
TransParam = namedtuple('TransParam', ['perturb', 'transform', 'shrink_ratio','normalize'])

class PerturbedData:
    def __init__(self, sagging_data, is_right_side, roi_size, shrink_ratio=1.0,
                 perturb_param=PerturbParam(util.Transform_(1., 1., np.zeros((2,)), 0.), 
                                            False, False)):
        self.sagging_data = sagging_data
        self.perturb_param = perturb_param

        sample_transform = None
        if sagging_data.type == "sag_type_low_face":
            sample_transform = sagging_data.lower_transform
        elif sagging_data.type == "sag_type_eye":
            sample_transform = sagging_data.right_transform if is_right_side else sagging_data.left_transform
        else:
            assert False
        
        self.transform = util.Transform_(sample_transform.scale_x,
                                        sample_transform.scale_y,
                                        sample_transform.center + perturb_param.transform.center,
                                        sample_transform.angle + perturb_param.transform.angle)

        self.face_scale = roi_size
        self.shrink_ratio = shrink_ratio
        self.is_right_side = is_right_side
        self.normalize_param = NormalizeParam(self.face_scale / 2. * self.shrink_ratio, 
                                              self.face_scale / 2.)

    def get_learning_pair(self, face_image):
        image = transform_img(self.transform, face_image, self.face_scale, shrink_ratio=self.shrink_ratio)
        
        if self.perturb_param.blur:
            image = gaussian(image, multichannel=True)

        if self.perturb_param.flip:
            image = cv2.flip(image, 1)
            
        datum = image.transpose(2, 0, 1)
        trans_param = TransParam(self.perturb_param, self.transform, self.shrink_ratio, self.normalize_param)
        
        return datum, trans_param

    # Get only the input image with the same way as get_learning_pair().
    def GetInputData(self, src_image):
        image = transform_img(self.transform, src_image, self.face_scale, shrink_ratio=self.shrink_ratio)
        
        if self.perturb_param.blur:
            image = gaussian(image, multichannel=True)

        if self.perturb_param.flip:
            image = cv2.flip(image, 1)
            
        input_data = image.transpose(2, 0, 1)
        trans_param = TransParam(self.perturb_param, self.transform, self.shrink_ratio, self.normalize_param)
        
        return input_data, trans_param


def PerturbData(sample, is_right_side, roi_size,
                rotation_range=math.pi / 36.,
                shift_ratio_range=0.1,
                flip_prob=0.0, blur_prob=0.0,
                shrink_ratio=1.0, face_part='face'):
    scale_range = np.random.uniform(-0.05, 0.05)
    np.random.seed()

    shrink_ratio += scale_range

    region_size = None
    if sample.type == "sag_type_low_face":
        region_size = sample.low_region_size
    elif sample.type == "sag_type_eye":
        region_size = sample.right_region_size if is_right_side else sample.left_region_size
    else:
        assert False

    shift_min = [-shift_ratio_range * region_size[0], -shift_ratio_range * region_size[1]]
    shift_max = [ shift_ratio_range * region_size[0],  shift_ratio_range * region_size[1]]

    return PerturbedData(sample, is_right_side, roi_size, shrink_ratio,
                             PerturbParam(util.Transform_(1.,
                                                         1., 
                                                         np.random.uniform(shift_min, shift_max, (2,)),
                                                         np.random.uniform(-rotation_range, rotation_range)),
                                          np.random.uniform() < flip_prob,
                                          np.random.uniform() < blur_prob))                                          

def ColorArray(color, width, height, is_channel_last=False):
    color_array = np.ones((3, height, width), dtype='float32')
    color_array[0, :, :] *= color[0]
    color_array[1, :, :] *= color[1]
    color_array[2, :, :] *= color[2]
    
    if (is_channel_last):
        color_array = color_array.transpose(1, 2, 0)
        
    return color_array


def SaveBatchDebugImageLowFace(image_data, train_score, save_folder, basename):
    debug_data = image_data[:3, :, :].copy()

    score_int = int(train_score + 0.5)
    plt.imsave(save_folder + basename + '_%03d.jpg' % (score_int), debug_data.transpose(1, 2, 0))

def SaveBatchDebugImageEye(image_data, train_score_upper, train_score_lower, save_folder, basename):
    debug_data = image_data[:3, :, :].copy()

    score_upper_int = int(train_score_upper + 0.5)
    score_lower_int = int(train_score_lower + 0.5)
    plt.imsave(save_folder + basename + '_%03d_%03d.jpg' % (score_upper_int, score_lower_int), debug_data.transpose(1, 2, 0))

def MakeSourceSizedLabelImage(src_image, bbox, label, trans_params):
    perturb = trans_params[0]
    transform = trans_params[1]
    shrink_ratio = trans_params[2]
    normalize = trans_params[3]
    
    src_size = src_image.shape[0:2]
    
    box_height = int(np.max(bbox[2:4]) * (1.0 / shrink_ratio))
    box_width = int(box_height * 3.0 / 4.0)

    label_image = label.transpose(1, 2, 0).repeat(3, axis=2)
    resized_label_image = cv2.resize(label_image, (box_width, box_height))
    if perturb.flip:
        resized_label_image = cv2.flip(resized_label_image, 1)
    
    src_sized_label_image = transform_ori_img(transform, resized_label_image, np.array([box_height, box_width]), np.array(src_size), shrink_ratio)
    
    return src_sized_label_image

def GetInputOutputShapeFromNPZ(data_path, is_channel_last):
    # Get the network input / output shape from a .npz data file.
    # Assume all npz data have the same shape.
    
    npz_content = np.load(data_path, allow_pickle=True)
    batch_data = npz_content['data']
    batch_score = npz_content['score']
    
    if (is_channel_last):
        input_height = batch_data.shape[1]
        input_width = batch_data.shape[2]
        input_channels = batch_data.shape[3]
    else:
        input_height = batch_data.shape[2]
        input_width = batch_data.shape[3]
        input_channels = batch_data.shape[1]
        
    score_dimension = batch_score.shape[1]
        
    # Always return in H x W x C order.
    input_shape = (input_height, input_width, input_channels)
    
    return [input_shape, score_dimension]