from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import os
from os import path
import sys
from math import *
import configparser
import skimage.io
import skimage.color
import struct
from skimage import draw
from skimage import transform as sktf
import copy
from collections import namedtuple
from numpy.linalg import inv as matinv
import cv2
import random


from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

PerturbParam = namedtuple('PerturbParam', ['transform', 'flip', 'blur'])
NormalizeParam = namedtuple('NormalizeParam', ['scale', 'shift'])
Transform = namedtuple('Transform', ['scale', 'center', 'angle'])
Transform_ = namedtuple('Transform', ['scale_x', 'scale_y', 'center', 'angle'])

EXIF_ORIENTATION_TABLE = {
    'Horizontal (normal)': 1,
    'Mirrored horizontal': 2,
    'Rotated 180': 3,
    'Mirrored vertical': 4,
    'Mirrored horizontal then rotated 90 CCW': 5,
    'Rotated 90 CW': 6,
    'Mirrored horizontal then rotated 90 CW': 7,
    'Rotated 90 CCW': 8
}
            
def GetEXIF(image_path):
    # Imported here: exifread is not in the kowloon comfy env and nothing on the emulation path reads EXIF
    import exifread
    with open(image_path, 'rb') as f:
        exif_data = exifread.process_file(f)
        
    # remove some unwanted elements lik thumb nails
    for key in ('JPEGThumbnail', 'TIFFThumbnail', 'MakerNote JPEGThumbnail'):
        if key in exif_data:
            del exif_data[key]
            
    return exif_data

def GetEXIFOrientation(image_path):
    exif = GetEXIF(image_path)

    mirror = 0
    rotation = 0
    code = exif.get('Image Orientation', None)

    if code is None:
        return (mirror, rotation)

    code = str(code)
    try:
        code = int(code)
    except ValueError:
        code = EXIF_ORIENTATION_TABLE[code]

    if code in (2, 5, 7):
        mirror = 1 # Horizontal
    if code in (4,):
        mirror = 2 # Vertical
        
    if code in (1, 2):
        rotation = 0
    elif code in (3, 4):
        rotation = 180
    elif code in (5, 6):
        rotation = 90
    elif code in (7, 8):
        rotation = 270

    return (mirror, rotation)

def dump_exception(e):
    exc_type, exc_obj, exc_tb = sys.exc_info()
    print(e, '@line %d'%exc_tb.tb_lineno, 
          'in %s'%exc_tb.tb_frame.f_code.co_filename)

valid_align_index_of_ini = list(range(0, 35)) + [36,38,39] + list(range(40,84)) + [85,88]
eye_index = [4, 6, 13, 15]

def load_landmark_pts(pts_path):
    try:
        with open(pts_path) as f:
            lines = f.readlines()
            if len(lines) < 71:
                raise SyntaxError('ini fail: %s (insufficient line %d)'%(pts_path, len(lines)))

            loaded_list = []
            for line in lines[3:71]:
                loaded_list.append([float(i) for i in line.split()[:2]])
            loaded_landmarks = np.asarray(loaded_list)
            return loaded_landmarks[:, [1, 0]]

    except Exception as e:
        dump_exception(e)

def load_landmark_ini(ini_path):
    config = configparser.ConfigParser()
    try:
        config.read(ini_path)
        loaded_list = []
        for i in valid_align_index_of_ini:
            coorstr = config.get('Landmark', 'Landmark_' + str(i)).split()
            loaded_list.append([float(i) for i in coorstr])
        loaded_landmarks = np.asarray(loaded_list)
    except:
        print('ini fail: ' + ini_path)

    return loaded_landmarks[:, [1, 0]]

def get_pts_from_txt(all_pts, id):
    return all_pts[id]

def get_pts_dict_from_txt(txt_folder):
    pts_dict = {}

    img_id = 0
    for dirpath, dirnames, filenames in os.walk(txt_folder):
        random.shuffle(filenames)
        for filename in filenames:
            file_prefix, file_ext = path.splitext(filename)
            if file_ext == '.txt':
                txt_path = path.join(dirpath, filename)
                for line in open(txt_path):
                    pts_dict[str(img_id)] = line
                    img_id += 1

    return pts_dict

