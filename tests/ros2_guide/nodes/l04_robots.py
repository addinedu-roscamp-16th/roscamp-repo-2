#!/usr/bin/env python3
"""L-04 robots (runs on the Raspberry Pi): six robot nodes in one process.

Nodes pinky1~3 and arm1~3 run in one MultiThreadedExecutor. Each publishes /<name>/state
(STATE); arm1 and arm2 also publish /<arm>/vision/items (LATCHED). Each subscribes to
/fleet/heartbeat (STATE); pinky1 also subscribes to /central/events (EVENT) and /fleet/robots
(LATCHED). Topic names and QoS follow contract.yaml; the message type is std_msgs/String.

Prints READY once the nodes exist, then waits up to --match-timeout for the central to
subscribe to every state topic, then publishes for settle + seconds. Last stdout line:
{"sent": {topic: n}, "hb_recv_ns": {robot: [...]}, "window": [start_ns, end_ns],
 "seqs": {topic: [...]}, "matched": bool}
The window is [publish start + settle, publish end] on this device's time.monotonic_ns().
Robot-side seqs keys: "/fleet/heartbeat@<robot>", "/central/events", "/fleet/robots".
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor  # noqa: E402
from std_msgs.msg import String  # noqa: E402

from r2g import payload, qos  # noqa: E402

PINKIES = ("pinky1", "pinky2", "pinky3")
ARMS = ("arm1", "arm2", "arm3")
VISION_ARMS = ("arm1", "arm2")
TAIL_S = 1.0  # keep spinning after the last publish so reliable samples go out


class Robot:
    """One robot node: its publishers, timers and what it received."""

    def __init__(self, name: str, profiles: dict, args, seqs: dict, hb_recv_ns: dict):
        self.name = name
        self.node = rclpy.create_node(f"r2g_l04_{name}")
        self.args = args
        self.sent: dict[str, int] = {}
        self.timers = []
        self.state_topic = f"/{name}/state"
        self.state_pub = self.node.create_publisher(
            String, self.state_topic, qos.to_qos(profiles["STATE"])
        )
        self.sent[self.state_topic] = 0
        self.vision_topic = None
        self.vision_pub = None
        if name in VISION_ARMS:
            self.vision_topic = f"/{name}/vision/items"
            self.vision_pub = self.node.create_publisher(
                String, self.vision_topic, qos.to_qos(profiles["LATCHED"])
            )
            self.sent[self.vision_topic] = 0
        hb_key = f"/fleet/heartbeat@{name}"
        seqs[hb_key] = []
        hb_recv_ns[name] = []
        self.node.create_subscription(
            String, "/fleet/heartbeat", _recorder(seqs[hb_key], hb_recv_ns[name]),
            qos.to_qos(profiles["STATE"]),
        )  # fmt: skip
        if name == "pinky1":
            seqs["/central/events"] = []
            seqs["/fleet/robots"] = []
            self.node.create_subscription(
                String, "/central/events", _recorder(seqs["/central/events"], None),
                qos.to_qos(profiles["EVENT"]),
            )  # fmt: skip
            self.node.create_subscription(
                String, "/fleet/robots", _recorder(seqs["/fleet/robots"], None),
                qos.to_qos(profiles["LATCHED"]),
            )  # fmt: skip

    def matched(self) -> bool:
        return self.state_pub.get_subscription_count() > 0

    def start(self) -> None:
        self.timers.append(self.node.create_timer(1.0 / self.args.state_hz, self._state))
        if self.vision_pub is not None:
            self.timers.append(self.node.create_timer(1.0 / self.args.vision_hz, self._vision))

    def stop(self) -> None:
        for t in self.timers:
            t.cancel()

    def _state(self) -> None:
        self._publish(self.state_pub, self.state_topic, self.args.state_bytes)

    def _vision(self) -> None:
        self._publish(self.vision_pub, self.vision_topic, self.args.vision_bytes)

    def _publish(self, pub, topic: str, size: int) -> None:
        seq = self.sent[topic]
        pub.publish(String(data=payload.encode_sized(seq, time.time_ns(), size)))
        self.sent[topic] = seq + 1


def _recorder(seqs: list, recv_ns: list | None):
    def on_msg(msg) -> None:
        now = time.monotonic_ns()
        try:
            d = payload.decode(msg.data)
        except ValueError:
            return
        seqs.append(d["seq"])
        if recv_ns is not None:
            recv_ns.append(now)

    return on_msg


def _spin_until(executor, deadline: float, stop=None) -> None:
    while rclpy.ok() and time.monotonic() < deadline:
        if stop is not None and stop():
            return
        executor.spin_once(timeout_sec=0.05)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-hz", type=float, required=True)
    ap.add_argument("--vision-hz", type=float, required=True)
    ap.add_argument("--state-bytes", type=int, required=True)
    ap.add_argument("--vision-bytes", type=int, required=True)
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--settle", type=float, default=5.0)
    ap.add_argument("--match-timeout", type=float, default=10.0)
    args = ap.parse_args(argv)

    profiles = qos.load_profiles()
    seqs: dict[str, list[int]] = {}
    hb_recv_ns: dict[str, list[int]] = {}
    robots: list[Robot] = []
    window = [0, 0]
    matched = False
    rclpy.init()
    executor = MultiThreadedExecutor()
    try:
        for name in (*PINKIES, *ARMS):
            robot = Robot(name, profiles, args, seqs, hb_recv_ns)
            robots.append(robot)
            executor.add_node(robot.node)
        print("READY", flush=True)
        _spin_until(executor, time.monotonic() + args.match_timeout,
                    stop=lambda: all(r.matched() for r in robots))  # fmt: skip
        matched = all(r.matched() for r in robots)
        if matched:
            start_ns = time.monotonic_ns()
            for r in robots:
                r.start()
            _spin_until(executor, time.monotonic() + args.settle + args.seconds)
            for r in robots:
                r.stop()
            window = [start_ns + int(args.settle * 1e9), time.monotonic_ns()]
            _spin_until(executor, time.monotonic() + TAIL_S)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        for r in robots:
            r.stop()
        executor.shutdown()
        for r in robots:
            r.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    sent = {topic: n for r in robots for topic, n in r.sent.items()}
    print(json.dumps({"sent": sent, "hb_recv_ns": hb_recv_ns, "window": window,
                      "seqs": seqs, "matched": matched}), flush=True)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
