import os
import sys
from collections import namedtuple
import configparser
import numpy as np
import struct
import math

import struct
import copy
import cv2 as cv
cv.setNumThreads(12) 

import skimage.io
import skimage.color
from skimage import draw
from skimage import transform as sktf
import time
from pathlib import Path

from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

FileStem = namedtuple('FileStem', 'image_path bg_mask_path rect face_landmarks track_landmarks \
                                   mouth_closed mouth_type pose iris_radius iris_points iris_ratio \
                                   is_mask_sample earlobe_points earlobe_visibility yaw pitch sample_weight')
valid_align_index_of_ini = (list(range(0, 35)) +
                            [36, 38, 39] + list(range(40, 84)) +
                            [85, 88])
align_mirror_map = {
    0: 19, 1: 18, 2: 17, 3: 16, 4: 15,
    5: 22, 6: 21, 7: 20, 8: 23, 9: 24,
    10: 25, 11: 27, 12: 26, 13: 29, 14: 28,
    34: 36, 37: 38,
    39: 43, 40: 42, 44: 45, 46: 49, 47: 48, 51: 52,
    53: 56, 54: 55, 57: 59, 60: 61, 63: 64,
    65: 81, 66: 80, 67: 79, 68: 78, 69: 77, 70: 76, 71: 75, 72: 74,
    82: 83
}
align_mirror_map.update({v: k for k, v in align_mirror_map.items()})
align_mirror_map.update({k: k for k in range(84) if k not in align_mirror_map})
align_mirror_indices = [align_mirror_map[k] for k in range(84)]

align_mirror_map_116 = {
    0: 19, 1: 18, 2: 17, 3: 16, 4: 15,
    5: 22, 6: 21, 7: 20, 8: 23, 9: 24,
    10: 25, 11: 27, 12: 26, 13: 29, 14: 28,
    34: 36, 37: 38, 39: 43, 40: 42, 44: 45, 
    46: 49, 47: 48, 51: 52, 53: 56, 54: 55, 
    57: 59, 60: 61, 63: 64, 65: 81, 66: 80, 
    67: 79, 68: 78, 69: 77, 70: 76, 71: 75, 72: 74, 82: 83,
    84: 91, 85: 90, 86: 89, 87: 88, 92:99, 93:98,
    94:97, 95:96, 100:107, 101:106, 102:105, 103:104,
    108:115, 109:114, 110:113, 111:112
}
align_mirror_map_116.update({v: k for k, v in align_mirror_map_116.items()})
align_mirror_map_116.update({k: k for k in range(116) if k not in align_mirror_map_116})
align_mirror_indices_116 = [align_mirror_map_116[k] for k in range(116)]

align_mirror_map_100_for_adding_mouth_pts_from_new_ini = {
    0: 19, 1: 18, 2: 17, 3: 16, 4: 15,
    5: 22, 6: 21, 7: 20, 8: 23, 9: 24,
    10: 25, 11: 27, 12: 26, 13: 29, 14: 28,
    34: 36, 37: 38, 39: 43, 40: 42, 44: 45, 
    46: 49, 47: 48, 51: 52, 53: 56, 54: 55, 
    57: 59, 60: 61, 63: 64, 65: 81, 66: 80, 
    67: 79, 68: 78, 69: 77, 70: 76, 71: 75, 72: 74, 82: 83,
    84: 91, 85: 90, 86: 89, 87: 88,
    92: 99, 93: 98, 94: 97, 95: 96
}
align_mirror_map_100_for_adding_mouth_pts_from_new_ini.update({v: k for k, v in align_mirror_map_100_for_adding_mouth_pts_from_new_ini.items()})
align_mirror_map_100_for_adding_mouth_pts_from_new_ini.update({k: k for k in range(100) if k not in align_mirror_map_100_for_adding_mouth_pts_from_new_ini})
align_mirror_indices_100_for_adding_mouth_pts_from_new_ini = [align_mirror_map_100_for_adding_mouth_pts_from_new_ini[k] for k in range(100)]

align_mirror_map_100_for_adding_extra_contour_pts = {
    0: 19, 1: 18, 2: 17, 3: 16, 4: 15,
    5: 22, 6: 21, 7: 20, 8: 23, 9: 24,
    10: 25, 11: 27, 12: 26, 13: 29, 14: 28,
    34: 36, 37: 38, 39: 43, 40: 42, 44: 45, 
    46: 49, 47: 48, 51: 52, 53: 56, 54: 55, 
    57: 59, 60: 61, 63: 64, 65: 81, 66: 80, 
    67: 79, 68: 78, 69: 77, 70: 76, 71: 75, 72: 74, 82: 83,
    84: 99, 85: 98, 86: 97, 87: 96, 88: 95, 89: 94, 90: 93, 91: 92 
}
align_mirror_map_100_for_adding_extra_contour_pts.update({v: k for k, v in align_mirror_map_100_for_adding_extra_contour_pts.items()})
align_mirror_map_100_for_adding_extra_contour_pts.update({k: k for k in range(100) if k not in align_mirror_map_100_for_adding_extra_contour_pts})
align_mirror_indices_100_for_adding_extra_contour_pts = [align_mirror_map_100_for_adding_extra_contour_pts[k] for k in range(100)]

