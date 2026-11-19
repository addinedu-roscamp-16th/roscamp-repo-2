# 스토리보드 생성 도구

관리자 · 고객 스토리보드(`design/diagrams/*_스토리보드.drawio`)를 만드는 Python 스크립트입니다. 표준 라이브러리만 씁니다 (미리보기만 PIL · 나눔고딕 글꼴 필요).

| 파일 | 하는 일 |
|---|---|
| `sb_core.py` | 공통: 스타일, 장면 칸(화면 + 설명 세 줄), 탭 배치 · 화살표 · 노트, 로그인 화면, draw.io 쓰기 |
| `admin_screen.py` · `admin_scenes.py` · `admin_build.py` · `admin_check.py` | 관리자 스토리보드: 화면 그리기 · 장면 명세 · 생성 · 검사 |
| `user_screen.py` · `user_scenes.py` · `user_build.py` · `user_check.py` | 고객 주문 페이지 스토리보드: 화면 그리기 · 장면 명세 · 생성 · 검사 |
| `tests/` | 고객 검사기 시험 |
| `render.py` | 확인용 근사 미리보기 PNG (글자 겹침 확인용, 제출용 아님) |

## 쓰는 법 (저장소 루트에서)

```bash
python3 design/storyboard_tools/user_build.py
python3 design/storyboard_tools/user_check.py design/diagrams/사용자_스토리보드.drawio
python3 -m unittest discover -s design/storyboard_tools/tests
python3 design/storyboard_tools/render.py design/diagrams/사용자_스토리보드.drawio "3." /tmp/tab3.png
```

관리자 스토리보드는 `admin_build.py` · `admin_check.py`로 같은 방법입니다.

## 주의

- 관리자 스토리보드의 2 · 3 · 4번 탭은 draw.io에서 손으로 고친 배치가 있어, `admin_build.py`가 기본으로 기존 파일의 탭을 그대로 가져오고 버전 라벨만 바꿉니다. 새로 그리려면 `--fresh`.
- **다시 만들면 draw.io에서 손으로 고친 내용이 덮어써집니다.** 장면을 고칠 때는 `*_scenes.py`를 고치고 다시 만드는 것을 기본으로 합니다. draw.io에서 직접 고쳤다면 그 내용을 장면 명세에도 옮긴 뒤 다시 만드세요.
- 화면 안 문구 규칙(고객 화면의 상태 말 7개, [취소]는 접수됨 줄에만, 내부 말 금지)은 `user_check.py`가 막습니다. 규칙을 바꾸면 설계 문서(`docs/superpowers/specs/2026-11-19-user-storyboard-design.md`)도 함께 고칩니다.
- 버전 라벨은 `*_build.py`의 `VER`, 검사기의 `VERSION`을 함께 올립니다.
