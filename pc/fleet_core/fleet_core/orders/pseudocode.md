# 주문 코어(OrderOrchestrator) 의사코드

주문 코어는 주문판을 받아 로봇에 주문을 하나씩 묶고, 채우기와 교환을 거쳐 결과를 보고한다.
길, 구역, 도킹은 교통 층이 맡고 팔 goal의 시간 초과와 재시도는 ArmJobs가 맡는다.
주문 코어는 입력 함수로만 듣고 `take_output()`으로만 말한다. 명령은 모두 `common/commands.py`에 있다.

동작 기준은 설계 v2.4(주문 보류, 2026-11-19)다. 보고 상태 가운데 끝은 SHIPPED, PARTIAL, CANCELED 셋이다.
HELD는 끝이 아니다. 보류된 주문은 주문판에 남아 있는 동안 다시 배정하지 않는다.
- `ARM_FAULT` 보류: 주문이 로봇에 묶인 채로 운영자의 강제 출고나 치움을 기다린다.
- `ROBOT_FAULT` 보류: 주문은 로봇에서 이미 떨어졌고 강제 출고도 거절한다. 주문판에서 그 id가 빠질 때까지 보류로 남는다.
- 주문판에서 빠졌다가 같은 id로 다시 들어오면 보류 기록이 지워진 뒤라 새 주문처럼 배정될 수 있다.

이 문서는 v2.4의 목표 동작이다. 1차는 주문판 처리, 작업대 호출 · 배정, 채우기, 출고 이동, 교환, 끝 상태 · 진행 보고, contract 1.0 팔 보고(일시 중지 · 재시작 · 작업대 막힘 · 늦은 결과 무시), ArmFault 보류(HELD), 서비스 정지를 구현한다.
2차(이번 계획 밖)는 격리, 치움, 복귀, 늦은 결과의 수량 반영, 이동 명령(trip command), 강제 출고(`force_ship`)로 5장의 해당 부분은 아직 뼈대다.
- `on_force_ship`은 늘 `(False, NOT_IMPLEMENTED)`를 돌려주고, manager는 그 답을 그대로 넘긴다.
- `/fleet/force_ship` 서버는 `ForceShip.srv`와 contract의 `force_ship` 항목이 둘 다 있어야 열린다. 지금은 둘 다 없다(주문 담당 몫).

## 1. 바꾸면 안 되는 접점

| 구분 | 이름 |
|---|---|
| 생성 | `OrderOrchestrator(stations, params, robots, clock)`. stations에서 action이 FILL인 곳이 WORK, SWAP인 곳이 OUTBOUND |
| 입력 | `on_order_board(run_mode, orders)`, `on_dispatch_feedback(goal_id, robot_id, phase)`, `on_dispatch_result(goal_id, success, robot_id, reason="")`, `on_arm_report(report)`, `on_robot_removed(robot)`, `on_robot_reinstated(robot, basket)`, `on_robot_isolated(robot, reason)`, `on_force_ship(order_id) -> (accepted, reason)`, `step(robot)` |
| 출력 | `take_output()`: 쌓인 명령 목록을 주고 비운다 |
| 읽기 속성 | `service_paused`: 마지막 `run_mode`가 `PAUSE`이면 True, `RUN` · `STOP`이면 False |
| 조회 | `order_of(robot)`: 묶인 주문 id, 없으면 `""`. `robot_view(robot)`: `stage`, `basket`, `order`, `placed`. 모르는 로봇이면 `{}`. `shipped`: 내보낸 주문 수 |
| 내는 명령 | DispatchCmd, CancelDispatchCmd, ReleaseCmd, ArmFillCmd, ArmSwapCmd, ArmResetCmd, ReportOrderCmd(HELD일 때 `hold_reason`), RobotFaultCmd(출고 배차 재시도 한도 초과만), EventOut, 그리고 고장 처리용 ArmFailedCmd, ArmStopCmd, ArmDropCmd |
| 받는 팔 보고 | ArmDeclined, ArmFillResult, ArmSwapResult, ArmLost, ArmLate, ArmFault |

