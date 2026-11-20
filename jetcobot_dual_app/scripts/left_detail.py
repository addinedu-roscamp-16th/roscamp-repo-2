#!/usr/bin/env python3
"""LEFT camera: automatic chocolate-wrapper topmost-edge detection.

Perception-only OpenCV viewer: NO robot, ROS, or SDK commands.
Third reference image is the preset BEST position.
Pink rectangle: allowed region for detected (center_x, topmost_y).
Green crosshair: horizontal BEST top edge Y and vertical BEST center X.
Keys: T=update BEST from stable observation; R=reset to preset BEST;
      M=toggle mask, Q/Esc=quit.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import time

import cv2
import numpy as np


VIEW_W, VIEW_H = 640, 480
# Chocolate fine-alignment target in the 640x480 camera image.
PRESET_BEST_X = 320.0
PRESET_BEST_TOP_Y = 125.0
DEFAULT_X_TOL = 65.0  # Accepted X range: 255..385 px.
DEFAULT_Y_UP = 70.0     # Top edge may be as high as y=55.
DEFAULT_Y_DOWN = 60.0   # Top edge may be as low as y=185.
REFERENCE_METHOD = 'connected_component_topmost_v2'



class LeftCamera:
    """Keep the RTP/GStreamer pipeline from the uploaded dual-camera viewer."""

    def __init__(self, port):
        import gi
        gi.require_version('Gst', '1.0')
        gi.require_version('GstVideo', '1.0')
        from gi.repository import Gst, GstVideo
        self.Gst, self.GstVideo = Gst, GstVideo
        self.port = port
        self.frame = None
        self.received = None
        self.count = 0
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
            raise RuntimeError(f'LEFT receiver failed on UDP {self.port}')

    def poll(self):
        Gst = self.Gst
        message = self.bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message:
            if message.type == Gst.MessageType.ERROR:
                error, detail = message.parse_error()
                raise RuntimeError(f'LEFT UDP {self.port}: {error}; {detail}')
            raise RuntimeError('LEFT video stream ended')
        sample = self.sink.emit('try-pull-sample', 0)
        if sample is None:
            return False
        info = self.GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            self.frame = np.ndarray(
                (info.height, info.width, 3), dtype=np.uint8,
                buffer=mapped.data, offset=info.offset[0],
                strides=(info.stride[0], 3, 1)
            ).copy()
        finally:
            buffer.unmap(mapped)
        self.count += 1
        self.received = time.monotonic()
        if self.count == 1:
            print(f'LEFT: video received UDP {self.port}, {info.width}x{info.height}', flush=True)
        return True

    def close(self):
        self.pipeline.set_state(self.Gst.State.NULL)


def image_to_view(frame):
    """Match the 640x480 presentation of the original viewer (letterboxed)."""
    h, w = frame.shape[:2]
    scale = min(VIEW_W / w, VIEW_H / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    img = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    view = np.zeros((VIEW_H, VIEW_W, 3), dtype=np.uint8)
    ox, oy = (VIEW_W - new_w) // 2, (VIEW_H - new_h) // 2
    view[oy:oy + new_h, ox:ox + new_w] = img
    return view


class WrapperDetector:
    def __init__(self, hmin=15, hmax=40, smin=43, vmin=60, min_area=10000):
        self.lower = np.array([hmin, smin, vmin], dtype=np.uint8)
        self.upper = np.array([hmax, 255, 255], dtype=np.uint8)
        self.min_area = min_area
        self.close_kernel = np.ones((9, 9), np.uint8)
        self.open_kernel = np.ones((5, 5), np.uint8)

    def detect(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lower, self.upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.close_kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.open_kernel)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n <= 1:
            return None, mask

        # Largest connected yellow region: generally the visible candy wrapper.
        idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y, w, h, area = map(int, stats[idx])
        if area < self.min_area or w < 90 or h < 55:
            return None, mask

        # Measure the actual upper edge in the central 50% of the wrapper.
        # This avoids unreliable outer corners and does NOT depend on a drawn ROI.
        x0 = int(x + w * 0.25)
        x1 = int(x + w * 0.75)
        points = []
        for xx in range(x0, x1):
            yy = np.flatnonzero(labels[:, xx] == idx)
            if len(yy) > 0:
                points.append((xx, int(yy[0])))
        if len(points) < max(20, int(0.8 * (x1 - x0))):
            return None, mask

        top_array = np.array([p[1] for p in points], dtype=np.float32)
        # IMPORTANT: use the very top pixel of the selected yellow component,
        # NOT the center-column median. Noise was already reduced by morphology
        # and the largest connected-component constraint.
        # A large wrapper touching the top of the image has its true top edge
        # outside the frame. Treat the visible edge as y=0 so fine alignment
        # moves it down toward BEST, then measures the edge again.
        top_clipped = y <= 3
        top_y = 0.0 if top_clipped else float(y)
        first_row_xs = np.flatnonzero(labels[y, x:x + w] == idx)
        top_x = float(x + np.mean(first_row_xs)) if len(first_row_xs) else float(x + w / 2)
        # Reject severely fragmented masks.
        edge_spread = float(np.percentile(top_array, 90) - np.percentile(top_array, 10))
        if edge_spread > 50:
            return None, mask

        center_x = float(x + w / 2.0)
        return {
            'x': center_x,
            'y': top_y,
            'top_x': top_x,
            'box': (x, y, w, h),
            'edge': points,
            'area': area,
            'edge_spread': edge_spread,
            'top_clipped': top_clipped,
        }, mask


class TemporalFilter:
    def __init__(self, size=9, max_std=12.0):
        self.xs = deque(maxlen=size)
        self.ys = deque(maxlen=size)
        self.size = size
        self.max_std = max_std

    def reset(self):
        self.xs.clear()
        self.ys.clear()

    def update(self, detection):
        if detection is None:
            self.reset()
            return None
        self.xs.append(detection['x'])
        self.ys.append(detection['y'])
        if len(self.ys) < self.size:
            return None
        # While the robot / camera is moving, the ROI-free detection can still
        # show the raw edge. Do not mark it STABLE until it settles.
        if np.std(self.ys) > self.max_std or np.std(self.xs) > self.max_std:
            return None
        return float(np.median(self.xs)), float(np.median(self.ys))


def load_target(path):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('method') != REFERENCE_METHOD:
            print('Old reference uses a different Y definition; using preset BEST.', flush=True)
            return None
        if data.get('width') != VIEW_W or data.get('height') != VIEW_H:
            print('Saved reference ignored: frame size differs.', flush=True)
            return None
        return {'x': float(data['x']), 'y': float(data['y'])}
    except (ValueError, KeyError, OSError, TypeError) as e:
        print(f'Reference file ignored: {e}', flush=True)
        return None


def save_target(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'width': VIEW_W, 'height': VIEW_H,
        'method': REFERENCE_METHOD,
        'x': round(target['x'], 3), 'y': round(target['y'], 3)
    }, indent=2), encoding='utf-8')


def put_line(img, text, y, color=(255, 255, 255), scale=0.55):
    cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, 1, cv2.LINE_AA)


def tolerance_status(stable, target, x_tol, y_up, y_down):
    if stable is None:
        return 'UNSTABLE', None, None
    dx = stable[0] - target['x']
    dy = stable[1] - target['y']
    ok_x = abs(dx) <= x_tol
    ok_y = -y_up <= dy <= y_down
    return ('OK' if ok_x and ok_y else 'OUT OF RANGE'), ok_x, ok_y


def draw_view(bgr, detection, stable, target, mask, show_mask,
              x_tol, y_up, y_down, target_source):
    """Minimal, image-first viewer. All coordinates are in 640x480 video pixels.

    Pink rectangle = EXACT accepted range for the detected (center_x, topmost_y),
    not an approximate bounding box around the entire candy wrapper.
    Green horizontal line = BEST Y; green vertical line = BEST X.
    Only a short status and BEST label are drawn; no opaque HUD.
    """
    canvas = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) if show_mask else bgr.copy()
    tx, ty = int(round(target['x'])), int(round(target['y']))
    xl = max(0, int(round(target['x'] - x_tol)))
    xr = min(VIEW_W - 1, int(round(target['x'] + x_tol)))
    yt = max(0, int(round(target['y'] - y_up)))
    yb = min(VIEW_H - 1, int(round(target['y'] + y_down)))

    PINK = (195, 60, 240)   # BGR: allowable range
    GREEN = (45, 220, 50)   # BGR: BEST reference
    YELLOW = (0, 235, 255) # BGR: observed top boundary

    # Thin transparent pink fill + clearly visible 3px boundary.
    # Exact tolerances are unchanged: +/-X and asymmetric -Y/+Y.
    region = canvas.copy()
    cv2.rectangle(region, (xl, yt), (xr, yb), PINK, -1)
    canvas = cv2.addWeighted(region, 0.055, canvas, 0.945, 0)
    cv2.rectangle(canvas, (xl, yt), (xr, yb), PINK, 3, cv2.LINE_AA)

    # Clearly visible green crosshair for BOTH independent BEST references.
    # Horizontal = best Y (top edge); vertical = best X (wrapper center).
    half_len_x = 165
    bx0, bx1 = max(0, tx - half_len_x), min(VIEW_W - 1, tx + half_len_x)
    by0, by1 = max(0, ty - 95), min(VIEW_H - 1, ty + 175)
    cv2.line(canvas, (bx0, ty), (bx1, ty), GREEN, 4, cv2.LINE_AA)
    cv2.line(canvas, (tx, by0), (tx, by1), GREEN, 3, cv2.LINE_AA)
    cv2.circle(canvas, (tx, ty), 5, GREEN, -1, cv2.LINE_AA)

    # BEST label near the right end, without a large text/opaque panel.
    label_x = bx1 + 9 if bx1 + 80 < VIEW_W else max(6, bx1 - 73)
    label_y = ty - 10 if ty > 26 else ty + 28
    cv2.putText(canvas, 'BEST', (label_x, label_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(canvas, 'BEST', (label_x, label_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREEN, 2, cv2.LINE_AA)

    # Observed outline (yellow): keep it thin so the candy remains visible.
    # The single dot shows (center_x, topmost_y) used in the tolerance check.
    if detection is not None:
        edge = np.asarray(detection['edge'], dtype=np.int32)
        if len(edge) >= 2:
            cv2.polylines(canvas, [edge.reshape((-1, 1, 2))], False,
                          YELLOW, 2, cv2.LINE_AA)
        cx, cy = int(round(detection['x'])), int(round(detection['y']))
        cv2.circle(canvas, (cx, cy), 6, (0, 0, 0), -1, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), 4, YELLOW, -1, cv2.LINE_AA)

    # Small text only: no black rectangular background and no coordinates.
    status, ok_x, ok_y = tolerance_status(stable, target, x_tol, y_up, y_down)
    if detection is None:
        short = 'NO DETECTION'
        color = (0, 165, 255)
    elif stable is None:
        short = 'CHECKING'
        color = (0, 195, 255)
    elif status == 'OK':
        short = 'OK'
        color = GREEN
    else:
        if not ok_x and not ok_y:
            short = 'X / Y OUT'
        elif not ok_x:
            short = 'X OUT'
        else:
            short = 'Y OUT'
        color = (0, 165, 255)
    cv2.putText(canvas, short, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.76, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(canvas, short, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.76, color, 2, cv2.LINE_AA)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--left-port', type=int, default=5000)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--duration', type=float, default=0)
    parser.add_argument('--target-file', type=Path, default=Path('left_chocobar_target.json'))
    parser.add_argument('--load-saved-target', action='store_true',
                        help='Use BEST saved with T on a previous run. Default: third image BEST for X and Y.')
    parser.add_argument('--target-x', type=float, default=None, help='Override saved X pixel reference')
    parser.add_argument('--target-y', type=float, default=None, help='Override saved Y pixel reference')
    parser.add_argument('--hmin', type=int, default=15)
    parser.add_argument('--hmax', type=int, default=40)
    parser.add_argument('--smin', type=int, default=43)
    parser.add_argument('--vmin', type=int, default=60)
    parser.add_argument('--min-area', type=int, default=10000)
    parser.add_argument('--x-tol', type=float, default=DEFAULT_X_TOL, help='Allowed +/- X pixels')
    parser.add_argument('--y-up', type=float, default=DEFAULT_Y_UP, help='Allowed pixels above BEST top Y')
    parser.add_argument('--y-down', type=float, default=DEFAULT_Y_DOWN, help='Allowed pixels below BEST top Y')
    args = parser.parse_args()
    if not (1 <= args.left_port <= 65535):
        parser.error('left-port must be 1..65535')
    if not np.isfinite(args.duration) or args.duration < 0:
        parser.error('duration must be finite and >=0')
    if not (0 <= args.hmin <= args.hmax <= 179 and 0 <= args.smin <= 255 and 0 <= args.vmin <= 255):
        parser.error('invalid HSV thresholds')
    if (not all(np.isfinite(v) and v >= 0 for v in (args.x_tol, args.y_up, args.y_down))):
        parser.error('all tolerances must be finite and nonnegative')
    if args.target_x is not None and not (0 <= args.target_x < VIEW_W):
        parser.error('target-x must be 0..639')
    if args.target_y is not None and not (0 <= args.target_y < VIEW_H):
        parser.error('target-y must be 0..479')

    # Always begin at the user's third (BEST) screenshot, unless explicitly
    # opting into a previously saved T target. Avoid silent old JSON overrides.
    saved = load_target(args.target_file) if args.load_saved_target else None
    target = dict(saved) if saved is not None else {'x': PRESET_BEST_X, 'y': PRESET_BEST_TOP_Y}
    target_source = 'SAVED T' if saved is not None else 'PRESET 3RD IMAGE' 
    if args.target_x is not None:
        target['x'] = float(args.target_x)
        target_source = 'CLI'
    if args.target_y is not None:
        target['y'] = float(args.target_y)
        target_source = 'CLI'

    detector = WrapperDetector(args.hmin, args.hmax, args.smin, args.vmin, args.min_area)
    temporal = TemporalFilter()
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
    Gst.init(None)
    camera = LeftCamera(args.left_port)
    start = time.monotonic()
    last_log = 0.0
    detection, stable = None, None
    mask = np.zeros((VIEW_H, VIEW_W), dtype=np.uint8)
    show_mask = False
    try:
        camera.start()
        print(f'LEFT only. BEST X={target["x"]:.1f} Y={target["y"]:.1f} ({target_source})', flush=True)
        print('Green vertical = X BEST; horizontal = Y BEST; pink = XY limits.', flush=True)
        print('Keys: T=save BEST, R=reset to third image, M=mask, Q=quit; No robot control.', flush=True)
        while args.duration == 0 or time.monotonic() - start < args.duration:
            new_frame = camera.poll()
            now = time.monotonic()
            live = camera.received is not None and now - camera.received < 1.0
            if new_frame:
                frame = image_to_view(camera.frame)
                detection, mask = detector.detect(frame)
                stable = temporal.update(detection)
            elif not live:
                detection, stable = None, None
                temporal.reset()
                frame = np.zeros((VIEW_H, VIEW_W, 3), dtype=np.uint8)
                mask = np.zeros((VIEW_H, VIEW_W), dtype=np.uint8)
            else:
                frame = image_to_view(camera.frame)

            if now - last_log > 1.0:
                if stable is None:
                    print('LEFT: no stable target', flush=True)
                else:
                    x, y = stable
                    errors = '' if target is None else (
                        f" dx={x-target['x']:+.1f}px dy={y-target['y']:+.1f}px")
                    status, _, _ = tolerance_status(stable, target, args.x_tol, args.y_up, args.y_down)
                    print(f'LEFT: x={x:.1f}px topmost_y={y:.1f}px{errors} STATUS={status}', flush=True)
                last_log = now

            if not args.headless:
                vis = draw_view(frame, detection, stable, target, mask, show_mask,
                                args.x_tol, args.y_up, args.y_down, target_source)
                if not live:
                    put_line(vis, 'NO VIDEO', 58, (0, 140, 255))
                cv2.imshow('JetCobot LEFT - chocobar auto detection', vis)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), 27):
                    break
                elif key == ord('m'):
                    show_mask = not show_mask
                elif key == ord('t'):
                    if stable is None:
                        print('Cannot set target: detection is not STABLE.', flush=True)
                    else:
                        target = {'x': stable[0], 'y': stable[1]}
                        target_source = 'SAVED T'
                        save_target(args.target_file, target)
                        print(f"Saved best-pose reference: x={target['x']:.1f}, "
                              f"y={target['y']:.1f} -> {args.target_file}", flush=True)
                elif key == ord('r'):
                    target = {'x': PRESET_BEST_X, 'y': PRESET_BEST_TOP_Y}
                    target_source = 'PRESET 3RD IMAGE'
                    if args.target_file.exists():
                        args.target_file.unlink()
                    print('Reset BEST to preset X=320, Y=125.', flush=True)
            time.sleep(0.005)
    finally:
        camera.close()
        if not args.headless:
            cv2.destroyAllWindows()
        print(f'LEFT: {camera.count} frames decoded', flush=True)
    if args.duration and camera.count == 0:
        raise RuntimeError('No frames: verify LEFT UDP port and sender IP')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, ImportError) as exc:
        raise SystemExit(f'ERROR: {exc}')

