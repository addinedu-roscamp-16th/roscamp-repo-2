"""fleet 연결 뼈대 단위 테스트. 앱과 로봇 없이 가짜 스냅샷으로 확인한다."""
import os
import threading
import time
import unittest

from pathlib import Path
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
    def test_mapping_uses_contract_constants(self):
        # 11-27 PH-07: contract 0.9 ArmState 상수
        from fleet_interfaces.msg import ArmState
        expected = {'idle': ArmState.STATE_IDLE, 'pick_waiting': ArmState.STATE_PICK_WAITING,
                    'picking': ArmState.STATE_PICKING, 'place_waiting': ArmState.STATE_PLACE_WAITING,
                    'placing': ArmState.STATE_PLACING, 'paused': ArmState.STATE_PAUSED,
                    'error': ArmState.STATE_ERROR}
        self.assertEqual(APP_TO_ARM_STATE, expected)
        for app_state, arm_state in expected.items():
            snap = {'arms': {'left': app_state, 'right': 'idle'}, 'reasons': {}}
            self.assertEqual(arm_state_fields(snap, 'arm_left')[0], arm_state)

    def test_detail_and_both_arms_rule(self):
        # 11-27 회의: 한 팔이 멈추면 두 팔 모두 PAUSED, 한 팔이 장애면 두 팔 모두 ERROR
        snap = {'arms': {'left': 'paused', 'right': 'idle'},
                'reasons': {'left': 'PLAN worker exit=1'}}
        self.assertEqual(arm_state_fields(snap, 'arm_left'), ('PAUSED', 'PLAN worker exit=1'))
        self.assertEqual(arm_state_fields(snap, 'arm_right'), ('PAUSED', 'PLAN worker exit=1'))
        snap = {'arms': {'left': 'picking', 'right': 'placing'}, 'reasons': {}, 'paused': True,
                'last_pause': ('operator pressed P', None, False)}
        self.assertEqual(arm_state_fields(snap, 'arm_right'), ('PAUSED', 'operator pressed P'))
        snap = {'arms': {'left': 'error', 'right': 'error'}, 'reasons': {'service': 'gate fault'}}
        self.assertEqual(arm_state_fields(snap, 'arm_right'), ('ERROR', 'gate fault'))
        snap = {'arms': {'left': 'idle', 'right': 'picking'}, 'reasons': {}}
        self.assertEqual(arm_state_fields(snap, 'arm_left'), ('IDLE', ''))

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
                                 'reasons': {'right': 'FINE worker exit=2'},
                                 'held_arms': ['left'],
                                 'fleet_goal': {'order_id': 'O-9', 'placed': 1}})
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
        # 11-27 회의: 한 팔이 멈추면 두 팔 모두 PAUSED
        self.assertEqual((left.arm_id, left.state, left.detail),
                         ('arm_left', 'PAUSED', 'FINE worker exit=2'))
        self.assertEqual((right.arm_id, right.state, right.detail),
                         ('arm_right', 'PAUSED', 'FINE worker exit=2'))
        self.assertGreater(left.stamp.sec, 0)
        # PH-07 · PH-11 칸
        self.assertEqual((left.mode, left.lock, left.boot_id), ('UNKNOWN', True, link.boot_id))
        self.assertEqual((left.holding, right.holding), (True, False))
        self.assertEqual((left.order_id, left.placed_total), ('O-9', 1))


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


