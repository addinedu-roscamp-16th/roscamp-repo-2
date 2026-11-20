#!/usr/bin/env python3
"""Test whether successive drag targets pass through intermediate J1 points.

One right arm, one joint, +5/+10/+15 degrees from the measured start.
Dry run by default; --execute sends three packets at 0.0, 0.8 and 1.6 s.
"""
import argparse
import json
import math
import threading
import time

from pymycobot.common import ProtocolCode
from pymycobot.mycobot280 import MyCobot280

from drag_trajectory import angles_to_encoders
from trial_drag_joint import read_position


OFFSETS_DEG = (5.0, 10.0, 15.0)
SEND_TIMES_S = (0.0, 0.8, 1.6)
SPEED_VALUES = (60, 0, 0, 0, 0, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--direction', type=int, choices=(-1, 1), default=1,
                        help='J1 direction relative to measured start (default +1)')
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
            spread = max(row[0][joint] for row in baseline) - min(row[0][joint] for row in baseline)
            if spread > 0.5:
                raise RuntimeError('Arm is not stationary')
        before, before_encoders = baseline[-1]
        targets = []
        for offset in OFFSETS_DEG:
            angles = (before[0] + args.direction*offset,) + before[1:]
            encoders = angles_to_encoders(tuple(math.radians(q) for q in angles))
            if tuple(encoders[1:]) != tuple(before_encoders[1:]):
                raise RuntimeError('Unexpected non-J1 encoder target change')
            targets.append({'offset_deg': offset, 'j1_target_deg': angles[0],
                            'encoders': encoders})
        print(json.dumps({'mode': mode, 'before_deg': before,
                          'before_encoders': before_encoders,
                          'send_times_s': SEND_TIMES_S,
                          'direction': args.direction,
                          'servo_speed_values': SPEED_VALUES,
                          'targets': targets}, indent=2), flush=True)
        if not args.execute:
            print('DRY RUN: no motion command sent', flush=True)
            return

        started = time.monotonic()
        cancel_sends = threading.Event()
        send_errors = []

        def send_targets():
            try:
                for index, (send_at, target) in enumerate(zip(SEND_TIMES_S, targets)):
                    if cancel_sends.wait(max(0.0, started + send_at - time.monotonic())):
                        return
                    mc._mesg(ProtocolCode.SET_ENCODERS_DRAG,
                             list(target['encoders']), list(SPEED_VALUES), _async=True)
                    print(json.dumps({'event': 'send', 'index': index+1,
                                      'elapsed_s': round(time.monotonic()-started, 2),
                                      'j1_target_deg': target['j1_target_deg']}), flush=True)
            except Exception as exc:
                send_errors.append(exc)
                cancel_sends.set()

        sender = threading.Thread(target=send_targets, daemon=True)
        sender.start()
        last_angles = before
        max_other_change = 0.0
        try:
            while time.monotonic() - started < 6.0:
                if send_errors:
                    raise RuntimeError(f'Drag send failed: {send_errors[0]!r}')
                angles, encoders = read_position(mc, verify_mapping=False)
                elapsed = time.monotonic() - started
                max_other_change = max(max_other_change, *(abs(angles[j]-before[j])
                                                             for j in range(1, 6)))
                print(json.dumps({'event': 'sample', 'elapsed_s': round(elapsed, 2),
                                  'j1_deg': angles[0], 'j1_encoder': encoders[0],
                                  'other_deg': angles[1:]}), flush=True)
                last_angles = angles
                if max_other_change > 3.0:
                    cancel_sends.set()
                    reply = mc.stop()
                    raise RuntimeError(f'Non-J1 change exceeded 3 deg; stop reply={reply!r}; inspect robot')
                time.sleep(0.1)
            print(json.dumps({'event': 'result', 'j1_change_deg': round(last_angles[0]-before[0], 2),
                              'j1_final_error_deg': round(targets[-1]['j1_target_deg']-last_angles[0], 2),
                              'max_other_joint_change_deg': round(max_other_change, 2)}), flush=True)
        finally:
            cancel_sends.set()
            sender.join(timeout=2.0)
    finally:
        mc.close()


if __name__ == '__main__':
    main()
