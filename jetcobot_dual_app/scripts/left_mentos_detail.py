#!/usr/bin/env python3
"""Left JetCobot camera: two Mentos tubes automatic alignment preview.

Uses only the UDP 5000 video stream. No ROS, MoveIt, SDK, robot commands,
manual ROI, or black/transparent overlay panels.

Normal: geometric center (X) and top (Y) of magenta upper band.
Upside down: fallback yellow wrapper bounding box ONLY (no BEST judgment).

Reference coordinates are estimated from the user's FIRST BEST screenshot
(640x480 video portion), not from a camera calibration. Test on the live view.
"""

import argparse
import json
from collections import deque
from pathlib import Path
import time

import cv2
import numpy as np

VIEW_W = 640
VIEW_H = 480
PRESET_BEST_X = 320.0
PRESET_BEST_Y = 330.0  # top of magenta wrap, not top of silver cap
PRESET_X_TOL = 30.0  # BEST +/- 30 px
PRESET_Y_TOL_UP = 40.0    # BEST minus 40 px
PRESET_Y_TOL_DOWN = 40.0  # BEST plus 40 px; total 80 px
SAVE_PATH = Path(__file__).with_name('left_mentos_best.json')


class LeftCamera:
    """GStreamer decoder preserved from the uploaded dual_camera_view(1).py."""
    def __init__(self, port):
        import gi
        gi.require_version('Gst', '1.0')
        gi.require_version('GstVideo', '1.0')
        from gi.repository import Gst, GstVideo
        self.Gst, self.GstVideo = Gst, GstVideo
        self.port = port
        self.frame = None
        self.received = None
        self.pipeline = Gst.parse_launch(
            f'udpsrc port={port} reuse=false '
            'caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! '
            'rtpjitterbuffer latency=30 drop-on-latency=true ! '
            'rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! '
            'video/x-raw,format=BGR ! '
            'appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true'
        )
        self.sink = self.pipeline.get_by_name('sink')
        self.bus = self.pipeline.get_bus()

    def start(self):
        if self.pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f'Cannot start LEFT UDP {self.port}')

    def poll(self):
        Gst = self.Gst
        msg = self.bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg is not None:
            if msg.type == Gst.MessageType.ERROR:
                err, detail = msg.parse_error()
                raise RuntimeError(f'LEFT UDP {self.port}: {err}; {detail}')
            raise RuntimeError('LEFT camera stream ended')
        sample = self.sink.emit('try-pull-sample', 0)
        if sample is None:
            return False
        info = self.GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buf = sample.get_buffer()
        ok, mapped = buf.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            self.frame = np.ndarray(
                (info.height, info.width, 3), dtype=np.uint8,
                buffer=mapped.data, offset=info.offset[0],
                strides=(info.stride[0], 3, 1)
            ).copy()
        finally:
            buf.unmap(mapped)
        self.received = time.monotonic()
        return True

    def close(self):
        self.pipeline.set_state(self.Gst.State.NULL)


