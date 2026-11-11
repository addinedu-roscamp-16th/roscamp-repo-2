#!/usr/bin/env python3
"""L-03 publisher: fixed-rate stream stamped right before each publish().

Last stdout line: {"sent", "last_publish_ns", "matched"}. last_publish_ns is time.time_ns()
right after the last publish() returns; the runner uses it to cut the live window.
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

MATCH_WAIT_S = 5.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--reliability", required=True, choices=["best_effort", "reliable"])
    ap.add_argument("--depth", type=int, required=True)
    ap.add_argument("--rate", type=float, default=50.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args(argv)

    rclpy.init()
    node = rclpy.create_node("r2g_l03_pub")
    sent = 0
    last_publish_ns = None
    matched = False
    try:
        pub = node.create_publisher(
            String, args.topic, qos.to_qos(qos.custom(args.reliability, "volatile", args.depth))
        )
        t0 = time.monotonic()
        while rclpy.ok() and pub.get_subscription_count() == 0:
            if time.monotonic() - t0 >= MATCH_WAIT_S:
                break
            rclpy.spin_once(node, timeout_sec=0.01)
        matched = pub.get_subscription_count() > 0
        period = 1.0 / args.rate
        total = int(round(args.seconds * args.rate))
        t0 = time.monotonic()
        for seq in range(total):
            if not rclpy.ok():
                break
            pub.publish(String(data=payload.encode(0, seq, time.time_ns())))
            last_publish_ns = time.time_ns()
            sent += 1
            rclpy.spin_once(
                node, timeout_sec=max(0.0, t0 + (seq + 1) * period - time.monotonic())
            )
        end = time.monotonic() + 0.2
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.05)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    result = {"sent": sent, "last_publish_ns": last_publish_ns, "matched": matched}
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
