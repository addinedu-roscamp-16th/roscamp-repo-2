"""시험 DB 준비: IPONIC_DB_DSN의 DB를 지우고 다시 만든 뒤 두 SQL과 seed 적용 (설계 8.1).

지우는 명령이므로 DB 이름이 `_test`로 끝날 때만 한다.
"""
import argparse
import os
import pathlib
import sys

import psycopg

from service_bridge.core.db import dsn_from_env
from service_bridge.tools.seed import seed

SQL_FILES = ("order_db.sql", "order_db_extra.sql")
MAINTENANCE_DB = "postgres"


def require_test_db(dsn: str) -> str:
    """DSN의 DB 이름. `_test`로 끝나지 않으면 SystemExit."""
    name = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname") or ""
    if not name.endswith("_test"):
        raise SystemExit(f"setup_db: DB 이름이 _test로 끝나지 않아 멈춤 (지우는 명령): {name!r}")
    return name


def find_sql_dir() -> pathlib.Path:
    """design/db 폴더: $IPONIC_REPO, 지금 폴더, 이 파일 위 폴더 순으로 찾는다."""
    bases = [pathlib.Path(p) for p in (os.environ.get("IPONIC_REPO"), os.getcwd()) if p]
    bases += pathlib.Path(__file__).resolve().parents
    for base in bases:
        d = base / "design" / "db"
        if all((d / f).is_file() for f in SQL_FILES):
            return d
    raise SystemExit("setup_db: design/db를 못 찾음. 저장소 폴더에서 부르거나 --sql-dir를 주세요")


def setup_db(dsn: str, sql_dir: pathlib.Path) -> dict:
    name = require_test_db(dsn)
    admin = psycopg.conninfo.make_conninfo(dsn, dbname=MAINTENANCE_DB)
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(dsn, autocommit=True) as c:
        for f in SQL_FILES:
            c.execute((sql_dir / f).read_text(encoding="utf-8"))
        return seed(c)


def main(args=None):
    parser = argparse.ArgumentParser(description="시험 DB를 지우고 다시 만든다 (_test DB만)")
    parser.add_argument("--sql-dir", type=pathlib.Path, help="order_db.sql이 있는 폴더")
    opts = parser.parse_args(args)
    dsn = dsn_from_env()
    require_test_db(dsn)
    out = setup_db(dsn, opts.sql_dir or find_sql_dir())
    print(f"setup_db: {require_test_db(dsn)} 준비됨, 계정 {out['account_id']}, "
          f"상품 {out['products']}")


if __name__ == "__main__":
    sys.exit(main())
