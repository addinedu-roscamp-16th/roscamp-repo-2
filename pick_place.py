import argparse
import csv
import json
import subprocess
import threading
import time
from datetime import datetime

import cv2
import numpy as np
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

# =====================================
# 0. 실행 인자 및 설정
# =====================================
ap = argparse.ArgumentParser()
ap.add_argument("--robot", required=True, help="SSH 대상 (예: jetcobot@192.168.6.1)")
ap.add_argument("--port-name", default="/dev/ttyUSB0", help="로봇 시리얼 포트")
ap.add_argument("--baud", type=int, default=1000000, help="로봇 baud rate")
ap.add_argument("--log-file", default="cycle_log.csv", help="로그 저장 파일")
args = ap.parse_args()

# 첫 실동작은 낮은 속도로. 검증 후 22 등으로 올리세요.
MOVE_SPEED = 20
SSH_CMD = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", args.robot, "python3", "-u", "-"]

# 사이클 옵션
ENABLE_FULL_TO_SHIP = True      # False면 출고 단계 생략하고 빈상자->AMR만 실행
MOVE_TOLERANCE_DEG = 6.0        # 도착 판정 허용 오차(도). 타임아웃이 자주 나면 8로 완화
MOVE_TIMEOUT_SEC = 12.0         # 이동 하나당 최대 대기 시간
CAMERA_SETTLE_SEC = 1.0         # 출고 후 팔이 시야에서 벗어나고 영상이 안정될 때까지 대기
CAMERA_WAIT_SEC = 3.0           # 그 뒤 최상단 상자를 찾을 최대 대기 시간 (못 찾으면 ERROR로 멈춤)

# [1] AMR(채워진 바구니) -> 출고위치 관절 각도
AMR_FULL_PICK = [16.43, -48.25, -85.69, 41.3, 4.83, -71.01]
ABOVE_AMR_FULL_PICK = [16.43, -10.89, -86.92, 14.06, 2.9, -70.75]
SHIP_PLACE = [106.69, -8.34, -120.05, 33.48, 6.94, 18.45]
ABOVE_SHIP_PLACE = [106.61, 52.82, -107.22, 30.93, 14.41, -0.43]

# [2] 빈상자 더미 (왼쪽 DUMMY_1_LEFT부터 시작 -> [n] 수동 전환 또는 max_picks 완료 시 DUMMY_2_RIGHT)
# roi_norm: (x1_ratio, x2_ratio, y1_ratio, y2_ratio)
# fixed_j6: 슬롯별 그리퍼 J6 고정값. None이면 above_pick의 J6를 그대로 사용
EMPTY_SLOTS = [
    {
        "name": "DUMMY_1_LEFT",
        "above_pick": [69.6, 27.15, 23.55, 29.44, -159.08, -7.29],
        "pick": [72.15, 48.6, 29.0, 7.64, -158.81, -7.99],
        "max_picks": 2,
        "roi_norm": (0.03, 0.54, 0.03, 0.96),
        "fixed_j6": -7.99,
    },
    {
        "name": "DUMMY_2_RIGHT",
        "above_pick": [46.05, 19.68, 50.18, 9.05, -159.25, -29.79],
        "pick": [54.49, 42.8, 50.71, -16.52, -158.64, 140.09],
        "max_picks": 2,
        "roi_norm": (0.48, 0.96, 0.03, 0.96),
        "fixed_j6": None,
    },
]

# [3] 빈바구니 -> AMR 적재 관절 각도
AMR_EMPTY_PLACE = [158.64, 47.63, 101.77, -63.19, -158.29, 66.88]
ABOVE_AMR_EMPTY_PLACE = [158.37, -15.99, 99.4, -29.7, -156.97, -7.99]

# 그리퍼 회전 방지용 J6 고정 사용 여부 (값은 슬롯별 fixed_j6)
LOCK_GRIPPER_J6 = True

# 비전 높이(target_y) 변화 보정 게인 및 안전 제한
Y_TO_J2_GAIN = -0.05
MAX_ABS_J2_OFFSET = 20.0

