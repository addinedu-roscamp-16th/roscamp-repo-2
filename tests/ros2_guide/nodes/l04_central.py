#!/usr/bin/env python3
"""L-04 fleet central (runs on the PC).

Publishes /fleet/heartbeat (STATE), /fleet/robots (LATCHED) and /central/events (EVENT) at
fixed rates, and subscribes to the six robot state topics and the two vision topics. Names and
QoS follow contract.yaml; the message type is std_msgs/String.

Prints READY once. --seconds is only a safety cap; the runner stops it with SIGINT. The out
file is written on exit (also on SIGINT):
{"sent": {topic: n}, "seqs": {topic: [...]}, "recv_ns": {topic: [...]}}
recv_ns is time.monotonic_ns() on this device; it is never compared with the Pi clock.
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

STATE_TOPICS = tuple(f"/{n}/state" for n in
                     ("pinky1", "pinky2", "pinky3", "arm1", "arm2", "arm3"))  # fmt: skip
VISION_TOPICS = ("/arm1/vision/items", "/arm2/vision/items")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heartbeat-hz", type=float, required=True)
    ap.add_argument("--robots-hz", type=float, required=True)
    ap.add_argument("--events-hz", type=float, required=True)
    ap.add_argument("--msg-bytes", type=int, default=128)
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    profiles = qos.load_profiles()
    sent: dict[str, int] = {}
    seqs: dict[str, list[int]] = {t: [] for t in (*STATE_TOPICS, *VISION_TOPICS)}
    recv_ns: dict[str, list[int]] = {t: [] for t in (*STATE_TOPICS, *VISION_TOPICS)}

    def recorder(topic: str):
        def on_msg(msg) -> None:
            now = time.monotonic_ns()
            try:
                d = payload.decode(msg.data)
            except ValueError:
                return
            seqs[topic].append(d["seq"])
            recv_ns[topic].append(now)

        return on_msg

    def sender(pub, topic: str):
        sent[topic] = 0

        def on_timer() -> None:
            seq = sent[topic]
            pub.publish(String(data=payload.encode_sized(seq, time.time_ns(), args.msg_bytes)))
            sent[topic] = seq + 1

        return on_timer

    rclpy.init()
    node = rclpy.create_node("r2g_l04_central")
    try:
        for topic in STATE_TOPICS:
            node.create_subscription(String, topic, recorder(topic),
                                     qos.to_qos(profiles["STATE"]))  # fmt: skip
        for topic in VISION_TOPICS:
            node.create_subscription(String, topic, recorder(topic),
                                     qos.to_qos(profiles["LATCHED"]))  # fmt: skip
        for topic, profile, hz in (
            ("/fleet/heartbeat", "STATE", args.heartbeat_hz),
            ("/fleet/robots", "LATCHED", args.robots_hz),
            ("/central/events", "EVENT", args.events_hz),
        ):
            pub = node.create_publisher(String, topic, qos.to_qos(profiles[profile]))
            node.create_timer(1.0 / hz, sender(pub, topic))
        print("READY", flush=True)
        end = time.monotonic() + args.seconds
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        Path(args.out).write_text(
            json.dumps({"sent": sent, "seqs": seqs, "recv_ns": recv_ns}), encoding="utf-8"
        )
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