part_indexlists = {
    'eye': list(range(5, 15)) + list(range(20, 30)),
    'mouth': list(range(39, 65)),
    'brow': list(range(0, 5)) + list(range(15, 20)) + list(range(82, 84)),
    'contour': list(range(65, 82)),
    'nose': list(range(30, 39)),
    'eyetop': [6, 21],
    'eartop': [65, 81],
    'contour_left': list(range(65, 74)),
    'contour_right': list(range(73, 82)),
}

part_indexlists_116 = {
    'eye': list(range(5, 15)) + list(range(20, 30)),
    'mouth': list(range(39, 65)) + list(range(84, 116)),
    'brow': list(range(0, 5)) + list(range(15, 20)) + list(range(82, 84)),
    'contour': list(range(65, 82)),
    'nose': list(range(30, 39)),
    'eyetop': [6, 21],
    'eartop': [65, 81],
    'contour_left': list(range(65, 74)),
    'contour_right': list(range(73, 82)),
    'only_old_mouth_pts': list(range(39, 65))
}

part_indexlists_100_for_adding_mouth_pts_from_new_ini = {
    'eye': list(range(5, 15)) + list(range(20, 30)),
    'mouth': list(range(39, 65)) + list(range(84, 100)),
    'brow': list(range(0, 5)) + list(range(15, 20)) + list(range(82, 84)),
    'contour': list(range(65, 82)),
    'nose': list(range(30, 39)),
    'eyetop': [6, 21],
    'eartop': [65, 81],
    'contour_left': list(range(65, 74)),
    'contour_right': list(range(73, 82)),
    'only_old_mouth_pts': list(range(39, 65))
}

part_indexlists_100_for_adding_extra_contour_pts = {
    'eye': list(range(5, 15)) + list(range(20, 30)),
    'mouth': list(range(39, 65)),
    'brow': list(range(0, 5)) + list(range(15, 20)) + list(range(82, 84)),
    'contour': list(range(65, 82)) + list(range(84, 100)),
    'nose': list(range(30, 39)),
    'eyetop': [6, 21],
    'eartop': [65, 81],
    'contour_left': list(range(65, 74)),
    'contour_right': list(range(73, 82)),
}

subshape_indices = [
    0, 1, 2, 3, 4,
    5, 6, 8, 9, 10,
    15, 16, 17, 18, 19,
    21, 22, 23, 24, 25,
    30, 33, 35, 37, 38,
    39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64,
    65, 67, 69, 71, 73, 75, 77, 79, 81,
    82, 83,
]

celeb_mirror_indices = [1, 0, 2, 4, 3]

mapping_84_to_163 = [
# left brow  5
0, 1, 2, 4, 5, 

# left eye  10
20, 24, 29, 33, 28, 37, 22, 26, 31, 35,

# right brow  5
10, 11, 13, 14, 15,

# right eye  10
38, 43, 47, 51, 39, 55, 41, 45, 49, 53,

# nose  9
56, 57, 58, 59, 61, 63, 65, 60, 66,

# mouth  26
67, 75, 76, 77, 85, 71, 81, 69, 73, 79, 83, 93, 97, 89, 99, 95, 91, 87,
101, 107, 113, 104, 110, 119, 122, 116,

# contour  17
157, 155, 153, 151, 149, 147, 145, 143, 141, 139, 137, 135, 133, 131, 129, 127, 125,

#brow bottom  2
8, 17,

]


large_angle_folder = ['large_head_pose', 'large_head_pose_mt']

duplicate_table = [
    17 ,10 ,50 ,0 ,50 ,25 ,9 ,17 ,10 ,0 ,50 ,25 ,0 ,0 ,50 ,25,
    25 ,50 ,25 ,0 ,25 ,17 ,13 ,9 ,25 ,13 ,0 ,13 ,25 ,25 ,25 ,25,
    13 ,13 ,9 ,13 ,10 ,6 ,10 ,7 ,6 ,5 ,10 ,13 ,25 ,17 ,13 ,17,
    4 ,7 ,3 ,4 ,6 ,5 ,4 ,2 ,3 ,4 ,6 ,5 ,8 ,4 ,6 ,4,
    4 ,3 ,3 ,9 ,5 ,6 ,2 ,1 ,1 ,3 ,2 ,5 ,4 ,5 ,3 ,4,
    2 ,4 ,2 ,3 ,2 ,3 ,1 ,1 ,1 ,1 ,2 ,2 ,4 ,2 ,3 ,1,
    1 ,1 ,2 ,1 ,2 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,2 ,1 ,2 ,2,
    1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1,
    1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1,
    1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1,
    1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1,
    1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1 ,1,
    2 ,2 ,1 ,2 ,2 ,1 ,1 ,1 ,1 ,1 ,2 ,2 ,2 ,1 ,2 ,2,
    4 ,7 ,4 ,4 ,4 ,2 ,1 ,2 ,2 ,3 ,2 ,2 ,4 ,4 ,10 ,10,
    50 ,7 ,5 ,8 ,7 ,7 ,4 ,2 ,6 ,9 ,6 ,25 ,7 ,7 ,7 ,9,
    25 ,10 ,13 ,0 ,6 ,13 ,9 ,6 ,5 ,7 ,8 ,13 ,17 ,17 ,0 ,10
]

