import numpy as np
import os
from os import path
import sys
import shutil
import time
from . import util
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
    scale_matrix = MakeScaleMatrix(1.0 / transform.scale, 1.0 / transform.scale)
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

    return dst_image.astype(np.uint8)

def transform_ori_img(transform, src_image, target_size, ori_size, shrink_ratio=1.0):
    forward_matrix = MakeCVForwardTransformMatrix(transform, shrink_ratio, target_size[1], target_size[0])
    backward_matrix = matinv(forward_matrix)
    
    dst_image = cv2.warpAffine(src_image, backward_matrix[0:2, :], (ori_size[1], ori_size[0])) 
    if (len(dst_image.shape) == 2):
        dst_image = dst_image.reshape(dst_image.shape + (1,))
        
    return dst_image.astype(np.uint8)

def calculate_score(label_4ch_float_chw):
    label_4ch_int = (label_4ch_float_chw * 255.0 + 0.5).astype(int)
    label_4ch_int = np.clip(label_4ch_int, 0, 90)
    pr_score = [0.0, 0.0, 0.0, 0.0]
    mapping_factor = [94.6292, 96.5046, 97.6469, 98.4006]
    for i in range(label_4ch_int.shape[0]):
        average_intensity = np.sum(label_4ch_int[i,:,:]) / float(label_4ch_int.shape[1] * label_4ch_int.shape[2])
        intermediate_ratio = 43.93
        a = 0.0
        b = 0.0
        if average_intensity < intermediate_ratio:
            a = -90.0 / intermediate_ratio
            b = 100.0
        else:
            a = 10.0 / (intermediate_ratio - 255.0)
            b = -255.0 * a
        raw_score = min(max(a * average_intensity + b, 0.0), 100.0)
        if raw_score < mapping_factor[i]:
            pr_score[i] = raw_score / mapping_factor[i] * 5.0
        else:
            pr_score[i] = 5.0 + (raw_score - mapping_factor[i]) / (100.0 - mapping_factor[i]) * 95.0
    return pr_score

def get_aligned_image(image, align_points, xy_to_yx=False):
    if xy_to_yx:
        align_points = align_points[..., ::-1]
    height, width, channel = image.shape
    roi_height, roi_width = 1280, 960
    batch_data = np.zeros((1, channel, roi_height, roi_width))
    roi_size = np.array([roi_height, roi_width])
    image_info = WrinkleData(img_path=image, mask_path=None, label_path=None, pts=align_points)
    perturb_face = PerturbedData(image_info, roi_size, shrink_ratio=1.0)
    batch_data[0] = perturb_face.get_learning_pair(face_image=image_info.get_img())
    return (batch_data[0].transpose(1,2,0) * 255)

def get_transform_info(align_points, xy_to_yx=False):
    def get_eye_dis(pts):
        left_eye_left = pts[4]
        right_eye_right = pts[15]
        return util.point_distance(left_eye_left, right_eye_right)
    if xy_to_yx:
        align_points = align_points[..., ::-1]
    eye_distance = get_eye_dis(align_points)
    eye_center = (align_points[4] + align_points[15]) / 2.0
    nose_center = (align_points[18] + align_points[20]) / 2.0
    blend_center = (eye_center * 2.0 + nose_center * 1.0) / 3.0
    angle = -util.get_angle_from_shape(align_points)

    transform_info = util.Transform(eye_distance * 2.0 / 2.0,
                               blend_center,
                               angle) 
    return transform_info   

