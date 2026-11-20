"""Offline planning-scene checks for two simultaneously held objects."""
import unittest

from moveit_pick import held_object


class HeldObjectTests(unittest.TestCase):
    def test_attaching_second_arm_preserves_first_arm(self):
        left = held_object.held_id('left')
        right = held_object.held_id('right')
        scene = held_object.scene_request(
            {held_object.OBJECT_ID}, {left: 'left_TCP'},
            object_type='chocobar', arm='right').scene
        self.assertEqual(scene.robot_state.attached_collision_objects[-1].object.id, right)
        self.assertNotIn(left, [item.object.id for item in
                                scene.robot_state.attached_collision_objects
                                if item.object.operation == item.object.REMOVE])
        self.assertEqual([item.id for item in scene.world.collision_objects],
                         [held_object.OBJECT_ID])

    def test_releasing_one_arm_keeps_peer_attached(self):
        left = held_object.held_id('left')
        right = held_object.held_id('right')
        scene = held_object.scene_request(
            set(), {left: 'left_TCP', right: 'right_TCP'},
            arm='left', remove=True).scene
        self.assertEqual([item.object.id for item in
                          scene.robot_state.attached_collision_objects], [left])

    def test_legacy_leftover_can_be_removed_without_peer(self):
        scene = held_object.scene_request(
            {held_object.OBJECT_ID},
            {held_object.held_id('right'): 'right_TCP'},
            arm='left', remove=True, remove_legacy_world=True).scene
        self.assertEqual([item.id for item in scene.world.collision_objects],
                         [held_object.OBJECT_ID])
        self.assertEqual(scene.robot_state.attached_collision_objects, [])


if __name__ == '__main__':
    unittest.main()
