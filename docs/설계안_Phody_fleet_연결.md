# 설계안: Phody ↔ 팀 관제(fleet) 연결

2026-11-25 작성. **승인 전 설계안**이다. 관련 문서:

- 비교 검토: [인터페이스_통합_검토_Phody_2026-11-25.md](인터페이스_통합_검토_Phody_2026-11-25.md)
- 팀에 보낸 요청: [사람용](인터페이스_요청_Phody_사람용.md) · [AI용](인터페이스_요청_Phody_AI용.md)
- 미구현 번호 U-1 ~ U-10은 검토 문서 3장 번호다.

## 1. 구조: 앱 안에 "fleet 연결" 노드 하나를 얹는다

```
 [팀 관제 fleet]
     │  /workcell/fill_basket (액션)      /arm_left/state, /arm_right/state (5 Hz)
     │  set_mode · set_lock · force · fault_resolved (서비스)   /fleet/heartbeat
     ▼
 ┌──────────────── 픽 앱 프로세스 (sam_multi_real.py) ─────────────────┐
 │ fleet_link (새 파일)                 기존 앱 (화면 루프, 상태 머신)       │
 │  - 자기 ROS 노드 + 실행기 스레드  ──명령 큐──►  캡처 → 인식 → 큐 실행      │
 │  - 액션 서버 · 서비스 서버          ◄──상태 스냅샷──  WorkState, 담은 수   │
 │  - 상태 발행 5 Hz                                                      │
 └─────────────────────────────────────────────────────────────────────────┘
     ▼ (지금과 같음) worker 프로세스 → 실행 게이트 → Pi 브리지
```

**앱 안에 넣는 이유**
- 앱 상태(큐, `WorkState`, 쥔 물건, 단계)를 그대로 읽을 수 있다.
- 별도 프로세스로 만들면 이 상태를 파일이나 소켓으로 다시 주고받아야 한다.

**스레드 규칙 (IR-30 · IR-31)**
- fleet 연결 노드는 자기 실행기 스레드에서 돈다. 콜백 안에서 앱을 직접 건드리거나 기다리지 않는다.
- 앱에 시킬 일은 **명령 큐**에 넣는다. 화면 루프(메인 스레드)가 매 프레임 꺼내 실행한다.
- 앱 상태는 화면 루프가 매 프레임 **스냅샷**(잠금으로 보호한 dict)에 써 둔다. fleet 연결은 그것만 읽는다.

**켜는 법**
- `pick.launch.py ... fleet:=true`. 끄면 지금과 똑같이 동작한다.
- 화면과 키(P 정지, U 복구 등)는 fleet 모드에서도 그대로 쓴다.

## 2. 인터페이스별 동작

### 2-1. `/workcell/fill_basket` (U-1)

**목표 받기**: 아래 중 하나면 거절하고 사유를 준다.

| 조건 | 사유 |
| --- | --- |
| 모드를 아직 못 받음 | `MODE_MISMATCH` (IR-72) |
| 두 팔 모두 락 해제 | `LOCK_RELEASED` |
| 이미 담기 목표가 있음 | `NOT_READY` |
| 앱이 쉬는 상태가 아님(정지 · 복구 필요 · 물건 쥠) | `NOT_READY` |
| `station_id`가 `WORK`가 아님 | `UNKNOWN_TARGET` |
| 상품 코드를 모름 | `UNKNOWN_TARGET` |

**실행 순서** (화면 루프가 명령 큐에서 꺼내 실행):
1. 지금 `R`(초기화) → 캡처 → `M`(YOLO 인식 + 스테레오 매칭)을 차례로 부른다. 사람이 누르던 것과 같은 함수다.
2. 인식된 물체 중 요청 상품(`kind` → YOLO 클래스)만, 요청 개수만큼 고른다. 고르는 순서는 지금 큐 순서와 같다.
   - 모자란 수는 `missing`으로 둔다. 사유는 팀 결정(DECIDE-3/요청 2-3)을 기다린다. 그때까지는 `RECOGNITION_ERROR`, 코드에 `TODO(Q)`.
3. `--auto-execute`와 같은 방식으로 큐를 자동 실행한다. 지금 동시 모드(`concurrent_pick_place`)를 그대로 쓴다.
4. 진행 피드백(0.5초마다): `arm_id`(지금 움직이는 팔), `phase`, `placed_total`.
   - `phase` 값은 팀 결정 전까지 제안값 `CAPTURING` / `PICKING` / `TO_BASKET` / `PLACING` / `RETURNING`을 쓴다.
5. 끝:
   - **큐를 다 비움**: `success=true`, `placed`, `missing`.
   - `placed`는 C에서 그리퍼 열기에 성공한 물체 수다(DECIDE-8 제안). `placed_confirmed`는 같은 값이다(팀 msg에 칸이 생기면).
6. 실패 (worker 실패 → 앱 일시정지):

| 실패 종류 | `success` | `reason` |
| --- | --- | --- |
| 집기 실패 · 미세조정 실패 | false | `PICK_FAILED` |
| 게이트 고장 잠금 · 관절 상태 끊김 | false | `ARM_FAULT` |

   - 둘 다 그때까지 `placed`를 싣는다. 물건을 쥐고 있으면 `uncertain`에 넣는다(팀 msg에 칸이 생기면).
7. **취소**: 앱 `P`(정지)와 같다. 쥔 물건은 `uncertain`, `reason=CANCELED`. 쥔 물건 처리는 DECIDE-9를 기다린다.

### 2-2. `/arm_left/state` · `/arm_right/state` (U-2), 5 Hz

