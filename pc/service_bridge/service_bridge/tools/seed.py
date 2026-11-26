# ---- 시험 데이터: 상품 A ~ D(재고 20, 원장 입고)와 시험 계정 ----
import psycopg

KINDS = ("A", "B", "C", "D")  # sim/fake_robots/scenarios와 같은 이름
STOCK = 20
TEST_LOGIN = "tester"


def seed(conn: psycopg.Connection) -> dict:
    """{"account_id": int, "accounts": {"tester", "tester2", "admin": id},
    "products": {kind: product_id}}를 돌려준다. "account_id"는 tester.

    재고는 상품을 0으로 만든 뒤 stock_ledger 입고로 넣는다
    (product.stock_qty 직접 쓰기는 트리거가 막음).
    """
    with conn.transaction():
        accounts = {}
        for login, role in ((TEST_LOGIN, "USER"), ("tester2", "USER"), ("admin", "ADMIN")):
            accounts[login] = conn.execute(
                "INSERT INTO account (login_id, password_hash, role, name) "
                "VALUES (%s, 'x', %s, %s) RETURNING account_id",
                (login, role, login)).fetchone()[0]
        account_id = accounts[TEST_LOGIN]
        products = {}
        for kind in KINDS:
            pid = conn.execute(
                "INSERT INTO product (kind, name) VALUES (%s, %s) RETURNING product_id",
                (kind, kind)).fetchone()[0]
            conn.execute(
                "INSERT INTO stock_ledger (product_id, entry_type, change_qty, reason) "
                "VALUES (%s, 'INBOUND', %s, 'REGULAR')", (pid, STOCK))
            products[kind] = pid
    return {"account_id": account_id, "accounts": accounts, "products": products}
