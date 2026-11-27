#!/usr/bin/env python3
"""Store the current physical position as zero for selected JetCobot joints."""

import argparse
import math
import time

from pymycobot import MyCobot280


ALIGN_TOLERANCE_DEG = 0.5


def read_joint(robot, joint):
    angles = robot.get_angles()
    if isinstance(angles, (list, tuple)) and len(angles) == 6:
        angle = angles[joint - 1]
        if isinstance(angle, (int, float)) and math.isfinite(angle):
            return angle
    raise RuntimeError(f'J{joint} 각도를 읽을 수 없습니다.')


def align_joint(robot, joint, target):
    current = read_joint(robot, joint)
    print(f'J{joint} 현재 {current:+.2f}° → 목표 {target:+.2f}°', flush=True)
    while True:
        key = input(f'J{joint} 이동: m / 종료: q > ').strip().lower()
        if key == 'q':
            return False
        if key == 'm':
            break

    deadline = time.monotonic() + 10.0
    samples = 0
    try:
        robot.send_angle(joint, target, 10)
        while time.monotonic() < deadline:
            angle = read_joint(robot, joint)
            samples = samples + 1 if abs(angle - target) <= ALIGN_TOLERANCE_DEG else 0
            if samples == 3:
                print(f'J{joint} 도착 확인: {angle:+.2f}°', flush=True)
                return True
            time.sleep(0.25)
        raise RuntimeError(f'J{joint} 목표 각도 도착 시간 초과. 정지를 요청했습니다.')
    except (KeyboardInterrupt, RuntimeError):
        robot.stop()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyJETCOBOT')
    parser.add_argument('--baud', type=int, default=1000000)
    parser.add_argument('--joint', type=int, choices=range(1, 7),
                        help='저장할 관절 번호. 생략하면 6개 관절을 저장합니다.')
    parser.add_argument('--align-angle', type=float, help='저장 전에 맞출 현재 기준 각도(°)')
    args = parser.parse_args()
    if args.align_angle is not None and (args.joint is None or not math.isfinite(args.align_angle)):
        parser.error('--align-angle에는 --joint와 유한한 각도가 필요합니다.')

    joints = (args.joint,) if args.joint is not None else range(1, 7)
    scope = f'J{args.joint}만' if args.joint is not None else 'J1~J6 전체'
    robot = MyCobot280(args.port, args.baud)
    print('현재 각도 [deg]:', robot.get_angles(), flush=True)
    if args.align_angle is not None and not align_joint(robot, args.joint, args.align_angle):
        return
    while True:
        key = input(f'{scope} 현재 물리 자세를 0도로 저장: 1 / 종료: q > ').strip().lower()
        if key == 'q':
            return
        if key == '1':
            break

    if args.align_angle is not None:
        angle = read_joint(robot, args.joint)
        if abs(angle - args.align_angle) > ALIGN_TOLERANCE_DEG:
            raise RuntimeError(f'J{args.joint} 각도가 목표에서 벗어났습니다. 현재 {angle:+.2f}°')
    print('영점 저장 전 각도 [deg]:', robot.get_angles(), flush=True)
    for joint in joints:
        reply = robot.set_servo_calibration(joint)
        print(f'{joint}번 관절 보정 응답: {reply}', flush=True)
        time.sleep(1)
    print('영점 저장 후 각도 [deg]:', robot.get_angles(), flush=True)
    print('보정 위치를 유지한 채 물리적 표시선과 출력 각도를 확인하세요.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n종료', flush=True)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
