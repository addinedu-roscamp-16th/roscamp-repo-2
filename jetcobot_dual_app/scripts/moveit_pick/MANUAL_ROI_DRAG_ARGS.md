# `manual_roi_drag.py` 실행 인자

기준 코드: [`manual_roi_drag.py`](manual_roi_drag.py), [`sam_dual_yaw_moveit.py`](sam_dual_yaw_moveit.py). 이 문서는 `manual_roi_drag.py`로 실행할 때의 기본값을 설명한다. 이 진입점은 `--fine-roi`, `--match-rotation`, 실물 실행 허용을 자동으로 켠다.

## 자주 쓰는 명령

```bash
# ROI 선택, SAM2/LightGlue, 3D 미리보기와 MoveIt 계획 (실행 키 사용 가능)
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py

# 저장된 see_center 스테레오 보정 적용, 실행 키 사용 가능
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --stereo-fixed-see-center

# 실물 실행 비활성, 계획과 미리보기만
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --stereo-fixed-see-center --plan-only

# 회전 매칭을 끄고 기존 0° 매칭 사용
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --no-match-rotation

# 물체 종류를 캡처 파일에 기록 (자동 분류는 아님)
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --object-type chocobar
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --object-type mentos_pair
```

이 진입점은 실행을 기본 허용한다. `--plan-only`를 주면 실물 실행을 막는다. 기존 `--enable-execute` 인자도 사용할 수 있다. ROI를 잡으면 미리보기와 계획을 만들고, 실제 이동은 화면에서 해당 실행 키를 눌렀을 때 요청한다. 저장된 대응점 수나 3D `valid` 개수만으로 실제 물체 좌표·충돌 여유가 검증되지는 않는다.

`goal_tolerance_deg`는 이 스크립트의 인자가 아니다. Pi 브리지의 ROS 파라미터이며, PC의 `manual_roi_drag.py` 명령에는 붙이지 않는다. 현재 오른팔 1° 잔차만을 이유로 자동 재이동/실패하던 로직은 제거됐다.

`--object-type`은 `moveit_target.json`에 초코바/멘토스 형상 이름을 기록한다. 기존 픽업 이동은 그대로이며, G로 잡은 뒤 MoveIt 부착 명령은 [`PICK_OBJECTS.md`](../../pinky_ws/src/jetcobot_dual_real/config/PICK_OBJECTS.md)를 본다.

접근 목표는 그리퍼 TCP가 물체 위에 놓이는 위치다. 손목 카메라가 TCP에서 떨어져 있으므로 접근 후 물체 바운딩 박스가 화면 중앙에 오지 않을 수 있다. 미세 보정은 X에서 ROI 중심을, Y에서 ROI 윗변을 사용한다. SDK XY 명령 뒤에는 실제 TCP 이동과 기울기를 로그에서 확인한다.

오른팔 접근 끝점의 관절 잔차는 로그에 남긴다. 1° 초과만으로 재이동하거나 접근 실패로 처리하지 않는다. 계획·SDK 액션 성공은 실제 집게 접촉이나 자세의 정확도를 입증하지 않는다.

## ROI·매칭·카메라

| 인자 | 이 진입점의 기본값 | 의미 |
|---|---|---|
| `--fine-roi` | 켜짐 | 접근 후 새 실시간 ROI로 X/Y 미세 보정 UI 사용. X는 ROI 중심, Y는 ROI 윗변을 화면 Y=240 px로 맞춘다. 기본 이동은 SDK Cartesian XY MoveL 요청이며 실제 Z·기울기 유지는 실측 로그로 확인한다. |
| `--match-rotation` | 켜짐 | 오른쪽 ROI를 0°·90°·180°·270°로 비교하고, 선택한 매칭 좌표를 원본 영상 좌표로 되돌린다. 2D 기하 일관성 후보만 이후 단계에 전달한다. |
| `--no-match-rotation` | 꺼짐 | 위 회전 탐색을 끄고 기존 0° 매칭을 사용한다. 이 인자를 주면 진입점이 `--match-rotation`을 자동 삽입하지 않는다. |
| `--retry-clahe` | 꺼짐 | 3D 유효점이 적을 때 대비 강화 영상을 이용한 재매칭을 시도한다. 정확도 향상이 보장된 옵션은 아니다. |
| `--mask-erode N` | `3` px | SAM 마스크 경계에서 제외할 픽셀 수. 허용 범위 0~20. |
| `--max-keypoints N` | `2048` | 마스크 안 SuperPoint 특징점 최대 개수. |
| `--left-port N` / `--right-port N` | `5000` / `5001` | 좌우 RTP/H.264 카메라 UDP 포트. 서로 달라야 한다. |
| `--device cuda\|cpu` | 사용 가능하면 CUDA, 아니면 CPU | SAM2·SuperPoint·LightGlue 추론 장치. |
| `--sam-checkpoint PATH` | `~/sam2_src/checkpoints/sam2.1_hiera_small.pt` | SAM2.1 가중치. |
| `--sam-config NAME` | `configs/sam2.1/sam2.1_hiera_s.yaml` | SAM2.1 모델 설정. |
| `--camera-calibration PATH` | `moveit_pick/calibration/camera_calibration.npz` | 카메라 내부 파라미터와 왜곡 계수. |
| `--handeye PATH` | `moveit_pick/calibration/T_gripper_camera.json` | 왼팔 손–눈 변환. 새 후보는 `moveit_pick/calibration/T_left_gripper_camera_20261117.json`으로 보관하며 인자로 지정해 비교할 수 있다. |
| `--right-handeye PATH` | `moveit_pick/calibration/T_right_gripper_camera_20261117.json` | 오른팔 손–눈 변환. 2026-11-17 재계산 결과를 기본 사용한다. 내부 위치 산포 20.09 mm로 일관성 판정은 실패했다. |
| `--stereo-board-relative` | 꺼짐 | 같은 캡처의 두 영상에 ChArUco 보드가 보이면 보드로 오른쪽 카메라의 상대 자세를 구한다. 왼쪽 카메라의 세계 자세를 기준으로 둔다. |
| `--stereo-fixed-see-center` | 꺼짐 | 실시간 마커 없이 저장한 보드 기반 보정값을 관찰 자세에 적용한다. 양팔 관절이 기록된 기준 자세에서 각각 2° 이내여야 한다. |

