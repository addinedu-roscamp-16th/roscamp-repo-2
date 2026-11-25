"""스프린트 3회차 연동 테스트 보고서 그림 (비개발자용).

python3 make.py → *.svg · *.html, 그다음 chromium으로 PNG(아래 PNG 줄).
모양 · 색은 design/diagrams/sprint4_guide/make.py와 같게 둔다.
"""
from pathlib import Path

OUT = Path(__file__).parent
FONT = "NanumSquare, 'Noto Sans CJK KR', sans-serif"
C = {  # 담당 색: (배경, 테두리, 글자)
    "주문": ("#e8f1fd", "#2f6fd0", "#163f80"),
    "관제": ("#e6f6ec", "#2e9a55", "#165c30"),
    "회색": ("#f3f4f6", "#9aa0a8", "#374151"),
}
OK, HOLD, OUTSIDE = "#2e9a55", "#d9822b", "#9aa0a8"  # 성공 · 보류 · 범위 밖


# ---- 그리기 도우미 ----
def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;")


def text(x, y, t, size=16, w="normal", fill="#1f2937", anchor="middle"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{w}" fill="{fill}" '
            f'text-anchor="{anchor}">{esc(t)}</text>')


def group(x, y, w, h, who, label):
    bg, bd, fg = C[who]
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{bg}" stroke="{bd}" '
            f'stroke-width="1.5" stroke-dasharray="6 4"/>' + text(x + 16, y + 26, label, 15, "bold", fg, "start"))


def node(x, y, w, h, who, title, sub="", dashed=False):
    _, bd, fg = C[who]
    dash = ' stroke-dasharray="7 5"' if dashed else ""
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#fff" stroke="{bd}" stroke-width="2.5"{dash}/>'
    if sub:
        return s + text(x + w / 2, y + h / 2 - 4, title, 18, "bold", fg) + text(x + w / 2, y + h / 2 + 19, sub, 13, "normal", "#4b5563")
    return s + text(x + w / 2, y + h / 2 + 7, title, 18, "bold", fg)


def arrow(x1, y1, x2, y2, label="", color=OK, lx=None, ly=None, anchor="middle", dashed=False):
    mark = {OK: "ok", HOLD: "hold", OUTSIDE: "out"}.get(color, "ok")
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    s = (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="3"{dash} '
         f'marker-end="url(#ah-{mark})"/>')
    if label:
        lx = (x1 + x2) / 2 if lx is None else lx
        ly = (y1 + y2) / 2 - 9 if ly is None else ly
        s += text(lx, ly, label, 14, "bold", color, anchor)
    return s


def chip(x, y, t, color, w=None):
    w = w or (len(t) * 14 + 26)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="28" rx="14" fill="#fff" stroke="{color}" stroke-width="2"/>'
            + text(x + w / 2, y + 19, t, 14, "bold", color))


def page(name, w, h, body, title):
    marks = "".join(
        f'<marker id="ah-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        for k, c in (("ok", OK), ("hold", HOLD), ("out", OUTSIDE)))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
           f'font-family="{FONT}"><defs>{marks}</defs><rect width="{w}" height="{h}" fill="#ffffff"/>'
           + text(24, 38, title, 22, "bold", "#111827", "start") + body + "</svg>")
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    (OUT / f"{name}.html").write_text(
        f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0}}</style>{svg}', encoding="utf-8")


# ---- 1. 시험 범위와 결과 ----
b = ""
b += group(20, 60, 440, 330, "주문", "주문 처리 담당")
b += node(40, 110, 160, 92, "주문", "주문 넣기", "시험 도구, 1건")
b += node(280, 110, 160, 92, "주문", "central", "할 일 목록(주문판)")
b += node(40, 270, 160, 92, "주문", "DB", "주문 · 진행 상태")
b += node(280, 270, 160, 92, "주문", "브리지", "로봇 소식을 DB로")
b += group(500, 60, 580, 330, "관제", "관제 담당 (fleet)")
b += node(560, 110, 180, 92, "관제", "주문 코어", "고르기 · 호출 · 배정")
b += node(880, 110, 180, 92, "관제", "교통 층", "Pode 고르기 · 이동")
b += node(560, 270, 180, 92, "관제", "배정 조회", "어느 Pode에 어떤 주문")
b += node(880, 270, 180, 92, "관제", "Pode 2대", "가짜 로봇, 실 주행 없음", dashed=True)
b += arrow(200, 156, 278, 156, "T-01")
b += arrow(120, 202, 120, 268, "T-01", lx=130, ly=240, anchor="start")
b += arrow(440, 135, 558, 135, "T-03")
b += arrow(558, 180, 442, 180, "T-07", ly=200)
b += arrow(740, 135, 878, 135, "T-04 호출")
b += arrow(878, 180, 742, 180, "T-06 도착 응답", ly=202)
b += arrow(970, 202, 970, 268, "T-05 이동", lx=982, ly=240, anchor="start")
b += arrow(650, 202, 650, 268, "T-08", lx=662, ly=240, anchor="start")
b += arrow(558, 316, 442, 316, "T-10 보류", color=HOLD, ly=305, dashed=True)
b += arrow(280, 316, 202, 316, "T-02", ly=305)
b += text(24, 425, "T-03 주문판 전달 · T-07 배정 알림 · T-08 배정 조회 · T-09 (예외) 바구니 용량(4개) 넘는 주문은 경고만, 다른 주문은 배정", 14, "normal", "#374151", "start")
lx = 24
for t, c in (("성공", OK), ("보류", HOLD)):
    b += f'<line x1="{lx}" y1="460" x2="{lx + 44}" y2="460" stroke="{c}" stroke-width="3"/>' + text(lx + 54, 465, t, 15, "bold", c, "start")
    lx += 120
