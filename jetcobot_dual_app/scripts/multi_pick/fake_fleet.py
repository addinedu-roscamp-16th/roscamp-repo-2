"""시험용 가짜 관제(fleet). 팀 UI가 나오기 전까지 터미널에서 Phody에 명령을 보낸다.

작업 지시: docs/worklog/2026-11-26_fleet연결_작업지시.md (단계 7)

실행 (jazzy + ~/phody_fleet_ws source, 앱과 같은 ROS_DOMAIN_ID):
    python3 ~/pinky_ws/src/jetcobot_dual/jetcobot_dual_app/scripts/multi_pick/fake_fleet.py

명령:
    mode NORMAL | mode TEST      두 팔에 set_mode
    fill N [kind]                kind(기본 chocobar) N개 담기 목표
    cancel                       진행 중 목표 취소
    state                        두 팔 상태 한 번 출력
    quit
"""
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_link import ARMS, PENDING_NAMES, _load_yaml, contract_path, qos_from_contract  # noqa: E402

HELP = ('commands: mode NORMAL|TEST, fill N [kind], cancel, state, quit')
DEFAULT_KIND = 'chocobar'
SENDING = object()  # goal sent, handle not back yet (ClientGoalHandle == str raises)


def parse_command(line):
    """입력 한 줄 -> (명령, 인자들) 또는 ('error', 설명)."""
    words = line.split()
    if not words:
        return ('none',)
    cmd, args = words[0].lower(), words[1:]
    if cmd == 'mode' and len(args) == 1:
        return ('mode', args[0].upper())
    if cmd == 'fill' and len(args) in (1, 2):
        try:
            count = int(args[0])
        except ValueError:
            return ('error', f'count must be a number: {args[0]!r}')
        return ('fill', count, args[1] if len(args) == 2 else DEFAULT_KIND)
    if cmd in ('cancel', 'state', 'quit', 'exit', 'help') and not args:
        return ('quit',) if cmd == 'exit' else (cmd,)
    return ('error', HELP)


def arm_label(arm_id):
    """arm_left -> 'LEFT ', arm_right -> 'RIGHT', '' -> 'PHODY'."""
    return {'arm_left': 'LEFT ', 'arm_right': 'RIGHT'}.get(arm_id, 'PHODY')


def items_text(items):
    return ', '.join(f'{item.kind} x{item.count}' for item in items) or '-'


