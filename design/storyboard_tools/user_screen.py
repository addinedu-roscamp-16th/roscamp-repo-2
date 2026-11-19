"""고객 주문 화면 그리기. 위쪽 줄 · 알림 줄 · 왼쪽 상품 · 오른쪽 내 주문.

screen dict:
  kind: 'login' | 'shop' | 'maint'(점검 중: 가운데 문구 하나뿐, 로그인 전 · 뒤 같음 — 설계 8절)
  notice: 위쪽 알림 줄 글 (없으면 None)
  mark: 'notice'면 알림 줄 [확인]에 ①
  body 항목:
    ('search', 글)
    ('product', 이름, 재고, 수량, 표시)   왼쪽에 이미지 칸. 표시 '' | '①' | 'soldout'(품절: 수량 버튼 꺼짐)
    ('order_btn', 표시)                   표시 '' | '①' | 'off'
    ('inline', 글)                        [주문하기] 아래 안내
    ('order', 줄 글, 상태, 표시)           상태가 '접수됨'일 때만 [취소]를 그림. 표시 'hi'면 줄만 노랑,
                                          '①'이면 줄 노랑 + [취소]에 ①, 'open'이면 줄 노랑 + 주문 번호에 ① (누르면 상세)
                                          (줄 글은 '번호 상품 수량 · …')
    ('detail', 번호, 상품 글, 상태, 진행, 표시[, 바구니])
                                          주문 상세 패널: 오른쪽 내 주문 자리를 덮고 열림. 진행은 [(시각|None, 상태 말, 덧붙임?)],
                                          시각이 None이면 아직 안 온 단계(회색). 상태가 '접수됨'이면 [취소].
                                          표시 '' | 'close'(① ×) | 'cancel'(① 취소)
                                          바구니: 나뉜 주문(OMS-19)의 바구니별 진행 글 (예 '바구니 1/2 출고'). 상태 줄 아래 따로 한 줄
    ('dialog', 글, 버튼들)
"""
from sb_core import (BAR_H, S_ALERT, S_BAR, S_BTN, S_BTN_OFF, S_BTN_ON, S_DIALOG, S_DIM, S_PANEL, S_PANEL_HEAD, S_ROW,
                     S_ROW_HI, S_SCREEN, S_STEP_WAIT, S_TXT, S_TXTC, SCR_H, SCR_W, buttons, login_screen)

STATUS = ['접수됨', '준비 중', '출고 중', '출고 완료', '일부 출고', '취소됨', '거절됨']
S_INLINE = S_TXT.replace('fontColor=#333333', 'fontColor=#b20000')
S_STATUS = S_TXTC + 'fontStyle=1;'
LEFT_W = 250
ROW_H = 38  # 두 줄 (상품: 이미지 · 이름 / 재고, 주문: 번호 · 상태 / 상품 수량)
IMG = 30
S_IMG = 'rounded=0;html=1;dashed=1;fillColor=#ffffff;strokeColor=#999999;fontSize=7;fontColor=#999999;whiteSpace=wrap;'
S_TXT_TOP = S_TXT.replace('verticalAlign=middle', 'verticalAlign=top')
S_MAINT = 'rounded=1;arcSize=6;html=1;fillColor=#ffffff;strokeColor=#999999;fontSize=12;align=center;verticalAlign=middle;whiteSpace=wrap;fontColor=#333333;'
MAINT_MSG = '지금은 시스템 점검 중입니다.<br>점검이 끝나면 다시 주문할 수 있습니다.'


def _btn(t, label, right, y, cid=None):
    """버튼 하나를 오른쪽 끝(right)에 맞춰 그림. '~'로 시작하면 꺼짐, ①로 시작하면 강조."""
    off = label.startswith('~')
    label = label.lstrip('~')
    w = 12 + 8.5 * len(label)
    x = right - w
    style = S_BTN_OFF if off else (S_BTN_ON if label[:1] in '①②③' else S_BTN)
    t.v(label, style, x, y, w, 16, cid=cid)
    return w


