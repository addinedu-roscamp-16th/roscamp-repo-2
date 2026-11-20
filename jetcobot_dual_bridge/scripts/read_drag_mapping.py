#!/usr/bin/env python3
"""Read stationary angles, encoders and servo speeds for a drag-mode trial.

Use only after the arm bridge has stopped and released this serial port. This
script never sends a motion or servo-release command.
"""
import json
import math
import time

from pymycobot.mycobot280 import MyCobot280


def six_numbers(value, name, minimum=None, maximum=None):
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise RuntimeError(f'{name}: expected six values, got {value!r}')
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise RuntimeError(f'{name}: non-finite reading {value!r}')
    if minimum is not None and any(item < minimum for item in result):
        raise RuntimeError(f'{name}: value below {minimum}: {value!r}')
    if maximum is not None and any(item > maximum for item in result):
        raise RuntimeError(f'{name}: value above {maximum}: {value!r}')
    return result


def main():
    mc = MyCobot280('/dev/ttyJETCOBOT', 1000000)
    mc.thread_lock = True
    samples = []
    try:
        for _ in range(3):
            stamp = time.time()
            angles = six_numbers(mc.get_angles(), 'angles')
            encoders = six_numbers(mc.get_encoders(), 'encoders', 0, 4096)
            speeds = six_numbers(mc.get_servo_speeds(), 'servo_speeds', 0, 6000)
            samples.append({'time': stamp, 'angles_deg': angles,
                            'encoders': encoders, 'servo_speeds': speeds})
            time.sleep(0.3)
    finally:
        mc.close()
    for joint in range(6):
        if (max(row['angles_deg'][joint] for row in samples) -
                min(row['angles_deg'][joint] for row in samples) > 0.5):
            raise RuntimeError('Arm moved during mapping read; repeat only when stationary')
        if (max(row['encoders'][joint] for row in samples) -
                min(row['encoders'][joint] for row in samples) > 8):
            raise RuntimeError('Encoder changed during mapping read; repeat only when stationary')
    print(json.dumps({'device': '/dev/ttyJETCOBOT', 'samples': samples}, indent=2))
    print('READ ONLY: no movement or servo-release command sent')


if __name__ == '__main__':
    main()
