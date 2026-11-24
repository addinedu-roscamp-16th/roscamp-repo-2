# ROS 2 QoS 호환과 통신 안내

- 버전 v2.1 (2026-11-24) · 대상: ROS 2를 처음 쓰는 개발자 · ROS 2 Jazzy, rclpy 기준 (프로젝트는 Jazzy, IR-80. Humble은 다른 곳만 표시)
- **원본은 이 파일입니다.** Confluence "ROS2 QoS 호환과 통신 안내"(https://iphonic.atlassian.net/wiki/x/EwBV)는 이 파일을 옮긴 것입니다. 고칠 때는 이 파일을 고치고 맨 아래 변경 이력에 적은 뒤 Confluence에 다시 옮깁니다.
- 이 문서는 ROS 2 통신의 개념과 사례를 설명하는 **해설서**입니다. 우리 프로젝트의 규칙은 [통신 규칙](통신_규칙.md)(`IR-nn`)이 정하고, 이름 · 타입 · QoS의 원본은 `interfaces/fleet_interfaces/contract.yaml`입니다. **두 문서가 다르면 통신 규칙을 따릅니다.**
- v2.0부터 본문의 바른 예 · 기억할 규칙을 통신 규칙에 맞췄습니다. 일반론과 프로젝트 방식이 다른 곳에는 "이 프로젝트에서는"을 적었습니다.

**이 문서를 읽는 법**

- 처음이면 1부 항목 1 ~ 10을 순서대로 읽고, 코드를 쓰기 전에 2부 체크리스트를 확인하세요. 3부 기술 상세는 궁금할 때 찾아봅니다.
- 항목마다 **사례 → 잘못된 예 → 바른 예 → 기억할 규칙** 순서입니다.
- `IR-nn`은 [통신 규칙](통신_규칙.md)의 규칙 번호이고, contract는 `interfaces/fleet_interfaces/contract.yaml`을 줄여 부른 말입니다.
- 예제는 핵심 패턴을 보여 주는 코드 조각입니다. 그대로는 실행되지 않습니다.
  - `self.outbox` · `self.db` · `contract.qos()` · `contract.topic()` 같은 도우미는 설명용 이름입니다. contract를 읽는 실제 코드는 예를 들어 `pode/pode_fsm/pode_fsm/contract.py`를 봅니다.
  - `/robot1/...` · `/scan` · `/stop_robot`처럼 우리 인터페이스가 아닌 이름도 설명용입니다. 실제 이름 · 타입은 [인터페이스 명세](인터페이스_명세.md)와 contract를 봅니다.
  - `ReportEvent` · `ReportTrip` · `ReportCommand` · `SetLock`은 인터페이스 명세의 **신규** 정의입니다. contract 0.6에는 아직 없고, 넣는 일정은 명세와 주문 흐름 1차 계획을 따릅니다.
- 이 문서에서 **관제**는 fleet, **서비스 브리지**(줄여서 브리지)는 웹과 ROS를 잇는 우리 노드, **웹 브리지**는 rosbridge 같은 일반 도구를 뜻합니다.
- 모르는 용어는 4부 용어집을 보세요.

### 목차

- **1부. 사례로 배우기**
  1. 놓치면 안 되는 알림 · 보고는 서비스로 보냅니다
  2. 주기 상태와 놓치면 안 되는 진행을 나눕니다
  3. 서비스 QoS는 바꾸지 않습니다
  4. 콜백 안에서 call()을 쓰지 않습니다
  5. callback group을 나눕니다
  6. QoS가 안 맞으면 연결이 안 됩니다
  7. 알림 · 명령 메시지는 작게 (목표 64 KB 이하)
  8. 작업 지시는 액션으로 보냅니다
  9. 느린 구독자 하나가 전체를 늦춥니다
  10. Wi-Fi에서는 보내는 양을 줄입니다
- **2부. 체크리스트**
- **3부. 기술 상세** (A ~ G)
- **4부. 용어집**
- **5부. 참고 자료**
- 변경 이력

## 1부. 사례로 배우기

### 1. 놓치면 안 되는 알림 · 보고는 서비스로 보냅니다

**사례**

- 로봇이 "배터리 부족" 알림을 보냈습니다.
- 그런데 관리자 페이지에는 나오지 않았습니다.
- 그 순간 받는 쪽 노드가 재시작 중이었습니다.

#### ❌ 잘못된 예

```py
self.pub = self.create_publisher(Alert, '/alerts', 10)
self.pub.publish(msg)   # 보내고 끝
```

- 숫자 `10`만 주면 기본 QoS가 쓰입니다.
- 기본 QoS는 VOLATILE입니다. 지난 메시지를 남기지 않습니다.
- 받는 쪽이 꺼져 있던 동안의 알림은 그대로 사라집니다. 받는 쪽이 막 켜져서 아직 연결되지 않은 순간에도 사라집니다. 사전 시험에서는 같은 PC에서도 막 켜진 구독자가 첫 메시지를 받은 경우가 절반 정도였습니다(IR-11).
- 받았는지 확인하는 방법도 없습니다.

#### ✅ 바른 예 (이 프로젝트: `ReportEvent` 서비스)

```py
import random, threading, uuid
from rclpy.duration import Duration

TIMEOUT = Duration(seconds=3.0)          # 응답 대기 시간 (IR-40 표의 Report* 줄)

# ----- 보내는 쪽 (예: fleet 노드의 __init__ 안) -----
#   self.event_cli = self.create_client(ReportEvent, '/bridge/report_event', callback_group=...)
#   self.outbox = {}                      # event_id → {'req', 'future', 'sent_at', 'backoff'}
#   self.lock = threading.Lock()          # 타이머와 응답 콜백이 outbox를 함께 씀
#   self.create_timer(0.5, self.on_retry_timer, callback_group=...)

def send_event(self, level, code, robot_id='', trip_id='', detail=''):
    req = ReportEvent.Request(
        event_id=str(uuid.uuid4()),      # 재시작해도 겹치지 않는 번호
        level=level, source=self.get_name(), code=code,
        robot_id=robot_id, trip_id=trip_id, detail=detail)
    with self.lock:
        self.outbox[req.event_id] = {'req': req, 'future': None, 'backoff': 1.0}
    # 보내는 쪽 재시작까지 막으려면 여기서 파일에도 저장

def on_retry_timer(self):                # 보내기 · 다시 보내기를 이 타이머 한 곳에서
    if not self.event_cli.service_is_ready():
        return
    now = self.get_clock().now()
    with self.lock:
        for eid, item in self.outbox.items():
            fut = item['future']
            if fut is not None and not fut.done():
                if now - item['sent_at'] < TIMEOUT:
                    continue                                   # 아직 응답을 기다리는 중
                self.event_cli.remove_pending_request(fut)     # 응답 대기 시간이 지남
                item['next_at'] = now + Duration(seconds=item['backoff'] * random.uniform(0.8, 1.2))
                item['backoff'] = min(item['backoff'] * 2, 10.0)   # 1 → 2 → 4초, 최대 10초 (IR-40 · IR-41)
                item['future'] = None
                continue
            if item.get('next_at') and now < item['next_at']:
                continue
            item['sent_at'] = now
            item['future'] = self.event_cli.call_async(item['req'])   # 같은 event_id로
            item['future'].add_done_callback(lambda f, e=eid: self.on_event_reply(f, e))

def on_event_reply(self, fut, event_id):
    resp = fut.result()
    if resp is not None and resp.accepted:
        with self.lock:
            self.outbox.pop(event_id, None)

# ----- 받는 쪽 (서비스 브리지) -----
def on_report_event(self, req, resp):
    self.db.save_if_new(req.event_id, req)   # 이미 있으면 다시 저장하지 않음
    resp.accepted = True                      # DB에 쓴 뒤 응답 (IR-12)
    return resp
```

- 서비스는 응답이 있어서 "받았다"를 확인할 수 있습니다. 토픽에 따로 ACK를 붙일 필요가 없습니다.
- 응답이 없으면 **같은 `event_id`로** 다시 보냅니다. 받는 쪽은 같은 번호를 한 번만 반영하고 같은 결과를 돌려줍니다(IR-20).
- 받는 쪽은 저장(DB)이 끝난 뒤에 응답합니다. 그래야 응답을 받은 알림은 사라지지 않습니다.
- 다시 보내는 간격은 통신 규칙 IR-40 표를 따릅니다(`Report*`: 응답 대기 시간 3초, 1 → 2 → 4초 · 최대 10초 간격, 포기하지 않음). 다시 보낼 때는 ±20% 무작위 지연을 둡니다(IR-41).
- 부르기 전에는 서비스가 준비됐는지 확인합니다. 처음 연결은 `wait_for_service`로 5초 이상 기다립니다(IR-32).
- 실제 콜백 그룹 · 타이머 배치는 항목 4 · 5를 함께 봅니다.

**이 예제가 막는 것과 못 막는 것**

- 막는 것: 받는 노드의 재시작, 잠깐의 네트워크 끊김, 응답 유실
- 못 막는 것: **보내는 노드의 재시작**. `outbox`가 메모리에만 있으면 함께 사라집니다.
- 보내는 노드의 재시작까지 막으려면, 보내기 전에 알림을 파일에 저장합니다.
- 순서는 이렇습니다: **보내는 쪽 저장 → 요청 → 받는 쪽 저장 → 응답 → 보내는 쪽 삭제**

**이 프로젝트에서는**

- 결과 · 진행 · 알림 보고는 모두 `Report*` 서비스(`ReportTrip` · `ReportCommand` · `ReportEvent`)로 보냅니다(IR-12).
- 사건은 모두 기록용 토픽 `/central/events`(EVENT: reliable · volatile · depth 50)에 내고, WARN 이상은 `ReportEvent`로도 보냅니다(IR-10 표).
- 운반 건 진행 보고(`ReportTrip`)는 위 예제와 조금 다릅니다.
  - 번호는 `event_id` 대신 `(report_epoch, report_n)`입니다. 켜질 때 `Hello`로 `epoch`를 받기 전에는 보내지 않습니다(IR-22).
  - 같은 요청을 그대로 다시 보내지 않고, 기다리는 보고를 **지금 상태 전체**로 바꿔 끼우고 새 `report_n`을 붙입니다(IR-23).
  - 같은 운반 건의 보고는 순서대로 보냅니다(IR-40 표).

**기억할 규칙: 놓치면 안 되는 알림 · 보고 = 서비스 + 같은 번호로 다시 보내기 + 받는 쪽 중복 확인 + 저장 뒤 응답 (+ 보내는 쪽 재시작 대비는 보내기 전 저장)**

왜? 토픽의 RELIABLE만으로 부족한 이유 → 기술 상세 A

### 2. 주기 상태와 놓치면 안 되는 진행을 나눕니다

**사례**

- 관리자 화면의 로봇 위치가 몇 초씩 늦게 움직입니다.
- Wi-Fi가 잠깐 느려진 뒤로 계속 늦습니다.
- 이미 지난 위치를 차례대로 그리고 있었습니다.
- 위치와 "작업 완료" 같은 진행 정보가 한 토픽에 섞여 있어서, QoS를 어느 한쪽에 맞출 수밖에 없었습니다.

#### ❌ 잘못된 예

```py
qos = QoSProfile(
    history=HistoryPolicy.KEEP_ALL,
    reliability=ReliabilityPolicy.RELIABLE,
)
self.create_publisher(RobotState, '/robot1/state', qos)   # 위치 + 작업 완료, 10 Hz 발행
```

- 지난 상태를 모두 쌓아 두고 하나도 빼지 않고 보냅니다.
- 한 번 밀리면 계속 옛날 값을 보냅니다.
- 느린 구독자 하나 때문에 보내는 쪽까지 막힐 수 있습니다. (항목 9 참고)
- 반대로 이 토픽을 BEST_EFFORT로 바꾸면, 위치는 괜찮지만 "작업 완료" 같은 진행 정보가 빠질 수 있습니다.

#### ✅ 바른 예 (이 프로젝트)

```py
# QoS는 contract.yaml에서 읽습니다 (항목 6). 아래는 contract의 값입니다.
#   STATE: best_effort · volatile · depth 5
#   HEARTBEAT: reliable · volatile · depth 1

# 주기 상태: 로봇 상태 토픽 하나, 초당 5번 안팎으로 지금 값을 보냄
name, msg_type, qos = contract.topic('robot_state', pode='pode1')   # STATE 프로필
self.state_pub = self.create_publisher(msg_type, name, qos)
self.create_timer(0.2, self.publish_state)

# 놓치면 안 되는 진행: 토픽이 아니라 서비스로 보고 (항목 1)
#   운반 건 단계 → ReportTrip, 관리 명령 결과 → ReportCommand, 알림 → ReportEvent

# 받는 쪽: 마지막으로 받은 시각을 기억하고, 1.5초 넘게 끊겼다 다시 오면 "다시 연결"로 봄
def on_state(self, msg):
    now = self.get_clock().now()
    last = self.last_rx.get(msg.robot_id)
    if last is not None and (now - last).nanoseconds / 1e9 > 1.5:
        self.on_reconnected(msg.robot_id)     # 끊겼다 다시 온 것
    self.last_rx[msg.robot_id] = now
    self.latest[msg.robot_id] = msg            # 최신 값만 씀
```

- 주기 상태(위치 · 상태 · 배터리 등)는 최신 값만 의미가 있습니다. 빠진 값은 다음 값이 곧 대신하므로 BEST_EFFORT로 보냅니다.
- "도착했다 · 끝났다"처럼 놓치면 잘못 판단하는 진행은 토픽에 맡기지 않고 `Report*` 서비스로 보냅니다. 서비스는 응답이 있어 받았는지 확인할 수 있습니다.
- 주기 상태도 끊김을 감시해야 합니다. 이 프로젝트는 받는 쪽이 1.5초를 기준으로 봅니다(IR-34). DDS의 `deadline`을 쓸 때도 값을 직접 정합니다. 기본값은 사실상 무한대입니다. (기술 상세 C)

#### ✅ 바른 예 · 반대 방향(관제 → 로봇)도 감시

```py
import copy
from rclpy.duration import Duration
from std_msgs.msg import Header
from rclpy.event_handler import SubscriptionEventCallbacks   # Jazzy
# from rclpy.qos_event import SubscriptionEventCallbacks     # Humble

HEARTBEAT_QOS = copy.copy(contract.qos('HEARTBEAT'))   # reliable · volatile · depth 1 (공유 객체를 바꾸지 않게 복사)
HEARTBEAT_QOS.deadline = Duration(seconds=1.5)        # 0.5초 주기의 3배 (contract에는 아직 없는 값)

# ----- 관제 쪽 (fleet) -----
self.hb_pub = self.create_publisher(Header, '/fleet/heartbeat', HEARTBEAT_QOS)
self.create_timer(0.5, self.publish_heartbeat)

# ----- 로봇 쪽 -----
def on_heartbeat_missed(self, event):
    self.get_logger().warn('관제 신호 끊김 → 멈춤')
    self.stop_motion()                         # 스스로 멈춤 (비상정지를 대신하지 않음)

self.create_subscription(
    Header, '/fleet/heartbeat', self.on_heartbeat, HEARTBEAT_QOS,
    event_callbacks=SubscriptionEventCallbacks(deadline=self.on_heartbeat_missed))
```

- 로봇 → 관제 방향만 감시하면, 관제가 멈췄을 때 로봇은 그 사실을 모르고 계속 움직입니다.
- 관제가 0.5초마다 하트비트를 보내고, 로봇은 1.5초 끊기면 스스로 멈춥니다(IR-10 표의 "관제 신호", contract `/fleet/heartbeat`).
- deadline 1.5초는 contract의 HEARTBEAT 프로필에 아직 없습니다. 지금은 양쪽 코드가 같은 값을 쓰고, contract에 올릴지는 설계 검토 사항으로 정합니다. 직접 시각을 재서 1.5초를 판단해도 됩니다.
- deadline은 발행 · 구독 양쪽에 같은 값을 줍니다. 받는 쪽 deadline이 보내는 쪽보다 짧으면 연결되지 않습니다.
- 이 정지는 비상정지를 대신하지 않습니다. (항목 10)

**기억할 규칙: 주기 상태 = STATE 토픽(BEST_EFFORT, 최신 값만) · 놓치면 안 되는 진행 = `Report*` 서비스 · 끊김은 양방향으로 감시 (1.5초)**

### 3. 서비스 QoS는 바꾸지 않습니다

**사례**

- 서버 노드를 재시작했습니다.
- 그런데 재시작 전에 들어온 "로봇 정지" 요청이 다시 실행되었습니다.

#### ❌ 잘못된 예

```py
self.create_service(StopRobot, '/stop_robot', self.on_stop,
                    qos_profile=LATCHED_QOS)   # TRANSIENT_LOCAL
```

- 서비스에 TRANSIENT_LOCAL을 쓰면 오래된 요청이 남습니다.
- 재시작한 서버가 그 요청을 다시 받아 실행할 수 있습니다.

#### ✅ 바른 예

```py
self.create_service(StopRobot, '/stop_robot', self.on_stop)   # 기본 QoS 그대로
```

- 서비스는 기본 QoS(VOLATILE)를 그대로 씁니다. contract의 서비스 줄에도 QoS를 적지 않습니다.
- 서비스는 곧바로 끝나는 요청 · 응답에 씁니다. 오래 걸리는 작업은 접수만 곧바로 답하고 결과는 따로 보고하거나(`Report*`), 액션으로 만듭니다. (항목 8, IR-10)

**기억할 규칙: 서비스 = 기본 QoS(VOLATILE) 그대로**

### 4. 콜백 안에서 call()을 쓰지 않습니다

**사례**

- 타이머 콜백에서 서비스를 호출했습니다.
- 노드가 그대로 멈췄습니다. 에러 메시지도 없습니다.

#### ❌ 잘못된 예

```py
def on_timer(self):
    resp = self.cli.call(req)    # 응답이 올 때까지 기다림
    self.get_logger().info(str(resp))
```

- `call()`은 응답이 올 때까지 기다립니다.
- 그런데 응답을 처리할 스레드가 지금 이 콜백에 묶여 있습니다.
- 서로 기다리다 영원히 멈춥니다(deadlock). 브리지 시험에서도 실제로 멈췄습니다(IR-31).

#### ✅ 바른 예

```py
def on_timer(self):
    future = self.cli.call_async(req)            # 기다리지 않음
    future.add_done_callback(self.on_response)

def on_response(self, future):
    resp = future.result()
    self.get_logger().info(str(resp))
```

- `call_async()`는 바로 돌아옵니다.
- 응답이 오면 `on_response`가 호출됩니다. 응답 콜백은 별도 콜백 그룹에 둡니다(IR-30, 항목 5).
- DB 저장이나 HTTP 호출도 콜백 안에서 오래 붙잡지 않습니다. DB 락을 잡은 채 응답을 기다리지 않습니다(IR-31).

#### ✅ 바른 예 · 응답이 안 올 때 (이 프로젝트)

```py
import random, threading, uuid
from rclpy.duration import Duration

TIMEOUT = Duration(seconds=2.0)                  # 응답 대기 시간 (IR-40 표의 "fleet → 로봇 관리 명령" 줄)

# 노드 __init__ 안:
#   self.cli = self.create_client(SetLock, set_lock_name, callback_group=...)
#   self.lock = threading.Lock()                 # 타이머와 응답 콜백이 self.pending을 함께 씀
#   self.create_timer(0.1, self.on_check_timer, callback_group=...)
#   처음 부르기 전 self.cli.wait_for_service(timeout_sec=5.0)   (IR-32)

def send_command(self, target):                  # fleet → 로봇 관리 명령 (예: 락 잠금)
    req = SetLock.Request(request_id=str(uuid.uuid4()), robot_id=target, locked=True)
    with self.lock:
        self.pending = {'req': req, 'tries': 0, 'future': None, 'next_at': self.get_clock().now()}

def on_check_timer(self):
    now = self.get_clock().now()
    with self.lock:
        p = self.pending
        if p is None:
            return
        f = p['future']
        if f is not None and f.done():
            return                               # 응답 처리는 on_response에서
        if f is not None and now - p['sent_at'] > TIMEOUT:
            self.cli.remove_pending_request(f)   # 기다리던 응답은 버림 (IR-41)
            p['future'] = None
            if p['tries'] >= 5:                  # 첫 요청 포함 5번
                self.report_command(p['req'], result='UNCONFIRMED', reason='ROBOT_TIMEOUT')
                self.pending = None              # 결과 모름 → 30초 안에 끝 상태를 한 번만 정함 (IR-62)
                return
            p['next_at'] = now + Duration(seconds=1.0 * random.uniform(0.8, 1.2))   # 1초 간격 ±20%
        if p['future'] is None and now >= p['next_at']:
            p['tries'] += 1
            p['sent_at'] = now
            p['future'] = self.cli.call_async(p['req'])   # 같은 request_id로 그대로 다시
            p['future'].add_done_callback(self.on_response)
```

- 응답이 끝내 안 올 수 있습니다(무선 끊김, 서버 재시작). 응답 대기 시간(타임아웃)을 정해 둡니다.
- 이 프로젝트는 받는 쪽이 같은 `request_id`를 한 번만 반영하고 같은 결과를 돌려줍니다(IR-20). 그래서 상태를 먼저 조회하지 않고 **같은 번호로 그대로 다시 보냅니다.**
- 다시 보낼 때는 ±20% 무작위 지연을 두고, 노드별 동시 요청 수에 상한을 둡니다(IR-41).
- 로봇 명령은 "켜기/끄기 전환"이 아니라 목표 값(`locked=true`)으로 보냅니다. 두 번 반영돼도 결과가 같습니다(IR-21).
- 정해진 횟수만큼 보내도 응답이 없으면 실패가 아니라 "결과 모름"(`UNCONFIRMED`, `ROBOT_TIMEOUT`)으로 보고하고, 늦은 응답이나 `/fleet/robots` 관측으로 30초 안에 끝 상태를 한 번만 정합니다(IR-62). "연결 안 됨" 표시는 서비스 준비 대기에 실패했을 때입니다.
- 로봇의 거절은 서비스 응답으로 받습니다(IR-61). 상태 토픽은 관측을 돕는 보조이고, 결과는 응답과 `ReportCommand`로 받습니다.

**기억할 규칙: 콜백 안에서는 기다리지 않는다 → call_async() + 응답 대기 시간 + 같은 request_id로 다시 보내기 (응답이 끝내 없으면 "결과 모름")**

### 5. callback group을 나눕니다

**사례**

- 로봇 6대의 상태가 한꺼번에 몰려들었습니다.
- 그 사이 들어온 보고 응답이 1초 넘게 늦게 처리되었습니다.

#### ❌ 잘못된 예

```py
# 노드 __init__ 안
self.create_subscription(RobotState, '/pode1/state', self.on_state, STATE_QOS)
self.report_cli = self.create_client(ReportTrip, '/bridge/report_trip')

# main() 안
rclpy.spin(node)    # 스레드 1개, 그룹 1개
```

- 모든 콜백이 한 줄로 서서 차례를 기다립니다.
- 상태 콜백이 많으면 서비스 응답 콜백이 뒤로 밀립니다.

#### ✅ 바른 예

```py
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor

# 노드 __init__ 안
self.state_group = MutuallyExclusiveCallbackGroup()
self.reply_group = MutuallyExclusiveCallbackGroup()

self.create_subscription(RobotState, '/pode1/state', self.on_state, STATE_QOS,
                         callback_group=self.state_group)
self.report_cli = self.create_client(ReportTrip, '/bridge/report_trip',
                                     callback_group=self.reply_group)

# main() 안
executor = MultiThreadedExecutor()
executor.add_node(node)
executor.spin()
```

- 그룹이 다르면 다른 스레드에서 동시에 실행됩니다.
- 일꾼에 여유가 있으면, 응답 콜백이 상태 콜백과 동시에 실행될 수 있습니다.
- 스레드가 모두 바쁘면 응답도 기다립니다. 콜백을 짧게 유지하는 것이 함께 필요합니다. (항목 9)
- 그룹을 나누려면 `MultiThreadedExecutor`를 써야 합니다(IR-30).
- `future.add_done_callback()`으로 단 함수는 클라이언트의 그룹이 아니라 executor 작업으로 따로 실행됩니다. 타이머 · 다른 콜백과 같은 변수를 쓰면 `Lock`으로 보호합니다(항목 1 · 4 예제).

**기억할 규칙: 서비스 응답 등 중요한 콜백은 자기 그룹 + MultiThreadedExecutor**

### 6. QoS가 안 맞으면 연결이 안 됩니다

**사례**

- 토픽 이름도 타입도 맞습니다.
- 그런데 구독 콜백이 한 번도 불리지 않습니다.
- 에러도 나지 않습니다.

#### ❌ 잘못된 예

```py
# 보내는 쪽: BEST_EFFORT (STATE)
self.create_publisher(RobotState, '/pode1/state', STATE_QOS)
# 받는 쪽: 숫자만 줌 → 기본값 RELIABLE
self.create_subscription(RobotState, '/pode1/state', self.on_state, 10)
```

- 받는 쪽이 보내는 쪽보다 더 엄격한 QoS를 요구합니다.
- 이 경우 ROS 2는 둘을 연결하지 않습니다.
- 경고 로그만 한 줄 나오고 조용히 넘어갑니다.
- LATCHED(transient_local) 토픽을 VOLATILE로 받으면 연결은 되지만, 켜진 뒤 마지막 값을 받지 못합니다(IR-33).

#### ✅ 바른 예 (이 프로젝트)

```py
# 이름 · 타입 · QoS를 contract.yaml에서 읽어 양쪽이 같은 값을 씀 (IR-01 · IR-33)
name, msg_type, qos = contract.topic('robot_state', pode='pode1')   # 설명용 도우미 (읽는 법 참고)
self.create_subscription(msg_type, name, self.on_state, qos)
```

```bash
# 연결 상태와 양쪽 QoS 확인
ros2 topic info -v /pode1/state
```

- QoS 프로필의 원본은 `interfaces/fleet_interfaces/contract.yaml`(STATE · LATCHED · EVENT · HEARTBEAT)입니다. 노드는 contract에서 읽고, 코드에 따로 정의하지 않습니다.
- 연결이 안 되면 먼저 `ros2 topic info -v`로 QoS를 비교합니다.
- 어떤 조합이 안 되는지는 기술 상세 B 표를 보세요.

**기억할 규칙: QoS는 contract.yaml 한 곳에서 읽고, 양쪽이 같은 프로필을 쓴다**

### 7. 알림 · 명령 메시지는 작게 (목표 64 KB 이하)

**사례**

- 알림에 카메라 사진을 함께 넣어 보냈습니다.
- Wi-Fi에서 알림이 자주 늦거나 빠졌습니다.

#### ❌ 잘못된 예

```py
msg = Alert()
msg.image = cv_bridge.cv2_to_imgmsg(frame)   # 수백 KB
self.alert_pub.publish(msg)
```

- 큰 메시지는 네트워크에서 여러 조각으로 나뉘어 전송됩니다.
- 조각 하나만 빠져도 메시지 전체가 늦거나 사라질 수 있습니다.
- 어느 크기부터 나뉘는지는 미들웨어와 전송 설정에 따라 다릅니다.
- 한 연구(Raspberry Pi 4, ROS 2 Foxy)에서는 500 KB 메시지의 약 30%가 사라졌습니다.

#### ✅ 바른 예

```py
req.detail = f'image=http://192.168.0.10:8080/alerts/{req.event_id}.jpg'   # 받는 PC가 열 수 있는 주소
```

- 알림 · 명령 메시지는 작게 유지합니다. 처음에는 64 KB 이하를 목표로 잡습니다.
- 64 KB는 시작 기준입니다. 64 KB 이하여도 IP 계층에서 약 1.5 KB 단위로 나뉘므로, Wi-Fi에서는 작을수록 유리합니다. 실제 한계는 쓰는 미들웨어로 시험해 확인합니다.
- 로봇 컴퓨터(Raspberry Pi 등)가 감당할 메시지 크기와 빈도의 한도를 로봇별 파라미터로 정해 둡니다. 보내는 쪽은 그 한도를 넘지 않습니다.
- 큰 데이터는 파일 서버나 별도 영상 경로로 보냅니다. 이 프로젝트의 영상은 ROS 2 밖(GStreamer, UDP)으로 보냅니다(IR-10).
- 메시지에는 **받는 PC가 열 수 있는** URL이나 저장소 ID만 담습니다.
- 보내는 PC의 로컬 경로(`/data/...`)나 `localhost` 주소는 다른 PC에서 열 수 없습니다. (기술 상세 G-8)

**기억할 규칙: 알림 · 명령 메시지에 이미지 · 대용량 데이터를 넣지 않는다. 대신 받는 쪽이 열 수 있는 주소를 넣는다**

### 8. 작업 지시는 액션으로 보냅니다

**사례**

- 로봇에 "A 선반으로 이동" 명령을 토픽으로 보냈습니다.
- 로봇이 받았는지, 어디까지 갔는지 알 수 없었습니다.
- 다시 보냈더니 로봇이 같은 일을 두 번 했습니다.

#### ❌ 잘못된 예

```py
self.cmd_pub = self.create_publisher(MoveCmd, '/robot1/move_cmd', 10)
self.cmd_pub.publish(cmd)   # 받았는지 모름
```

- 토픽은 "보내고 끝"입니다(IR-11).
- 수락, 진행률, 완료 여부를 알 수 없습니다.
- 다시 보내면 중복 실행될 수 있습니다.

#### ✅ 바른 예

```py
from rclpy.action import ActionClient
from action_msgs.msg import GoalStatus

self.client = ActionClient(self, MoveTo, '/pode1/move_to')

goal = MoveTo.Goal()
# goal 칸은 contract의 정의를 따릅니다. 0.6 MoveTo: target_id · target_pose · order_id · order_update_id 등
future = self.client.send_goal_async(goal, feedback_callback=self.on_feedback)
future.add_done_callback(self.on_goal_response)

def on_goal_response(self, future):
    handle = future.result()
    if not handle.accepted:
        self.on_rejected()                   # 거절된 목표에는 결과가 없음. 기록하고 상위에 알림
        return
    handle.get_result_async().add_done_callback(self.on_result)

def on_result(self, future):
    resp = future.result()                   # resp.status, resp.result
    if resp.status == GoalStatus.STATUS_SUCCEEDED:
        self.save_result(resp.result)        # 받는 즉시 저장 (서버는 일정 시간 후 결과를 버림)
    elif resp.status == GoalStatus.STATUS_ABORTED:
        self.mark_failed()                   # 실행 중 실패
    elif resp.status == GoalStatus.STATUS_CANCELED:
        self.mark_canceled()                 # 취소됨
```

- 액션은 수락 → 진행(feedback) → 결과(result)를 모두 알려 줍니다.
- 결과의 `status`로 성공 · 실패 · 취소를 구분합니다. `result`만 보면 실패한 작업도 성공으로 착각할 수 있습니다.
- 결과는 받는 즉시 저장합니다.
- 취소 · 정지도 "보냈으니 멈췄다"로 보지 않습니다. 결과 `status`(CANCELED)나 로봇 상태(항목 2)로 확인합니다.
- 결과가 오지 않을 때 "실패"로 단정하지 않습니다. 관리 명령에서는 "결과 모름"으로 두고 끝 상태를 한 번만 정합니다(IR-62).

#### ✅ 바른 예 · 서버 쪽 중복 확인

```py
from rclpy.action import GoalResponse

def goal_callback(self, goal):                    # ActionServer(..., goal_callback=...)
    last = self.store.last_update_id(goal.order_id)   # 처음 받는 주문이면 None
    if last is not None and goal.order_update_id < last:
        return GoalResponse.REJECT                # 늦게 도착한 옛 지시
    return GoalResponse.ACCEPT

def execute_callback(self, handle):              # ActionServer(..., execute_callback=...)
    g = handle.request
    if self.store.is_running_or_done(g.order_id, g.order_update_id):
        return self.store.wait_result(g.order_id, g.order_update_id)   # 다시 보낸 같은 지시: 새로 실행하지 않고 그 결과를 돌려줌
    self.store.record(g.order_id, g.order_update_id)
    ...                                           # 실제 실행
```

- 서버가 받은 번호를 기억해야 중복 실행을 막을 수 있습니다.
- **같은 번호가 다시 오면 거절하지 않습니다.** 거절하면 다시 보낸 쪽은 원래 목표가 실행 중인데도 "거절"로 받고, 원래 결과를 받을 방법이 없습니다. 새로 실행하지 않고 진행 중이거나 끝난 그 목표의 결과를 돌려줍니다(IR-20과 같은 생각).
- 더 작은 번호는 늦게 도착한 옛 지시라 거절합니다.
- 액션 목표의 중복 확인 칸과 방법은 0.6 정의(`order_id` · `order_update_id`)를 기준으로 한 예입니다. 팀이 확정하기 전에는 설계 검토 사항에 올려 정합니다(IR-92).
- 서버가 재시작한 뒤에도 중복을 막아야 하면, 받은 번호와 결과를 파일에 저장합니다.
- 곧바로 끝나는 요청은 서비스, 오래 걸리는 실행은 액션으로 나눕니다(IR-10).

**이 프로젝트에서는:** 운반 건 지시는 fleet이 정해 로봇에 액션(`MoveTo` · `Dock` · `FillBasket` · `SwapBasket` · `Retreat`)으로 보냅니다. central은 로봇에 직접 명령하지 않습니다(IR-56).

**기억할 규칙: 오래 걸리는 작업 = 액션 + 중복 확인 번호 + 서버 쪽 중복 확인(같은 번호는 같은 결과) + 결과 status 확인**

### 9. 느린 구독자 하나가 전체를 늦춥니다

**느린 구독자**란 메시지가 들어오는 속도보다 처리하는 속도가 느린 구독자입니다.
예를 들어 메시지는 1초에 10개 오는데, 1초에 2개만 처리하는 경우입니다.

**사례**

- 분석 노드 하나를 추가했습니다.
- 그 뒤로 다른 노드들의 수신도 들쭉날쭉해졌습니다.
- 분석 노드의 콜백이 한 번에 0.5초씩 걸리고 있었습니다.

#### ❌ 잘못된 예

```py
self.create_subscription(LaserScan, '/scan', self.on_scan, 10)   # 10 Hz 수신

def on_scan(self, msg):
    result = self.heavy_analysis(msg)   # 0.5초 걸림
    self.save_to_db(result)             # DB 저장도 콜백 안에서
```

- 1초에 10개가 오는데 2개만 처리합니다.
- 받는 큐가 금방 가득 차고, 오래된 메시지부터 버려집니다.
- 같은 PC에서 CPU와 executor를 함께 쓰므로 다른 콜백 · 노드도 함께 늦어집니다.
- KEEP_ALL이거나 네트워크가 느려 확인 응답이 늦으면, RELIABLE의 부담이 보내는 쪽과 다른 구독자로 번질 수 있습니다(기술 상세 F).

#### ✅ 바른 예

```py
import threading

SCAN_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)   # /scan은 contract 밖의 설명용 토픽이라 직접 정의 (프로젝트 토픽은 contract에서 읽음)
self.latest_scan = None
self.rx_group = MutuallyExclusiveCallbackGroup()     # 받기 전용
self.work_group = MutuallyExclusiveCallbackGroup()   # 처리 전용 (항목 5)

self.create_subscription(LaserScan, '/scan', self.on_scan, SCAN_QOS,
                         callback_group=self.rx_group)
self.create_timer(0.5, self.on_process, callback_group=self.work_group)  # 2 Hz
self.scan_lock = threading.Lock()

def on_scan(self, msg):
    with self.scan_lock:
        self.latest_scan = msg          # 최신 값만 저장하고 바로 끝

def on_process(self):                   # 자기 속도로 처리
    with self.scan_lock:                # 읽기와 비우기를 한 번에
        scan, self.latest_scan = self.latest_scan, None
    if scan is None:
        return
    result = self.heavy_analysis(scan)  # 무거운 일은 락 밖에서
```

- 콜백은 받기만 하고 바로 끝냅니다.
- 두 콜백은 그룹이 달라서 동시에 실행될 수 있습니다.
- 같은 변수(`latest_scan`)를 함께 쓰므로 `Lock`으로 보호합니다. 보호하지 않으면 방금 들어온 스캔이 지워질 수 있습니다.
- 무거운 분석은 락을 푼 뒤에 합니다. 그래야 받는 콜백이 기다리지 않습니다.
- 무거운 처리는 타이머로 자기 속도에 맞춰 합니다.
- 모든 메시지가 필요 없다면 BEST_EFFORT, depth 1로 받습니다.

**기억할 규칙: 콜백은 짧게. 못 따라가는 구독자는 최신 값만 받는다**

원인, QoS별 영향, 확인 방법 → 기술 상세 F

### 10. Wi-Fi에서는 보내는 양을 줄입니다

**사례**

- 카메라 노드를 켜자 모든 로봇의 통신이 느려졌습니다.
- `ros2 topic list`에 다른 팀 로봇의 토픽이 보였습니다.
- 공유기를 재시작한 뒤 일부 노드가 서로를 찾지 못했습니다.

#### ❌ 잘못된 예

```bash
# 모든 PC가 기본 설정 그대로 (ROS_DOMAIN_ID = 0)
ros2 run camera_pkg camera_node   # 640x480 원본 이미지, 30 Hz를 Wi-Fi로 발행
```

- 640×480 원본 이미지 한 장은 약 0.9 MB입니다.
- 30 Hz면 초당 약 27 MB, 약 220 Mbps입니다.
- Wi-Fi 하나를 모든 로봇이 나눠 쓰므로, 이것만으로 대역이 부족해집니다.
- 도메인 ID가 기본값(0)이면 같은 공유기의 다른 팀과 서로 섞입니다.

#### ✅ 바른 예 (이 프로젝트)

```bash
# 1) 로봇 네트워크 (운영): 모든 PC에서 같은 값 + 디스커버리 서버 (실제로는 tools/net/net_ds.sh로)
export ROS_DOMAIN_ID=6
export ROS_DISCOVERY_SERVER=192.168.0.252:11811

# 2) 혼자 하는 시험: 다른 도메인 번호 + 이 PC 안에서만 (그 터미널에서만)
export ROS_DOMAIN_ID=43
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST   # Jazzy
# export ROS_LOCALHOST_ONLY=1                    # Humble
```

- 로봇 네트워크는 `ROS_DOMAIN_ID=6`이고, 모든 장치가 디스커버리 서버로 서로 찾습니다. 시험은 반드시 다른 도메인 번호와 `LOCALHOST`, 서버 없음으로 띄웁니다(IR-81 · IR-82, [디스커버리 서버 안내](디스커버리_서버_안내.md)).
- 같은 서버에 붙으면 도메인 번호가 달라도 서로 보입니다. 서버 설정이 남은 터미널에서 도메인만 바꾸면 실물 망에 섞입니다.
- 로봇 장치와 함께 하는 연동 시험은 다른 PC와 통신해야 해서 `LOCALHOST`를 쓸 수 없습니다. 이때 쓸 시험용 서버는 설계 검토 사항 Q-18에서 정한 뒤 따르고(IR-93), 정해지기 전에는 IR-81을 따릅니다.
- localhost 제한은 `~/.bashrc`에 넣지 않습니다. 그 PC의 모든 노드가 다른 PC에서 안 보이게 됩니다. (기술 상세 G-8)
- 이미지는 로봇에서 처리하고, 결과(인식 결과, 좌표)만 보냅니다.
- 영상이 꼭 필요하면 ROS 2 밖의 별도 경로로 보냅니다(IR-10).
- 주기 상태 토픽은 항목 2, 놓치면 안 되는 보고는 항목 1을 따릅니다.
- 서버 PC처럼 움직이지 않는 장비는 유선으로 연결합니다.

**통신 규칙은 비상정지를 대신하지 않습니다**

- 이 문서의 규칙은 메시지가 늦거나 사라질 때를 대비한 것입니다. 안전을 보장하지 않습니다.
- 이 프로젝트의 비상정지는 central · fleet과 따로 도는 estop 노드가 모든 로봇을 멈추고, 로봇이 해제 신호까지 스스로 멈춤을 유지하는 방식입니다(기능 목록 COM-02, `design/diagrams/IFP_다이어그램.drawio`의 6-11 탭). 신호 · 상태 정의는 아직 인터페이스 명세에 없습니다. PC · Wi-Fi가 죽으면 위 하트비트 규칙으로 로봇이 스스로 섭니다.
- 통신이 끊기거나 허가 응답이 오지 않을 때, 로봇의 기본 동작은 "진행하지 않음"으로 정합니다.

**기억할 규칙: Wi-Fi로는 결과만 보낸다. 운영은 도메인 6 + 디스커버리 서버, 혼자 하는 시험은 다른 번호 + LOCALHOST + 서버 없음으로**

네트워크 구성, 탐색(discovery) 문제, 점검 명령 → 기술 상세 G

## 2부. 체크리스트

토픽 · 서비스 · 액션을 새로 만들 때 확인하세요. [통신 규칙](통신_규칙.md)의 요약 10줄도 함께 확인합니다.

#### 토픽을 만들 때

* [ ] 다시 오지 않아 놓치면 안 되는 메시지(요청 · 결과 · 알림)인가요? → 토픽이 아니라 서비스 (항목 1, IR-11)
* [ ] 최신 값만 의미가 있나요? → 주기적으로 계속 보내면 STATE, 바뀔 때 보내고 늦게 켜진 노드도 받아야 하면 LATCHED (항목 2)
* [ ] 이름 · 타입 · QoS를 contract.yaml에서 읽나요? (항목 6, IR-01)
* [ ] 보내는 쪽과 받는 쪽이 같은 QoS 프로필을 쓰나요? (IR-33)
* [ ] 주기 상태는 초당 5번 안팎으로 다시 보내고, 받는 쪽이 1.5초 끊김을 감시하나요? (IR-34)
* [ ] 알림 · 명령 메시지가 64 KB 목표 이하인가요? 다른 PC로 보내는 주소는 받는 PC가 열 수 있나요?
* [ ] `deadline` · `liveliness` lease를 쓴다면 값을 직접 정했나요? (기본값은 사실상 무한대)

#### 서비스 · 액션을 만들 때

* [ ] 곧바로 끝나는 요청인가요? → 서비스. 오래 걸리면 → 액션 (IR-10)
* [ ] 서비스 QoS를 기본값 그대로 두었나요?
* [ ] 결과 · 진행 · 알림 보고를 `Report*` 서비스로 보내나요? (IR-12)
* [ ] 요청에 보내는 쪽이 만든 번호(`request_id` · `event_id` 등)가 있고, 받는 쪽이 같은 번호를 한 번만 반영하나요? (IR-20)
* [ ] 부르기 전 `wait_for_service`를 5초 이상 기다리나요? (IR-32)
* [ ] `call_async` + 응답 대기 시간 + 같은 번호로 다시 보내기를 하나요? 시간 · 횟수는 IR-40 표를 따르나요?
* [ ] 받는 쪽은 저장(DB 커밋)이 끝난 뒤에 응답하나요?
* [ ] 정지 · 취소 · 완료를 "보냈다"가 아니라 응답과 로봇 상태로 확인하나요?
* [ ] 액션 결과의 `status`로 성공 · 실패 · 취소를 구분하고, 결과를 받는 즉시 저장하나요?

#### 노드를 만들 때

* [ ] 콜백 안에 `call()`이나 오래 걸리는 작업이 없나요? DB 락을 잡은 채 기다리지 않나요? (IR-31)
* [ ] 서비스 응답 등 중요한 콜백을 별도 callback group에 넣었나요?
* [ ] `MultiThreadedExecutor`를 쓰나요?
* [ ] 다른 그룹의 콜백이 같은 변수를 쓰면 `Lock`으로 보호했나요?
* [ ] 놓치면 안 되는 보고를 보내는 노드가 재시작해도 보고가 남나요? (보내기 전 저장)
* [ ] 다시 켜진 뒤 지금 값을 다시 받고 나서 움직이나요? (IR-70)

#### Wi-Fi로 통신할 때

* [ ] 운영은 `ROS_DOMAIN_ID=6`, 시험은 다른 번호를 쓰나요? (IR-81)
* [ ] 실물 망 노드를 `tools/net/net_ds.sh`를 `source`한 터미널(또는 그 값을 넣은 systemd · docker)에서 띄웠나요? (IR-81 · IR-82)
* [ ] 혼자 하는 시험 터미널에서 `echo $ROS_DISCOVERY_SERVER`가 비어 있나요? (서버 값이 남으면 도메인이 달라도 실물 망에 섞임)
* [ ] Wi-Fi로 원본 이미지나 포인트클라우드를 보내고 있지 않나요?
* [ ] "메시지 크기 × 초당 개수 × 로봇 수"를 계산해 보았나요?
* [ ] 관제 → 로봇 방향도 하트비트로 감시하나요? 신호가 끊기면 로봇이 스스로 멈추나요?
* [ ] 콜백이 오래 걸리는 느린 구독자가 없나요? (노드 안에서 콜백 처리 시간을 재서 확인)
* [ ] localhost 제한을 `~/.bashrc`에 넣지 않았나요? 다른 PC로 보내는 주소에 `localhost` · `127.0.0.1`을 쓰지 않았나요?
* [ ] 같은 PC 안의 HTTP · WebSocket 호출은 `localhost` 대신 `127.0.0.1`을 쓰나요?

#### 연결이 안 될 때

* [ ] `ros2 topic info -v <토픽>`로 양쪽 QoS를 비교했나요?
* [ ] 같은 `ROS_DOMAIN_ID`와 같은 탐색 범위 설정을 쓰나요?
* [ ] 양쪽이 같은 디스커버리 서버 목록(순서까지)을 쓰나요? 서버를 쓰는 노드와 안 쓰는 노드는 서로 못 찾습니다. (IR-82)
* [ ] 디스커버리 서버가 켜져 있나요? 서버가 꺼진 동안 새로 뜬 노드는 상대를 못 찾습니다.
* [ ] 확인 터미널을 super client(`SUPER=1`)로 열고 `ros2 daemon`을 다시 띄웠나요? 보통 노드 설정의 `ros2 topic list`는 일부만 보입니다.

## 3부. 기술 상세

여기부터는 "왜 그런가요?"에 대한 설명입니다.
처음에는 건너뛰어도 됩니다.

### A. 토픽의 RELIABLE만으로 부족한 이유

RELIABLE은 "연결된 상대에게는 빠진 메시지를 다시 보낸다"는 뜻입니다.
아래 경우는 막지 못합니다.

- **아직 연결되지 않았을 때:** 받는 쪽이 막 켜져 탐색이 끝나기 전의 메시지는 받지 못합니다.
- **큐가 넘칠 때:** KEEP_LAST는 depth를 넘으면 가장 오래된 메시지를 버립니다.
- **받는 쪽이 꺼져 있을 때:** VOLATILE은 지난 메시지를 남기지 않습니다.
- **보내는 쪽이 재시작할 때:** TRANSIENT_LOCAL도 보내는 쪽 메모리에만 보관합니다. 재시작하면 사라집니다.
- **QoS가 안 맞을 때:** 연결 자체가 안 됩니다.
- **콜백이 막혔을 때:** 받는 쪽 큐가 넘쳐 버려집니다.

그래서 꼭 필요한 메시지는 애플리케이션에서 "받았다"를 확인하고 다시 보냅니다.
일반적으로는 토픽에 ACK를 붙이는 방법도 있지만, 이 프로젝트는 응답이 기본으로 있는 서비스(`Report*`)를 씁니다(IR-11 · IR-12).
VDA 5050(AGV 통신 표준)도 같은 생각입니다.
전송은 가장 가벼운 MQTT QoS 0으로 하고, 신뢰성은 ID와 중복 확인으로 지킵니다.
단, MQTT는 TCP 위에서 동작하므로 QoS 0도 연결이 살아 있는 동안은 재전송됩니다.
ROS 2의 BEST_EFFORT(보통 UDP, 재전송 없음)와 같지 않으니, "VDA가 QoS 0이니 BEST_EFFORT"로 옮기지 않습니다.
자세한 내용은 프로젝트 문서 [VDA 5050(v3.0) 선례로 보는 다중 토픽 처리](https://iphonic.atlassian.net/wiki/x/AwBe)를 보세요.

RELIABLE의 비용은 대체로 작습니다.
한 연구(i7 데스크톱 · Raspberry Pi 4, ROS 2 Foxy)에서는 RELIABLE의 p95 지연이 BEST_EFFORT보다 최대 15% 늘었습니다
([Kronauer et al. 2021](https://arxiv.org/html/2101.02074v3)).
이 값은 그 실험 조건의 측정값입니다. 환경이 다르면 값도 달라집니다.

### B. QoS 호환 표

받는 쪽(구독자)이 보내는 쪽보다 엄격하면 연결되지 않습니다.

| 정책 | 보내는 쪽 | 받는 쪽 | 결과 |
| --- | --- | --- | --- |
| Reliability | BEST_EFFORT | BEST_EFFORT | **연결됨** |
| Reliability | BEST_EFFORT | RELIABLE | **연결 안 됨** |
| Reliability | RELIABLE | BEST_EFFORT | **연결됨 (BEST_EFFORT로 동작)** |
| Reliability | RELIABLE | RELIABLE | **연결됨** |
| Durability | VOLATILE | VOLATILE | **연결됨** |
| Durability | VOLATILE | TRANSIENT_LOCAL | **연결 안 됨** |
| Durability | TRANSIENT_LOCAL | VOLATILE | **연결됨 (지난 메시지는 못 받음)** |
| Durability | TRANSIENT_LOCAL | TRANSIENT_LOCAL | **연결됨 (지난 메시지도 받음)** |
| Deadline | 기간 X | 기간 Y (Y < X) | **연결 안 됨** (받는 쪽 기간이 같거나 길어야 함) |
| Liveliness | AUTOMATIC | MANUAL_BY_TOPIC | **연결 안 됨** |
| Liveliness lease | lease X | lease Y (Y < X) | **연결 안 됨** |

### C. QoS 정책 한눈에 보기

| 정책 | 선택지 | 뜻 |
| --- | --- | --- |
| History | KEEP_LAST(n) / KEEP_ALL | 최근 n개만 보관 / 모두 보관(자원 한도까지) |
| Reliability | RELIABLE / BEST_EFFORT | 빠지면 다시 보냄 / 빠지면 그냥 넘어감 |
| Durability | VOLATILE / TRANSIENT_LOCAL | 지난 메시지 안 남김 / 늦게 온 구독자에게 지난 메시지 보냄 |
| Deadline | 시간 | 이 시간 안에 다음 메시지가 와야 함. 안 오면 이벤트 발생. 기본값은 사실상 무한대(감시 안 함) |
| Lifespan | 시간 | 이 시간이 지난 메시지는 버림 |
| Liveliness | AUTOMATIC 등 + lease 시간 | 상대 발행자(writer)가 살아 있는지 확인. lease 기본값도 사실상 무한대 |

기본 QoS는 KEEP_LAST 10, RELIABLE, VOLATILE입니다.
`create_publisher(..., 10)`처럼 숫자만 주면 이 기본값이 쓰입니다.

이 프로젝트의 프로필 (contract.yaml):

| 프로필 | 값 | 쓰는 곳 |
| --- | --- | --- |
| STATE | best_effort · volatile · depth 5 | 주기 상태 (`/{pode}/state` · `/{arm}/state`) |
| LATCHED | reliable · transient_local · depth 1 | 지금 값 상태판 (`/central/orders` · `/fleet/robots` 등) |
| EVENT | reliable · volatile · depth 50 | 기록용 사건 (`/central/events`) |
| HEARTBEAT | reliable · volatile · depth 1 | 관제 신호 (`/fleet/heartbeat`) |

deadline과 liveliness lease는 값을 직접 정해야 끊김을 감지합니다. 예: 발행 주기의 3배.
감지 결과는 받는 쪽 노드에만 알려지므로, 관제가 끊김을 판단하면 그 결과를 다시 토픽으로 알립니다(예: `/fleet/robots`).

### D. Executor와 callback group

- executor는 콜백을 실행하는 일꾼입니다.
- `rclpy.spin()`은 일꾼이 1명입니다. 콜백이 한 번에 하나씩 실행됩니다.
- `MultiThreadedExecutor`는 일꾼이 여럿입니다.
- 노드의 기본 callback group은 MutuallyExclusive입니다. 그룹 안에서는 한 번에 하나만 실행됩니다.
- 그래서 일꾼이 여럿이어도 그룹을 나누지 않으면 동시에 실행되지 않습니다.
- ReentrantCallbackGroup은 같은 콜백도 동시에 실행될 수 있습니다. 공유 데이터를 다룰 때는 조심해야 합니다.
- executor는 우선순위도 도착 순서도 아닌, 준비된 콜백을 종류별로 정해진 순서(타이머 · 구독 · 서비스 등)대로 처리합니다. 중요한 콜백이 다른 콜백 뒤로 밀릴 수 있어 따로 빼는 것입니다.

### E. 메시지 크기

- DDS는 보통 UDP로 전송합니다.
- UDP 한 번에 보낼 수 있는 크기는 최대 약 64 KB입니다.
- 큰 메시지는 여러 조각으로 나뉘어 전송됩니다(단편화).
- 어느 크기부터 나누는지는 DDS 구현과 설정에 따라 다릅니다. 64 KB보다 훨씬 작은 단위로 나누는 구현도 있습니다.
- 64 KB 이하의 UDP 데이터그램도 IP 계층에서 MTU(보통 1500바이트) 단위로 다시 나뉩니다. 조각 하나만 잃어도 데이터그램 전체를 잃습니다.
- 같은 PC 안에서는 Fast DDS가 기본으로 공유 메모리를 쓰므로 이 문제가 거의 없습니다.
- 조각이 많을수록 하나라도 빠질 가능성이 커집니다. 빠진 조각을 다시 받는 동안 메시지 전체가 늦어집니다.
- Wi-Fi처럼 손실이 있는 구간에서는 큰 메시지일수록 불리합니다.
- 한 연구(Raspberry Pi 4, ROS 2 Foxy)에서는 64 KB를 넘자 지연이 크게 늘었고, 500 KB 메시지는 약 30%가 사라졌습니다
([Kronauer et al. 2021](https://arxiv.org/html/2101.02074v3)).
- 이 문서의 "64 KB"는 알림 · 명령 메시지의 **초기 크기 목표**입니다. 실제 한계는 쓰는 미들웨어로 시험해 정합니다.

### F. 느린 구독자 자세히

**그림 · 빠른 구독자와 느린 구독자 비교**

```text
보내는 쪽 (1초에 10개 발행)
 ├─▶ [받는 큐: 여유 있음]                    ─▶ 빠른 구독자 (1초에 10개 처리)
 └─▶ [받는 큐: 가득 참 → 오래된 것부터 버림] ─▶ 느린 구독자 (1초에 2개 처리)

※ RELIABLE이면: 느린 구독자 때문에 재전송 · 확인 응답 부담이 보내는 쪽에 쌓임
```

같은 토픽을 받는 두 구독자. 느린 구독자의 문제가 보내는 쪽으로 거슬러 올라갑니다.

#### 왜 느려지나요?

- 콜백 안에서 무거운 계산, DB 저장, HTTP 호출을 합니다.
- 스레드가 1개라서 다른 콜백이 끝나기를 기다립니다.
- Wi-Fi 신호가 약해서 메시지가 늦게 도착합니다.
- Raspberry Pi처럼 성능이 낮은 PC에서 받습니다.
- 웹 브리지 너머의 브라우저가 느립니다. 브리지 입장에서는 브라우저도 구독자입니다.

#### QoS에 따라 무슨 일이 생기나요?

| QoS | 느린 구독자에게 | 다른 구독자 · 보내는 쪽에게 |
| --- | --- | --- |
| BEST_EFFORT | 못 받은 메시지는 그냥 놓칩니다. | 영향이 거의 없습니다. |
| RELIABLE + KEEP_LAST | 큐가 넘치면 오래된 메시지를 잃습니다. | 콜백이 느린 것만으로는 재전송이 늘지 않습니다(받는 쪽 DDS가 따로 받아 확인 응답을 보냄). 네트워크가 느리거나 손실이 있어 확인 응답이 늦으면, 보내는 쪽이 보관 · 재전송을 계속해 Wi-Fi의 다른 통신까지 느려집니다. |
| RELIABLE + KEEP_ALL | 메시지는 잃지 않지만 계속 밀립니다. | 보관 공간이 차면 `publish()`가 멈추고 기다릴 수 있습니다. 그러면 다른 구독자도 함께 늦어집니다. |

정확한 동작은 DDS 구현과 설정에 따라 조금씩 다릅니다.
하지만 "RELIABLE + KEEP_ALL이거나 네트워크가 느릴수록, 느린 구독자의 영향이 보내는 쪽으로 번진다"는 점은 같습니다.

#### 실제 측정 사례

- (아래 외부 수치는 원문 그대로 옮겼고, 팀이 다시 재 보지는 않았습니다.)
- 2026년 연구에서 RELIABLE 토픽의 느린 구독자 하나가 보내는 쪽을 멈추게 했습니다.
중요한 구독자를 따로 분리하자 그 구독자의 지연(p95)이 최대 15초에서 1.55 ms로 줄었습니다
([Adaptive Bridge, 2026](https://arxiv.org/abs/2608.15380), 초록 기준).
- 웹 브리지 rosbridge에서도 부하가 걸리자 한 클라이언트가 메시지의 95.8 ~ 99.8%를 받지 못했습니다
([rosbridge Issue #1243](https://github.com/RobotWebTools/rosbridge_suite/issues/1243)).

#### 어떻게 막나요?

- 콜백은 받기만 하고 바로 끝냅니다. 무거운 일은 타이머나 별도 스레드로 넘깁니다. (항목 9)
- 중요한 콜백은 별도 callback group에 넣습니다. (항목 5)
- 모든 메시지가 필요 없는 구독자는 BEST_EFFORT, depth 1로 받습니다.
- 느린 쪽이 있다면 낮은 주기로 다시 발행하는 중계 토픽을 따로 만듭니다. (예: 10 Hz 원본 → 1 Hz 요약 토픽)
- 중요한 토픽과 대량 토픽은 이름을 나눠 서로 영향을 주지 않게 합니다.
- 웹 브리지는 클라이언트마다 큐 크기를 정합니다. 큐가 넘치는 클라이언트는 연결을 끊고 다시 붙게 합니다.

#### 두 가지 문제를 구분하세요

| 구분 | 무슨 일인가 | 누가 손해를 보나 | 해결 |
| --- | --- | --- | --- |
| 받는 쪽 처리 적체 | 받는 노드의 콜백이 느려 그 노드의 큐가 넘칩니다. | 주로 그 노드 자신 | 콜백 짧게, 최신 값만 받기, callback group 분리 |
| DDS 재전송 · 보내는 쪽 대기 | RELIABLE에서 확인 응답이 늦은 구독자(KEEP_ALL, 느린 네트워크) 때문에 보내는 쪽이 계속 재전송하거나 기다립니다. | 같은 토픽의 다른 구독자, 보내는 쪽, 네트워크 전체 | BEST_EFFORT 사용, 중요 · 대량 토픽 분리, 느린 쪽용 중계 토픽 |

두 문제는 함께 생기기도 하지만, 원인과 해결이 다릅니다.
어느 쪽인지 먼저 구분한 뒤 대처합니다.

#### 어떻게 확인하나요?

- `ros2 topic hz`는 CLI가 따로 만든 구독자의 수신 주기입니다.
- 그래서 "토픽이 실제로 얼마나 자주 오는지"를 볼 때 씁니다.
- "내 노드가 따라가고 있는지"는 알려 주지 않습니다.
- 내 노드의 병목은 그 노드 안에서 잽니다. 콜백 처리 시간과 받은 개수, 놓친 개수입니다.

```bash
# 토픽 자체의 발행 주기 확인 (CLI 구독자 기준)
ros2 topic hz /scan

# 메시지에 header.stamp가 있으면 도착 지연도 볼 수 있습니다 (CLI 구독자 기준)
ros2 topic delay /scan
```

```py
# 내 노드 안에서 콜백 처리 시간 재기
import time

def on_scan(self, msg):
    t0 = time.monotonic()
    ...                                   # 실제 처리
    dt = time.monotonic() - t0
    if dt > 0.1:                          # 발행 주기(0.1초)보다 오래 걸리면 경고
        self.get_logger().warn(f'콜백 {dt * 1000:.0f} ms')
```

- 메시지에 순번(seq)을 넣으면, 받는 쪽에서 빠진 개수를 셀 수 있습니다.
- 보내는 노드가 재시작하면 seq가 0으로 돌아갑니다. seq와 함께 부팅(세션) ID를 넣어, 받는 쪽이 재시작과 누락을 구분하게 합니다. 이 프로젝트의 `ReportTrip`은 `(report_epoch, report_n)`, 로봇 상태는 `boot_id`로 같은 일을 합니다(IR-22 · IR-72).
- 콜백 시간이 발행 주기보다 길면 그 노드는 느린 구독자입니다.

### G. Wi-Fi 환경 운영

Wi-Fi는 유선보다 느리고, 손실이 있고, 여러 장비가 나눠 씁니다.
로봇은 움직이므로 신호 세기도 계속 바뀝니다.
아래 내용을 기본으로 지켜 주세요.

#### 1. 네트워크 구성

- 로봇 전용 공유기를 씁니다. 다른 용도의 장비와 섞지 않습니다.
- 가능하면 5 GHz 대역을 씁니다. 2.4 GHz는 간섭이 많습니다.
- 서버 PC처럼 움직이지 않는 장비는 유선으로 연결합니다.
- 로봇이 다니는 구역 끝에서 신호 세기를 미리 확인합니다.

#### 2. 보내는 양 계산하기

- Wi-Fi의 실제 처리량은 공유기에 표시된 속도보다 훨씬 낮습니다.
- 그 처리량을 연결된 모든 장비가 나눠 씁니다.
- 보내기 전에 "메시지 크기 × 초당 개수 × 로봇 수"를 계산해 봅니다.
- 예: 원본 이미지 640×480, 30 Hz는 로봇 1대만으로 약 220 Mbps입니다.
- 실제 양은 `ros2 topic bw <토픽>`으로 확인합니다.

#### 3. 도메인 ID로 시스템 분리

- 멀티캐스트로 찾을 때(기본)는 같은 도메인 ID를 쓰는 노드끼리만 서로를 찾습니다.
- **디스커버리 서버를 쓰면 다릅니다.** 같은 서버에 붙은 노드는 도메인 ID가 달라도 서로 보입니다. 갈라지는 단위는 서버(주소:포트)입니다(2026-11-24 실측, [디스커버리 서버 안내](디스커버리_서버_안내.md) 8절).
- 기본값은 0입니다. 기본값을 쓰면 같은 공유기의 다른 시스템과 섞입니다.
- 시스템마다 다른 번호를 정하고, 그 시스템의 모든 PC에 같은 값을 넣습니다. 0 ~ 101 사이 번호를 권장합니다. ([Domain ID 문서](https://docs.ros.org/en/jazzy/Concepts/Intermediate/About-Domain-ID.html))
- **이 프로젝트에서는:** 로봇 네트워크는 `ROS_DOMAIN_ID=6` + 디스커버리 서버, 혼자 하는 시험은 다른 번호 + LOCALHOST + 서버 없음입니다(IR-81 · IR-82, `docs/개발_규칙.md`). 도메인만 바꾸는 것으로는 실물 망과 갈라지지 않습니다.

#### 4. 탐색(discovery) 문제

> **이 프로젝트에서는:** 실물 망은 Fast DDS 디스커버리 서버가 기본입니다(IR-81). 2026-11-23 실물 시험에서 멀티캐스트로는 늦게 합류한 Pode가 격리되고 먼저 뜬 Pode가 멈췄고, 서버로는 주문이 모두 출고됐습니다. 서버는 fleet PC(0번 `192.168.0.252:11811`)와 central PC(1번, 주소 미정 Q-20) 두 대이고, 설정은 `tools/net/net_ds.sh` 한 곳입니다. 역할 · 순서 · 문제 찾기는 [디스커버리 서버 안내](디스커버리_서버_안내.md)를 봅니다. 아래는 일반 설명입니다.

- DDS는 기본적으로 multicast로 다른 노드를 찾습니다.
- Wi-Fi 공유기는 multicast를 느리게 보내거나 막는 경우가 있습니다.
- 그러면 노드가 안 보이거나, 늦게 보이거나, 가끔 끊깁니다.
- 먼저 multicast가 되는지 확인합니다. 두 PC에서 아래를 실행합니다.

```bash
# PC A
ros2 multicast receive
# PC B
ros2 multicast send
```

multicast가 잘 안 되면 아래 방법을 검토합니다.

- **찾는 범위 줄이기:** 다른 PC와 통신하지 않는 노드는 localhost로 제한합니다. (항목 10)
- **상대 고정하기 (Jazzy):** `ROS_STATIC_PEERS`에 통신할 PC의 IP를 적습니다.
([Improved Dynamic Discovery 문서](https://docs.ros.org/en/jazzy/Tutorials/Advanced/Improved-Dynamic-Discovery.html))
- **Discovery Server (Fast DDS):** 탐색을 서버 한 곳에서 처리합니다. multicast가 필요 없습니다.
([Discovery Server 문서](https://docs.ros.org/en/jazzy/Tutorials/Advanced/Discovery-Server/Discovery-Server.html))

```bash
# 일반 예 (주소는 예시. 이 프로젝트는 tools/net/discovery_server.sh · net_ds.sh를 씀)
# 서버 PC에서 Discovery Server 실행
fastdds discovery -i 0

# 각 PC의 노드 실행 전
export ROS_DISCOVERY_SERVER=192.168.0.10:11811
```

Discovery Server를 쓰면 보통 노드는 자기가 주고받는 상대만 알기 때문에, `ros2 topic list` 같은 CLI 명령은 일부만 보이거나 비어 보입니다. 전체를 볼 터미널은 super client(`ROS_SUPER_CLIENT=TRUE`, 이 프로젝트에서는 `SUPER=1 source tools/net/net_ds.sh`)로 열고, 설정을 바꾼 뒤에는 `ros2 daemon stop; ros2 daemon start`로 CLI 데몬을 다시 띄웁니다. 공식 문서의 "ROS 2 CLI" 부분도 참고하세요.

서버를 여러 대 둘 때(`ROS_DISCOVERY_SERVER="주소0;주소1"`)는 목록의 순서가 서버 번호(`-i`)입니다. 모든 PC가 같은 순서를 써야 하고, 순서가 다른 노드는 서버에 붙지 못합니다.
어느 방법을 쓸지는 팀에서 하나로 정해 모든 PC에 똑같이 적용해야 합니다.

#### 5. QoS 운영

- 위치처럼 다음 값이 곧 대신하는 주기 상태만 BEST_EFFORT(STATE)로 보냅니다. Wi-Fi에서 재전송이 쌓이지 않습니다.
- 지금 값을 늦게 켜진 노드도 받아야 하는 상태판은 LATCHED(reliable · transient_local · depth 1)로 보내고, 바뀔 때와 주기적으로 다시 보냅니다.
- 놓치면 안 되는 진행 · 알림은 토픽이 아니라 `Report*` 서비스로 보내고, 응답이 없으면 같은 번호로 다시 보냅니다. Wi-Fi가 끊겼다 돌아와도 다시 보냅니다. (항목 1)
- 받는 쪽은 1.5초 끊김을 기준으로 "다시 연결"을 판단합니다(IR-34). `deadline` · `liveliness`를 쓴다면 lease 값은 반드시 정합니다.
- 관제 → 로봇 방향도 하트비트로 감시합니다. (항목 2)

#### 6. 큰 메시지와 미들웨어

- Wi-Fi에서는 64 KB를 넘는 메시지의 지연과 손실이 크게 늘어납니다.
- 한 연구에서는 Wi-Fi에서 1 KB 메시지 평균 지연이 CycloneDDS 51.40 ms, Zenoh(router를 거치는 방식) 12.58 ms였습니다
([Zhang et al. 2023](https://ar5iv.labs.arxiv.org/html/2309.07496)).
- Wi-Fi 구간이 계속 문제라면 Zenoh(rmw_zenoh, zenoh-bridge-ros2dds)를 검토해 볼 수 있습니다.
- 다만 미들웨어를 바꾸는 것은 팀 전체에 영향이 큽니다. 먼저 보내는 양과 QoS를 정리한 뒤에 검토합니다.
- 미들웨어마다 지원하는 QoS 이벤트(deadline · liveliness 등)가 다를 수 있습니다. 하트비트 감시를 이 이벤트에 맡기고 있다면 바꾸기 전에 지원 여부를 확인합니다.

#### 7. 점검 명령 모음

```bash
ros2 doctor --report          # ROS 2 환경 · 네트워크 설정 요약
ros2 topic hz <토픽>           # CLI 구독자 기준 수신 주기
ros2 topic bw <토픽>           # 토픽이 쓰는 대역폭
ros2 topic info -v <토픽>      # 연결된 노드와 양쪽 QoS
iw dev wlan0 link             # Wi-Fi 신호 세기, 연결 속도 (Linux)
ping <상대 IP>                 # 지연과 손실
```

지연을 PC끼리 비교하려면 시계가 맞아야 합니다.
모든 PC에 chrony 같은 시간 동기화를 켜 둡니다. 이 프로젝트는 관제 PC 기준으로 맞춥니다(IR-42).

#### 8. localhost · 127.0.0.1 사용 시 주의

localhost 제한은 Wi-Fi 부담을 줄여 주지만, 잘못 쓰면 "토픽이 안 보인다" 문제의 가장 흔한 원인이 됩니다.
웹 브리지처럼 HTTP · WebSocket을 함께 쓰는 프로그램에서는 `localhost`라는 이름 자체가 지연을 일으키기도 합니다.

| 상황 | 무슨 일이 생기나 | 이렇게 합니다 |
| --- | --- | --- |
| localhost 제한을 `~/.bashrc`에 넣음 | 그 PC의 모든 노드가 다른 PC에서 안 보입니다. | 다른 PC와 통신이 전혀 없는 실행에만, 그 터미널이나 launch 파일에서만 설정합니다. |
| 같은 PC에서 설정이 섞임 | 설정이 다른 노드끼리 못 찾을 수 있습니다. 디스커버리 서버를 쓰는 노드와 쓰지 않는 노드(LOCALHOST · 멀티캐스트)도 서로 못 찾습니다. `ros2 topic list`도 비어 보일 수 있습니다. | 함께 통신하는 노드와 CLI는 같은 설정을 씁니다. 설정을 바꾼 뒤에는 `ros2 daemon stop`으로 CLI 데몬을 다시 시작합니다. |
| Humble + CycloneDDS에서 `ROS_LOCALHOST_ONLY=1` | 환경에 따라 loopback(`lo`)에 multicast가 꺼져 있어 탐색이 실패합니다. | `ip link show lo`에 MULTICAST가 있는지 확인합니다. 없으면 `sudo ip link set lo multicast on` (재부팅하면 다시 꺼짐) |
| Jazzy에서 `ROS_LOCALHOST_ONLY` 사용 | 동작은 하지만 지원 중단 예정 경고가 나옵니다. | `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`로 바꿉니다. |
| Docker 컨테이너 안에서 `localhost` | 컨테이너 안의 localhost는 컨테이너 자신입니다. 호스트의 노드나 서버가 아닙니다. 공유 메모리 전송도 기본 설정으로는 막힙니다. | ROS 2 노드는 컨테이너에 넣지 않거나, 넣는다면 host 네트워크 · IPC 설정을 팀 표준으로 정합니다. 실물 망에 붙는 컨테이너에는 `net_ds.sh`와 같은 값(도메인 · `RMW_IMPLEMENTATION` · `ROS_DISCOVERY_SERVER`)도 넣습니다. (웹 · DB는 정상 운영에서 docker로 따로 운영합니다) |
| 같은 PC의 HTTP · WebSocket 서버(예: 브리지 REST)를 `localhost`로 호출 | 환경에 따라 연결이 느려질 수 있습니다. 팀에서도 `127.0.0.1`로 바꾸자 빨라진 사례가 있었습니다. `localhost`는 IPv6 주소(`::1`)로 먼저 풀릴 수 있습니다. 서버가 IPv4(`127.0.0.1`)에서만 받고 있으면, 클라이언트가 `::1` 연결에 실패한 뒤 IPv4로 다시 시도합니다. 실패 응답이 바로 오면 지연이 작지만, 응답 없이 버려지는 환경에서는 시간이 걸립니다. 이름을 주소로 바꾸는 단계(DNS · hosts 조회)가 느린 환경에서도 지연이 생깁니다. | 같은 PC 안의 호출은 `http://127.0.0.1:포트`처럼 IP로 씁니다. 또는 서버가 IPv4와 IPv6를 모두 받도록 설정합니다. 주소는 설정 파일 한 곳에 두고 함께 씁니다. |
| 다른 PC에 보내는 주소에 `localhost` · `127.0.0.1` | 받는 PC가 자기 자신을 가리키게 됩니다. 이미지 URL, `ROS_STATIC_PEERS`, `ROS_DISCOVERY_SERVER` 모두 해당합니다. | 다른 PC가 접근할 수 있는 실제 IP나 호스트 이름을 씁니다. (항목 7) |

여러 PC에 노드가 나뉘어 있는 시스템이라면, 대부분의 노드는 네트워크로 통신해야 합니다.
localhost 제한은 "한 PC 안에서만 주고받는 것이 확실한 실행"에만 씁니다.
확실하지 않으면 쓰지 않는 편이 안전합니다.

## 4부. 용어집

- **QoS (Quality of Service)** — 메시지를 어떻게 보낼지 정하는 설정 묶음입니다.
- **History / depth** — 보관할 메시지 개수입니다. KEEP_LAST 10이면 최근 10개만 보관합니다.
- **RELIABLE / BEST_EFFORT** — 빠진 메시지를 다시 보낼지 여부입니다.
- **VOLATILE / TRANSIENT_LOCAL** — 늦게 접속한 구독자에게 지난 메시지를 줄지 여부입니다.
- **STATE · LATCHED · EVENT · HEARTBEAT** — 이 프로젝트 contract.yaml의 QoS 프로필 이름입니다 (기술 상세 C).
- **IR-nn** — [통신 규칙](통신_규칙.md)의 규칙 번호입니다.
- **contract** — `interfaces/fleet_interfaces/contract.yaml`. 토픽 · 서비스 · 액션의 이름 · 타입 · QoS 원본입니다.
- **fleet (관제) · central (주문 처리) · 서비스 브리지 · Pode · Phody · Phony** — 이 프로젝트의 노드와 로봇입니다. fleet은 로봇 배정 · 운행, central은 주문판, 서비스 브리지는 웹과 ROS를 잇습니다. Pode는 운반 로봇, Phody는 양팔, Phony는 출고 팔입니다.
- **DDS** — ROS 2가 메시지를 실제로 주고받는 데 쓰는 미들웨어 표준입니다 (Fast DDS, CycloneDDS 등).
- **UUID** — 겹치지 않게 만드는 무작위 번호 문자열입니다. `request_id` · `event_id`에 씁니다.
- **응답 대기 시간 (타임아웃)** — 응답을 기다리는 최대 시간입니다. 지나면 다시 보내거나 "결과 모름"으로 처리합니다.
- **p95** — 측정값 100개 중 95번째로 작은 값입니다. "대부분 이 안에 든다"는 뜻으로 씁니다.
- **MQTT** — 브로커를 거치는 가벼운 메시지 통신 방식입니다. VDA 5050이 씁니다.
- **ACK** — "받았다"는 확인 응답입니다. 서비스에서는 응답이 이 역할을 합니다.
- **하트비트 (heartbeat)** — "살아 있다"를 알리려고 일정한 주기로 보내는 작은 신호입니다.
- **요청 번호 (`request_id`)** — 보내는 쪽이 요청마다 만드는 번호(UUID)입니다. 받는 쪽은 같은 번호를 한 번만 반영합니다.
- **부팅(세션) ID** — 노드가 시작될 때마다 새로 정하는 값입니다. 재시작 전후의 순번을 구분하는 데 씁니다.
- **중복 확인 (멱등성)** — 같은 요청이 여러 번 와도 한 번만 처리되게 하는 것입니다.
- **콜백 (callback)** — 메시지가 오거나 타이머가 울릴 때 실행되는 함수입니다.
- **executor** — 콜백을 실행하는 일꾼입니다.
- **callback group** — 콜백을 묶는 그룹입니다. 그룹마다 동시 실행 규칙이 다릅니다.
- **deadlock** — 서로 상대를 기다리다 둘 다 멈추는 상태입니다.
- **액션 (action)** — 오래 걸리는 작업용 통신입니다. 수락 · 진행 · 결과를 모두 알려 줍니다.
- **goal / feedback / result** — 액션의 요청 / 진행 상황 / 최종 결과입니다.
- **느린 구독자 (slow subscriber)** — 메시지가 들어오는 속도보다 처리하는 속도가 느린 구독자입니다.
- **탐색 (discovery)** — 노드들이 네트워크에서 서로를 찾는 과정입니다.
- **multicast** — 한 번 보내서 여러 장비가 함께 받는 전송 방식입니다. DDS가 탐색에 씁니다.
- **ROS_DOMAIN_ID** — ROS 2 통신 그룹 번호입니다. 멀티캐스트로 찾을 때는 같은 번호끼리만 서로를 찾습니다. 디스커버리 서버를 쓰면 같은 서버에 붙은 노드는 번호가 달라도 보입니다.
- **디스커버리 서버 (discovery server)** — 노드들이 멀티캐스트 대신 정해진 서버에 자기를 알리고, 서버가 통신할 상대를 알려 주는 Fast DDS 방식입니다. 찾는 일만 돕고 데이터는 노드끼리 바로 주고받습니다.
- **super client** — 디스커버리 서버에서 전체 노드 목록을 받는 클라이언트입니다. `ros2` 명령 · RViz 터미널에 씁니다.
- **대역폭 (bandwidth)** — 1초에 보낼 수 있는 데이터 양입니다. 보통 Mbps로 나타냅니다.

## 5부. 참고 자료

- 프로젝트 규칙 — [통신 규칙](통신_규칙.md) (`IR-nn`), [인터페이스 명세](인터페이스_명세.md), `interfaces/fleet_interfaces/contract.yaml`
- ROS 2 QoS 문서 — [About Quality of Service Settings](https://github.com/ros2/ros2_documentation/blob/rolling/source/ROS-Framework/interfaces/topics/About-Quality-of-Service-Settings.rst)
- ROS 2 callback group 문서 — [Using callback groups](https://docs.ros.org/en/rolling/How-To-Guides/Using-callback-groups.html)
- ROS 2 액션 설계 — [design.ros2.org: Actions](https://design.ros2.org/articles/actions.html)
- RELIABLE 지연, 64 KB 단편화, 500 KB 손실 측정 — [Kronauer et al. 2021](https://arxiv.org/html/2101.02074v3)
- executor 처리 순서 분석 — [Casini et al., ECRTS 2019](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.ECRTS.2019.6)
- 느린 구독자가 보내는 쪽을 멈추는 사례 — [Adaptive Bridge, 2026](https://arxiv.org/abs/2608.15380), [rosbridge Issue #1243](https://github.com/RobotWebTools/rosbridge_suite/issues/1243)
- Wi-Fi · 4G에서 DDS · Zenoh · MQTT 지연 비교 — [Zhang et al. 2023](https://ar5iv.labs.arxiv.org/html/2309.07496)
- 도메인 ID — [About Domain ID](https://docs.ros.org/en/jazzy/Concepts/Intermediate/About-Domain-ID.html)
- 탐색 범위 · 고정 상대 — [Improved Dynamic Discovery](https://docs.ros.org/en/jazzy/Tutorials/Advanced/Improved-Dynamic-Discovery.html)
- Discovery Server — [Using Fast DDS Discovery Server](https://docs.ros.org/en/jazzy/Tutorials/Advanced/Discovery-Server/Discovery-Server.html)
- VDA 5050 표준 — [VDA5050 v3.0](https://github.com/VDA5050/VDA5050/blob/main/VDA5050_EN.md)
- 무선 환경의 다중 토픽 처리 선례 — 프로젝트 문서 [VDA 5050(v3.0) 선례로 보는 다중 토픽 처리](https://iphonic.atlassian.net/wiki/x/AwBe)
- 더 깊은 내용 — 프로젝트 문서 「ROS2 REST 브리지 대량 메시지 처리」

## 변경 이력

| 날짜 | 버전 | 내용 |
|---|---|---|
| 2026-11-24 | v2.1 | 디스커버리 서버 반영(IR-81 · IR-82): 항목 10(실물 망 서버, 같은 서버면 도메인으로 갈라지지 않음, 연동 시험 예외를 Q-18로) · 기억할 규칙 · 체크리스트(서버 설정 · 서버 목록 순서 · 서버 켜짐 · super client) · 기술 상세 G-3(도메인 ID 설명 정정) · G-4(이 프로젝트 기본은 서버, CLI super client, 여러 서버 순서) · G-8(서버 섞임 · docker) · 용어집(ROS_DOMAIN_ID 정정, 디스커버리 서버 · super client 추가). 디스커버리 서버 안내 링크 |
| 2026-11-24 | v2.0 | **저장소로 옮김** (원본을 이 파일로, Confluence는 옮긴 판). 통신 규칙(IR)에 맞춰 본문 정정: 항목 1을 "토픽 + ACK"에서 `Report*` 서비스 + 같은 번호로 다시 보내기로 · 항목 2를 위치 / 작업 상태 토픽 둘에서 STATE 주기 상태 + `Report*` 진행 보고로, 하트비트 타입을 contract의 `std_msgs/Header`로 · 항목 4의 다시 보내기를 "상태 조회 뒤"에서 "같은 `request_id`로 그대로"로, 끝내 응답이 없으면 "결과 모름"(IR-62) · 항목 5 · 6 예제를 프로젝트 토픽 · contract 읽기로 · 항목 8 서버 중복 확인을 0.6 `order_id` · `order_update_id` 기준으로(같은 번호는 거절하지 않고 같은 결과) · 항목 10 도메인 6과 시험 도메인, 비상정지(estop 노드) · 항목 3 서비스 QoS를 contract 서비스 줄과, 항목 7 영상 경로를 IR-10과 맞춤 · 체크리스트 · 기술 상세 A · C · F · G-3 · G-5 · G-7 · 용어집을 같은 기준으로. 하트비트 메시지 타입을 contract에 맞춤(`Empty` → `std_msgs/Header`). 검수 3명(ROS 2 기술 · 프로젝트 규칙 대조 · 처음 읽는 사람) 의견 반영: 예제 재시도 코드(동시 요청 · 간격 늘리기 · Lock), `Duration` 비교, 느린 구독자와 RELIABLE의 관계, executor 처리 순서, 호환 표의 deadline · liveliness, IP 단편화, 목차 · 읽는 법 · 용어집, 용어 통일 |
| 2026-11-23 | v1.1 | (Confluence 판 4) 통신 규칙 게시에 맞춰 "이 프로젝트에 적용할 때" · "이 프로젝트에서는" 안내 상자 추가: 맨 앞, 항목 1 · 2 · 4 · 6 · 10, 체크리스트, 기술 상세 G-5 |
| 2026-11-06 | v1.0 | (Confluence 판 1 ~ 3) 처음 작성: 사례 10개, 체크리스트, 기술 상세 A ~ G, 용어집, 참고 자료. 판 사이 세부 이력은 남아 있지 않음 |
