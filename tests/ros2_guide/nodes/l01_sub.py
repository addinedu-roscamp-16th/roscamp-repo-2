#!/usr/bin/env python3
"""L-01 subscriber: appends one JSONL line per received message to --out."""
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--profile", required=True, choices=["STATE", "LATCHED", "EVENT"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=None)
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out = out_path.open("w", encoding="utf-8")
    bad = 0
    sub_created_ns = None

    rclpy.init()
    node = rclpy.create_node("r2g_l01_sub")

    def on_msg(msg) -> None:
        nonlocal bad
        rx_ns = time.time_ns()
        try:
            d = payload.decode(msg.data)
        except ValueError:
            bad += 1
            return
        out.write(json.dumps({"trial": d["trial"], "seq": d["seq"], "rx_ns": rx_ns}) + "\n")
        out.flush()

    profile = qos.load_profiles()[args.profile]
    node.create_subscription(String, args.topic, on_msg, qos.to_qos(profile))
    sub_created_ns = time.time_ns()
    print("READY", flush=True)
    try:
        if args.duration is None:
            rclpy.spin(node)
        else:
            end = time.monotonic() + args.duration
            while rclpy.ok() and time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        out.close()
        summary = out_path.with_name(out_path.name + ".summary.json")
        summary.write_text(json.dumps({"bad": bad, "sub_created_ns": sub_created_ns}), encoding="utf-8")
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
