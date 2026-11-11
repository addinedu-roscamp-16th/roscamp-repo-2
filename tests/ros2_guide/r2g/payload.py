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


def encode_sized(seq: int, stamp_ns: int, size: int) -> str:
    """Payload whose UTF-8 length is exactly `size` bytes (padding is ASCII)."""
    head = len(encode(0, seq, stamp_ns).encode("utf-8"))
    if size < head:
        raise ValueError(f"size {size} is smaller than the {head}-byte header")
    return encode(0, seq, stamp_ns, pad_bytes=size - head)
