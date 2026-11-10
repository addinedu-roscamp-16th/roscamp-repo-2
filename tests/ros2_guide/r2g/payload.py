"""Message payload format shared by the publisher and subscriber nodes."""
import json


def encode(trial: int, seq: int, stamp_ns: int, pad_bytes: int = 0) -> str:
    return json.dumps(
        {"trial": trial, "seq": seq, "stamp_ns": stamp_ns, "pad": "x" * pad_bytes},
        separators=(",", ":"),
    )


def decode(data: str) -> dict:
    try:
        obj = json.loads(data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not valid JSON: {data[:40]!r}") from exc
    if not isinstance(obj, dict):
        raise ValueError("payload is not a JSON object")
    try:
        return {
            "trial": int(obj["trial"]),
            "seq": int(obj["seq"]),
            "stamp_ns": int(obj["stamp_ns"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"missing or invalid key: {exc}") from exc
