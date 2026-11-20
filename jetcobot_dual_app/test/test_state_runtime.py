"""Offline scheduler handoff checks; no ROS node or robot worker is created."""
import io
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from multi_pick.sam_multi_real import MultiApp
from multi_pick import sam_multi_real
from multi_pick.state_machine import WorkState


class Finished:
    returncode = 0


class RuntimeHandoffTests(unittest.TestCase):
    def test_initial_see_center_height_does_not_release_pick_area(self):
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.work.request_pick('left')
        app.held_jobs = []
        app.see_center_z = {'left': .30}
        pose = np.eye(4)
        pose[2, 3] = .40
        app.tcp_mount = {'left': np.eye(4)}
        app.live_poses = SimpleNamespace(
            samples={'left': (time.monotonic(), None, {})},
            wrist_transform=lambda side, joints: pose)
        app._watch_pick_height()
        self.assertEqual(app.work.pick_owner, 'left')
        app.held_jobs = [{'arm': 'left', 'folder': Path('/tmp/left')}]
        app._watch_pick_height()
        self.assertIsNone(app.work.pick_owner)

    def test_pick_handoff_only_after_height_release(self):
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.work.request_pick('left')
        app.held_jobs = [{'arm': 'left', 'folder': Path('/tmp/left')}]
        app.queue = [{'assigned_arm': 'right'}]
        app.active_arm = 'left'
        app.queue_started = True
        app._watch_pick_height = lambda: None
        app._await_pick_release = lambda side: app.work.pick_owner != side
        app.motion_log_reader = io.StringIO()
        app.motion_log_writer = io.StringIO()
        app.motion_child = Finished()
        app.motion_stage = 'HOLD_RETURN'
        app._finish_custom(Finished(), 'HOLD_RETURN')
        self.assertEqual(app.work.arms['left'], 'paused')
        self.assertEqual(app.work.pick_owner, 'left')

        app.work = WorkState()
        app.work.request_pick('left')
        app.work.release_pick_at_height('left', .30, .30)
        app.motion_log_reader = io.StringIO()
        app.motion_log_writer = io.StringIO()
        app.motion_child = Finished()
        app.motion_stage = 'HOLD_RETURN'
        started = []
        app.start_next_object = lambda: started.append('right')
        with patch.object(sam_multi_real.sam.SAMYawApp, 'reset'):
            app._finish_custom(Finished(), 'HOLD_RETURN')
        self.assertEqual(started, ['right'])
        self.assertIsNone(app.active_arm)

    def test_two_held_objects_preplan_then_place_in_order(self):
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.work.request_pick('left')
        app.work.release_pick_at_height('left', .30, .30)
        app.work.request_pick('right')
        app.work.release_pick_at_height('right', .30, .30)
        app.held_jobs = [{'arm': 'left', 'folder': Path('/tmp/left')},
                         {'arm': 'right', 'folder': Path('/tmp/right')}]
        app.queue = []
        app.queue_total = 2
        app.queue_started = True
        app.active_arm = 'right'
        app.preplanned_case = None
        app._watch_pick_height = lambda: None
        app._await_pick_release = lambda side: app.work.pick_owner != side
        events = []
        app._start_preplan = lambda: events.append('preplan right')

        def start_place(job):
            if app.work.basket_owner != job['arm']:
                self.assertTrue(app.work.request_place(job['arm']))
            events.append('place ' + job['arm'])

        app._start_place = start_place

        def finish(stage):
            app.motion_log_reader = io.StringIO()
            app.motion_log_writer = io.StringIO()
            app.motion_child = Finished()
            app.motion_stage = stage
            app._finish_custom(Finished(), stage)

        finish('HOLD_RETURN')
        self.assertEqual(events, ['preplan right'])
        self.assertEqual(app.work.arms['right'], 'place_waiting')
        finish('PREPLAN_PLACE')
        self.assertEqual(events[-1], 'place left')
        finish('PLACE')
        self.assertEqual(events[-1], 'place right')
        finish('PLACE')
        self.assertEqual(app.work.arms, {'left': 'idle', 'right': 'idle'})
        self.assertEqual(app.status, 'ALL 2 OBJECTS DONE - R NEW CAPTURE')


if __name__ == '__main__':
    unittest.main()
