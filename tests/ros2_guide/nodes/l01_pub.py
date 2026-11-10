#!/usr/bin/env python3
"""L-01 publisher: sends a message right after creating the publisher."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rclpy  # noqa: E402
from std_msgs.msg import String  # noqa: E402

from r2g import payload, qos  # noqa: E402


def _spin_for(node, seconds: float) -> None:
    end = time.monotonic() + seconds
    while rclpy.ok() and time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.05)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--profile", required=True, choices=["STATE", "LATCHED", "EVENT"])
    ap.add_argument("--trial", type=int, required=True)
    ap.add_argument("--mode", required=True, choices=["once", "wait_match", "stream"])
    ap.add_argument("--linger", type=float, default=2.0)
    ap.add_argument("--wait-timeout", type=float, default=5.0)
    ap.add_argument("--rate", type=float, default=10.0)
    ap.add_argument("--stream-seconds", type=float, default=3.0)
    args = ap.parse_args(argv)

    rclpy.init()
    node = rclpy.create_node("r2g_l01_pub")
    matched = None
    wait_ms = None
    sent = 0
    pub_start_ns = None
    try:
        profile = qos.load_profiles()[args.profile]
        pub_start_ns = time.time_ns()
        pub = node.create_publisher(String, args.topic, qos.to_qos(profile))

        def send(seq: int) -> None:
            nonlocal sent
            pub.publish(String(data=payload.encode(args.trial, seq, time.time_ns())))
            sent += 1

        if args.mode == "wait_match":
            t0 = time.monotonic()
            while rclpy.ok() and pub.get_subscription_count() == 0:
                if time.monotonic() - t0 >= args.wait_timeout:
                    break
                rclpy.spin_once(node, timeout_sec=0.01)
            matched = pub.get_subscription_count() > 0
            wait_ms = round((time.monotonic() - t0) * 1000, 1)
            send(0)
        elif args.mode == "once":
            send(0)
        else:
            period = 1.0 / args.rate
            total = int(round(args.stream_seconds * args.rate))
            t0 = time.monotonic()
            for seq in range(total):
                if not rclpy.ok():
                    break
                send(seq)
                _spin_for(node, max(0.0, t0 + (seq + 1) * period - time.monotonic()))
        _spin_for(node, args.linger)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    print(
        json.dumps(
            {
                "trial": args.trial,
                "mode": args.mode,
                "pub_start_ns": pub_start_ns,
                "matched": matched,
                "wait_ms": wait_ms,
                "sent": sent,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
