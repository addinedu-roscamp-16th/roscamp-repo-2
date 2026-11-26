"""fleet 연결 뼈대 단위 테스트. 앱과 로봇 없이 가짜 스냅샷으로 확인한다."""
import os
import threading
import time
import unittest

from types import SimpleNamespace
from unittest.mock import patch

from multi_pick.fleet_link import (APP_TO_ARM_STATE, ARMS, FleetLink, MODE_UNKNOWN, RequestMemory,
                                   SnapshotBox, app_idle, arm_state_fields, fill_feedbacks,
                                   split_placed)

# 실물 도메인 83과 섞이지 않게, 그리고 앞 테스트의 노드가 발견 정보에 남아 목표를 잃지 않게
# ROS 테스트마다 다른 도메인을 쓴다(84~99를 돌아가며).
_DOMAINS = iter(range(10**6))


def next_test_domain():
    return str(84 + next(_DOMAINS) % 16)


def ros_ready():
    try:
        import rclpy  # noqa: F401
        from fleet_interfaces.msg import ArmState  # noqa: F401
        return True
    except ImportError:
        return False


class TestDomain:
    def setUp(self):
        self._old_domain = os.environ.get('ROS_DOMAIN_ID')
        os.environ['ROS_DOMAIN_ID'] = next_test_domain()

    def tearDown(self):
        if self._old_domain is None:
            os.environ.pop('ROS_DOMAIN_ID', None)
        else:
            os.environ['ROS_DOMAIN_ID'] = self._old_domain


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RequestMemoryTests(unittest.TestCase):
    def test_same_request_id_returns_same_response(self):
        memory = RequestMemory()
        memory.put('r1', {'accepted': True})
        self.assertEqual(memory.get('r1'), {'accepted': True})
        self.assertIsNone(memory.get('r2'))

    def test_keeps_only_latest_32(self):
        memory = RequestMemory()
        for i in range(33):
            memory.put(f'r{i}', i)
        self.assertIsNone(memory.get('r0'))
        self.assertEqual(memory.get('r1'), 1)
        self.assertEqual(memory.get('r32'), 32)

    def test_forgets_after_60_seconds(self):
        clock = FakeClock()
        memory = RequestMemory(clock=clock)
        memory.put('r1', 'a')
        clock.now = 60.0
        self.assertEqual(memory.get('r1'), 'a')
        clock.now = 60.1
        self.assertIsNone(memory.get('r1'))


class SnapshotAndQueueTests(unittest.TestCase):
    def test_snapshot_read_is_a_copy(self):
        box = SnapshotBox()
        app_state = {'arms': {'left': 'idle', 'right': 'picking'}}
        box.write(app_state)
        app_state['arms']['left'] = 'placing'  # 앱이 나중에 바꿔도 스냅샷은 그대로
        seen = box.read()
        self.assertEqual(seen['arms']['left'], 'idle')
        seen['arms']['right'] = 'error'  # 읽은 쪽이 바꿔도 스냅샷은 그대로
        self.assertEqual(box.read()['arms']['right'], 'picking')

    def test_commands_come_out_in_order_without_waiting(self):
        link = FleetLink()
        self.assertEqual(link.pop_commands(), [])
        link.push_command({'type': 'a'})
        link.push_command({'type': 'b'})
        self.assertEqual(link.pop_commands(), [{'type': 'a'}, {'type': 'b'}])
        self.assertEqual(link.pop_commands(), [])

    def test_link_reads_app_snapshot(self):
        box = SnapshotBox()
        link = FleetLink(snapshot=box)
        box.write({'placed_total': 2})
        self.assertEqual(link.read_snapshot(), {'placed_total': 2})


class BootStateTests(unittest.TestCase):
    def test_boot_state(self):
        a, b = FleetLink(), FleetLink()
        self.assertNotEqual(a.boot_id, b.boot_id)
        self.assertEqual(a.mode, {arm: MODE_UNKNOWN for arm in ARMS})
        self.assertEqual(a.lock, {'arm_left': True, 'arm_right': True})


@unittest.skipUnless(ros_ready(), 'rclpy / fleet_interfaces 없음 (jazzy + ~/phody_fleet_ws source 후 실행)')
class StartStopTests(TestDomain, unittest.TestCase):
    def test_start_spins_in_own_thread_and_stops(self):
        link = FleetLink(node_name='phody_fleet_link_test')
        link.start()
        try:
            self.assertEqual(link.node.get_name(), 'phody_fleet_link_test')
            spin_threads = [t for t in threading.enumerate() if t.name == 'fleet_link']
            self.assertEqual(len(spin_threads), 1)
            self.assertIsNot(spin_threads[0], threading.main_thread())
        finally:
            link.stop()
        self.assertIsNone(link.node)
        self.assertFalse(any(t.name == 'fleet_link' for t in threading.enumerate()))


