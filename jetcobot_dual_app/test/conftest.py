"""Make `scripts/` importable (multi_pick, moveit_pick, ...) for pytest."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
for path in (SCRIPTS, SCRIPTS / 'moveit_pick'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
