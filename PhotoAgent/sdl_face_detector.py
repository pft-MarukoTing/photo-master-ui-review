"""RetinaFace-style ONNX face detector ("sdl" model), detection-only.

Ported from the ONNXFaceProcessingPipeline demo's FaceDetector (landmark/pose/
recognition stages dropped -- PhotoAgent only needs boxes) and adapted to take
an in-memory BGR array instead of a file path, since PhotoAgent never writes
the uploaded photo to disk.
"""

from itertools import product
from math import ceil

import cv2
import numpy as np
import onnxruntime as ort

_IMG_DIM = 640
_RGB_MEAN = (127, 127, 127)
_PRIOR_CFG = {
    "min_sizes": [[32, 48], [64, 96], [128, 192, 256, 320, 384, 448]],
    "steps": [16, 32, 64],
    "variance": [0.1, 0.2],
}


def _build_priors(img_dim):
    feature_maps = [[ceil(img_dim / step), ceil(img_dim / step)] for step in _PRIOR_CFG["steps"]]
    anchors = []
    for k, f in enumerate(feature_maps):
        for i, j in product(range(f[0]), range(f[1])):
            for min_size in _PRIOR_CFG["min_sizes"][k]:
                s_kx = min_size / img_dim
                s_ky = min_size / img_dim
                cx = (j + 0.5) * _PRIOR_CFG["steps"][k] / img_dim
                cy = (i + 0.5) * _PRIOR_CFG["steps"][k] / img_dim
                anchors += [cx, cy, s_kx, s_ky]
    return np.array(anchors).reshape(-1, 4)


def _preprocess(bgr_image, img_dim, rgb_mean):
    original_shape = bgr_image.shape[:2]
    resize_ratio = float(img_dim) / float(max(original_shape))

    h, w = original_shape
    if h > w:
        new_h, new_w = img_dim, int(round(w * img_dim / h))
    else:
        new_h, new_w = int(round(h * img_dim / w)), img_dim
    resized = cv2.resize(bgr_image, (new_w, new_h), interpolation=cv2.INTER_AREA).astype(np.float32)

    long_side = max(new_w, new_h)
    padded = np.empty((long_side, long_side, 3), dtype=resized.dtype)
    padded[:, :] = rgb_mean
    padded[0:new_h, 0:new_w] = resized

    tensor = (padded - rgb_mean).transpose(2, 0, 1)[np.newaxis, :, :, :].astype(np.float32)
    scale = np.array([long_side, long_side, long_side, long_side], dtype=np.float32)
    return tensor, resize_ratio, original_shape, scale


def _decode_boxes(loc, priors, variances):
    boxes = np.concatenate((
        priors[:, :2] + loc[:, :2] * variances[0] * priors[:, 2:],
        priors[:, 2:] * np.exp(loc[:, 2:] * variances[1])), 1)
    boxes[:, :2] -= boxes[:, 2:] / 2
    boxes[:, 2:] += boxes[:, :2]
    return boxes


def _nms(dets, thresh):
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]

    return keep


class SDLFaceDetector:
    def __init__(self, model_path, intra_op_num_threads=None):
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if intra_op_num_threads:
            sess_options.intra_op_num_threads = intra_op_num_threads

        self.session = ort.InferenceSession(model_path, sess_options=sess_options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.priors = _build_priors(_IMG_DIM)

    def detect(self, bgr_image, confidence_threshold=0.739, nms_threshold=0.4):
        """Detect faces in a BGR image array.

        Returns a list of {"bbox": [x1, y1, x2, y2], "confidence": score},
        boxes already clipped to the input image's own pixel bounds.
        """
        tensor, resize_ratio, original_shape, scale = _preprocess(bgr_image, _IMG_DIM, _RGB_MEAN)
        outputs = self.session.run(self.output_names, {self.input_name: tensor})

        conf, loc = [], []
        for output in outputs:
            if output.ndim == 3:
                conf.append(output)
            else:
                loc.append(np.transpose(output, (0, 2, 3, 1)).reshape((1, -1, 4)))
        if not conf or not loc:
            return []

        loc = np.concatenate(loc, axis=1)[0]
        confidence = np.concatenate(conf, axis=1)[0]

        boxes = _decode_boxes(loc, self.priors, _PRIOR_CFG["variance"])
        boxes = boxes * scale / resize_ratio
        scores = confidence[:, 1]

        inds = np.where(scores > confidence_threshold)[0]
        if len(inds) == 0:
            return []
        boxes, scores = boxes[inds], scores[inds]

        order = scores.argsort()[::-1]
        boxes, scores = boxes[order], scores[order]

        dets = np.hstack((boxes, scores[:, np.newaxis])).astype(np.float32, copy=False)
        keep = _nms(dets, nms_threshold)
        dets = dets[keep, :]

        height, width = original_shape
        results = []
        for x1, y1, x2, y2, score in dets:
            results.append({
                "bbox": [
                    max(0, min(int(x1), width)),
                    max(0, min(int(y1), height)),
                    max(0, min(int(x2), width)),
                    max(0, min(int(y2), height)),
                ],
                "confidence": float(score),
            })
        return results
