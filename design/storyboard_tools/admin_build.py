"""관리자 스토리보드 생성. usage: python3 admin_build.py [out.drawio] [--fresh]

2 · 3 · 4번 탭은 draw.io에서 손으로 고친 배치(화살표 경로 등)가 있다 (설계 9절 v1.3 "만드는 법").
그래서 기본으로 out 파일이 이미 있으면 그 탭들의 <diagram>은 기존 파일에서 그대로 가져오고
버전 라벨만 VER로 바꾼다. 장면 명세(admin_scenes.py)로 다시 그리려면 --fresh.
"""
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from admin_scenes import TABS  # noqa: E402
from admin_screen import screen  # noqa: E402
from sb_core import build_tab, write  # noqa: E402

OUT = os.path.join(HERE, '..', 'diagrams', '관리자_스토리보드.drawio')
VER = 'v1.4 · 2026-11-19'
HAND_EDITED = ('2.', '3.', '4.')  # 탭 이름 앞부분


def tidy_points(d):
    """화살표 꺾는 점 좌표를 정수로 (draw.io가 저장한 모양과 같게: 1570.0 → 1570)."""
    for p in d.iter('mxPoint'):
        for k in ('x', 'y'):
            v = p.get(k)
            if v is not None and float(v) == int(float(v)):
                p.set(k, str(int(float(v))))
    return d


def splice(old_path, tabs):
    """손으로 고친 탭은 기존 파일의 것을 쓰고 버전 라벨만 바꾼다."""
    old = {d.get('name'): d for d in ET.parse(old_path).getroot().findall('diagram')}
    out = []
    for d in tabs:
        name = d.get('name')
        if name.startswith(HAND_EDITED) and name in old:
            keep = old[name]
            for c in keep.iter('mxCell'):
                if c.get('id') == 'version-label':
                    c.set('value', VER)
            print('kept from existing file:', name)
            out.append(keep)
        else:
            out.append(d)
    return out


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    path = args[0] if args else OUT
    tabs = [tidy_points(build_tab(s, VER, screen)) for s in TABS]
    if '--fresh' not in sys.argv and os.path.exists(path):
        tabs = splice(path, tabs)
    write(path, tabs)
    print('written', len(tabs), 'tabs')