| 칸 | 값 |
| --- | --- |
| `state` | `WorkState.arms[side]`를 그대로 옮긴다: `idle→IDLE`, `pick_waiting→PICK_WAITING`, `picking→PICKING`, `place_waiting→PLACE_WAITING`, `placing→PLACING`, `paused→PAUSED`, `error→ERROR`. `placing`은 B 진입 ~ C ~ B 복귀(바구니 점유)라서 두 팔이 동시에 `PLACING`이 되지 않는다 |
| `holding` | 그 팔이 담기 대기 목록(`held_jobs`)에 있으면 true |
| `gripper` | `/{side}/gripper_value` < 50 → `CLOSED`, 아니면 `OPEN` |
| `detail` | 마지막 실패 사유(없으면 "") |
| `lock` · `mode` · `boot_id` · `last_request_id` · `forced` | fleet 연결이 갖고 있는 값 |
| `order_id` · `placed_total` | 지금 담기 목표의 `order_id`(= trip_id)와 담은 수 |

팀 contract에 이 토픽이 생기기 전(FIX-1)에는 이름을 우리 쪽 임시 표에서 읽는다(3장).

### 2-3. 관리 서비스 (U-3)

공통 규칙:
- 응답은 `accepted` · `reason` · `request_id` · `detail`이다.
- 같은 `request_id`는 최근 32건을 60초 기억해서 같은 답을 돌려준다(IR-21).
- 콜백 안에서 기다리지 않는다.

| 서비스 | 지금 만들 것 | 팀 결정 기다리는 것 |
| --- | --- | --- |
| `/{팔}/set_mode` | 모드 저장. 다르면 담기 목표 거절 | — |
| `/{팔}/fault_resolved` | 게이트 `reset_execution_fault`를 **비동기로** 부르고 바로 `accepted=true`. 결과는 상태(`ERROR` 해제)로 보임 | 2초 한도 예외(DECIDE-5) |
| `/{팔}/set_lock` | 목표 값 저장. 멈춘 팔만(`NOT_STOPPED`), 쥐고 있으면 `confirm_drop` 없이 `HOLDING_ITEM`. 락 해제된 팔은 새 물체 배정에서 뺌. **팔은 움직이지 않음** | 해제 때 안전 자세로 옮길지(DECIDE-4) |
| `/{팔}/force` | 시험 모드가 아니면 `NOT_TEST_MODE`. 시험 모드면 값만 저장하고 `forced`에 보임 | 어떤 키를 받을지 |

### 2-4. 관제 신호 `/fleet/heartbeat` (U-4)

- **지금 만들 것**: 끊김 감지(1.5초)만 한다. `detail=HEARTBEAT_LOST`로 보이고, 새 담기 목표는 거절한다.
- **팀 결정 기다리는 것**: 움직이는 중에 어떻게 멈출지(DECIDE-6). 결정 전에는 **동작을 멈추지 않는다.** 이건 통신 규칙과 다르므로 코드와 문서에 `TODO(Q)`로 남긴다.

### 2-5. 켜질 때 (U-5, IR-72)

- 시작 상태: 모드 `UNKNOWN`, 두 팔 락 잠김(`lock=true`), 강제 값 없음, 새 `boot_id`(UUID).
- 담기 목표는 `set_mode`를 받은 뒤부터 받는다.

## 3. 이름과 정의 (U-6)

- 팀 `fleet_interfaces` 패키지(msg · action · srv)가 우리 워크스페이스에서 빌드돼야 한다. **결정 필요(4장 ①).**
- 바깥 이름은 contract.yaml에서 읽는다.
  - 팀 contract에 아직 없는 것(`/{arm}/state`, 팔 서비스)은 우리 쪽 임시 파일 `jetcobot_dual_app/config/fleet_names_pending.yaml`에 적는다.
  - 팀 contract에 생기면 그 파일에서 지운다.
- 도메인: 개발 중 83 유지, 통합 시험 때 6.

## 4. 정해 주셔야 할 것

1. **팀 정의 파일을 어떻게 가져올지**
   - (a) dev 브랜치의 `fleet_interfaces`를 그대로 가져와 빌드한다. 지금 칸만 쓰고, 없는 칸(`holding` · `uncertain` 등)은 팀이 넣을 때까지 비워 둔다. **← 권장**
   - (b) 명세 v0.2 칸을 우리가 로컬에서 미리 넣는다. 팀 것과 달라질 위험이 있다.
2. **1차 범위** (권장)
   - 1차: `set_mode`, 상태 발행, `fill_basket`(정상 · 실패 · 취소), `fault_resolved`, 켜질 때 규칙
   - 2차(팀 답 이후): `set_lock` 동작, `force` 키, heartbeat 멈춤, 멈춤 · 이어서(Q-05)
3. **팀 결정 전 임시 동작**을 위처럼 해도 되는지
   - 모자란 수는 `RECOGNITION_ERROR`로 보고한다.
   - 락 해제 때 팔은 움직이지 않는다.
   - heartbeat가 끊겨도 움직이는 팔을 멈추지 않는다.

## 5. 확인 방법

- **단위 테스트**: 가짜 앱 상태로 서버 응답 · 상태 변환 · 중복 `request_id` · 모드 거절을 확인한다.
- **로컬 연동**: 팀 `sim/fake_robots`처럼 가짜 fleet 클라이언트로 `fill_basket` 목표를 보내고, 실물 1회 실행한다.
- 실물 확인 전까지 "단위 테스트만"으로 보고한다.
