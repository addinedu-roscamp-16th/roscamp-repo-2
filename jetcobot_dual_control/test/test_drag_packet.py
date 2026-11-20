"""Verify the exact SDK packet from the failed right-arm probe, without serial I/O."""
from pymycobot.common import ProtocolCode
from pymycobot.mycobot280 import MyCobot280


def test_drag_probe_packet_has_expected_joint_and_speed_fields():
    robot = MyCobot280.__new__(MyCobot280)
    robot.thread_lock = False
    frames = []
    robot._write = lambda frame: frames.append(list(frame))
    robot._mesg(ProtocolCode.SET_ENCODERS_DRAG,
                [2040, 2059, 2038, 2068, 2040, 2054],
                [25, 0, 0, 0, 0, 0], _async=True)
    assert len(frames) == 1
    frame = frames[0]
    assert frame[:4] == [0xFE, 0xFE, 0x1A, 0x3E]
    assert frame[-1] == 0xFA
    fields = [int.from_bytes(bytes(frame[index:index+2]), 'big', signed=True)
              for index in range(4, len(frame)-1, 2)]
    assert fields == [2040, 2059, 2038, 2068, 2040, 2054,
                      25, 0, 0, 0, 0, 0]