## 3D 미리보기·접근 계획

| 인자 | 기본값 | 의미 |
|---|---|---|
| `--arm left\|right` | `right` | 초기 팔 설정. 실행 중 자동 팔 선택 흐름에서 바뀔 수 있다. |
| `--yaw-camera auto\|left\|right` | `auto` | yaw 계산에 쓸 카메라 선택 방식. |
| `--above-mm N` | `40` | 물체 3D Z 위 접근 목표 높이(mm). `G` 하강량과 같은 값은 아니다. |
| `--reproj-px N` | `30` | 삼각측량 재투영 허용 오차(px). 통과가 올바른 대응점임을 보증하지 않는다. |
| `--min-angle-deg N` | `1.5` | 삼각측량 광선 사이 최소 각도. |
| `--min-valid-points N` | `6` | 3D/yaw 미리보기에 필요한 최소 유효점 수. |
| `--tcp-closing-axis x\|y` | `x` | 모델 TCP에서 집게 닫힘 축. |
| `--yaw-offset-deg N` | `0` | 목표 yaw 추가 회전각(도). |
| `--allow-gripper-tilt` | 꺼짐 | 수직 TCP 목표 자세 허용 오차를 기본 2°에서 8°로 완화. |
| `--camera-center` | 꺼짐 | 카메라 중심 기준 접근. 이 진입점의 `--fine-roi`와 함께 사용할 수 없다. |
| `--allow-uncertain-yaw` | 꺼짐 | 호환용 인자. 현재 yaw 불일치는 경고로 취급한다. |
| `--max-capture-age N` | `120`초 | 저장된 캡처를 계획에 사용할 최대 경과 시간. |
| `--out-dir PATH` | `~/camera_code/sam_yaw_output` | 캡처, 미리보기, 계획·실행 기록 저장 경로. |

## 실행·ROS 연결

| 인자 | 기본값 | 의미 |
|---|---|---|
| `--enable-execute` | 켜짐 | `E` 등 실물 동작 요청 허용. 이 진입점에서는 기본 적용되므로 생략 가능. |
| `--plan-only` | 꺼짐 | 실물 동작을 막고 계획과 미리보기만 사용. |
| `--minimal-drag` | 꺼짐 | 접근 경로에서 끝점/드래그 구간 분할 실행 사용. |
| `--motion-python PATH` | `/usr/bin/python3` | ROS 2 Jazzy 및 MoveIt 메시지를 사용할 worker Python. |
| `--group NAME` | `arm_<선택 팔>` | MoveIt SRDF 그룹. 코드에서 선택 팔의 `arm_left`/`arm_right`와 일치해야 한다. |
| `--tip-link NAME` | `<선택 팔>_TCP` | MoveIt TCP 링크. 선택 팔과 일치해야 한다. |
| `--move-action NAME` | `/jetcobot_dual/move_action` | MoveIt 계획 액션. |
| `--execute-action NAME` | `/jetcobot_dual/execute_trajectory` | MoveIt 실행 액션. |
| `--display-topic NAME` | `/jetcobot_dual/display_planned_path` | 계획 경로 표시 토픽. |
| `--max-joint-change-deg N` | `45`° | PC 쪽 계획·실행 관절 변화 검사값. Pi SDK 자체 제한과 별개다. |
| `--max-state-change-deg N` | `1`° | 계획 시작 상태와 실측 상태 간 허용 차이. |

## 화면 키

| 키 | 동작 |
|---|---|
| `0` / `1` / `2` | yaw 카메라 자동 / 왼쪽 / 오른쪽 선택 |
| `p` | 계획 요청 |
| `e` | 접근 또는 선택된 미세 보정 실행 요청 (`--plan-only`에서는 비활성) |
| `m` | 현재 캡처 처리 재요청. 이미 처리한 캡처는 `r`로 새로 잡아야 한다. |
| `X` / `Y` | 미세 ROI 보정 축 선택 |
| `G` | 첫 번은 현재/목표 TCP Z와 하강량 출력, 두 번째는 하강 후 그리퍼 닫기 요청 |
| `L` | 파지 완료 기록이 있을 때 5 cm 상승 후 `see_center` 복귀 요청 |
| `O` | 선택 팔 그리퍼 열기 요청 |
| `C` | 양팔 `see_center` 복귀와 그리퍼 열기 요청 |
| `r` | 새 캡처를 위해 초기화 |
| `s` | 실행 중 worker 중단 요청 |
| `q` 또는 `Esc` | 종료 |

전체 인자 목록은 `~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --help`로 확인할 수 있다.