# 자동 실행 설정
AUTO_TRIGGER_ON_DETECTION = False
DETECT_CONFIRM_FRAMES = 4
AUTO_COOLDOWN_SEC = 3.0

# 미검출로 자동 슬롯 전환되는 현상 방지 (수동 [n] 또는 max_picks 달성 시 전환)
AUTO_SWITCH_ON_MISSING = False
MISSING_FRAMES_THRESHOLD = 9999

ROBOT_SINGLE_PNP_SCRIPT = r'''
import json, time
from pymycobot.mycobot280 import MyCobot280
P = json.loads(r"""__PARAMS__""")

def st(s):
    print("STATE:" + s, flush=True)

def clamp_joint_angles(angles):
    out = []
    for idx, val in enumerate(angles):
        v = float(val)
        if idx == 4:
            v = max(-155.0, min(160.0, v))
        else:
            v = max(-150.0, min(150.0, v))
        out.append(round(v, 2))
    return out

mc = MyCobot280(P["port"], P["baud"])
mc.thread_lock = True
time.sleep(0.5)

def get_angles_safe(retries=5):
    for _ in range(retries):
        cur = mc.get_angles()
        if isinstance(cur, list) and len(cur) == 6:
            return cur
        time.sleep(0.15)
    raise RuntimeError("get_angles failed: 통신 확인 (포트/baud/점유 프로세스)")

def ang_diff(a, b):
    d = (float(a) - float(b)) % 360.0
    return d - 360.0 if d > 180.0 else d

def reached(target_angles, tol):
    cur = get_angles_safe()
    return max(abs(ang_diff(cur[i], target_angles[i])) for i in range(6)) <= tol

def move_to(target_angles, speed, timeout=None, tol=None):
    timeout = P["timeout"] if timeout is None else timeout
    tol = P["tol"] if tol is None else tol
    safe_angles = clamp_joint_angles(target_angles)
    mc.send_angles(safe_angles, speed)
    time.sleep(0.4)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if reached(safe_angles, tol):
            return
        time.sleep(0.15)
    cur = get_angles_safe()
    raise RuntimeError("move_to timeout: target=%s current=%s" % (safe_angles, cur))

def grip(flag, speed=30):
    mc.set_gripper_state(flag, speed)
    time.sleep(1.0)

# 통신 확인 및 서보 활성화
get_angles_safe()
mc.power_on(); time.sleep(0.5)
mc.focus_all_servos(); time.sleep(0.3)
st("comm ok")

slow_speed = max(15, P["speed"] - 3)

st("open gripper");         grip(0, speed=35)
st("above pick");           move_to(P["above_pick"], P["speed"])
st("moving to pick");       move_to(P["pick"], slow_speed)
st("gripping");             grip(1, speed=30)
try:
    st("gripper value:%s" % mc.get_gripper_value())
except Exception:
    pass
st("lifting from pick");    move_to(P["above_pick"], P["speed"])
st("above place");          move_to(P["above_place"], P["speed"])
st("moving to place");      move_to(P["place"], slow_speed)
time.sleep(0.3)
st("releasing");            grip(0, speed=25)
time.sleep(0.3)
st("lifting after place");  move_to(P["above_place"], P["speed"])
st("done")
'''

ROBOT_STOP_SCRIPT = r'''
import json
from pymycobot.mycobot280 import MyCobot280
P = json.loads(r"""__PARAMS__""")
mc = MyCobot280(P["port"], P["baud"])
mc.thread_lock = True
mc.stop()
print("STATE:stopped", flush=True)
'''

# =====================================
# 1. 전역 상태 및 로그
# =====================================
is_busy = False
robot_state = "IDLE"
last_error = ""
ssh_proc = None

cycle_count = 0
active_slot_idx = 0
slot_pick_counts = [0 for _ in EMPTY_SLOTS]
slot_base_y = [None for _ in EMPTY_SLOTS]
slot_missing_frames = [0 for _ in EMPTY_SLOTS]
detected_streak = 0
last_cycle_done_time = 0.0
latest_detections = {}
detections_lock = threading.Lock()


