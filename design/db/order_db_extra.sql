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
  ADD CONSTRAINT ck_trip_seq CHECK (seq > 0);

ALTER TABLE trip_item ADD CONSTRAINT ck_trip_item_qty CHECK (
  qty_ordered > 0 AND qty_placed >= 0 AND qty_missing >= 0 AND qty_placed + qty_missing <= qty_ordered);

ALTER TABLE status_history
  ADD CONSTRAINT ck_status_history_to_status CHECK (to_status IN (
    'RECEIVED', 'ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'SHIPPED', 'PARTIAL', 'HELD', 'CANCELLED')),
  ADD CONSTRAINT ck_status_history_from_status CHECK (from_status IN (
    'RECEIVED', 'ASSIGNED', 'FILLING', 'FILLING_PAUSE', 'TRANSPORTING', 'SHIPPED', 'PARTIAL', 'HELD', 'CANCELLED')),
  ADD CONSTRAINT ck_status_history_reason CHECK (reason IN (
    'OUT_OF_STOCK', 'PICK_FAILED', 'VISION_MISS', 'PODE_FAULT', 'PHODY_FAULT', 'USER_CANCEL', 'NOTHING_TO_FILL',
    'STOCK_RECHECK', 'FORCED_SHIP', 'FORCED_PARTIAL', 'REASSIGN', 'RETURNED_TO_SHELF')),
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
  ADD CONSTRAINT ck_admin_action_result CHECK (result IN ('SENT', 'ACCEPTED', 'REJECTED', 'LOCAL'));

ALTER TABLE stock_close
  ADD CONSTRAINT ck_stock_close_status CHECK (status IN ('IN_PROGRESS', 'CONFIRMED', 'CANCELLED')),
  ADD CONSTRAINT ck_stock_close_kind CHECK (close_kind IN ('MANUAL', 'TEST_ENTRY', 'OPS_RESUME')),
  ADD CONSTRAINT ck_stock_close_finished CHECK ((status = 'IN_PROGRESS') = (finished_at IS NULL)),
  ADD CONSTRAINT ck_stock_close_starter CHECK ((close_kind = 'OPS_RESUME') = (started_by IS NULL)),
  ADD CONSTRAINT ck_stock_close_ops_resume_no_cancel CHECK (close_kind <> 'OPS_RESUME' OR status <> 'CANCELLED');

ALTER TABLE stock_count
  ADD CONSTRAINT ck_stock_count_qty CHECK (counted_qty >= 0 AND book_qty >= 0 AND in_transit_qty >= 0),
  ADD CONSTRAINT ck_stock_count_reason CHECK (reason IN (
    'COUNT_DIFF', 'VISION_MISS', 'DAMAGE_FOUND', 'LOST', 'INPUT_ERROR', 'TEST_RUN', 'OTHER')),
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

-- == Task 4: 재고 원장 트리거 ==

-- == Task 5: 마감 검사 · 대기 주문 재확인 ==

-- == Task 6: NOTIFY · 처음 설정 값 ==
