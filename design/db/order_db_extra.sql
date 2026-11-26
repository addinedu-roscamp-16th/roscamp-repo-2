-- 주문 관리 DB: DBML로 표현하지 못하는 규칙
-- 적용 순서: order_db.sql → order_db_extra.sql
-- 원본 설계: docs/superpowers/specs/2026-11-18-order-db-design.md

-- == Task 2: CHECK · 부분 UNIQUE · 생성 칼럼 · updated_at ==

-- 값 목록 (설계 문서 4절 표의 값 그대로)
ALTER TABLE account ADD CONSTRAINT ck_account_role CHECK (role IN ('USER', 'ADMIN', 'DEV'));

ALTER TABLE product
  ADD CONSTRAINT ck_product_stock_qty CHECK (stock_qty >= 0),
  ADD CONSTRAINT ck_product_low_stock_threshold CHECK (low_stock_threshold >= 0);

ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN (
  'RECEIVED', 'ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'SHIPPED', 'PARTIAL', 'HELD', 'CANCELLED'));

ALTER TABLE order_item ADD CONSTRAINT ck_order_item_qty CHECK (qty_ordered > 0);

ALTER TABLE trip
  ADD CONSTRAINT ck_trip_status CHECK (status IN (
    'RECEIVED', 'ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'SHIPPED', 'PARTIAL', 'HELD', 'CANCELLED')),
  ADD CONSTRAINT ck_trip_seq CHECK (seq > 0),
  -- 보고 번호 (report_epoch, report_n)는 함께 채운다 (통신 규칙 #18)
  ADD CONSTRAINT ck_trip_report_no CHECK ((report_epoch IS NULL) = (report_n IS NULL));

ALTER TABLE bridge_session ADD CONSTRAINT ck_bridge_session_epoch CHECK (epoch > 0);

ALTER TABLE trip_dispatch ADD CONSTRAINT ck_trip_dispatch_attempts CHECK (attempts >= 0);

ALTER TABLE trip_item ADD CONSTRAINT ck_trip_item_qty CHECK (
  qty_ordered > 0 AND qty_placed >= 0 AND qty_missing >= 0 AND qty_placed + qty_missing <= qty_ordered);

ALTER TABLE status_history
  ADD CONSTRAINT ck_status_history_to_status CHECK (to_status IN (
    'RECEIVED', 'ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'SHIPPED', 'PARTIAL', 'HELD', 'CANCELLED')),
  ADD CONSTRAINT ck_status_history_from_status CHECK (from_status IN (
    'RECEIVED', 'ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'SHIPPED', 'PARTIAL', 'HELD', 'CANCELLED')),
  ADD CONSTRAINT ck_status_history_reason CHECK (reason IN (
    -- contract 사유 코드 표의 이름 (통신 규칙 #37): 인식 오류 RECOGNITION_ERROR, Pode 장애 ROBOT_FAULT, Phody 장애 ARM_FAULT
    'OUT_OF_STOCK', 'PICK_FAILED', 'RECOGNITION_ERROR', 'ROBOT_FAULT', 'ARM_FAULT', 'FLEET_RESTART', 'USER_CANCEL',
    'NOTHING_TO_FILL', 'STOCK_RECHECK', 'FORCED_SHIP', 'FORCED_PARTIAL', 'REASSIGN', 'RETURNED_TO_SHELF')),
  ADD CONSTRAINT ck_status_history_actor CHECK (actor IN ('USER', 'ADMIN', 'SYSTEM'));

ALTER TABLE alert
  ADD CONSTRAINT ck_alert_level CHECK (level IN ('INFO', 'WARN', 'ERROR')),
  ADD CONSTRAINT ck_alert_status CHECK (status IN ('OPEN', 'RESOLVED'));

ALTER TABLE admin_action
  ADD CONSTRAINT ck_admin_action_action CHECK (action IN (
    -- 주문 조치
    'RESUME_FILL', 'MARK_MISSING', 'FORCE_SHIP', 'FORCE_PARTIAL', 'RETURN_TO_SHELF',
    -- 로봇 조치
    'CONFIRM_CLEAR', 'REINSTATE', 'FAULT_RESOLVED', 'RESUME_ARM', 'ARM_RECOVER', 'LOCK_RELEASE', 'LOCK_ENGAGE',
    -- 서비스
    'SERVICE_PAUSE', 'SERVICE_RESUME', 'ESTOP', 'ESTOP_RELEASE', 'SETTING_CHANGE',
    -- 마감
    'CLOSE_START', 'CLOSE_CONFIRM', 'CLOSE_CANCEL',
    -- 시험 (시험 DB에만 생김)
    'MOCK_SCENARIO', 'MOCK_ORDER', 'FORCE_CONDITION', 'FORCE_STATE', 'FORCE_CLEAR', 'CHARGE_END')),
  ADD CONSTRAINT ck_admin_action_result CHECK (result IN ('SENT', 'ACCEPTED', 'REJECTED', 'LOCAL')),
  -- ReportCommand 최종 결과 (통신 규칙 #25 ⑥): UNCONFIRMED = ROBOT_TIMEOUT(결과 모름), 30초가 지나면 TIMEOUT
  ADD CONSTRAINT ck_admin_action_final_result CHECK (final_result IN ('ACCEPTED', 'REJECTED', 'UNCONFIRMED', 'TIMEOUT'));

ALTER TABLE stock_close
  ADD CONSTRAINT ck_stock_close_status CHECK (status IN ('IN_PROGRESS', 'CONFIRMED', 'CANCELLED')),
  ADD CONSTRAINT ck_stock_close_kind CHECK (close_kind IN ('MANUAL', 'TEST_ENTRY', 'OPS_RESUME')),
  ADD CONSTRAINT ck_stock_close_finished CHECK ((status = 'IN_PROGRESS') = (finished_at IS NULL)),
  ADD CONSTRAINT ck_stock_close_starter CHECK ((close_kind = 'OPS_RESUME') = (started_by IS NULL)),
  ADD CONSTRAINT ck_stock_close_ops_resume_no_cancel CHECK (close_kind <> 'OPS_RESUME' OR status <> 'CANCELLED');

ALTER TABLE stock_count
  ADD CONSTRAINT ck_stock_count_qty CHECK (counted_qty >= 0 AND book_qty >= 0 AND in_transit_qty >= 0),
  ADD CONSTRAINT ck_stock_count_reason CHECK (reason IN (
    'COUNT_DIFF', 'RECOGNITION_ERROR', 'DAMAGE_FOUND', 'LOST', 'INPUT_ERROR', 'TEST_RUN', 'OTHER')),
  ADD CONSTRAINT ck_stock_count_other_note CHECK (reason IS DISTINCT FROM 'OTHER' OR note IS NOT NULL);

ALTER TABLE app_setting
  ADD CONSTRAINT ck_app_setting_key CHECK (key IN ('max_order_items', 'basket_capacity')),
  ADD CONSTRAINT ck_app_setting_value CHECK (value > 0);

-- 재고 원장 (설계 문서 4.3)
ALTER TABLE stock_ledger
  ADD CONSTRAINT ck_stock_ledger_entry_type CHECK (entry_type IN ('INBOUND', 'OUTBOUND', 'SHIP', 'CLOSE_ADJUST', 'SPOT_ADJUST')),
  ADD CONSTRAINT ck_stock_ledger_change_qty CHECK (change_qty <> 0),
  ADD CONSTRAINT ck_stock_ledger_qty_after CHECK (qty_after >= 0),
  ADD CONSTRAINT ck_stock_ledger_inbound CHECK (
    entry_type <> 'INBOUND' OR (change_qty > 0 AND reason IN ('REGULAR', 'ADDITIONAL', 'INPUT_ERROR', 'OTHER'))),
  ADD CONSTRAINT ck_stock_ledger_outbound CHECK (
    entry_type <> 'OUTBOUND' OR (change_qty < 0 AND reason IN ('DAMAGED', 'EXPIRED', 'INPUT_ERROR', 'OTHER'))),
  ADD CONSTRAINT ck_stock_ledger_reason_only_in_out CHECK (entry_type IN ('INBOUND', 'OUTBOUND') OR reason IS NULL),
  ADD CONSTRAINT ck_stock_ledger_ship CHECK (
    (entry_type = 'SHIP') = (trip_id IS NOT NULL) AND (entry_type <> 'SHIP' OR change_qty < 0)),
  ADD CONSTRAINT ck_stock_ledger_adjust_count CHECK ((entry_type IN ('CLOSE_ADJUST', 'SPOT_ADJUST')) = (count_id IS NOT NULL)),
  ADD CONSTRAINT ck_stock_ledger_input_error CHECK ((reason IS NOT DISTINCT FROM 'INPUT_ERROR') = (corrects_ledger_id IS NOT NULL)),
  ADD CONSTRAINT ck_stock_ledger_other_note CHECK (reason IS DISTINCT FROM 'OTHER' OR note IS NOT NULL);

-- 부분 UNIQUE
CREATE UNIQUE INDEX ux_stock_close_one_open ON stock_close ((true)) WHERE status = 'IN_PROGRESS';
CREATE UNIQUE INDEX ux_alert_low_stock_open ON alert (code, product_id) WHERE status = 'OPEN' AND code = 'LOW_STOCK';

-- 넘기는 중인 운반 건 (central 확정 답 전). 넘기기 루프가 접수 순서대로 고른다 (접수 API 설계 8.1).
CREATE UNIQUE INDEX ux_trip_dispatch_pending ON trip_dispatch (trip_id) WHERE central_acked_at IS NULL;
-- 넘기기 멈춤 알림은 운반 건마다 열린 것 하나 (접수 API 설계 6.5)
CREATE UNIQUE INDEX ux_alert_dispatch_stalled_open ON alert (code, trip_id) WHERE status = 'OPEN' AND code = 'DISPATCH_STALLED';

-- 생성 칼럼 (DBML에는 노트로만)
ALTER TABLE stock_count
  DROP COLUMN diff_qty,
  ADD COLUMN diff_qty integer GENERATED ALWAYS AS (counted_qty - (book_qty - in_transit_qty)) STORED;

-- updated_at: INSERT 때 기본값, UPDATE 때 트리거
ALTER TABLE product ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE orders ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE trip ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE stock_count ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE app_setting ALTER COLUMN updated_at SET DEFAULT now();

CREATE FUNCTION fn_set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := clock_timestamp();
  RETURN NEW;
END $$;

CREATE TRIGGER tg_product_updated_at BEFORE UPDATE ON product FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER tg_orders_updated_at BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER tg_trip_updated_at BEFORE UPDATE ON trip FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER tg_stock_count_updated_at BEFORE UPDATE ON stock_count FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER tg_app_setting_updated_at BEFORE UPDATE ON app_setting FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- == Task 3: v_in_flight ==

-- 진행 중 운반 건 (설계 문서 4.12): 결품 사전 확인 · 실사 여는 조건 · 수시 조정 막기가 같이 쓴다.
-- 시작됨 = 접수됨 · 취소됨이 아닌 운반 건이 같은 주문에 있음 (시작된 나뉜 주문의 남은 운반 건 포함)
CREATE VIEW v_in_flight AS
SELECT t.trip_id, t.order_id, t.status, t.robot_id, t.placed_at
FROM trip t
WHERE t.status IN ('ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'HELD')
   OR (t.status = 'RECEIVED' AND EXISTS (
        SELECT 1 FROM trip s
        WHERE s.order_id = t.order_id AND s.trip_id <> t.trip_id
          AND s.status NOT IN ('RECEIVED', 'CANCELLED')));

-- == Task 4: 재고 원장 트리거 ==

-- 재고는 원장 한 길로만 바뀐다 (설계 문서 5절 "재고 기록", OMS-08).
-- 잠금 키 7001: 원장 기록은 공유 잠금, 마감 시작 · 확정은 배타 잠금.

-- 같은 실사 행으로 두 번 조정하지 않는다.
CREATE UNIQUE INDEX ux_stock_ledger_count ON stock_ledger (count_id) WHERE count_id IS NOT NULL;

CREATE FUNCTION fn_ledger_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  open_close stock_close%ROWTYPE;
  cnt stock_count%ROWTYPE;
  cnt_close_status text;
  orig stock_ledger%ROWTYPE;
  last_confirmed timestamptz;
BEGIN
  PERFORM pg_advisory_xact_lock_shared(7001);

  -- 마감 중 막기: 반출 · 수시 조정은 마감 시작부터, 입고는 실사를 연 뒤부터
  SELECT * INTO open_close FROM stock_close WHERE status = 'IN_PROGRESS';
  IF FOUND THEN
    IF NEW.entry_type = 'OUTBOUND' THEN
      RAISE EXCEPTION '마감 중에는 반출할 수 없음' USING ERRCODE = 'check_violation';
    ELSIF NEW.entry_type = 'SPOT_ADJUST' THEN
      RAISE EXCEPTION '마감 중에는 수시 조정할 수 없음' USING ERRCODE = 'check_violation';
    ELSIF NEW.entry_type = 'INBOUND' AND open_close.counting_started_at IS NOT NULL THEN
      RAISE EXCEPTION '실사를 연 뒤에는 입고할 수 없음' USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  -- 수시 조정: 그 상품을 담는 진행 중 운반 건의 담은 수를 모르면 막음 (D-4 가)
  IF NEW.entry_type = 'SPOT_ADJUST' AND EXISTS (
       SELECT 1 FROM v_in_flight f JOIN trip_item ti ON ti.trip_id = f.trip_id
       WHERE ti.product_id = NEW.product_id AND f.placed_at IS NULL) THEN
    RAISE EXCEPTION '담은 수를 모르는 운반 건이 있어 수시 조정할 수 없음' USING ERRCODE = 'check_violation';
  END IF;

  -- 마감 조정은 확정 트랜잭션 안에서만: 배타 잠금을 먼저 잡아야 한다 (설계 문서 5절 "마감" 4).
  -- 잠금이 앞서야 조정과 확정 사이에 다른 원장 기록(늦은 출고 등)이 끼지 못하고, 교착도 생기지 않는다.
  IF NEW.entry_type = 'CLOSE_ADJUST' AND NOT EXISTS (
       SELECT 1 FROM pg_locks
       WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND mode = 'ExclusiveLock'
         AND granted AND classid = 0 AND objid = 7001 AND objsubid = 1) THEN
    RAISE EXCEPTION '마감 확정은 배타 잠금을 먼저 잡아야 함' USING ERRCODE = 'check_violation';
  END IF;

  -- 조정: 실사 행과 맞는지
  IF NEW.entry_type IN ('CLOSE_ADJUST', 'SPOT_ADJUST') THEN
    SELECT * INTO cnt FROM stock_count WHERE count_id = NEW.count_id;
    IF cnt.product_id <> NEW.product_id
       OR (NEW.entry_type = 'CLOSE_ADJUST') <> (cnt.close_id IS NOT NULL) THEN
      RAISE EXCEPTION '실사 행과 맞지 않음' USING ERRCODE = 'check_violation';
    END IF;
    IF cnt.close_id IS NOT NULL THEN
      SELECT status INTO cnt_close_status FROM stock_close WHERE close_id = cnt.close_id;
      IF cnt_close_status = 'CANCELLED' THEN
        RAISE EXCEPTION '취소된 마감의 실사로 조정할 수 없음' USING ERRCODE = 'check_violation';
      ELSIF cnt_close_status = 'CONFIRMED' THEN
        RAISE EXCEPTION '확정된 마감의 실사로 다시 조정할 수 없음' USING ERRCODE = 'check_violation';
      END IF;
    END IF;
    IF cnt.diff_qty IS DISTINCT FROM NEW.change_qty THEN
      RAISE EXCEPTION '실사 차이와 맞지 않음' USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  -- 입력 오류 바로잡기: 원래 종류의 반대 기록 (D-1 가)
  IF NEW.reason = 'INPUT_ERROR' THEN
    SELECT * INTO orig FROM stock_ledger WHERE ledger_id = NEW.corrects_ledger_id;
    IF orig.product_id <> NEW.product_id
       OR orig.entry_type NOT IN ('INBOUND', 'OUTBOUND')
       OR orig.entry_type = NEW.entry_type
       OR orig.change_qty <> -NEW.change_qty THEN
      RAISE EXCEPTION '바로잡을 기록과 맞지 않음' USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(finished_at) INTO last_confirmed FROM stock_close WHERE status = 'CONFIRMED';
    IF last_confirmed IS NOT NULL AND orig.created_at < last_confirmed THEN
      RAISE EXCEPTION '마감 이전 기록은 고칠 수 없음' USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  -- 재고 반영
  PERFORM set_config('ifp.via_ledger', 'on', true);
  UPDATE product SET stock_qty = stock_qty + NEW.change_qty
   WHERE product_id = NEW.product_id
   RETURNING stock_qty INTO NEW.qty_after;
  PERFORM set_config('ifp.via_ledger', 'off', true);
  RETURN NEW;
END $$;

CREATE TRIGGER tg_stock_ledger_apply BEFORE INSERT ON stock_ledger
  FOR EACH ROW EXECUTE FUNCTION fn_ledger_before_insert();

CREATE FUNCTION fn_ledger_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '원장 기록은 고치거나 지울 수 없음 (반대 기록으로 바로잡음)' USING ERRCODE = 'check_violation';
END $$;

CREATE TRIGGER tg_stock_ledger_immutable BEFORE UPDATE OR DELETE ON stock_ledger
  FOR EACH ROW EXECUTE FUNCTION fn_ledger_immutable();

CREATE FUNCTION fn_product_stock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.stock_qty <> 0 THEN
      RAISE EXCEPTION '재고는 원장으로만 바꿈' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.stock_qty IS DISTINCT FROM OLD.stock_qty
        AND current_setting('ifp.via_ledger', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION '재고는 원장으로만 바꿈' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER tg_product_stock_guard BEFORE INSERT OR UPDATE OF stock_qty ON product
  FOR EACH ROW EXECUTE FUNCTION fn_product_stock_guard();

-- == Task 5: 마감 검사 · 대기 주문 재확인 ==

-- 마감 (설계 문서 5절 "마감"): 시작은 배타 잠금, 실사는 보류 반영 + 진행 중 운반 건 0일 때만 열고,
-- 확정은 모든 상품 실사 · 차이 사유 · 진행 중 운반 건 0을 검사한다.
CREATE FUNCTION fn_close_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'IN_PROGRESS' THEN
      RAISE EXCEPTION '마감은 진행 중으로만 시작함' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(7001);
    RETURN NEW;
  END IF;

  -- 끝난 마감은 central 재개 반영 시각(release_acked_at)만 적을 수 있다.
  IF OLD.status <> 'IN_PROGRESS' AND
     (NEW.status, NEW.started_at, NEW.started_by, NEW.close_kind, NEW.hold_acked_at, NEW.counting_started_at,
      NEW.finished_at, NEW.finished_by, NEW.note)
     IS DISTINCT FROM
     (OLD.status, OLD.started_at, OLD.started_by, OLD.close_kind, OLD.hold_acked_at, OLD.counting_started_at,
      OLD.finished_at, OLD.finished_by, OLD.note) THEN
    RAISE EXCEPTION '끝난 마감은 바꿀 수 없음' USING ERRCODE = 'check_violation';
  END IF;

  IF OLD.counting_started_at IS NULL AND NEW.counting_started_at IS NOT NULL THEN
    IF NEW.hold_acked_at IS NULL THEN
      RAISE EXCEPTION '배정 보류 반영 전' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM v_in_flight) THEN
      RAISE EXCEPTION '진행 중 운반 건이 남음' USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF NEW.status = 'CONFIRMED' AND OLD.status <> 'CONFIRMED' THEN
    PERFORM pg_advisory_xact_lock(7001);
    IF NEW.counting_started_at IS NULL THEN
      RAISE EXCEPTION '실사를 열지 않은 마감' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM v_in_flight) THEN
      RAISE EXCEPTION '진행 중 운반 건이 남음' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
         SELECT 1 FROM product p
         LEFT JOIN stock_count c ON c.close_id = NEW.close_id AND c.product_id = p.product_id
         WHERE c.count_id IS NULL OR c.counted_qty IS NULL) THEN
      RAISE EXCEPTION '실사하지 않은 상품이 있음' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM stock_count WHERE close_id = NEW.close_id AND book_qty IS NULL) THEN
      RAISE EXCEPTION '장부 수량이 없는 행이 있음' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM stock_count WHERE close_id = NEW.close_id AND diff_qty <> 0 AND reason IS NULL) THEN
      RAISE EXCEPTION '사유 없는 차이가 있음' USING ERRCODE = 'check_violation';
    END IF;
    -- 조정을 모두 넣었고, 그 뒤 재고가 바뀌지 않았는지: 확정 순간의 재고 = 센 수량
    IF EXISTS (SELECT 1 FROM stock_count c JOIN product p ON p.product_id = c.product_id
               WHERE c.close_id = NEW.close_id AND p.stock_qty <> c.counted_qty) THEN
      RAISE EXCEPTION '재고가 실사 수량과 맞지 않음' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER tg_stock_close_guard BEFORE INSERT OR UPDATE ON stock_close
  FOR EACH ROW EXECUTE FUNCTION fn_close_guard();

-- 실사 행: 마감 행은 실사를 연 진행 중 마감에만, "시험 영향"은 운영 재개 마감에서만
CREATE FUNCTION fn_count_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  c stock_close%ROWTYPE;
BEGIN
  IF NEW.close_id IS NULL THEN
    IF NEW.reason = 'TEST_RUN' THEN
      RAISE EXCEPTION '시험 영향은 운영 재개 마감에서만' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO c FROM stock_close WHERE close_id = NEW.close_id;
  IF c.status <> 'IN_PROGRESS' OR c.counting_started_at IS NULL THEN
    RAISE EXCEPTION '실사를 열지 않은 마감' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.reason = 'TEST_RUN' AND c.close_kind <> 'OPS_RESUME' THEN
    RAISE EXCEPTION '시험 영향은 운영 재개 마감에서만' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER tg_stock_count_guard BEFORE INSERT OR UPDATE ON stock_count
  FOR EACH ROW EXECUTE FUNCTION fn_count_guard();

-- 대기 주문 재확인 (D-2 가): 시작하지 않은 주문을 접수 순으로 쌓아 비교, 넘는 주문은 주문 전체 거절하고 건너뜀.
-- 읽기만 한다. 거절(취소됨 + STOCK_RECHECK)은 웹 서비스가 같은 트랜잭션에서 쓴다.
CREATE FUNCTION fn_recheck_rejects() RETURNS TABLE (order_id text) LANGUAGE plpgsql AS $$
DECLARE
  o record;
  short boolean;
BEGIN
  CREATE TEMP TABLE IF NOT EXISTS _recheck_left (product_id bigint PRIMARY KEY, left_qty integer) ON COMMIT DROP;
  DELETE FROM _recheck_left;
  INSERT INTO _recheck_left SELECT p.product_id, p.stock_qty FROM product p;

  FOR o IN
    SELECT od.order_id FROM orders od
    WHERE NOT EXISTS (SELECT 1 FROM trip t WHERE t.order_id = od.order_id AND t.status <> 'RECEIVED')
      AND EXISTS (SELECT 1 FROM trip t WHERE t.order_id = od.order_id)
    ORDER BY od.created_at, od.order_id
  LOOP
    SELECT EXISTS (
      SELECT 1 FROM order_item oi JOIN _recheck_left l ON l.product_id = oi.product_id
      WHERE oi.order_id = o.order_id AND oi.qty_ordered > l.left_qty) INTO short;
    IF short THEN
      order_id := o.order_id;
      RETURN NEXT;
    ELSE
      UPDATE _recheck_left l SET left_qty = l.left_qty - oi.qty_ordered
        FROM order_item oi WHERE oi.order_id = o.order_id AND oi.product_id = l.product_id;
    END IF;
  END LOOP;
  DROP TABLE _recheck_left;
END $$;

-- == Task 6: NOTIFY · 처음 설정 값 ==

-- 화면 전달 (OMS-05, 통신 규칙 #32 · #37): 사건은 ws_event에 넣고 같은 트랜잭션에서 NOTIFY한다.
-- 채널 ifp_events. ws_event 한 줄마다 {"seq": 순번, "t": type, "id": order_changed면 order_id, trip_changed면 trip_id, 그 밖엔 data의 id, "status": data의 status}.
-- 웹 서비스가 직접 넣는 사건(관리 명령 결과 등)도 같은 INSERT 트리거로 알린다.
CREATE FUNCTION fn_ws_event_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('ifp_events', jsonb_strip_nulls(jsonb_build_object(
    'seq', NEW.seq,
    't', NEW.type,
    'id', CASE NEW.type
      WHEN 'order_changed' THEN NEW.data ->> 'order_id'
      WHEN 'trip_changed' THEN NEW.data ->> 'trip_id'
      ELSE NEW.data ->> 'id' END,
    'status', NEW.data ->> 'status'))::text);
  RETURN NULL;
END $$;

CREATE TRIGGER tg_ws_event_notify AFTER INSERT ON ws_event
  FOR EACH ROW EXECUTE FUNCTION fn_ws_event_notify();

-- 주문 · 운반 건 · 알림은 새로 생기거나 상태가 바뀔 때 ws_event 한 줄(사건), 마감은 바뀔 때마다 NOTIFY만(상태, 순번 없음).
-- 주문 · 운반 건 · 알림 트리거는 커밋 때 돈다(DEFERRED). 그 트랜잭션의 마지막 값을 싣도록 표를 다시 읽어 data를 만든다.
-- 상태가 바뀌었는지는 그 문장의 OLD · NEW로 비교한다. 같은 줄은 한 트랜잭션에서 한 번만 바뀐다고 보고 중복 제거는 없다
-- (접수 API 설계 8.2). 트리거 인자 = 키 칼럼 이름.
CREATE FUNCTION fn_notify_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  ev_type text;
  ev_data jsonb;
BEGIN
  IF TG_TABLE_NAME = 'stock_close' THEN
    PERFORM pg_notify('ifp_events', json_build_object(
      't', TG_TABLE_NAME,
      'id', to_jsonb(NEW) ->> TG_ARGV[0],
      'status', NEW.status)::text);
    RETURN NULL;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
    RETURN NULL;
  END IF;

  IF TG_TABLE_NAME = 'orders' THEN
    ev_type := 'order_changed';
    SELECT jsonb_build_object('order_id', o.order_id, 'status', o.status, 'account_id', o.account_id)
      INTO ev_data FROM orders o WHERE o.order_id = NEW.order_id;
  ELSIF TG_TABLE_NAME = 'trip' THEN
    ev_type := 'trip_changed';
    SELECT jsonb_build_object('trip_id', t.trip_id, 'order_id', t.order_id, 'status', t.status, 'robot_id', t.robot_id,
             'items', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'product_id', ti.product_id, 'qty_ordered', ti.qty_ordered,
                'qty_placed', ti.qty_placed, 'qty_missing', ti.qty_missing) ORDER BY ti.product_id)
              FROM trip_item ti WHERE ti.trip_id = t.trip_id), '[]'::jsonb))
      INTO ev_data FROM trip t WHERE t.trip_id = NEW.trip_id;
  ELSE
    ev_type := TG_TABLE_NAME;
    SELECT jsonb_build_object('id', a.alert_id::text, 'status', a.status)
      INTO ev_data FROM alert a WHERE a.alert_id = NEW.alert_id;
  END IF;

  -- 같은 트랜잭션에서 지워진 줄이면 다시 읽을 수 없으므로 사건을 남기지 않는다.
  IF ev_data IS NOT NULL THEN
    INSERT INTO ws_event (type, data) VALUES (ev_type, ev_data);
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER tg_orders_notify AFTER INSERT OR UPDATE ON orders
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fn_notify_change('order_id');
CREATE CONSTRAINT TRIGGER tg_trip_notify AFTER INSERT OR UPDATE ON trip
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fn_notify_change('trip_id');
CREATE CONSTRAINT TRIGGER tg_alert_notify AFTER INSERT OR UPDATE ON alert
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fn_notify_change('alert_id');
CREATE TRIGGER tg_stock_close_notify AFTER INSERT OR UPDATE ON stock_close
  FOR EACH ROW EXECUTE FUNCTION fn_notify_change('close_id');

-- 운영 설정 처음 값 (D-6): 바구니 하나 4개 (fleet-v1과 같게), 주문 한 건 8개 (바구니 2개, 11-19 사용자 결정)
INSERT INTO app_setting (key, value) VALUES ('basket_capacity', 4), ('max_order_items', 8);
