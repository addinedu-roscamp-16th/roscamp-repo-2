"""Phody ↔ 팀 관제(fleet) 연결 노드.

작업 지시: docs/worklog/2026-11-26_fleet연결_작업지시.md (단계 2: 뼈대)

- 픽 앱 프로세스 안에서 자기 ROS 노드와 실행기 스레드로 돈다.
- 앱 상태는 SnapshotBox로 읽기만 한다. 앱은 메인 스레드에서 매 프레임 써 둔다(R13).
- 앱에 시킬 일은 명령 큐에 넣기만 한다. 앱 메인 루프가 pop_commands()로 꺼낸다(R12).
- 단계 4: 팔 상태 /{arm}/state 발행(5 Hz). 단계 5: set_mode. 단계 6: /workcell/fill_basket.
"""
import copy
import queue
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

ARMS = ('arm_left', 'arm_right')
ARM_SIDE = {'arm_left': 'left', 'arm_right': 'right'}  # 팀 이름 -> 앱(WorkState) 이름
MODE_UNKNOWN = 'UNKNOWN'  # 켜질 때 모드(IR-72). SystemMode에는 NORMAL · TEST만 있다.
MODES = ('NORMAL', 'TEST')  # set_mode가 받는 값 (팀 pode_fsm/admin.py와 같음)
MODE_MISMATCH = 'MODE_MISMATCH'
NOT_READY = 'NOT_READY'
UNKNOWN_TARGET = 'UNKNOWN_TARGET'
STATION = 'WORK'            # Phody 작업대 (팀 config/robots.yaml)
FEEDBACK_PERIOD_SEC = 0.5
# 피드백 phase (TODO(DECIDE-3): 팀이 값을 정하면 바꾼다)
PHASE_CAPTURING = 'CAPTURING'
PHASE_PICKING = 'PICKING'
PLACE_PHASE = {'STAGE2_ENTER': 'TO_BASKET', 'STAGE3_PLACE': 'PLACING', 'STAGE2_RETURN': 'RETURNING'}
STATE_PERIOD_SEC = 0.2    # 명세 3.5: 팔 상태 약 5 Hz
PENDING_NAMES = Path(__file__).resolve().parents[2] / 'config' / 'fleet_names_pending.yaml'

# 앱 WorkState.arms 값 -> dev ArmState.state (dev: IDLE · PICKING · PLACING · SWAPPING · ERROR)
# TODO(FIX-2): 팀이 명세 4.5 상수를 넣으면 아래 세 줄을 바꾼다.
#   pick_waiting -> PICK_WAITING, place_waiting -> PLACE_WAITING, paused -> PAUSED
APP_TO_ARM_STATE = {
    'idle': 'IDLE',
    'pick_waiting': 'IDLE',      # TODO(FIX-2): PICK_WAITING
    'picking': 'PICKING',
    'place_waiting': 'PICKING',  # TODO(FIX-2): PLACE_WAITING (물건을 쥐고 바구니 대기)
    'placing': 'PLACING',        # B 진입 ~ C ~ B 복귀(바구니 점유). 두 팔 동시에 되지 않는다
    # P 정지 · worker 실패 정지(앱 paused)도 ERROR로 보인다. dev에 PAUSED가 없어서다.
    # 사용자 결정(11-26): 팀이 PAUSED를 넣을 때까지 ERROR 그대로(detail에 정지 사유).
    # 미세조정 정지(AUTO FINE PAUSED)만으로는 앱이 picking 그대로라 PICKING으로 보인다(정상 대기).
    'paused': 'ERROR',           # TODO(FIX-2): PAUSED
    'error': 'ERROR',
}
# TODO(FIX-2): 명세 4.5에서 나중에 채울 ArmState 칸 (dev에는 arm_id · state · detail · stamp뿐)
#   boot_id, lock, last_request_id, mode, forced  <- 이 노드가 가진 값
#   holding (앱 held_jobs에 그 팔이 있음), order_id (담기 목표의 trip_id), placed_total (담은 수)
#   gripper (/{side}/gripper_value < 50 -> CLOSED). Pi에서 오는 값이라 Wi-Fi가 끊기면 마지막 값이 남는다.


def arm_state_fields(snapshot, arm):
    """앱 스냅샷 -> (state, detail). 스냅샷에 팔 상태가 없으면 None."""
    arms = snapshot.get('arms')
    if not arms:
        return None
    side = ARM_SIDE[arm]
    app_state = arms[side]
    reasons = snapshot.get('reasons') or {}
    detail = reasons.get(side) or (reasons.get('service') if app_state == 'error' else None) or ''
    return APP_TO_ARM_STATE[app_state], detail


