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
LOCK_RELEASED = 'LOCK_RELEASED'   # 락이 풀린 동안 담기 거절 (팀 통합 절차 3.1)
NOT_STOPPED = 'NOT_STOPPED'       # 이어서 하기: 앱이 멈춰 있지 않음 (U와 같은 조건)
RESUME_SAME_PRESS_SEC = 2.0       # 두 팔에 온 이어서 하기를 한 번의 [재개]로 보는 간격
STATION = 'WORK'            # Phody 작업대 (팀 config/robots.yaml)
FEEDBACK_PERIOD_SEC = 0.5
# 피드백 phase (TODO(DECIDE-3): 팀이 값을 정하면 바꾼다)
PHASE_CAPTURING = 'CAPTURING'
PHASE_PICKING = 'PICKING'
PLACE_PHASE = {'STAGE2_ENTER': 'TO_BASKET', 'STAGE3_PLACE': 'PLACING', 'STAGE2_RETURN': 'RETURNING'}
STATE_PERIOD_SEC = 0.2    # 명세 3.5: 팔 상태 약 5 Hz
HEARTBEAT_LOST = 'HEARTBEAT_LOST'   # 11-27 Pode fleet: contract 0.10 사유 코드에 들어올 예정
HEARTBEAT_LOST_SEC = 30.0           # 11-27 Pode fleet: 30초 끊기면 detail에 싣고 작업은 계속
GATE_RESET_SERVICE = '/jetcobot_dual/reset_execution_fault'
BRIDGE_STATE_TOPIC = '/{side}/real_joint_states'  # Pi 브리지 노드가 직접 내보냄(재시작하면 GID가 바뀜)
BRIDGE_CHECK_SEC = 1.0  # 게이트(jetcobot_dual_control) 고장 잠금 해제
GATE_LATCHED_DETAIL = 'gate fault latched (motion canceled); fault_resolved needed'
PENDING_NAMES = Path(__file__).resolve().parents[2] / 'config' / 'fleet_names_pending.yaml'

# 앱 WorkState.arms 값 -> ArmState.state (contract 0.9, 명세 4.5. 11-27 PH-07)
APP_TO_ARM_STATE = {
    'idle': 'IDLE',
    'pick_waiting': 'PICK_WAITING',
    'picking': 'PICKING',
    'place_waiting': 'PLACE_WAITING',  # 물건을 쥐고 바구니 대기
    'placing': 'PLACING',        # B 진입 ~ C ~ B 복귀(바구니 점유). 두 팔 동시에 되지 않는다
    'paused': 'PAUSED',
    'error': 'ERROR',
}
# 11-27 회의: 한 팔이 멈추거나 고장 나면 두 팔 모두 그 상태로 보고한다(arm_state_fields).
# 미세조정 정지(AUTO FINE PAUSED)만으로는 앱이 picking 그대로라 PICKING으로 보인다(P 전까지 정상 대기).
# TODO(PH-11): gripper 칸은 아직 비워 둔다(/{side}/gripper_value를 이 노드가 받지 않음).