class ArmStateFieldTests(unittest.TestCase):
    def test_mapping_uses_dev_constants_only(self):
        expected = {'idle': 'IDLE', 'pick_waiting': 'IDLE', 'picking': 'PICKING',
                    'place_waiting': 'PICKING', 'placing': 'PLACING', 'paused': 'ERROR',
                    'error': 'ERROR'}
        self.assertEqual(APP_TO_ARM_STATE, expected)
        for app_state, arm_state in expected.items():
            snap = {'arms': {'left': app_state, 'right': 'idle'}, 'reasons': {}}
            self.assertEqual(arm_state_fields(snap, 'arm_left')[0], arm_state)

    def test_detail(self):
        snap = {'arms': {'left': 'paused', 'right': 'idle'},
                'reasons': {'left': 'PLAN worker exit=1'}}
        self.assertEqual(arm_state_fields(snap, 'arm_left'), ('ERROR', 'PLAN worker exit=1'))
        self.assertEqual(arm_state_fields(snap, 'arm_right'), ('IDLE', ''))
        snap = {'arms': {'left': 'error', 'right': 'error'}, 'reasons': {'service': 'gate fault'}}
        self.assertEqual(arm_state_fields(snap, 'arm_right'), ('ERROR', 'gate fault'))

    def test_no_snapshot_yet(self):
        self.assertIsNone(arm_state_fields({}, 'arm_left'))


