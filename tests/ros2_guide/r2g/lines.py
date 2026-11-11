"""Tolerant reading of node stdout: the result is the last line that is a JSON object."""
import json

TAIL_LINES = 20
TAIL_WIDTH = 300


def last_json_object(stdout: str) -> dict | None:
    """Last line of stdout that parses as a JSON object; other lines (logs, tracebacks
    printed during shutdown) are skipped. None when there is none."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def stdout_tail(stdout: str, lines: int = TAIL_LINES, width: int = TAIL_WIDTH) -> list[str]:
    """Last `lines` non-empty stdout lines, each cut to `width` characters."""
    kept = [ln.rstrip() for ln in stdout.splitlines() if ln.strip()]
    return [ln[:width] for ln in kept[-lines:]]
