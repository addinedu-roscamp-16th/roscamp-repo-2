"""관리자 화면 그리기: 위쪽 고정 줄 · 현황 · 주문 관리 · 로봇 · 재고 · 이력 · 비상조치."""
from sb_core import *  # noqa: F401,F403
from sb_core import buttons as _buttons, login_screen


S_BAND = 'rounded=0;html=1;fillColor=#fa6800;strokeColor=#c73500;fontColor=#ffffff;fontSize=9;fontStyle=1;align=left;spacingLeft=6;'
S_OPLINE = 'rounded=0;html=1;fillColor=#f0f0f0;strokeColor=#bbbbbb;fontColor=#555555;fontSize=9;align=left;spacingLeft=6;'
S_ENTERING = 'rounded=0;html=1;fillColor=#fff2cc;strokeColor=#d79b00;fontColor=#9a5b00;fontSize=9;fontStyle=1;align=left;spacingLeft=6;'
S_TOAST = 'rounded=1;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontColor=#2d6a2d;fontSize=9;fontStyle=1;whiteSpace=wrap;'
S_MODAL = 'rounded=1;arcSize=3;html=1;fillColor=#ffffff;strokeColor=#333333;strokeWidth=2;'
OP_H = 20  # 운영 모드 줄(주황 띠 · 진입 중 줄 · 개발자 운영 모드 줄) 높이


def opline(t, x, y, w, op, mark=None):
    """위쪽 고정 줄 바로 아래 운영 모드 줄 (v1.4).
    op: 'dev' (정상 운영 · 개발자: [시험 모드로]) | 'entering' (시험 모드 진입 중 · [열기]) | 'test' (주황 띠 "시험 모드")."""
    yy = y + BAR_H
    if op == 'test':
        t.v('시험 모드 · 운영 모드: 시험 (시험 DB)', S_BAND, x, yy, w, OP_H)
        _buttons(t, x, yy + 2, [('① ' if mark == 'op' else '') + '정상 운영으로'], x + w - 4)
    elif op == 'entering':
        t.v('시험 모드 진입 중 · 남은 운반 건 2건', S_ENTERING, x, yy, w, OP_H)
        _buttons(t, x, yy + 2, [('① ' if mark == 'op' else '') + '열기'], x + w - 4)
    elif op == 'dev':
        t.v('운영 모드: 정상 운영 · 개발자 계정', S_OPLINE, x, yy, w, OP_H)
        _buttons(t, x, yy + 2, [('① ' if mark == 'op' else '') + '시험 모드로'], x + w - 4)


def modal(t, x, y, title, rows, btns, close=''):
    """단계형 모달 (v1.4 운영 모드 마감). rows: ('row', 글, hi) · ('text', 글). close: 제목 줄 오른쪽 버튼 ('닫기' 등)."""
    W, H = SCR_W, SCR_H
    t.v('', S_DIM, x, y, W, H)
    mx, my, mw, mh = x + 16, y + BAR_H + 8, W - 32, H - BAR_H - 14
    t.v('', S_MODAL, mx, my, mw, mh)
    t.v('<b>' + title + '</b>', S_PANEL_HEAD, mx + 4, my + 4, mw - 8, 18)
    if close:
        _buttons(t, mx, my + 5, [close], mx + mw - 6)
    cy = my + 26
    for it in rows:
        if it[0] == 'row':
            t.v(it[1], S_ROW_HI if it[2] else S_ROW, mx + 6, cy, mw - 12, 18)
            cy += 20
        else:
            t.v(it[1], S_TXT, mx + 6, cy, mw - 12, 16)
            cy += 17
    _buttons(t, mx, my + mh - 21, btns, mx + mw - 6)


def overlay(t, x, y, body):
    """화면 위에 겹치는 것 (v1.4): 확인 창 · 모달 · 짧은 안내. 대시보드가 아닌 화면에서도 쓴다."""
    for it in body:
        if it[0] == 'toast':
            t.v(it[1], S_TOAST, x + 318, y + BAR_H + OP_H + 22, SCR_W - 324, 30)  # 알림 칸 자리
        elif it[0] == 'dialog2':
            t.v('', S_DIM, x, y, SCR_W, SCR_H)
            t.v(it[1], S_DIALOG, x + 100, y + 66, 320, 116)
            _buttons(t, x, y + 160, it[2], x + 410)
        elif it[0] == 'modal':
            modal(t, x, y, *it[1:])


