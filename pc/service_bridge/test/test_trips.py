"""시험 도구: insert_trip (설계 8.2 1) · setup_db의 시험 DB 확인."""
import re

import pytest
from service_bridge.tools.setup_db import require_test_db
from service_bridge.tools.trips import insert_trip
from test_reports import count


def test_insert_trip_ids_and_history(conn, seeded):
    o, t = insert_trip(conn, seeded["account_id"], {"mentos": 1, "mygummy": 1})
    assert re.fullmatch(r"O-\d{8}-0001", o) and t == f"{o}-1"
    assert count(conn, "status_history where order_id=%s", o) == 2
    assert count(conn, "status_history where trip_id=%s and actor='USER'", t) == 1
    assert count(conn, "trip_item where trip_id=%s", t) == 2
    o2, _ = insert_trip(conn, seeded["account_id"], {"choco_bar": 2})
    assert o2 == o[:-4] + "0002"


def test_insert_trip_over_basket_capacity_rejected(conn, seeded):
    with pytest.raises(ValueError):
        insert_trip(conn, seeded["account_id"], {"mentos": 3, "choco_bar": 2})
    assert count(conn, "orders") == 0


def test_setup_db_only_touches_test_dbs():
    assert require_test_db("postgresql://u:p@h:1/ifp_test") == "ifp_test"
    with pytest.raises(SystemExit):
        require_test_db("postgresql://u:p@h:1/ifp")
