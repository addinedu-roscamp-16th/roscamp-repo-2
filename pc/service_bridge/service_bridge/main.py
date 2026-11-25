# ---- 브리지 실행: 풀을 기다리지 않고 열고 노드를 MultiThreadedExecutor(4)로 돌린다 ----
import rclpy
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor

from service_bridge.core.db import dsn_from_env, make_pool
from service_bridge.ros.bridge_node import BridgeNode


def main(args=None):
    rclpy.init(args=args)
    pool = make_pool(dsn_from_env(), open=False)
    # DB가 없어도 /system/mode는 바로 내야 하므로 기다리지 않는다. 첫 연결 확인은 노드 타이머
    pool.open(wait=False)
    node = None
    # 스레드 수는 풀 크기(4)와 같게: 콜백마다 연결 하나를 쓰므로 더 많으면 풀 대기만 는다
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        node = BridgeNode(pool)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # 끌 때: 서버 먼저 없앰 → 실행기 멈춤 → 노드 → 풀.
        # 새 요청을 먼저 막아, 처리 중인 콜백이 닫힌 풀을 만나지 않게 한다
        if node is not None:
            node.close_servers()
        executor.shutdown(timeout_sec=2.0)
        if node is not None:
            node.destroy_node()
        pool.close()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
