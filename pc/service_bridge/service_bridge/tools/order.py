# ---- 화면 대신 주문 하나를 POST /api/orders로 보낸다 (설계 9) ----
#     python -m service_bridge.tools.order --account 1 --items 3:2,5:1 [--key <UUID>]
# 같은 --key로 두 번 부르면 같은 답이어야 한다(중복 접수 없음).
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

DEFAULT_URL = "http://localhost:8000"
TIMEOUT_SEC = 5.0


# ---- 품목 읽기 ----
def parse_lines(text: str) -> list[dict]:
    """"3:2,5:1" → [{"product_id": 3, "qty": 2}, {"product_id": 5, "qty": 1}]. 순서 그대로."""
    out = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        pid, sep, qty = part.partition(":")
        if not sep:
            raise ValueError(f"품목 형식은 product_id:개수: {part!r}")
        out.append({"product_id": int(pid), "qty": int(qty)})
    return out


# ---- 보내기 ----
def post_order(url: str, account_id: int, items: list[dict], key: str) -> tuple[int, str]:
    """(상태 코드, 본문). 연결이 안 되면 (0, 이유)."""
    req = urllib.request.Request(
        url.rstrip("/") + "/api/orders", method="POST",
        data=json.dumps({"account_id": account_id, "items": items}).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as e:  # 4xx · 5xx도 본문을 보여 준다
        return e.code, e.read().decode()
    except (urllib.error.URLError, OSError) as e:
        return 0, f"연결 실패: {getattr(e, 'reason', e)}"


def main(args=None) -> int:
    p = argparse.ArgumentParser(description="POST /api/orders 시험 호출")
    p.add_argument("--account", type=int, required=True, help="account_id")
    p.add_argument("--items", required=True, help="product_id:개수,… 예 3:2,5:1")
    p.add_argument("--key", default=None, help="Idempotency-Key (없으면 새 UUID)")
    p.add_argument("--url", default=DEFAULT_URL, help=f"웹 주소 (기본 {DEFAULT_URL})")
    opts = p.parse_args(args)
    try:
        items = parse_lines(opts.items)
    except ValueError as e:
        p.error(str(e))
    key = opts.key or str(uuid.uuid4())
    status, body = post_order(opts.url, opts.account, items, key)
    print(f"key {key}")
    print(f"status {status}")
    print(body)
    return 0 if status == 202 else 1


if __name__ == "__main__":
    sys.exit(main())