모르는 로봇 이름은 무시한다. 다만 열린 WORK 호출의 결과는 로봇과 상관없이 그 호출을 닫고 호출 수를 다시 맞춘다.

## 2. 로봇 한 대의 기록

| 칸 | 값 |
|---|---|
| stage | `""`(쉼), `FILLING`, `SWAPPING` |
| basket | 처음 `EMPTY`. 채운 뒤 `FULL`, 찬 바구니를 뺀 뒤 `NONE` |
| order | 묶인 주문: id, items, placed(담긴 수), missing(못 담은 수) |
| last_order_id | 마지막으로 묶은 주문 id. 교환 명령에 쓴다 |
| outbound_goal | 답을 기다리는 OUTBOUND 호출 id |
| dispatch_failures, dispatch_retry_at, dispatch_reason | OUTBOUND 호출 실패 횟수, 다시 보낼 시각, 마지막 실패 이유 |
| asked | 답을 기다리는 팔 작업 종류(`fill`, `swap`), 없으면 `""` |
| bound | 주문을 묶을 때마다 1씩 오르는 번호. 늦은 팔 결과가 같은 묶음인지 가린다 |
| isolated | 격리 이유, 아니면 `""` |
| held | 팔 고장으로 작업대에 붙잡혔으면 `ARM_FAULT`, 아니면 `""` |

코어 전체로는 아래를 둔다.
- 주문판 목록
- 최종 보고(SHIPPED, PARTIAL, CANCELED)한 주문 id 집합
- 보류 중인 주문: 주문 id → (로봇, 사유 `ROBOT_FAULT` 또는 `ARM_FAULT`)
- 용량 초과를 이미 알린 주문 id 집합
- 열어 둔 WORK 호출 id 목록, 결과를 잃은 팔 작업 목록
호출 id는 코어 안의 일련번호다.

## 3. 단계별 흐름

1. 쉼
   - held인 로봇은 `step`에서 아무것도 하지 않는다(강제 출고나 치움을 기다림).
   - `step`에서 stage가 `""`이고, 격리가 아니고, 답을 기다리는 OUTBOUND 호출과 재시도 대기가 없을 때
     - basket이 `NONE`이고 주문이 없으면 빈 바구니를 받으러 `DispatchCmd(id, OUTBOUND, robot)`를 낸다(5단계와 같은 길).
     - 그 밖에는 아무것도 하지 않는다. 다음 일은 교통 층이 열린 호출에 이 로봇을 고를 때 온다.
2. 주문 묶음
   - `on_order_board(run_mode, orders)`: 주문판을 통째로 바꾼다.
     - 최종 보고 집합, 보류 목록, 용량 초과 집합은 새 주문판에 있는 id만 남긴다.
     - held 로봇의 주문이 주문판에서 빠지면 로봇의 주문만 비운다. 로봇은 계속 붙잡혀 있고 운영자의 치움으로 풀린다.
     - 품목 수 합이 `basket_capacity`(4)보다 큰 주문은 배정하지 않는다. ReportOrder는 보내지 않는다.
       처음 넘을 때 `EventOut(WARN, ORDER_OVER_CAPACITY, "O-xxx: N items exceed the basket capacity of 4")`를 한 번 낸다.
       그 id가 주문판에서 빠지거나 용량 안으로 줄면 기록을 지운다. 그 뒤 다시 넘으면 다시 낸다.
   - 대기 주문 = 주문판 순서대로, 아직 묶이지 않았고, 최종 보고도 보류도 아니고, 용량 안인 주문. 목록 순서가 곧 우선순위다.
   - 열린 호출 맞추기: 열린 호출 수가 대기 주문 수보다 적으면 로봇 이름 없는 `DispatchCmd(id, WORK)`를 더 내고,
     많으면 가장 나중에 연 것부터 `CancelDispatchCmd(id)`를 낸다.
   - 실제로 주문을 로봇에 묶는 때는 3단계가 끝난 뒤다. `on_dispatch_result`가 열린 호출의 id로 오면
     - 그 호출을 목록에서 뺀다.
     - 성공이고 아는 로봇이면: 대기 주문이 없으면 `ReleaseCmd(robot)`. 있으면 맨 앞 주문을 묶고 bound를 올리고
       stage를 `FILLING`으로 두고 `EventOut(INFO, ORDER_BOUND, 주문 id, robot)`을 낸다.
     - 성공이든 실패든 열린 호출을 다시 맞춘다. 실패한 호출은 새 호출로 다시 열린다.
   - 로봇 하나에는 주문 하나만 묶인다. 주문판에서 빠진 주문도 이미 묶였으면 끝까지 진행한다.
     예외: held 로봇의 주문은 주문판에서 빠지면 로봇에서 떼어 낸다(위 `on_order_board`).
