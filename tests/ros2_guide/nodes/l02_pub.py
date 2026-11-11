#!/usr/bin/env python3
"""L-02 publisher (runs on the Raspberry Pi): fixed-rate, fixed-size stream.

Waits up to --match-timeout for a subscriber; without one nothing is published. Last stdout
line: {"sent", "matched"}. The expected count is int(round(seconds * rate)).
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
SETTLE_S = 2.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--reliability", required=True, choices=["best_effort", "reliable"])
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--rate", type=float, required=True)
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--match-timeout", type=float, default=10.0)
    args = ap.parse_args(argv)

    rclpy.init()
    node = rclpy.create_node("r2g_l02_pub")
    sent = 0
    matched = False
    try:
        pub = node.create_publisher(
            String, args.topic, qos.to_qos(qos.custom(args.reliability, "volatile", DEPTH))
        )
        t0 = time.monotonic()
        while rclpy.ok() and pub.get_subscription_count() == 0:
            if time.monotonic() - t0 >= args.match_timeout:
                break
            rclpy.spin_once(node, timeout_sec=0.05)
        matched = pub.get_subscription_count() > 0
        if matched:
            period = 1.0 / args.rate
            total = int(round(args.seconds * args.rate))
            t0 = time.monotonic()
            for seq in range(total):
                if not rclpy.ok():
                    break
                pub.publish(String(data=payload.encode_sized(seq, time.time_ns(), args.size)))
                sent += 1
                rclpy.spin_once(
                    node, timeout_sec=max(0.0, t0 + (seq + 1) * period - time.monotonic())
                )
            end = time.monotonic() + SETTLE_S
            while rclpy.ok() and time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0.05)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    print(json.dumps({"sent": sent, "matched": matched}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