class WrinkleData:
    def __init__(self, img_path, mask_path, label_path, pts, bbox=None, order=0, score=[0.0, 0.0, 0.0, 0.0], face_detect=False):
        self.img_path = img_path
        self.img = None
        self.mask_path = mask_path
        self.mask = None
        self.label_paths = label_path
        self.labels = None
        self.pts = pts
        self.bbox = bbox if face_detect else self.fix_bbox(util.compute_rect_from_shape(self.pts[0:51]))
        self.angle = -util.get_angle_from_shape(self.pts)

        eye_distance = self.get_eye_dis(self.pts)
        eye_center = (self.pts[4] + self.pts[15]) / 2.0
        nose_center = (self.pts[18] + self.pts[20]) / 2.0
        blend_center = (eye_center * 2.0 + nose_center * 1.0) / 3.0

        self.transform = util.Transform(eye_distance * 2.0 / 2.0,
                                        blend_center,
                                        self.angle)
        self.order = order
        self.score = score
        
        if isinstance(img_path, str):
            npy_title = img_path.replace('.', '_')
            npy_title = npy_title.replace('/', '-')
            self.image_npy_name = npy_title + '.npy'
            self.mask_npy_name = npy_title + '_mask.npy'
            self.label_npy_name = npy_title + '_label.npy'

    def get_score(self):
        return self.score
            
    def get_img(self):
        if (self.img is None):
            img = util.get_img(self.img_path, False)
    
        return img


    def get_mask(self):
        if (self.mask is None):
            mask = util.get_img(self.mask_path, True)
    
        return mask
    
    def get_nasolabial_wrinkle_combined_mask(self):
        if (self.mask is None):
            for index, label_path in enumerate(self.label_paths):
                if index == 0:
                    mask = util.get_img(label_path, True)
                else:
                    mask = np.maximum(mask, util.get_img(label_path, True))
    
        return mask

    def get_label(self):
        if (self.labels is None):
            # only support one gray mask and one color mask
            assert len(self.label_paths) == 2
            wrinkle_label_image = util.get_img(self.label_paths[0], False)
            fineline_label_image = util.get_img(self.label_paths[1], False)
            assert wrinkle_label_image.shape[0] == fineline_label_image.shape[0] and wrinkle_label_image.shape[1] == fineline_label_image.shape[1]

            color_label_image = np.maximum(wrinkle_label_image, np.clip(fineline_label_image * 0.3, 0.0, 0.5))
            temp_color_label_image = color_label_image.copy()
            gray_label_image = np.sum(temp_color_label_image, axis=2)

            self.labels = np.zeros((gray_label_image.shape[0], gray_label_image.shape[1], 4), dtype=gray_label_image.dtype)
            self.labels[:, :, 0] = gray_label_image
            self.labels[:, :, 1:4] = color_label_image[:, :, ::-1] # inverse the array order to make first channel be forehead
                
        return self.labels
        
    def del_all_images(self):
        self.img = None
        self.mask = None
        self.labels = None
        
    
    @staticmethod
    def fix_bbox(bbox):
        width = bbox[3]
        height = bbox[2]
        curr_aspect_ratio = height / width
        target_aspect_ratio = 640.0 / 480.0
        if curr_aspect_ratio > target_aspect_ratio:
            #adjust horizontal
            new_width = height / target_aspect_ratio
            new_x = bbox[1] - (new_width - width) / 2.0
            bbox[1] = new_x
            bbox[3] = new_width
        else:
            #adjust vertical
            new_height = width * target_aspect_ratio
            new_y = bbox[0] - (new_height - height) / 2.0
            bbox[0] = new_y
            bbox[2] = new_height

        return bbox

    @staticmethod
    def get_eye_dis(pts):
        left_eye_left = pts[4]
        right_eye_right = pts[15]
        return util.point_distance(left_eye_left, right_eye_right)

PerturbParam = namedtuple('PerturbParam', ['transform', 'flip', 'blur'])
NormalizeParam = namedtuple('NormalizeParam', ['scale', 'shift'])
TransParam = namedtuple('TransParam', ['perturb', 'transform', 'shrink_ratio','normalize'])