def get_pts_dict_from_300w_txt(txt_folder):
    pts_dict = {}
    pts = np.zeros((194, 2))
    pts_folder = '../300-W/Helen_194/annotation/'
    find = False
    img_id = 0

    for line in open(txt_folder):
        img_name = line[:-1] + '.jpg'
        for dirpath, dirnames, filenames in os.walk(pts_folder):
            random.shuffle(filenames)
            for filename in filenames:
                txt_path = path.join(dirpath, filename)
                result = list()
                for pts_line in open(txt_path):
                    if pts_line[:-2] == line[:-1]:
                        find = True
                    if find:
                        result.append(pts_line[:-2])
                    else:
                        break

                if find:
                    for i in range(194):
                        pts[i] = np.array([float(result[i + 1].split(' ')[-1]), float(result[i + 1].split(' ')[0])])
                    save_pts = pts.copy()
                    pts_dict[str(img_id)] = [img_name, save_pts]
                    img_id += 1
                    find = False
                    del result[:]

    print(img_id)
    return pts_dict





def get_all_pts_from_txt(txt_folder, n_img, n_p):
    pts = np.zeros([n_img, n_p, 2])

    img_id = 0
    for dirpath, dirnames, filenames in os.walk(txt_folder):
        for filename in filenames:
            file_prefix, file_ext = path.splitext(filename)
            if file_ext == '.txt':
                txt_path = path.join(dirpath, filename)
                for line in open(txt_path):
                    pts[img_id] = np.array((line.split())[1: 2 * n_p + 1], float).reshape(-1, 2)
                    img_id += 1

    return pts[:, :, [1, 0]]

def compute_nme(new_label, v_ys, full_gt, database='our'):
    result_label = new_label
    sum_nme = 0.
    if database == 'our':
        for i in range(result_label.shape[0]):
            left_eye = (full_gt[i, [10, 11]] + full_gt[i, [18, 19]]) / 2
            right_eye = (full_gt[i, [44, 45]] + full_gt[i, [48, 49]]) / 2
            eye_dis = point_distance(left_eye, right_eye)
            sum_nme += point_distance(result_label[i].reshape((-1, 2)), v_ys[i].reshape((-1, 2))) / eye_dis
    elif database == '300w':                                                               #  300w
        for i in range(result_label.shape[0]):
            left_eye = (full_gt[i, [72, 73]] + full_gt[i, [78, 79]]) / 2
            right_eye = (full_gt[i, [84, 85]] + full_gt[i, [90, 91]]) / 2
            eye_dis = point_distance(left_eye, right_eye)
            sum_nme += point_distance(result_label[i].reshape((-1, 2)), v_ys[i].reshape((-1, 2))) / eye_dis
    elif database == '300w-194':
        for i in range(result_label.shape[0]):
            left_eye = (full_gt[i, [286, 287]] + full_gt[i, [306, 307]]) / 2
            right_eye = (full_gt[i, [228, 229]] + full_gt[i, [246, 247]]) / 2
            eye_dis = point_distance(left_eye, right_eye)
            sum_nme += point_distance(result_label[i].reshape((-1, 2)), v_ys[i].reshape((-1, 2))) / eye_dis
    else:
        for i in range(result_label.shape[0]):
            left_eye = full_gt[i, [0, 1]]
            right_eye = full_gt[i, [2, 3]]
            eye_dis = point_distance(left_eye, right_eye)
            sum_nme += point_distance(result_label[i].reshape((-1, 2)), v_ys[i].reshape((-1, 2))) / eye_dis

    return sum_nme * 100 / result_label.shape[0]


def get_angle_from_shape(shape):
    left = np.mean(shape[eye_index[:2], :], axis=0)
    right = np.mean(shape[eye_index[2:], :], axis=0)

    return atan2(right[0] - left[0], right[1] - left[1])

def compute_rect_from_shape(shape):
    lt, rb = np.min(shape, axis=0), np.max(shape, axis=0)
    center = (lt + rb) * 0.5
    length = np.max(rb - lt)

    return np.concatenate((lt, rb - lt))

def rect_to_corner(rect):
    return np.asarray([rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]])

def rect_to_center(rect):
    return rect[:2] + rect[2:] / 2.

def get_transformed_shape(mat, shape):
    return np.dot(mat[0:2, 0:2], shape.transpose()).transpose() + mat[0:2, 2].transpose()

def point_distance(a, b):
    return np.mean(np.sqrt(np.sum(np.square(a - b), axis=min(1, len(a.shape) - 1))))

def get_rotate_matrix(angle):
    cos_ = cos(angle)
    sin_ = sin(angle)
    return np.array([[cos_, -sin_], [sin_, cos_]])

