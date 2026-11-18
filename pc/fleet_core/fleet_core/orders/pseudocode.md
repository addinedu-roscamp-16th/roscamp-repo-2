# 주문 코어(OrderOrchestrator) 의사코드

주문 코어는 주문판을 받아 로봇에 주문을 하나씩 묶고, 채우기와 교환을 거쳐 결과를 보고한다.
길, 구역, 도킹은 교통 층이 맡고 팔 goal의 시간 초과와 재시도는 ArmJobs가 맡는다.
주문 코어는 입력 함수로만 듣고 `take_output()`으로만 말한다. 명령은 모두 `common/commands.py`에 있다.

## 1. 바꾸면 안 되는 접점

| 구분 | 이름 |
|---|---|
| 생성 | `OrderOrchestrator(stations, params, robots, clock)`. stations에서 action이 FILL인 곳이 WORK, SWAP인 곳이 OUTBOUND |
| 입력 | `on_order_board(run_mode, orders)`, `on_dispatch_feedback(goal_id, robot_id, phase)`, `on_dispatch_result(goal_id, success, robot_id, reason="")`, `on_arm_report(report)`, `on_robot_removed(robot)`, `on_robot_reinstated(robot, basket)`, `on_robot_isolated(robot, reason)`, `step(robot)` |
| 출력 | `take_output()`: 쌓인 명령 목록을 주고 비운다 |
| 조회 | `order_of(robot)`: 묶인 주문 id, 없으면 `""`. `robot_view(robot)`: `stage`, `basket`, `order`, `placed`. 모르는 로봇이면 `{}`. `shipped`: 내보낸 주문 수 |
| 내는 명령 | DispatchCmd, CancelDispatchCmd, ReleaseCmd, ArmFillCmd, ArmSwapCmd, ArmResetCmd, ReportOrderCmd, RobotFaultCmd, EventOut, 그리고 고장 처리용 ArmFailedCmd, ArmStopCmd, ArmDropCmd |

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

코어 전체로는 주문판 목록, 보고한 주문 id 집합, 열어 둔 WORK 호출 id 목록, 결과를 잃은 팔 작업 목록을 둔다.
호출 id는 코어 안의 일련번호다.

## 3. 단계별 흐름

1. 쉼
   - `step`에서 stage가 `""`이고, 격리가 아니고, 답을 기다리는 OUTBOUND 호출과 재시도 대기가 없을 때
     - basket이 `NONE`이고 주문이 없으면 빈 바구니를 받으러 `DispatchCmd(id, OUTBOUND, robot)`를 낸다(5단계와 같은 길).
     - 그 밖에는 아무것도 하지 않는다. 다음 일은 교통 층이 열린 호출에 이 로봇을 고를 때 온다.
2. 주문 묶음
   - `on_order_board(run_mode, orders)`: 주문판을 통째로 바꾼다. 보고한 id 집합은 새 주문판에 있는 id만 남긴다.
   - 대기 주문 = 주문판 순서대로, 아직 묶이지 않았고 보고하지 않은 주문. 목록 순서가 곧 우선순위다.
   - 열린 호출 맞추기: 열린 호출 수가 대기 주문 수보다 적으면 로봇 이름 없는 `DispatchCmd(id, WORK)`를 더 내고,
     많으면 가장 나중에 연 것부터 `CancelDispatchCmd(id)`를 낸다.
   - 실제로 주문을 로봇에 묶는 때는 3단계가 끝난 뒤다. `on_dispatch_result`가 열린 호출의 id로 오면
     - 그 호출을 목록에서 뺀다.
     - 성공이고 아는 로봇이면: 대기 주문이 없으면 `ReleaseCmd(robot)`. 있으면 맨 앞 주문을 묶고 bound를 올리고
       stage를 `FILLING`으로 두고 `EventOut(INFO, ORDER_BOUND, 주문 id, robot)`을 낸다.
     - 성공이든 실패든 열린 호출을 다시 맞춘다. 실패한 호출은 새 호출로 다시 열린다.
   - 로봇 하나에는 주문 하나만 묶인다. 주문판에서 빠진 주문도 이미 묶였으면 끝까지 진행한다.
3. WORK로 이동
   - 로봇 고르기, 이동, 도킹은 교통 층이 한다. 주문 코어는 결과만 기다린다.
   - `on_dispatch_feedback`의 phase가 `NO_ROBOT`이면 `EventOut(WARN, NO_ROBOT_AVAILABLE, "[대기 주문 id] wait and no robot can take them")`.
4. 채우기 (`step`, stage `FILLING`)
   - asked가 있으면 기다린다.
   - 품목 수 합이 `basket_capacity`(4)보다 크면 팔을 부르지 않고 HELD 보고("N items exceed the basket capacity of 4"),
     stage를 `""`로 두고 `ReleaseCmd`.
   - 남은 것 = items에서 placed와 missing을 뺀 것. 남은 것이 있으면 asked=`fill`, `ArmFillCmd(robot, WORK, 주문 id, 남은 것)`.
   - 남은 것이 없으면 마무리: `ArmResetCmd(robot)`, missing = items - placed, 그다음
     - 하나도 못 담았으면 HELD 보고("nothing could be placed, missing ..."), stage `""`, `ReleaseCmd`.
     - 모자라고 `ship_partial`이 거짓이면 basket `FULL`, stage `""`, HELD 보고,
       `RobotFaultCmd(robot, detail)`, `EventOut(ERROR, FILL_FAILED, detail)`. 놓아 주지 않는다.
     - 그 밖(다 담았거나, 모자라도 `ship_partial`이 참)이면 basket `FULL`, stage `""`, 5단계로 간다.
   - 팔 답(`on_arm_report`):
     - ArmDeclined: asked를 비운다. 다음 `step`에서 다시 묻는다.
     - ArmFillResult: asked를 비우고 placed에 더한다. 성공이면 missing = items - placed.
       실패면 `ArmFailedCmd(robot)`. 횟수 세기, `arm_retry_wait_s` 대기, `move_retry_limit` 초과 시 고장은 ArmJobs가 한다.
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
7. 보고
   - shipped를 1 올리고 `ReportOrderCmd(주문 id, robot, 상태, placed, missing, "")`. missing이 있으면 `PARTIAL`, 없으면 `SHIPPED`.
   - 보고는 찬 바구니를 뺀 순간에 나간다. 빈 바구니 놓기는 그 뒤다.
   - 모든 보고(HELD 포함)는 주문 id를 보고 집합에 넣고 로봇의 주문을 비운다. 그 주문은 주문판에 남아 있어도 다시 하지 않는다.

