#!/usr/bin/env python3
"""Open the dual-arm ROI UI with selectable chocolate/Mentos fine alignment."""
import sys
from sam_dual_yaw_moveit import main

if __name__ == '__main__':
    if '--fine-roi' not in sys.argv:
        sys.argv.insert(1, '--fine-roi')
    if '--match-rotation' not in sys.argv and '--no-match-rotation' not in sys.argv:
        sys.argv.insert(1, '--match-rotation')
    if not any(flag in sys.argv for flag in ('--auto-fine', '--no-auto-fine',
                                            '--auto-left-fine', '--no-auto-left-fine')):
        sys.argv.insert(1, '--auto-fine')
    if '--enable-execute' not in sys.argv and '--plan-only' not in sys.argv:
        sys.argv.insert(1, '--enable-execute')
    main()
