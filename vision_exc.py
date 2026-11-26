import time
import cv2
import numpy as np
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

# =====================================
# 1. 최상단 바구니 인식 파라미터
# =====================================
ROI_NORM = (0.03, 0.56, 0.05, 0.96)

CANNY_LOW = 25
CANNY_HIGH = 85

# 바구니 비율 및 면적 필터 (0개 방지용으로 넉넉하게 설정)
EXPECTED_ASPECT = 1.05
ASPECT_TOL = 0.65             # 약 0.37 ~ 1.73 허용
MIN_AREA_RATIO = 0.03
MAX_AREA_RATIO = 0.95

# 박스가 세로로 너무 길게(2단처럼) 잡혔을 때 최상단 1개 높이로 축소할 기준 비율
TALL_BOX_HEIGHT_RATIO = 0.55  # ROI 높이의 55%보다 크면 2단으로 보고 상단 절반만 사용

SMOOTH_ALPHA = 0.35

smoothed_rect = None
last_log_time = 0.0


def smooth_box(prev, curr, alpha):
    if curr is None:
        return prev
    if prev is None:
        return tuple(float(v) for v in curr)
    return tuple((1.0 - alpha) * prev[i] + alpha * float(curr[i]) for i in range(4))


def detect_topmost_bin(frame, roi_norm, c_low, c_high):
    h_img, w_img = frame.shape[:2]
    rx1, rx2, ry1, ry2 = roi_norm
    x1, x2 = int(w_img * rx1), int(w_img * rx2)
    y1, y2 = int(h_img * ry1), int(h_img * ry2)

    roi = frame[y1:y2, x1:x2]
    roi_h, roi_w = roi.shape[:2]
    roi_area = max(1, roi_h * roi_w)

    # 1) 엣지 추출 (2단 경계가 뭉치지 않도록 (3, 3) 약한 닫기 연산)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, c_low, c_high)

    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k_close)

    # ROI 외곽 경계 제거
    closed[0, :] = 0
    closed[-1, :] = 0
    closed[:, 0] = 0
    closed[:, -1] = 0

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = roi_area * MIN_AREA_RATIO
    max_area = roi_area * MAX_AREA_RATIO
    min_ratio = EXPECTED_ASPECT * (1.0 - ASPECT_TOL)
    max_ratio = EXPECTED_ASPECT * (1.0 + ASPECT_TOL)

    valid_boxes = []
    rejected_boxes = []

    for cnt in contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw < 35 or bh < 35:
            continue

        # 세로로 너무 긴 덩어리(2단 전체로 잡힌 경우)는 최상단 바구니 영역(상단 50%)으로 변환
        if bh > roi_h * TALL_BOX_HEIGHT_RATIO:
            bh = max(35, int(bh * 0.5))

        area = bw * bh
        area_ratio = area / float(roi_area)
        aspect = bw / float(bh)
        rect_global = (x1 + bx, y1 + by, x1 + bx + bw, y1 + by + bh)

        in_area = (min_area <= area <= max_area)
        in_aspect = (min_ratio <= aspect <= max_ratio)

        if in_area and in_aspect:
            valid_boxes.append({
                "rect": rect_global,
                "by": by,
                "bh": bh,
                "aspect": aspect,
                "area": area,
                "area_ratio": area_ratio,
            })
        else:
            rejected_boxes.append((rect_global, aspect, area_ratio))

    # 가장 위쪽(by가 가장 작은) 바구니 1개 선택
    valid_boxes.sort(key=lambda item: item["by"])
    top_box = valid_boxes[0] if valid_boxes else None
    raw_rect = top_box["rect"] if top_box is not None else None

    return (x1, y1, x2, y2), edges, closed, valid_boxes, rejected_boxes, raw_rect


