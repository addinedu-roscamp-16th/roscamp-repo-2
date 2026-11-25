"""브리지 시험 틀: 틀 DB를 한 번 만들고 시험마다 새 DB를 복사한다 (design/db 시험과 같은 방식)."""
import os
import pathlib
import sys
import uuid

import psycopg
import pytest

# rclpy 시험은 이 컴퓨터 안에서만 찾는다 (개인 도메인은 시험마다 rclpy.init에서 줌)
os.environ["ROS_AUTOMATIC_DISCOVERY_RANGE"] = "LOCALHOST"

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
DB_DIR = REPO / "design" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_DIR / "tests"))

from helpers import make_account, make_product  # noqa: E402
from service_bridge.core.db import make_pool  # noqa: E402

DSN = os.environ.get("IFP_PG_DSN", "postgresql://postgres:ifp_test@localhost:55432/postgres")
TEMPLATE = "sb_tmpl"


def _dsn(dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(DSN, dbname=dbname)


@pytest.fixture(scope="session")
def template_db() -> str:
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEMPLATE} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEMPLATE}")
    with psycopg.connect(_dsn(TEMPLATE), autocommit=True) as c:
        for name in ("order_db.sql", "order_db_extra.sql"):
            c.execute((DB_DIR / name).read_text(encoding="utf-8"))
    return TEMPLATE


@pytest.fixture
def test_db(template_db):
    name = f"t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name} TEMPLATE {template_db}")
    yield name
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def pool(test_db):
    p = make_pool(_dsn(test_db))
    yield p
    p.close()


@pytest.fixture
def conn(test_db):
    with psycopg.connect(_dsn(test_db), autocommit=True) as c:
        yield c


@pytest.fixture
def seeded(conn):
    """USER 계정 하나와 A~D 상품(재고 20). Task 7의 seed가 생기면 그쪽으로 바꾼다."""
    account_id = make_account(conn, role="USER")
    products = {k: make_product(conn, k, qty=20) for k in "ABCD"}
    return {"account_id": account_id, "products": products}
