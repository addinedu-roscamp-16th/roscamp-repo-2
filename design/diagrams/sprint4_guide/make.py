"""스프린트 4회차 연동 안내 그림 (비개발자용). python3 make.py → *.html, 그다음 chromium으로 PNG."""
from pathlib import Path
OUT = Path(__file__).parent
FONT = "NanumSquare, 'Noto Sans CJK KR', sans-serif"
C = {  # 담당 색: (배경, 테두리, 글자)
    "주문": ("#e8f1fd", "#2f6fd0", "#163f80"),
    "관제": ("#e6f6ec", "#2e9a55", "#165c30"),
    "양팔": ("#fdf0e2", "#d9822b", "#7a4210"),
    "출고": ("#f1eafb", "#8256c9", "#45247d"),
    "회색": ("#f3f4f6", "#9aa0a8", "#374151"),
}
def esc(t): return t.replace("&", "&amp;").replace("<", "&lt;")
def text(x, y, t, size=16, w="normal", fill="#1f2937", anchor="middle"):
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{w}" fill="{fill}" text-anchor="{anchor}">{esc(t)}</text>'
def group(x, y, w, h, who, label):
    bg, bd, fg = C[who]
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{bg}" stroke="{bd}" stroke-width="1.5" stroke-dasharray="6 4"/>'
            + text(x + 16, y + 26, label, 15, "bold", fg, "start"))
def node(x, y, w, h, who, title, sub="", dashed=False):
    bg, bd, fg = C[who]
    dash = ' stroke-dasharray="7 5"' if dashed else ""
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#fff" stroke="{bd}" stroke-width="2.5"{dash}/>'
    if sub:
        s += text(x + w / 2, y + h / 2 - 4, title, 19, "bold", fg) + text(x + w / 2, y + h / 2 + 20, sub, 14, "normal", "#4b5563")
    else:
        s += text(x + w / 2, y + h / 2 + 7, title, 19, "bold", fg)
    return s
def arrow(x1, y1, x2, y2, label="", lx=None, ly=None, color="#374151", anchor="middle"):
    s = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2.5" marker-end="url(#ah)"/>'
    if label:
        lx = (x1 + x2) / 2 if lx is None else lx
        ly = (y1 + y2) / 2 - 8 if ly is None else ly
        s += text(lx, ly, label, 14, "bold", color, anchor)
    return s
def chip(x, y, t, who, w=None):
    bg, bd, fg = C[who]
    w = w or (len(t) * 15 + 24)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="28" rx="14" fill="{bg}" stroke="{bd}" stroke-width="1.5"/>'
            + text(x + w / 2, y + 19, t, 14, "bold", fg))
def page(name, w, h, body, title):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{FONT}">'
           '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker></defs>'
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>' + text(24, 38, title, 22, "bold", "#111827", "start") + body + "</svg>")
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    (OUT / f"{name}.html").write_text(f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0}}</style>{svg}', encoding="utf-8")

# 1. 전체 구성과 담당
b = ""
b += group(20, 60, 1060, 190, "주문", "주문 처리 담당")
b += node(45, 110, 215, 100, "주문", "고객 · 관리자 화면", "주문하고 진행 보기")
b += node(310, 110, 215, 100, "주문", "웹 서버 · DB", "주문 저장, 화면에 알림")
b += node(575, 110, 215, 100, "주문", "브리지", "웹과 로봇 쪽을 이어 줌")
b += node(840, 110, 215, 100, "주문", "central", "할 일 목록(주문판) 관리")
b += arrow(260, 160, 308, 160, "주문", ly=150)
b += arrow(525, 160, 573, 160)
b += arrow(790, 160, 838, 160, "넘기기", ly=150)
b += group(20, 290, 1060, 140, "관제", "관제 담당")
b += node(380, 330, 440, 80, "관제", "fleet (관제)", "어느 로봇이 언제 무엇을 할지 정함")
b += arrow(930, 212, 760, 328, "① 할 일 목록", lx=905, ly=275, anchor="start")
b += arrow(470, 328, 650, 212, "② 진행 보고", lx=500, ly=275, anchor="end")
b += group(20, 470, 340, 170, "관제", "관제 담당")
b += group(380, 470, 340, 170, "양팔", "양팔 담당")
b += group(740, 470, 340, 170, "출고", "출고 담당")
b += node(60, 520, 260, 90, "관제", "Pode 3대", "바구니를 싣고 다니는 운반 로봇", dashed=True)
b += node(420, 520, 260, 90, "양팔", "Phody (양팔)", "바구니에 상품을 담음", dashed=True)
b += node(780, 520, 260, 90, "출고", "Phony (출고)", "찬 바구니를 내보냄", dashed=True)
b += arrow(470, 412, 190, 518) + arrow(600, 412, 550, 518) + arrow(730, 412, 910, 518)
b += text(40, 458, "로봇에게 지시 · 로봇 상태 받기", 14, "bold", "#374151", "start")
b += text(24, 672, "점선 로봇: 이번 주에는 가짜 로봇(시뮬레이션)으로 흐름을 확인하고, 다음 주에 실제 로봇으로 바꿉니다.", 15, "normal", "#4b5563", "start")
page("1_전체_구성과_담당", 1100, 692, b, "누가 무엇을 맡나요")