def fill_goal(station='WORK', items=(('choco_bar', 2),)):
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
        self.assertEqual(FleetLink().kinds, {'choco_bar': 'chocolate_bar'})

    def test_app_idle(self):
        self.assertTrue(app_idle(IDLE_SNAPSHOT))
        self.assertFalse(app_idle(dict(IDLE_SNAPSHOT, fleet_goal={'captured': False})))

    def test_split_placed(self):
        self.assertEqual(split_placed([('choco_bar', 3)], 2), ([('choco_bar', 2)], [('choco_bar', 1)]))
        self.assertEqual(split_placed([('choco_bar', 2)], 2), ([('choco_bar', 2)], []))
        self.assertEqual(split_placed([('choco_bar', 2)], 0), ([], [('choco_bar', 2)]))


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

    def finish_fill(self, goal_id, success, placed, reason, uncertain=0):
        self.finished.append((goal_id, success, placed, reason) + ((uncertain,) if uncertain else ()))


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
        app.bg = None
        app.status = ''
        return app

    def _goal(self, app, **change):
        app.fleet_goal = dict({'goal': 7, 'order_id': 'O-1', 'classes': {'chocolate_bar'},
                               'requested': 2, 'found': 2, 'placed_start': 0, 'captured': True,
                               'canceled': False, 'error': False}, **change)

    def test_end_cases(self):
        cases = [
            ({}, {}, (True, 2, '')),
            ({'found': 1}, {}, (False, 2, 'RECOGNITION_ERROR')),   # 11-27 contract: success=false
            ({'error': True}, {}, (False, 2, 'RECOGNITION_ERROR')),
            ({'canceled': True}, {'operator_paused': True,
                                  'fleet_last_pause': ('fleet canceled', None, False)},
             (False, 2, 'CANCELED')),
            # 11-27 fleet note: a dropped item counts as uncertain at the end
            ({'canceled': True, 'dropped': 1}, {'operator_paused': True}, (False, 2, 'CANCELED', 1)),
            # PH-09: an item still held at the end -> uncertain
            ({'canceled': True}, {'operator_paused': True, 'held_jobs': [{'arm': 'left'}]},
             (False, 2, 'CANCELED', 1)),
            # 11-27: placed already (B return) -> not uncertain
            ({'canceled': True}, {'operator_paused': True,
                                  'held_jobs': [{'arm': 'left', 'fleet_released': True}]},
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

    def test_pause_keeps_goal_open(self):
        # 11-27 user decision (나): P, worker failure and fine failure all wait for resume / reset
        for pause in [('operator pressed P', None, False), ('operator pressed P', None, True),
                      ('PLAN worker exit=1', 'PLAN', False), ('FINE worker exit=3', 'FINE', False),
                      ('STAGE3_PLACE worker exit=1', 'STAGE3_PLACE', False)]:
            app = self._app()
            self._goal(app)
            app.operator_paused = True
            app.fleet_last_pause = pause
            app._fleet_check_goal_end()
            self.assertEqual(app.fleet.finished, [], pause)
            self.assertIsNotNone(app.fleet_goal)

    def test_returned_keeps_goal_open_as_paused(self):
        # 11-27 fleet note: no PICK_FAILED / ARM_FAULT; back at see_center = PAUSED
        app = self._app()
        self._goal(app, returned=True)
        app._fleet_check_goal_end()
        self.assertEqual(app.fleet.finished, [])
        app.fleet = FleetLink()
        app.fleet_goal['returned'] = True
        app.work.pick_owner = app.work.basket_owner = None
        app.fleet_last_pause = None
        app.fleet_gate_latched = False
        app.return_settling = None
        app._fleet_write_snapshot()
        self.assertTrue(app.fleet.read_snapshot()['paused'])

    def test_resume_after_return_recaptures_remaining(self):
        app = self._app()
        self._goal(app, returned=True, requested=3, dropped=1)
        app.fleet = FleetLink()
        app.fleet_placed = 1
        app.fleet.push_command({'type': 'resume', 'arm': 'left'})
        with patch.object(type(app), '_freeze', return_value=True), \
                patch.object(type(app), 'run',
                             side_effect=lambda: setattr(app, 'queue_started', True)) as run, \
                patch.object(type(app), 'recover_work') as u_key, \
                patch.object(type(app), '_fleet_write_snapshot'):
            app._fleet_tick()
        run.assert_called_once()
        u_key.assert_not_called()
        self.assertEqual(app.fleet_goal['need'], 1)   # 3 requested - 1 placed - 1 dropped
        self.assertFalse(app.fleet_goal['returned'])

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
        app._fleet_start_fill({'goal': 3, 'order_id': 'O', 'items': [('choco_bar', 'chocolate_bar', 1)]})
        self.assertEqual(app.fleet.finished, [(3, False, 0, 'NOT_READY')])
        self.assertIsNone(app.fleet_goal)

    def test_start_fill_camera_failure_is_error(self):
        app = self._app()
        with patch.object(type(app), 'reset', return_value=None), \
                patch.object(type(app), '_freeze', return_value=False), \
                patch.object(type(app), 'run') as run:
            app._fleet_start_fill({'goal': 3, 'order_id': 'O',
                                   'items': [('choco_bar', 'chocolate_bar', 2)]})
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

    def test_cancel_then_c_after_workers_stop(self):
        # 11-27: fleet reset = cancel -> C (start_reset_return) once the workers have exited
        app = self._app()
        self._goal(app)
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'cancel', 'goal': 7})
        app.motion_child = object()  # worker still exiting
        with patch.object(type(app), 'pause_motion',
                          side_effect=lambda reason, **kw: setattr(app, 'operator_paused', True)), \
                patch.object(type(app), 'start_reset_return') as c_key:
            app._fleet_tick()
            c_key.assert_not_called()
            app.motion_child = None
            app._fleet_tick()
            app._fleet_tick()
        c_key.assert_called_once()

    def test_pause_during_b_marks_gate_latched_until_reset(self):
        app = self._app()
        app.fleet = FleetLink()
        app.place_child, app.place_stage = SimpleNamespace(poll=lambda: None), 'STAGE2_RETURN'
        app.operator_paused = True  # only the fleet block of pause_motion runs below
        app.recovery = None
        with patch.object(type(app), 'stop_motion'):
            app.pause_motion('operator pressed P')
        self.assertTrue(app.fleet_gate_latched)
        app.place_child = None
        app.fleet.push_command({'type': 'gate_reset', 'ok': False, 'message': 'still moving'})
        app._fleet_tick()
        self.assertTrue(app.fleet_gate_latched)
        app.fleet.push_command({'type': 'gate_reset', 'ok': True, 'message': 'cleared'})
        app._fleet_tick()
        self.assertFalse(app.fleet_gate_latched)

    def test_resume_is_see_center_then_recapture(self):
        # 11-27 Pode fleet: [복구] = C (or see_center + open grippers), then recapture the rest
        app = self._app()
        self._goal(app)
        app.operator_paused = True
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'resume', 'arm': 'left'})
        with patch.object(type(app), 'start_reset_return') as c_key, \
                patch.object(type(app), 'start_miss_return') as miss_return, \
                patch.object(type(app), 'recover_work') as u_key:
            app._fleet_tick()
        c_key.assert_called_once()
        miss_return.assert_called_once()   # C did not start a worker in this test
        u_key.assert_not_called()
        self.assertFalse(app.fleet_recover_pending)  # neither started -> reported, not pending

    def test_recapture_after_return_when_recover_pending(self):
        app = self._app()
        self._goal(app, returned=True)
        app.fleet = FleetLink()
        app.fleet_recover_pending = True
        with patch.object(type(app), '_fleet_recapture') as recapture, \
                patch.object(type(app), '_fleet_write_snapshot'):
            app._fleet_tick()
        recapture.assert_called_once()
        self.assertFalse(app.fleet_recover_pending)

    def test_set_pause_true_is_p(self):
        app = self._app()
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'pause', 'arm': 'left'})
        with patch.object(type(app), 'pause_motion') as pause:
            app._fleet_tick()
        pause.assert_called_once_with('fleet set_pause(true)')

    def test_bridge_restart_stops_and_drops_goal(self):
        app = self._app()
        self._goal(app)
        app.queue_started = True
        app.held_jobs = [{'arm': 'left'}]
        app.fleet_placed = 1
        app.fleet = FleetLink()
        calls = []
        app.fleet.push_command({'type': 'bridge_restart', 'why': 'left bridge restarted'})
        real_finish = app.fleet.finish_fill
        with patch.object(type(app), 'pause_motion', side_effect=lambda r, **k: calls.append(r)), \
                patch.object(app.fleet, 'finish_fill', wraps=real_finish) as finish:
            app._fleet_tick()
        self.assertEqual(calls, ['fleet: left bridge restarted'])
        self.assertIsNone(app.fleet_goal)
        finish.assert_called_once_with(7, False, 1, 'CANCELED', uncertain=1)

    def test_heartbeat_flag(self):
        app = self._app()
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'heartbeat', 'lost': True})
        app._fleet_tick()
        self.assertTrue(app.fleet_heartbeat_lost)

    def test_lock_release_stops_and_returns_to_see_center(self):
        app = self._app()
        self._goal(app)
        app.fleet = FleetLink()
        app.fleet.push_command({'type': 'lock_release', 'arm': 'right'})
        calls = []
        with patch.object(type(app), 'pause_motion',
                          side_effect=lambda reason, **kw: (calls.append(reason),
                                                            setattr(app, 'operator_paused', True))), \
                patch.object(type(app), 'start_miss_return') as miss_return:
            app._fleet_tick()
        self.assertEqual(calls, ['fleet lock released'])
        miss_return.assert_called_once()
        self.assertIsNotNone(app.fleet_goal)  # 11-27 fleet note: goal stays open (PAUSED)


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
                                    items=[OrderItem(kind='choco_bar', count=count)])
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
        self.assertEqual([(i.kind, i.count) for i in reply.result.missing], [('choco_bar', 2)])

    def test_goal_feedback_result(self):
        self.link.mode = {'arm_left': 'NORMAL', 'arm_right': 'NORMAL'}
        state = {}

        def on_fill(command):
            if command['type'] != 'fill':
                return False
            self.assertEqual(command['items'], [('choco_bar', 'chocolate_bar', 3)])
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
        self.assertEqual([(i.kind, i.count) for i in reply.result.placed], [('choco_bar', 2)])
        self.assertEqual([(i.kind, i.count) for i in reply.result.missing], [('choco_bar', 1)])
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
        self.assertEqual(parse_command('fill 2'), ('fill', 2, 'choco_bar'))
        self.assertEqual(parse_command('fill 3 mentos'), ('fill', 3, 'mentos'))
        self.assertEqual(parse_command('cancel'), ('cancel',))
        self.assertEqual(parse_command('exit'), ('quit',))
        self.assertEqual(parse_command(''), ('none',))
        self.assertEqual(parse_command('fill two')[0], 'error')
        self.assertEqual(parse_command('dance')[0], 'error')
        # 11-27 관리 명령
        self.assertEqual(parse_command('resume'), ('resume', 'arm_left'))
        self.assertEqual(parse_command('resume right'), ('resume', 'arm_right'))
        self.assertEqual(parse_command('fault'), ('fault', 'arm_left'))
        self.assertEqual(parse_command('lock off'), ('lock', False, 'arm_left'))
        self.assertEqual(parse_command('lock on right'), ('lock', True, 'arm_right'))
        self.assertEqual(parse_command('lock maybe')[0], 'error')
        self.assertEqual(parse_command('resume up')[0], 'error')
        self.assertEqual(parse_command('hb off'), ('hb', False))
        self.assertEqual(parse_command('hb on'), ('hb', True))

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
        self.assertIn('[GOAL ] O-TEST-0001 sent: choco_bar x2', text)
        self.assertIn('[LEFT ] PLACING    placed 1/2', text)
        self.assertIn('[RIGHT] PICKING    placed 1/2', text)
        self.assertIn('[RESULT] success=True placed=choco_bar x2 missing=- reason=-', text)
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
        self.assertIn('[RESULT] success=False placed=- missing=choco_bar x1 reason=CANCELED',
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


class AdminCommandTests(unittest.TestCase):
    """11-27: set_lock · fault_resolved · resume_arm (ROS 없이 로직만)."""

    def test_lock_release_once_and_relock(self):
        link = FleetLink()
        self.assertEqual(link.set_lock('arm_left', 'r1', False), (True, '', ''))
        self.assertEqual(link.pop_commands(), [{'type': 'lock_release', 'arm': 'left'}])
        self.assertFalse(link.lock['arm_left'])
        # 같은 request_id: 다시 반영하지 않고 같은 답
        self.assertEqual(link.set_lock('arm_left', 'r1', False), (True, '', ''))
        # 이미 풀림 -> 다시 풀어도 팔은 안 움직임
        link.set_lock('arm_left', 'r2', False)
        self.assertEqual(link.pop_commands(), [])
        link.set_lock('arm_left', 'r3', True)
        self.assertTrue(link.lock['arm_left'])
        self.assertEqual(link.pop_commands(), [])
        self.assertEqual(link.last_request_id['arm_left'], 'r3')

    def test_fill_rejected_while_unlocked(self):
        link = FleetLink()
        link.set_mode('arm_left', 'm1', 'NORMAL')
        link.set_mode('arm_right', 'm2', 'NORMAL')
        link.set_lock('arm_right', 'r1', False)
        self.assertEqual(link.check_fill_goal(fill_goal(), IDLE_SNAPSHOT), 'LOCK_RELEASED')
        link.set_lock('arm_right', 'r2', True)
        self.assertIsNone(link.check_fill_goal(fill_goal(), IDLE_SNAPSHOT))

    def test_resume_only_when_paused(self):
        link = FleetLink()
        link.snapshot.write(IDLE_SNAPSHOT)
        self.assertEqual(link.resume_arm('arm_left', 'q1'), (False, 'NOT_STOPPED', 'app is not paused'))
        self.assertEqual(link.pop_commands(), [])
        link.snapshot.write(dict(IDLE_SNAPSHOT, paused=True))
        self.assertEqual(link.resume_arm('arm_right', 'q2'), (True, '', ''))
        self.assertEqual(link.pop_commands(), [{'type': 'resume', 'arm': 'right'}])
        # 11-27: the fleet sends [재개] to both arms -> U only once
        self.assertEqual(link.resume_arm('arm_left', 'q2b'),
                         (True, '', 'resume already requested by the other arm'))
        self.assertEqual(link.pop_commands(), [])
        link._last_resume_at -= 10
        self.assertEqual(link.resume_arm('arm_right', 'q2'), (True, '', ''))  # 같은 답, 다시 안 넣음
        self.assertEqual(link.pop_commands(), [])
        link.snapshot.write(dict(IDLE_SNAPSHOT, paused=True, gate_latched=True))
        self.assertEqual(link.resume_arm('arm_right', 'q3'),
                         (False, 'NOT_READY', 'gate fault latched; send fault_resolved first'))
        self.assertEqual(link.pop_commands(), [])

    def test_fault_resolved_without_gate_service(self):
        link = FleetLink()  # not started: no gate reset client
        self.assertEqual(link.fault_resolved('arm_left', 'f1'),
                         (False, 'NOT_READY', '/jetcobot_dual/reset_execution_fault not found'))

    def test_fault_resolved_calls_gate_reset_once(self):
        link = FleetLink()
        future = SimpleNamespace(add_done_callback=lambda cb: cb(SimpleNamespace(
            result=lambda: SimpleNamespace(success=True, message='PC fault latch cleared'))))
        calls = []
        link._gate_reset = SimpleNamespace(service_is_ready=lambda: True,
                                           call_async=lambda rq: calls.append(rq.data) or future)
        self.assertEqual(link.fault_resolved('arm_left', 'f1'), (True, '', 'gate reset requested'))
        self.assertEqual(link.fault_resolved('arm_left', 'f1'), (True, '', 'gate reset requested'))
        self.assertEqual(calls, [True])  # same request_id: not called again
        self.assertEqual(link.pop_commands(),
                         [{'type': 'gate_reset', 'ok': True, 'message': 'PC fault latch cleared'}])

    def test_latched_detail(self):
        snap = {'arms': {'left': 'idle', 'right': 'paused'}, 'reasons': {'right': 'operator pressed P'},
                'paused': True, 'gate_latched': True}
        state, detail = arm_state_fields(snap, 'arm_left')
        self.assertEqual(state, 'PAUSED')
        self.assertTrue(detail.startswith('gate fault latched'))


@unittest.skipUnless(ros_ready(), 'rclpy / fleet_interfaces 없음 (jazzy + ~/phody_fleet_ws source 후 실행)')
class ResultFieldTests(unittest.TestCase):
    def test_placed_confirmed_and_uncertain(self):
        link = FleetLink()
        result = link._fill_result(fill_goal(items=(('choco_bar', 3),)), False, 1, 'CANCELED', 1)
        pairs = lambda items: [(i.kind, i.count) for i in items]  # noqa: E731
        self.assertEqual(pairs(result.placed), [('choco_bar', 1)])
        self.assertEqual(pairs(result.placed_confirmed), [('choco_bar', 1)])
        self.assertEqual(pairs(result.uncertain), [('choco_bar', 1)])
        self.assertEqual(pairs(result.missing), [('choco_bar', 1)])


class PlaceRecoveryTests(unittest.TestCase):
    """11-27: U during B / C (concurrent mode, no pick recovery)."""

    def _app(self, stage, ok):
        from multi_pick.sam_multi_real import MultiApp
        from multi_pick.state_machine import WorkState
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.work.request_pick('left')
        app.work.pick_owner = None      # left lifted back to see_center
        app.work.request_place('left')
        app.work.pause('left', 'operator pressed P')
        job = {'arm': 'left', 'folder': '/tmp'}
        if stage:
            job.update(stage=stage, stage_ok=ok)
        app.held_jobs = [job]
        app.queue, app.queue_total = [], 1
        app.operator_paused, app.recovery = True, None
        app.place_child = app.motion_child = app.bg = None
        app.active_arm = app.active_object = None
        app.queue_started = True
        app.status = ''
        return app

    def run_u(self, app):
        calls = []
        with patch.object(type(app), '_continue_place',
                          side_effect=lambda job, stage: calls.append(('continue', stage))), \
                patch.object(type(app), '_start_place',
                             side_effect=lambda job: calls.append(('start', 'STAGE2_ENTER'))):
            app.recover_work()
        return calls

    def test_interrupted_stage_runs_again(self):
        for stage, expected in [('STAGE2_ENTER', ('start', 'STAGE2_ENTER')),
                                ('STAGE3_PLACE', ('continue', 'STAGE3_PLACE')),
                                ('STAGE2_RETURN', ('continue', 'STAGE2_RETURN'))]:
            app = self._app(stage, False)
            self.assertEqual(self.run_u(app), [expected], stage)
            self.assertFalse(app.operator_paused)
            self.assertEqual(app.work.arms['left'], 'placing')

    def test_finished_stage_goes_to_next(self):
        app = self._app('STAGE3_PLACE', True)
        self.assertEqual(self.run_u(app), [('continue', 'STAGE2_RETURN')])
        app = self._app('STAGE2_RETURN', True)
        self.assertEqual(self.run_u(app), [])
        self.assertEqual(app.held_jobs, [])
        self.assertEqual(app.work.arms['left'], 'idle')
        self.assertFalse(app.queue_started)

    def test_waits_for_workers(self):
        app = self._app('STAGE2_RETURN', False)
        app.place_child = object()
        self.assertEqual(self.run_u(app), [])
        self.assertTrue(app.operator_paused)


class ExecRetryTests(unittest.TestCase):
    """11-27: stale Pi joint state before auto-execute -> 1 s retry, pause after 30 s."""

    def _app(self, stale):
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        state = {'stale': stale}
        def snapshot():
            if state['stale']:
                raise RuntimeError('left joints are stale (no valid message received within 1.5 s).')
            return None, None, None
        app.live_poses = SimpleNamespace(snapshot=snapshot)
        app.motion_child = None
        app.exec_retry = {'deadline': 100.0, 'next': 0.0}
        return app, state

    def test_executes_when_fresh(self):
        app, state = self._app(stale=False)
        with patch.object(type(app), 'start_motion') as start, \
                patch('multi_pick.sam_multi_real.time.monotonic', return_value=5.0):
            app._poll_exec_retry()
        start.assert_called_once_with(execute=True)
        self.assertIsNone(app.exec_retry)

    def test_waits_then_pauses_after_deadline(self):
        app, state = self._app(stale=True)
        with patch.object(type(app), 'pause_motion') as pause, \
                patch.object(type(app), 'start_motion') as start, \
                patch('multi_pick.sam_multi_real.time.monotonic', return_value=50.0):
            app._poll_exec_retry()
            pause.assert_not_called()
            self.assertEqual(app.exec_retry['next'], 51.0)
        with patch.object(type(app), 'pause_motion') as pause, \
                patch('multi_pick.sam_multi_real.time.monotonic', return_value=100.0):
            app.exec_retry['next'] = 0.0
            app._poll_exec_retry()
        pause.assert_called_once()
        self.assertEqual(pause.call_args.kwargs['stage'], 'EXECUTE')
        self.assertIsNone(app.exec_retry)
        start.assert_not_called()

    def test_not_stale_for_other_errors(self):
        app, _ = self._app(stale=False)
        self.assertFalse(app._joints_stale())


class WorkerErrorTests(unittest.TestCase):
    def test_last_error_line_of_newest_log(self):
        import tempfile
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / 'run_01' / 'a.log'
            old.parent.mkdir()
            old.write_text('ERROR old\n')
            time.sleep(0.02)
            new = Path(folder) / 'run_01' / 'b.log'
            new.write_text('MoveIt case object is disabled\nSTAGE3 PLACE STOP: RuntimeError: '
                           'PLACE DESCENT JOINT GOAL rejected\nlast line\n')
            app.multi_folder = Path(folder)
            self.assertEqual(app._fleet_worker_error(),
                             'STAGE3 PLACE STOP: RuntimeError: PLACE DESCENT JOINT GOAL rejected')
        app.multi_folder = None
        self.assertEqual(app._fleet_worker_error(), '')

    def test_detail_carries_error(self):
        snap = {'arms': {'left': 'paused', 'right': 'idle'}, 'reasons': {'left': 'STAGE3_PLACE worker exit=1'},
                'paused': True, 'last_error': 'PLACE DESCENT JOINT GOAL rejected'}
        self.assertEqual(arm_state_fields(snap, 'arm_right'),
                         ('PAUSED', 'STAGE3_PLACE worker exit=1 | PLACE DESCENT JOINT GOAL rejected'))


class SelfRecoveryTests(unittest.TestCase):
    """11-27 user decision: Phody's own stop -> U by itself twice, then report (fleet on/off)."""

    def _app(self):
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        app.recovery = {'object': 'object_1', 'stage': 'EXECUTE'}
        app.held_jobs = []
        app.self_recovery, app.self_recovery_used = None, {}
        app.operator_paused = True
        app.motion_child = app.place_child = app.bg = None
        return app

    def test_two_tries_then_report(self):
        app = self._app()
        for n in (1, 2):
            app._schedule_self_recovery('EXECUTE worker exit=1')
            self.assertEqual(app.self_recovery['n'], n)
            app.self_recovery = None
        app._schedule_self_recovery('EXECUTE worker exit=1')
        self.assertIsNone(app.self_recovery)   # 2/2 used -> wait for operator / fleet

    def test_not_for_person_or_fleet(self):
        for reason in ('operator pressed P', 'fleet set_pause(true)', 'fleet canceled'):
            app = self._app()
            app._schedule_self_recovery(reason)
            self.assertIsNone(app.self_recovery, reason)

    def test_poll_runs_u_and_counts_a_refusal(self):
        app = self._app()
        app._schedule_self_recovery('PLAN worker exit=1')
        app.self_recovery['at'] = 0.0
        with patch.object(type(app), 'recover_work') as u_key:
            app._poll_self_recovery()
        u_key.assert_called_once()
        self.assertEqual(app.self_recovery['n'], 2)   # U changed nothing -> next try scheduled

    def test_fleet_sees_work_state_during_self_recovery(self):
        snap = {'arms': {'left': 'paused', 'right': 'idle'}, 'reasons': {'left': 'x'}, 'paused': False,
                'self_recovery': {'text': 'self-recovery 1/2', 'state': 'PICKING'}}
        self.assertEqual(arm_state_fields(snap, 'arm_left'), ('PICKING', 'self-recovery 1/2'))
        self.assertEqual(arm_state_fields(snap, 'arm_right'), ('IDLE', 'self-recovery 1/2'))


class HeartbeatTests(unittest.TestCase):
    def test_lost_after_30_s_only_once_seen(self):
        link = FleetLink()
        self.assertFalse(link.heartbeat_lost(now=100.0))   # never received
        link._heartbeat_at = 100.0
        self.assertFalse(link.heartbeat_lost(now=129.0))
        self.assertTrue(link.heartbeat_lost(now=131.0))
        self.assertEqual(link.pop_commands(), [{'type': 'heartbeat', 'lost': True}])
        self.assertTrue(link.heartbeat_lost(now=132.0))
        self.assertEqual(link.pop_commands(), [])            # tells the app only on change
        link._heartbeat_at = 132.0
        self.assertFalse(link.heartbeat_lost(now=133.0))
        self.assertEqual(link.pop_commands(), [{'type': 'heartbeat', 'lost': False}])

    def test_set_pause_true(self):
        link = FleetLink()
        self.assertEqual(link.set_pause('arm_left', 'p1', True), (True, '', ''))
        self.assertEqual(link.pop_commands(), [{'type': 'pause', 'arm': 'left'}])


class BridgeRestartTests(unittest.TestCase):
    """11-27: Pi bridge restart (new publisher GID) -> new boot_id, mode UNKNOWN, lock on."""

    def test_new_gid_restarts_boot(self):
        link = FleetLink()
        link.set_mode('arm_left', 'm1', 'NORMAL')
        link.set_lock('arm_right', 'l1', False)
        link.pop_commands()
        first = link.boot_id
        self.assertFalse(link.note_bridge_gids('left', set()))          # no publisher yet
        self.assertFalse(link.note_bridge_gids('left', {b'A'}))         # first seen
        self.assertFalse(link.note_bridge_gids('left', set()))          # Wi-Fi gap / gone
        self.assertFalse(link.note_bridge_gids('left', {b'A'}))         # same program back
        self.assertTrue(link.note_bridge_gids('left', {b'B'}))          # restarted
        self.assertNotEqual(link.boot_id, first)
        self.assertEqual(link.mode['arm_left'], 'UNKNOWN')
        self.assertTrue(link.lock['arm_right'])
        self.assertEqual(link.pop_commands(), [{'type': 'bridge_restart', 'why': 'left bridge restarted'}])
        self.assertFalse(link.note_bridge_gids('left', {b'B'}))

    def test_resume_rejected_while_unlocked(self):
        link = FleetLink()
        link.snapshot.write(dict(IDLE_SNAPSHOT, paused=True))
        link.set_lock('arm_left', 'l1', False)
        link.pop_commands()
        self.assertEqual(link.resume_arm('arm_left', 'r1'),
                         (False, 'LOCK_RELEASED', 'lock released; lock again before resume'))
        self.assertEqual(link.pop_commands(), [])


class SelfResetGateTests(unittest.TestCase):
    """11-27 user decision: self-recovery clears the gate latch itself before U."""

    def _app(self, success=True, ready=True):
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        app.recovery = {'object': 'object_1', 'stage': 'EXECUTE'}
        app.held_jobs = []
        app.self_recovery, app.self_recovery_used = None, {}
        app.operator_paused = True
        app.motion_child = app.place_child = app.bg = None
        app.fleet_gate_latched = True
        app.queue_auto_started, app.motion_stage = False, None
        reply = SimpleNamespace(success=success, message='cleared' if success else 'still moving')
        future = SimpleNamespace(done=lambda: True, result=lambda: reply)
        self.calls = []
        client = SimpleNamespace(service_is_ready=lambda: ready,
                                 call_async=lambda rq: self.calls.append(rq.data) or future)
        app._gate_reset_client = client
        app.live_poses = SimpleNamespace(node=None, poll=lambda: None)
        return app

    def tick(self, app):
        app.self_recovery['at'] = 0.0
        app._poll_self_recovery()

    def test_reset_then_u(self):
        app = self._app()
        app._schedule_self_recovery('EXECUTE worker exit=1', need_reset=True)
        with patch.object(type(app), 'recover_work',
                          side_effect=lambda: setattr(app, 'operator_paused', False)) as u_key:
            self.tick(app)          # sends the reset
            u_key.assert_not_called()
            self.tick(app)          # reset answered -> U
        self.assertEqual(self.calls, [True])
        u_key.assert_called_once()
        self.assertFalse(app.fleet_gate_latched)

    def test_failed_reset_counts_and_stops_after_two(self):
        app = self._app(success=False)
        app._schedule_self_recovery('EXECUTE worker exit=1', need_reset=True)
        with patch.object(type(app), 'recover_work') as u_key:
            for _ in range(4):
                if app.self_recovery is None:
                    break
                self.tick(app)
        u_key.assert_not_called()
        self.assertIsNone(app.self_recovery)
        self.assertEqual(app.self_recovery_used, {'pick/object_1/EXECUTE': 2})

    def test_no_gate_service_reports(self):
        app = self._app(ready=False)
        app._schedule_self_recovery('EXECUTE worker exit=1', need_reset=True)
        self.tick(app)
        self.assertIsNone(app.self_recovery)
        self.assertEqual(self.calls, [])


class SelfRecoveryOtherArmTests(unittest.TestCase):
    """11-27 user decision: plan failure -> 1st self-recovery same arm, 2nd the other arm."""

    def _app(self, stage):
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        app.recovery = {'object': 'object_1', 'stage': stage}
        app.active_object = {'id': 'object_1', 'assigned_arm': 'left'}
        app.active_arm = 'left'
        app.held_jobs = []
        app.self_recovery, app.self_recovery_used = None, {}
        app.operator_paused = True
        app.motion_child = app.place_child = app.bg = None
        return app

    def run_tries(self, app):
        arms = []
        with patch.object(type(app), 'recover_work',
                          side_effect=lambda: arms.append(app.active_object['assigned_arm'])):
            app._schedule_self_recovery(f'{app.recovery["stage"]} worker exit=1')
            for _ in range(2):
                app.self_recovery['at'] = 0.0
                app._poll_self_recovery()
        return arms

    def test_plan_failure_last_try_other_arm(self):
        self.assertEqual(self.run_tries(self._app('PLAN')), ['left', 'right'])

    def test_execute_failure_keeps_arm(self):
        self.assertEqual(self.run_tries(self._app('EXECUTE')), ['left', 'left'])


class SelfRecoveryAutoExecuteTests(unittest.TestCase):
    def test_self_recovery_keeps_auto_execute(self):
        from multi_pick.sam_multi_real import MULTI, MultiApp
        app = MultiApp.__new__(MultiApp)
        app.recovery = {'object': 'object_1', 'stage': 'PLAN'}
        app.active_object = {'id': 'object_1', 'assigned_arm': 'left'}
        app.active_arm = 'left'
        app.held_jobs = []
        app.self_recovery, app.self_recovery_used = None, {}
        app.operator_paused = True
        app.motion_child = app.place_child = app.bg = None
        app.motion_stage = None
        app.queue_auto_started = False

        def u_key():  # like recover_work(): re-plan, manual restart, no auto execute
            app.operator_paused = False
            app.manual_restart = True
            app.auto_execute_pending = False
            app.motion_stage = 'PLAN'
        app._schedule_self_recovery('PLAN worker exit=1')
        app.self_recovery['at'] = 0.0
        with patch.object(type(app), 'recover_work', side_effect=u_key), \
                patch.object(MULTI, 'auto_execute', True):
            app._poll_self_recovery()
        self.assertFalse(app.manual_restart)
        self.assertTrue(app.auto_execute_pending)


class AutoResetTests(unittest.TestCase):
    """11-27 user decision: after all objects / every fleet goal end -> R by itself."""

    def _app(self):
        from multi_pick.sam_multi_real import MultiApp
        from multi_pick.state_machine import WorkState
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.operator_paused = app.queue_started = False
        app.held_jobs = []
        app.motion_child = app.place_child = app.bg = app.fleet_goal = None
        app.auto_reset_pending = True
        return app

    def test_resets_when_idle(self):
        app = self._app()
        with patch.object(type(app), 'reset') as reset, \
                patch('multi_pick.sam_multi_real.cv2.destroyWindow'):
            app._poll_auto_reset()
        reset.assert_called_once()
        self.assertFalse(app.auto_reset_pending)

    def test_waits_while_other_arm_places(self):
        app = self._app()
        app.held_jobs = [{'arm': 'right'}]
        with patch.object(type(app), 'reset') as reset:
            app._poll_auto_reset()
        reset.assert_not_called()
        self.assertTrue(app.auto_reset_pending)

    def test_no_reset_after_a_stop(self):
        app = self._app()
        app.operator_paused = True
        with patch.object(type(app), 'reset') as reset:
            app._poll_auto_reset()
        reset.assert_not_called()
        self.assertFalse(app.auto_reset_pending)


class PostponeFailedPlanTests(unittest.TestCase):
    """11-27 user decision: plan failure -> other arm keeps working, this arm plans another object."""

    def _app(self, queue):
        import numpy as np
        from multi_pick.sam_multi_real import MultiApp
        from multi_pick.state_machine import WorkState
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.work.request_pick('right')
        app.active_arm = 'right'
        app.active_object = {'id': 'object_1', 'assigned_arm': 'right', 'center_xyz_mm': [-135, 58, 23]}
        app.queue = queue
        app.self_recovery_used = {}
        T = np.eye(4)
        T[0, 3] = 0.4
        app.live_poses = SimpleNamespace(T_right_left=T)
        return app

    def test_moves_to_other_arm_and_plans_next(self):
        from multi_pick.sam_multi_real import sam
        app = self._app([{'id': 'object_3', 'assigned_arm': 'left', 'center_xyz_mm': [-60, 120, 23]}])
        with patch.object(sam.SAMYawApp, 'reset'):
            self.assertTrue(app._postpone_failed_plan(1))
        self.assertEqual([(o['id'], o['assigned_arm']) for o in app.queue],
                         [('object_3', 'right'), ('object_1', 'left')])
        self.assertIsNone(app.work.pick_owner)
        self.assertEqual(app.work.arms['right'], 'idle')
        self.assertIsNone(app.active_object)

    def test_only_object_goes_to_other_arm(self):
        from multi_pick.sam_multi_real import sam
        app = self._app([])
        with patch.object(sam.SAMYawApp, 'reset'):
            self.assertTrue(app._postpone_failed_plan(1))
        self.assertEqual([(o['id'], o['assigned_arm']) for o in app.queue], [('object_1', 'left')])

    def test_both_arms_failed_pauses_without_self_recovery(self):
        app = self._app([])
        app.active_object['plan_failed_arms'] = ['left']
        self.assertFalse(app._postpone_failed_plan(1))
        self.assertEqual(app.self_recovery_used, {'pick/object_1/PLAN': 2})


class FineStopTests(unittest.TestCase):
    """11-27: AUTO FINE PAUSED -> saved image + a real pause after the other arm finished."""

    def _app(self):
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        app.auto_fine_paused = False
        app.operator_paused = False
        app.held_jobs = [{'arm': 'left'}]
        app.place_child = object()
        app.fine_stop_pending = None
        return app

    def test_waits_for_other_arm_then_pauses(self):
        from multi_pick.sam_multi_real import sam
        app = self._app()
        with patch.object(sam.SAMYawApp, 'pause_auto_fine'), \
                patch.object(type(app), '_save_fine_frame') as save:
            app.pause_auto_fine('selected object not stable within 20 s')
        save.assert_called_once()
        self.assertEqual(app.fine_stop_pending, 'selected object not stable within 20 s')

    def test_person_s_is_not_turned_into_a_pause(self):
        from multi_pick.sam_multi_real import sam
        app = self._app()
        with patch.object(sam.SAMYawApp, 'pause_auto_fine'), \
                patch.object(type(app), '_save_fine_frame'):
            app.pause_auto_fine('operator pressed S')
        self.assertIsNone(app.fine_stop_pending)

    def test_saves_raw_frame(self):
        import tempfile
        import numpy as np
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        with tempfile.TemporaryDirectory() as folder:
            app.preview_file = Path(folder) / 'moveit_target.json'
            app.args = SimpleNamespace(arm='right')
            frame = np.zeros((480, 640, 3), np.uint8)
            app.cameras = [None, SimpleNamespace(is_live=lambda: True, frame=frame)]
            app._save_fine_frame('x')
            self.assertEqual(len(list(Path(folder).glob('auto_fine_paused_right_*.jpg'))), 1)


class DeferPlanPauseTests(unittest.TestCase):
    """11-27: both arms failed planning while the other arm places -> pause only after it is done."""

    def test_pending_then_pause_with_plan_stage(self):
        from multi_pick.sam_multi_real import MultiApp
        app = MultiApp.__new__(MultiApp)
        app.fine_stop_pending = ('PLAN', 'PLAN worker exit=1')
        app.operator_paused = False
        app.held_jobs, app.place_child = [], None
        app.self_recovery = None
        app.auto_reset_pending = False
        app.fleet = None
        app.exec_retry = None
        app.return_settling = None
        calls = []
        with patch.object(type(app), 'pause_motion',
                          side_effect=lambda reason, **kw: calls.append((reason, kw))), \
                patch.object(type(app), '_record_stationary_plan_wait', side_effect=RuntimeError('stop')):
            try:
                app.poll_motion()
            except RuntimeError:
                pass
        self.assertEqual(calls, [('PLAN worker exit=1', {'stage': 'PLAN'})])