def app_idle(snapshot):
    """앱이 새 담기 목표를 받을 수 있는 쉬는 상태인가 (작업지시서 단계 6 거절 조건)."""
    arms = snapshot.get('arms')
    return bool(arms) and all(state == 'idle' for state in arms.values()) and not (
        snapshot.get('queue_started') or snapshot.get('paused') or snapshot.get('held') or
        snapshot.get('motion_busy') or snapshot.get('place_busy') or snapshot.get('fleet_goal'))


def fill_feedbacks(snapshot):
    """[(arm_id, phase, placed_total)]: 움직이는 팔마다 하나 (사용자 결정 11-26, 두 팔이면 둘)."""
    goal = snapshot.get('fleet_goal')
    if not goal:
        return []
    placed = int(goal.get('placed', 0))
    if not goal.get('captured'):
        return [('', PHASE_CAPTURING, placed)]
    out = []
    motion_stage = snapshot.get('motion_stage') if snapshot.get('motion_busy') else None
    place_stage = snapshot.get('place_stage') or (motion_stage if motion_stage in PLACE_PHASE else None)
    owner = snapshot.get('basket_owner')
    if place_stage in PLACE_PHASE and owner:
        out.append((f'arm_{owner}', PLACE_PHASE[place_stage], placed))
    active = snapshot.get('active_arm')
    if active and motion_stage and motion_stage not in PLACE_PHASE:
        out.append((f'arm_{active}', PHASE_PICKING, placed))
    return out


def split_placed(items, placed):
    """요청 [(kind, count)]와 담은 수 -> (placed [(kind, n)], missing [(kind, n)]). 요청 순서대로 채운다."""
    done, missing = [], []
    for kind, count in items:
        n = max(0, min(count, placed))
        placed -= n
        if n:
            done.append((kind, n))
        if count - n:
            missing.append((kind, count - n))
    return done, missing


def _load_yaml(path):
    import yaml
    with open(path, encoding='utf-8') as stream:
        return yaml.safe_load(stream)


def contract_path():
    from ament_index_python.packages import get_package_share_directory
    return Path(get_package_share_directory('fleet_interfaces')) / 'contract.yaml'


def qos_from_contract(contract, key):
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    profile = contract['qos_profiles'][key]
    return QoSProfile(depth=int(profile['depth']),
                      reliability=ReliabilityPolicy[profile['reliability'].upper()],
                      durability=DurabilityPolicy[profile['durability'].upper()])


class RequestMemory:
    """같은 request_id에는 같은 답을 돌려주기 위한 기억(IR-21: 최근 32건, 60초)."""

    def __init__(self, max_items=32, ttl_sec=60.0, clock=time.monotonic):
        self.max_items = max_items
        self.ttl_sec = ttl_sec
        self._clock = clock
        self._items = OrderedDict()  # request_id -> (저장 시각, 응답)
        self._lock = threading.Lock()

    def _drop_old(self, now):
        while self._items:
            key, (saved, _) = next(iter(self._items.items()))
            if now - saved <= self.ttl_sec:
                break
            del self._items[key]

    def get(self, request_id):
        """기억한 응답을 돌려준다. 없거나 60초가 지났으면 None."""
        with self._lock:
            self._drop_old(self._clock())
            item = self._items.get(request_id)
            return None if item is None else item[1]

    def put(self, request_id, response):
        with self._lock:
            now = self._clock()
            self._drop_old(now)
            self._items.pop(request_id, None)
            self._items[request_id] = (now, response)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


class SnapshotBox:
    """앱(메인 스레드)이 쓰고 fleet 연결 스레드가 읽는 상태 사본."""

    def __init__(self, initial=None):
        self._lock = threading.Lock()
        self._data = copy.deepcopy(initial or {})

    def write(self, data):
        with self._lock:
            self._data = copy.deepcopy(data)

    def read(self):
        with self._lock:
            return copy.deepcopy(self._data)