def append_log(phase, slot_name, target_xy, j2_offset, duration_sec, status, note=""):
    file_exists = False
    try:
        with open(args.log_file, "r", encoding="utf-8"):
            file_exists = True
    except FileNotFoundError:
        file_exists = False

    with open(args.log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp", "cycle", "phase", "slot",
                "target_x", "target_y", "j2_offset", "duration_sec", "status", "note"
            ])
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            cycle_count + 1,
            phase,
            slot_name,
            target_xy[0] if target_xy else "",
            target_xy[1] if target_xy else "",
            j2_offset,
            round(duration_sec, 2),
            status,
            note,
        ])


def clamp(val, limit):
    return max(-limit, min(limit, val))


def build_script(template, **extra):
    params = {
        "port": args.port_name,
        "baud": args.baud,
        "timeout": MOVE_TIMEOUT_SEC,
        "tol": MOVE_TOLERANCE_DEG,
    }
    params.update(extra)
    return template.replace("__PARAMS__", json.dumps(params))


def detect_slots_in_frame(frame):
    h_img, w_img = frame.shape[:2]
    detections = {}
    vis_items = []

    for idx, slot in enumerate(EMPTY_SLOTS):
        rx1, rx2, ry1, ry2 = slot["roi_norm"]
        x1, x2 = int(w_img * rx1), int(w_img * rx2)
        y1, y2 = int(h_img * ry1), int(h_img * ry2)

        roi = frame[y1:y2, x1:x2]
        roi_h, roi_w = roi.shape[:2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 20, 80)

        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (18, 1))
        horiz_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, horiz_kernel)
        row_counts = np.sum(horiz_edges > 0, axis=1)

        line_ys = []
        for r_idx, cnt in enumerate(row_counts):
            if cnt >= int(roi_w * 0.16):
                if not line_ys or (r_idx - line_ys[-1]) > 20:
                    line_ys.append(r_idx)

        cols_with_edge = np.where(np.sum(edges > 0, axis=0) > int(roi_h * 0.14))[0]
        if len(cols_with_edge) >= 2:
            box_x1 = x1 + int(cols_with_edge[0])
            box_x2 = x1 + int(cols_with_edge[-1])
        else:
            box_x1 = x1 + int(roi_w * 0.12)
            box_x2 = x1 + int(roi_w * 0.88)

        target = None
        box_rect = None
        box_lines = [r for r in line_ys if r >= int(roi_h * 0.02)]
        min_box_h = max(35, int(roi_h * 0.16))
        max_box_h = int(roi_h * 0.60)
        est_box_h = int(roi_h * 0.35)

        if (box_x2 - box_x1) >= 60 and box_lines:
            if len(box_lines) >= 2:
                for i in range(len(box_lines) - 1):
                    cand_y1 = y1 + box_lines[i]
                    cand_y2 = min(y2, y1 + box_lines[i + 1])
                    cand_h = cand_y2 - cand_y1
                    if min_box_h <= cand_h <= max_box_h:
                        top_y1, top_y2 = cand_y1, cand_y2
                        cx = (box_x1 + box_x2) // 2
                        cy = (top_y1 + top_y2) // 2
                        target = (cx, cy)
                        detections[idx] = target
                        box_rect = (box_x1, top_y1, box_x2, top_y2)
                        break

            if target is None:
                for r_top in box_lines:
                    cand_y1 = y1 + r_top
                    cand_y2 = min(y2, cand_y1 + est_box_h)
                    if (cand_y2 - cand_y1) >= min_box_h:
                        cx = (box_x1 + box_x2) // 2
                        cy = (cand_y1 + cand_y2) // 2
                        target = (cx, cy)
                        detections[idx] = target
                        box_rect = (box_x1, cand_y1, box_x2, cand_y2)
                        break

        vis_items.append((idx, slot, (x1, y1, x2, y2), target, box_rect))

    return detections, vis_items


