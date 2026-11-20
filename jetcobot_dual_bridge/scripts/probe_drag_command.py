#!/usr/bin/env python3
"""Read-only reconstruction of the failed drag packet target."""
import json
import math

from pymycobot.mycobot280 import MyCobot280

from drag_trajectory import angles_to_encoders, check_stationary_mapping


def measured(mc):
    angles = mc.get_angles()
    encoders = mc.get_encoders()
    if (not isinstance(angles, (list, tuple)) or len(angles) != 6 or
            not isinstance(encoders, (list, tuple)) or len(encoders) != 6):
        raise RuntimeError(f'Invalid measured angles/encoders: {angles!r}, {encoders!r}')
    angles = tuple(float(value) for value in angles)
    encoders = tuple(int(value) for value in encoders)
    check_stationary_mapping(angles, encoders)
    return angles, encoders


def main():
    mc = MyCobot280('/dev/ttyJETCOBOT', 1000000)
    mc.thread_lock = True
    try:
        mode = mc.get_fresh_mode()
        if mode != 0:
            raise RuntimeError(f'Expected restored SDK mode 0; read {mode!r}')
        before, encoders = measured(mc)
        target_angles = (before[0]+1.0,) + before[1:]
        target_encoders = angles_to_encoders(tuple(math.radians(q) for q in target_angles))
        speeds = (25, 0, 0, 0, 0, 0)  # servo encoder steps/s; one 1-degree J1 move
        print(json.dumps({'mode': mode, 'before_deg': before,
                          'before_encoders': encoders,
                          'target_deg': target_angles,
                          'target_encoders': target_encoders,
                          'servo_speeds_steps_s': speeds}, indent=2))
        print('READ ONLY: drag motion probe disabled after unexpected right-arm motion')
    finally:
        mc.close()


if __name__ == '__main__':
    main()