# Print iterations progress
def print_progress(iteration, total, prefix='', suffix='', decimals=1, bar_length=100):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        bar_length  - Optional  : character length of bar (Int)
    """
    str_format = "{0:." + str(decimals) + "f}"
    percents = str_format.format(100 * (iteration / float(total)))
    filled_length = int(round(bar_length * iteration / float(total)))
    bar = 'x' * filled_length + '-' * (bar_length - filled_length)

    sys.stdout.write('\r%s |%s| %s%s %s' % (prefix, bar, percents, '%', suffix)),

    if iteration == total:
        sys.stdout.write('\n')
    sys.stdout.flush()


def load_rect(rect_path):
    with open(rect_path, 'rb') as f:
        line = f.read()
        face_count = struct.unpack('i', line[:4])
        if face_count[0] > 0:
            return np.asarray(struct.unpack('iiii', line[4:]))
        else:
            return None

def load_occlude_image_npy_list(dir_path_list):
    npy_list = []
    for dirpath, dirnames, filenames in os.walk(dir_path_list):
        for filename in filenames:
            file_prefix, file_ext = os.path.splitext(filename)
            if file_ext == '.npy':
                npyname = os.path.join(dirpath, filename)
                npy_list.append(npyname)
    return npy_list

def load_ini_folder_list(dir_path_list, tracking_ini_list=(), npy_path_list=(), count=0, offset=0, dde_path_list=(), file_path_list=(), bg_mask_img_folder_path=(), duplicate_sample=False, duplicate_time=5, data_balance=False, mode='normal'):
    faces = []
    img_ext_list = {'.jpg', '.png', '.JPG', '.Jpg'}
    if file_path_list:
        file_path_list = [os.path.splitext(os.path.split(i)[-1])[0] for i in file_path_list]

    ini_list = []
    big_angle_count = 0
    dde_count = 0
    none_dde_count = 0
    for dir_i, dir_path in enumerate(dir_path_list):
        for dirpath, dirnames, filenames in os.walk(dir_path):

            for filename in filenames:
                if(("ear" in dirpath) or 
                ("white_background" in dirpath) or
                ("Jackei" in dirpath) or 
                ("magic head_rotate" in dirpath)):
                    continue
                file_prefix, file_ext = os.path.splitext(filename)

                if file_ext == '.ini' and (not file_path_list or file_prefix in file_path_list):
                    npyname = None
                    if len(npy_path_list) > dir_i:
                        npy_root_path = npy_path_list[dir_i]
                        npy_path = (os.path.join(dirpath, filename)).replace(dir_path, npy_root_path)
                        npy_path = os.path.splitext(npy_path)[0] + '.npy'
                        if os.path.isfile(npy_path):
                            npyname = npy_path
                    ddename = None
                    if len(dde_path_list) > dir_i:
                        dde_root_path = dde_path_list[dir_i]
                        dde_path = (os.path.join(dirpath, filename)).replace(dir_path, dde_root_path)
                        dde_path = os.path.splitext(dde_path)[0] + '.dde'
                        if os.path.isfile(dde_path):
                            ddename = dde_path
                    ini_list.append((os.path.join(dirpath, filename), dirpath, filename, npyname, ddename))

    config = configparser.ConfigParser()
    config_track = configparser.ConfigParser()
    tracking_ini_loaded = 0
    max_loaded_count = len(ini_list)
    if count > 0:
        max_loaded_count = min(max_loaded_count, max(0, offset) + count)
    for ini_i, ini_element in enumerate(ini_list):
        print_progress(ini_i + 1, max_loaded_count)
        pts_name, dir_path, filename, npyname, ddename = ini_element

        def get_loading_pts_name(fullname):
            for i in tracking_ini_list:
                tracking_name = os.path.split(i)[-1]
                name = os.path.split(fullname)[-1]
                if tracking_name == name:
                    return i
            return fullname

        loading_pts_name = get_loading_pts_name(pts_name)
        rect_path = os.path.splitext(pts_name)[0] + '.rect'
        rect = None
        if os.path.isfile(rect_path):
            rect = load_rect(rect_path)
        for ext_candidate in img_ext_list:
            img_path = os.path.splitext(pts_name)[0] + ext_candidate
            if os.path.isfile(img_path):
                def read_landmarks(config_, name, mode):
                    ret_list = []
                    config_.clear()
                    config_.read(name)
                    is_tagged_ini = config_.has_option('Landmark', 'Landmark_84')
                    for index, j in enumerate(valid_align_index_of_ini if is_tagged_ini else range(84)):
                        coorstr = config_.get('Landmark', 'Landmark_' + str(j)).split()
                        ret_list.append(np.asarray([float(k) for k in coorstr]))
                    return ret_list
                    # elif (mode == 'extra_contour'):
                    #     is_tagged_ini = config_.has_option('Landmark', 'Landmark_84')
                    #     for index, j in enumerate(valid_align_index_of_ini if is_tagged_ini else range(84)):
                    #         coorstr = config_.get('Landmark', 'Landmark_' + str(j)).split()
                    #         ret_list.append(np.asarray([float(k) for k in coorstr]))

                    #     for i in range(65,81):
                    #         new_contour_pt = []
                    #         new_contour_pt.append((ret_list[i][0] + ret_list[i+1][0]) / 2)
                    #         new_contour_pt.append((ret_list[i][1] + ret_list[i+1][1]) / 2)
                    #         ret_list.append(new_contour_pt)                    
                    #     return ret_list

                loaded_list = read_landmarks(config, pts_name, mode)
                track_loaded_list = (
                    [] if pts_name == loading_pts_name
                    else read_landmarks(config_track, loading_pts_name, mode)
                )

                landmarks = np.asarray(loaded_list).astype(np.float32)
                if mode == 'extra_contour':
                    extra_contour = generate_extra_contour(landmarks)
                    landmarks = np.vstack((landmarks,extra_contour))
                is_equal = np.abs(landmarks[[58, 60, 61]] - landmarks[[62, 63, 64]]) == 0
                mouth_closed = is_equal.all()

                inner_mouth_width = point_distance(landmarks[57], landmarks[59])
                inner_mouth_height = point_distance(landmarks[58], landmarks[62])
                inner_mouth_ratio = inner_mouth_height/ (inner_mouth_width + 1e-6)
                if inner_mouth_ratio < 0.1:
                    mouth_type = 0
                elif inner_mouth_ratio < 0.4:
                    mouth_type = 1
                elif inner_mouth_ratio < 0.8:
                    mouth_type = 2
                else:
                    mouth_type = 3

                pose = 'unknown'
                frontal_yaw = 15. / 180. * 3.14159265359
                iris_radius = (-1., -1.)
                iris_ratio = 0.5
                def get_iris_point(center, eye_corner, radius):
                    distance = point_distance(center, eye_corner)
                    if distance > 0.0:
                        uni_vector = (eye_corner - center) / distance
                        return center + uni_vector * radius
                    else:
                        return center

                def compute_iris_location_ratio(iris_center, inner_corner, outer_corner):
                    distance_to_inner = point_distance(iris_center, inner_corner)
                    distance_to_outer = point_distance(iris_center, outer_corner)
                    if distance_to_inner + distance_to_outer == 0:
                        print ("pts = %s" % pts_name)
                    return distance_to_inner / (distance_to_inner + distance_to_outer)

                iris_points = (landmarks[10], landmarks[10], landmarks[25], landmarks[25])

                left_eye_out = landmarks[5]
                left_eye_in  = landmarks[9]
                right_eye_out = landmarks[22]
                right_eye_in  = landmarks[24]
                iris_points = (landmarks[10], landmarks[10], landmarks[25], landmarks[25])
                left_out_radius = point_distance(landmarks[10], left_eye_out) * 0.52
                left_in_radius = point_distance(landmarks[10], left_eye_in) * 0.54
                right_in_radius = point_distance(landmarks[25], right_eye_in) * 0.54
                right_out_radius = point_distance(landmarks[25], right_eye_out) * 0.52
                iris_points = (get_iris_point(landmarks[10], left_eye_out, left_out_radius),
                               get_iris_point(landmarks[10], left_eye_in, left_in_radius),
                               get_iris_point(landmarks[25], right_eye_in, right_in_radius),
                               get_iris_point(landmarks[25], right_eye_out, right_out_radius))

                if config.has_option('Attribute', 'LEFT_IRIS_RADIUS'):
                    iris_radius = (float(config.get('Attribute', 'LEFT_IRIS_RADIUS')),
                                   float(config.get('Attribute', 'RIGHT_IRIS_RADIUS')))
                    left_eye_center_str = config.get('Attribute', 'LEFT_IRIS_CENTER').split()
                    landmarks[10, 0] = float(left_eye_center_str[0])
                    landmarks[10, 1] = float(left_eye_center_str[1])
                    right_eye_center_str = config.get('Attribute', 'RIGHT_IRIS_CENTER').split()
                    landmarks[25, 0] = float(right_eye_center_str[0])
                    landmarks[25, 1] = float(right_eye_center_str[1])

                    left_iris_out = iris_points[0]
                    left_iris_in = iris_points[1]
                    right_iris_in = iris_points[2]
                    right_iris_out = iris_points[3]

                    if iris_radius[0] > 0.0001 :
                        left_iris_out = get_iris_point(landmarks[10], left_eye_out, iris_radius[0])
                        left_iris_in = get_iris_point(landmarks[10], left_eye_in, iris_radius[0])
                    if iris_radius[1] > 0.0001 :
                        right_iris_in = get_iris_point(landmarks[25], right_eye_in, iris_radius[1])
                        right_iris_out = get_iris_point(landmarks[25], right_eye_out, iris_radius[1])

                    iris_points = (left_iris_out,
                                   left_iris_in,
                                   right_iris_in,
                                   right_iris_out)

                is_mask_sample = (0., 1.)
                if config.has_option('Attribute', 'MASK_SAMPLE'):
                    mask_value = float(config.get('Attribute', 'MASK_SAMPLE'))
                    is_mask_sample = (mask_value, 1. - mask_value)

                
                left_earlobe = landmarks[67].copy()
                right_earlobe = landmarks[79].copy()
                if config.has_option('Landmark', 'Landmark_90') and config.has_option('Landmark', 'Landmark_91'):
                    left_earlobe_str = config.get('Landmark', 'Landmark_90').split()
                    left_earlobe[0] = float(left_earlobe_str[0])
                    left_earlobe[1] = float(left_earlobe_str[1])
                    right_earlobe_str = config.get('Landmark', 'Landmark_91').split()
                    right_earlobe[0] = float(right_earlobe_str[0])
                    right_earlobe[1] = float(right_earlobe_str[1])
                earlobe_landmarks = (left_earlobe, right_earlobe)

                left_visibility = 1.0
                right_visibility = 1.0
                if config.has_option('Attribute', 'LEFT_EARLOBE_OCCLUDED'):
                    left_visibility = float(config.get('Attribute', 'LEFT_EARLOBE_OCCLUDED'))
                if config.has_option('Attribute', 'RIGHT_EARLOBE_OCCLUDED'):
                    right_visibility = float(config.get('Attribute', 'RIGHT_EARLOBE_OCCLUDED'))
                earlobe_visibility = (left_visibility, right_visibility)
                
                duplicate_num = 1

                if ddename:
                    dde_count += 1
                    with open(ddename, 'r') as f:
                        lines = f.readlines()
                        yaw = float(lines[9].split(' ')[0])
                        pitch = float(lines[9].split(' ')[1])
                        pose = 'frontal' if np.abs(yaw) < frontal_yaw else 'potrait'
                        if pose == 'potrait':
                            iris_ratio = compute_iris_location_ratio(landmarks[25], right_eye_in, right_eye_out) if yaw > 0 else \
                                         compute_iris_location_ratio(landmarks[10], left_eye_in, left_eye_out)

                    if duplicate_sample:
                        if ((pitch > 20. / 180. * 3.14159265359) or (pitch < -15. / 180. * 3.14159265359)):
                        # if ((np.abs(yaw) > 10. / 180. * 3.14159265359) and (np.abs(pitch) > 20. / 180. * 3.14159265359)):
                            big_angle_count += 1
                            duplicate_num = duplicate_time
                    
                    if data_balance:
                        pitch_index = int(min(np.floor(((pitch * 180 / 3.14159265359) + 40) / 5), 15))
                        yaw_index = int(min(np.floor(((yaw * 180 / 3.14159265359) + 40) / 5), 15))
                        duplicate_num = duplicate_table[pitch_index * 16 + yaw_index]
                else:
                    none_dde_count += 1
                    if duplicate_sample:
                        is_large_angle = False
                        for i in range(len(large_angle_folder)):
                            is_large_angle |= large_angle_folder[i] in dir_path
                        if is_large_angle:
                            big_angle_count += 1
                            duplicate_num = duplicate_time

                img_bg_mask_path = ''
                for folder_path in bg_mask_img_folder_path:
                    tmp_path = f'{folder_path}/{str(Path(img_path).parent).split("/")[-1]}/{Path(img_path).name}.revised_background_mask.npy'
                    if (os.path.isfile(tmp_path)):
                        img_bg_mask_path = tmp_path
                for i in range(duplicate_num):
                    faces.append(
                        FileStem(
                            img_path if npyname is None else npyname,
                            img_bg_mask_path,
                            rect,
                            landmarks,
                            None if len(track_loaded_list) == 0 else np.asarray(track_loaded_list),
                            mouth_closed,
                            mouth_type,
                            pose,
                            np.asarray(iris_radius),
                            np.asarray(iris_points),
                            iris_ratio,
                            np.asarray(is_mask_sample),
                            np.asarray(earlobe_landmarks),
                            np.asarray(earlobe_visibility),
                            yaw if ddename else 0,
                            pitch if ddename else 0,
                            1.0,
                        )
                    )

                if loading_pts_name != pts_name:
                    tracking_ini_loaded += 1

                if count > 0 and len(faces) >= offset + count:
                    del faces[(offset + count):]
                    break

                break

            if count > 0 and len(faces) >= offset + count:
                break
        if count > 0 and len(faces) >= offset + count:
            break
    if offset > 0:
        del faces[:offset]
    print('loaded data:', len(faces), ', using tracking:', tracking_ini_loaded)
    print('big angle: %d, dde: %d, none dde: %d'%(big_angle_count, dde_count, none_dde_count))

    return faces

def load_celeba_folder(dir_path, npy_dir_path='', count=0, offset=0):

    list_path = os.path.join(dir_path, 'Anno/list_landmarks_celeba.txt')
    image_dir = os.path.join(dir_path, 'Img/img_celeba')
    if os.path.isfile(list_path) is not True:
        raise FileNotFoundError(list_path)
    if os.path.isdir(image_dir) is not True:
        raise FileNotFoundError(image_dir)
    if os.path.isdir(npy_dir_path) is not True:
        npy_dir_path = ''

    excluded = ['109050.jpg', '092543.jpg']
    faces = []
    with open(list_path, 'r') as f:
        for i in range(2):
            next(f)

        for line in f:
            filename, *coords = line.split()
            if filename in excluded:
                continue
            npyname = None
            img_path = os.path.join(image_dir, filename)
            if npy_dir_path:
                npy_path = img_path.replace(dir_path, npy_dir_path)
                npy_path = os.path.splitext(npy_path)[0] + '.npy'
                if os.path.isfile(npy_path):
                    npyname = npy_path
            faces.append(
                FileStem(
                    img_path if npyname is None else npyname,
                    None,
                    np.array([float(i) for i in coords]).reshape(-1, 2),
                    None,
                    None,
                    0,
                    'unknown',
                    np.asarray((-1., -1.),
                    np.asarray(()))
                )
            )

            if count > 0 and len(faces) > offset + count:
                break

    if offset > 0:
        faces = faces[offset:]

    print('loaded data:', len(faces))

    return faces


def compute_rect_from_shape(shape):
    lt, rb = np.min(shape, axis=0), np.max(shape, axis=0)
    center = (lt + rb) * 0.5
    length = np.max(rb - lt)

    return np.asarray([center[0] - length / 2, center[1] - length / 2, length, length])



def rect_to_corner(rect):
    return np.asarray([rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]])


def get_transformed_shape(mat, shape):
    return np.dot(mat[0:2, 0:2], shape.transpose()).transpose() + mat[0:2, 2].transpose()


def point_distance(a, b):
    return np.mean(np.sqrt(np.sum(np.square(a - b), axis=min(1, len(a.shape) - 1))))


def get_eye_distance(points):
    return point_distance(np.mean(points[(5, 7), :], axis=0), np.mean(points[(20, 22), :], axis=0))


class WeightedLoss:
    def __init__(self,
                 batch_size,
                 with_iris,
                 with_occlude,
                 with_earlobe,
                 **kwargs):
        self.batch_size = batch_size
        self.weight_dict = kwargs
        self.detect_mouth_closed = kwargs.get('detect_mouth_closed', False)
        self.use_iris = with_iris
        self.occlude = with_occlude
        self.use_earlobe = with_earlobe
        self.num_output_landmarks = kwargs.get('num_predicted_landmarks', False)
        self.iris_weight = kwargs.get('iris_weight', 5.0)
        self.update_weight()

    def update_weight(self):
        num_components = self.num_output_landmarks + self.detect_mouth_closed + self.use_iris * 4 + self.occlude + self.use_earlobe * 3
        self.weights = np.ones((self.batch_size, num_components, 2))
        weights = self.weights.copy()
        init_norm = np.sum(weights[:, :num_components])
        if self.num_output_landmarks == 116:
            used_part_indexlists = part_indexlists_116
        elif self.num_output_landmarks == 100:
            used_part_indexlists = part_indexlists_100_for_adding_extra_contour_pts
        elif self.num_output_landmarks == 84:
            used_part_indexlists = part_indexlists
        for k, part_indices in used_part_indexlists.items():
            part_weight = self.weight_dict.get(k, 1.0)
            weights[:, part_indices, :] = part_weight
        for k in range(self.num_output_landmarks):
            curr_weight = self.weight_dict.get('landmark_%d' % k, -1.0)
            curr_weight_x = self.weight_dict.get('landmark_%d_x' % k, -1.0)
            curr_weight_y = self.weight_dict.get('landmark_%d_y' % k, -1.0)
            if curr_weight >= 0.:
                weights[:, k, :] = curr_weight
            if curr_weight_x >= 0.:
                weights[:, k, 0] = curr_weight_x
            if curr_weight_y >= 0.:
                weights[:, k, 1] = curr_weight_y
        if self.use_iris:
            for i in range(4):
                weights[:, self.num_output_landmarks + i, :] = self.iris_weight
        if self.occlude:
            weights[:, self.num_output_landmarks + self.use_iris * 4, :] = 1.0
        if self.use_earlobe:
            for i in range(2):
                weights[:, self.num_output_landmarks + 5 + i, :] = 1.0
            weights[:, self.num_output_landmarks + 5 + 2, :] = 1.0
        
        modified_norm = np.sum(weights[:, :num_components])
        self.no_weight = str(modified_norm) == str(init_norm)
        if self.no_weight is not True:
            weights[:, :num_components] *= (init_norm / modified_norm)
        self.weights = weights.reshape(self.batch_size, -1).astype(np.float32)

class Parabola:
    def to_level(self, p):
        return np.dot(p, self.rot_to_level)

    def to_src(self, p):
        return np.dot(p, self.rot_to_src)

    def get_y(self, x):
        return self.a * (x ** 2) + self.b * x + self.c

    def __init__(self, points_or_coeffs):
        if points_or_coeffs.shape == (3, 2):
            p = points_or_coeffs
            dy = p[2, 1] - p[0, 1]
            dx = p[2, 0] - p[0, 0]
            self.theta = 0. if dx == 0. and dy == 0. else math.atan2(dy, dx)
            level = Parabola(np.array([0, 0, 0, self.theta]))
            rot_p = level.to_level(p)
            self.a, self.b, self.c = np.dot(rot_p[:, 1],
                                            np.linalg.inv(np.vstack([rot_p[:, 0] ** 2,
                                                                    rot_p[:, 0] ** 1,
                                                                    rot_p[:, 0] ** 0])))
        elif points_or_coeffs.shape == (4,):
            self.a, self.b, self.c, self.theta = points_or_coeffs
        else:
            raise NotImplementedError('argument not in shape (4,) or (3, 2):', points_or_coeffs.shape)

        self.cos_theta = math.cos(self.theta)
        self.sin_theta = math.sin(self.theta)
        self.rot_to_level = np.array([[self.cos_theta, -self.sin_theta], [self.sin_theta, self.cos_theta]])
        self.rot_to_src = np.linalg.inv(self.rot_to_level)


def get_points_of_parabola(unit, start_p, end_p, parabola1, parabola2=None):
    ret_list = [start_p]

    rot_start_p1, rot_end_p1 = parabola1.to_level(start_p), parabola1.to_level(end_p)
    if not parabola2:
        step_count = int(np.ceil(max(abs(rot_end_p1[0] - rot_start_p1[0]), unit) / unit))
        step_unit_1 = (rot_end_p1[0] - rot_start_p1[0]) / step_count
    else:
        rot_start_p2, rot_end_p2 = parabola2.to_level(start_p), parabola2.to_level(end_p)
        step_count = int(np.ceil(max(abs(rot_end_p1[0] - rot_start_p1[0]), abs(rot_end_p2[0] - rot_start_p2[0]), unit) / unit))
        step_unit_1 = (rot_end_p1[0] - rot_start_p1[0]) / step_count
        step_unit_2 = (rot_end_p2[0] - rot_start_p2[0]) / step_count

    for step_i in range(1, step_count):
        x1 = rot_start_p1[0] + step_unit_1 * step_i
        p1 = parabola1.to_src(np.array([x1, parabola1.get_y(x1)]))
        if not parabola2:
            ret_list.append(p1)
        else:
            x2 = rot_start_p2[0] + step_unit_2 * step_i
            p2 = parabola2.to_src(np.array([x2, parabola2.get_y(x2)]))
            ret_list.append((p1 + p2) / 2.)

    return ret_list


def get_parabola_line_points(sample_unit, anchor_points):
    ret_points = []

    prev_parabola = None
    for anchor_i, anchor_point in enumerate(anchor_points):
        if anchor_i == len(anchor_points) - 1:
            ret_points.append(anchor_point)
            break

        curr_parabola = None
        if anchor_i < len(anchor_points) - 2:
            curr_parabola = Parabola(anchor_points[anchor_i:anchor_i+3])
        else:
            curr_parabola = prev_parabola
            prev_parabola = None

        assert curr_parabola or prev_parabola

        ret_points.extend(get_points_of_parabola(sample_unit, anchor_point, anchor_points[anchor_i + 1], curr_parabola, prev_parabola))
        prev_parabola = curr_parabola

    return ret_points

def SaveImageHWC(image, save_path):
    # Assume the data format is Height x Width x Channel.
    
    channels = image.shape[2]
    if (channels >= 3):
        image_3ch = image[:, :, 0:3].copy()
    else:
        image_3ch = image[:, :, 0:1].repeat(3, axis=2)

    cv.imwrite(save_path, image)

def create_landmark_plotted_img(img, landmarks, landmark_gt, earlobe, earlobe_gt, radius, deep_copy=True):
    if len(img.shape) == 2:
        show_img = skimage.color.gray2rgb(img)
    else:
        show_img = copy.deepcopy(img) if deep_copy else img
    for index in range(len(landmark_gt)):
       cv.circle(show_img, (int(landmark_gt[index, 0]), int(landmark_gt[index, 1])), int(radius*1.2), (0, 0, 255), -1)
       cv.circle(show_img, (int(landmarks[index, 0]), int(landmarks[index, 1])), int(radius), (0, 255, 0), -1)
    # for index in range(2):
    #     cv.circle(show_img, (int(earlobe[index, 0]), int(earlobe[index, 1])), int(radius * 1.2) , (0, 255, 0), -1)
    #     cv.circle(show_img, (int(earlobe_gt[index, 0]), int(earlobe_gt[index, 1])), int(radius) , (0, 0, 255), -1)

    return show_img






def generate_extra_contour(landmark, insert_point=1,sample_unit=1):

    split_part = insert_point + 1 
    left_contour_landmark = landmark[65:74]
    left_ear_top_idx = 0
    left_ear_bottom_index = 2
    left_center_idx = left_contour_landmark.shape[0] - 1
    right_contour_landmark = landmark[73:82]
    right_ear_top_idx = right_contour_landmark.shape[0] -1 
    right_ear_bottom_index = right_ear_top_idx - 2
    right_center_idx =  0
    
    # draw left face paraboline
    contour_landmarks= []
    left_keypoint_index = [left_ear_top_idx, left_ear_bottom_index, left_center_idx]
    for idx in range(len(left_keypoint_index)-1):
        extra_contour_idx = []
        cur_key_idx = left_keypoint_index[idx]
        next_key_idx = left_keypoint_index[idx+1]
        contourline = np.asarray(get_parabola_line_points(sample_unit,left_contour_landmark[cur_key_idx:next_key_idx+1]))
        
        left_ori_pts_idx = []
        for pt in left_contour_landmark[cur_key_idx:next_key_idx+1]:
            for idx in range(len(contourline)):
                if np.equal(pt,contourline[idx]).all():
                    left_ori_pts_idx.append(idx)
                    break
        
        if idx==0:
            # ear region
            
            for ori_idx in range(len(left_ori_pts_idx)-1):
                cur_idx = left_ori_pts_idx[ori_idx]
                next_idx = left_ori_pts_idx[ori_idx + 1] 
                tmp_extra_contour_idx = [cur_idx + (next_idx - cur_idx) * i // split_part for i in range(1,split_part)]
                extra_contour_idx += tmp_extra_contour_idx

        else:
            for ori_idx in range(len(left_ori_pts_idx)-1):
                cur_idx = left_ori_pts_idx[ori_idx]
                next_idx = left_ori_pts_idx[ori_idx + 1] 
                tmp_extra_contour_idx = [cur_idx + (next_idx - cur_idx) * i // split_part for i in range(1,split_part)]
                extra_contour_idx += tmp_extra_contour_idx
        
        if extra_contour_idx is not None:
            contour_pts = contourline[extra_contour_idx]
            contour_landmarks.append(contour_pts)

    right_keypoint_index = [right_center_idx, right_ear_bottom_index, right_ear_top_idx]
    
    for idx in range(len(right_keypoint_index)-1):
        extra_contour_idx = []

        cur_key_idx = right_keypoint_index[idx]
        next_key_idx = right_keypoint_index[idx+1]
        contourline = np.asarray(get_parabola_line_points(sample_unit,right_contour_landmark[cur_key_idx:next_key_idx+1]))
        
        right_ori_pts_idx = []
        for pt in right_contour_landmark[cur_key_idx:next_key_idx+1]:
            for idx in range(len(contourline)):
                if np.equal(pt,contourline[idx]).all():
                    right_ori_pts_idx.append(idx)
                    break
        
        if idx!=0:

            for ori_idx in range(len(right_ori_pts_idx)-1):
                cur_idx = right_ori_pts_idx[ori_idx]
                next_idx = right_ori_pts_idx[ori_idx + 1] 
                tmp_extra_contour_idx = [cur_idx + (next_idx - cur_idx) * i // (split_part) for i in range(1,split_part)]
                extra_contour_idx += tmp_extra_contour_idx

        else:
            for ori_idx in range(len(right_ori_pts_idx)-1):
                cur_idx = right_ori_pts_idx[ori_idx]
                next_idx = right_ori_pts_idx[ori_idx + 1]
                tmp_extra_contour_idx = [cur_idx + (next_idx - cur_idx) * i // (split_part) for i in range(1,split_part)]
                extra_contour_idx += tmp_extra_contour_idx

        if extra_contour_idx is not None:
            contour_pts = contourline[extra_contour_idx]
            contour_landmarks.append(contour_pts)
        
    extra_contour_landmark = np.vstack(contour_landmarks)
    return extra_contour_landmark