from scipy.io import loadmat
import numpy as np
import cv2
import pathlib
from PIL import Image
import os
import configparser
from .util import *
import random

def mkl_to_afw(landmarks):
    index = {
        'contour': np.arange(65, 82),
        'left_eyebrow': np.arange(0, 5),
        'right_eyebrow': np.arange(15, 20),
        'nose_bridge': np.arange(30, 34),
        'nose_bottom': np.array([37, 34, 35, 36, 38]),
        'left_eye': np.array([5, 11, 12, 9, 13, 14]),
        'right_eye': np.array([24, 26, 27, 22, 28, 29]),
        'mouth_out': np.array([39, 44, 40, 41, 42, 45, 43, 56, 55, 50, 54, 53]),
        'mouth_in': np.array([57, 60, 58, 61, 59, 64, 62, 63])
    }
    all_indices = []
    order = [
        'contour', 
        'left_eyebrow', 
        'right_eyebrow', 
        'nose_bridge', 
        'nose_bottom', 
        'left_eye', 
        'right_eye', 
        'mouth_out', 
        'mouth_in'
    ]

    for key in order:
        all_indices.extend(index[key])
    all_indices = np.array(all_indices)

    return landmarks[all_indices,:]



def load_lm3d(pth=None):
    if pth is None:
        pth = os.path.join(os.path.dirname(__file__), "similarity_Lm3D_all.mat")
        
    Lm3D = loadmat(pth)
    Lm3D = Lm3D['lm']

    # calculate 5 facial landmarks using 68 landmarks
    lm_idx = np.array([31, 37, 40, 43, 46, 49, 55]) - 1
    Lm3D = np.stack([Lm3D[lm_idx[0], :], np.mean(Lm3D[lm_idx[[1, 2]], :], 0), np.mean(Lm3D[lm_idx[[3, 4]], :], 0),
                     Lm3D[lm_idx[5], :], Lm3D[lm_idx[6], :]], axis=0)
    Lm3D = Lm3D[[1, 2, 0, 3, 4], :]

    return Lm3D

def set_lm3d(pth):
    global lm3D
    lm3D = load_lm3d(pth)


def POS(xp, x):
    npts = xp.shape[0]
    if npts == 68:
        lm_idx = np.array([31, 37, 40, 43, 46, 49, 55]) - 1
        xp = np.stack([xp[lm_idx[0], :], np.mean(xp[lm_idx[[1, 2]], :], 0), np.mean(xp[lm_idx[[3, 4]], :], 0),
                       xp[lm_idx[5], :], xp[lm_idx[6], :]], axis=0)
        xp = xp[[1, 2, 0, 3, 4], :]
        npts = 5
    A = np.zeros([2 * npts, 8])
    x = np.concatenate((x, np.ones((npts, 1))), axis=1)
    A[0:2 * npts - 1:2, 0:4] = x

    A[1:2 * npts:2, 4:] = x
    b = np.reshape(xp, [-1, 1])

    k, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    custom_k = least_squares(A,b)


    R1 = k[0:3]
    R2 = k[4:7]
    

    sTx = k[3]
    sTy = k[7]

    # print(f"R1_norm:{np.linalg.norm(R1)} R2_norm:{np.linalg.norm(R2)}")
    s = (np.linalg.norm(R1) + np.linalg.norm(R2)) / 2
    t = np.stack([sTx, sTy], axis=0)

    return t, s




def process_img(img, fg_mask, occ_mask, t, s, target_size=256, ffhq_shift = 0):
    h0, w0 = img.shape[:2]
    scale = 116. / s
    if target_size == 128:
        scale = 58. / s
    if target_size == 192:
        scale = 87. / s
    dx = -(t[0, 0] * scale - target_size / 2)
    dy = -((h0 - t[1, 0]) * scale - target_size / 2)
    mat = np.array([[scale, 0, dx-ffhq_shift],
                    [0, scale, dy]])

    # process segmentation mask,
    # the cv2.warpAffine have bug for nearest sampling, the edges are inaccurate,
    # so we use the implementation of Image
    mat_extend = np.array([[scale, 0, dx],
                           [0, scale, dy],
                           [0, 0, 1]])
    
    seg_affine = None
    if fg_mask is not None:
        coeff = np.linalg.inv(mat_extend).flatten()[:6]
        seg = Image.fromarray(fg_mask)
        seg_affine = seg.transform((target_size, target_size), Image.AFFINE, coeff, resample=Image.NEAREST)
        seg_affine = np.array(seg_affine)

    occ_seg_affine = None
    if occ_mask is not None:
        occ_seg = Image.fromarray(occ_mask)
        occ_seg_affine = occ_seg.transform((target_size, target_size), Image.AFFINE, coeff, resample=Image.NEAREST)
        occ_seg_affine = np.array(occ_seg_affine)



    corners = np.array([[0, 0, 1], [w0 - 1, h0 - 1, 1]])
    new_corners = (corners @ mat.T).astype('int32')
    pad_left = max(new_corners[0, 0], 0)
    pad_top = max(new_corners[0, 1], 0)
    pad_right = min(new_corners[1, 0], target_size - 1)
    pad_bottom = min(new_corners[1, 1], target_size - 1)
    mask = np.zeros((target_size, target_size, 3))
    mask[:pad_top, :, :] = 1
    mask[pad_bottom:, :, :] = 1
    mask[:, :pad_left, :] = 1
    mask[:, pad_right:, :] = 1
    img_affine = cv2.warpAffine(img, mat, (target_size, target_size) )
    img_affine = img_affine.astype('float32') * (1 - mask) + cv2.blur(img_affine, (10, 10)) * mask
    img_affine = img_affine.astype('uint8')

    return img_affine, seg_affine, occ_seg_affine, mat



