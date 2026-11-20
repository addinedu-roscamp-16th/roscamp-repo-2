#!/usr/bin/env python3
"""Recover a recorded SDK mode after abnormal exit. No joint commands."""
import json
import os
from pathlib import Path

from mode_lease import ModeLease


def main():
    record=Path(__file__).resolve().parent/'sdk_mode_restore.json'
    if not record.exists():
        print('No pending mode recovery record; no SDK connection or change performed.')
        return
    try:
        data=json.loads(record.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f'Invalid recovery record {record} ({record.stat().st_size} bytes). '
            'Original SDK mode is unknown; do not guess or overwrite the record. '
            'Check for a running bridge, preserve this file, and inspect the '
            'current SDK mode before starting another mode test.') from exc
    original=data.get('original_mode')
    pid=data.get('pid')
    if original not in (0,1) or not isinstance(pid,int) or pid<=0:
        raise RuntimeError('Invalid recovery record; inspect it before proceeding')
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError(f'Original process PID {pid} still exists; stop it normally first')
    from pymycobot.mycobot280 import MyCobot280
    sdk=MyCobot280('/dev/ttyJETCOBOT',1000000)
    lease=ModeLease(sdk,record)
    lease.original=original
    lease.restore()
    print(f'Original SDK mode {original} restored and verified. No joint/gripper commands sent.')


if __name__=='__main__':main()
