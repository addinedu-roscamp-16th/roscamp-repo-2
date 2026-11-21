# `state_pick`: 양팔 교대 집기·담기 실험 사본

2026-11-20의 `multi_pick/`, `moveit_pick/`, `right_place_trial/`을 이 폴더에 복사해 수정했다. 원본 실행 코드는 그대로 둔다. 보정 파일은 복사 당시의 로컬 스냅샷이다.

## 실행 흐름

1. 물체를 처음 한 번 인식하고, 첫 팔(`--first-arm left` 기본)부터 좌우 교대로 배정한다. 배정된 팔로 접근 경로가 없으면 다른 팔로 자동 교체하지 않는다.
2. 집은 팔은 바구니가 비어 있으면 **들고 기다리지 않고 바로 운반·놓기·see_center 복귀**를 한다(2026-11-20 변경: gate가 한 번에 한 동작만 받으므로 들고 기다리면 복귀 2번이 더해질 뿐이었다).
3. 첫 접근 계획이 끝나면 `E`를 기다리는 동안 두 번째 상품 접근을 양팔의 SRDF `see_center` 자세에서 미리 계획한다. `E`는 첫 계획이 준비되면 누를 수 있다. 놓기·리프트 후 복귀 경로가 확정되면 사전 계획과 예정 도착 자세를 비교한다. 맞는 계획이 없으면 복귀 중에 예정 도착 관절값으로 다음 접근을 계획한다. (`moveit_target.json`의 `multi_object.assumed_start_rad`, 로그 `moveit_plan_background.log`).
4. 복귀 worker가 성공하면 UI 루프에서 새 양팔 관절 피드백으로 안정 여부를 확인한 뒤 저장된 접근 경로의 시작점과 비교한다. 피드백이 8초 안에 안정되지 않으면 작업을 일시정지한다. 기본 3° 이내면 기존 검사와 실행 게이트를 거쳐 실행한다. 시작점이 어긋나거나 사전 계획이 실패했으면 실측 자세에서 다시 계획한다.
5. 상품 구역 해제: TCP `z ≥ see_center z − 15 mm`(실물 복귀 오차 허용). 접근 **계획만** 실패한 경우(팔 미이동)는 `paused`로 두되 상품 구역은 해제한다.
6. 팔 배정은 좌우 교대를 유지하되, 각 팔 차례에 남은 물체 중 **그 팔 쪽에 더 가까운 물체**를 고른다.

이전 방식(두 물체를 들고 see_center에서 대기 후 순서대로 놓기)은 `multi_pick/sam_multi_real.py.before_direct_place.bak`에 있다. `test_state_runtime.py`는 이전 방식 기준이라 갱신 전이다.

양팔이 각각 물체를 들 때 MoveIt의 부착 물체 ID를 팔별로 분리한다. 먼저 놓은 팔의 물체 모델만 제거하며, 기다리는 팔의 물체 모델은 유지한다.

PC gate는 실행 목표를 한 번에 하나만 받는다. 다음 상품의 접근 계획은 복귀 계획이 확정된 뒤 실행과 겹쳐 진행한다. 실물에서 복귀 실행 전 gate 충돌 검사와 접근 계획이 같은 MoveIt 서비스에 부하를 주는지는 확인이 필요하다.

## 상태와 실패 처리

팔별 상태와 상품·바구니 구역 소유권을 별도로 기록한다. 실패 또는 `P`·`S` 정지 시 다음 작업을 막고 캡처 폴더의 `recovery.json`에 상품, 마지막 작업 단계, 구역 점유와 담기 체크포인트를 저장한다.

- `U`: 로봇과 게이트를 확인한 뒤 사용한다. 계획만 실패했으면 같은 상품을 다시 계획한다. 그리퍼 열림과 부착 모델 제거가 기록된 뒤 실패했다면, 실제로 상품이 바구니에 있고 양팔이 `see_center`로 복귀했음을 확인한 뒤 다음 상품부터 진행한다. 복구 후 실행은 `E`로 승인한다.
- 동작 결과나 보유 물체가 미확인인 경우에는 `U`로 진행하지 않는다. 실행 중 `C`, `O`, `R`로 구역 소유권을 우회할 수 없다. 복구 정보는 운영 확인용이며 앱 재시작 시 자동 복원을 하지 않는다.
- 선행 계획은 큐의 첫 상품에 연결해 두고, 해당 팔이 상품 구역을 확보할 때 상품을 큐에서 꺼낸다.
- `stationary_plan_wait.jsonl`에는 양팔 관절 샘플로 추정한 계획 대기 구간만 기록한다. 접근·담기·복귀 계획 및 복귀 후 선행 계획 대기를 포함한다. 양쪽 피드백이 신선하고 인접 샘플 변화가 0.3° 이내인 간격만 합산한다.

## 실행 방법

Pi 양쪽과 PC MoveIt·gate는 기존 `multi_pick` 명령을 그대로 사용한다. 픽 UI만 바꾼다.

```bash
~/camera_code/.venv/bin/python ~/camera_code/state_pick/multi_pick/sam_multi_real.py --stereo-fixed-see-center --first-arm left
```

주문 테스트에는 `--order-ids object_1 object_3`으로 매칭된 ROI ID를 지정한다. ID를 생략하면 매칭된 상품 전체를 사용한다. 주문 서비스와 핑키 도킹 신호는 아직 연결하지 않았다.

기본값은 첫 접근 계획 뒤 `E`로 실행을 시작한다. 그다음 배정된 팔의 접근은 자동 실행한다. `--auto-execute`를 붙이면 첫 접근도 자동 실행한다. 양쪽 카메라에서 물체별 ROI를 그린 뒤 `M`으로 짝을 확인한다. `--first-arm right`로 시작 팔을 바꿀 수 있다.

## 검증 범위

```bash
cd ~/camera_code/state_pick
~/camera_code/.venv/bin/python -m unittest -v test_state_machine test_state_runtime test_state_cache test_held_object
python3 -m py_compile multi_pick/sam_multi_real.py moveit_pick/held_object.py right_place_trial/coordinated_right_case.py right_place_trial/place_steps.py
```

상태 전이, 두 물체의 계획·담기 순서, 양팔 부착 물체 모델 분리 테스트와 문법 검사만 통과했다. **실물에서 `state_pick`의 물체 집기·담기 흐름은 아직 검증하지 않았다.** 처음에는 물체 두 개를 떨어뜨려 놓고 각 단계의 자세, 붙은 물체 모델, 바구니 점유 해제와 로그를 확인해야 한다.