def show_ldmk(img, lm):
    for pt in lm:
        cv2.circle(img, (int(pt[0]), int(pt[1])), 2, (255, 0, 0), 1)
    return img


def Preprocess(img, fg_mask, occ_mask, lm, input_size):
    h0, w0 = img.shape[:2]
    lm_ = np.stack([lm[:, 0], h0 - 1 - lm[:, 1]], axis=1)
    t, s = POS(lm_, lm3D)

    img_new, seg_new, occ_seg_new, mat = process_img(img, fg_mask, occ_mask, t, s, target_size=input_size)
    lm_affine = np.concatenate((lm[:, :2], np.ones((lm.shape[0], 1))), axis=1)
    lm_affine = lm_affine @ mat.T
    return img_new, seg_new, occ_seg_new, lm_affine, mat



def GetAugmentMatrix(mat, scale,rotate_angle,translate,input_size):
    
    augment_scale = random.uniform(scale[0], scale[1])
    augment_angle = random.uniform(-rotate_angle, rotate_angle)

    augment_translate_x =  random.uniform(-translate, translate) * input_size
    augment_translate_y =  random.uniform(-translate, translate) * input_size

    aug_scale_mat = np.array([
        [augment_scale, 0, 0],
        [0, augment_scale, 0],
        [0, 0, 1]
    ])

    angle_rad = np.deg2rad(augment_angle)  # 将角度转换为弧度
    cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
    aug_rotate_mat = np.array([
        [cos_a, -sin_a, 0],
        [sin_a, cos_a, 0],
        [0, 0, 1]
    ])

    aug_translate_mat = np.array([
        [1, 0, augment_translate_x],
        [0, 1, augment_translate_y],
        [0, 0, 1]
    ])
    augmented_mat = aug_translate_mat @ aug_rotate_mat @ aug_scale_mat
    return augmented_mat


def RandomAlignTransform(img, lm, input_size, aug_scale, aug_rotate, aug_translate):
    h0, w0 = img.shape[:2]
    lm_ = np.stack([lm[:, 0], h0 - 1 - lm[:, 1]], axis=1)
    t, s = POS(lm_, lm3D)

    if input_size ==256:
        scale = 116. / s
    if input_size == 128:
        scale = 58. / s
    if input_size == 192:
        scale = 87. / s
    dx = -(t[0, 0] * scale - input_size / 2)
    dy = -((h0 - t[1, 0]) * scale - input_size / 2)


    augment_scale = random.uniform(aug_scale[0], aug_scale[1])
    augment_angle = random.uniform(-aug_rotate, aug_rotate)

    augment_translate_x =  random.uniform(-aug_translate, aug_translate) * input_size
    augment_translate_y =  random.uniform(-aug_translate, aug_translate) * input_size
    angle_rad = np.deg2rad(augment_angle)  # 将角度转换为弧度
    cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)

    aug_mat = np.array([[scale*augment_scale*cos_a, -sin_a * scale* augment_scale, dx+augment_translate_x],
                    [scale*augment_scale * sin_a, scale*augment_scale*cos_a, dy+augment_translate_y],
                    [0,  0,    1]])
    final_mat = aug_mat[:2, :]

    lm_affine = np.concatenate((lm[:, :2], np.ones((lm.shape[0], 1))), axis=1)
    lm_affine = lm_affine @ final_mat.T
    
    img_affine = cv2.warpAffine(img, final_mat, (input_size, input_size), cv.INTER_AREA)
    return img_affine, lm_affine, final_mat