def switch_to_next_slot(reason):
    global active_slot_idx
    prev_idx = active_slot_idx
    next_idx = (active_slot_idx + 1) % len(EMPTY_SLOTS)
    active_slot_idx = next_idx
    slot_missing_frames[prev_idx] = 0
    print(f"[슬롯 전환 - {reason}] {EMPTY_SLOTS[prev_idx]['name']} -> {EMPTY_SLOTS[next_idx]['name']}")


def update_slot_by_presence(detections):
    slot = EMPTY_SLOTS[active_slot_idx]
    used = slot_pick_counts[active_slot_idx]

    if used >= slot["max_picks"]:
        switch_to_next_slot(f"{used}/{slot['max_picks']} 완료")
        return

    cur_det = detections.get(active_slot_idx)
    if cur_det is not None:
        slot_missing_frames[active_slot_idx] = 0
        return

    if not AUTO_SWITCH_ON_MISSING:
        return

    slot_missing_frames[active_slot_idx] += 1
    if slot_missing_frames[active_slot_idx] >= MISSING_FRAMES_THRESHOLD:
        switch_to_next_slot("빈박스/미검출 확인")


def run_ssh_script(phase_name, script, slot_name="-", target_xy=None, j2_offset=0.0, on_success=None, on_finished=None):
    global is_busy, robot_state, last_error, ssh_proc
    t_start = time.time()

    def worker():
        global is_busy, robot_state, last_error, ssh_proc
        try:
            ssh_proc = subprocess.Popen(
                SSH_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True
            )
            ssh_proc.stdin.write(script)
            ssh_proc.stdin.close()

            for line in ssh_proc.stdout:
                line = line.strip()
                if line.startswith("STATE:"):
                    robot_state = f"{phase_name}:{line[6:]}"
                    print(f"[로봇] {robot_state}")

            err = ssh_proc.stderr.read().strip()
            rc = ssh_proc.wait()
            elapsed = time.time() - t_start

            if rc != 0:
                last_error = err.splitlines()[-1] if err else f"exit code {rc}"
                robot_state = "ERROR"
                print(f"[오류/{phase_name}] {err}")
                append_log(phase_name, slot_name, target_xy, j2_offset, elapsed, "FAIL", last_error)
            else:
                last_error = ""
                append_log(phase_name, slot_name, target_xy, j2_offset, elapsed, "OK", "")
                if on_success:
                    on_success()
        except Exception as e:
            elapsed = time.time() - t_start
            last_error = str(e)
            robot_state = "ERROR"
            print(f"[예외/{phase_name}] {e}")
            append_log(phase_name, slot_name, target_xy, j2_offset, elapsed, "ERROR", str(e))
        finally:
            is_busy = False
            ssh_proc = None
            if on_finished:
                on_finished()

    is_busy = True
    robot_state = f"{phase_name}:START"
    threading.Thread(target=worker, daemon=True).start()


def compute_empty_pick_angles(slot_idx, target_y):
    slot = EMPTY_SLOTS[slot_idx]
    above_p = list(slot["above_pick"])
    base_pick = list(slot["pick"])
    j2_offset = 0.0

    if target_y is not None:
        if slot_base_y[slot_idx] is None:
            slot_base_y[slot_idx] = float(target_y)
        dy = float(target_y) - slot_base_y[slot_idx]
        j2_offset = round(clamp(dy * Y_TO_J2_GAIN, MAX_ABS_J2_OFFSET), 2)
        base_pick[1] = round(base_pick[1] + j2_offset, 2)

    if LOCK_GRIPPER_J6:
        fixed = slot.get("fixed_j6")
        j6_val = fixed if fixed is not None else above_p[5]
        above_p[5] = j6_val
        base_pick[5] = j6_val

    return above_p, base_pick, j2_offset


