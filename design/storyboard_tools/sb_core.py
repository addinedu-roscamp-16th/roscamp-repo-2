"""스토리보드 공통 부품: 스타일 · 장면 칸 · 탭 배치 · draw.io 쓰기. 관리자 · 고객 스토리보드가 같이 쓴다."""
import xml.etree.ElementTree as ET

S_VLAB = 'text;html=1;fontSize=12;fontColor=#666666;align=left;verticalAlign=middle;'
S_TITLE = 'text;html=1;fontSize=18;fontStyle=1;align=left;verticalAlign=middle;'
S_SUB = 'text;html=1;fontSize=12;align=left;verticalAlign=top;whiteSpace=wrap;fontColor=#333333;'
S_CARD = 'rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#666666;verticalAlign=top;align=left;spacingLeft=8;fontSize=12;fontStyle=1;'
S_SCREEN = 'rounded=0;html=1;fillColor=#fafafa;strokeColor=#999999;'
S_BAR = 'rounded=0;html=1;fillColor=#e8e8e8;strokeColor=#999999;align=left;spacingLeft=6;fontSize=10;fontStyle=1;'
S_TXT = 'text;html=1;fontSize=9;align=left;verticalAlign=middle;whiteSpace=wrap;fontColor=#333333;spacingLeft=2;'
S_TXTC = 'text;html=1;fontSize=9;align=center;verticalAlign=middle;whiteSpace=wrap;fontColor=#333333;'
S_BOX = 'rounded=0;html=1;fillColor=#ffffff;strokeColor=#bbbbbb;fontSize=9;align=left;verticalAlign=top;spacingLeft=4;whiteSpace=wrap;fontColor=#333333;'
S_MAP = 'rounded=0;html=1;dashed=1;fillColor=none;strokeColor=#bbbbbb;fontSize=9;fontColor=#999999;whiteSpace=wrap;'
S_BTN = 'rounded=1;html=1;fillColor=#ffffff;strokeColor=#666666;fontSize=9;whiteSpace=wrap;'
S_BTN_ON = 'rounded=1;html=1;fillColor=#dae8fc;strokeColor=#1f5fbf;strokeWidth=2;fontSize=9;fontStyle=1;whiteSpace=wrap;'
S_ESTOP = 'rounded=1;html=1;fillColor=#e51400;strokeColor=#b20000;fontColor=#ffffff;fontSize=12;fontStyle=1;'
S_ESTOP_ON = 'rounded=1;html=1;fillColor=#e51400;strokeColor=#1f5fbf;strokeWidth=3;fontColor=#ffffff;fontSize=12;fontStyle=1;'
S_BTN_OFF = 'rounded=1;html=1;fillColor=#eeeeee;strokeColor=#bbbbbb;fontColor=#999999;fontSize=9;whiteSpace=wrap;'
S_RUN = 'rounded=1;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=9;fontColor=#2d6a2d;'
S_PAUSE = 'rounded=1;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;fontSize=9;fontColor=#9a5b00;'
S_ALERT = 'rounded=0;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=9;align=left;verticalAlign=top;spacingLeft=4;whiteSpace=wrap;fontColor=#5c4700;'
S_ALERT_R = 'rounded=0;html=1;fillColor=#f8cecc;strokeColor=#b85450;fontSize=9;align=left;verticalAlign=top;spacingLeft=4;whiteSpace=wrap;fontColor=#7a1f1c;'
S_ROW = 'rounded=0;html=1;fillColor=#ffffff;strokeColor=#cccccc;fontSize=9;align=left;spacingLeft=4;whiteSpace=wrap;fontColor=#333333;'
S_ROW_HI = 'rounded=0;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=9;align=left;spacingLeft=4;whiteSpace=wrap;fontColor=#5c4700;'
S_BANNER = 'rounded=0;html=1;fillColor=#e51400;strokeColor=#b20000;fontColor=#ffffff;fontSize=10;fontStyle=1;align=left;spacingLeft=6;'
S_DIM = 'rounded=0;html=1;fillColor=#000000;opacity=25;strokeColor=none;'
S_DIALOG = 'rounded=1;arcSize=6;html=1;fillColor=#ffffff;strokeColor=#333333;fontSize=10;align=center;verticalAlign=top;spacingTop=6;whiteSpace=wrap;'
S_DESC = 'text;html=1;fontSize=11;align=left;verticalAlign=top;whiteSpace=wrap;spacingLeft=8;spacingTop=4;fontColor=#222222;fillColor=#f5f5f5;strokeColor=#dddddd;'
S_NOTE = 'shape=note;size=14;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontSize=11;align=left;spacingLeft=6;verticalAlign=top;'
S_NOTE_P = 'shape=note;size=14;whiteSpace=wrap;html=1;dashed=1;fillColor=#ffe6cc;strokeColor=#d79b00;fontSize=11;align=left;spacingLeft=6;verticalAlign=top;'
S_PANEL = 'rounded=0;html=1;shadow=1;fillColor=#ffffff;strokeColor=#1f5fbf;strokeWidth=2;'
S_PANEL_HEAD = 'rounded=0;html=1;fillColor=#dae8fc;strokeColor=#1f5fbf;fontSize=9;fontStyle=1;align=left;spacingLeft=4;whiteSpace=wrap;'
S_STEP_DONE = 'rounded=0;html=1;fillColor=#f5f5f5;strokeColor=#dddddd;fontSize=9;align=left;verticalAlign=top;spacingLeft=4;whiteSpace=wrap;fontColor=#888888;'
S_STEP_WAIT = 'rounded=0;html=1;fillColor=#ffffff;strokeColor=#dddddd;fontSize=9;align=left;verticalAlign=top;spacingLeft=4;whiteSpace=wrap;fontColor=#888888;'
S_EDGE = 'edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeWidth=2;strokeColor=#555555;fontSize=11;labelBackgroundColor=#ffffff;fontStyle=1;'