def image_to_view(frame):
    h, w = frame.shape[:2]
    scale = min(VIEW_W / w, VIEW_H / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(frame, (nw, nh))
    view = np.zeros((VIEW_H, VIEW_W, 3), dtype=np.uint8)
    x, y = (VIEW_W - nw) // 2, (VIEW_H - nh) // 2
    view[y:y+nh, x:x+nw] = resized
    return view


class MentosDetector:
    def __init__(self):
        # Upper magenta sleeves, chosen from the provided Mentos photos.
        self.lower = np.array((148, 70, 40), np.uint8)
        self.upper = np.array((179, 255, 255), np.uint8)
        self.kernel = np.ones((5, 5), np.uint8)
        # Yellow upper section when the two Mentos tubes are upside down.
        self.yellow_lower = np.array((15, 65, 65), np.uint8)
        self.yellow_upper = np.array((40, 255, 255), np.uint8)

    def detect_yellow(self, hsv):
        """Find the two large yellow wrapper regions; return their union bbox.

        This is a coarse presence/bounding-box detector, NOT the same Y anchor
        as the magenta top. It must never trigger the normal BEST/OK logic.
        """
        yellow = cv2.inRange(hsv, self.yellow_lower, self.yellow_upper)
        yellow = cv2.morphologyEx(yellow, cv2.MORPH_CLOSE,
                                  np.ones((7, 7), np.uint8))
        yellow = cv2.morphologyEx(yellow, cv2.MORPH_OPEN,
                                  np.ones((3, 3), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(yellow, 8)
        # For partial views, each tube can be cut off by the bottom edge.
        candidates = []
        for i in range(1, n):
            x, y, w, h, area = map(int, stats[i])
            if area >= 450 and w >= 26 and h >= 9 and y >= int(VIEW_H * .32):
                candidates.append((x, y, w, h, area))
        candidates.sort(key=lambda a: a[4], reverse=True)
        candidates = candidates[:2]
        if not candidates or sum(c[4] for c in candidates) < 1200:
            return None, yellow
        # Reject a single yellow speck and extremely wide background patches.
        x1 = min(c[0] for c in candidates)
        y1 = min(c[1] for c in candidates)
        x2 = max(c[0] + c[2] for c in candidates)
        y2 = max(c[1] + c[3] for c in candidates)
        if (x2 - x1) < 90 or (x2 - x1) > VIEW_W * .88:
            return None, yellow
        return {'kind': 'yellow', 'bbox': (x1, y1, x2 - x1, y2 - y1),
                'x': (x1 + x2) / 2, 'y': float(y1)}, yellow

    def detect(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lower, self.upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                np.ones((3, 3), np.uint8))
        num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        if num > 1:
            idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            x, y, w, h, area = map(int, stats[idx])
            if area >= 1800 and w >= 130 and h >= 23 and y >= 4:
                top_end = min(y + 65, y + h)
                yy, xx = np.where(labels[y:top_end, x:x+w] == idx)
                if len(xx) >= 350:
                    xs = xx.astype(np.float32) + x
                    left_edge = float(np.percentile(xs, 2))
                    right_edge = float(np.percentile(xs, 98))
                    if right_edge - left_edge >= 115:
                        return {
                            'kind': 'magenta',
                            'x': (left_edge + right_edge) / 2.0,
                            'y': float(y),
                            'left': left_edge,
                            'right': right_edge,
                            'bbox': (x, y, w, h),
                            'area': area,
                        }, mask
        # Pink band absent: try upside-down yellow section instead.
        return self.detect_yellow(hsv)


class StableCoords:
    def __init__(self, count=7, jitter_limit=9):
        self.x = deque(maxlen=count)
        self.y = deque(maxlen=count)
        self.count = count
        self.jitter_limit = jitter_limit

    def update(self, detection):
        if detection is None:
            self.x.clear(); self.y.clear()
            return None
        self.x.append(detection['x']); self.y.append(detection['y'])
        if len(self.x) != self.count:
            return None
        if np.std(self.x) > self.jitter_limit or np.std(self.y) > self.jitter_limit:
            return None
        return float(np.median(self.x)), float(np.median(self.y))

    def reset(self):
        self.x.clear(); self.y.clear()


def put_status(img, text, color):
    # Tiny text outline for legibility, without an image-occluding panel.
    cv2.putText(img, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (255,255,255), 3, cv2.LINE_AA)
    cv2.putText(img, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                .65, color, 2, cv2.LINE_AA)


def draw(frame, target, det, stable, tol_x, tol_y_up, tol_y_down, show_mask=False, mask=None):
    view = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) if show_mask else frame.copy()
    bx, by = round(target[0]), round(target[1])
    # Magenta: allowable coordinate range, NOT the detection bounding box.
    cv2.rectangle(view,
                  (round(bx-tol_x), round(by-tol_y_up)),
                  (round(bx+tol_x), round(by+tol_y_down)),
                  (225, 50, 230), 2, cv2.LINE_AA)
    # Green: BEST cross. Short and unobtrusive.
    cv2.line(view, (bx-12, by), (bx+12, by), (0, 230, 50), 3, cv2.LINE_AA)
    cv2.line(view, (bx, by-12), (bx, by+12), (0, 230, 50), 3, cv2.LINE_AA)
    if det is None:
        put_status(view, 'NO OBJECT', (0, 110, 255))
        return view

    # Cyan bbox: HSV-selected magenta band OR upside-down yellow wrapper.
    # The two modes use DIFFERENT position anchors.
    xx, yy, ww, hh = det['bbox']
    cv2.rectangle(view, (xx, yy),
                  (min(VIEW_W - 1, xx + ww - 1), min(VIEW_H - 1, yy + hh - 1)),
                  (255, 220, 0), 2, cv2.LINE_AA)
    tag_y = max(16, yy - 7)
    cv2.putText(view, 'YELLOW' if det['kind'] == 'yellow' else 'DETECTED',
                (max(2, xx), tag_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 220, 0), 2, cv2.LINE_AA)

    if det['kind'] == 'magenta':
        px, py = round(det['x']), round(det['y'])
        cv2.circle(view, (px, py), 6, (0, 230, 255), -1, cv2.LINE_AA)
        cv2.circle(view, (px, py), 7, (0, 0, 0), 1, cv2.LINE_AA)

    if det['kind'] == 'yellow':
        # Yellow section has a different Y reference than magenta.
        # Show detection box only; NEVER claim position is within BEST.
        put_status(view, 'YELLOW DETECTED', (0, 200, 255))
    elif stable is None:
        put_status(view, 'CHECKING', (0, 200, 255))
    else:
        dx, dy = stable[0] - target[0], stable[1] - target[1]
        okx = abs(dx) <= tol_x
        oky = -tol_y_up <= dy <= tol_y_down
        status = ('OK' if okx and oky else
                  'X OUT' if not okx and oky else
                  'Y OUT' if okx and not oky else 'X/Y OUT')
        put_status(view, status,
                   (20, 190, 10) if okx and oky else (0, 110, 255))
    return view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--x-tol', type=float, default=PRESET_X_TOL,
                        help='Allowed X error in pixels (default 30)')
    parser.add_argument('--y-tol-up', type=float, default=PRESET_Y_TOL_UP,
                        help='Allow this many pixels ABOVE BEST (default 40)')
    parser.add_argument('--y-tol-down', type=float, default=PRESET_Y_TOL_DOWN,
                        help='Allow this many pixels BELOW BEST (default 40)')
    parser.add_argument('--best-x', type=float, default=PRESET_BEST_X)
    parser.add_argument('--best-y', type=float, default=PRESET_BEST_Y)
    parser.add_argument('--load-saved-target', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be in 1..65535')
    if not (0 < args.x_tol < 200 and 0 < args.y_tol_up < 150 and 0 < args.y_tol_down < 150):
        parser.error('x-tol, y-tol-up and y-tol-down must be positive')

    target = (args.best_x, args.best_y)
    if args.load_saved_target and SAVE_PATH.exists():
        try:
            saved = json.loads(SAVE_PATH.read_text(encoding='utf-8'))
            if saved.get('method') == 'mentos_magenta_top_v1':
                target = (float(saved['x']), float(saved['y']))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    camera = LeftCamera(args.port)
    detector = MentosDetector()
    stability = StableCoords()
    show_mask = False
    camera.start()
    try:
        while True:
            camera.poll()
            if camera.received is None or time.monotonic()-camera.received > 1.0:
                view = np.zeros((VIEW_H, VIEW_W, 3), np.uint8)
                put_status(view, 'WAITING FOR LEFT CAMERA', (0, 170, 255))
                stable = None
            else:
                frame = image_to_view(camera.frame)
                det, mask = detector.detect(frame)
                stable = stability.update(det if det and det['kind'] == 'magenta' else None)
                view = draw(frame, target, det, stable,
                            args.x_tol, args.y_tol_up, args.y_tol_down, show_mask, mask)
            cv2.imshow('LEFT - Mentos', view)
            key = cv2.waitKey(1) & 0xff
            if key in (ord('q'), 27):
                break
            elif key == ord('m'):
                show_mask = not show_mask
            elif key == ord('r'):
                target = (PRESET_BEST_X, PRESET_BEST_Y)
                print('BEST reset:', target)
            elif key == ord('t'):
                if stable is None:
                    print('Cannot set BEST: object not stably detected')
                else:
                    target = stable
                    SAVE_PATH.write_text(json.dumps({
                        'method': 'mentos_magenta_top_v1',
                        'x': target[0], 'y': target[1]}, indent=2), encoding='utf-8')
                    print('BEST set:', target)
            time.sleep(.005)
    finally:
        camera.close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        raise SystemExit(f'ERROR: {e}')

