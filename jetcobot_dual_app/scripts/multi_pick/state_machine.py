"""Pure arm/resource state machine for the isolated state_pick experiment.

This module never sends robot commands. The caller supplies measured TCP Z and
reports successful worker completion; neither a planned nor requested motion
is treated as completed motion.
"""
from dataclasses import dataclass, field

SIDES = ('left', 'right')
ARM_STATES = frozenset(('idle', 'pick_waiting', 'picking', 'place_waiting',
                        'placing', 'paused', 'error'))


@dataclass
class WorkState:
    arms: dict = field(default_factory=lambda: {side: 'idle' for side in SIDES})
    pick_owner: str | None = None
    basket_owner: str | None = None
    stopped: bool = False
    reasons: dict = field(default_factory=dict)

    def _check_side(self, side):
        if side not in SIDES:
            raise ValueError(f'Unknown arm: {side}')
        if self.stopped or self.arms[side] in ('paused', 'error'):
            raise RuntimeError(f'{side} cannot start while {self.arms[side]} or service stopped')

    def request_pick(self, side):
        self._check_side(side)
        if self.arms[side] not in ('idle', 'pick_waiting'):
            raise RuntimeError(f'{side} cannot pick from {self.arms[side]}')
        if self.pick_owner not in (None, side):
            self.arms[side] = 'pick_waiting'
            return False
        self.pick_owner = side
        self.arms[side] = 'picking'
        return True

    def release_pick_at_height(self, side, measured_z_m, see_center_z_m, tolerance_m=0.015):
        # Real returns land a few mm off see_center (about 2 deg joint error,
        # 2026-11-19); an exact >= made a correct return pause the arm.
        self._check_side(side)
        if self.pick_owner != side:
            return False
        if measured_z_m >= see_center_z_m - tolerance_m:
            self.pick_owner = None
            return True
        return False

    def request_place(self, side):
        self._check_side(side)
        if self.arms[side] not in ('picking', 'place_waiting'):
            raise RuntimeError(f'{side} cannot place from {self.arms[side]}')
        if self.basket_owner not in (None, side):
            self.arms[side] = 'place_waiting'
            return False
        self.basket_owner = side
        self.arms[side] = 'placing'
        return True

    def returned_from_place(self, side):
        self._check_side(side)
        if self.arms[side] != 'placing' or self.basket_owner != side:
            raise RuntimeError(f'{side} has no completed basket return to release')
        self.basket_owner = None
        self.arms[side] = 'idle'

    def pause(self, side, reason, release_pick=False):
        self._check_side(side)
        self.arms[side] = 'paused'
        self.reasons[side] = str(reason)
        # Keep resources occupied until a physical recovery confirms they are
        # clear, except after a plan-only failure: that arm never moved.
        if release_pick and self.pick_owner == side:
            self.pick_owner = None

    def error(self, reason):
        self.stopped = True
        self.reasons['service'] = str(reason)
        for side in SIDES:
            self.arms[side] = 'error'
        # Resource ownership is intentionally retained on an error.


def alternating_assignments(count, first_arm='left'):
    if first_arm not in SIDES or count < 0:
        raise ValueError('Invalid first arm or object count')
    other = 'right' if first_arm == 'left' else 'left'
    return [first_arm if index % 2 == 0 else other for index in range(count)]
