#!/usr/bin/env python3
"""One deliberate right-arm J1 drag-command trial, separate from MoveIt.

Run with exclusive access to /dev/ttyJETCOBOT. A dry run is the default.
"""
import argparse
import json
import math
import time

from pymycobot.common import ProtocolCode
from pymycobot.mycobot280 import MyCobot280

from drag_trajectory import angles_to_encoders, check_stationary_mapping


def read_position(mc, verify_mapping=True):
    angles = mc.get_angles()
    encoders = mc.get_encoders()
    if not isinstance(angles, (list, tuple)) or len(angles) != 6:
        raise RuntimeError(f'Invalid angle reading: {angles!r}')
    if not isinstance(encoders, (list, tuple)) or len(encoders) != 6:
        raise RuntimeError(f'Invalid encoder reading: {encoders!r}')
    angles = tuple(float(value) for value in angles)
    encoders = tuple(int(value) for value in encoders)
    # These are separate serial reads. During motion they describe different
    # instants, so angle/encoder agreement is meaningful only while stationary.
    if verify_mapping:
        check_stationary_mapping(angles, encoders)
    return angles, encoders


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='send exactly one drag packet')
    args = parser.parse_args()

    mc = MyCobot280('/dev/ttyJETCOBOT', 1000000)
    mc.thread_lock = True
    try:
        mode = mc.get_fresh_mode()
        if mode != 0:
            raise RuntimeError(f'Expected SDK mode 0, got {mode!r}')
        baseline = []
        for _ in range(3):
            baseline.append(read_position(mc))
            time.sleep(0.25)
        for joint in range(6):
            if max(row[0][joint] for row in baseline) - min(row[0][joint] for row in baseline) > 0.5:
                raise RuntimeError('Arm is not stationary')
        before, before_encoders = baseline[-1]
        target_deg = (before[0] + 5.0,) + before[1:]
        target_encoders = angles_to_encoders(tuple(math.radians(q) for q in target_deg))
        speeds = (60, 0, 0, 0, 0, 0)
        if tuple(before_encoders[1:]) != tuple(target_encoders[1:]):
            raise RuntimeError('Unexpected non-J1 encoder target change')
        print(json.dumps({'mode': mode, 'before_deg': before,
                          'before_encoders': before_encoders,
                          'target_deg': target_deg,
                          'target_encoders': target_encoders,
                          'servo_speed_values': speeds}, indent=2), flush=True)
        if not args.execute:
            print('DRY RUN: no motion command sent', flush=True)
            return

        # The public SDK method may retry when this firmware gives no reply.
        # _async=True writes this same SDK protocol packet once, without retry.
        mc._mesg(ProtocolCode.SET_ENCODERS_DRAG, list(target_encoders),
                 list(speeds), _async=True)
        print('SET_ENCODERS_DRAG packet written once', flush=True)
        started = time.monotonic()
        observations = []
        while time.monotonic() - started < 5.0:
            angles, encoders = read_position(mc)
            elapsed = round(time.monotonic() - started, 2)
            observations.append({'elapsed_s': elapsed, 'angles_deg': angles,
                                 'encoders': encoders})
            print(json.dumps(observations[-1]), flush=True)
            if max(abs(angles[j]-before[j]) for j in range(1, 6)) > 3.0:
                reply = mc.stop()
                raise RuntimeError(f'Non-J1 deviation exceeded 3 deg; stop reply={reply!r}; inspect robot')
            time.sleep(0.2)
        last = observations[-1]['angles_deg']
        print(json.dumps({'j1_change_deg': round(last[0]-before[0], 2),
                          'j1_target_error_deg': round(target_deg[0]-last[0], 2),
                          'max_other_joint_change_deg': round(max(
                              abs(row['angles_deg'][j]-before[j])
                              for row in observations for j in range(1, 6)), 2)}), flush=True)
    finally:
        mc.close()


if __name__ == '__main__':
    main()