## 4. 운행 모드

- 주문 코어는 run_mode를 저장만 하고 `RUN`, `PAUSE`, `STOP`을 가리지 않는다. 호출 열기와 묶기는 모드와 상관없다.
- 모드의 효과는 바깥에서 난다. FleetManager는 교통 층이 `RUN`이고 모든 로봇이 지도 위 위치를 보고했을 때만, 일할 수 있는 로봇마다
  `step` → 교통 `step` → `step` 순서로 부른다. `PAUSE`와 `STOP`에서는 `step`이 불리지 않는다.
- `STOP`으로 바뀌면 ArmJobs가 진행 중인 팔 goal을 취소한다. 이렇게 취소된 결과는 시도로 세지 않고, `RUN`이 오면 다시 묻는다.
- 주문판 검사는 FleetManager가 한다. 모드가 틀리면 주문판 전체를 버리고, 주문이 틀리면 모드만 받고 주문은 앞의 것을 쓴다.

## 5. 고장 처리

- 이동 실패 재시도: OUTBOUND 호출 실패는 5단계대로 다시 보낸다. 이동 goal 자체의 재시도는 교통 층의 일이다.
- 로봇 치움 `on_robot_removed(robot)` (운영자의 구역 풀기가 받아들여졌을 때):
  1. 격리 표시를 지운다. asked가 있으면 `ArmDropCmd(robot, False, 주문 id, bound)`를 내고 asked를 비운다.
  2. 주문이 묶여 있으면 `EventOut(INFO, ORDER_REQUEUED, ...)`을 내고 주문을 놓는다. 보고하지 않았으니 대기 주문으로 돌아간다.
  3. stage `""`, outbound_goal과 OUTBOUND 실패 기록을 지우고 열린 호출을 다시 맞춘다. basket은 그대로 둔다.
- 격리 `on_robot_isolated(robot, reason)` (교통 층이 `RobotFaultCmd`를 받거나 스스로 고장을 잡으면 온다):
  1. 결과를 잃은 팔 작업(ARM_NO_RESULT)이 이 로봇의 지금 묶음에 남아 있으면 무시한다. 주문은 운영자가 치울 때까지 묶여 있다.
  2. isolated에 이유를 적는다. 교환을 묻는 중이고 주문이 있으면 `ArmStopCmd(robot)`만 내고 교환 답을 기다린다.
  3. 그 밖에 asked가 있으면 `ArmDropCmd(robot, True, 주문 id, bound)`.
  4. 되돌리기: 주문이 있으면 ORDER_REQUEUED를 내고 놓는다. basket `NONE`, stage `""`, OUTBOUND 기록을 지우고 열린 호출을 다시 맞춘다.
- 다시 투입 `on_robot_reinstated(robot, basket)`: 로봇 기록을 새로 만들고 basket(`EMPTY` 또는 `NONE`)을 넣는다.
  `ArmResetCmd(robot, forget=True)`. basket이 `NONE`이면 1단계대로 빈 바구니를 받으러 간다.
- 늦은 팔 결과:
  1. ArmLost가 오면 goal id별로 잃은 작업을 적는다(로봇, 서버, 종류, 주문 id, bound, 치움 여부, 격리 여부).
     치움에서 온 것이 아니면 asked를 비우고 지금 주문과 bound를 적는다.
  2. ArmLate(goal id, 종류)가 오면 같은 종류의 잃은 작업을 한 번만 꺼낸다. 없으면 무시한다.
     치움에서 온 작업이고 팔이 아무것도 안 했으면 조용히 끝낸다.
  3. 아니면 `EventOut(WARN, ARM_LATE_RESULT, ...)`. 주문 id와 bound가 지금도 같으면
     채우기는 placed에 더하고, 교환은 took_full이고 basket이 `FULL`일 때 basket을 `EMPTY`(빈 바구니도 놓음) 또는 `NONE`으로 두고 7단계 보고를 한다.
  4. 늦은 결과로는 `ReleaseCmd`를 내지 않는다. 주문이 이미 다른 로봇으로 돌아갔으면 두 번 채우거나 내보낼 수 있다는 경고만 낸다.

## 6. 구현 확인

1. 가짜 central과 가짜 로봇, 가짜 팔로 `real_two_orders`를 띄운다. 이 시나리오에는 pode1, pode2만 있어 로봇 목록을 맞춘다.
   `ros2 launch iponic_bringup pc.launch.py use_fake:=true scenario:=real_two_orders map_file:=v7 robots:="pode1 pode2" fake_robots:="pode1 pode2"`
2. 주문 R-001부터 R-004까지 네 건이 20 s 간격으로 들어온다. `ros2 topic echo /central/events`에서
   `ORDER_SHIPPED` 네 건이 나오면 된다. `ORDER_PARTIAL`, `ORDER_HELD`는 0건이어야 한다.