CARD_W, SCR_W, SCR_H, BAR_H = 540, 520, 250, 34
GAP_X, GAP_Y = 90, 70


def text_len(s):
    """표시 폭 근사: 한글 · 기호 1, ASCII 0.55."""
    return sum(1 if ord(ch) > 0x2000 else 0.55 for ch in s)


def lines_needed(text, width, px=11.5):
    import re
    per = max(10, int((width - 16) / px))
    n = 0
    for ln in text.split('<br>'):
        n += max(1, -(-int(text_len(re.sub(r'<[^>]+>', '', ln)) + 0.999) // per))
    return n


class Tab:
    def __init__(self, tid, name):
        self.d = ET.Element('diagram', id=tid, name=name)
        m = ET.SubElement(self.d, 'mxGraphModel', dx='1400', dy='900', grid='1', gridSize='10', guides='1',
                          tooltips='1', connect='1', arrows='1', fold='1', page='1', pageScale='1',
                          pageWidth='1600', pageHeight='1200', math='0', shadow='0')
        self.m = m
        self.r = ET.SubElement(m, 'root')
        ET.SubElement(self.r, 'mxCell', id='0')
        ET.SubElement(self.r, 'mxCell', id='1', parent='0')
        self.n = 0
        self.maxx, self.maxy = 0, 0

    def nid(self, p='c'):
        self.n += 1
        return f'{self.d.get("id")}_{p}{self.n}'

    def v(self, val, style, x, y, w, h, cid=None):
        cid = cid or self.nid()
        c = ET.SubElement(self.r, 'mxCell', id=cid, value=val, style=style, vertex='1', parent='1')
        ET.SubElement(c, 'mxGeometry', x=str(round(x)), y=str(round(y)), width=str(round(w)), height=str(round(h)),
                      **{'as': 'geometry'})
        self.maxx, self.maxy = max(self.maxx, x + w), max(self.maxy, y + h)
        return cid

    def e(self, src, dst, label='', ports='', pts=()):
        c = ET.SubElement(self.r, 'mxCell', id=self.nid('e'), value=label, style=S_EDGE + ports, edge='1',
                          parent='1', source=src, target=dst)
        g = ET.SubElement(c, 'mxGeometry', relative='1', **{'as': 'geometry'})
        if pts:
            a = ET.SubElement(g, 'Array', **{'as': 'points'})
            for x, y in pts:
                ET.SubElement(a, 'mxPoint', x=str(x), y=str(y))

    def finish(self):
        self.m.set('pageWidth', str(int(max(1600, self.maxx + 60))))
        self.m.set('pageHeight', str(int(max(1100, self.maxy + 60))))
        return self.d


def buttons(t, x, y, buttons, right):
    """버튼 목록을 오른쪽 끝(right)부터 왼쪽으로. 버튼 텍스트가 ①로 시작하면 강조."""
    bx = right
    for b in reversed(buttons):
        off = b.startswith('~')
        b = b.lstrip('~')
        w = 12 + 8.5 * len(b)
        bx -= w + 4
        t.v(b, S_BTN_OFF if off else (S_BTN_ON if b[:1] in '①②③' else S_BTN), bx, y, w, 16)


def login_screen(t, x, y, body):
    """로그인 화면 (관리자 · 고객 공통). body: ('button', [...]) · ('text', 글)."""
    W, H = SCR_W, SCR_H
    t.v('<b>IFP 로그인</b><br><br>아이디 [__________]<br>비밀번호 [__________]', S_BOX, x + 160, y + 50, 200, 100)
    buttons(t, x, y + 160, [b for it in body if it[0] == 'button' for b in it[1]], x + 360)
    for it in body:
        if it[0] == 'text':
            t.v(it[1], S_TXTC, x + 20, y + 200, W - 40, 30)


def scene(t, sc, x, y, screen_fn):
    """장면 칸. sc: {n, title, screen:{kind, mode, body, menu, mark}, lines:[...]}. 카드 id 반환."""
    n = sc['n']
    lines = sc['lines']
    desc = '<br>'.join(lines)
    dh = lines_needed(desc, CARD_W - 20) * 15 + 16
    h = 30 + SCR_H + 10 + dh + 10
    label = sc.get('label') or f'장면 {n} · {sc["title"]}'
    cid = t.v(label, S_CARD, x, y, CARD_W, h, cid=f'sc{n}')
    s = sc['screen']
    screen_fn(t, x + 10, y + 28, s)
    t.v(desc, S_DESC, x + 10, y + 28 + SCR_H + 8, CARD_W - 20, dh, cid=f'desc{n}')
    return cid, h


def note(t, x, y, w, title, text_lines, pending=False):
    body = f'<b>{title}</b><br>' + '<br>'.join(text_lines)
    h = lines_needed(body, w) * 15 + 16
    t.v(body, S_NOTE_P if pending else S_NOTE, x, y, w, h, cid=t.nid('note'))
    return h


def build_tab(spec, ver, screen_fn):
    """탭 하나. screen_fn(t, x, y, screen_dict)가 장면의 화면을 그린다."""
    t = Tab(spec['id'], spec['name'])
    t.v(ver, S_VLAB, 40, -26, 200, 24, cid='version-label')
    t.v(spec['title'], S_TITLE, 40, 10, 1400, 36)
    if spec.get('subtitle'):
        t.v(spec['subtitle'], S_SUB, 40, 46, 1700, 40)
    if spec.get('custom'):
        spec['custom'](t)
        return t.finish()
    # 장면 배치: pos=(col,row)
    rows = {}
    for sc in spec['scenes']:
        rows.setdefault(sc['pos'][1], []).append(sc)
    y = 100
    pos = {}
    for r in sorted(rows):
        hmax = 0
        for sc in rows[r]:
            x = 40 + sc['pos'][0] * (CARD_W + GAP_X)
            cid, h = scene(t, sc, x, y, screen_fn)
            pos[sc['n']] = (cid, x, y, h)
            hmax = max(hmax, h)
        y += hmax + GAP_Y
    for sc in spec['scenes']:
        for nxt in sc.get('next', []):
            dst, label = nxt[0], nxt[1]
            ports = nxt[2] if len(nxt) > 2 else ''
            _, sx, sy, sh = pos[sc['n']]
            _, dx, dy, dh = pos[dst]
            pts = ()
            if 'exitY=1' in ports and 'entryY=0' in ports and sx != dx:
                gy = dy - GAP_Y / 2
                pts = ((sx + CARD_W / 2, gy), (dx + CARD_W / 2, gy))
            t.e(pos[sc['n']][0], pos[dst][0], label, ports, pts)
    nx, ny, rowh = 40, y, 0
    for title, lines, pending, w in spec.get('notes', []):
        if nx + w > 40 + 3 * (CARD_W + GAP_X):
            nx, ny, rowh = 40, ny + rowh + 20, 0
        h = note(t, nx, ny, w, title, lines, pending)
        nx += w + 20
        rowh = max(rowh, h)
    return t.finish()


def write(path, tabs):
    root = ET.Element('mxfile', host='app.diagrams.net', pages=str(len(tabs)))
    for d in tabs:
        root.append(d)
    tree = ET.ElementTree(root)
    ET.indent(tree, '  ')
    tree.write(path, encoding='utf-8')

