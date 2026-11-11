#!/usr/bin/env python3
"""L-02 subscriber (runs on the PC): records seq and local receive time of every message.

Prints READY once. --seconds is only a safety cap; the runner stops it with SIGINT. The out
file is written on exit (also on SIGINT): {"seqs": [...], "recv_ns": [...], "bad": n}.
recv_ns is time.monotonic_ns() on this device; it is never compared with the publisher clock.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from std_msgs.msg import String  # noqa: E402

from r2g import payload, qos  # noqa: E402

DEPTH = 50


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--reliability", required=True, choices=["best_effort", "reliable"])
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    seqs: list[int] = []
    recv_ns: list[int] = []
    bad = 0

    def on_msg(msg) -> None:
        nonlocal bad
        now = time.monotonic_ns()
        try:
            d = payload.decode(msg.data)
        except ValueError:
            bad += 1
            return
        seqs.append(d["seq"])
        recv_ns.append(now)

    rclpy.init()
    node = rclpy.create_node("r2g_l02_sub")
    try:
        node.create_subscription(
            String, args.topic, on_msg,
            qos.to_qos(qos.custom(args.reliability, "volatile", DEPTH)),
        )  # fmt: skip
        print("READY", flush=True)
        end = time.monotonic() + args.seconds
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        Path(args.out).write_text(
            json.dumps({"seqs": seqs, "recv_ns": recv_ns, "bad": bad}), encoding="utf-8"
        )
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