# 2. 주문 한 건의 여정
steps = [
    ("주문 넣기", "고객이 화면에서 주문하면 웹이 DB에 저장", ["주문"], "접수됨"),
    ("로봇 쪽으로 넘기기", "브리지가 주문(바구니 단위)을 central에 전달", ["주문"], "접수됨"),
    ("할 일 목록에 올리기", "central이 목록을 fleet에 보냄, 들어온 순서대로", ["주문", "관제"], "접수됨"),
    ("로봇 배정 · 상품 담기", "fleet이 Pode를 작업대로 보내고 Phody가 상품을 담음", ["관제", "양팔"], "준비 중"),
    ("출고로 운반", "다 담은 바구니를 Pode가 출고 쪽으로 옮김", ["관제"], "출고 중"),
    ("바구니 내보내기", "Phony가 찬 바구니를 빼서 내보냄", ["출고"], "출고 완료"),
    ("목록에서 빼기", "fleet이 끝났다고 알리면 central이 목록에서 뺌", ["관제", "주문"], "출고 완료"),
]
b = ""
y0, rh = 110, 74
b += text(70, 88, "단계", 15, "bold", "#6b7280", "start") + text(380, 88, "누가", 15, "bold", "#6b7280", "start") + text(800, 88, "고객 화면", 15, "bold", "#6b7280", "start")
for i, (t, d, whos, scr) in enumerate(steps):
    y = y0 + i * rh
    b += f'<rect x="20" y="{y}" width="1060" height="{rh-10}" rx="12" fill="{"#fafafa" if i % 2 else "#ffffff"}" stroke="#e5e7eb"/>'
    b += f'<circle cx="48" cy="{y+32}" r="17" fill="#111827"/>' + text(48, y + 38, str(i + 1), 16, "bold", "#fff")
    b += text(78, y + 28, t, 18, "bold", "#111827", "start") + text(78, y + 52, d, 14, "normal", "#4b5563", "start")
    cx = 500
    for wv in whos:
        b += chip(cx, y + 18, wv, wv, 64); cx += 74
    b += chip(800, y + 18, scr, "회색", 120)
    if i < len(steps) - 1:
        b += f'<line x1="48" y1="{y+50}" x2="48" y2="{y+rh+14}" stroke="#9ca3af" stroke-width="2"/>'
bx = 945
b += f'<rect x="{bx}" y="{y0+3*rh}" width="125" height="{3*rh-10}" rx="12" fill="#e6f6ec" stroke="#2e9a55" stroke-width="1.5"/>'
for k, line in enumerate(["단계가 바뀔", "때마다 fleet이", "진행 보고", "→ 화면이", "바로 바뀜"]):
    b += text(bx + 62, y0 + 3 * rh + 50 + k * 26, line, 14, "bold" if k == 2 else "normal", "#165c30")
page("2_주문_한_건의_흐름", 1100, y0 + len(steps) * rh + 20, b, "주문 한 건은 이렇게 흘러갑니다")

# 3. 진행 보고를 잃지도 겹치지도 않게
b = ""
def panel(x, title, sub):
    return (f'<rect x="{x}" y="60" width="340" height="360" rx="16" fill="#f9fafb" stroke="#d1d5db"/>'
            + text(x + 170, 96, title, 18, "bold", "#111827") + text(x + 170, 120, sub, 14, "normal", "#4b5563"))
def note(x, y, t, who="관제", w=120):
    bg, bd, fg = C[who]
    return f'<rect x="{x}" y="{y}" width="{w}" height="38" rx="8" fill="#fff" stroke="{bd}" stroke-width="2"/>' + text(x + w / 2, y + 25, t, 15, "bold", fg)
# A 번호
b += panel(20, "① 보고마다 번호를 붙임", "몇 번째 소식인지 알 수 있음")
for k, t in enumerate(["7-1 배정됨", "7-2 담는 중", "7-3 출고 중", "7-4 출고 완료"]):
    b += note(50, 150 + k * 60, t, w=150)
    b += arrow(202, 169 + k * 60, 268, 169 + k * 60)
b += f'<rect x="270" y="150" width="70" height="218" rx="10" fill="#e8f1fd" stroke="#2f6fd0" stroke-width="2"/>' + text(305, 265, "브리지", 15, "bold", "#163f80")
b += text(190, 400, "앞 숫자 7 = fleet이 켜진 차수", 14, "normal", "#4b5563")
# B 다시 보내기
x = 380
b += panel(x, "② 답이 없으면 다시 보냄", "같은 번호는 한 번만 반영")
b += note(x + 30, 160, "7-2 담는 중", w=140) + arrow(x + 172, 179, x + 240, 179) + text(x + 290, 184, "반영 ✓", 15, "bold", "#165c30")
b += text(x + 170, 230, "답이 사라짐 → 같은 소식을 다시 보냄", 14, "normal", "#4b5563")
b += note(x + 30, 260, "7-2 담는 중", w=140) + arrow(x + 172, 279, x + 240, 279) + text(x + 290, 284, "이미 받음", 15, "bold", "#6b7280")
b += text(x + 170, 350, "화면이 두 번 바뀌지 않고,", 14, "normal", "#4b5563") + text(x + 170, 372, "소식을 잃지도 않음", 14, "normal", "#4b5563")
# C 재시작
x = 740
b += panel(x, "③ fleet이 다시 켜지면", "새 차수 8로 시작")
b += note(x + 30, 160, "8-1 담는 중", w=140) + arrow(x + 172, 179, x + 240, 179) + text(x + 290, 184, "반영 ✓", 15, "bold", "#165c30")
b += text(x + 170, 230, "꺼지기 전 늦게 온 옛 소식", 14, "normal", "#4b5563")
b += note(x + 30, 260, "7-3 출고 중", w=140) + arrow(x + 172, 279, x + 240, 279) + text(x + 290, 284, "덮어쓰지 않음", 14, "bold", "#b91c1c")
b += text(x + 170, 350, "최신 상태가 옛 소식에", 14, "normal", "#4b5563") + text(x + 170, 372, "밀려나지 않음", 14, "normal", "#4b5563")
page("3_진행_보고_규칙", 1100, 440, b, "진행 보고를 잃지도, 겹치지도 않게")
print("ok")