class PerturbedData:
    def __init__(self, oiliness_data, roi_size, shrink_ratio=1.0,
                 perturb_param=PerturbParam(util.Transform(1., np.zeros((2,)), 0.), 
                                            False, False)):
        self.oiliness_data = oiliness_data
        self.perturb_param = perturb_param
        self.transform = util.Transform(oiliness_data.transform.scale,
                                        oiliness_data.transform.center + perturb_param.transform.center,
                                        oiliness_data.transform.angle + perturb_param.transform.angle)
        self.face_scale = roi_size
        self.shrink_ratio = shrink_ratio
        self.normalize_param = NormalizeParam(self.face_scale / 2. * self.shrink_ratio, 
                                              self.face_scale / 2.)

    def get_learning_pair(self, face_image, binarize_mask=False):
        if binarize_mask:
            face_image = np.where(face_image > 0, 255, 0).astype(np.uint8)
        image = transform_img(self.transform, face_image, self.face_scale, shrink_ratio=self.shrink_ratio)
        datum = image.transpose(2, 0, 1)
        return datum

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


def PerturbData(face_data, roi_size,
                rotation_range=math.pi / 36.,
                shift_ratio_range=0.1,
                flip_prob=0.0, blur_prob=0.0,
                shrink_ratio=1.0, face_part='face'):
    shift_range = np.max(face_data.bbox[2:]) * shift_ratio_range
    scale_range = np.random.uniform(-0.05, 0.05)
    np.random.seed()

    shrink_ratio += scale_range
    return PerturbedData(face_data, roi_size, shrink_ratio,
                             PerturbParam(util.Transform(1., 
                                                         np.random.uniform(-shift_range, shift_range, (2,)),
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


def SaveBatchDebugImage(image_data, label_data, target_label, total_label_amount, train_score, save_folder, basename):
    image_width = image_data.shape[2]
    image_height = image_data.shape[1]
    image_and_mask_channels = image_data.shape[0]
    
    max_alpha = 1.0
    
    label_index = 0
    for i in range(total_label_amount):
        debug_data = image_data[:3, :, :].copy()
        debug_color = ColorArray([0.0, 1.0, 0.0], image_width, image_height)
        if (i == 1):
            debug_color = ColorArray([1.0, 0.0, 0.0], image_width, image_height)
        if (i == 2):
            debug_color = ColorArray([0.0, 0.0, 1.0], image_width, image_height)
        if (i == 3):
            debug_color = ColorArray([0.0, 1.0, 1.0], image_width, image_height)
            
        target_label_data = label_data[label_index:label_index+1, :, :]
        label_index += 1
        
        alpha_array = np.repeat(target_label_data * max_alpha, 3, axis=0)
        debug_data[:] = debug_data[:] * (1.0 - alpha_array) + debug_color * alpha_array
        
        wrinkle_score_int = int(train_score[0] * 100.0 + 0.5)
        forehead_score_int = int(train_score[1] * 100.0 + 0.5)
        eye_score_int = int(train_score[2] * 100.0 + 0.5)
        lowface_score = int(train_score[3] * 100.0 + 0.5)
        plt.imsave(save_folder + basename + '_%d_%03d_%03d_%03d_%.3f.jpg' % (i, wrinkle_score_int, forehead_score_int, eye_score_int, lowface_score), debug_data.transpose(1, 2, 0))

        # debug_label = label_data[0:3,:,:].copy()
        # plt.imsave(save_folder + basename + '_%d_label.jpg' % i, debug_label.transpose(1, 2, 0))
        
        if (image_and_mask_channels > 3):
            debug_data = image_data[:3, :, :].copy()
            debug_color = ColorArray([0.0, 0.0, 1.0], image_width, image_height)
            
            alpha_array = np.repeat(image_data[3:4, :, :], 3, axis=0)
            debug_data[:] = debug_data[:] * (1.0 - alpha_array) + debug_color * alpha_array
                    
            plt.imsave(save_folder + basename + '_%d_mask.jpg' % i, debug_data.transpose(1, 2, 0))

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