@unittest.skipUnless(ros_ready(), 'rclpy / fleet_interfaces 없음 (jazzy + ~/phody_fleet_ws source 후 실행)')
class ArmStatePublishTests(TestDomain, unittest.TestCase):
    def test_contract_state_qos(self):
        from rclpy.qos import DurabilityPolicy, ReliabilityPolicy
        from multi_pick.fleet_link import _load_yaml, contract_path, qos_from_contract
        qos = qos_from_contract(_load_yaml(contract_path()), 'STATE')
        self.assertEqual(qos.reliability, ReliabilityPolicy.BEST_EFFORT)
        self.assertEqual(qos.durability, DurabilityPolicy.VOLATILE)
        self.assertEqual(qos.depth, 5)

    def test_publishes_both_arms_at_5hz_after_first_snapshot(self):
        import rclpy
        from fleet_interfaces.msg import ArmState
        from rclpy.executors import SingleThreadedExecutor
        from multi_pick.fleet_link import _load_yaml, contract_path, qos_from_contract

        ctx = rclpy.Context()
        rclpy.init(context=ctx)
        node = rclpy.create_node('fleet_state_listener', context=ctx)
        got = {'arm_left': [], 'arm_right': []}
        qos = qos_from_contract(_load_yaml(contract_path()), 'STATE')
        for arm in got:
            node.create_subscription(ArmState, f'/{arm}/state',
                                     lambda msg, arm=arm: got[arm].append(msg), qos)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)

        def spin_for(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                executor.spin_once(timeout_sec=0.05)

        link = FleetLink(node_name='phody_fleet_link_test')
        link.start()
        try:
            spin_for(1.0)  # 스냅샷 전: 발행 없음
            self.assertEqual(got, {'arm_left': [], 'arm_right': []})
            link.snapshot.write({'arms': {'left': 'placing', 'right': 'paused'},
                                 'reasons': {'right': 'FINE worker exit=2'}})
            spin_for(0.5)  # 발견 시간
            for arm in got:
                got[arm].clear()
            spin_for(2.0)
        finally:
            link.stop()
            executor.shutdown()
            node.destroy_node()
            ctx.shutdown()
        for arm in got:
            self.assertGreaterEqual(len(got[arm]), 8, arm)   # 2초 x 5 Hz = 10
            self.assertLessEqual(len(got[arm]), 12, arm)
        left, right = got['arm_left'][-1], got['arm_right'][-1]
        self.assertEqual((left.arm_id, left.state, left.detail), ('arm_left', 'PLACING', ''))
        self.assertEqual((right.arm_id, right.state, right.detail),
                         ('arm_right', 'ERROR', 'FINE worker exit=2'))
        self.assertGreater(left.stamp.sec, 0)


class SetModeTests(unittest.TestCase):
    def test_accepts_normal_and_test_per_arm(self):
        link = FleetLink()
        self.assertEqual(link.set_mode('arm_left', 'r1', 'NORMAL'), (True, '', ''))
        self.assertEqual(link.mode, {'arm_left': 'NORMAL', 'arm_right': MODE_UNKNOWN})
        self.assertEqual(link.set_mode('arm_right', 'r2', 'TEST'), (True, '', ''))
        self.assertEqual(link.mode, {'arm_left': 'NORMAL', 'arm_right': 'TEST'})

    def test_rejects_unknown_mode_without_storing(self):
        link = FleetLink()
        self.assertEqual(link.set_mode('arm_left', 'r1', 'FAST'),
                         (False, 'MODE_MISMATCH', "unknown mode 'FAST'"))
        self.assertEqual(link.mode['arm_left'], MODE_UNKNOWN)

    def test_same_request_id_same_answer_not_reapplied(self):
        link = FleetLink()
        link.set_mode('arm_left', 'r1', 'NORMAL')
        link.set_mode('arm_left', 'r2', 'TEST')
        self.assertEqual(link.set_mode('arm_left', 'r1', 'NORMAL'), (True, '', ''))
        self.assertEqual(link.mode['arm_left'], 'TEST')  # r1을 다시 반영하지 않음
        # 팔마다 따로 기억: 오른팔의 r1은 새 요청
        link.set_mode('arm_right', 'r1', 'TEST')
        self.assertEqual(link.mode['arm_right'], 'TEST')

    def test_mode_ready_only_when_both_arms_same_mode(self):
        cases = [((MODE_UNKNOWN, MODE_UNKNOWN), False), (('NORMAL', MODE_UNKNOWN), False),
                 (('NORMAL', 'TEST'), False), (('NORMAL', 'NORMAL'), True), (('TEST', 'TEST'), True)]
        for (left, right), ready in cases:
            link = FleetLink()
            link.mode = {'arm_left': left, 'arm_right': right}
            self.assertEqual(link.mode_ready(), ready, (left, right))


@unittest.skipUnless(ros_ready(), 'rclpy / fleet_interfaces 없음 (jazzy + ~/phody_fleet_ws source 후 실행)')
class SetModeServiceTests(TestDomain, unittest.TestCase):
    def test_service_call(self):
        import rclpy
        from fleet_interfaces.srv import SetMode

        link = FleetLink(node_name='phody_fleet_link_test')
        link.start()
        ctx = rclpy.Context()
        rclpy.init(context=ctx)
        node = rclpy.create_node('fleet_mode_client', context=ctx)
        from rclpy.executors import SingleThreadedExecutor
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        try:
            def call(arm, request_id, mode):
                client = node.create_client(SetMode, f'/{arm}/set_mode')
                self.assertTrue(client.wait_for_service(timeout_sec=5.0))
                future = client.call_async(SetMode.Request(request_id=request_id, mode=mode))
                executor.spin_until_future_complete(future, timeout_sec=5.0)
                return future.result()

            reply = call('arm_left', 'q1', 'NORMAL')
            self.assertEqual((reply.accepted, reply.reason, reply.request_id), (True, '', 'q1'))
            reply = call('arm_right', 'q2', 'BAD')
            self.assertEqual((reply.accepted, reply.reason, reply.request_id),
                             (False, 'MODE_MISMATCH', 'q2'))
            self.assertFalse(link.mode_ready())
            call('arm_right', 'q3', 'NORMAL')
            self.assertTrue(link.mode_ready())
        finally:
            executor.shutdown()
            node.destroy_node()
            ctx.shutdown()
            link.stop()


IDLE_SNAPSHOT = {'arms': {'left': 'idle', 'right': 'idle'}, 'queue_started': False, 'paused': False,
                 'held': 0, 'motion_busy': False, 'place_busy': False, 'fleet_goal': None}


def fill_goal(station='WORK', items=(('chocobar', 2),)):
    return SimpleNamespace(order_id='O-1', robot_id='', station_id=station,
                           items=[SimpleNamespace(kind=k, count=n) for k, n in items])


class FillGoalCheckTests(unittest.TestCase):
    def _link(self):
        link = FleetLink()
        link.mode = {'arm_left': 'NORMAL', 'arm_right': 'NORMAL'}
        return link

    def test_accepts_ready_goal(self):
        self.assertIsNone(self._link().check_fill_goal(fill_goal(), IDLE_SNAPSHOT))

    def test_reject_reasons(self):
        link = FleetLink()  # 모드 UNKNOWN
        self.assertEqual(link.check_fill_goal(fill_goal(), IDLE_SNAPSHOT), 'MODE_MISMATCH')
        link = self._link()
        link._goal = {'id': 1}
        self.assertEqual(link.check_fill_goal(fill_goal(), IDLE_SNAPSHOT), 'NOT_READY')
        link = self._link()
        for change in ({'queue_started': True}, {'paused': True}, {'held': 1},
                       {'motion_busy': True}, {'place_busy': True},
                       {'arms': {'left': 'paused', 'right': 'idle'}}, {'arms': None}):
            self.assertEqual(link.check_fill_goal(fill_goal(), dict(IDLE_SNAPSHOT, **change)),
                             'NOT_READY', change)
        self.assertEqual(link.check_fill_goal(fill_goal(station='SHIP'), IDLE_SNAPSHOT),
                         'UNKNOWN_TARGET')
        self.assertEqual(link.check_fill_goal(fill_goal(items=(('mentos', 1),)), IDLE_SNAPSHOT),
                         'UNKNOWN_TARGET')

    def test_kind_mapping_default(self):
        self.assertEqual(FleetLink().kinds, {'chocobar': 'chocolate_bar'})

    def test_app_idle(self):
        self.assertTrue(app_idle(IDLE_SNAPSHOT))
        self.assertFalse(app_idle(dict(IDLE_SNAPSHOT, fleet_goal={'captured': False})))

    def test_split_placed(self):
        self.assertEqual(split_placed([('chocobar', 3)], 2), ([('chocobar', 2)], [('chocobar', 1)]))
        self.assertEqual(split_placed([('chocobar', 2)], 2), ([('chocobar', 2)], []))
        self.assertEqual(split_placed([('chocobar', 2)], 0), ([], [('chocobar', 2)]))


class FillFeedbackTests(unittest.TestCase):
    def test_capturing(self):
        snap = dict(IDLE_SNAPSHOT, fleet_goal={'captured': False, 'placed': 0})
        self.assertEqual(fill_feedbacks(snap), [('', 'CAPTURING', 0)])

    def test_both_arms_moving_gives_both(self):
        snap = dict(IDLE_SNAPSHOT, fleet_goal={'captured': True, 'placed': 1},
                    basket_owner='left', place_stage='STAGE3_PLACE', place_busy=True,
                    active_arm='right', motion_busy=True, motion_stage='FINE')
        self.assertEqual(fill_feedbacks(snap), [('arm_left', 'PLACING', 1),
                                                ('arm_right', 'PICKING', 1)])

    def test_one_arm(self):
        snap = dict(IDLE_SNAPSHOT, fleet_goal={'captured': True, 'placed': 0},
                    active_arm='left', motion_busy=True, motion_stage='EXECUTE',
                    basket_owner=None, place_stage=None)
        self.assertEqual(fill_feedbacks(snap), [('arm_left', 'PICKING', 0)])
        # 순차 모드: 담기 단계가 픽 칸(motion)에서 돈다
        snap.update(basket_owner='left', motion_stage='STAGE2_RETURN')
        self.assertEqual(fill_feedbacks(snap), [('arm_left', 'RETURNING', 0)])

    def test_nothing_moving_or_no_goal(self):
        self.assertEqual(fill_feedbacks(IDLE_SNAPSHOT), [])
        snap = dict(IDLE_SNAPSHOT, fleet_goal={'captured': True, 'placed': 0}, active_arm=None,
                    basket_owner=None, place_stage=None, motion_stage=None)
        self.assertEqual(fill_feedbacks(snap), [])


class FakeFleet:
    def __init__(self):
        self.finished = []
        self.snapshot = SnapshotBox()

    def finish_fill(self, goal_id, success, placed, reason):
        self.finished.append((goal_id, success, placed, reason))


class AppFillTests(unittest.TestCase):
    def _app(self):
        from multi_pick.sam_multi_real import MultiApp
        from multi_pick.state_machine import WorkState
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.fleet = FakeFleet()
        app.fleet_placed = 0
        app.active_arm = app.active_object = None
        app.place_stage = app.motion_stage = None
        app.queue_started = False
        app.queue, app.held_jobs = [], []
        app.operator_paused = False
        app.motion_child = app.place_child = None
        app.status = ''
        return app

    def _goal(self, app, **change):
        app.fleet_goal = dict({'goal': 7, 'order_id': 'O-1', 'classes': {'chocolate_bar'},
                               'requested': 2, 'found': 2, 'placed_start': 0, 'captured': True,
                               'canceled': False, 'error': False}, **change)

    def test_end_cases(self):
        cases = [
            ({}, {}, (True, 2, '')),
            ({'found': 1}, {}, (True, 2, 'OUT_OF_STOCK')),
            ({'error': True}, {}, (False, 2, 'RECOGNITION_ERROR')),
            ({}, {'operator_paused': True,
                  'fleet_last_pause': ('PLAN worker exit=1', 'PLAN', False)},
             (False, 2, 'PICK_FAILED')),
            ({}, {'operator_paused': True,
                  'fleet_last_pause': ('FINE worker exit=3', 'FINE', False)},
             (False, 2, 'PICK_FAILED')),
            ({}, {'operator_paused': True,
                  'fleet_last_pause': ('STAGE3_PLACE worker exit=1', 'STAGE3_PLACE', False)},
             (False, 2, 'ARM_FAULT')),
            ({}, {'operator_paused': True, 'fleet_last_pause': ('operator pressed P', None, False)},
             (False, 2, 'ARM_FAULT')),
            ({}, {'operator_paused': True, 'fleet_last_pause': ('operator pressed P', None, True)},
             (False, 2, 'PICK_FAILED')),
            ({'canceled': True}, {'operator_paused': True,
                                  'fleet_last_pause': ('fleet canceled', None, False)},
             (False, 2, 'CANCELED')),
        ]
        for goal_change, app_change, expected in cases:
            app = self._app()
            self._goal(app, **goal_change)
            app.fleet_placed = 2
            for key, value in app_change.items():
                setattr(app, key, value)
            app._fleet_check_goal_end()
            self.assertEqual(app.fleet.finished, [(7,) + expected], (goal_change, app_change))
            self.assertIsNone(app.fleet_goal)

    def test_not_finished_while_running(self):
        app = self._app()
        self._goal(app)
        app.queue_started = True
        app._fleet_check_goal_end()
        app.queue_started = False
        app.held_jobs = [{'arm': 'left'}]
        app._fleet_check_goal_end()
        app.held_jobs = []
        app.place_child = object()
        app._fleet_check_goal_end()
        self.assertEqual(app.fleet.finished, [])

    def test_start_fill_not_ready(self):
        app = self._app()
        app.queue_started = True
        app._fleet_start_fill({'goal': 3, 'order_id': 'O', 'items': [('chocobar', 'chocolate_bar', 1)]})
        self.assertEqual(app.fleet.finished, [(3, False, 0, 'NOT_READY')])
        self.assertIsNone(app.fleet_goal)

    def test_start_fill_camera_failure_is_error(self):
        app = self._app()
        with patch.object(type(app), 'reset', return_value=None), \
                patch.object(type(app), '_freeze', return_value=False), \
                patch.object(type(app), 'run') as run:
            app._fleet_start_fill({'goal': 3, 'order_id': 'O',
                                   'items': [('chocobar', 'chocolate_bar', 2)]})
        run.assert_not_called()
        self.assertTrue(app.fleet_goal['error'])
        self.assertEqual(app.fleet_goal['requested'], 2)

    def test_candidates_keep_requested_class_only(self):
        app = self._app()
        self._goal(app, found=None)
        objects = [{'id': 'object_1', 'class_name': 'chocolate_bar'},
                   {'id': 'object_2', 'class_name': 'mentos_pair'},
                   {'id': 'object_3', 'class_name': 'chocolate_bar'}]
        self.assertEqual([o['id'] for o in app._fleet_candidates(objects)], ['object_1', 'object_3'])
        self.assertEqual(app.fleet_goal['found'], 2)

    def test_side_choice_for_two_of_three(self):
        import numpy as np
        from multi_pick.state_machine import alternating_assignments
        app = self._app()
        # 오른팔 받침 (0,0), 왼팔 받침 (400,0) mm
        T = np.eye(4)
        T[0, 3] = 0.4
        app.live_poses = SimpleNamespace(T_right_left=T)
        objects = [{'id': 'near_right', 'center_xyz_mm': [50, 100, 20]},
                   {'id': 'middle', 'center_xyz_mm': [200, 100, 20]},
                   {'id': 'near_left', 'center_xyz_mm': [350, 100, 20]}]
        queue = app._order_by_side(objects, alternating_assignments(3, 'left')[:2])
        self.assertEqual([(o['id'], o['assigned_arm']) for o in queue],
                         [('near_left', 'left'), ('near_right', 'right')])

    def test_replan_with_spare(self):
        import numpy as np
        from multi_pick.sam_multi_real import sam
        app = self._app()
        self._goal(app)
        T = np.eye(4)
        T[0, 3] = 0.4
        app.live_poses = SimpleNamespace(T_right_left=T)
        app.work.request_pick('left')
        app.active_arm = 'left'
        app.active_object = {'id': 'bad', 'center_xyz_mm': [350, 100, 20]}
        app.queue = [{'id': 'next', 'assigned_arm': 'right', 'center_xyz_mm': [50, 100, 20]}]
        app.fleet_spares = [{'id': 'spare_right', 'center_xyz_mm': [60, 100, 20]},
                            {'id': 'spare_left', 'center_xyz_mm': [330, 100, 20]}]
        with patch.object(sam.SAMYawApp, 'reset'), \
                patch.object(type(app), 'start_next_object') as start:
            app._fleet_replan_with_spare(1)
        start.assert_called_once()
        self.assertEqual((app.queue[0]['id'], app.queue[0]['assigned_arm']), ('spare_left', 'left'))
        self.assertEqual(app.queue[1]['id'], 'next')
        self.assertEqual([o['id'] for o in app.fleet_spares], ['spare_right'])
        self.assertIsNone(app.work.pick_owner)
        self.assertEqual(app.work.arms['left'], 'idle')

    def test_cancel_command_pauses_like_p(self):
        app = self._app()
        self._goal(app)
        app.queue_started = True
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'cancel', 'goal': 7})
        calls = []
        with patch.object(type(app), 'pause_motion',
                          side_effect=lambda reason, **kw: calls.append(reason)):
            app._fleet_tick()
        self.assertEqual(calls, ['fleet canceled'])
        self.assertTrue(app.fleet_goal['canceled'])