def start_cycle(trigger_mode="AUTO"):
    global cycle_count, detected_streak, last_cycle_done_time
    detected_streak = 0

    def mark_cycle_finished():
        global last_cycle_done_time
        last_cycle_done_time = time.time()

    def step_2_pick_empty_to_amr():
        global active_slot_idx
        chosen_idx = active_slot_idx
        time.sleep(CAMERA_SETTLE_SEC)
        target = None
        t_wait = time.time()
        order = [(chosen_idx + k) % len(EMPTY_SLOTS) for k in range(len(EMPTY_SLOTS))]
        while time.time() - t_wait < CAMERA_WAIT_SEC:
            with detections_lock:
                snap = dict(latest_detections)
            found = next((i for i in order if snap.get(i) is not None), None)
            if found is not None:
                if found != chosen_idx:
                    print(f"[슬롯 전환 - 현재 슬롯 상자 없음] {EMPTY_SLOTS[chosen_idx]['name']} -> {EMPTY_SLOTS[found]['name']}")
                    chosen_idx = found
                    active_slot_idx = found
                target = snap[found]
                break
            time.sleep(0.1)
        slot = EMPTY_SLOTS[chosen_idx]
        if target is None:
            global robot_state, last_error
            robot_state = "ERROR"
            last_error = f"{slot['name']} 최상단 상자 미검출 (확인 후 [r])"
            print(f"[중단] {last_error}")
            append_log("EMPTY_TO_AMR", slot["name"], None, 0.0, 0.0, "ABORT", last_error)
            return
        target_xy = target[:2] if target else None
        target_y = target[1] if target else None

        above_p, adj_pick, j2_off = compute_empty_pick_angles(chosen_idx, target_y)
        print(
            f"[사이클 #{cycle_count + 1} ({trigger_mode}) - 빈상자->AMR] 슬롯={slot['name']} | "
            f"타겟={target_xy} | J2보정={j2_off:+.2f} | J6={adj_pick[5]}"
        )

        def on_empty_done():
            global cycle_count
            slot_pick_counts[chosen_idx] += 1
            cycle_count += 1
            if slot_pick_counts[chosen_idx] >= slot["max_picks"]:
                switch_to_next_slot(f"{slot['name']} {slot['max_picks']}회 완료")
            print(f"[사이클 완료] #{cycle_count} 완료.")

        pnp_script = build_script(
            ROBOT_SINGLE_PNP_SCRIPT,
            above_pick=above_p,
            pick=adj_pick,
            above_place=ABOVE_AMR_EMPTY_PLACE,
            place=AMR_EMPTY_PLACE,
            speed=MOVE_SPEED,
        )
        run_ssh_script(
            phase_name="EMPTY_TO_AMR",
            script=pnp_script,
            slot_name=slot["name"],
            target_xy=target_xy,
            j2_offset=j2_off,
            on_success=on_empty_done,
            on_finished=mark_cycle_finished,
        )

    if not ENABLE_FULL_TO_SHIP:
        print(f"[사이클 #{cycle_count + 1} ({trigger_mode})] 출고 단계 생략, 빈상자->AMR만 실행")
        step_2_pick_empty_to_amr()
        return

    print(f"[사이클 #{cycle_count + 1} ({trigger_mode}) - 출고 이동] AMR 채워진 바구니 -> 출고위치")
    full_script = build_script(
        ROBOT_SINGLE_PNP_SCRIPT,
        above_pick=ABOVE_AMR_FULL_PICK,
        pick=AMR_FULL_PICK,
        above_place=ABOVE_SHIP_PLACE,
        place=SHIP_PLACE,
        speed=MOVE_SPEED,
    )
    run_ssh_script(
        phase_name="FULL_TO_SHIP",
        script=full_script,
        slot_name="AMR_TO_SHIP",
        on_success=step_2_pick_empty_to_amr,
        on_finished=lambda: mark_cycle_finished() if robot_state == "ERROR" else None,
    )


def emergency_stop():
    global ssh_proc
    if ssh_proc is not None:
        ssh_proc.terminate()
    try:
        r = subprocess.run(
            SSH_CMD, input=build_script(ROBOT_STOP_SCRIPT),
            text=True, capture_output=True, timeout=15
        )
        print("[비상 정지]", r.stdout.strip() or r.stderr.strip())
    except Exception as e:
        print(f"[비상 정지 실패] {e}")


