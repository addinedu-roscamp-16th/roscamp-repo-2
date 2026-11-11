#!/usr/bin/env python3
"""G-11 node (PC or Raspberry Pi): publisher or subscriber for the isolation test.

pub: 5 Hz EVENT messages for --seconds; records the max of count_subscribers().
sub: counts received messages for --seconds; records the max of count_publishers() (polled
every 0.5 s). Both print READY once and end with one JSON line:
{"sent"|"received": n, "peer_seen_max": m}. The subscriber exits by itself after --seconds, so
its final line reaches the runner's stdout (a stopped remote ssh child would lose it).
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

from r2g import pacing, qos  # noqa: E402

RATE_HZ = 5.0
POLL_S = 0.5


def spin(node, timeout_sec: float) -> None:
    rclpy.spin_once(node, timeout_sec=timeout_sec)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=["pub", "sub"])
    ap.add_argument("--topic", required=True)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args(argv)

    count = 0
    peer_max = 0
    rclpy.init()
    node = rclpy.create_node(f"r2g_g11_{args.role}")
    try:
        profile = qos.to_qos(qos.load_profiles()["EVENT"])
        if args.role == "pub":
            pub = node.create_publisher(String, args.topic, profile)
            print("READY", flush=True)
            period = 1.0 / RATE_HZ
            t0 = time.monotonic()
            while rclpy.ok() and time.monotonic() - t0 < args.seconds:
                pub.publish(String(data=f"g11 {count}"))
                count += 1
                peer_max = max(peer_max, pub.get_subscription_count())
                pacing.wait_until(
                    t0 + count * period, lambda timeout_sec: spin(node, timeout_sec)
                )
        else:
            def on_msg(_msg) -> None:
                nonlocal count
                count += 1

            node.create_subscription(String, args.topic, on_msg, profile)
            print("READY", flush=True)
            t0 = time.monotonic()
            next_poll = t0
            while rclpy.ok() and time.monotonic() - t0 < args.seconds:
                rclpy.spin_once(node, timeout_sec=0.05)
                if time.monotonic() >= next_poll:
                    peer_max = max(peer_max, node.count_publishers(args.topic))
                    next_poll += POLL_S
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    key = "sent" if args.role == "pub" else "received"
    print(json.dumps({key: count, "peer_seen_max": peer_max}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