b += text(lx, 465, "이번 범위 밖: 상품 담기 · 출고 · 실제 로봇 주행", 15, "normal", "#6b7280", "start")
page("1_시험_범위와_결과", 1100, 490, b, "무엇을 시험했고, 결과는 어떤가요")


# ---- 2. 주문 한 건이 배정되기까지 (ROS 연동 실행 기록) ----
# (주문판에 들어온 뒤 초, 제목, 설명, 글 위치: up · down · none, 글 정렬)
events = [
    (0.00, "주문판에 올라옴", "central → fleet", "up", "start"),
    (0.06, "Pode 선택 · 출발", "pode2에 작업대 이동 지시", "down", "start"),
    (1.35, "", "", "none", "middle"),
    (10.95, "중간 지점 도착", "작업대 구역 통행 허가 받음", "down", "middle"),
    (14.14, "", "", "none", "middle"),
    (15.94, "작업대 앞 도착", "", "up", "middle"),
    (19.04, "도킹 · 배정 완료", "주문이 pode2에 묶임", "down", "middle"),
]
x0, x1, ty = 70, 1040, 190
scale = (x1 - x0) / 20.0
b = ""
b += (f'<rect x="{x0 + 0.06 * scale}" y="{ty - 10}" width="{(15.94 - 0.06) * scale}" height="20" rx="10" '
      'fill="#e6f6ec"/>')
b += text(x0 + 8.0 * scale, ty + 5, "pode2 이동: 4구간 (가짜 로봇)", 13, "bold", "#165c30")
b += f'<line x1="{x0}" y1="{ty + 26}" x2="{x1}" y2="{ty + 26}" stroke="#9ca3af" stroke-width="2"/>'
for s in range(0, 21, 5):
    x = x0 + s * scale
    b += (f'<line x1="{x}" y1="{ty + 20}" x2="{x}" y2="{ty + 32}" stroke="#9ca3af" stroke-width="2"/>'
          + text(x, ty + 50, f"{s}초", 13, "normal", "#6b7280"))
for sec, title, sub, where, anchor in events:
    x = x0 + sec * scale
    color = OK if sec in (0.0, 19.04) else "#374151"
    r = 7 if where != "none" else 5
    b += f'<circle cx="{x}" cy="{ty + 26}" r="{r}" fill="{color}"/>'
    tx = x - 6 if anchor == "start" else x
    if where == "up":
        b += f'<line x1="{x}" y1="{ty + 18}" x2="{x}" y2="{ty - 40}" stroke="#d1d5db" stroke-width="1.5"/>'
        b += text(tx, ty - 66, title, 15, "bold", "#111827", anchor) + text(tx, ty - 47, sub, 13, "normal", "#4b5563", anchor)
    elif where == "down":
        b += f'<line x1="{x}" y1="{ty + 34}" x2="{x}" y2="{ty + 74}" stroke="#d1d5db" stroke-width="1.5"/>'
        b += text(tx, ty + 94, title, 15, "bold", "#111827", anchor) + text(tx, ty + 113, sub, 13, "normal", "#4b5563", anchor)
b += text(24, 350, "기록: ROS 토픽을 그대로 녹화(rosbag)한 실제 실행. 이동 시간은 가짜 로봇 기준이라 실제 로봇과 다를 수 있습니다.", 15, "normal", "#4b5563", "start")
page("2_주문_한_건이_배정되기까지", 1100, 370, b, "주문 한 건이 Pode에 배정되기까지 (약 19초)")
print("ok")

# PNG: for f in *.html; do chromium --headless --disable-gpu --hide-scrollbars \
#   --screenshot="$PWD/${f%.html}.png" --window-size=1100,<높이> "file://$PWD/$f"; done
