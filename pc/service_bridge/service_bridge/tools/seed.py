# ---- 시험 데이터: 실제 상품 다섯 개(재고 20, 원장 입고)와 시험 계정 ----
import psycopg

# (kind, name). kind는 ROS OrderItem.kind · 비전 클래스 이름과 같다(영문 소문자와 _).
# sim/fake_robots/scenarios의 팔 재고 · 주문 코드와 같게 둔다
PRODUCTS = (
    ("mentos", "미니멘토스 레인보우"),
    ("choco_bar", "자유시간 Mini"),
    ("mygummy", "과즙팡 비타민 마이구미"),
    ("choco_ball", "M&M's"),
    ("bungeo_cake", "참붕어빵"),
)
KINDS = tuple(k for k, _ in PRODUCTS)
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
        for kind, name in PRODUCTS:
            pid = conn.execute(
                "INSERT INTO product (kind, name) VALUES (%s, %s) RETURNING product_id",
                (kind, name)).fetchone()[0]
            conn.execute(
                "INSERT INTO stock_ledger (product_id, entry_type, change_qty, reason) "
                "VALUES (%s, 'INBOUND', %s, 'REGULAR')", (pid, STOCK))
            products[kind] = pid
    return {"account_id": account_id, "accounts": accounts, "products": products}
