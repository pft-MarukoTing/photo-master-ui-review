import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'  

import numpy as np
import cv2 as cv
from pathlib import Path
import math
import shutil

GROUP_THRESHOLD = .25
SCORE_THREHSOLD = .7
MAX_IMAGE_SIDE = 1024
DUMP_DETAIL = False

def iou(r1, r2):
    ir, ur = intersect_rect(r1, r2), union_rect(r1, r2)
    ira = ir[2] * ir[3]
    ura = ur[2] * ur[3]
    return ira / ura if ura > 0 else 0

def intersect_rect(r1, r2):
    return (
        max(r1[0], r2[0]),
        max(r1[1], r2[1]),
        max(min(r1[2] + r1[0], r2[2] + r2[0]) - max(r1[0], r2[0]), 0),
        max(min(r1[3] + r1[1], r2[3] + r2[1]) - max(r1[1], r2[1]), 0),
    )

def union_rect(r1, r2):
    return (
        min(r1[0], r2[0]),
        min(r1[1], r2[1]),
        max(r1[2] + r1[0], r2[2] + r2[0]) - min(r1[0], r2[0]),
        max(r1[3] + r1[1], r2[3] + r2[1]) - min(r1[1], r2[1]),
    )

def detect_face(model, src_img):
    anchor_list_dict = {
        32: [[-248, -248, 263, 263], [-120, -120, 135, 135]],
        16: [[-56, -56, 71, 71], [-24, -24, 39, 39]],
        8: [[-8, -8, 23, 23], [0, 0, 15, 15]],
    }

    img = src_img[np.newaxis, :src_img.shape[0] // 32 * 32, :src_img.shape[1] // 32 * 32, ::-1]
    tensors = model.predict(img)
    tensors = sorted(tensors, key=lambda s: (s.shape[1], s.shape[3]))
    result_tuple, best_detect = [], []
    face_count = 0
    for idx in range(0, len(tensors), len(tensors) // 3):
        _, h, w, c = tensors[idx].shape
        stride = img.shape[1] / h

        score = tensors[idx]
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






def get_transformed_shape(mat, shape):
    return np.dot(mat[0:2, 0:2], shape.transpose()).transpose() + mat[0:2, 2].transpose()


def point_distance(a, b):
    return np.mean(np.sqrt(np.sum(np.square(a - b), axis=min(1, len(a.shape) - 1))))


def compute_rect_from_shape(shape):
    lt, rb = np.min(shape, axis=0), np.max(shape, axis=0)
    center = (lt + rb) * 0.5
    length = np.max(rb - lt)

    return np.asarray([center[0] - length / 2, center[1] - length / 2, length, length])


def align_face(model, face_rect, img, infer_size=(256, 256)):
    face, transform, crop_img = align_preprocess(img, face_rect, infer_size)
    transform = np.linalg.inv(transform)
    face= face[np.newaxis, :]
    points = model.predict(face)
    points = points[0].reshape(-1, 2) * np.asarray(infer_size)
    points_on_original = get_transformed_shape(transform, points)
    return points_on_original

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
        
    crop_img = cv.warpAffine(img, transform_mat[:2, :3], (ow, oh))
    return (normalize(crop_img),
            np.vstack((transform_mat, np.asarray([0., 0., 1.]))),
            crop_img)



def filter_bigface(face_model,align_model,src_img,save_path):
    scale = 1
    if src_img.shape[0] > MAX_IMAGE_SIDE or src_img.shape[1] > MAX_IMAGE_SIDE:
        if src_img.shape[0] > src_img.shape[1]:
            scale = MAX_IMAGE_SIDE / src_img.shape[0]
        else:
            scale = MAX_IMAGE_SIDE / src_img.shape[1]
    
    target_shape = [round(src_img.shape[i] * scale) for i in reversed(range(2))]
    ori_img = src_img
    img = cv.resize(ori_img, tuple(target_shape), cv.INTER_AREA)
    ret_info = {'rect': (), 'face_count': None, 'all_rect': None, 'align': None, }
    ret_info['rect'], ret_info['face_count'], ret_info['all_rect'] = tuple(detect_face(face_model,img))

    ret_info['rect'] = [i / scale for i in ret_info['rect']]
    is_success = False
    if len(ret_info['rect']) >= 4:
        ret_info['align'] = align_face(align_model, ret_info['rect'], src_img, (256,256))
        ret_info['rect'] = compute_rect_from_shape(ret_info['align'])
        length = ret_info['rect'][2]
        
        landmarks =  ret_info['align'][:84]
        
        h,w,_ = src_img.shape
        bbox = (compute_rect_from_shape(landmarks)).astype(np.int32)
        nose_center = (landmarks[30] + landmarks[35]) / 2
        eye_center = (landmarks[5] + landmarks[22]) / 2
        center =( (eye_center * 2.0 + nose_center * 1.0) / 3.0).astype(np.int32)
        crop_width = int(bbox[2] * 1.5)
        crop_height = int(crop_width * 4 / 3) 
        sX = max(0,center[0] - crop_width //2)
        sY=  max(0,center[1] - crop_height //2)
        eX = min(w,center[0] + crop_width // 2)
        eY = min(h,center[1] + crop_height // 2)
        cv.imwrite(save_path,src_img[sY:eY,sX:eX,:])
        print("SaveImage:",save_path)
        is_success = True
    else:
        is_success = False
    return  is_success

def custom_loss_fn(y_true, y_pred, sample_weight=None):
        # ignore sample weight currently
        return K.mean(K.square(y_true - y_pred), axis=-1)


def cv_imread(filepath):
    cv_img = cv.imdecode(np.fromfile(filepath,dtype=np.uint8),cv.IMREAD_COLOR)
    return cv_img


if __name__== "__main__":
    filelist = list(Path( './data').glob('**/*.jpg'))
    model = load_model('./facert_model.hdf5',compile=False)
    align_model = load_model('./baseline_84pts_220210.hdf5',custom_objects={'custom_loss_fn': custom_loss_fn, 'custom_loss_fn_168': custom_loss_fn},compile=False)

    for filepath in filelist:

        filepath = str(filepath)
        basename = os.path.basename(filepath)
        save_path = './filter_data'
        img = cv.imread(filepath)
        is_success = filter_bigface(model,align_model,img)
        if is_success:
            shutil.copy(filepath,os.path.join(save_path,basename))
