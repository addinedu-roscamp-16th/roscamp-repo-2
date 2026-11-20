# `state_pick`: 양팔 교대 집기·담기 실험 사본

2026-11-20의 `multi_pick/`, `moveit_pick/`, `right_place_trial/`을 이 폴더에 복사해 수정했다. 원본 실행 코드는 그대로 둔다. 보정 파일은 복사 당시의 로컬 스냅샷이다.

## 실행 흐름

1. 물체를 처음 한 번 인식하고, 첫 팔(`--first-arm left` 기본)부터 좌우 교대로 배정한다. 배정된 팔로 접근 경로가 없으면 다른 팔로 자동 교체하지 않는다.
2. 집은 팔은 바구니가 비어 있으면 **들고 기다리지 않고 바로 운반·놓기·see_center 복귀**를 한다(2026-11-20 변경: gate가 한 번에 한 동작만 받으므로 들고 기다리면 복귀 2번이 더해질 뿐이었다).
3. 그 팔이 놓는 동안 다음 팔의 **접근 경로를 백그라운드로 계획**한다. 시작 자세는 "놓는 팔 = MoveIt `see_center`, 다음 팔 = 현재 실측"으로 가정한다(`moveit_target.json`의 `multi_object.assumed_start_rad`, 로그 `moveit_plan_background.log`).
4. 놓은 팔이 복귀하면 실제 관절이 가정과 1° 이내인지 확인하고 저장된 접근 경로를 바로 실행한다. 1°를 넘거나 백그라운드 계획이 실패하면 현재 자세에서 다시 계획한다.
5. 상품 구역 해제: TCP `z ≥ see_center z − 15 mm`(실물 복귀 오차 허용). 접근 **계획만** 실패한 경우(팔 미이동)는 `paused`로 두되 상품 구역은 해제한다.
6. 팔 배정은 좌우 교대를 유지하되, 각 팔 차례에 남은 물체 중 **그 팔 쪽에 더 가까운 물체**를 고른다.

이전 방식(두 물체를 들고 see_center에서 대기 후 순서대로 놓기)은 `multi_pick/sam_multi_real.py.before_direct_place.bak`에 있다. `test_state_runtime.py`는 이전 방식 기준이라 갱신 전이다.

양팔이 각각 물체를 들 때 MoveIt의 부착 물체 ID를 팔별로 분리한다. 먼저 놓은 팔의 물체 모델만 제거하며, 기다리는 팔의 물체 모델은 유지한다.

PC gate는 실행 목표를 한 번에 하나만 받는다. 따라서 두 팔의 **물리 동작은 순서대로 실행**하며, 집기·담기 작업의 소유권과 순서를 연동한다. 다음 담기 계획은 첫 팔의 담기 **시작 전**, 두 팔이 실제 `see_center`에 있을 때 계산한다. 현재 gate와 전역 planning scene에서 첫 팔이 움직이는 도중 계획을 돌리는 방식은 사용하지 않는다.

## 상태와 실패 처리

팔별 상태: `idle`, `pick_waiting`, `picking`, `place_waiting`, `placing`, `paused`, `error`. 상품·바구니 구역 소유권은 별도로 기록한다. 집기 worker 실패는 해당 팔을 `paused`, 운반·담기·복귀 worker 실패는 서비스 `error`로 둔다. 실패 시 자원 소유권을 자동 해제하지 않는다. `retrying` 상태는 없다. 거리 기준 정지와 `PHD-08` 인식 오류의 신호 연결은 아직 없다.

실행 중 `C` 복귀, `O` 그리퍼 열기, `R` 큐 폐기는 막는다. `S`는 활성 worker에 중단을 요청하며, 사전 계획 중 `S`를 눌러 중단한 경우 담기를 자동 시작하지 않는다. 실제 중단과 복구에는 로봇 자세·gate fault를 사용자가 확인해야 한다.

## 실행 방법

Pi 양쪽과 PC MoveIt·gate는 기존 `multi_pick` 명령을 그대로 사용한다. 픽 UI만 바꾼다.

```bash
~/camera_code/.venv/bin/python ~/camera_code/state_pick/multi_pick/sam_multi_real.py --stereo-fixed-see-center --first-arm left
```

기본값은 첫 접근 계획 뒤 `E`로 실행을 시작한다. 그다음 배정된 팔의 접근은 자동 실행한다. `--auto-execute`를 붙이면 첫 접근도 자동 실행한다. 양쪽 카메라에서 물체별 ROI를 그린 뒤 `M`으로 짝을 확인한다. `--first-arm right`로 시작 팔을 바꿀 수 있다.

## 검증 범위

```bash
cd ~/camera_code/state_pick
~/camera_code/.venv/bin/python -m unittest -v test_state_machine test_state_runtime test_state_cache test_held_object
python3 -m py_compile multi_pick/sam_multi_real.py moveit_pick/held_object.py right_place_trial/coordinated_right_case.py right_place_trial/place_steps.py
```

상태 전이, 두 물체의 계획·담기 순서, 양팔 부착 물체 모델 분리 테스트와 문법 검사만 통과했다. **실물에서 `state_pick`의 물체 집기·담기 흐름은 아직 검증하지 않았다.** 처음에는 물체 두 개를 떨어뜨려 놓고 각 단계의 자세, 붙은 물체 모델, 바구니 점유 해제와 로그를 확인해야 한다.
