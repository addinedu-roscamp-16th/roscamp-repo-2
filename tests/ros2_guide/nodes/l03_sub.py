#!/usr/bin/env python3
"""L-03 subscriber: slow processing, naive callback vs store-latest + timer.

Prints READY once and nothing afterwards; results go to --out (written even on SIGINT):
{"processed", "samples": [[stamp_ns, proc_ns], ...], "bad"}. proc_ns is time.time_ns() when
processing of that message starts; staleness = proc_ns - stamp_ns.
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rclpy  # noqa: E402
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup  # noqa: E402
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor  # noqa: E402
from std_msgs.msg import String  # noqa: E402

from r2g import payload, qos  # noqa: E402

PROCESS_PERIOD_S = 0.1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--reliability", required=True, choices=["best_effort", "reliable"])
    ap.add_argument("--depth", type=int, required=True)
    ap.add_argument("--mode", required=True, choices=["naive", "latest"])
    ap.add_argument("--work-ms", type=float, default=100.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    samples: list[list[int]] = []
    bad = 0
    work_s = args.work_ms / 1000.0
    lock = threading.Lock()
    latest = None

    def record(msg) -> None:
        nonlocal bad
        proc_ns = time.time_ns()
        try:
            d = payload.decode(msg.data)
        except ValueError:
            bad += 1
            return
        samples.append([d["stamp_ns"], proc_ns])

    def on_naive(msg) -> None:
        record(msg)
        time.sleep(work_s)

    def on_scan(msg) -> None:
        nonlocal latest
        with lock:
            latest = msg

    def on_process() -> None:
        nonlocal latest
        with lock:
            msg, latest = latest, None
        if msg is None:
            return
        record(msg)
        time.sleep(work_s)

    rclpy.init()
    node = rclpy.create_node("r2g_l03_sub")
    executor = None
    try:
        q = qos.to_qos(qos.custom(args.reliability, "volatile", args.depth))
        if args.mode == "naive":
            node.create_subscription(String, args.topic, on_naive, q)
            executor = rclpy.executors.SingleThreadedExecutor()
        else:
            rx_group = MutuallyExclusiveCallbackGroup()
            work_group = MutuallyExclusiveCallbackGroup()
            node.create_subscription(String, args.topic, on_scan, q, callback_group=rx_group)
            node.create_timer(PROCESS_PERIOD_S, on_process, callback_group=work_group)
            executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(node)
        print("READY", flush=True)
        end = time.monotonic() + args.seconds
        while rclpy.ok() and time.monotonic() < end:
            executor.spin_once(timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        Path(args.out).write_text(
            json.dumps({"processed": len(samples), "samples": samples, "bad": bad}),
            encoding="utf-8",
        )
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
