"""Offline scheduler handoff checks; no ROS node or robot worker is created."""
import io
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from multi_pick import sam_multi_real
from multi_pick.sam_multi_real import MultiApp
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

    def test_pick_handoff_starts_stage2_entry(self):
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        app.held_jobs = []
        app.active_arm = 'left'
        app.preview_file = Path('/tmp/test_pick/target.json')
        events = []
        app._watch_pick_height = lambda: events.append('watch')
        app._start_place = lambda job: events.append(('transport', job['arm']))

        app.start_place_after_lift()

        self.assertEqual(events, ['watch', ('transport', 'left')])

    def _finish(self, app, stage):
        app.motion_log_reader = io.StringIO()
        app.motion_log_writer = io.StringIO()
        app._finish_custom(Finished(), stage)

    def test_concurrent_handoff_frees_pick_slot_and_places_in_own_slot(self):
        for placing in ('left', 'right'):
            with self.subTest(placing=placing):
                app = MultiApp.__new__(MultiApp)
                app.work = WorkState()
                app.work.request_pick(placing)
                app.work.release_pick_at_height(placing, .30, .30)
                app.held_jobs = []
                app.queue = [{'id': 'b'}]
                app.queue_total = 2
                app.active_arm = placing
                app.active_object = {'id': 'a'}
                app.preview_file = Path('/tmp/test_pick/target.json')
                app._watch_pick_height = lambda: None
                app.motion_child = None
                launched = []
                app._launch_place_worker = lambda stage, script, args, folder: launched.append(stage)

                with patch.object(sam_multi_real.MULTI, 'concurrent_pick_place', True), \
                        patch.object(sam_multi_real.sam.SAMYawApp, 'reset'):
                    app.start_place_after_lift()

                self.assertEqual(launched, ['STAGE2_ENTER'])
                self.assertIsNone(app.active_arm)          # pick slot free for the other arm
                self.assertEqual(app.work.basket_owner, placing)

                # Placement slot finishes stage by stage, without touching the pick slot.
                events = []
                app._continue_place = lambda job, stage: events.append(stage)
                app.operator_paused = False
                app.active_arm = 'other'
                for stage in ('STAGE2_ENTER', 'STAGE3_PLACE', 'STAGE2_RETURN'):
                    app.place_child = Finished()
                    app.place_stage = stage
                    app.place_log_reader = io.StringIO()
                    app.place_log_writer = io.StringIO()
                    app._finish_custom(app.place_child, stage)
                self.assertEqual(events, ['STAGE3_PLACE', 'STAGE2_RETURN'])
                self.assertEqual(app.held_jobs, [])
                self.assertIsNone(app.work.basket_owner)
                self.assertEqual(app.active_arm, 'other')
                self.assertIsNone(app.place_child)

    def test_sequential_mode_places_alone(self):
        app = MultiApp.__new__(MultiApp)
        app.work = WorkState()
        job = {'arm': 'left', 'folder': Path('/tmp/place')}
        app.held_jobs = [job]
        app.queue = []
        app.bg = None
        app.place_child = None
        app.operator_paused = False
        events = []
        app._continue_place = lambda job, stage: events.append(stage)
        with patch.object(sam_multi_real.MULTI, 'concurrent_pick_place', False):
            self._finish(app, 'STAGE2_ENTER')
            self._finish(app, 'STAGE3_PLACE')
        self.assertEqual(events, ['STAGE3_PLACE', 'STAGE2_RETURN'])


if __name__ == '__main__':
    unittest.main()