@unittest.skipUnless(ros_ready(), 'rclpy / fleet_interfaces 없음 (jazzy + ~/phody_fleet_ws source 후 실행)')
class FillActionTests(TestDomain, unittest.TestCase):
    """실제 액션 클라이언트 <-> FleetLink. 앱 대신 작은 가짜 앱 스레드가 명령을 처리한다."""

    def setUp(self):
        super().setUp()
        import rclpy
        from fleet_interfaces.action import FillBasket
        from rclpy.action import ActionClient
        from rclpy.executors import SingleThreadedExecutor
        self.link = FleetLink(node_name='phody_fleet_link_test')
        self.link.snapshot.write(IDLE_SNAPSHOT)
        self.link.start()
        self.ctx = rclpy.Context()
        rclpy.init(context=self.ctx)
        self.node = rclpy.create_node('fake_fleet_client', context=self.ctx)
        self.executor = SingleThreadedExecutor(context=self.ctx)
        self.executor.add_node(self.node)
        self.client = ActionClient(self.node, FillBasket, '/workcell/fill_basket')
        self.assertTrue(self.client.wait_for_server(timeout_sec=5.0))
        self.FillBasket = FillBasket

    def tearDown(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.ctx.shutdown()
        self.link.stop()
        super().tearDown()

    def _send(self, count=2):
        from fleet_interfaces.msg import OrderItem
        goal = self.FillBasket.Goal(order_id='O-1', station_id='WORK',
                                    items=[OrderItem(kind='chocobar', count=count)])
        feedback = []
        future = self.client.send_goal_async(goal, feedback_callback=lambda fb: feedback.append(
            (fb.feedback.arm_id, fb.feedback.phase, fb.feedback.placed_total)))
        self.executor.spin_until_future_complete(future, timeout_sec=5.0)
        handle = future.result()
        self.assertTrue(handle.accepted)
        return handle, feedback

    def _result(self, handle):
        future = handle.get_result_async()
        self.executor.spin_until_future_complete(future, timeout_sec=10.0)
        return future.result()

    def _fake_app(self, on_fill):
        def loop():
            end = time.monotonic() + 8.0
            while time.monotonic() < end:
                for command in self.link.pop_commands():
                    if on_fill(command):
                        return
                time.sleep(0.02)
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread

    def test_reject_before_set_mode(self):
        handle, _ = self._send()
        reply = self._result(handle)
        self.assertFalse(reply.result.success)
        self.assertEqual(reply.result.reason, 'MODE_MISMATCH')
        self.assertEqual([(i.kind, i.count) for i in reply.result.missing], [('chocobar', 2)])

    def test_goal_feedback_result(self):
        self.link.mode = {'arm_left': 'NORMAL', 'arm_right': 'NORMAL'}
        state = {}

        def on_fill(command):
            if command['type'] != 'fill':
                return False
            self.assertEqual(command['items'], [('chocobar', 'chocolate_bar', 3)])
            self.link.snapshot.write(dict(
                IDLE_SNAPSHOT, queue_started=True, fleet_goal={'captured': True, 'placed': 1},
                basket_owner='left', place_stage='STAGE3_PLACE', place_busy=True,
                active_arm='right', motion_busy=True, motion_stage='FINE'))
            time.sleep(1.2)
            state['goal'] = command['goal']
            self.link.finish_fill(command['goal'], True, 2, 'OUT_OF_STOCK')
            return True

        thread = self._fake_app(on_fill)
        handle, feedback = self._send(count=3)
        reply = self._result(handle)
        thread.join()
        self.assertTrue(reply.result.success)
        self.assertEqual([(i.kind, i.count) for i in reply.result.placed], [('chocobar', 2)])
        self.assertEqual([(i.kind, i.count) for i in reply.result.missing], [('chocobar', 1)])
        self.assertEqual(reply.result.reason, 'OUT_OF_STOCK')
        self.assertIn(('arm_left', 'PLACING', 1), feedback)
        self.assertIn(('arm_right', 'PICKING', 1), feedback)

    def test_second_goal_while_running_is_not_ready(self):
        self.link.mode = {'arm_left': 'TEST', 'arm_right': 'TEST'}
        release = threading.Event()

        def on_fill(command):
            if command['type'] == 'fill':
                release.wait(5.0)
                self.link.finish_fill(command['goal'], True, 2, '')
                return True
            return False

        thread = self._fake_app(on_fill)
        first, _ = self._send()
        time.sleep(0.3)
        second, _ = self._send()
        reply2 = self._result(second)
        release.set()
        reply1 = self._result(first)
        thread.join()
        self.assertEqual(reply2.result.reason, 'NOT_READY')
        self.assertTrue(reply1.result.success)

    def test_cancel(self):
        from action_msgs.msg import GoalStatus
        self.link.mode = {'arm_left': 'NORMAL', 'arm_right': 'NORMAL'}
        seen = []

        def on_fill(command):
            seen.append(command['type'])
            if command['type'] == 'cancel':
                self.link.finish_fill(command['goal'], False, 0, 'CANCELED')
                return True
            return False

        thread = self._fake_app(on_fill)
        handle, _ = self._send()
        time.sleep(0.3)
        cancel = handle.cancel_goal_async()
        self.executor.spin_until_future_complete(cancel, timeout_sec=5.0)
        reply = self._result(handle)
        thread.join()
        self.assertEqual(seen, ['fill', 'cancel'])
        self.assertEqual(reply.status, GoalStatus.STATUS_CANCELED)
        self.assertEqual(reply.result.reason, 'CANCELED')


class FakeFleetParseTests(unittest.TestCase):
    def test_parse(self):
        from multi_pick.fake_fleet import parse_command
        self.assertEqual(parse_command('mode normal'), ('mode', 'NORMAL'))
        self.assertEqual(parse_command('fill 2'), ('fill', 2, 'chocobar'))
        self.assertEqual(parse_command('fill 3 mentos'), ('fill', 3, 'mentos'))
        self.assertEqual(parse_command('cancel'), ('cancel',))
        self.assertEqual(parse_command('exit'), ('quit',))
        self.assertEqual(parse_command(''), ('none',))
        self.assertEqual(parse_command('fill two')[0], 'error')
        self.assertEqual(parse_command('dance')[0], 'error')

    def test_arm_label(self):
        from multi_pick.fake_fleet import arm_label
        self.assertEqual([arm_label(a) for a in ('arm_left', 'arm_right', '')],
                         ['LEFT ', 'RIGHT', 'PHODY'])


def wait_until(check, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(ros_ready(), 'rclpy / fleet_interfaces 없음 (jazzy + ~/phody_fleet_ws source 후 실행)')
class FakeFleetLiveTests(TestDomain, unittest.TestCase):
    """fake_fleet <-> FleetLink. 앱 대신 작은 가짜 앱 스레드가 명령을 처리한다."""

    def setUp(self):
        super().setUp()
        from multi_pick.fake_fleet import FakeFleet
        self.link = FleetLink(node_name='phody_fleet_link_test')
        self.link.snapshot.write(IDLE_SNAPSHOT)
        self.link.start()
        self.lines = []
        self.fleet = FakeFleet(out=self.lines.append, node_name='fake_fleet_test')
        self.fleet.start()
        self.assertTrue(wait_until(lambda: self.fleet._fill.server_is_ready() and all(
            c.service_is_ready() for c in self.fleet._mode_clients.values())))
        self.stop_app = threading.Event()

    def tearDown(self):
        self.stop_app.set()
        self.fleet.stop()
        self.link.stop()
        super().tearDown()

    def _fake_app(self, on_command):
        def loop():
            while not self.stop_app.is_set():
                for command in self.link.pop_commands():
                    on_command(command)
                time.sleep(0.02)
        threading.Thread(target=loop, daemon=True).start()

    def test_mode_fill_feedback_result_state(self):
        def on_command(command):
            if command['type'] == 'fill':
                self.link.snapshot.write(dict(
                    IDLE_SNAPSHOT, arms={'left': 'placing', 'right': 'picking'}, queue_started=True,
                    fleet_goal={'captured': True, 'placed': 1}, basket_owner='left',
                    place_stage='STAGE3_PLACE', place_busy=True, active_arm='right',
                    motion_busy=True, motion_stage='FINE'))
                time.sleep(1.2)
                self.link.snapshot.write(IDLE_SNAPSHOT)
                self.link.finish_fill(command['goal'], True, 2, '')

        self._fake_app(on_command)
        self.fleet.send_mode('NORMAL')
        self.assertTrue(wait_until(self.link.mode_ready))
        self.fleet.send_fill(2)
        self.assertTrue(wait_until(lambda: self.fleet.results))
        text = '\n'.join(self.lines)
        self.assertIn('[MODE ] arm_left accepted', text)
        self.assertIn('[MODE ] arm_right accepted', text)
        self.assertIn('[GOAL ] O-TEST-0001 sent: chocobar x2', text)
        self.assertIn('[LEFT ] PLACING    placed 1/2', text)
        self.assertIn('[RIGHT] PICKING    placed 1/2', text)
        self.assertIn('[RESULT] success=True placed=chocobar x2 missing=- reason=-', text)
        self.assertIn('[STATE] LEFT=PLACING', text)
        self.assertEqual(text.count('[LEFT ] PLACING'), 1)  # 같은 피드백은 한 번만 출력

    def test_cancel(self):
        def on_command(command):
            if command['type'] == 'cancel':
                self.link.finish_fill(command['goal'], False, 0, 'CANCELED')

        self._fake_app(on_command)
        self.fleet.send_mode('TEST')
        self.assertTrue(wait_until(self.link.mode_ready))
        self.fleet.send_fill(1)
        from multi_pick.fake_fleet import SENDING
        self.assertTrue(wait_until(
            lambda: self.fleet._goal_handle is not None and self.fleet._goal_handle is not SENDING))
        self.fleet.cancel()
        self.assertTrue(wait_until(lambda: self.fleet.results))
        self.assertIn('[RESULT] success=False placed=- missing=chocobar x1 reason=CANCELED',
                      '\n'.join(self.lines))

    def test_bad_mode_is_rejected(self):
        self.fleet.send_mode('FAST')
        self.assertTrue(wait_until(lambda: sum('[MODE ]' in line for line in self.lines) == 2))
        self.assertIn("[MODE ] arm_left rejected MODE_MISMATCH unknown mode 'FAST'", self.lines)


class AppHookTests(unittest.TestCase):
    """sam_multi_real 연결 지점: fleet 꺼짐이면 아무것도 안 함, 켜짐이면 큐 · 스냅샷."""

    class Stop(Exception):
        pass

    def _app(self):
        from multi_pick.sam_multi_real import MultiApp
        from multi_pick.state_machine import WorkState
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.active_arm = None
        app.place_stage = None
        app.motion_stage = None
        app.queue_started = False
        app.queue = []
        app.held_jobs = []
        app.operator_paused = False
        app.motion_child = None
        app.place_child = None
        return app

    def test_fleet_off_by_default(self):
        from multi_pick import sam_multi_real
        self.assertFalse(sam_multi_real.MULTI.fleet)
        self.assertIsNone(self._app().fleet)

    def test_poll_motion_skips_fleet_when_off(self):
        from unittest.mock import patch
        app = self._app()
        with patch.object(type(app), '_fleet_tick', side_effect=AssertionError('called')), \
                patch.object(type(app), '_record_stationary_plan_wait', side_effect=self.Stop):
            with self.assertRaises(self.Stop):
                app.poll_motion()

    def test_poll_motion_ticks_fleet_first_when_on(self):
        from unittest.mock import patch
        app = self._app()
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'x'})
        with patch.object(type(app), '_record_stationary_plan_wait', side_effect=self.Stop):
            with self.assertRaises(self.Stop):
                app.poll_motion()
        self.assertEqual(app.fleet.pop_commands(), [])  # 꺼내서 처리함
        snap = app.fleet.read_snapshot()
        self.assertEqual(snap['arms'], {'left': 'idle', 'right': 'idle'})
        self.assertEqual(snap['placed'], 0)
        self.assertFalse(snap['paused'])

    def test_pause_records_reason_only_when_fleet_on(self):
        from unittest.mock import patch
        for fleet in (None, FleetLink()):
            app = self._app()
            app.fleet = fleet
            app.operator_paused = True  # 기존 정지 처리 블록은 건너뜀
            app.motion_child = None
            app.recovery = None
            with patch.object(type(app), 'stop_motion'):
                app.pause_motion('test reason', stage='PLAN')
            expected = None if fleet is None else ('test reason', 'PLAN', False)
            self.assertEqual(app.fleet_last_pause, expected)

    def test_pause_remembers_auto_fine_pause(self):
        app = self._app()
        app.fleet = FleetLink()
        app.operator_paused = True
        app.motion_child = None
        app.recovery = None
        app.auto_fine_paused = True  # AUTO FINE PAUSED 뒤에 P
        with patch.object(type(app), 'stop_motion'):
            app.pause_motion('operator pressed P')
        self.assertEqual(app.fleet_last_pause, ('operator pressed P', None, True))


if __name__ == '__main__':
    unittest.main()
