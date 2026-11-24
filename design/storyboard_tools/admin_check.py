"""관리자 스토리보드 검증기. usage: python3 check.py <file.drawio>  (종료 코드 = 실패 수)"""
import html
import re
import sys
import xml.etree.ElementTree as ET

TAB_NAMES = ['0. 화면 구성', '1. 평상시 현황', '2. 결품 · 담기 일시 중지', '3. Pode 장애', '4. Phody 장애',
             '5. Phony 장애', '6. 재고 · 이력', '7. 마감', '8. 서비스 일시 정지 · 재시작 · 운영 모드', '9. 비상정지',
             '10. 시험 (개발자)']
VERSION = 'v1.4 · 2026-11-19'
COVER = ['SCR-01', 'SCR-06', 'SCR-07', 'SCR-08', 'SCR-10', 'SCR-11', 'SCR-12', 'SCR-16', 'SCR-18', 'SCR-19',
         'OMS-08', 'OMS-09', 'OMS-16', 'COM-02', 'SCR-20', 'OMS-03', 'OMS-18', 'SCR-21', 'OMS-19',
         'SCR-17', 'OMS-21', 'COM-06']
COVER_BASIS = ['SCR-17', 'OMS-21', 'COM-06']  # v1.4: 장면의 근거 줄에 있어야 함
FAILS = []


def fail(msg):
    FAILS.append(msg)
    print('FAIL', msg)


def plain(v):
    v = re.sub(r'<br\s*/?>', '\n', v or '')
    return html.unescape(re.sub(r'<[^>]+>', '', v))


def text_len(s):
    return sum(1 if ord(ch) > 0x2000 else 0.55 for ch in s)


