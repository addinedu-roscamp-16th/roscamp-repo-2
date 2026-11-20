"""draw.io 다이어그램을 제출 이름 규칙에 맞춰 PNG로 한꺼번에 내보냅니다.

제출 이름 규칙 (2팀)
- 1차 제출: 2팀_<종류>.png            (예: 2팀_SequenceDiagram.png)
- 2차 제출: 2팀_<종류>_01.png         (3차는 _02, 4차는 _03 …)
- 한 종류에 탭이 여럿이면 뒤에 "-<탭 이름>"을 붙입니다 (탭 이름의 : / \\ * ? " < > | 는 _로).
  예: 2팀_SequenceDiagram_01-6-1-1. 시퀀스_ 주문.png
- 종류: SequenceDiagram · StateDiagram · ClassDiagram · ERDiagram · GUIDesign · InterfaceSpecification

usage (저장소 루트에서):
  python3 design/tools/export_png.py                 # 차수 · 출력 폴더를 물어보고, 목록을 보인 뒤 확인을 받음
  python3 design/tools/export_png.py <제출 차수> [<출력 폴더>] [--only 종류,…] [--scale 1] [--dry-run] [--force] [--yes]
  예: python3 design/tools/export_png.py 3 design/exports/Scenario_3rd
      python3 design/tools/export_png.py 2 design/exports/GUI_2nd --only GUIDesign --dry-run

어느 파일 · 탭이 어느 종류인지는 아래 RULES 표에서 정합니다. 새 다이어그램이 생기면 표에 한 줄 더합니다.
draw.io Desktop(`drawio` 명령)이 필요합니다. snap으로 설치한 draw.io는 /tmp에 쓸 수 없으니 출력 폴더는 저장소 안이나 홈 폴더로 둡니다.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
KINDS = ['SequenceDiagram', 'StateDiagram', 'ClassDiagram', 'ERDiagram', 'GUIDesign', 'InterfaceSpecification']

# file: 저장소 루트 기준 경로, kind: 종류, tabs: 탭 이름 앞부분 목록(없으면 모든 탭),
# label: 같은 종류에 파일이 여럿일 때 탭 이름 앞에 붙일 말, enabled: False면 내보내지 않음
RULES = [
    {'file': 'design/diagrams/IFP_다이어그램.drawio', 'kind': 'StateDiagram', 'tabs': ['0.', '2.', '3.', '4.', '5.']},
    {'file': 'design/diagrams/IFP_다이어그램.drawio', 'kind': 'SequenceDiagram', 'tabs': ['6-']},
    {'file': 'design/diagrams/관리자_스토리보드.drawio', 'kind': 'GUIDesign', 'label': '관리자'},
    {'file': 'design/diagrams/사용자_스토리보드.drawio', 'kind': 'GUIDesign', 'label': '사용자'},
    # 순서도는 제출하지 않음 (IFP "1. 순서도" 탭도 위에서 뺌). 내보내려면 enabled를 지우고 종류를 정함
    {'file': 'design/diagrams/비상정지_순서도.drawio', 'kind': 'StateDiagram', 'label': '비상정지', 'enabled': False},
]


def round_suffix(n):
    """제출 차수 → 번호. 1차 '', 2차 '_01', 3차 '_02' …"""
    if n < 1:
        raise ValueError('제출 차수는 1부터')
    return '' if n == 1 else f'_{n - 1:02d}'


def safe(name):
    return re.sub(r'[:/\\*?"<>|]', '_', name)


def file_name(kind, rnd, tab, label=None):
    tail = '-'.join(safe(p) for p in (label, tab) if p) if tab else ''
    return f'2팀_{kind}{round_suffix(rnd)}' + (f'-{tail}' if tail else '') + '.png'


def tab_names(path):
    return [d.get('name') for d in ET.parse(path).getroot().iter('diagram')]


def plan(rules, rnd, get_tabs, only=None):
    """내보낼 일 목록: [{'file', 'page'(1부터), 'tab', 'kind', 'out'}]."""
    picked = []
    for r in rules:
        if r.get('enabled', True) is False or (only and r['kind'] not in only):
            continue
        for i, t in enumerate(get_tabs(r['file']), 1):
            if 'tabs' not in r or any(t.startswith(p) for p in r['tabs']):
                picked.append((r, i, t))
    count = {}
    for r, _, _ in picked:
        count[r['kind']] = count.get(r['kind'], 0) + 1
    return [{'file': r['file'], 'page': i, 'tab': t, 'kind': r['kind'],
             'out': file_name(r['kind'], rnd, t if count[r['kind']] > 1 else None,
                              r.get('label') if count[r['kind']] > 1 else None)}
            for r, i, t in picked]


def ask_round(ask=input):
    """제출 차수를 물어봄. 1 이상의 정수가 나올 때까지 다시 물음."""
    while True:
        v = ask('제출 차수를 입력하세요 (1차는 번호 없음, 2차는 _01, 3차는 _02 …): ').strip()
        if v.isdigit() and int(v) >= 1:
            return int(v)
        print('1 이상의 숫자로 입력하세요.')


def default_out_dir(rnd):
    return f'design/exports/제출_{rnd}차'


def ask_out_dir(rnd, ask=input):
    v = ask(f'출력 폴더 (그냥 Enter면 {default_out_dir(rnd)}): ').strip()
    return v or default_out_dir(rnd)


def confirm(ask=input):
    return ask('내보낼까요? [Y/n]: ').strip().lower() in ('', 'y', 'yes', 'ㅛ')


def main(argv=None):
    ap = argparse.ArgumentParser(description='draw.io → 제출 이름 PNG 일괄 내보내기')
    ap.add_argument('round', type=int, nargs='?', help='제출 차수 (1차는 번호 없음, 2차는 _01 …). 빼면 물어봄')
    ap.add_argument('out_dir', nargs='?', help='출력 폴더 (저장소 루트 기준 또는 절대 경로). 빼면 물어봄')
    ap.add_argument('--only', help='이 종류만 (쉼표로): ' + ', '.join(KINDS))
    ap.add_argument('--scale', type=float, default=1, help='배율 (기본 1, 지난 제출본과 같은 크기)')
    ap.add_argument('--border', type=int, default=10, help='여백 px (기본 10)')
    ap.add_argument('--dry-run', action='store_true', help='이름만 보이고 내보내지 않음')
    ap.add_argument('--force', action='store_true', help='같은 이름 파일이 있어도 덮어씀')
    ap.add_argument('--yes', '-y', action='store_true', help='확인을 묻지 않고 바로 내보냄')
    a = ap.parse_args(argv)

    asked = a.round is None or a.out_dir is None
    if a.round is None:
        a.round = ask_round()
    elif a.round < 1:
        ap.error('제출 차수는 1부터')
    if a.out_dir is None:
        a.out_dir = ask_out_dir(a.round)

    only = a.only.split(',') if a.only else None
    if only and set(only) - set(KINDS):
        ap.error(f'모르는 종류: {sorted(set(only) - set(KINDS))}')
    jobs = plan(RULES, a.round, lambda f: tab_names(os.path.join(ROOT, f)), only)
    out_dir = a.out_dir if os.path.isabs(a.out_dir) else os.path.join(ROOT, a.out_dir)

    for j in jobs:
        print(f"{j['file']} · {j['tab']}  →  {j['out']}")
    print(f'{len(jobs)}장')
    print(f'출력 폴더: {a.out_dir}')
    if a.dry_run:
        return 0
    if asked and not a.yes and not confirm():
        print('취소했습니다.')
        return 0

    exists = [j['out'] for j in jobs if os.path.exists(os.path.join(out_dir, j['out']))]
    if exists and not a.force:
        print(f'이미 있는 파일 {len(exists)}개 (덮어쓰려면 --force): {exists[:3]} …', file=sys.stderr)
        return 1
    drawio = shutil.which('drawio')
    if not drawio:
        print('drawio 명령이 없습니다. draw.io Desktop을 설치하세요 (sudo snap install drawio).', file=sys.stderr)
        return 1
    if drawio.startswith('/snap/') and os.path.realpath(out_dir).startswith('/tmp'):
        print('snap으로 설치한 draw.io는 /tmp에 쓸 수 없습니다. 다른 출력 폴더를 고르세요.', file=sys.stderr)
        return 1
    os.makedirs(out_dir, exist_ok=True)

    fails = 0
    for n, j in enumerate(jobs, 1):
        out = os.path.join(out_dir, j['out'])
        cmd = [drawio, '-x', '-f', 'png', '-p', str(j['page']), '-s', str(a.scale), '-b', str(a.border),
               '-o', out, os.path.join(ROOT, j['file'])]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            ok = r.returncode == 0 and os.path.exists(out)
            err = (r.stderr or r.stdout).strip().splitlines()[-1:] if not ok else []
        except subprocess.TimeoutExpired:
            ok, err = False, ['180초 넘게 걸려 멈춤']
        print(f'[{n}/{len(jobs)}] {"OK " if ok else "실패"} {j["out"]}' + (f'  ({err[0]})' if err else ''))
        fails += not ok
    print('완료' if not fails else f'실패 {fails}장')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