class FakeFleet:
    def __init__(self, out=print, node_name='phody_fake_fleet'):
        self.out = out
        self.node_name = node_name
        self.order_number = 0
        self._lock = threading.Lock()
        self._goal_handle = None
        self._requested = 0
        self._last_feedback = {}   # arm_id -> (phase, placed)
        self._last_state = {}      # arm_id -> (state, detail)
        self.results = []          # 받은 결과 (시험용)
        self._context = self._node = self._executor = self._thread = None

    # ---- 켜고 끄기 ----
    def start(self):
        import rclpy
        from fleet_interfaces.action import FillBasket
        from fleet_interfaces.msg import ArmState
        from fleet_interfaces.srv import SetMode
        from rclpy.action import ActionClient
        from rclpy.executors import MultiThreadedExecutor

        contract = _load_yaml(contract_path())
        pending = _load_yaml(PENDING_NAMES)['arm_state']
        mode_name = next(s for s in contract['services'] if s['key'] == 'robot_set_mode')['name']
        fill_name = next(a for a in contract['actions'] if a['key'] == 'fill_basket')['name']
        qos = qos_from_contract(contract, pending['qos'])

        self._context = rclpy.Context()
        rclpy.init(context=self._context)
        self._node = rclpy.create_node(self.node_name, context=self._context)
        self._mode_clients = {arm: self._node.create_client(SetMode, mode_name.replace('{robot}', arm))
                              for arm in ARMS}
        self._fill = ActionClient(self._node, FillBasket, fill_name)
        for arm in pending['arms']:
            self._node.create_subscription(ArmState, pending['name'].replace('{arm}', arm),
                                           self._on_state, qos)
        self._executor = MultiThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, name='fake_fleet', daemon=True)
        self._thread.start()

    def stop(self):
        if self._executor is not None:
            self._executor.shutdown()
        if self._thread is not None:
            self._thread.join()
        if self._node is not None:
            self._node.destroy_node()
        if self._context is not None and self._context.ok():
            self._context.shutdown()
        self._context = self._node = self._executor = self._thread = None

    # ---- 명령 ----
    def send_mode(self, mode):
        from fleet_interfaces.srv import SetMode

        for arm, client in self._mode_clients.items():
            if not client.service_is_ready():
                self.out(f'[MODE ] {arm}: set_mode server not found')
                continue
            request = SetMode.Request(request_id=str(uuid.uuid4()), mode=mode)
            future = client.call_async(request)
            future.add_done_callback(lambda f, arm=arm: self._on_mode_reply(arm, f))

    def _on_mode_reply(self, arm, future):
        reply = future.result()
        text = 'accepted' if reply.accepted else f'rejected {reply.reason} {reply.detail}'.rstrip()
        self.out(f'[MODE ] {arm} {text}')

    def send_fill(self, count, kind=DEFAULT_KIND):
        from fleet_interfaces.action import FillBasket
        from fleet_interfaces.msg import OrderItem

        with self._lock:
            if self._goal_handle is not None:
                self.out('[GOAL ] a goal is still running; cancel it first')
                return
            if not self._fill.server_is_ready():
                self.out('[GOAL ] /workcell/fill_basket server not found (app with fleet:=true?)')
                return
            self.order_number += 1
            order_id = f'O-TEST-{self.order_number:04d}'
            self._requested = count
            self._last_feedback = {}
            self._goal_handle = SENDING
        # robot_id: 바구니를 실은 Pode. 가짜 관제라 비워 둔다.
        goal = FillBasket.Goal(order_id=order_id, robot_id='', station_id='WORK',
                               items=[OrderItem(kind=kind, count=count)])
        self.out(f'[GOAL ] {order_id} sent: {kind} x{count}')
        future = self._fill.send_goal_async(goal, feedback_callback=self._on_feedback)
        future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future):
        handle = future.result()
        if not handle.accepted:  # Phody는 항상 받는다. 받지 않으면 결과도 없음
            with self._lock:
                self._goal_handle = None
            self.out('[GOAL ] not accepted')
            return
        with self._lock:
            self._goal_handle = handle
        handle.get_result_async().add_done_callback(self._on_result)

    def _on_feedback(self, message):
        fb = message.feedback
        key = (fb.phase, fb.placed_total)
        with self._lock:
            if self._last_feedback.get(fb.arm_id) == key:
                return
            self._last_feedback[fb.arm_id] = key
            requested = self._requested
        self.out(f'[{arm_label(fb.arm_id)}] {fb.phase:<10} placed {fb.placed_total}/{requested}')

    def _on_result(self, future):
        reply = future.result()
        result = reply.result
        with self._lock:
            self._goal_handle = None
            self.results.append(result)
        self.out(f'[RESULT] success={result.success} placed={items_text(result.placed)} '
                 f'missing={items_text(result.missing)} reason={result.reason or "-"}')

    def cancel(self):
        with self._lock:
            handle = self._goal_handle
        if handle is None or handle is SENDING:
            self.out('[GOAL ] no running goal')
            return
        self.out('[GOAL ] cancel requested')
        handle.cancel_goal_async()

    def _on_state(self, msg):
        key = (msg.state, msg.detail)
        with self._lock:
            if self._last_state.get(msg.arm_id) == key:
                return
            self._last_state[msg.arm_id] = key
        self.out(f'[STATE] {arm_label(msg.arm_id).strip()}={msg.state}'
                 + (f' ({msg.detail})' if msg.detail else ''))

    def print_state(self):
        with self._lock:
            states = dict(self._last_state)
        if not states:
            self.out('[STATE] no arm state received yet')
            return
        self.out('[STATE] ' + '  '.join(
            f'{arm_label(arm).strip()}={state}' + (f' ({detail})' if detail else '')
            for arm, (state, detail) in sorted(states.items())))


def main():
    fleet = FakeFleet()
    fleet.start()
    print(HELP, flush=True)
    try:
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            command = parse_command(line)
            name = command[0]
            if name == 'quit':
                break
            if name == 'mode':
                fleet.send_mode(command[1])
            elif name == 'fill':
                fleet.send_fill(command[1], command[2])
            elif name == 'cancel':
                fleet.cancel()
            elif name == 'state':
                fleet.print_state()
            elif name in ('help', 'error'):
                print(command[1] if name == 'error' else HELP, flush=True)
    finally:
        fleet.stop()


if __name__ == '__main__':
    main()