Gst.init(None)
pipeline = Gst.parse_launch(
    'udpsrc port=5003 '
    'caps="application/x-rtp,media=video,clock-rate=90000,'
    'encoding-name=JPEG,payload=26" ! '
    'rtpjpegdepay ! jpegdec ! videoconvert ! '
    'video/x-raw,format=BGR ! '
    'appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true'
)
sink = pipeline.get_by_name('sink')
pipeline.set_state(Gst.State.PLAYING)

print("최상단 바구니 인식 테스트 | [[/]] Canny_Low | [q] 종료")

while True:
    sample = sink.emit('try-pull-sample', 2 * Gst.SECOND)
    if sample is None:
        continue

    buffer = sample.get_buffer()
    caps = sample.get_caps()
    structure = caps.get_structure(0)
    width = structure.get_value('width')
    height = structure.get_value('height')

    ok, map_info = buffer.map(Gst.MapFlags.READ)
    if not ok:
        continue
    try:
        frame = np.ndarray((height, width, 3), dtype=np.uint8, buffer=map_info.data).copy()
    finally:
        buffer.unmap(map_info)

    roi_rect, edges, closed, valid_boxes, rejected_boxes, raw_rect = detect_topmost_bin(
        frame, ROI_NORM, CANNY_LOW, CANNY_HIGH
    )
    smoothed_rect = smooth_box(smoothed_rect, raw_rect, SMOOTH_ALPHA)

    now = time.time()
    if now - last_log_time >= 1.0:
        last_log_time = now
        v_info = [(round(b["aspect"], 2), round(b["area_ratio"], 2), b["by"]) for b in valid_boxes]
        r_info = [(round(asp, 2), round(ar, 2)) for _, asp, ar in rejected_boxes[:4]]
        print(f"[Valid(asp, area_r, by)]={v_info} | [Rej(asp, area_r)]={r_info}")

    vis = frame.copy()
    rx1, ry1, rx2, ry2 = roi_rect
    cv2.rectangle(vis, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)

    for rej_rect, asp, _ in rejected_boxes:
        jx1, jy1, jx2, jy2 = rej_rect
        cv2.rectangle(vis, (jx1, jy1), (jx2, jy2), (120, 120, 120), 1)

    for idx, vb in enumerate(valid_boxes):
        vx1, vy1, vx2, vy2 = vb["rect"]
        cv2.rectangle(vis, (vx1, vy1), (vx2, vy2), (0, 255, 255), 1)
        cv2.putText(
            vis, f"#{idx} r={vb['aspect']:.2f}", (vx1 + 4, vy1 + 16),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1
        )

    if smoothed_rect is not None:
        sx1, sy1, sx2, sy2 = [int(round(v)) for v in smoothed_rect]
        cx, cy = (sx1 + sx2) // 2, (sy1 + sy2) // 2
        cv2.rectangle(vis, (sx1, sy1), (sx2, sy2), (0, 0, 255), 2)
        cv2.circle(vis, (cx, cy), 5, (255, 0, 0), -1)
        cv2.putText(
            vis, f"Top Target: ({cx}, {cy}) | Valid: {len(valid_boxes)}",
            (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2
        )
    else:
        cv2.putText(
            vis, f"No Valid Box | Valid: {len(valid_boxes)} | CannyLow={CANNY_LOW}",
            (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2
        )

    info = f"TOPMOST ONLY | Canny:{CANNY_LOW}/{CANNY_HIGH} | [[/]]Low [q]Quit"
    cv2.putText(vis, info, (15, vis.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

    cv2.imshow("Topmost Bin Test", vis)
    cv2.imshow("Closed Edges", closed)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord(']'):
        CANNY_LOW = min(200, CANNY_LOW + 5)
        print(f"[CANNY_LOW] -> {CANNY_LOW}")
    elif key == ord('['):
        CANNY_LOW = max(5, CANNY_LOW - 5)
        print(f"[CANNY_LOW] -> {CANNY_LOW}")

pipeline.set_state(Gst.State.NULL)
cv2.destroyAllWindows()
