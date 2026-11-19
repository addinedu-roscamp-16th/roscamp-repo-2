"""고객 스토리보드 검사기. usage: python3 user_check.py <사용자_스토리보드.drawio>. 실패 수만큼 종료 코드."""
import html
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sb_core import S_SCREEN  # noqa: E402
from user_screen import STATUS  # noqa: E402

TAB_NAMES = ['0. 화면 구성', '1. 평상시 주문', '2. 재고 초과 거절', '3. 주문 취소', '4. 결품', '5. 마감 뒤 거절']
VERSION = 'v1.0 · 2026-11-19'
COVER = ['SCR-01', 'SCR-02', 'SCR-03', 'SCR-04', 'SCR-05', 'SCR-09', 'OMS-03', 'OMS-10', 'OMS-13', 'CEN-13']
FORBIDDEN = ['장애', '보류', '일시 정지', '일시 중지', '마감', '결품', '배정', '담는 중', '운반 중', 'Pode', 'Phody', 'Phony',
             'RECEIVED', 'ASSIGNED', 'FILLING', 'HELD', 'SHIPPED', 'PARTIAL', 'CANCELLED', 'REJECT']
REQUIRED_TEXT = {'0. 화면 구성': STATUS,
                 '3. 주문 취소': ['이미 준비가 시작되어 취소할 수 없습니다']}
DESC_HEADS = ('고객: ', '시스템: ', '근거: ')
BASKET = 4  # 주문 한 건 최대 수량 (바구니 용량, 주문 DB 설계 4.5)
# 다이어그램 2. 주문의 상태 이름
DIAGRAM_STATES = ['접수됨', '배정됨', '담는 중', '담기 일시 중지', '운반 중', '출고됨', '일부 출고됨', '주문 처리 보류', '취소됨']


def plain(v):
    return html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<br\s*/?>', '\n', v or ''))).strip()


def geo(c):
    g = c.find('mxGeometry')
    return tuple(float(g.get(k) or 0) for k in ('x', 'y', 'width', 'height'))


def _screens(d):
    cells = [c for c in d.iter('mxCell') if c.get('vertex') == '1']
    for s in cells:
        if s.get('style') == S_SCREEN:
            sx, sy, sw, sh = geo(s)
            inside = [c for c in cells if c is not s and sx - 0.5 <= geo(c)[0] < sx + sw and sy - 0.5 <= geo(c)[1] < sy + sh]
            yield (sx, sy, sw, sh), inside


def check_screen_rules(root):
    """화면 안 규칙: 금지어 · 상태 말 · [취소] 위치 · 화면 밖 넘침."""
    fails = []
    for d in root.iter('diagram'):
        tab = d.get('name')
        for (sx, sy, sw, sh), inside in _screens(d):
            where = f'{tab} 화면({int(sx)},{int(sy)})'
            sts = [c for c in inside if _is_status(c)]
            for c in inside:
                x, y, w, h = geo(c)
                txt = plain(c.get('value'))
                if x + w > sx + sw + 1 or y + h > sy + sh + 1:
                    fails.append(f'{where}: 화면 밖으로 넘침 "{txt[:20]}"')
                for wd in FORBIDDEN:
                    if wd in txt:
                        fails.append(f'{where}: 고객 화면에 내부 말 "{wd}" — "{txt[:30]}"')
                if _is_status(c) and txt not in STATUS:
                    fails.append(f'{where}: 대응표에 없는 상태 말 "{txt}"')
                is_cancel = txt.replace('①', '').strip() == '취소'
                if is_cancel or '_cancel' in c.get('id', ''):
                    ok = '_cancel' in c.get('id', '') and any(
                        plain(s.get('value')) == '접수됨' and geo(s)[1] < y + h and y < geo(s)[1] + geo(s)[3] for s in sts)
                    if not ok:
                        fails.append(f'{where}: [취소]가 접수됨 줄이 아닌 곳에 있음')
            marks = [plain(c.get('value')) for c in inside if plain(c.get('value')).startswith('①')]
            if len(marks) > 1:
                fails.append(f'{where}: ① 누르는 곳이 {len(marks)}개 {marks}')
            fails += _check_numbers(where, inside)
            fails += _check_detail(where, inside)
    return fails


