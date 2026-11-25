"""브리지 실행: 풀을 기다리지 않고 열고 노드를 MultiThreadedExecutor(4)로 돌린다 (설계 6.1)."""
import rclpy
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor

from service_bridge.core.db import dsn_from_env, make_pool
from service_bridge.ros.bridge_node import BridgeNode


def main(args=None):
    rclpy.init(args=args)
    pool = make_pool(dsn_from_env(), open=False)
    pool.open(wait=False)  # 첫 연결 확인은 노드의 타이머가 한다
    node = None
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        node = BridgeNode(pool)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # 끌 때: 서버 먼저 없앰 → 실행기 멈춤 → 노드 → 풀
        if node is not None:
            node.close_servers()
        executor.shutdown(timeout_sec=2.0)
        if node is not None:
            node.destroy_node()
        pool.close()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