def wrapped_lines(text, per_line):
    n = 0
    for ln in text.split('\n'):
        n += max(1, -(-int(text_len(ln) + 0.999) // per_line))
    return n


def load(path):
    return ET.parse(path).getroot()


def check_structure(root):
    if root.tag != 'mxfile' or root.get('pages') != str(len(TAB_NAMES)):
        fail(f'mxfile pages={root.get("pages")}')
    tabs = root.findall('diagram')
    names = [d.get('name') for d in tabs]
    if names != TAB_NAMES:
        fail(f'탭 이름 {names}')
    for d in tabs:
        cells = list(d.iter('mxCell'))
        ids = [c.get('id') for c in cells]
        if len(ids) != len(set(ids)):
            fail(f'{d.get("name")}: id 중복')
        idset = set(ids)
        for c in cells:
            if c.get('edge') and any(c.get(k) and c.get(k) not in idset for k in ('source', 'target')):
                fail(f'{d.get("name")}: 끊긴 화살표 {c.get("id")}')
        lab = [c for c in cells if c.get('id') == 'version-label']
        if len(lab) != 1 or lab[0].get('value') != VERSION:
            fail(f'{d.get("name")}: 버전 라벨')


def all_text(root):
    for d in root.findall('diagram'):
        for c in d.iter('mxCell'):
            yield d.get('name'), c, plain(c.get('value'))


def check_terms(root):
    for tab, c, t in all_text(root):
        for pat, why in ((r'(?<!서비스 )(?<!팔 )일시 정지', '대상 없는 일시 정지'), (r'담기 보류', '담기 보류'),
                         (r'취소하고 멈춤', '취소하고 멈춤'), (r'(?<!E)STOP', 'STOP')):
            if re.search(pat, t):
                fail(f'{tab} {c.get("id")}: 용어 "{why}" — {t[:60]!r}')


def check_coverage(root):
    text = '\n'.join(t for _, _, t in all_text(root))
    for k in COVER:
        if k not in text:
            fail(f'기능 {k} 없음')


def check_button_refs(root):
    owner = {}
    for tab, c, t in all_text(root):
        if not (c.get('id') or '').startswith('desc'):
            continue
        lines = {ln.split(':', 1)[0]: ln for ln in t.split('\n') if ':' in ln}
        admin, basis = lines.get('관리자', ''), lines.get('근거', '')
        buttons = re.findall(r'[①②]\[([^\]]+)\]', admin)  # 장면에서 실제로 누르는 버튼(①②)만
        buttons = [b for b in buttons if b not in ('로봇으로', '주문 관리로', '재고로', '확인')]  # 이동 · 확인 버튼은 메시지가 아님
        if not buttons:
            continue
        if not re.search(r'(SCR|OMS|COM|P)-\d+', basis):
            fail(f'{tab} {c.get("id")}: 버튼 {buttons} 근거 ID 없음')
        for msg in re.findall(r'6-\d+(?:-\d+)? #\d+', basis):
            for b in buttons:
                prev = owner.setdefault(msg, b)
                if prev != b:
                    fail(f'메시지 {msg}: 버튼 "{prev}"와 "{b}"에 함께 쓰임 ({tab})')


def check_text_fit(root):
    for d in root.findall('diagram'):
        for c in d.iter('mxCell'):
            cid = c.get('id') or ''
            if not (cid.startswith('desc') or cid.startswith('note')):
                continue
            g = c.find('mxGeometry')
            w, h = float(g.get('width')), float(g.get('height'))
            per = max(10, int((w - 16) / 11.5))
            need = wrapped_lines(plain(c.get('value')), per) * 15 + 12
            if need > h:
                fail(f'{d.get("name")} {cid}: 글자 넘침 (필요 {need} > 높이 {h:.0f})')


def check_estop_order(root):
    tab = [d for d in root.findall('diagram') if d.get('name') == '9. 비상정지']
    if not tab:
        return
    descs = {}
    for c in tab[0].iter('mxCell'):
        m = re.match(r'desc(\d+)$', c.get('id') or '')
        if m:
            descs[int(m.group(1))] = plain(c.get('value'))
    if not descs:
        return
    def first(pred):
        for n in sorted(descs):
            if pred(descs[n]):
                return n
        return None
    adm = lambda t: next((ln for ln in t.split('\n') if ln.startswith('관리자')), '')
    sysl = lambda t: next((ln for ln in t.split('\n') if ln.startswith('시스템')), '')
    n_rel = first(lambda t: '[비상정지 해제]' in adm(t))
    n_hold = first(lambda t: '서비스 일시 정지' in sysl(t) and '유지' in sysl(t))
    n_run = first(lambda t: '[재시작]' in adm(t))
    if None in (n_rel, n_hold, n_run) or not (n_rel < n_hold < n_run):
        fail(f'비상정지 순서: 해제 {n_rel}, 일시 정지 유지 {n_hold}, 재시작 {n_run}')
    elif '실행 중' in sysl(descs[n_rel]):
        fail('비상정지: 해제 장면에서 바로 실행 중')


REQUIRED_EDGE_LABELS = {'4. Phody 장애': ['[담는 중인 주문]', '[담는 중인 주문 없음]']}
FORBIDDEN_EDGE_LABELS = ['같은 조치 줄']
REQUIRED_TEXT = {'0. 화면 구성': ['운영 모드(정상 운영 · 시험) · 개발자 역할 · 주황 띠', '확인 창', '알림 칸에서 빠짐', '처리 중', '[로봇으로]', '락 해제', '마감', '수동 [해결]은 없음', '락 풀림 n', '배정에서 빠지고', '마감 시작', '간편 처리', '각 화면에서도 똑같이'],
                 '2. 결품 · 담기 일시 중지': ['로봇으로', '락 해제', '락 잠금', '수시 조정', '☐ 현장 담당자', 'Phody 왼팔  → 락 풀림', '입고 먼저', '두 버튼 모두 꺼짐'],
                 '3. Pode 장애': ['로봇으로', '락 해제', '락 잠금', '위치를 다시 설정', '☐ 현장 담당자', '다른 로봇은 계속 운행 중'],
                 '4. Phody 장애': ['로봇으로', 'R-06', 'R-10', 'PHD-10'], '5. Phony 장애': ['로봇으로', 'R-07', 'R-10'],
                 '8. 서비스 일시 정지 · 재시작 · 운영 모드': ['R-09', 'R-20', '시험 모드로', '시험 모드 진입 중', '열기', '진입 취소', '진입 확정',
                                             '연결 중…', '정상 운영으로', '시험에서 멈춘 로봇', '시험 영향', '마감 확정', '운영 모드: 정상 운영',
                                             '화면 밖', '모드가 다른 로봇', '보통 복구 절차'],
                 '10. 시험 (개발자)': ['시험 주문', '시나리오 실행', '직접 주문', '조건 바꾸기', '상태 바꾸기', '강제 해제', '충전 끝내기',
                                   '강제: 배터리 20%', '팔 일시 정지(PAUSED)', '운행 복귀 확인', '기준 35% 미만', '주문 관리로', 'R-20'],
                 '9. 비상정지': ['[비상정지 해제]'],
                 '6. 재고 · 이력': ['반출', '사유', '주문 거절', '입고 · 반출 기록', '수시 조정', 'R-01', 'R-08', '기록 종류: 수시 조정', '입력 오류 · 기타', '수동 해결은 없음', '추가 입고', '7번 탭'],
                 '7. 마감': ['마감 취소', '마감 확정', '남은 운반 건', '배정 보류 반영됨', '추가 입고', '실사', '입력한 수량은 재고에 반영되지 않음', '아직 안 셈', 'R-17', 'R-19']}
FORBIDDEN_TEXT = ['입고 · 수정', '재고 수정', '입력한 수량은 버려짐', '마감 이전 날짜', '조치 버튼을 누르면 자동 해결', '① 해결', '현장에 사람이 있나요']
ALERT_OK = {'주문 관리로', '로봇으로', '재고로', '확인', '간편 처리'}
PANEL_KEY = 'shadow=1;'
PANEL_FORBIDDEN = {'락 해제', '락 잠금', '마감', '재시작', '비상정지 해제'}
OFF_REQUIRED = {'2. 결품 · 담기 일시 중지': '락 잠금 뒤', '3. Pode 장애': '락 잠금 뒤'}
S_ALERT_KEYS = ('fillColor=#fff2cc;strokeColor=#d6b656;fontSize=9;align=left;verticalAlign=top',
                'fillColor=#f8cecc;strokeColor=#b85450;fontSize=9;align=left;verticalAlign=top')


def geo(c):
    g = c.find('mxGeometry')
    return float(g.get('x', 0)), float(g.get('y', 0)), float(g.get('width', 0)), float(g.get('height', 0))


def check_v11(root):
    for d in root.findall('diagram'):
        name = d.get('name')
        cells = [c for c in d.iter('mxCell') if c.get('vertex')]
        for c in cells:
            t = plain(c.get('value'))
            for f in FORBIDDEN_TEXT:
                if f in t:
                    fail(f'{name} {c.get("id")}: 금지 문구 "{f}"')
            if '주문 관리' in t and ' | ' in t and '로봇' not in t:
                fail(f'{name} {c.get("id")}: 메뉴에 로봇 없음')
        alerts = [geo(c) for c in cells if any(k in (c.get('style') or '') for k in S_ALERT_KEYS)]
        for c in cells:
            st = c.get('style') or ''
            if not st.startswith('rounded=1;html=1;') or 'fontSize=9' not in st:
                continue
            x, y, w, h = geo(c)
            for ax, ay, aw, ah in alerts:
                if ax <= x and x + w <= ax + aw + 1 and ay <= y and y + h <= ay + ah + 1:
                    b = re.sub(r'^[①②③]\s*', '', plain(c.get('value')).strip())
                    if b not in ALERT_OK:
                        fail(f'{name}: 알림 안의 조치 버튼 "{b}" (알림은 이동 · 확인만)')
        panels = [geo(c) for c in cells if PANEL_KEY in (c.get('style') or '')]
        for c in cells:
            st = c.get('style') or ''
            if not st.startswith('rounded=1;html=1;') or 'fontSize=9' not in st:
                continue
            x, y, w, h = geo(c)
            for ax, ay, aw, ah in panels:
                if ax <= x and x + w <= ax + aw + 1 and ay <= y and y + h <= ay + ah + 1:
                    b = re.sub(r'^[①②③]\s*', '', plain(c.get('value')).strip())
                    if b in PANEL_FORBIDDEN:
                        fail(f'{name}: 간편 처리 패널에 단순 조치가 아닌 버튼 "{b}"')
        if name in ('3. Pode 장애', '4. Phody 장애', '5. Phony 장애') and not panels:
            fail(f'{name}: 간편 처리 패널 장면 없음')
        need = OFF_REQUIRED.get(name)
        if need and not any(need in plain(c.get('value')) and 'fillColor=#eeeeee' in (c.get('style') or '') for c in cells):
            fail(f'{name}: 락이 풀린 동안 꺼진 버튼("{need}") 없음')


def check_review_fixes(root):
    for d in root.findall('diagram'):
        name = d.get('name')
        cells = list(d.iter('mxCell'))
        text = '\n'.join(plain(c.get('value')) for c in cells)
        labels = [plain(c.get('value')) for c in cells if c.get('edge')]
        for lab in REQUIRED_EDGE_LABELS.get(name, []):
            if lab not in labels:
                fail(f'{name}: 갈래 라벨 {lab} 없음')
        for lab in FORBIDDEN_EDGE_LABELS:
            if lab in labels:
                fail(f'{name}: 화살표 라벨 {lab}')
        for t in REQUIRED_TEXT.get(name, []):
            if t not in text:
                fail(f'{name}: 규칙 문구 "{t}" 없음')
        # 버튼 이름: 대상 없는 "재개" 금지
        for c in cells:
            v = plain(c.get('value')).strip()
            if re.fullmatch(r'(①\s*)?재개', v) or '[재개]' in v:
                fail(f'{name} {c.get("id")}: 대상 없는 "재개" 버튼')
        # 알림이 있는 탭은 D-06 표시
        if any('fillColor=#fff2cc;strokeColor=#d6b656;fontSize=9;align=left;verticalAlign=top' in (c.get('style') or '')
               or 'fillColor=#f8cecc' in (c.get('style') or '') for c in cells) and 'D-06' not in text:
            fail(f'{name}: 알림 장면이 있는데 D-06 표시 없음')
        # 장면 번호가 읽는 순서(위 → 아래, 왼 → 오른)와 같음
        sc = []
        for c in cells:
            m = re.fullmatch(r'sc(\d+)', c.get('id') or '')
            if m:
                g = c.find('mxGeometry')
                sc.append((float(g.get('y')), float(g.get('x')), int(m.group(1))))
        nums = [n for _, _, n in sorted(sc)]
        if nums != sorted(nums):
            fail(f'{name}: 장면 번호가 배치 순서와 다름 {nums}')
    tab8 = [d for d in root.findall('diagram') if d.get('name') == '9. 비상정지']
    if tab8:
        descs = {}
        for c in tab8[0].iter('mxCell'):
            m = re.fullmatch(r'desc(\d+)', c.get('id') or '')
            if m:
                descs[int(m.group(1))] = plain(c.get('value'))
        rel = next((n for n in sorted(descs) if '[비상정지 해제]' in descs[n].split('\n')[0]), None)
        for n in sorted(descs):
            if rel and n < rel and '접수됨' in descs[n].split('\n')[1]:
                fail(f'9번 탭 장면 {n}: 해제 전에 주문을 접수됨으로 되돌림')
        dis = [c for c in tab8[0].iter('mxCell') if plain(c.get('value')).startswith('비상정지 해제 (체크리스트')]
        if not dis:
            fail('9번 탭: 해제 버튼 꺼짐 표시 없음')


BAND_KEY = 'fillColor=#fa6800;'  # 주황 띠 "시험 모드" (admin_screen.S_BAND)
TEST_SCENES = {'8. 서비스 일시 정지 · 재시작 · 운영 모드': {8, 9}, '10. 시험 (개발자)': None}  # None = 모든 장면
ROBOT_ROWS_10 = ['Pode 1', 'Pode 2', 'Pode 3', 'Phody 왼팔', 'Phody 오른팔', 'Phony']


def scene_cards(d):
    """{장면 번호: (카드 geo, 카드 안 vertex 셀들, 설명 글)}."""
    cells = [c for c in d.iter('mxCell') if c.get('vertex')]
    cards = {}
    for c in cells:
        m = re.fullmatch(r'sc(\d+)', c.get('id') or '')
        if m:
            cards[int(m.group(1))] = geo(c)
    out = {}
    for n, (cx, cy, cw, ch) in cards.items():
        inside = [c for c in cells if c.get('id') != f'sc{n}' and cx <= geo(c)[0] and geo(c)[0] + geo(c)[2] <= cx + cw + 1
                  and cy <= geo(c)[1] and geo(c)[1] + geo(c)[3] <= cy + ch + 1]
        desc = next((plain(c.get('value')) for c in inside if c.get('id') == f'desc{n}'), '')
        out[n] = ((cx, cy, cw, ch), inside, desc)
    return out


def check_v14(root):
    """v1.4 운영 모드 · 시험 화면."""
    for tab, c, t in all_text(root):
        if '[시험]' in t:
            fail(f'{tab} {c.get("id")}: "[시험]" 표시 (시험 데이터는 DB를 따로 씀)')
    basis = '\n'.join(ln for _, c, t in all_text(root) if (c.get('id') or '').startswith('desc')
                      for ln in t.split('\n') if ln.startswith('근거'))
    for k in COVER_BASIS:
        if k not in basis:
            fail(f'기능 {k}가 근거 줄에 없음')
    for d in root.findall('diagram'):
        name = d.get('name')
        want = TEST_SCENES.get(name, set())
        for n, (_, inside, desc) in scene_cards(d).items():
            band = [c for c in inside if BAND_KEY in (c.get('style') or '')]
            is_test = want is None or n in want
            if is_test and not any('시험 모드' in plain(c.get('value')) for c in band):
                fail(f'{name} 장면 {n}: 시험 모드 장면에 주황 띠 "시험 모드" 없음')
            if not is_test and band:
                fail(f'{name} 장면 {n}: 정상 운영 장면에 주황 띠')
            texts = [plain(c.get('value')) for c in inside]
            menu = [x for x in texts if ' | ' in x and '주문 관리' in x]
            if any(re.search(r'\|\s*(① )?시험\b', x) for x in menu) and not is_test:
                fail(f'{name} 장면 {n}: 정상 운영 화면에 메뉴 "시험"')
            btn = lambda b: any(re.sub(r'^[①②③]\s*', '', x.strip()) == b for x in texts)
            if btn('시험 모드로') and is_test:
                fail(f'{name} 장면 {n}: 시험 모드에 [시험 모드로]')
            if btn('정상 운영으로') and not is_test:
                fail(f'{name} 장면 {n}: 정상 운영에 [정상 운영으로]')
            # ① 표시: 화면에 하나뿐, 관리자 줄의 ①[X]와 같은 버튼
            ones = [re.sub(r'^①\s*', '', x.strip()) for x in texts if x.strip().startswith('①')]
            ones += [m for x in menu for m in re.findall(r'① (\S+)', x)]
            adm = next((ln for ln in desc.split('\n') if ln.startswith('관리자')), '')
            said = [x or y for x, y in re.findall(r'①\[([^\]]+)\]|① ?"([^"]+)"', adm)]  # 메뉴는 ①"시험" ("[시험]" 금지)
            if name in TEST_SCENES:
                if len(ones) > 1:
                    fail(f'{name} 장면 {n}: ① 표시가 여러 개 {ones}')
                norm = lambda b: re.sub(r'^[⏸▶]\s*', '', b)
                if [norm(b) for b in ones] != said:
                    fail(f'{name} 장면 {n}: 화면의 ① {ones}와 관리자 줄의 ① {said}가 다름')
        if name == '10. 시험 (개발자)':
            for n, (_, inside, desc) in scene_cards(d).items():
                rows = [plain(c.get('value')) for c in inside]
                if any(x.startswith('시험 주문') for x in rows):
                    missing = [r for r in ROBOT_ROWS_10 if not any(x.startswith(r + ' ') for x in rows)]
                    if missing:
                        fail(f'10번 탭 장면 {n}: 로봇 줄 없음 {missing}')


def main(path):
    try:
        root = load(path)
    except (FileNotFoundError, ET.ParseError) as e:
        fail(f'파일 읽기 실패: {e}')
        return len(FAILS)
    for f in (check_structure, check_terms, check_coverage, check_button_refs, check_text_fit, check_estop_order, check_review_fixes, check_v11, check_v14):
        f(root)
    print('OK' if not FAILS else f'{len(FAILS)} failures')
    return len(FAILS)


if __name__ == '__main__':
    sys.exit(min(main(sys.argv[1]), 100))