def backward_transform(transform, points):
    return np.dot(points, get_rotate_matrix(-transform.angle)) * transform.scale + transform.center

def forward_transform(transform, points):
    re_scale = transform.scale / 64
    resize_transform = Transform(scale=64, center=transform.center / re_scale, angle=transform.angle)
    resize_points = points / re_scale

    return np.dot((resize_points - resize_transform.center) / resize_transform.scale, get_rotate_matrix(resize_transform.angle))

def transform_to_skimage_transform(transform):
    matrix = np.dot(sktf.SimilarityTransform(rotation=-transform.angle).params, 
                    sktf.SimilarityTransform(scale=1. / transform.scale).params)
    matrix = np.dot(matrix,
                    sktf.SimilarityTransform(translation=-transform.center).params)
    return sktf.SimilarityTransform(matrix)
    
def get_eye_distance(points):
    return point_distance(np.mean(points[eye_index[:2], :], axis=0), np.mean(points[eye_index[2:], :], axis=0))

def get_img(result, is_gray=False):
    exif_orientation = (0, 0)
    if isinstance(result, str):
        ext = path.splitext(result)[1]
        if ext == '.png':
            img = skimage.io.imread(result if isinstance(result, str) else result.path)
        else:
            img = skimage.io.imread(result if isinstance(result, str) else result.path, plugin='matplotlib')
            
        try:
            exif_orientation = GetEXIFOrientation(result)
        except KeyError:
            print('load exif fail: KeyError!')
    else:
        img = result
    if len(img.shape) == 2:
        img = skimage.color.gray2rgb(img)
    elif len(img.shape) == 4:
        img = img[0,:,:,:]
    elif len(img.shape) == 1:
        img = img[0]
    if img.shape[2] == 4:
        img = img[:, :, :3]
    
    if (img.dtype != np.float32):
        img = img.astype(np.float32)
    img /= 255.0
    
    if is_gray:
        img = skimage.color.rgb2gray(img).reshape(img.shape[0], img.shape[1], 1)
    
    img[img < 0.0] = 0.0
    img[img > 1.0] = 1.0
    if isinstance(result, str):
        if (exif_orientation[0] == 1):
            img = np.flip(img, axis=1)
        elif (exif_orientation[0] == 2):
            img = np.flip(img, axis=0)

        if (exif_orientation[1] != 0):
            rotate_times = 4 - exif_orientation[1] / 90
            img = np.rot90(img, k=rotate_times, axes=(0, 1))
        
    return img

def create_plotted_img(img, points, color=[[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0]], deep_copy=True, radius=1, bbox=[]):
    if len(img.shape) == 2:
        show_img = skimage.color.gray2rgb(img)
    else:
        show_img = copy.deepcopy(img) if deep_copy else img

    face_size = img.shape[0] if bbox == [] else max(bbox[2], bbox[3])
    if face_size < 113:
        point_scale = 1
    else:
        point_scale = 4

    radius = radius * point_scale

    while len(points.shape) < 3:
        points = points[np.newaxis, :]

    for point_i in xrange(points.shape[0]):
        curr_points = points[point_i]
        curr_points[:, 0] = np.clip(curr_points[:, 0], 0, img.shape[0])
        curr_points[:, 1] = np.clip(curr_points[:, 1], 0, img.shape[1])
        if point_i == 0:
            for index in xrange(points.shape[1]):
                if face_size == 112:
                    draw.set_color(show_img, 
                                draw.circle(int(curr_points[index, 0]), int(curr_points[index, 1]), radius),
                                color[min(len(color)-1, point_i)])
                else:
                    draw.set_color(show_img, 
                                draw.circle(int(curr_points[index, 0]), int(curr_points[index, 1]), radius * 1.5),
                                color[min(len(color)-1, point_i)])
        else:
            for index in xrange(points.shape[1]):
                draw.set_color(show_img, 
                            draw.circle(int(curr_points[index, 0]), int(curr_points[index, 1]), radius),
                            color[min(len(color)-1, point_i)])

    return show_img

