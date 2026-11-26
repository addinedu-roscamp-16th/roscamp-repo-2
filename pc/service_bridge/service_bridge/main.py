# ---- 브리지 실행: 웹(uvicorn, 메인 스레드)과 BridgeNode(실행기, 뒷 스레드)를 한 프로세스로 (설계 6.1) ----
import os
import signal
import threading

import rclpy
import uvicorn
from fastapi import FastAPI
from psycopg_pool import ConnectionPool
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.signals import SignalHandlerOptions

from service_bridge.core.db import dsn_from_env, make_pool
from service_bridge.ros.bridge_node import BridgeNode
from service_bridge.web.app import create_app

DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 8000
EXECUTOR_JOIN_SEC = 3.0


# ---- 노드와 앱 묶기 (시험도 이 함수를 쓴다) ----
def build(bridge_pool: ConnectionPool, web_pool: ConnectionPool, dsn: str,
          **node_kwargs) -> tuple[BridgeNode, FastAPI]:
    """노드를 먼저 만든다: 모드는 노드 매개변수(-p mode:=)에서만 읽기 때문.
    노드의 로봇 콜백은 앱이 생긴 뒤에야 불리도록(실행기를 돌리기 전) 앱을 나중에 가리킨다."""
    holder: dict[str, FastAPI] = {}

    def on_robots(robots: list[dict]) -> None:
        app = holder.get("app")
        if app is not None:
            app.state.robots.put(robots)

    node = BridgeNode(bridge_pool, on_robots=on_robots, **node_kwargs)
    app = create_app(web_pool, dsn, node.mode, node.wake_dispatch)
    holder["app"] = app
    return node, app


def _web_address() -> tuple[str, int]:
    return (os.environ.get("IPONIC_WEB_HOST", DEFAULT_WEB_HOST),
            int(os.environ.get("IPONIC_WEB_PORT", str(DEFAULT_WEB_PORT))))


# ---- SIGTERM ----
class _Terminated(Exception):
    pass


def _on_sigterm(_sig, _frame) -> None:
    # uvicorn은 run()이 끝날 때 받은 신호를 원래 처리기로 다시 낸다. 기본 SIGTERM 처리기면
    # 그 자리에서 프로세스가 죽어 아래 끄기 순서를 건너뛰므로, 예외로 바꿔 finally로 보낸다
    raise _Terminated()


def _spin(executor: MultiThreadedExecutor) -> None:
    try:
        executor.spin()
    except ExternalShutdownException:
        pass


def main(args=None):
    # 신호(SIGINT · SIGTERM)는 메인 스레드의 uvicorn이 받는다
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGTERM, _on_sigterm)
    dsn = dsn_from_env()
    pool = make_pool(dsn, open=False)
    # DB가 없어도 /system/mode는 바로 내야 하므로 기다리지 않는다. 첫 연결 확인은 노드 타이머
    pool.open(wait=False)
    web_pool = make_pool(dsn, open=False)
    web_pool.open(wait=False)
    node = None
    thread = None
    # 스레드 수는 풀 크기(4)와 같게: 콜백마다 연결 하나를 쓰므로 더 많으면 풀 대기만 는다
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        node, app = build(pool, web_pool, dsn)
        executor.add_node(node)
        thread = threading.Thread(target=_spin, args=(executor,), name="ros_executor",
                                  daemon=True)
        thread.start()
        host, port = _web_address()
        # 끝나면 돌아온다: 앱 lifespan이 WS를 닫고 LISTEN을 멈춘 뒤
        uvicorn.Server(uvicorn.Config(app, host=host, port=port)).run()
    except (KeyboardInterrupt, _Terminated):
        # SIGINT는 KeyboardInterrupt, SIGTERM은 _Terminated로 (uvicorn이 다시 낸 신호)
        pass
    finally:
        # 끌 때(1단계 순서): 서버 먼저 없앰 → 실행기 멈춤 → 노드 → 풀, 그 뒤 웹 풀.
        # 새 요청을 먼저 막아, 처리 중인 콜백이 닫힌 풀을 만나지 않게 한다
        if node is not None:
            node.close_servers()
        executor.shutdown(timeout_sec=2.0)
        if thread is not None:
            thread.join(timeout=EXECUTOR_JOIN_SEC)
        if node is not None:
            node.destroy_node()
        pool.close()
        web_pool.close()
        rclpy.try_shutdown()
        # 끝까지 왔는지 기록(시험 · 손 확인에서 끄기 순서가 다 돌았는지 봄)
        rclpy.logging.get_logger("service_bridge").info("브리지 끝")


if __name__ == "__main__":
    main()
