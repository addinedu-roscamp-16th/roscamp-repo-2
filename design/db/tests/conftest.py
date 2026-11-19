"""주문 DB 시험 틀: 두 SQL 파일을 적용한 틀 DB를 만들고, 시험마다 그 틀에서 새 DB를 복사한다."""
import os
import pathlib
import uuid

import psycopg
import pytest

DSN = os.environ.get("IFP_PG_DSN", "postgresql://postgres:ifp_test@localhost:55432/postgres")
DB_DIR = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = "ifp_tmpl"


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
def conn_factory(test_db):
    opened = []

    def make(autocommit: bool = True) -> psycopg.Connection:
        c = psycopg.connect(_dsn(test_db), autocommit=autocommit)
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.close()


@pytest.fixture
def conn(conn_factory) -> psycopg.Connection:
    return conn_factory()