3. WORK로 이동
   - 로봇 고르기, 이동, 도킹은 교통 층이 한다. 주문 코어는 결과만 기다린다.
   - `on_dispatch_feedback`의 phase가 `NO_ROBOT`이면 `EventOut(WARN, NO_ROBOT_AVAILABLE, "[대기 주문 id] wait and no robot can take them")`.
4. 채우기 (`step`, stage `FILLING`)
   - asked가 있으면 기다린다.
   - 남은 것 = items에서 placed와 missing을 뺀 것. 남은 것이 있으면 asked=`fill`, `ArmFillCmd(robot, WORK, 주문 id, 남은 것)`.
   - 진행 보고: 주문마다 첫 `ArmFillCmd`에서 `ReportTripCmd(FILLING)`을 한 번만 낸다(재요청에서는 다시 내지 않음). 담기 일시 중지는 `FILLING_PAUSE`, 찬 바구니를 싣고 출고로 갈 때는 `TRANSPORTING`을 낸다.
     `missing`은 실제로 못 담은 것(`order.missing`)만 싣고, 남은 상품을 `missing`으로 싣지 않는다.
   - 남은 것이 없으면 마무리: `ArmResetCmd(robot)`, missing = items - placed, 그다음
     - 하나도 못 담았으면 CANCELED 보고("nothing could be placed, missing ..."), stage `""`, `ReleaseCmd`.
     - 그 밖(다 담았거나 일부만 담았거나)이면 basket `FULL`, stage `""`, 5단계로 간다. 모자라도 늘 내보낸다.
       v2.4에는 `ship_partial`이 없다(팀 params의 `ship_partial`을 지울지는 주문 담당이 정한다).
   - 팔 답(`on_arm_report`):
     - ArmDeclined: asked를 비운다. 다음 `step`에서 다시 묻는다.
     - ArmFillResult: asked를 비우고 placed에 더한다. 격리된 로봇이면 5절 "격리 뒤 판단"으로 간다.
       성공이면 missing = items - placed. 실패면 `ArmFailedCmd(robot)`.
       횟수 세기와 `arm_retry_wait_s` 대기는 ArmJobs가 한다. `move_retry_limit`을 넘거나 답이 없으면 ArmJobs가 ArmFault를 보낸다(5절).
5. OUTBOUND로 이동
   - `DispatchCmd(새 id, OUTBOUND, robot)`를 내고 outbound_goal에 둔다.
   - `on_dispatch_result`가 outbound_goal로 오면 outbound_goal을 비우고
     - 성공: stage `SWAPPING`, 실패 횟수 0.
     - 실패: 횟수 1 증가, 다시 보낼 시각 = 지금 + `dispatch_retry_wait_s`(2.0 s), 이유 저장.
   - `step`에서 다시 보낼 시각이 되면: 실패 횟수가 `dispatch_retry_limit`(3)보다 크면 `RobotFaultCmd`와
     `EventOut(ERROR, DISPATCH_FAILED)`만 내고 끝낸다. 아니면 `EventOut(WARN, DISPATCH_RETRY)`와 새 id로 다시 보낸다.
