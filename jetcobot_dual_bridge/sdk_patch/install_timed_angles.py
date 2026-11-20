#!/usr/bin/env python3
"""Install the reviewed timed-stream method into pymycobot 4.0.7 only."""
import hashlib
import importlib.metadata
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from pymycobot.mycobot280 import MyCobot280

EXPECTED_SHA256 = '2e745de4eed76652f7dd9f703eb6d5b7aae9c838b63b0f97cfc32b8bf9452729'


def main():
    if importlib.metadata.version('pymycobot') != '4.0.7':
        raise RuntimeError('Installer supports pymycobot 4.0.7 only')
    target = Path(inspect.getfile(MyCobot280)).resolve()
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f'SDK source hash differs: {digest}; no files changed')
    backup = target.with_name(target.name + '.before_timed_stream')
    if backup.exists():
        raise RuntimeError(f'Backup already exists: {backup}; no files changed')
    patch = Path(__file__).with_name('mycobot280_4_0_7_timed_angles.patch')
    if not patch.is_file():
        raise RuntimeError(f'Patch missing: {patch}')
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.timed_sdk_') as temporary:
        staged = Path(temporary) / target.name
        shutil.copy2(target, staged)
        subprocess.run(['patch', '-p1', '-d', temporary, '-i', str(patch)],
                       check=True, capture_output=True, text=True)
        compile(staged.read_bytes(), str(staged), 'exec')
        patched = staged.read_bytes()
        if b'def send_timed_angles(' not in patched:
            raise RuntimeError('Patched method not found; no files changed')
        # Keep the exact original as a single, non-overwritten recovery file.
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(target.read_bytes())
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        shutil.copymode(target, staged)
        os.replace(staged, target)
    print(f'Installed MyCobot280.send_timed_angles in {target}')
    print(f'Original SDK backup: {backup}')
    print('Restart the Python bridge to load the added method; no motion was sent.')


if __name__ == '__main__':
    main()