def reset_all_slots():
    global active_slot_idx, slot_pick_counts, slot_base_y, slot_missing_frames
    global cycle_count, detected_streak, robot_state, last_error
    active_slot_idx = 0
    slot_pick_counts = [0 for _ in EMPTY_SLOTS]
    slot_base_y = [None for _ in EMPTY_SLOTS]
    slot_missing_frames = [0 for _ in EMPTY_SLOTS]
    cycle_count = 0
    detected_streak = 0
    robot_state = "IDLE"
    last_error = ""
    print("[리셋] 슬롯 인덱스, 카운트, 기준 Y, 사이클 횟수, 에러 상태가 초기화되었습니다.")


# =====================================
# 2. GStreamer 영상 수신 및 루프
# =====================================
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

print(f"포트={args.port_name} baud={args.baud} 속도={MOVE_SPEED} 자동트리거={AUTO_TRIGGER_ON_DETECTION} 출고단계={ENABLE_FULL_TO_SHIP}")
print("조작법: [Space] AMR 도착 신호(사이클 시작) | [n] 다음 슬롯 | [r] 리셋 | [s] 비상정지 | [q] 종료 (자동 트리거는 설정에 따름)")

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

    detections, vis_items = detect_slots_in_frame(frame)
    with detections_lock:
        latest_detections = detections
    if not is_busy:
        update_slot_by_presence(detections)

        has_target_now = detections.get(active_slot_idx) is not None
        if AUTO_TRIGGER_ON_DETECTION and robot_state != "ERROR" and has_target_now:
            if (time.time() - last_cycle_done_time) >= AUTO_COOLDOWN_SEC:
                detected_streak += 1
                if detected_streak >= DETECT_CONFIRM_FRAMES:
                    start_cycle(trigger_mode="AUTO_DETECT")
            else:
                detected_streak = 0
        else:
            detected_streak = 0

    display = frame.copy()
    h_img = display.shape[0]

    for idx, slot, (x1, y1, x2, y2), target, box_rect in vis_items:
        if idx != active_slot_idx:
            continue
        color = (0, 255, 0)
        cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            display,
            f"{slot['name']} ({slot_pick_counts[idx]}/{slot['max_picks']})",
            (x1 + 4, max(16, y1 + 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1
        )
        if box_rect is not None and target is not None:
            bx1, by1, bx2, by2 = box_rect
            cv2.rectangle(display, (bx1, by1), (bx2, by2), (0, 0, 255), 2)
            cv2.circle(display, target, 4, (255, 0, 0), -1)

    mode_txt = "AUTO" if AUTO_TRIGGER_ON_DETECTION else "WAIT AMR - press SPACE"
    status_line = (
        f"BUSY:{robot_state}" if is_busy
        else (f"ERR:{last_error[:35]} ([r]=clear)" if robot_state == "ERROR"
              else f"{mode_txt} READY | Cycle#{cycle_count + 1} | Slot:{EMPTY_SLOTS[active_slot_idx]['name']}")
    )
    cv2.putText(display, status_line, (15, h_img - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 255), 2)
    cv2.imshow("AMR + Empty Slot Cycle", display)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('s'):
        emergency_stop()
    elif key == ord('r'):
        if not is_busy:
            reset_all_slots()
    elif key == ord('n'):
        if not is_busy:
            active_slot_idx = (active_slot_idx + 1) % len(EMPTY_SLOTS)
            print(f"[수동 전환] 활성 슬롯: {EMPTY_SLOTS[active_slot_idx]['name']}")
    elif key == 32:
        if is_busy:
            print("[무시] 로봇 동작 중입니다.")
        elif robot_state == "ERROR":
            print(f"[무시] 에러 상태입니다: {last_error} -> 확인 후 [r]")
        else:
            start_cycle(trigger_mode="AMR_ARRIVED")

pipeline.set_state(Gst.State.NULL)
cv2.destroyAllWindows()
