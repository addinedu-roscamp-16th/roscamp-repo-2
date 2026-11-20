# moveit_pick 코드 지도

평소 실행: `~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py`  
계획만 확인: 같은 명령에 `--plan-only` 추가. 로봇/PC 제어 프로세스 재시작은 사용자가 직접 한다.

## 현재 픽업 실행 경로

| 단계 | 파일 | 주 역할 |
|---|---|---|
| 시작 | `manual_roi_drag.py` | fine ROI, 회전 매칭, 실행 기본 옵션을 켜는 13줄 진입점. 짧아도 사용자 명령을 유지하려고 별도 보관 |
| 화면·인식 | `sam_dual_yaw_moveit.py` | 좌우 영상, ROI, SAM2/LightGlue, 3D/yaw, 키 입력, worker 시작 |
| ROI 좌표 | `fine_roi_real.py` | 화면 ROI 픽셀과 카메라 변환에서 XY 보정량 계산 |
| 접근 | `move_dual_sam_yaw_real.py`, `dual_arm_coordinator_real.py` | 양팔 후보 선택·계획 저장·충돌 확인·접근 실행 |
| fine·G·L | `move_right_sam_yaw.py` | 저장된 `--fine-request` 처리, fine 실행, Z 하강, 닫기, 리프트 |
| 복귀·열기 | `return_see_center.py`, `open_gripper.py` | 화면에서 별도 프로세스로 실행하는 로봇 동작 |
| 공용 계산 | `motion_math.py` | 양팔 경로 표본화와 관절 이동 비용. 이전 `dual_arm_math_real.py` + `yaw_trajectory_cost.py` 통합 |

실행 순서: `manual_roi_drag` → `sam_dual_yaw_moveit` → 접근 시 `move_dual_sam_yaw_real`/`dual_arm_coordinator_real` → fine·G·L 시 `move_right_sam_yaw`. Fine 요청은 JSON으로 저장한 뒤 같은 worker에 `--fine-request`로 전달한다. 이전 전달용 파일 `move_roi_fine_real.py`는 제거했다.

## 별도 진단·실험

- `tcp_z0.py --arm left|right`: 양쪽에 중복되던 TCP Z=0 계획/진단 코드를 통합. 기본은 계획만, `--execute`를 명시해야 실제 이동한다. 이전 `left_tcp_z0.py`, `right_tcp_z0.py`는 제거했다.
- `trial_minimal_drag_route.py`: RViz 계획의 최소 드래그 구간 실험. `--minimal-drag`에서 동적 import도 하므로 실행 경로를 유지했다.
- `pi_set_current_zero.py`: Pi 영점 저장 도구. 일반 픽업 흐름과 무관하며 Pi에서만 사용한다.

## 케이스 놓기 관련

MoveIt 충돌 물체 위치와 실행법은 [`tin_case.yaml`](../../pinky_ws/src/jetcobot_dual_real/config/tin_case.yaml) 및 [`TIN_CASE.md`](../../pinky_ws/src/jetcobot_dual_real/config/TIN_CASE.md)에 있다. 현재는 오픈 케이스 형상과 임시 위치를 MoveIt 장면에 넣는 단계다. 케이스 안으로 이동해 놓고 그리퍼를 여는 절차는 아직 코드에 없다.

초코바·멘토스 2개 형상은 [`pick_objects.yaml`](../../pinky_ws/src/jetcobot_dual_real/config/pick_objects.yaml)에 있으며, UI에서 `--object-type chocobar|mentos_pair`로 캡처에 기록한다. G 파지 직후 MoveIt 부착 명령은 [`PICK_OBJECTS.md`](../../pinky_ws/src/jetcobot_dual_real/config/PICK_OBJECTS.md)를 본다.