def AlignTransform(img, lm, input_size):
    h0, w0 = img.shape[:2]
    lm_ = np.stack([lm[:, 0], h0 - 1 - lm[:, 1]], axis=1)
    t, s = POS(lm_, lm3D)

    img_new, seg_new, _, mat = process_img(img, None, None, t, s, target_size=input_size)
    lm_affine = np.concatenate((lm[:, :2], np.ones((lm.shape[0], 1))), axis=1)
    lm_affine = lm_affine @ mat.T
    return img_new, seg_new, lm_affine, mat

def GetAlignTransform(img, lm, input_size):
    h0, w0 = img.shape[:2]
    lm_ = np.stack([lm[:, 0], h0 - 1 - lm[:, 1]], axis=1)
    t, s = POS(lm_, lm3D)

    if input_size ==256:
        scale = 116. / s
    if input_size == 128:
        scale = 58. / s
    if input_size == 192:
        scale = 87. / s
    dx = -(t[0, 0] * scale - input_size / 2)
    dy = -((h0 - t[1, 0]) * scale - input_size / 2)
    mat = np.array([[scale, 0, dx],
                    [0, scale, dy],
                    [0,  0,    1]])
    return mat


def GetRandomAlignTransform(img, lm, input_size, aug_scale, aug_rotate, aug_translate):
    h0, w0 = img.shape[:2]
    lm_ = np.stack([lm[:, 0], h0 - 1 - lm[:, 1]], axis=1)
    t, s = POS(lm_, lm3D)

    if input_size ==256:
        scale = 116. / s
    if input_size == 128:
        scale = 58. / s
    if input_size == 192:
        scale = 87. / s
    dx = -(t[0, 0] * scale - input_size / 2)
    dy = -((h0 - t[1, 0]) * scale - input_size / 2)


    augment_scale = random.uniform(aug_scale[0], aug_scale[1])
    augment_angle = random.uniform(-aug_rotate, aug_rotate)

    augment_translate_x =  random.uniform(-aug_translate, aug_translate) * input_size
    augment_translate_y =  random.uniform(-aug_translate, aug_translate) * input_size
    angle_rad = np.deg2rad(augment_angle)  # 将角度转换为弧度
    cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)

    aug_mat = np.array([[scale*augment_scale*cos_a, -sin_a * scale* augment_scale, dx+augment_translate_x],
                    [scale*augment_scale * sin_a, scale*augment_scale*cos_a, dy+augment_translate_y],
                    [0,  0,    1]])
    return aug_mat


lm3D = load_lm3d()



def least_squares(A, b):
  
    At = np.transpose(A)
    
    AtA = np.dot(At, A)
    
    Atb = np.dot(At, b)
    
    AtA_inv = np.linalg.inv(AtA)
    
    x = np.dot(AtA_inv, Atb)

    res = (np.dot(AtA_inv,At))
    
    vec_b = b
    res = (np.dot(res,vec_b))
    return res


def read_landmarks(config_, name):
    ret_list = []
    config_.clear()
    config_.read(name)
    is_tagged_ini = config_.has_option('Landmark', 'Landmark_84')
    for j in range(84):
        coorstr = config_.get('Landmark', 'Landmark_' + str(j)).split()
        ret_list.append(np.asarray([float(k) for k in coorstr]))
    return ret_list


def detect_landmark(face_model,align_model,src_img):
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
    landmarks = None
    if len(ret_info['rect']) >= 4:
        ret_info['align'] = align_face(align_model, ret_info['rect'], src_img, (256,256))
        ret_info['rect'] = compute_rect_from_shape(ret_info['align'])
        landmarks =  ret_info['align'][:84]
    
    return landmarks
   

# def getFaceAlign(src_img):
#     landmarks = detect_landmark(model,align_model,src_img)
#     return landmarks
# if __name__ == '__main__':
    
    

    
    # img_path = '/data2/cody/PFSkinMap/testdata/626C0F75-30CC-4E08-8335-8D6E60535C93.jpeg'
    # # img_path = "/data2/cody/SkinCareData/God_Processed_Image/Testset_TearTrough/Asia/01536.png"
    # img = cv2.imread(img_path)
    
    # print("img shape:",img.shape)
    # landmarks = detect_landmark(model,align_model,img)
    
    # if landmarks is not None:
    #     afw_landmarks = mkl_to_afw(landmarks)

    #     input_img, ldmk, mat = Preprocess(img,afw_landmarks)
    #     inverse_mat = cv2.invertAffineTransform(mat)
    #     restored_image = cv.warpAffine(input_img, inverse_mat, (img.shape[1], img.shape[0]))
    #     cv2.imwrite("input_256.png",input_img)



