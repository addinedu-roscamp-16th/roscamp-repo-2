"""고객 스토리보드 검사기 시험. run: python3 -m unittest discover -s design/storyboard_tools/tests"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import sb_core  # noqa: E402
import user_check  # noqa: E402
import user_screen  # noqa: E402


def make(body, notice=None, extra=None, kind='shop'):
    """화면 하나짜리 탭을 만들어 mxfile 루트를 돌려준다. extra(t, x, y)로 규칙 위반 셀을 더한다."""
    t = sb_core.Tab('t', '시험')
    x, y = 40, 100
    user_screen.screen(t, x, y, {'kind': kind, 'notice': notice, 'body': body})
    if extra:
        extra(t, x, y)
    root = ET.Element('mxfile')
    root.append(t.finish())
    return root


CLEAN = [('search', '상품 검색 [______]'),
         ('product', '콜라', 12, 1, ''),
         ('order_btn', ''),
         ('order', '1043 콜라 1', '준비 중', ''),
         ('order', '1042 콜라 1', '접수됨', '')]


class UserCheckTest(unittest.TestCase):
    def test_clean_tab_passes(self):
        self.assertEqual(user_check.check_screen_rules(make(CLEAN, notice='주문 1042: 콜라 1개가 없어 일부만 출고됐습니다')), [])

    def test_cancel_only_on_received(self):
        def bad(t, x, y):
            # 준비 중 줄(첫 주문 줄) 높이에 [취소]를 억지로 붙임
            st = [c for c in t.r.iter('mxCell') if '_st' in c.get('id', '') and c.get('value') == '준비 중'][0]
            gy = float(st.find('mxGeometry').get('y'))
            t.v('취소', sb_core.S_BTN, x + 470, gy, 40, 16, cid=t.nid('cancel'))
        fails = user_check.check_screen_rules(make(CLEAN, extra=bad))
        self.assertTrue(any('[취소]' in f for f in fails), fails)

    def test_cancel_drawn_only_for_received(self):
        root = make(CLEAN)
        cancels = [c for c in root.iter('mxCell') if '_cancel' in c.get('id', '')]
        self.assertEqual(len(cancels), 1)

    def test_forbidden_word_in_screen(self):
        fails = user_check.check_screen_rules(make(CLEAN, notice='주문 1042 결품으로 일부 출고'))
        self.assertTrue(any('결품' in f for f in fails), fails)

    def test_unknown_status(self):
        fails = user_check.check_screen_rules(make(CLEAN + [('order', '1045 자유시간 Mini 1', '배송 중', '')]))
        self.assertTrue(any('배송 중' in f for f in fails), fails)

    def test_cells_inside_screen(self):
        def bad(t, x, y):
            t.v('넘침', sb_core.S_ROW, x + 400, y + 200, 200, 20)
        fails = user_check.check_screen_rules(make(CLEAN, extra=bad))
        self.assertTrue(any('화면 밖' in f for f in fails), fails)

    def test_many_orders_with_notice_fit(self):
        body = CLEAN + [('order', f'{1045 + i} 자유시간 Mini 1', '출고 완료', '') for i in range(2)]
        self.assertEqual(user_check.check_screen_rules(make(body, notice='알림 한 줄')), [])

    def test_one_mark_per_screen(self):
        body = [('product', '콜라', 12, 1, '①'), ('order_btn', '①'), ('order', '1044 콜라 1', '접수됨', '')]
        fails = user_check.check_screen_rules(make(body))
        self.assertTrue(any('①' in f for f in fails), fails)

    def test_hi_row_has_no_mark(self):
        root = make([('order', '1044 콜라 1', '접수됨', 'hi')])
        marks = [c for c in root.iter('mxCell') if (c.get('value') or '').startswith('①')]
        self.assertEqual(marks, [])

    def test_qty_over_stock(self):
        fails = user_check.check_screen_rules(make([('product', '콜라', 2, 3, '')]))
        self.assertTrue(any('재고보다' in f for f in fails), fails)

    def test_order_max(self):
        """한 주문 최대 수량은 운영 설정(OMS-20) ORDER_MAX. 바구니 용량을 넘어도 나뉘어 처리되므로 거절 아님 (OMS-19)."""
        m = user_check.ORDER_MAX
        self.assertGreater(m, user_check.BASKET)
        fails = user_check.check_screen_rules(make([('product', '콜라', 99, m - 2, ''), ('product', '우유', 99, 3, '')]))
        self.assertTrue(any(f'{m}개' in f for f in fails), fails)
        fails = user_check.check_screen_rules(make([('order', f'1044 콜라 {m - 2} · 우유 3', '접수됨', '')]))
        self.assertTrue(any(f'{m}개' in f for f in fails), fails)

    def test_order_over_basket_is_allowed(self):
        body = [('product', '콜라', 9, 4, ''), ('product', '우유', 9, 2, ''), ('order_btn', ''),
                ('order', '1044 콜라 4 · 우유 2', '접수됨', '')]
        self.assertEqual(user_check.check_screen_rules(make(body)), [])

    def test_notice_order_visible(self):
        fails = user_check.check_screen_rules(make(CLEAN, notice='주문 1099: 콜라가 없어 주문이 취소됐습니다'))
        self.assertTrue(any('1099' in f for f in fails), fails)

    def test_edge_label_needs_state_or_r(self):
        t = sb_core.Tab('t', '시험')
        a = t.v('a', sb_core.S_CARD, 0, 0, 10, 10)
        b = t.v('b', sb_core.S_CARD, 50, 0, 10, 10)
        t.e(a, b, '[그 사이에]')
        t.e(a, b, '[배정됨]')
        t.e(a, b, '[거절 (R-08)]')
        t.e(a, b, '')
        root = ET.Element('mxfile')
        root.append(t.finish())
        fails = user_check.check_edge_labels(root)
        self.assertEqual(len(fails), 1, fails)

    def test_customer_line_matches_mark(self):
        spec = {'id': 't', 'name': '시험', 'title': 't', 'scenes': [
            {'n': 1, 'pos': (0, 0), 'title': 'x', 'screen': {'kind': 'shop', 'body': [('order_btn', '①')]},
             'lines': ['고객: ① [취소]', '시스템: x', '근거: x']}]}
        root = ET.Element('mxfile')
        root.append(sb_core.build_tab(spec, 'v', user_screen.screen))
        fails = user_check.check_marks_vs_lines(root)
        self.assertTrue(any('취소' in f for f in fails), fails)

    def test_product_has_image(self):
        root = make(CLEAN)
        imgs = [c for c in root.iter('mxCell') if '_img' in c.get('id', '')]
        prods = [c for c in root.iter('mxCell') if '_prod' in c.get('id', '')]
        self.assertEqual(len(imgs), len(prods))
        self.assertEqual(user_check.check_screen_rules(make(CLEAN)), [])

    def test_product_without_image_fails(self):
        def drop(t, x, y):
            for c in [c for c in t.r.iter('mxCell') if '_img' in c.get('id', '')]:
                t.r.remove(c)
        fails = user_check.check_screen_rules(make(CLEAN, extra=drop))
        self.assertTrue(any('이미지' in f for f in fails), fails)

    # ---- 주문 상세 패널 (내 주문 줄을 누르면 오른쪽에서 열림)
    def _detail(self, row_status='준비 중', status='준비 중', steps=None, items='미니멘토스 레인보우 2 · 과즙팡 비타민 마이구미 1'):
        steps = steps or [('10:02', '접수됨'), ('10:03', '준비 중'), (None, '출고 중'), (None, '출고 완료')]
        return [('order', '1101 미니멘토스 레인보우 2 · 과즙팡 비타민 마이구미 1', row_status, 'hi'),
                ('detail', '1101', items, status, steps, '')]

    def test_detail_panel_clean(self):
        root = make(self._detail())
        self.assertEqual(user_check.check_screen_rules(root), [])
        tl = [c for c in root.iter('mxCell') if '_tl' in c.get('id', '')]
        self.assertEqual(len(tl), 4)

    def test_detail_status_is_last_timed_step(self):
        fails = user_check.check_screen_rules(make(self._detail(row_status='출고 중', status='출고 중')))
        self.assertTrue(any('마지막' in f for f in fails), fails)

    def test_detail_step_word_unknown(self):
        steps = [('10:02', '접수됨'), ('10:03', '배송 중')]
        fails = user_check.check_screen_rules(make(self._detail(steps=steps, status='접수됨', row_status='접수됨')))
        self.assertTrue(any('배송 중' in f for f in fails), fails)

    def test_detail_matches_order_row(self):
        fails = user_check.check_screen_rules(make(self._detail(row_status='접수됨')))
        self.assertTrue(any('내 주문 줄' in f for f in fails), fails)
        fails = user_check.check_screen_rules(make(self._detail(items='미니멘토스 레인보우 1')))
        self.assertTrue(any('상품' in f for f in fails), fails)

    def test_detail_times_in_order(self):
        steps = [('10:05', '접수됨'), ('10:03', '준비 중')]
        fails = user_check.check_screen_rules(make(self._detail(steps=steps)))
        self.assertTrue(any('시간' in f for f in fails), fails)

    def test_detail_cancel_only_when_received(self):
        steps = [('10:02', '접수됨'), (None, '준비 중')]
        root = make(self._detail(row_status='접수됨', status='접수됨', steps=steps))
        self.assertEqual(user_check.check_screen_rules(root), [])
        self.assertEqual(len([c for c in root.iter('mxCell') if '_cancel' in c.get('id', '')]), 2)  # 줄 + 패널

    def test_detail_forbidden_word(self):
        steps = [('10:02', '접수됨'), ('10:03', '준비 중', '결품 처리 중')]
        fails = user_check.check_screen_rules(make(self._detail(steps=steps)))
        self.assertTrue(any('결품' in f for f in fails), fails)

    def test_open_mark_on_order_number(self):
        root = make([('order', '1101 미니멘토스 레인보우 1', '준비 중', 'open')],
                    notice='주문 1101: 알림')
        marks = [user_check.plain(c.get('value')) for c in root.iter('mxCell') if user_check.plain(c.get('value')).startswith('①')]
        self.assertEqual(marks, ['① 1101'])
        self.assertEqual(user_check.check_screen_rules(root), [])


    # ---- 나뉜 주문 (OMS-19): 상세에만 바구니별 진행
    def _split(self, status='준비 중', basket='바구니 1/2 출고', steps=None):
        steps = steps or [('10:20', '접수됨'), ('10:21', '준비 중'), (None, '출고 중'), (None, '출고 완료')]
        items = '미니멘토스 레인보우 4 · 과즙팡 비타민 마이구미 2'
        return [('order', '1102 ' + items, status, 'hi'), ('detail', '1102', items, status, steps, '', basket)]

    def test_split_order_detail_clean(self):
        root = make(self._split())
        self.assertEqual(user_check.check_screen_rules(root), [])
        lines = [user_check.plain(c.get('value')) for c in root.iter('mxCell') if '_basket' in c.get('id', '')]
        self.assertEqual(lines, ['바구니 1/2 출고'])

    def test_split_order_never_back_to_received(self):
        steps = [('10:20', '접수됨'), (None, '준비 중')]
        fails = user_check.check_screen_rules(make(self._split(status='접수됨', steps=steps)))
        self.assertTrue(any('바구니' in f for f in fails), fails)

    def test_split_basket_line_format(self):
        fails = user_check.check_screen_rules(make(self._split(basket='바구니 3/2 출고')))
        self.assertTrue(any('바구니' in f for f in fails), fails)

    # ---- 점검 중 화면 (COM-06 · R-20)
    def test_maint_screen_clean(self):
        root = make([], kind='maint')
        self.assertEqual(user_check.check_screen_rules(root), [])
        text = ' '.join(user_check.plain(c.get('value')) for c in root.iter('mxCell'))
        self.assertIn('지금은 시스템 점검 중입니다', text)
        for word in ('주문하기', '내 주문', '아이디', '⚠'):
            self.assertNotIn(word, text)

    def test_maint_screen_has_no_shop_parts(self):
        def bad(t, x, y):
            t.v('콜라<br>재고 3', sb_core.S_TXT, x + 40, y + 60, 100, 30, cid=t.nid('prod'))
            t.v('<b>1042</b>', sb_core.S_TXT, x + 300, y + 60, 60, 16, cid=t.nid('ord'))
            t.v('주문하기', sb_core.S_BTN, x + 200, y + 200, 60, 16)
        fails = user_check.check_screen_rules(make([], kind='maint', extra=bad))
        self.assertGreaterEqual(len([f for f in fails if '점검 중' in f]), 3, fails)

    def test_maint_screen_needs_message(self):
        def drop(t, x, y):
            for c in [c for c in t.r.iter('mxCell') if '_maint' in c.get('id', '')]:
                c.set('value', '점검')
        fails = user_check.check_screen_rules(make([], kind='maint', extra=drop))
        self.assertTrue(any('문구' in f for f in fails), fails)

    def test_test_mode_word_forbidden(self):
        fails = user_check.check_screen_rules(make(CLEAN, notice='시험 모드라 주문을 받지 않습니다'))
        self.assertTrue(any('시험' in f for f in fails), fails)

        def bad(t, x, y):
            t.v('시험 모드', sb_core.S_TXT, x + 40, y + 200, 100, 16)
        fails = user_check.check_screen_rules(make([], kind='maint', extra=bad))
        self.assertTrue(any('시험' in f for f in fails), fails)


if __name__ == '__main__':
    unittest.main()
