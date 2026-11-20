import unittest

from multi_pick.state_machine import WorkState, alternating_assignments


class WorkStateTests(unittest.TestCase):
    def test_alternating_and_resource_handoff(self):
        self.assertEqual(alternating_assignments(4, 'left'),
                         ['left', 'right', 'left', 'right'])
        work = WorkState()
        self.assertTrue(work.request_pick('left'))
        self.assertFalse(work.request_pick('right'))
        self.assertEqual(work.arms['right'], 'pick_waiting')
        self.assertFalse(work.release_pick_at_height('left', .299, .300))
        self.assertTrue(work.release_pick_at_height('left', .300, .300))
        self.assertTrue(work.request_pick('right'))
        self.assertTrue(work.request_place('left'))
        self.assertFalse(work.request_place('right'))
        self.assertEqual(work.arms['right'], 'place_waiting')
        work.returned_from_place('left')
        self.assertTrue(work.request_place('right'))
        work.returned_from_place('right')
        self.assertEqual(work.arms, {'left': 'idle', 'right': 'idle'})

    def test_error_retains_resources(self):
        work = WorkState()
        work.request_pick('left')
        work.request_place('left')
        work.error('gate fault')
        self.assertEqual((work.pick_owner, work.basket_owner), ('left', 'left'))
        with self.assertRaises(RuntimeError):
            work.request_pick('right')


if __name__ == '__main__':
    unittest.main()