def transform_img(transform, img, target_length, shrink_ratio=1., nor_eye=False):
    re_scale = transform.scale / 64  #reshape image make bbox_size = 128 * 128
    resize_transform = Transform(scale=64, center=transform.center / re_scale, angle=transform.angle)
    resize_img_h = int(img.shape[0] / re_scale)
    resize_img_w = int(img.shape[1] / re_scale)
    resize_img = cv2.resize(img,(resize_img_w, resize_img_h), interpolation=cv2.INTER_AREA)

    forward_matrix = transform_to_skimage_transform(resize_transform).params
    if nor_eye:
        forward_matrix[0:2, 0:3] *= target_length / 2.
        forward_matrix[0, 2] += target_length / 4.
        forward_matrix[1, 2] += target_length / 3.
    else:
        forward_matrix[0:2, 0:3] *= target_length / 2. * shrink_ratio
        forward_matrix[0, 2] += target_length / 2.
        forward_matrix[1, 2] += target_length / 2.
    warp_matrix = matinv(forward_matrix)
    if len(resize_img.shape) < 3:
        resize_img = resize_img.reshape(resize_img.shape[0], resize_img.shape[1], 1)
        
    transformed_img = sktf.warp(resize_img.transpose(1, 0, 2), warp_matrix, 
                                output_shape=[target_length, target_length, 
                                              resize_img.shape[2] if len(resize_img.shape) > 2 else 1]).transpose(1, 0, 2)

    return transformed_img

def flip_face_label(label):
    our_flip_index = [19,18,17,16,15,
                22,21,20,23,24,25,27,26,29,28,
                4,3,2,1,0,
                7,6,5,8,9,10,12,11,14,13,
                30,31,32,33,36,35,34,38,37,
                43,42,41,40,39,45,44,49,48,47,46,50,52,51,56,55,54,53,59,58,57,61,60,62,64,63,
                81,80,79,78,77,76,75,74,73,72,71,70,69,68,67,66,65,
                83,
                82]
    flip_300w_index = [16,15,14,13,12,11,10,9,8,7,6,5,4,3,2,1,0,
                        26,25,24,23,22,
                        21,20,19,18,17,
                        27,28,29,30,35,34,33,32,31,
                        45,44,43,42,47,46,
                        39,38,37,36,41,40,
                        54,53,52,51,50,49,48,59,58,57,56,55,
                        64,63,62,61,60,67,66,65
                        ]

    if label.shape[0] == 84:
        flip_index = our_flip_index
    elif label.shape[0] == 68:
        flip_index = flip_300w_index

    flip_label = label.copy()
    for i in range(label.shape[0]):
        flip_label[i, :] = label[flip_index[i], :]
    
    return flip_label

def get_bbox(bbox_path, ini):
    ext = bbox_path.split('.')[-1]
    best_bbox_index = 0
    best_bbox_cover = 0

    if ext == 'txt':
        f = open(bbox_path, 'r').read().split('\n')
        is_intersect = False
        if int(f[0]) == 0:
            return compute_rect_from_shape(ini)
        else:
            for i in range(int(f[0])):
                bbox_info = f[i+1].split(' ')
                bbox = np.array([int(bbox_info[1]), int(bbox_info[0]),int(bbox_info[3]),int(bbox_info[2])])

                lt, rb = np.min(ini, axis=0), np.max(ini, axis=0)

                face_min_y = lt[0]
                face_min_x = lt[1]
                face_max_y = rb[0]
                face_max_x = rb[1]

                face_size = (face_max_y - face_min_y) * (face_max_x - face_min_x)

                bbox_min_y = bbox[0]
                bbox_min_x = bbox[1]
                bbox_max_y = bbox[0] + bbox[2]
                bbox_max_x = bbox[1] + bbox[3]

                if face_max_x < bbox_min_x or face_min_x > bbox_max_x or face_max_y < bbox_min_y or face_min_y > bbox_max_y:
                    is_intersect = False
                else:
                    is_intersect = True

                #print(is_intersect)

                if is_intersect:
                    overlap_min_y = max(face_min_y, bbox_min_y)
                    overlap_min_x = max(face_min_x, bbox_min_x)
                    overlap_max_y = min(face_max_y, bbox_max_y)
                    overlap_max_x = min(face_max_x, bbox_max_x)

                    overlap_size = (overlap_max_y - overlap_min_y) * (overlap_max_x - overlap_min_x)
                else:
                    overlap_size = 0
                #print('overlap: %f'%(overlap_size / face_size))

                if overlap_size > best_bbox_cover:
                    best_bbox_cover = overlap_size
                    best_bbox_index = i

            best_bbox_info = f[best_bbox_index + 1].split(' ')
            best_bbox = np.array([int(best_bbox_info[1]), int(best_bbox_info[0]),int(best_bbox_info[3]),int(best_bbox_info[2])])
            best_bbox[0] += int(best_bbox_info[2]) * 0.1

            if float(best_bbox_cover) / face_size < 0.2:
                print('face bbox error!! ',bbox_path)
                return compute_rect_from_shape(ini)
            else:
                return best_bbox

    elif ext == 'rect':
        f = open(bbox_path, 'rb').read()
        face_bbox = np.asarray(struct.unpack('i' * 5, f))
        if face_bbox[0] == 0:
            return compute_rect_from_shape(ini)
        else:
            bbox = np.array([face_bbox[2], face_bbox[1], face_bbox[4], face_bbox[3]])
            bbox[0] += face_bbox[3] * 0.1
            return bbox
    else:
        return compute_rect_from_shape(ini)