def screen(t, x, y, s):
    W, H = SCR_W, SCR_H
    t.v('', S_SCREEN, x, y, W, H)
    if s['kind'] == 'login':
        login_screen(t, x, y, s.get('body', ()))
        return
    if s['kind'] == 'maint':
        # 점검 중: 위쪽 줄은 이름만 (로그인 전 · 뒤 같은 화면), 가운데 문구 하나
        t.v('<b>IFP 주문</b>', S_BAR, x, y, W, BAR_H)
        t.v(MAINT_MSG, S_MAINT, x + 80, y + 95, W - 160, 70, cid=t.nid('maint'))
        return
    t.v('<b>IFP 주문</b>', S_BAR, x, y, W, BAR_H)
    t.v('고객1', S_TXT, x + W - 150, y + 2, 50, BAR_H - 4)
    buttons(t, x, y + 9, ['로그아웃'], x + W - 6)
    top = y + BAR_H + 6
    if s.get('notice'):
        t.v('⚠ ' + s['notice'], S_ALERT.replace('verticalAlign=top', 'verticalAlign=middle'), x + 6, top, W - 12, 20,
            cid=t.nid('notice'))
        lab = ('① ' if s.get('mark') == 'notice' else '') + '확인'
        buttons(t, x, top + 2, [lab], x + W - 9)
        top += 24
    body = s.get('body', ())
    # 왼쪽: 상품
    lx, cy = x + 6, top
    for it in body:
        if it[0] == 'search':
            t.v(it[1], S_TXT, lx, cy, LEFT_W, 16)
            cy += 20
        elif it[0] == 'product':
            _, name, stock, qty, mark = it
            sold = mark == 'soldout'
            t.v('', S_ROW, lx, cy, LEFT_W, ROW_H)
            t.v('이미지', S_IMG, lx + 4, cy + 4, IMG, IMG, cid=t.nid('img'))
            t.v(f'{name}<br>' + ('품절' if sold else f'재고 {stock}'), S_TXT_TOP, lx + IMG + 8, cy + 3, LEFT_W - IMG - 84, ROW_H - 6,
                cid=t.nid('prod'))
            pre = '~' if sold else ''
            lab = ('① ' if mark == '①' else '') + f'- {qty} +'
            _btn(t, pre + lab, lx + LEFT_W - 3, cy + 11, cid=t.nid('qty'))
            cy += ROW_H + 2
        elif it[0] == 'order_btn':
            mark = it[1]
            lab = ('~' if mark == 'off' else '') + ('① ' if mark == '①' else '') + '주문하기'
            _btn(t, lab, lx + LEFT_W - 3, cy + 2)
            cy += 22
        elif it[0] == 'inline':
            t.v(it[1], S_INLINE, lx, cy, LEFT_W, 28)
            cy += 30
    # 오른쪽: 내 주문
    rx, rw = x + LEFT_W + 16, W - LEFT_W - 22
    t.v('<b>내 주문</b>', S_TXT, rx, top, rw, 16)
    cy = top + 20
    for it in body:
        if it[0] == 'order':
            _, text, status, mark = it
            no, items = text.split(' ', 1)
            t.v('', S_ROW_HI if mark in ('①', 'hi', 'open') else S_ROW, rx, cy, rw, ROW_H)
            if mark == 'open':
                t.v(f'① {no}', S_BTN_ON, rx + 2, cy + 2, 54, 16, cid=t.nid('ord'))
            else:
                t.v(f'<b>{no}</b>', S_TXT, rx + 2, cy + 2, 60, 16, cid=t.nid('ord'))
            t.v(status, S_STATUS, rx + rw - 120, cy + 2, 64, 16, cid=t.nid('st'))
            if status == '접수됨':
                _btn(t, ('① ' if mark == '①' else '') + '취소', rx + rw - 3, cy + 2, cid=t.nid('cancel'))
            t.v(items, S_TXT, rx + 2, cy + 20, rw - 4, 16, cid=t.nid('items'))
            cy += ROW_H + 2
    for it in body:
        if it[0] == 'detail':
            _detail(t, x + LEFT_W + 10, top, W - LEFT_W - 16, y + H - 4 - top, it)
    for it in body:
        if it[0] == 'dialog':
            t.v('', S_DIM, x, y, W, H)
            t.v(it[1], S_DIALOG, x + 120, y + 70, 280, 110)
            buttons(t, x, y + 152, it[2], x + 390)


def _detail(t, px, py, pw, ph, it):
    """주문 상세 패널: 머리 · 상품 · 상태([취소]) · 시간별 진행."""
    _, no, items, status, steps, mark = it[:6]
    basket = it[6] if len(it) > 6 else None
    t.v('', S_PANEL, px, py, pw, ph)
    t.v(f'<b>주문 {no} 상세</b>', S_PANEL_HEAD, px, py, pw, 18, cid=t.nid('dno'))
    _btn(t, ('① ' if mark == 'close' else '') + '×', px + pw - 3, py + 1)
    lines = items.split(' · ')
    cy = py + 22
    t.v('<br>'.join(lines), S_TXT, px + 4, cy, pw - 8, 12 * len(lines) + 2, cid=t.nid('ditems'))
    cy += 12 * len(lines) + 6
    t.v('상태', S_TXT, px + 4, cy, 30, 16)
    t.v(status, S_STATUS, px + 36, cy, 64, 16, cid=t.nid('pst'))
    if status == '접수됨':
        _btn(t, ('① ' if mark == 'cancel' else '') + '취소', px + pw - 6, cy, cid=t.nid('cancel'))
    cy += 22
    if basket:
        t.v(basket, S_TXT, px + 36, cy - 6, pw - 40, 14, cid=t.nid('basket'))
        cy += 12
    t.v('<b>진행</b>', S_TXT, px + 4, cy, 60, 14)
    cy += 16
    last = max((i for i, s in enumerate(steps) if s[0]), default=-1)
    for i, step in enumerate(steps):
        tm, word = step[0], step[1]
        extra = f' · {step[2]}' if len(step) > 2 and step[2] else ''
        style = S_ROW_HI if i == last else (S_STEP_WAIT if not tm else S_ROW)
        t.v(f'{tm or "--:--"}  {word}{extra}', style, px + 4, cy, pw - 8, 15, cid=t.nid('tl'))
        cy += 17