6. 교환 (`step`, stage `SWAPPING`)
   - asked가 있으면 기다린다. basket이 `EMPTY`면 stage `""`, `ReleaseCmd`.
   - 아니면 asked=`swap`, `ArmSwapCmd(robot, OUTBOUND, last_order_id, take_full=(basket이 FULL), put_empty=True)`.
   - ArmSwapResult(took_full, put_empty_done): asked를 비운 뒤 차례로
     - took_full이고 basket이 `FULL`이면 basket `NONE`, 7단계 보고.
     - 격리된 로봇이면 아래 "격리"의 되돌리기를 하고 끝낸다.
     - put_empty_done이고 basket이 `NONE`이면 basket `EMPTY`, `ArmResetCmd`, stage `""`, `ReleaseCmd`. 1단계로 간다.
     - 그 밖에는 `ArmFailedCmd(robot)`. 다음 `step`에서 남은 일만 다시 묻는다(이미 뺐으면 take_full=False).
     - 재시도 한도를 넘으면 ArmJobs가 ArmFault를 보낸다(5절).
7. 보고
   - shipped를 1 올리고 `ReportOrderCmd(주문 id, robot, 상태, placed, missing, "")`. missing이 있으면 `PARTIAL`, 없으면 `SHIPPED`.
   - 보고는 찬 바구니를 뺀 순간에 나간다. 빈 바구니 놓기는 그 뒤다.
   - 최종 보고(SHIPPED, PARTIAL, CANCELED)는 주문 id를 최종 보고 집합에 넣고, 보류 목록에서 빼고, 로봇의 주문과 held를 비운다.
     그 주문은 주문판에 남아 있어도 다시 하지 않는다.
   - HELD 보고는 `ReportOrderCmd(주문 id, robot, HELD, placed, missing, detail, hold_reason)`이다.
     missing = items - placed. 주문 id를 보류 목록에 넣는다. HELD 뒤에도 같은 주문의 최종 보고가 한 번 더 나갈 수 있다.

## 4. 운행 모드

- 서비스 정지(`PAUSE`, 설계 4.8, 회의 정리 2026-11-27 8번): 이미 묶인 주문은 채우기, 출고 이동, 교환, 끝 상태 보고까지 그대로 진행한다. OUTBOUND 재시도도 한다.
  새 일은 막는다. 열린 WORK 호출은 0으로 맞추고(열린 것은 `CancelDispatchCmd`), 그 사이 성공한 WORK 호출 결과가 와도 묶지 않고 `ReleaseCmd`를 낸다.
  바구니 없는 쉬는 로봇이 빈 바구니를 받으러 가는 것도 하지 않는다. `RUN`으로 돌아오면 열린 호출을 다시 맞춘다.
- 모드의 효과는 바깥에서 난다. FleetManager는 교통 층이 `RUN`이고 모든 로봇이 지도 위 위치를 보고했을 때만, 일할 수 있는 로봇마다
  `step` → 교통 `step` → `step` 순서로 부른다. `STOP`에서는 `step`이 불리지 않고, `PAUSE`에서는 주문 코어에 `service_paused`가 있어 불린다(`manager.py` 347행 부근).
- `STOP`으로 바뀌면 ArmJobs가 진행 중인 팔 goal을 취소한다. 이렇게 취소된 결과는 시도로 세지 않고, `RUN`이 오면 다시 묻는다.
- 주문판 검사는 FleetManager가 한다. 모드가 틀리면 주문판 전체를 버리고, 주문이 틀리면 모드만 받고 주문은 앞의 것을 쓴다.

## 5. 고장 처리