def _is_status(c):
    """내 주문 줄의 상태(_st)와 상세 패널의 상태(_pst)."""
    cid = c.get('id', '')
    return '_st' in cid or '_pst' in cid


def _order_no(c):
    return re.sub(r'[①\s]', '', plain(c.get('value')))


def _check_detail(where, inside):
    """상세 패널: 진행 단계 말은 상태 말 7개, 시각은 차례대로, 상태 = 시각이 있는 마지막 단계,
    같은 번호의 내 주문 줄과 상태 · 상품이 같음."""
    fails = []
    heads = [c for c in inside if '_dno' in c.get('id', '')]
    if not heads:
        return fails
    no = re.search(r'\d+', plain(heads[0].get('value'))).group(0)
    pst = [c for c in inside if '_pst' in c.get('id', '')]
    status = plain(pst[0].get('value')) if pst else ''
    timed, prev = [], ''
    for c in sorted((c for c in inside if '_tl' in c.get('id', '')), key=lambda c: geo(c)[1]):
        txt = plain(c.get('value'))
        m = re.match(r'(\d\d:\d\d|--:--)\s+(.*)', txt)
        word = next((w for w in sorted(STATUS, key=len, reverse=True) if m and m.group(2).startswith(w)), None)
        if not word:
            fails.append(f'{where}: 진행 단계 "{txt}"의 말이 상태 말이 아님')
            continue
        if m.group(1) != '--:--':
            if m.group(1) < prev:
                fails.append(f'{where}: 진행 시간이 거꾸로 "{txt}"')
            prev = m.group(1)
            timed.append(word)
    if timed and timed[-1] != status:
        fails.append(f'{where}: 상세 상태 "{status}" ≠ 진행의 마지막 단계 "{timed[-1]}"')
    for o in (c for c in inside if '_ord' in c.get('id', '') and _order_no(c) == no):
        row = [c for c in inside if '_st' in c.get('id', '') and _overlap(c, o)]
        if row and plain(row[0].get('value')) != status:
            fails.append(f'{where}: 상세 상태 "{status}" ≠ 내 주문 줄 "{plain(row[0].get("value"))}"')
        items = [c for c in inside if '_items' in c.get('id', '') and 0 < geo(c)[1] - geo(o)[1] < 30]
        ditems = [c for c in inside if '_ditems' in c.get('id', '')]
        if items and ditems and plain(items[0].get('value')) != ' · '.join(plain(ditems[0].get('value')).split('\n')):
            fails.append(f'{where}: 상세의 상품이 내 주문 줄과 다름')
    return fails


def _overlap(a, b):
    return geo(a)[1] < geo(b)[1] + geo(b)[3] and geo(b)[1] < geo(a)[1] + geo(a)[3]


def _check_numbers(where, inside):
    """상품 줄마다 이미지 칸, 고른 수량 ≤ 재고, 한 주문 합 ≤ 바구니 용량, 알림의 주문 번호가 내 주문 목록에 있음."""
    fails = []
    prods = [c for c in inside if '_prod' in c.get('id', '')]
    qtys = [c for c in inside if '_qty' in c.get('id', '')]
    imgs = [c for c in inside if '_img' in c.get('id', '')]
    for p in prods:
        if not any(_overlap(p, i) for i in imgs):
            fails.append(f'{where}: 상품 줄에 이미지 칸이 없음 "{plain(p.get("value"))[:20]}"')
    total = 0
    for p in prods:
        pt = plain(p.get('value'))
        m = re.search(r'재고 (\d+)', pt)
        stock = int(m.group(1)) if m else 0
        for q in qtys:
            if _overlap(p, q):
                n = int(re.search(r'- (\d+) \+', plain(q.get('value'))).group(1))
                total += n
                if n > stock:
                    fails.append(f'{where}: 고른 수량 {n}이 재고보다 많음 "{pt}"')
    if total > BASKET:
        fails.append(f'{where}: 고른 수량 합 {total} — 한 주문 최대 {BASKET}개')
    nums = {_order_no(o) for o in inside if '_ord' in o.get('id', '')}
    for it in (c for c in inside if '_items' in c.get('id', '')):
        n = sum(int(k) for k in re.findall(r'\d+', plain(it.get('value'))))
        if n > BASKET:
            fails.append(f'{where}: 주문 "{plain(it.get("value"))}" 수량 합 {n} — 한 주문 최대 {BASKET}개')
    for nt in (c for c in inside if '_notice' in c.get('id', '')):
        for no in re.findall(r'주문 (\d+)', plain(nt.get('value'))):
            if no not in nums:
                fails.append(f'{where}: 알림의 주문 {no}가 내 주문 목록에 없음')
    return fails