def arm_state_fields(snapshot, arm):
    """앱 스냅샷 -> (state, detail). 스냅샷에 팔 상태가 없으면 None.

    11-27 회의: 한 팔이 장애면 두 팔 모두 ERROR, 앱이 멈춰 있으면(P · worker 실패 정지) 두 팔 모두 PAUSED.
    """
    arms = snapshot.get('arms')
    if not arms:
        return None
    side = ARM_SIDE[arm]
    reasons = snapshot.get('reasons') or {}
    if any(state == 'error' for state in arms.values()):
        return 'ERROR', reasons.get('service') or reasons.get(side) or ''
    recovering = snapshot.get('self_recovery')
    if recovering:  # 11-27: Phody's own U retry -> the work state + "self-recovery n/2"
        state = recovering['state'] if arms[side] == 'paused' else APP_TO_ARM_STATE[arms[side]]
        return state, recovering['text']
    if snapshot.get('paused') or any(state == 'paused' for state in arms.values()):
        last = snapshot.get('last_pause') or ('',)
        detail = reasons.get(side) or next((r for r in reasons.values() if r), '') or str(last[0] or '')
        error = snapshot.get('last_error') or ''
        if error and error not in detail:  # 11-27: 일꾼 로그의 마지막 오류 줄
            detail = f'{detail} | {error}' if detail else error
        if snapshot.get('gate_latched'):  # 11-27 (가): 관리자가 fault_resolved를 보내야 함
            detail = f'{GATE_LATCHED_DETAIL}; {detail}' if detail else GATE_LATCHED_DETAIL
        return 'PAUSED', detail
    return APP_TO_ARM_STATE[arms[side]], reasons.get(side) or ''


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
        # 관리 명령도 서비스 · 팔마다 따로 기억한다 (IR-21)
        self.admin_requests = {(name, arm): RequestMemory()
                               for name in ('set_lock', 'fault_resolved', 'resume_arm', 'set_pause_true')
                               for arm in ARMS}
        self.last_request_id = {arm: '' for arm in ARMS}  # ArmState.last_request_id
        self._last_resume_at = float('-inf')
        self._bridge_gids = {}         # side -> 지금 boot_id 동안 본 브리지 publisher GID
        self._heartbeat_at = None      # 마지막 /fleet/heartbeat 받은 시각 (받은 적 없으면 None)
        self._heartbeat_lost = False
        # 켜질 때 상태 (IR-72, 작업지시서 단계 2)
        self.boot_id = str(uuid.uuid4())
        self.mode = {arm: MODE_UNKNOWN for arm in ARMS}
        self.lock = {arm: True for arm in ARMS}  # 켜질 때 잠김(IR-72). set_lock으로 바뀜
        self._state_lock = threading.Lock()
        self._context = None
        self._node = None
        self._state_pubs = {}
        self._executor = None
        self._thread = None
        self._gate_reset = None    # SetBool client (start()에서 만듦)
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
        self._make_heartbeat()
        self._make_mode_servers()
        self._make_admin_servers()
        self._make_fill_server()
        self._executor = MultiThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, name='fleet_link', daemon=True)
        self._thread.start()

    # ---- 단계 4: 팔 상태 ----
    def _make_state_publishers(self):
        from fleet_interfaces.msg import ArmState
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup

        # contract 0.9부터 arm_state가 팀 contract에 있다(FIX-1, PH-02). Phody 팔은 ARMS 둘만.
        contract = _load_yaml(contract_path())
        entry = next(item for item in contract['topics'] if item['key'] == 'arm_state')
        qos = qos_from_contract(contract, entry['qos'])
        for arm in ARMS:
            topic = entry['name'].replace('{arm}', arm)
            self._state_pubs[arm] = self._node.create_publisher(ArmState, topic, qos)
        self._node.create_timer(STATE_PERIOD_SEC, self.publish_arm_states,
                                callback_group=MutuallyExclusiveCallbackGroup())
        self._node.create_timer(BRIDGE_CHECK_SEC, self._check_bridges,
                                callback_group=MutuallyExclusiveCallbackGroup())

    # ---- 11-27: 브리지 재시작 = [초기화] -> 새 boot_id (사용자 결정: 앱 재시작 · 브리지 재시작 둘 다) ----
    def _check_bridges(self):
        for side in ARM_SIDE.values():
            infos = self._node.get_publishers_info_by_topic(BRIDGE_STATE_TOPIC.format(side=side))
            self.note_bridge_gids(side, {bytes(info.endpoint_gid) for info in infos})

    def note_bridge_gids(self, side, gids):
        """처음 본 GID는 기억만 한다. 아는 GID가 있는데 새 GID가 나타나면 그 브리지가 다시 켜진 것.

        Wi-Fi 끊김은 같은 프로그램이라 GID가 그대로다(관절 상태 끊김과 구분됨).
        """
        if not gids:
            return False
        seen = self._bridge_gids.get(side)
        if seen is None:
            self._bridge_gids[side] = set(gids)
            return False
        if gids <= seen:
            return False
        self._bridge_gids[side] = set(gids)
        self.restart_boot(f'{side} bridge restarted')
        return True

    def restart_boot(self, why):
        """IR-72 재시작: 새 boot_id, 모드 UNKNOWN, 락 잠김. 앱에는 멈추고 목표를 끝내라고 알린다."""
        with self._state_lock:
            self.boot_id = str(uuid.uuid4())
            self.mode = {arm: MODE_UNKNOWN for arm in ARMS}
            self.lock = {arm: True for arm in ARMS}
            self.last_request_id = {arm: '' for arm in ARMS}
        self.push_command({'type': 'bridge_restart', 'why': why})

    # ---- 11-27: 관제 신호 /fleet/heartbeat ----
    def _make_heartbeat(self):
        from std_msgs.msg import Header

        contract = _load_yaml(contract_path())
        entry = next(item for item in contract['topics'] if item['key'] == 'heartbeat')
        self._node.create_subscription(Header, entry['name'], self._on_heartbeat,
                                       qos_from_contract(contract, entry['qos']))

    def _on_heartbeat(self, _msg):
        with self._state_lock:
            self._heartbeat_at = time.monotonic()

    def heartbeat_lost(self, now=None):
        """한 번이라도 받은 뒤 HEARTBEAT_LOST_SEC 넘게 끊겼나. 바뀌면 앱 화면에 알린다."""
        now = time.monotonic() if now is None else now
        with self._state_lock:
            lost = self._heartbeat_at is not None and now - self._heartbeat_at > HEARTBEAT_LOST_SEC
            changed = lost != self._heartbeat_lost
            self._heartbeat_lost = lost
        if changed:
            self.push_command({'type': 'heartbeat', 'lost': lost})
        return lost

    def publish_arm_states(self):
        from fleet_interfaces.msg import ArmState

        snapshot = self.read_snapshot()
        lost = self.heartbeat_lost()
        stamp = self._node.get_clock().now().to_msg()
        held = set(snapshot.get('held_arms') or ())
        goal = snapshot.get('fleet_goal') or {}
        for arm, pub in self._state_pubs.items():
            fields = arm_state_fields(snapshot, arm)
            if fields is None:  # 앱이 아직 첫 스냅샷을 쓰지 않음(사용자 승인 11-26)
                return
            msg = ArmState()
            msg.arm_id = arm
            msg.state, msg.detail = fields
            if lost:  # 작업은 계속하고 사유만 싣는다
                msg.detail = f'{HEARTBEAT_LOST}; {msg.detail}' if msg.detail else HEARTBEAT_LOST
            # 11-27 PH-07 · PH-11 (contract 0.9 ArmState 칸)
            with self._state_lock:
                msg.boot_id = self.boot_id
                msg.mode = self.mode[arm]
                msg.lock = self.lock[arm]
                msg.last_request_id = self.last_request_id[arm]
            msg.holding = ARM_SIDE[arm] in held        # B · C 동안 상품을 쥔 팔(앱 held_jobs)
            msg.order_id = str(goal.get('order_id') or '')
            msg.placed_total = int(goal.get('placed', 0))  # 이번 담기 목표에서 담은 수(두 팔 합)
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
            if request_id:
                with self._state_lock:
                    self.last_request_id[arm] = request_id
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

    # ---- 11-27: 관리 명령 (set_lock · fault_resolved · resume_arm) ----
    def _make_admin_servers(self):
        from fleet_interfaces.srv import RobotCommand, SetLock
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
        from std_srvs.srv import SetBool

        self._gate_reset = self._node.create_client(SetBool, GATE_RESET_SERVICE)

        contract = _load_yaml(contract_path())
        services = {item['key']: item['name'] for item in contract['services']}
        # TODO(Q-05): 이어서 하기 이름은 팀이 아직 정하지 않음 -> pending yaml (사용자 결정 11-27)
        resume_name = _load_yaml(PENDING_NAMES)['arm_resume']['name']
        group = MutuallyExclusiveCallbackGroup()
        for arm in ARMS:
            self._node.create_service(
                SetLock, services['robot_set_lock'].replace('{robot}', arm),
                lambda rq, rs, arm=arm: self._answer(rs, rq.request_id, self.set_lock(
                    arm, rq.request_id, rq.locked, rq.confirm_drop)),
                callback_group=group)
            self._node.create_service(
                RobotCommand, services['arm_fault_resolved'].replace('{arm}', arm),
                lambda rq, rs, arm=arm: self._answer(rs, rq.request_id, self.fault_resolved(
                    arm, rq.request_id)),
                callback_group=group)
            self._node.create_service(
                RobotCommand, resume_name.replace('{arm}', arm),
                lambda rq, rs, arm=arm: self._answer(rs, rq.request_id, self.resume_arm(
                    arm, rq.request_id)),
                callback_group=group)

    @staticmethod
    def _answer(response, request_id, reply):
        response.accepted, response.reason, response.detail = reply
        response.request_id = request_id
        return response

    def _admin(self, name, arm, request_id, decide):
        """같은 request_id는 다시 반영하지 않고 같은 답 (IR-21). decide() -> (accepted, reason, detail)."""
        memory = self.admin_requests[(name, arm)]
        if request_id:
            seen = memory.get(request_id)
            if seen is not None:
                return seen
        reply = decide()
        if request_id:
            memory.put(request_id, reply)
            if reply[0]:
                with self._state_lock:
                    self.last_request_id[arm] = request_id
        return reply

    def set_lock(self, arm, request_id, locked, confirm_drop=False):
        """락 (11-27 회의: 우리 안). 해제 = 두 팔 see_center + 그리퍼 열기, 모터 힘 유지.

        해제는 잠김 -> 풀림으로 바뀔 때만 앱에 한 번 시킨다. 다시 잠그면 값만 바꾸고 팔은 안 움직인다.
        쥔 상품 확인(confirm_drop · HOLDING_ITEM)은 fleet이 보내기 전에 한다(PI-27).
        """
        def decide():
            with self._state_lock:
                was_locked = self.lock[arm]
                self.lock[arm] = bool(locked)
            if was_locked and not locked:
                self.push_command({'type': 'lock_release', 'arm': ARM_SIDE[arm]})
            return (True, '', '')
        return self._admin('set_lock', arm, request_id, decide)

    def fault_resolved(self, arm, request_id):
        """장애 처리 완료 = 게이트 고장 잠금 해제 요청 (11-27 사용자 결정 (가)).

        관리자가 로봇이 멈춘 것을 확인한 뒤 보낸다. 게이트가 스스로 두 팔 정지 · 새 관절 상태를
        확인한 뒤에만 푼다. 콜백 안에서 기다리지 않고(IR-31) 받았다고 먼저 답한다(답변 2-5 Phody 제안).
        결과는 앱에 넘기고, 팔 상태 detail의 잠금 문구가 사라지는 것으로 보인다.
        TODO(PI-2C): 회의에서 "받음 ≠ 끝남"이 정해지면 다시 본다.
        """
        def decide():
            from std_srvs.srv import SetBool
            client = self._gate_reset
            if client is None or not client.service_is_ready():
                return (False, NOT_READY, f'{GATE_RESET_SERVICE} not found')
            future = client.call_async(SetBool.Request(data=True))
            future.add_done_callback(self._on_gate_reset)
            return (True, '', 'gate reset requested')
        return self._admin('fault_resolved', arm, request_id, decide)

    def _on_gate_reset(self, future):
        try:
            reply = future.result()
            ok, message = bool(reply.success), str(reply.message)
        except Exception as exc:  # noqa: BLE001 (서비스 실패도 앱에 알림)
            ok, message = False, f'gate reset call failed: {exc}'
        self.push_command({'type': 'gate_reset', 'ok': ok, 'message': message})

    def set_pause(self, arm, request_id, paused):
        """11-27 Pode fleet: set_pause(true) = P, set_pause(false) = [복구].

        TODO(Q-05): contract 0.10의 /{arm}/set_pause(SetPause)가 들어오면 서버를 이것에 연결한다.
        지금 임시 서버 /{arm}/resume_arm은 false만 보낸다.
        """
        if paused:
            return self._admin('set_pause_true', arm, request_id, lambda: (
                self.push_command({'type': 'pause', 'arm': ARM_SIDE[arm]}) or (True, '', '')))
        return self.resume_arm(arm, request_id)

    def resume_arm(self, arm, request_id):
        """이어서 하기 = 앱 U와 같다 (11-27 사용자 결정). 두 팔이 함께 멈추므로 어느 팔로 와도 앱 전체.

        U처럼 앱이 멈춰 있을 때만 받는다. 실제 복구 성공 여부는 팔 상태(PAUSED를 벗어남)로 보인다.
        """
        def decide():
            snapshot = self.read_snapshot()
            if not snapshot.get('paused'):
                return (False, NOT_STOPPED, 'app is not paused')
            with self._state_lock:
                unlocked = not all(self.lock.values())
            if unlocked:  # 11-27 팔 관제 계약 2판: 먼저 다시 잠근 뒤에 [복구]
                return (False, LOCK_RELEASED, 'lock released; lock again before resume')
            if snapshot.get('gate_latched'):  # 11-27 사용자 승인: 순서를 틀리면 팔에 보내지 않음
                return (False, NOT_READY, 'gate fault latched; send fault_resolved first')
            # 11-27 관제 답: [재개] 때 관제가 두 팔 모두에 보낸다. 두 팔이 한 앱이라 U는 한 번만.
            now = time.monotonic()
            with self._state_lock:
                recent = now - self._last_resume_at < RESUME_SAME_PRESS_SEC
                if not recent:
                    self._last_resume_at = now
            if recent:
                return (True, '', 'resume already requested by the other arm')
            self.push_command({'type': 'resume', 'arm': ARM_SIDE[arm]})
            return (True, '', '')
        return self._admin('resume_arm', arm, request_id, decide)

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
        with self._state_lock:
            unlocked = not all(self.lock.values())
        if unlocked:  # 팀 통합 절차 3.1: 락이 풀린 동안 담기는 LOCK_RELEASED
            return LOCK_RELEASED
        if self._goal is not None:
            return NOT_READY
        if not app_idle(snapshot):
            return NOT_READY
        if goal.station_id != STATION:
            return UNKNOWN_TARGET
        if any(item.kind not in self.kinds for item in goal.items):
            return UNKNOWN_TARGET
        return None

    def finish_fill(self, goal_id, success, placed, reason, uncertain=0):
        """앱 메인 스레드가 부른다: 목표 결과를 넘기고 끝낸다(기다리지 않음).

        uncertain = 끝날 때 쥐고 있던 상품 수(PH-09).
        """
        with self._goal_lock:
            slot = self._goal
        if slot is None or slot['id'] != goal_id:
            return
        slot['result'] = (bool(success), int(placed), str(reason), int(uncertain))
        slot['done'].set()

    def _fill_result(self, goal, success, placed, reason, uncertain=0):
        from fleet_interfaces.action import FillBasket
        from fleet_interfaces.msg import OrderItem

        done, rest = split_placed([(item.kind, int(item.count)) for item in goal.items], placed)
        # PH-09: 쥐고 있던 상품은 못 담은 것(missing)에서 빼서 uncertain으로 싣는다(겹쳐 세지 않음).
        held, missing = split_placed(rest, uncertain)
        result = FillBasket.Result()
        result.success = success
        result.placed = [OrderItem(kind=kind, count=n) for kind, n in done]
        result.placed_confirmed = [OrderItem(kind=kind, count=n) for kind, n in done]  # 그리퍼를 연 수
        result.missing = [OrderItem(kind=kind, count=n) for kind, n in missing]
        result.uncertain = [OrderItem(kind=kind, count=n) for kind, n in held]
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
        success, placed, reason, uncertain = slot['result']
        if success:
            handle.succeed()
        elif handle.is_cancel_requested:
            handle.canceled()
        else:
            handle.abort()
        return self._fill_result(goal, success, placed, reason, uncertain)

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