def topbar(t, x, y, w, mode, menu='현황', mark=None, estop_view=False, mode_off=False, badge=0, closing=False, dev=False):
    """위쪽 고정 줄. mode: '실행 중' | '서비스 일시 정지'. mark: 'mode' | 'estop' | 'menu' (① 표시).
    mode_off: 작업 모드 버튼을 꺼진 모양으로. badge: 현황 메뉴 옆 알림 수. dev: 시험 모드의 개발자 (메뉴 "시험")."""
    t.v('', S_BAR, x, y, w, BAR_H)
    t.v('<b>IFP</b>', S_TXT, x + 4, y + 2, 24, BAR_H - 4)
    if estop_view:
        t.v('<i>비상조치 화면으로 고정됨</i>', S_TXT.replace('fontColor=#333333', 'fontColor=#888888'), x + 30, y + 2, 150, BAR_H - 4)
    else:
        items = ['현황', '주문 관리', '로봇', '재고', '이력'] + (['시험'] if dev else [])
        lab = lambda i: (('① ' if mark == 'menu' and i == '시험' else '') + i
                         + (f' <font color="#b20000">●{badge}</font>' if i == '현황' and badge else ''))
        menu_txt = ' | '.join(f'<u><b>{lab(i)}</b></u>' if i == menu else lab(i) for i in items)
        t.v(menu_txt, S_TXT, x + 28, y + 2, 186, BAR_H - 4)
    run = mode == '실행 중'
    pw = 62 if run and not closing else 86
    if estop_view:
        t.v('비상정지 중', S_ALERT_R.replace('align=left;', 'align=center;'), x + w - 218 - 70, y + 9, 70, 16)
    else:
        t.v(mode + (' · 마감' if closing else ''), S_PAUSE if closing or not run else S_RUN, x + w - 218 - pw, y + 9, pw, 16)
        on = mark == 'mode'
        txt = ('① ' if on else '') + ('⏸ 서비스 일시 정지' if run else '▶ 재시작')
        style = S_BTN_OFF if mode_off else (S_BTN_ON if on else S_BTN)
        t.v(txt, style, x + w - 212, y + 9, 94, 16)
        if mode_off:
            t.v('장애 처리 완료 뒤 켜짐', S_TXT.replace('fontColor=#333333', 'fontColor=#888888'), x + w - 212, y + 24, 100, 10)
    on = mark == 'estop'
    style = S_BTN_OFF if estop_view else (S_ESTOP_ON if on else S_ESTOP)
    t.v(('① ' if on else '') + '비상정지', style, x + w - 106, y + 2, 102, 30)



