"""고객 주문 페이지 스토리보드 생성. usage: python3 user_build.py [out.drawio]"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sb_core import build_tab, write  # noqa: E402
from user_scenes import TABS  # noqa: E402
from user_screen import screen  # noqa: E402

OUT = os.path.join(HERE, '..', 'diagrams', '사용자_스토리보드.drawio')
VER = 'v1.0 · 2026-11-19'

if __name__ == '__main__':
    write(sys.argv[1] if len(sys.argv) > 1 else OUT, [build_tab(s, VER, screen) for s in TABS])
    print('written', len(TABS), 'tabs')