- 이동 실패 재시도: OUTBOUND 호출 실패는 5단계대로 다시 보낸다. 이동 goal 자체의 재시도는 교통 층의 일이다.
- 로봇 치움 `on_robot_removed(robot)` (운영자의 구역 풀기가 받아들여졌을 때):
  1. 격리 표시를 지운다. asked가 있으면 `ArmDropCmd(robot, False, 주문 id, bound)`를 내고 asked를 비운다.
     기다리던 팔 작업은 버리고, 그때까지 담은 것으로 판단한다.
  2. 주문이 묶여 있고 담은 것이 있으면 HELD(`ROBOT_FAULT`, "robot cleared") 보고를 하고 로봇의 주문을 비운다. 다시 배정하지 않는다.
  3. 주문이 묶여 있고 담은 것이 없으면 `EventOut(INFO, ORDER_REQUEUED, ...)`을 내고 주문을 놓는다. 대기 주문으로 돌아간다.
  4. held, stage `""`, outbound_goal과 OUTBOUND 실패 기록을 지우고 열린 호출을 다시 맞춘다. basket은 그대로 둔다.
- 격리 `on_robot_isolated(robot, reason)` (교통 층이 주행 고장이나 무응답으로 격리하면 온다. 팔 고장으로는 오지 않는다):
  1. isolated에 이유를 적는다.
  2. asked가 비어 있고, 결과를 잃은 팔 작업(ARM_NO_RESULT)이 남아 있으면 그 늦은 결과나 치움을 기다린다.
     남은 작업으로 치는 것: 이 로봇의 작업이고, 치움으로 버린 것이 아니고, 주문이 묶여 있을 때 잃은 것이고, 그 주문 id와 bound가 지금도 같은 것.
  3. 교환을 묻는 중이고 주문이 있으면 `ArmStopCmd(robot)`만 내고 교환 답을 기다린다.
  4. 채우기를 묻는 중이면 채우기 결과를 기다린다.
  5. 그 밖에 asked가 있으면 `ArmDropCmd(robot, True, 주문 id, bound)`를 내고 asked를 비운다.
  6. 2~4번에서 기다리지 않으면 바로 "격리 뒤 판단"을 한다(asked가 없고 남은 작업도 없을 때 포함).
- 격리 뒤 판단:
  - 들어오는 곳과 조건
    - 격리 중에 4단계 채우기 결과나 6단계 교환 결과가 오면 곧바로 한다.
    - ArmFault나 늦은 결과 뒤에는 asked가 없고 위 2번의 남은 작업도 없을 때만 한다. 남아 있으면 계속 기다린다.
      예: 답이 없어 ArmLost 다음 ArmFault가 오면, 그 늦은 결과나 치움을 기다린다.
  - 주문이 있고 담은 것이 있으면 HELD(`ROBOT_FAULT`, "robot isolated: 이유") 보고를 하고 로봇의 주문을 비운다. 되돌리지 않는다.
  - 주문이 있고 담은 것이 없으면 ORDER_REQUEUED를 내고 놓는다(예전과 같음).
  - 그다음 held를 지우고 basket `NONE`, stage `""`, OUTBOUND 기록을 지우고 열린 호출을 다시 맞춘다.
- 팔 고장 ArmFault(robot, kind, server, reason) (ArmJobs가 재시도 한도를 넘거나 답이 없을 때 보낸다):
  - 교통 층에는 알리지 않는다. 그래서 Pode는 격리되지 않고 사용 가능 상태로 작업대 구역을 잡고 있다.
  - asked를 비운다. 이미 격리된 로봇이면 "격리 뒤 판단"의 조건을 따진다(남은 작업이 있으면 기다린다).
  - 아니면 held = `ARM_FAULT`. 주문이 있으면 HELD(`ARM_FAULT`, reason) 보고를 한다. 주문은 로봇에 묶인 채로 둔다.
  - 출고 보고 뒤 빈 바구니를 넣다가 고장 나면 주문이 없으므로 Pode만 붙잡는다.
