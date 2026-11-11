"""Fixed-rate pacing helper for the publisher nodes."""
import time


def wait_until(deadline: float, spin, now=time.monotonic) -> None:
    """Call spin(timeout_sec=remaining) until `deadline` (monotonic) has passed.

    rclpy.spin_once returns as soon as an event is ready, so a single call can end before the
    deadline; the loop keeps the publish times on the grid."""
    while (remaining := deadline - now()) > 0:
        spin(timeout_sec=remaining)
