#!/usr/bin/env python3
"""L-04 video receive counter (runs on the PC, no ROS).

Counts UDP bytes arriving on --port until SIGINT (or the --seconds safety cap), then writes
{"bytes", "packets", "seconds"} to --out. seconds is the span from the first to the last
packet on this device's time.monotonic() (0 with fewer than 2 packets).
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    total = packets = 0
    first = last = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", args.port))
        sock.settimeout(0.2)
        print("READY", flush=True)
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            now = time.monotonic()
            if first is None:
                first = now
            last = now
            total += len(data)
            packets += 1
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        seconds = round(last - first, 3) if first is not None and packets > 1 else 0.0
        Path(args.out).write_text(
            json.dumps({"bytes": total, "packets": packets, "seconds": seconds}),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