class FleetLink:
    """fleet 연결 노드 뼈대. start()로 켜고 stop()으로 끈다."""

    def __init__(self, snapshot=None, node_name='phody_fleet_link'):
        self.node_name = node_name
        self.snapshot = snapshot if snapshot is not None else SnapshotBox()
        self.commands = queue.Queue()
        self.mode_requests = {arm: RequestMemory() for arm in ARMS}  # set_mode: 팔마다 따로 (IR-21)
        # 켜질 때 상태 (IR-72, 작업지시서 단계 2)
        self.boot_id = str(uuid.uuid4())
        self.mode = {arm: MODE_UNKNOWN for arm in ARMS}
        self.lock = {arm: True for arm in ARMS}  # TODO(DECIDE-4): 1차에는 보고만 하고 거절에 쓰지 않음
        self._state_lock = threading.Lock()
        self._context = None
        self._node = None
        self._state_pubs = {}
        self._executor = None
        self._thread = None
        self.kinds = dict(_load_yaml(PENDING_NAMES).get('kinds') or {})  # kind -> YOLO 클래스
        self._goal_lock = threading.Lock()
        self._goal = None          # 진행 중인 담기 목표 {'id', 'done', 'result'}
        self._goal_count = 0
        self._stopping = threading.Event()

    # ---- 앱 ↔ fleet 연결 ----
    def push_command(self, command):
        """fleet 연결 콜백이 앱에 시킬 일을 넣는다. 기다리지 않는다."""
        self.commands.put(command)

    def pop_commands(self):
        """앱 메인 루프가 부른다. 쌓인 명령을 넣은 순서대로 모두 꺼낸다(기다리지 않음)."""
        items = []
        while True:
            try:
                items.append(self.commands.get_nowait())
            except queue.Empty:
                return items

    def read_snapshot(self):
        return self.snapshot.read()

    # ---- 켜고 끄기 ----
    @property
    def node(self):
        return self._node

    def start(self):
        import rclpy
        from rclpy.executors import MultiThreadedExecutor

        # 앱의 다른 ROS 사용과 섞이지 않게 자기 컨텍스트를 쓴다.
        self._context = rclpy.Context()
        rclpy.init(context=self._context)
        self._node = rclpy.create_node(self.node_name, context=self._context)
        self._make_state_publishers()
        self._make_mode_servers()
        self._make_fill_server()
        self._executor = MultiThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, name='fleet_link', daemon=True)
        self._thread.start()

    # ---- 단계 4: 팔 상태 ----
    def _make_state_publishers(self):
        from fleet_interfaces.msg import ArmState
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup

        pending = _load_yaml(PENDING_NAMES)['arm_state']
        qos = qos_from_contract(_load_yaml(contract_path()), pending['qos'])
        for arm in pending['arms']:
            topic = pending['name'].replace('{arm}', arm)
            self._state_pubs[arm] = self._node.create_publisher(ArmState, topic, qos)
        self._node.create_timer(STATE_PERIOD_SEC, self.publish_arm_states,
                                callback_group=MutuallyExclusiveCallbackGroup())

    def publish_arm_states(self):
        from fleet_interfaces.msg import ArmState

        snapshot = self.read_snapshot()
        stamp = self._node.get_clock().now().to_msg()
        for arm, pub in self._state_pubs.items():
            fields = arm_state_fields(snapshot, arm)
            if fields is None:  # 앱이 아직 첫 스냅샷을 쓰지 않음(사용자 승인 11-26)
                return
            msg = ArmState()
            msg.arm_id = arm
            msg.state, msg.detail = fields
            msg.stamp = stamp
            pub.publish(msg)

    # ---- 단계 5: set_mode ----
    def _make_mode_servers(self):
        from fleet_interfaces.srv import SetMode
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup

        contract = _load_yaml(contract_path())
        entry = next(item for item in contract['services'] if item['key'] == 'robot_set_mode')
        group = MutuallyExclusiveCallbackGroup()
        for arm in ARMS:
            name = entry['name'].replace('{robot}', arm)
            self._node.create_service(
                SetMode, name,
                lambda request, response, arm=arm: self._on_set_mode(arm, request, response),
                callback_group=group)

    def set_mode(self, arm, request_id, mode):
        """(accepted, reason, detail). 같은 request_id는 다시 반영하지 않고 같은 답 (IR-20 · IR-21)."""
        if request_id:
            seen = self.mode_requests[arm].get(request_id)
            if seen is not None:
                return seen
        if mode in MODES:
            with self._state_lock:
                self.mode[arm] = mode
            reply = (True, '', '')
            # TODO(FIX-2): 받아들인 request_id는 나중에 ArmState.last_request_id로 보고한다.
        else:
            reply = (False, MODE_MISMATCH, f'unknown mode {mode!r}')
        if request_id:
            self.mode_requests[arm].put(request_id, reply)
        return reply

    def _on_set_mode(self, arm, request, response):
        response.accepted, response.reason, response.detail = self.set_mode(
            arm, request.request_id, request.mode)
        response.request_id = request.request_id
        return response

    def mode_ready(self):
        """담기 명령을 받아도 되는 모드인가. False면 MODE_MISMATCH로 거절 (IR-72, 단계 6).

        사용자 결정(11-26): 두 팔이 같은 모드(둘 다 NORMAL 또는 둘 다 TEST)일 때만 받는다.
        담기 명령 하나로 두 팔이 같이 움직이므로, 한 팔은 운영(NORMAL) · 한 팔은 시험(TEST)인
        상태는 관제 쪽 실수로 보고 거절한다. UNKNOWN(켜진 뒤 아직 안 받음)도 거절.
        """
        with self._state_lock:
            modes = set(self.mode.values())
        return len(modes) == 1 and modes <= set(MODES)

    # ---- 단계 6: 담기 명령 /workcell/fill_basket ----
    def _make_fill_server(self):
        from fleet_interfaces.action import FillBasket
        from rclpy.action import ActionServer, CancelResponse, GoalResponse
        from rclpy.callback_groups import ReentrantCallbackGroup

        contract = _load_yaml(contract_path())
        entry = next(item for item in contract['actions'] if item['key'] == 'fill_basket')
        # 팀 pode_fsm/server_ros.py와 같게: 목표는 항상 받고, 거절은 결과(success=false, reason)로 알린다.
        self._fill_server = ActionServer(
            self._node, FillBasket, entry['name'],
            execute_callback=self._execute_fill,
            goal_callback=lambda _goal: GoalResponse.ACCEPT,
            cancel_callback=lambda _handle: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup())

    def check_fill_goal(self, goal, snapshot):
        """거절 사유 또는 None. 작업지시서 단계 6 표의 조건만 본다(R9)."""
        if not self.mode_ready():
            return MODE_MISMATCH
        if self._goal is not None:
            return NOT_READY
        if not app_idle(snapshot):
            return NOT_READY
        if goal.station_id != STATION:
            return UNKNOWN_TARGET
        if any(item.kind not in self.kinds for item in goal.items):
            return UNKNOWN_TARGET
        # TODO(DECIDE-4, FIX-2): 락은 1차에서 거절에 쓰지 않는다(SetLock 정의 없음).
        return None

    def finish_fill(self, goal_id, success, placed, reason):
        """앱 메인 스레드가 부른다: 목표 결과를 넘기고 끝낸다(기다리지 않음)."""
        with self._goal_lock:
            slot = self._goal
        if slot is None or slot['id'] != goal_id:
            return
        slot['result'] = (bool(success), int(placed), str(reason))
        slot['done'].set()

    def _fill_result(self, goal, success, placed, reason):
        from fleet_interfaces.action import FillBasket
        from fleet_interfaces.msg import OrderItem

        done, missing = split_placed([(item.kind, int(item.count)) for item in goal.items], placed)
        result = FillBasket.Result()
        result.success = success
        result.placed = [OrderItem(kind=kind, count=n) for kind, n in done]
        result.missing = [OrderItem(kind=kind, count=n) for kind, n in missing]
        result.reason = reason
        return result

    def _publish_fill_feedback(self, handle):
        from fleet_interfaces.action import FillBasket

        for arm_id, phase, placed in fill_feedbacks(self.read_snapshot()):
            handle.publish_feedback(FillBasket.Feedback(arm_id=arm_id, phase=phase,
                                                        placed_total=placed))

    def _execute_fill(self, handle):
        goal = handle.request
        with self._goal_lock:
            reason = self.check_fill_goal(goal, self.read_snapshot())
            if reason is None:
                self._goal_count += 1
                slot = self._goal = {'id': self._goal_count, 'done': threading.Event(),
                                     'result': None}
        if reason is not None:
            handle.abort()
            return self._fill_result(goal, False, 0, reason)
        self.push_command({
            'type': 'fill', 'goal': slot['id'], 'order_id': goal.order_id,
            'items': [(item.kind, self.kinds[item.kind], int(item.count)) for item in goal.items]})
        cancel_sent = False
        next_feedback = 0.0
        # 팀 server_ros.py와 같은 실행 루프: 결과를 기다리며 취소 전달과 피드백만 한다.
        while not slot['done'].wait(0.02):
            if self._stopping.is_set():
                break
            if handle.is_cancel_requested and not cancel_sent:
                self.push_command({'type': 'cancel', 'goal': slot['id']})
                cancel_sent = True
            now = time.monotonic()
            if now >= next_feedback:
                next_feedback = now + FEEDBACK_PERIOD_SEC
                self._publish_fill_feedback(handle)
        with self._goal_lock:
            self._goal = None
        if slot['result'] is None:  # 노드를 끄는 중
            handle.abort()
            return self._fill_result(goal, False, 0, NOT_READY)
        success, placed, reason = slot['result']
        if success:
            handle.succeed()
        elif handle.is_cancel_requested:
            handle.canceled()
        else:
            handle.abort()
        return self._fill_result(goal, success, placed, reason)

    def stop(self):
        self._stopping.set()
        if self._executor is not None:
            self._executor.shutdown()
        if self._thread is not None:
            self._thread.join()
        if self._node is not None:
            self._node.destroy_node()
        if self._context is not None and self._context.ok():
            self._context.shutdown()
        self._executor = self._thread = self._node = self._context = None
        self._state_pubs = {}