def get_img_tag(pts):
    img_tag = []
    eye_close_type = 0
    ratio_mouth_open = 0.8
    ratio_small_mouth_open = 0.4
    ratio_mouth_close = 0.1
    ratio_eye_close = 0.1
    #--
    if pts.shape[0] == 84:
        mouth_inner_height = point_distance(pts[58], pts[62])
        mouth_inner_width = point_distance(pts[57], pts[59])
        mouth_inner_ratio = mouth_inner_height / mouth_inner_width if mouth_inner_width > 0 else 0.0
        if mouth_inner_ratio > ratio_mouth_open:
            img_tag.append('mouth_open')
        elif mouth_inner_ratio > ratio_small_mouth_open:
            img_tag.append('small_mouth_open')
        
        left_eye_height = point_distance(pts[6], pts[8])
        left_eye_width = point_distance(pts[5], pts[9])
        left_eye_ratio = left_eye_height / left_eye_width if left_eye_width > 0 else 0.0
        if left_eye_ratio < ratio_eye_close:
            eye_close_type += 1
            img_tag.append('left_eye_close')
        
        right_eye_height = point_distance(pts[21], pts[23])
        right_eye_width = point_distance(pts[22], pts[24])
        right_eye_ratio = right_eye_height / right_eye_width if right_eye_width > 0 else 0.0
        if right_eye_ratio < ratio_eye_close:
            img_tag.append('right_eye_close')
            eye_close_type += 1
        
        if eye_close_type == 2:
            img_tag.append('both_eyes_close')
        elif eye_close_type == 1:
            img_tag.append('one_eye_close')
    else:
        print('only support 84 point')

    #--
    return img_tag

def get_angle_from_points(points):
    point_num = points.shape[0]

    if point_num == 5:
        left = points[0]
        right = points[1]
    else:
        left = (points[5] + points[9]) / 2
        right = (points[22] + points[24]) / 2

    return atan2(right[0] - left[0], right[1] - left[1])

def get_part_index(face_part):
    if face_part == 'right_eye':
        return np.arange(20, 30, 1)
    elif face_part == 'left_eye':
        return  np.arange(5, 15, 1)
    elif face_part == 'nose':
        return np.arange(30, 39, 1)
    elif face_part == 'mouth':
        return np.arange(39, 65, 1)
    elif face_part == 'right_eyebrow':
        return np.array([15, 16, 17, 18, 19, 83])
    elif face_part == 'left_eyebrow':
        return np.array([0, 1, 2, 3, 4, 82])
    elif face_part == 'right_contour':
        return np.array([79, 80, 81])
    elif face_part == 'left_contour':
        return np.array([65, 66, 67])
    elif face_part == 'bottom_contour':
        return np.array([68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78])
    else:
        return np.arange(0, 84, 1)

def load_img_path(path_file=''):
    img_path = []
    
    with open(path_file, 'r') as f:
        for line in f:                              #need to encoding='unicode'->'utf-8'
            if line[-1] == '\n':
                img_path.append(line[:-1])
            else:
                img_path.append(line)

    return img_path