- 강제 출고 `on_force_ship(order_id) -> (accepted, reason)` (운영자가 `/fleet/force_ship`을 부르면 manager를 거쳐 온다):
  - 보류 목록에 없으면 거절. 주문판이나 로봇에 있는 주문이면 `NOT_HELD`, 없으면 `UNKNOWN_ORDER`.
  - 사유가 `ROBOT_FAULT`면 `ROBOT_FAULT`로 거절한다.
  - 로봇이 격리됐거나, held가 아니거나, 그 주문이 더는 로봇에 없거나, stage가 FILLING·SWAPPING이 아니면 `NOT_HELD`로 거절한다.
  - 받아들이면 보류 목록에서 빼고 held를 지운다.
    - FILLING이면 4단계 마무리를 한다. 담은 것이 있으면 출고로 가고(5단계), 없으면 CANCELED 보고 뒤 놓아 준다.
    - SWAPPING이면 `ArmResetCmd(robot)`. 다음 `step`에서 교환을 다시 묻는다.
  - 돌려주는 값은 `(True, "")` 또는 `(False, 사유)`. 사유 상수는 commands의 `FORCE_*`다.
- 다시 투입 `on_robot_reinstated(robot, basket)`: 로봇 기록을 새로 만들고 basket(`EMPTY` 또는 `NONE`)을 넣는다.
  `ArmResetCmd(robot, forget=True)`. basket이 `NONE`이면 1단계대로 빈 바구니를 받으러 간다.
- 늦은 팔 결과:
  1. ArmLost가 오면 goal id별로 잃은 작업을 적는다(로봇, 서버, 종류, 주문 id, bound, 치움 여부, 격리 여부).
     치움에서 온 것이 아니면 asked를 비우고 지금 주문과 bound를 적는다.
  2. ArmLate(goal id, 종류)가 오면 같은 종류의 잃은 작업을 한 번만 꺼낸다. 없으면 무시한다.
     치움에서 온 작업이고 팔이 아무것도 안 했으면 조용히 끝낸다.
  3. 아니면 `EventOut(WARN, ARM_LATE_RESULT, ...)`. 주문 id와 bound가 지금도 같으면
     채우기는 placed에 더하고 missing을 다시 계산한다. 교환은 took_full이고 basket이 `FULL`일 때 basket을 `EMPTY`(빈 바구니도 놓음) 또는 `NONE`으로 두고 7단계 보고를 한다.
     격리된 로봇이면 그 뒤 "격리 뒤 판단"의 조건을 따진다(asked가 없고 남은 작업도 없을 때만 판단).
  4. 늦은 결과로는 `ReleaseCmd`를 내지 않는다. 주문이 이미 다른 로봇으로 돌아갔으면 두 번 채우거나 내보낼 수 있다는 경고만 낸다.

## 6. 구현 확인

1. 가짜 central과 가짜 로봇, 가짜 팔로 `real_two_orders`를 띄운다. 이 시나리오에는 pode1, pode2만 있어 로봇 목록을 맞춘다.
   `ros2 launch iponic_bringup pc.launch.py use_fake:=true scenario:=real_two_orders map_file:=v7 robots:="pode1 pode2" fake_robots:="pode1 pode2"`
2. 주문 R-001부터 R-004까지 네 건이 20 s 간격으로 들어온다. `ros2 topic echo /central/events`에서
   `ORDER_SHIPPED` 네 건이 나오면 된다. `ORDER_PARTIAL`, `ORDER_HELD`는 0건이어야 한다.
3. 보류와 강제 출고를 확인하려면 가짜 central부터 v2.4에 맞춰야 한다.
   - 지금 팀의 가짜 central은 CANCELED를 거절하고 HELD를 끝으로 본다.
   - 개인 레포의 `arm_fault` 시나리오가 예다: HELD(ARM_FAULT) 뒤 force_ship이 오면 PARTIAL로 끝난다.