def draw(t, x, y, kind, mode='실행 중', body=(), menu=None, mark=None, mode_off=False, badge=0, closing=False, op=None):
    """kind: login | dashboard | orders | stock | history | alerts | estop | test. 화면 크기 (SCR_W, SCR_H).
    op: 운영 모드 줄 (opline 참고, v1.4). 'test'면 주황 띠와 메뉴 "시험"(개발자)."""
    W, H = SCR_W, SCR_H
    t.v('', S_SCREEN, x, y, W, H)
    if kind == 'login':
        login_screen(t, x, y, body)
        return
    menu = menu or {'dashboard': '현황', 'orders': '주문 관리', 'robots': '로봇', 'stock': '재고', 'history': '이력',
                    'alerts': '이력', 'test': '시험'}.get(kind, '현황')
    topbar(t, x, y, W, mode, menu, mark, estop_view=(kind == 'estop'), mode_off=mode_off, badge=badge, closing=closing,
           dev=(op == 'test'))
    top = y + BAR_H + 6
    if op:
        opline(t, x, y, W, op, mark)
        top += OP_H
    if kind == 'test':
        test_screen(t, x, top, body)
        return
    if kind == 'dashboard':
        lw = 300
        has_map = not any(it[0] == 'nomap' for it in body)
        cy = top
        if has_map:
            t.v('지도 · Pode 위치<br>(일정에 따라, SCR-06)', S_MAP, x + 6, cy, lw, 60)
            cy += 66
        for it in body:
            if it[0] in ('robots', 'progress'):
                h = 64 if it[0] == 'robots' else 56
                t.v(('<b>로봇 상태</b><br>' if it[0] == 'robots' else '<b>주문 진행</b><br>') + it[1], S_BOX,
                    x + 6, cy, lw, h)
                cy += h + 6
        ax = x + lw + 12
        aw = W - lw - 18
        t.v('<b>알림</b>', S_TXT, ax, top, aw, 14)
        ay = top + 16
        for it in body:
            if it[0] == 'alert':
                _, text, buttons, red = (list(it) + [[], False])[:4]
                lines_b, cur, cw = [], [], 0
                for b in buttons:
                    bw = 12 + 8.5 * len(b.lstrip('~')) + 4
                    if cur and cw + bw > aw - 6:
                        lines_b.append(cur)
                        cur, cw = [], 0
                    cur.append(b)
                    cw += bw
                if cur:
                    lines_b.append(cur)
                h = 30 + 18 * len(lines_b)
                t.v(text, S_ALERT_R if red else S_ALERT, ax, ay, aw, h)
                for k, lb in enumerate(lines_b):
                    _buttons(t, ax, ay + h - 19 - 18 * (len(lines_b) - 1 - k), lb, ax + aw - 3)
                ay += h + 4
        for it in body:
            if it[0] == 'panel':
                _, title, steps = it
                px, pw = x + W - 276, 270
                t.v('', S_PANEL, px, top - 2, pw, H - BAR_H - 8)
                t.v(title + '<span style="float:right">[닫기]</span>', S_PANEL_HEAD, px + 4, top + 2, pw - 8, 18)
                sy = top + 24
                for mark, text, btns in steps:
                    sh = 22 + (18 if btns else 0)
                    st = {'done': S_STEP_DONE, 'now': S_ROW_HI.replace('fontSize=9;', 'verticalAlign=top;fontSize=9;'),
                          'wait': S_STEP_WAIT}[mark]
                    sym = {'done': '✓ ', 'now': '▶ ', 'wait': '○ '}[mark]
                    t.v(sym + text, st, px + 4, sy, pw - 8, sh)
                    if btns:
                        _buttons(t, px, sy + sh - 19, btns, px + pw - 7)
                    sy += sh + 3
        for it in body:
            if it[0] == 'dialog':
                t.v('', S_DIM, x, y, W, H)
                t.v(it[1], S_DIALOG, x + 120, y + 70, 280, 110)
                _buttons(t, x, y + 152, it[2], x + 390)
        return
    if kind == 'estop':
        cy = top
        for it in body:
            if it[0] == 'banner':
                t.v(it[1], S_BANNER, x + 6, cy, W - 12, 20)
                cy += 24
            elif it[0] == 'row':
                _, text, buttons, hi = (list(it) + [[], False])[:4]
                t.v(text, S_ROW_HI if hi else S_ROW, x + 6, cy, W - 12, 20)
                if buttons:
                    _buttons(t, x, cy + 2, buttons, x + W - 9)
                cy += 22
            elif it[0] == 'check':
                t.v(it[1], S_BOX, x + 6, cy, W - 12, 44)
                cy += 48
            elif it[0] == 'button':
                _buttons(t, x, cy, it[1], x + W - 9)
                cy += 20
            elif it[0] == 'button_off':
                t.v(it[1], S_BTN_OFF, x + W - 9 - 170, cy, 170, 16)
                cy += 20
            elif it[0] == 'text':
                t.v(it[1], S_TXT, x + 6, cy, W - 12, 18)
                cy += 20
        return
    # orders / stock / history / alerts: 위쪽 필터 + 줄 목록
    cy = top
    for it in body:
        if it[0] == 'filter':
            t.v(it[1], S_TXT, x + 6, cy, W - 12, 16)
            cy += 20
        elif it[0] == 'row':
            _, text, buttons, hi = (list(it) + [[], False])[:4]
            h = 32 if buttons else 20
            st = S_ROW_HI if hi else S_ROW
            t.v(text, st + ('verticalAlign=top;spacingTop=-1;' if buttons else ''), x + 6, cy, W - 12, h)
            if buttons:
                _buttons(t, x, cy + 14, buttons, x + W - 9)
            cy += h + 3
        elif it[0] == 'text':
            t.v(it[1], S_TXT, x + 6, cy, W - 12, 18)
            cy += 20
        elif it[0] == 'button':
            _buttons(t, x, cy, it[1], x + W - 9)
            cy += 20



def test_screen(t, x, top, body):
    """시험 화면 (10번 탭, SCR-17 · OMS-21). body: ('orders', 글, 버튼들) · ('head', 글) · ROW · ('text', 글)."""
    W = SCR_W
    cy = top
    for it in body:
        if it[0] == 'orders':
            t.v(it[1], S_BOX, x + 6, cy, W - 12, 36)
            _buttons(t, x, cy + 18, it[2], x + W - 9)
            cy += 39
        elif it[0] == 'head':
            t.v(it[1], S_TXT, x + 6, cy, W - 12, 12)
            cy += 13
        elif it[0] == 'row':
            _, text, buttons, hi = (list(it) + [[], False])[:4]
            t.v(text, S_ROW_HI if hi else S_ROW, x + 6, cy, W - 12, 19)
            if buttons:
                _buttons(t, x, cy + 1.5, buttons, x + W - 9)
            cy += 20
        elif it[0] == 'text':
            t.v(it[1], S_TXT, x + 6, cy, W - 12, 15)
            cy += 15


def screen(t, x, y, s):
    """sb_core.build_tab에 넘기는 화면 함수. s: 장면의 screen dict."""
    draw(t, x, y, s['kind'], s.get('mode', '실행 중'), s.get('body', ()), s.get('menu'), s.get('mark'),
         s.get('mode_off', False), s.get('badge', 0), s.get('closing', False), s.get('op'))
    overlay(t, x, y, s.get('body', ()))