def get_trans_data(trans_path, result_path):
    file_content = np.load(trans_path)
    file_label = file_content['label']
    file_trans = file_content['trans']  #[perturb, transform, shrink_ratio, normalize]

    step_1_result = np.load(result_path)

    ori_label = file_label.reshape(file_label.shape[0], -1, 2)
    ori_step_1_label = step_1_result.reshape(step_1_result.shape[0], -1, 2)
    batch_perturb = []
    batch_transform = []
    batch_normalize = []

    for i in range(file_label.shape[0]):
        perturb = file_trans[i, 0]
        transform = file_trans[i, 1]
        shrink_ratio = file_trans[i, 2]
        normalize = file_trans[i, 3]
        label = file_label[i].reshape(-1, 2)
        step_1_label = step_1_result[i].reshape(-1, 2)

        if perturb.flip:
            label[:, 1] = 112 - label[:, 1]
            label = flip_face_label(label)
            step_1_label[:, 1] = 112 - step_1_label[:, 1]
            step_1_label = flip_face_label(step_1_label)

        ori_label[i] = backward_transform(transform, ((label - normalize.shift) / normalize.scale))
        ori_step_1_label[i] = backward_transform(transform, ((step_1_label - normalize.shift) / normalize.scale))
        batch_perturb.append(perturb)
        batch_transform.append(transform)
        batch_normalize.append(normalize)


    return ori_label, ori_step_1_label, batch_perturb, batch_transform, shrink_ratio, batch_normalize

