#!/usr/bin/env python3
"""Store the current physical position as zero for all six JetCobot joints."""

import argparse
import time

from pymycobot import MyCobot280


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyJETCOBOT')
    parser.add_argument('--baud', type=int, default=1000000)
    args = parser.parse_args()

    robot = MyCobot280(args.port, args.baud)
    print('현재 각도 [deg]:', robot.get_angles(), flush=True)
    while True:
        key = input('현재 물리 자세를 6개 관절의 0도로 저장: 1 / 종료: q > ').strip().lower()
        if key == 'q':
            return
        if key == '1':
            break

    print('영점 저장 전 각도 [deg]:', robot.get_angles(), flush=True)
    for joint in range(1, 7):
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
