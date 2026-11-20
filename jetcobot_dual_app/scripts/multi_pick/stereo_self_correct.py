"""Per-capture right-camera rotation correction from the scene itself (2026-11-20).

Why: after a return to see_center the right camera ends up 1-2 deg away from
where joints + hand-eye + the board correction say (measured 20 px epipolar
error on 2026-11-20 11:50 while joints matched within 1 deg). The board
correction cannot follow that, so pairing failed.

How: SIFT matches over the whole frozen stereo pair, RANSAC fundamental-matrix
inliers (independent of robot pose), then a small rotation of the right camera
about its own centre (3 parameters, robust Gauss-Newton) that minimises the
epipolar distance. The left camera stays the anchor. Offline on the failed
captures: 19.7 px -> 0.4 px median, fix 1.6 deg; good captures 4.5 -> 0.4 px.

Thresholds relaxed 2026-11-20 afternoon: 50 matches were rejected at 60.
3 unknowns need far fewer; the residual/not-better checks still guard.

Safety: the result is used only if enough inliers, small rotation and a low
residual; otherwise the input poses are returned unchanged.
"""
import cv2
import numpy as np


def _matches(left_bgr, right_bgr, K, dist):
    gl = cv2.cvtColor(left_bgr, cv2.COLOR_BGR2GRAY)
    gr = cv2.cvtColor(right_bgr, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(8000, contrastThreshold=0.02)
    kl, dl = sift.detectAndCompute(gl, None)
    kr, dr = sift.detectAndCompute(gr, None)
    if dl is None or dr is None or len(kl) < 20 or len(kr) < 20:
        return None, None
    good = [a for a, b in cv2.BFMatcher().knnMatch(dl, dr, 2) if a.distance < .8 * b.distance]
    if len(good) < 20:
        return None, None
    pl = np.float32([kl[m.queryIdx].pt for m in good])
    pr = np.float32([kr[m.trainIdx].pt for m in good])
    _, inl = cv2.findFundamentalMat(pl, pr, cv2.FM_RANSAC, 1.5, .999)
    if inl is None:
        return None, None
    inl = inl.ravel().astype(bool)
    ul = cv2.undistortPoints(pl[inl].reshape(-1, 1, 2), K, dist).reshape(-1, 2)
    ur = cv2.undistortPoints(pr[inl].reshape(-1, 1, 2), K, dist).reshape(-1, 2)
    return ul.astype(np.float64), ur.astype(np.float64)


def _rotated(T, w):
    out = T.copy()
    out[:3, :3] = T[:3, :3] @ cv2.Rodrigues(np.asarray(w, float).reshape(3, 1))[0]
    return out


def _epipolar_px(TL, TR, ul, ur, f):
    Rrl = TR[:3, :3].T @ TL[:3, :3]
    t = TR[:3, :3].T @ (TL[:3, 3] - TR[:3, 3])
    E = np.array([[0, -t[2], t[1]], [t[2], 0, -t[0]], [-t[1], t[0], 0]]) @ Rrl
    lines = np.c_[ul, np.ones(len(ul))] @ E.T
    return f * np.sum(lines * np.c_[ur, np.ones(len(ur))], 1) / np.hypot(lines[:, 0], lines[:, 1])


def correct_right_rotation(T_wc, frames, K, dist, min_points=25, max_fix_deg=5.0,
                           max_residual_px=2.0):
    """Return (poses, info). poses = [left unchanged, right rotated] or the input."""
    TL, TR = np.asarray(T_wc[0], float), np.asarray(T_wc[1], float)
    info = {'applied': False}
    ul, ur = _matches(frames[0], frames[1], K, dist)
    if ul is None or len(ul) < min_points:
        info['reason'] = f'only {0 if ul is None else len(ul)} scene matches'
        return T_wc, info
    f = float(K[0, 0])
    w = np.zeros(3)
    for _ in range(15):
        r = _epipolar_px(TL, _rotated(TR, w), ul, ur, f)
        weight = 1.0 / np.maximum(1.0, np.abs(r) / 3.0)  # Huber, 3 px
        J = np.stack([(_epipolar_px(TL, _rotated(TR, w + d), ul, ur, f) - r) / 1e-6
                      for d in np.eye(3) * 1e-6], 1)
        step = np.linalg.solve(J.T @ (J * weight[:, None]) + 1e-9 * np.eye(3), J.T @ (weight * r))
        w = w - step
        if np.linalg.norm(step) < 1e-7:
            break
    before = float(np.median(np.abs(_epipolar_px(TL, TR, ul, ur, f))))
    after = float(np.median(np.abs(_epipolar_px(TL, _rotated(TR, w), ul, ur, f))))
    fix = float(np.degrees(np.linalg.norm(w)))
    info.update({'points': int(len(ul)), 'median_before_px': before, 'median_after_px': after,
                 'rotation_fix_deg': fix, 'rotation_fix_vec_deg': np.degrees(w).round(3).tolist()})
    if fix > max_fix_deg or after > max_residual_px or after > before:
        info['reason'] = 'fix rejected (too large or not better)'
        return T_wc, info
    info['applied'] = True
    return [TL, _rotated(TR, w)], info
