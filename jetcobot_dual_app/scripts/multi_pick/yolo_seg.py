"""YOLO-seg instance masks in the same form as the ROI + SAM path (2026-11-24).

Each detection gives an ROI box (mask bounds + padding) and a full-image uint8
0/1 mask that is zero outside that box, exactly like segment_with_sam().
"""
import cv2
import numpy as np


class YoloSegmenter:
    def __init__(self, weights, conf=0.5, classes=None, device=None, pad_px=6, max_objects=12):
        from ultralytics import YOLO
        import torch
        self.model = YOLO(str(weights))
        self.names = {int(k): v for k, v in self.model.names.items()}
        unknown = set(classes or ()) - set(self.names.values())
        if unknown:
            raise ValueError(f'Unknown YOLO classes {sorted(unknown)}; model has {sorted(self.names.values())}')
        self.class_ids = (None if not classes else
                          [k for k, v in self.names.items() if v in classes])
        self.conf = conf
        self.device = device if device is not None else (0 if torch.cuda.is_available() else 'cpu')
        self.pad = pad_px
        self.max_objects = max_objects

    def detect(self, image_bgr, exclude=None, paint=None):
        """exclude: uint8 mask (1 = detected table ArUco marker) painted out before
        YOLO and removed from masks; detections mostly on it are dropped.
        paint: extra area painted out only (markers projected from the layout, which
        may lie partly under an object, so object masks are not cut there)."""
        h, w = image_bgr.shape[:2]
        painted = exclude if paint is None else (paint if exclude is None else (exclude | paint))
        if painted is not None and painted.any():
            image_bgr = image_bgr.copy()
            ring = cv2.dilate(painted, np.ones((9, 9), np.uint8)) & ~painted.astype(bool)
            fill = np.median(image_bgr[ring.astype(bool)], axis=0) if ring.any() else (200, 200, 200)
            image_bgr[painted.astype(bool)] = fill
        result = self.model.predict(image_bgr, conf=self.conf, classes=self.class_ids,
                                    device=self.device, retina_masks=True, verbose=False)[0]
        if result.masks is None:
            return []
        found = []
        for poly, cls, score in zip(result.masks.xy, result.boxes.cls.tolist(),
                                    result.boxes.conf.tolist()):
            if len(poly) < 3:
                continue
            mask = np.zeros((h, w), np.uint8)
            cv2.fillPoly(mask, [np.round(poly).astype(np.int32)], 1)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            if count < 2:
                continue
            mask = (labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
            if int(mask.sum()) < 70:
                continue
            if exclude is not None and (mask & exclude).sum() > 0.3 * mask.sum():
                continue
            if exclude is not None:
                mask = (mask & (1 - exclude)).astype(np.uint8)
            ys, xs = np.nonzero(mask)
            roi = [max(0, int(xs.min()) - self.pad), max(0, int(ys.min()) - self.pad),
                   min(w, int(xs.max()) + 1 + self.pad), min(h, int(ys.max()) + 1 + self.pad)]
            if min(roi[2] - roi[0], roi[3] - roi[1]) < 16:
                continue
            found.append({'roi': roi, 'mask': mask, 'class_name': self.names[int(cls)],
                          'score': float(score)})
        found.sort(key=lambda d: -d['score'])
        kept = []
        for det in found:  # validate_rois() rejects boxes with IoU > 0.8
            if all(_iou(det['roi'], k['roi']) <= 0.8 for k in kept):
                kept.append(det)
        return kept[:self.max_objects]


def _iou(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union if union else 0.0


def marker_exclusion(image_bgr, pad_px=6):
    """uint8 mask of every visible DICT_6X6_50 marker (with its white border)."""
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_50))
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY))
    out = np.zeros(image_bgr.shape[:2], np.uint8)
    for quad in corners:
        quad = quad.reshape(4, 2)
        center = quad.mean(axis=0)
        grown = center + (quad - center) * 1.6  # include the white margin
        cv2.fillConvexPoly(out, np.round(grown).astype(np.int32), 1)
    if pad_px:
        out = cv2.dilate(out, np.ones((pad_px, pad_px), np.uint8))
    return out
