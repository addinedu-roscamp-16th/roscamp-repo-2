#!/usr/bin/env python3
"""Patch only the Pi XY completion and stop service; no process restart."""
from pathlib import Path
from shutil import copy2
from datetime import datetime
import os

NEW_COMPLETION = "            started_at = time.monotonic()\n            deadline = started_at + 45.0\n            stopped_since = None\n            while rclpy.ok():\n                if self.native_xy_cancel.is_set():\n                    raise RuntimeError('Native XY stopped by operator')\n                moving = self.mc.is_moving()\n                now = time.monotonic()\n                if moving == 0:\n                    if stopped_since is None:\n                        stopped_since = now\n                    # Ignore an immediate idle reply while the command starts.\n                    if now - stopped_since >= 0.75 and now - started_at >= 1.0:\n                        break\n                elif moving == 1:\n                    stopped_since = None\n                else:\n                    raise RuntimeError(f'Invalid SDK moving status: {moving!r}')\n                if now > deadline:\n                    raise RuntimeError('SDK MoveL XY still moving after 45 s')\n                time.sleep(0.25)\n            else:\n                raise RuntimeError('ROS shutdown during SDK MoveL XY prediction')\n            actual = self.mc.get_coords()\n            if not isinstance(actual, (list, tuple)) or len(actual) != 6 or not all(\n                    isinstance(v, (int, float)) and math.isfinite(v) for v in actual):\n                raise RuntimeError(f'Invalid SDK Cartesian feedback: {actual!r}')\n            position_error_mm = math.dist(actual[:3], target[:3])\n            if position_error_mm > 10.0:\n                self.get_logger().warning(\n                    f'SDK MoveL XY position differs from target by {position_error_mm:.1f} mm; '\n                    f'target={target[:3]}, actual={actual[:3]}')\n            response.sum = 1\n            self.get_logger().info(\n                f'SDK MoveL XY stopped; actual delta='\n                f'{[round(actual[i]-start[i],2) for i in range(3)]}; '\n                f'position error={position_error_mm:.1f} mm')\n            return response\n"

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f'Expected one occurrence of {old!r}; file was not changed')
    return text.replace(old, new, 1)

path = Path.home() / 'jetcobot_dual_bridge/sdk_goal_bridge.py'
original = path.read_text()
start = original.index('    def sdk_linear_xy_step(')
end = original.index('    def stop_slow_xy(', start)
method = original[start:end]
if 'SDK MoveL XY stopped; actual delta=' in method:
    print('XY completion logic is already updated:', path)
    raise SystemExit(0)
old_start = '            deadline = time.monotonic() + 45.0'
old_end = '        except Exception as exc:'
if method.count(old_start) != 1 or method.count(old_end) != 1:
    raise SystemExit('Expected old XY completion block not found; file was not changed')
head, tail = method.split(old_start, 1)
tail = tail.split(old_end, 1)[1]
method = head + NEW_COMPLETION + old_end + tail
method = replace_once(method,
    "self.get_logger().warning(f'SDK MOVEL XY prediction:",
    "self.get_logger().info(f'SDK MOVEL XY prediction:")
method = replace_once(method, '            self.busy = True\n',
    '            self.busy = True\n'
    '            self.native_xy_active = True\n'
    '            self.native_xy_cancel.clear()\n')
method = replace_once(method, '                self.busy = False\n',
    '                self.busy = False\n'
    '                self.native_xy_active = False\n'
    '                self.native_xy_cancel.clear()\n')
updated = original[:start] + method + original[end:]
init_end = updated.index('    def sdk_linear_xy_step(')
init = replace_once(updated[:init_end], '        self.slow_xy_active = False\n',
    '        self.slow_xy_active = False\n'
    '        self.native_xy_cancel = threading.Event()\n'
    '        self.native_xy_active = False\n')
updated = init + updated[init_end:]
stop_start = updated.index('    def stop_slow_xy(')
stop_end = updated.index('    def sdk_linear_xy_slow_step(', stop_start)
stop = updated[stop_start:stop_end]
stop = replace_once(stop,
    '            active = bool(self.slow_xy_active or self.slow_joint_active or self.descend_active)\n',
    '            active = bool(self.native_xy_active or self.slow_xy_active or\n'
    '                          self.slow_joint_active or self.descend_active)\n')
stop = replace_once(stop, '            if active:\n                self.slow_xy_cancel.set()\n',
    '            if active:\n                self.native_xy_cancel.set()\n'
    '                self.slow_xy_cancel.set()\n')
updated = updated[:stop_start] + stop + updated[stop_end:]
compile(updated, str(path), 'exec')
backup = path.with_name(path.name + '.bak_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
copy2(path, backup)
temporary = path.with_name(path.name + '.xy_completion_tmp')
temporary.write_text(updated)
try:
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)
finally:
    temporary.unlink(missing_ok=True)
print('Updated:', path)
print('Backup:', backup)
print('Restart the Pi bridge yourself after checking the file.')
