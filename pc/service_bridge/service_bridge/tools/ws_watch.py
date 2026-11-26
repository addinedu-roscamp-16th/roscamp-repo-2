# ---- 화면 대신 WS에 붙어 받은 메시지를 한 줄에 하나씩 JSON으로 출력한다 (설계 9) ----
#     python -m service_bridge.tools.ws_watch --account 1
#     python -m service_bridge.tools.ws_watch --admin
from __future__ import annotations

import argparse
import json
import sys

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

DEFAULT_URL = "http://localhost:8000"


# ---- 주소 만들기 ----
def ws_url(base: str, admin: bool, account: int | None) -> str:
    """http(s)://… → ws(s)://…/ws/admin 또는 /ws/customer?account_id=N."""
    base = base.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/ws/admin" if admin else f"{base}/ws/customer?account_id={account}"


def main(args=None) -> int:
    p = argparse.ArgumentParser(description="WS 메시지를 한 줄씩 출력")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--admin", action="store_true", help="관리자 WS")
    who.add_argument("--account", type=int, help="고객 WS의 account_id")
    p.add_argument("--url", default=DEFAULT_URL, help=f"웹 주소 (기본 {DEFAULT_URL})")
    opts = p.parse_args(args)
    url = ws_url(opts.url, opts.admin, opts.account)
    try:
        with connect(url) as ws:
            print(f"# 연결됨 {url}", file=sys.stderr, flush=True)
            for raw in ws:
                print(json.dumps(json.loads(raw), ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        return 0
    except ConnectionClosed as e:
        # 4404: 모르는 계정. 서버가 닫으면 이유를 남기고 끝낸다
        print(f"# 닫힘 code={e.rcvd.code if e.rcvd else None}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"# 연결 실패: {e}", file=sys.stderr)
        return 1
    print("# 서버가 연결을 닫음", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
