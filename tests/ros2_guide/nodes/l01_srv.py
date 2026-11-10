#!/usr/bin/env python3
"""L-01 service variant: a Trigger server and a one-shot client."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from std_srvs.srv import Trigger  # noqa: E402


def run_server(service: str) -> int:
    rclpy.init()
    node = rclpy.create_node("r2g_l01_srv")

    def handle(_req, resp):
        resp.success = True
        resp.message = "ok"
        return resp

    node.create_service(Trigger, service, handle)
    print("READY", flush=True)  # nothing may be printed after this line
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


def run_client(service: str, trial: int, timeout: float) -> int:
    rclpy.init()
    node = rclpy.create_node("r2g_l01_cli")
    ok = False
    wait_ms = None
    try:
        client = node.create_client(Trigger, service)
        t0 = time.monotonic()
        found = client.wait_for_service(timeout_sec=timeout)
        wait_ms = round((time.monotonic() - t0) * 1000, 1)
        if found:
            future = client.call_async(Trigger.Request())
            end = time.monotonic() + timeout
            while rclpy.ok() and not future.done() and time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0.05)
            result = future.result() if future.done() else None
            ok = bool(result is not None and result.success)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    print(json.dumps({"trial": trial, "ok": ok, "wait_ms": wait_ms}), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=["server", "client"])
    ap.add_argument("--service", required=True)
    ap.add_argument("--trial", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args(argv)
    if args.role == "server":
        return run_server(args.service)
    return run_client(args.service, args.trial, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