def get_part_points(point, face_part):
    right_eye_index = np.arange(20, 30, 1)
    left_eye_index = np.arange(5, 15, 1)
    nose_index = np.arange(30, 39, 1)
    mouth_index = np.arange(39, 65, 1)
    right_eyebrow_index = np.array([15, 16, 17, 18, 19, 83])
    left_eyebrow_index = np.array([0, 1, 2, 3, 4, 82])
    right_contour_index = np.array([79, 80, 81])
    left_contour_index = np.array([65, 66, 67])
    bottom_contour_index = np.array([68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78])

    reshape_point = point.reshape(point.shape[0], -1, 2)

    if face_part == 'right_eye':
        return reshape_point[:, right_eye_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'left_eye':
        return reshape_point[:, left_eye_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'nose':
        return reshape_point[:, nose_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'mouth':
        return reshape_point[:, mouth_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'right_eyebrow':
        return reshape_point[:, right_eyebrow_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'left_eyebrow':
        return reshape_point[:, left_eyebrow_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'right_contour':
        return reshape_point[:, right_contour_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'left_contour':
        return reshape_point[:, left_contour_index].reshape(reshape_point.shape[0], -1)
    elif face_part == 'bottom_contour':
        return reshape_point[:, bottom_contour_index].reshape(reshape_point.shape[0], -1)
    else:
        return reshape_point.reshape(reshape_point.shape[0], -1)

def all_part_nme(result, gt_label, database, face_part):
    right_eye_index = np.arange(20, 30, 1)
    left_eye_index = np.arange(5, 15, 1)
    nose_index = np.arange(30, 39, 1)
    mouth_index = np.arange(39, 65, 1)
    right_eyebrow_index = np.array([15, 16, 17, 18, 19, 83])
    left_eyebrow_index = np.array([0, 1, 2, 3, 4, 82])
    right_contour_index = np.array([79, 80, 81])
    left_contour_index = np.array([65, 66, 67])
    bottom_contour_index = np.array([68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78])

    right_eye_nme = left_eye_nme = nose_nme = mouth_nme = right_eyebrow_nme = left_eyebrow_nme = right_contour_nme = left_contour_nme = bottom_contour_nme = 0

    if face_part == 'face':
        right_eye_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, right_eye_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, right_eye_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
        left_eye_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, left_eye_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, left_eye_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
        nose_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, nose_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, nose_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
        mouth_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, mouth_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, mouth_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)


        if face_part == 'no_contour':
            no_contour_right_eyebrow_index = np.array([15, 16, 17, 18, 19, 66])
            no_contour_left_eyebrow_index = np.array([0, 1, 2, 3, 4, 65])

            right_eyebrow_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, no_contour_right_eyebrow_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, right_eyebrow_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
            left_eyebrow_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, no_contour_left_eyebrow_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, left_eyebrow_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
            right_contour_nme = 0
            left_contour_nme = 0
            bottom_contour_nme = 0
        else:
            right_eyebrow_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, right_eyebrow_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, right_eyebrow_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
            left_eyebrow_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, left_eyebrow_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, left_eyebrow_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
            right_contour_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, right_contour_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, right_contour_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
            left_contour_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, left_contour_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, left_contour_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
            bottom_contour_nme = compute_nme(result.reshape([result.shape[0], -1, 2])[:, bottom_contour_index].reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, bottom_contour_index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
    else:
        index = get_part_index(face_part)
        nme = compute_nme(result.reshape([result.shape[0], -1, 2]).reshape([result.shape[0], -1]), gt_label.reshape([gt_label.shape[0], -1, 2])[:, index].reshape([gt_label.shape[0], -1]), gt_label, database=database)
        if face_part == 'right_eye':
            right_eye_nme = nme
        elif face_part == 'left_eye':
            left_eye_nme = nme
        elif face_part == 'mouth':
            mouth_nme = nme
        elif face_part == 'right_eyebrow':
            right_eyebrow_nme = nme
        elif face_part == 'left_eyebrow':
            left_eyebrow_nme = nme


    return right_eye_nme, left_eye_nme, nose_nme, mouth_nme, right_eyebrow_nme, left_eyebrow_nme, right_contour_nme, left_contour_nme, bottom_contour_nme

def CheckSlash(folder_path):
    result_path = folder_path
    if len(folder_path) > 0:
        last_char = folder_path[-1]
        if (last_char != '/'):
            result_path += '/'

    return result_path
    
    
def PrepareFolder(folder_path):
    checked_folder_path = CheckSlash(folder_path)
    if not os.path.isdir(checked_folder_path):
        os.makedirs(checked_folder_path)
    
    return checked_folder_path
    
    
def MakePath(folder_path, file_name):
    full_path = CheckSlash(folder_path) + file_name
    return full_path

def SplitPath(file_path):
    folder_path = ''
    full_name = file_path
    
    if file_path.rfind('/') != -1:
        path_partition = file_path.rpartition('/')
        folder_path = path_partition[0] + path_partition[1]
        full_name = path_partition[2]
        
    base_name = full_name
    ext_name = ''
    if full_name.find('.') != -1:
        name_partition = full_name.partition('.')
        base_name = name_partition[0]
        ext_name = name_partition[1] + name_partition[2]

    return folder_path, base_name, ext_name

def GetFileName(file_path):
    full_name = file_path
    if full_name.rfind('/') != -1:
        path_partition = file_path.rpartition('/')
        full_name = path_partition[2]

    base_name = full_name
    if base_name.find('.') != -1:
        name_partition = base_name.partition('.')
        base_name = name_partition[0]
        
    return base_name, full_name


def GetBaseName(file_path):
    base_name, full_name = GetFileName(file_path)
    return base_name


def GetFullName(file_path):
    base_name, full_name = GetFileName(file_path)
    return full_name

def GetExtName(file_path):
    folder_path, base_name, ext_name = SplitPath(file_path)
    return ext_name
    
def GetBaseAndExtName(file_path):
    folder_path, base_name, ext_name = SplitPath(file_path)
    return base_name, ext_name

def FindNumber(file_path, label_str):
    base_name = GetBaseName(file_path)
    label_index = base_name.find(label_str)
    if (label_index == -1):
        return -1
        
    # Get the number after label_str
    name_length = len(base_name)
    start_index = label_index + len(label_str)
        
    value_string = ''
    for j in range(start_index, name_length):
        if (ord(base_name[j]) >= ord('0') and ord(base_name[j]) <= ord('9')):
            value_string += base_name[j]
        else:
            break
                
    if (len(value_string) > 0):
        return int(value_string)
    else:
        return -1


def SaveImageCHW(image, save_path):
    # Assume the data format is Channel x Height x Width (Usually from network input array).
    
    channels = image.shape[0]
    if (channels >= 3):
        image_3ch = image[0:3, :, :].copy()
    else:
        image_3ch = image[0:1, :, :].repeat(3, axis=0)
    
    image_3ch[image_3ch < 0.0] = 0.0
    image_3ch[image_3ch > 1.0] = 1.0
    
    byte_image = (image_3ch.transpose(1, 2, 0) * 255).astype(np.uint8)
    saved_image = Image.fromarray(byte_image, 'RGB')
    saved_image.save(save_path)

    
def SaveImageHWC(image, save_path):
    # Assume the data format is Height x Width x Channel.
    
    channels = image.shape[2]
    if (channels >= 3):
        image_3ch = image[:, :, 0:3].copy()
    else:
        image_3ch = image[:, :, 0:1].repeat(3, axis=2)
    
    image_3ch[image_3ch < 0.0] = 0.0
    image_3ch[image_3ch > 1.0] = 1.0
    
    byte_image = (image_3ch * 255).astype(np.uint8)
    saved_image = Image.fromarray(byte_image, 'RGB')
    saved_image.save(save_path)
    


def CountInRange(image, min_value=-sys.float_info.max, max_value=sys.float_info.max):
    check_image = np.ones(image.shape, dtype=image.dtype)
    check_image[image < min_value] = 0.0
    check_image[image >= max_value] = 0.0
    return np.sum(check_image)


def SaveFloatArrayAsByte(save_path, float_array):
    truncated_array = float_array.copy()
    truncated_array[truncated_array < 0.0] = 0.0
    truncated_array[truncated_array > 1.0] = 1.0
    
    np.save(save_path, np.round(truncated_array * 255.0).astype(np.uint8))
    
    del truncated_array


def LoadByteArrayAsFloat(load_path):
    float_array = np.asarray(np.load(load_path), dtype=np.float32) / 255.0
    return float_array
    
def SafeDivide(a, b, epsilon=0.000001):
    safe_b = b
    if (b >= 0):
        safe_b = max(safe_b, epsilon)
    else:
        safe_b = min(safe_b, -epsilon)

    return a / safe_b

def PointDistance(point1, point2):
    dx = point1[0] - point2[0]
    dy = point1[1] - point2[1]
    return np.sqrt(dx * dx + dy * dy)

def GetRotateFactor(point1_yx, point2_yx):
    dx = point2_yx[1] - point1_yx[1]
    dy = point2_yx[0] - point1_yx[0]
    length = np.sqrt(dx * dx + dy * dy)
    
    rotate_sin = 0.0
    rotate_cos = 1.0
    if (length > 0.000001):
        rotate_sin = -dy / length
        rotate_cos =  dx / length
    
    return rotate_sin, rotate_cos

def RotatePoint(src_point_yx, rotate_sin, rotate_cos):
    dst_x = src_point_yx[1] * rotate_cos - src_point_yx[0] * rotate_sin
    dst_y = src_point_yx[1] * rotate_sin + src_point_yx[0] * rotate_cos
    return [dst_y, dst_x]

def InverseRotatePoint(src_point_yx, rotate_sin, rotate_cos):
    dst_x =  src_point_yx[1] * rotate_cos + src_point_yx[0] * rotate_sin
    dst_y = -src_point_yx[1] * rotate_sin + src_point_yx[0] * rotate_cos
    return [dst_y, dst_x]

def normalize_align_points_for_pose_estimate(align_points):
    # Calculate the face shape fit box
    min_coords = np.min(align_points, axis=0)
    max_coords = np.max(align_points, axis=0)
    center_coords = (min_coords + max_coords) / 2
    # Use face length as scale factor
    left_eartop, right_eartop = align_points[65], align_points[81]
    face_length = np.linalg.norm(left_eartop - right_eartop)
    # Update final bbox
    min_coords = center_coords - 1.5 * face_length
    max_coords = center_coords + 1.5 * face_length
    # Re-Normalize points
    return (align_points - min_coords) / (max_coords - min_coords), (min_coords[0], min_coords[1], max_coords[0] - min_coords[0], max_coords[1] - min_coords[1])

def landmarks_to_68(points):
    points_copy = points.copy()[:84]
    indices_to_delete = [0, 1, 2, 3, 4, 7, 10, 15, 16, 17, 18, 19, 20, 25, 82, 83]
    mask = np.ones(len(points_copy), dtype=bool)
    mask[indices_to_delete] = False
    return points_copy[mask]

class PoseValueMapper:
    def __init__(self):
        self.m_min_values = np.zeros(6, dtype=np.float32)
        self.m_max_values = np.zeros(6, dtype=np.float32)
        
        rotation_range = np.deg2rad(45.0)
        min_values = [-rotation_range, -rotation_range, -rotation_range, -1.0, -1.5, -10.0]
        max_values = [ rotation_range,  rotation_range,  rotation_range,  1.0,  0.5,  -2.0]
        
        self.SetMinMaxValues(min_values, max_values)
        
    def SetMinMaxValues(self, min_values, max_values):
        for i in range(6):
            self.m_min_values[i] = min_values[i]
            self.m_max_values[i] = max_values[i]
        
        self.m_centers = (self.m_min_values + self.m_max_values) * 0.5
        self.m_scales = 2.0 / (self.m_max_values - self.m_min_values)
        self.m_inv_scales = 1.0 / self.m_scales
        
    def GetMinMaxValues(self):
        return (list(self.m_min_values), list(self.m_max_values))
    
    def Map(self, values):
        return (values - self.m_centers) * self.m_scales
    
    def Unmap(self, values):
        return values * self.m_inv_scales + self.m_centers