def check_edge_labels(root):
    """화살표 라벨은 다이어그램 2. 주문의 상태 이름이나 R-xx(미반영 수정 요청)를 담는다."""
    fails = []
    for d in root.iter('diagram'):
        for c in d.iter('mxCell'):
            lab = plain(c.get('value'))
            if c.get('edge') == '1' and lab:
                if not any(s in lab for s in DIAGRAM_STATES) and not re.search(r'R-\d+', lab):
                    fails.append(f'{d.get("name")}: 화살표 라벨 "{lab}"에 2. 주문의 상태 이름이나 R-xx가 없음')
    return fails


def check_marks_vs_lines(root):
    """고객 줄의 '① [X]'와 그 장면 화면의 ① 버튼 이름이 같은지."""
    fails = []
    for d in root.iter('diagram'):
        cells = [c for c in d.iter('mxCell') if c.get('vertex') == '1']
        byid = {c.get('id'): c for c in cells}
        for cid, c in byid.items():
            card = byid.get('sc' + cid[4:]) if (cid or '').startswith('desc') else None
            if card is None:
                continue
            cx, cy, cw, ch = geo(card)
            marks = {plain(m.get('value')).lstrip('①').strip() for m in cells
                     if plain(m.get('value')).startswith('①') and cx <= geo(m)[0] < cx + cw and cy <= geo(m)[1] < cy + ch}
            line = plain(c.get('value')).split('\n')[0]
            want = set(re.findall(r'① \[([^\]]+)\]', line))
            if want != marks:
                fails.append(f'{d.get("name")} 장면 {cid[4:]}: 고객 줄의 ① {sorted(want)} ≠ 화면의 ① {sorted(marks)}')
    return fails


def check_doc(root):
    fails = []
    tabs = list(root.iter('diagram'))
    names = [d.get('name') for d in tabs]
    if names != TAB_NAMES:
        fails.append(f'탭 이름 다름: {names}')
    alltext = []
    for d in tabs:
        tab = d.get('name')
        vals = {c.get('id'): plain(c.get('value')) for c in d.iter('mxCell')}
        if vals.get('version-label') != VERSION:
            fails.append(f'{tab}: 버전 라벨 "{vals.get("version-label")}"')
        text = '\n'.join(v for v in vals.values() if v)
        alltext.append(text)
        for k, v in vals.items():
            if k and k.startswith('desc'):
                lines = v.split('\n')
                if len(lines) != 3 or not all(ln.startswith(hd) for ln, hd in zip(lines, DESC_HEADS)):
                    fails.append(f'{tab} {k}: 설명 세 줄(고객 · 시스템 · 근거) 아님')
        for need in REQUIRED_TEXT.get(tab, []):
            if need not in text:
                fails.append(f'{tab}: 필수 글 없음 "{need}"')
    basis = '\n'.join(ln for t in alltext for ln in t.split('\n') if ln.startswith('근거: '))
    for fid in COVER:
        if fid not in basis:
            fails.append(f'기능 {fid}가 어느 근거 줄에도 없음')
    return fails


def main(path):
    root = ET.parse(path).getroot()
    fails = check_doc(root) + check_screen_rules(root) + check_edge_labels(root) + check_marks_vs_lines(root)
    for f in fails:
        print('FAIL', f)
    print('OK' if not fails else f'{len(fails)} fails')
    return len(fails)


if __name__ == '__main__':
    sys.exit(min(main(sys.argv[1]), 100))
