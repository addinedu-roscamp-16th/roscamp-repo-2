# 설계 산출물 (design)

IFP(Intelligent Flying Pod) 2팀의 아키텍처 · 다이어그램 원본을 모아 이력을 관리합니다.
개발 문서(개발 규칙, 관제 동작 등)는 `docs/`에 둡니다.

## 현재 문서

| 문서 | 파일 | 버전 | 작성일 | 이전 파일 이름 |
|---|---|---|---|---|
| HW 아키텍처 | [architecture/hw_architecture.drawio](architecture/hw_architecture.drawio) | v1.18 | 2026-11-17 | hw_architecture_r18.drawio |
| SW 아키텍처 | [architecture/sw_architecture.drawio](architecture/sw_architecture.drawio) | v1.15 | 2026-11-17 | sw_architecture_r15.drawio |
| 상태 · 순서도 · 시퀀스 다이어그램 | [diagrams/IFP_다이어그램.drawio](diagrams/IFP_다이어그램.drawio) | v2.4 | 2026-11-18 | IFP(Intelligent Flying Pod)_다이어그램_r7.drawio |
| 비상정지 순서도 | [diagrams/비상정지_순서도.drawio](diagrams/비상정지_순서도.drawio) | v1.1 | 2026-11-17 | 비상정지_순서도_r1.drawio |
| 관리자 페이지 스토리보드 | [diagrams/관리자_스토리보드.drawio](diagrams/관리자_스토리보드.drawio) | v1.3 | 2026-11-19 | - |
| 사용자 주문 페이지 스토리보드 | [diagrams/사용자_스토리보드.drawio](diagrams/사용자_스토리보드.drawio) | v1.0 | 2026-11-19 | - |
| 다이어그램 수정 요청 | [다이어그램_수정_요청.md](다이어그램_수정_요청.md) | - | 2026-11-19 | - |

변경 내용은 [CHANGELOG.md](CHANGELOG.md)에 적습니다.

## 디렉터리

```
design/
├─ README.md        문서 목록 · 현재 버전 · 관리 규칙 (이 파일)
├─ CHANGELOG.md     문서별 변경 이력
├─ 다이어그램_수정_요청.md  다이어그램에 반영할 일 모음 (한꺼번에 반영 요청)
├─ architecture/    HW · SW 아키텍처
├─ diagrams/        상태 · 순서도 · 시퀀스, 비상정지 순서도, 관리자 · 사용자 스토리보드
├─ storyboard_tools/ 스토리보드 생성 · 검사 스크립트 (README 참고)
└─ exports/         제출용 PNG · zip (제출할 때만 갱신)
```

## 관리 규칙

1. **파일 이름은 바꾸지 않습니다.** `_r18` 같은 번호를 붙이지 않고, 이전 판은 git 이력으로 봅니다.
2. **버전은 문서마다 `v주.부`로 붙입니다.**
   - 고칠 때마다 부 번호를 올립니다 (v1.7 → v1.8).
   - 구조를 크게 바꿀 때만 주 번호를 올립니다 (v1.8 → v2.0).
   - 처음 등록할 때는 이전 `_rN` 번호를 그대로 옮겼습니다 (r18 → v1.18).
3. **버전과 작성일은 세 곳에 함께 고칩니다.**
   - draw.io 각 탭 왼쪽 위의 라벨 (`v1.7 · 2026-11-17`)
   - 이 README의 "현재 문서" 표
   - CHANGELOG.md
4. **브랜치는 `docs/design`을 씁니다.** 팀이 이 방식에 합의하면 PR로 main에 합칩니다.
5. **커밋 메시지**는 팀 규칙(Conventional Commits)을 따릅니다.
   - 예: `docs(design): 시나리오 다이어그램 v1.8 — 6-8 비상정지 추가`
6. **제출할 때는 태그를 남깁니다.**
   - 제출용 PNG · zip을 `exports/`에 넣어 커밋한 뒤, 그 커밋에 주석 태그를 붙입니다.
   - 태그 이름: `submit/<제출일>-<대상>` (예: `submit/2026-11-24-scenario`)
   - 태그 설명에는 그때의 문서별 버전을 적습니다.

```bash
git tag -a submit/2026-11-24-scenario -m "시나리오 제출: IFP_다이어그램 v1.8, 비상정지_순서도 v1.1"
git push origin submit/2026-11-24-scenario
```

## 제출본 (exports/)

제출할 때는 제출 이름 규칙(`2팀_<종류>_<번호>_<이름>.png`)을 따르므로 draw.io 파일 · 탭 이름과 다릅니다. 아래 표로 서로 찾습니다.
버전은 제출할 때의 마지막 `_rN` 판입니다.

| 제출일 | 제출 파일 | draw.io 파일 · 탭 | 버전 |
|---|---|---|---|
| 2026-11-10 | 2팀_HW_01.png | architecture/hw_architecture.drawio | v1.15 |
| 2026-11-10 | 2팀_SW_01.png | architecture/sw_architecture.drawio | v1.10 |
| 2026-11-13 | 2팀_HW_02.png | architecture/hw_architecture.drawio | v1.16 |
| 2026-11-13 | 2팀_SW_02.png | architecture/sw_architecture.drawio | v1.13 |
| 2026-11-13 | 2팀_StateDiagram_0_상태_목록.png | diagrams/IFP_다이어그램.drawio · 0. 상태 목록 | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_2_주문.png | 〃 · 2. 주문 | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_3_Pode.png | 〃 · 3. Pode | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_3-1_Pode_장애_예외.png | 〃 · 3-1. Pode 장애 예외 | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_4_Phody_가이드.png | 〃 · 4. Phody (가이드) | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_4-1_Phody_장애_예외.png | 〃 · 4-1. Phody 장애 예외 | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_5_Phony.png | 〃 · 5. Phony | v1.7 |
| 2026-11-13 | 2팀_StateDiagram_5-1_Phony_장애_예외.png | 〃 · 5-1. Phony 장애 예외 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-1_정상_흐름.png | 〃 · 6-1. 시퀀스: 정상 흐름 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-2_결품.png | 〃 · 6-2. 시퀀스: 결품 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-3_빈_바구니_부족.png | 〃 · 6-3. 시퀀스: 빈 바구니 부족 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-4_주문_취소.png | 〃 · 6-4. 시퀀스: 주문 취소 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-5_Pode_장애.png | 〃 · 6-5. 시퀀스: Pode 장애 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-6_Phody_장애.png | 〃 · 6-6. 시퀀스: Phody 장애 | v1.7 |
| 2026-11-13 | 2팀_SequenceDiagram_6-7_Phony_장애.png | 〃 · 6-7. 시퀀스: Phony 장애 | v1.7 |
| 2026-11-16 | 2팀_HW_03.png | architecture/hw_architecture.drawio | v1.18 |
| 2026-11-16 | 2팀_SW_03.png | architecture/sw_architecture.drawio | v1.15 |
| 2026-11-17 | 비상정지_순서도_r1-0. 구성.drawio.png | diagrams/비상정지_순서도.drawio · 0. 구성 | v1.1 |
| 2026-11-17 | 비상정지_순서도_r1-1. 비상정지 발생.drawio.png | 〃 · 1. 비상정지 발생 | v1.1 |
| 2026-11-17 | 비상정지_순서도_r1-2. 현장 정리.drawio.png | 〃 · 2. 현장 정리 | v1.1 |
| 2026-11-17 | 비상정지_순서도_r1-3. 해제 · 재개.drawio.png | 〃 · 3. 해제 · 재개 | v1.1 |
| 2026-11-18 | Scenario_2nd/2팀_StateDiagram_01-0. 상태표.png | diagrams/IFP_다이어그램.drawio · 0. 상태표 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_StateDiagram_01-2. 주문.png | 〃 · 2. 주문 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_StateDiagram_01-3. Pode.png | 〃 · 3. Pode | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_StateDiagram_01-4. Phody (가이드).png | 〃 · 4. Phody (가이드) | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_StateDiagram_01-5. Phony.png | 〃 · 5. Phony | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-1-1. 시퀀스_ 주문.png | 〃 · 6-1-1. 시퀀스: 주문 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-1-2. 시퀀스_ 상품 담기.png | 〃 · 6-1-2. 시퀀스: 상품 담기 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-1-3. 시퀀스_ 출고.png | 〃 · 6-1-3. 시퀀스: 출고 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-2. 시퀀스_ 결품.png | 〃 · 6-2. 시퀀스: 결품 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-4. 시퀀스_ 주문 취소.png | 〃 · 6-4. 시퀀스: 주문 취소 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-5. 시퀀스_ Pode 장애.png | 〃 · 6-5. 시퀀스: Pode 장애 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-6. 시퀀스_ Phody 장애.png | 〃 · 6-6. 시퀀스: Phody 장애 | v2.4 |
| 2026-11-18 | Scenario_2nd/2팀_SequenceDiagram_01-6-7. 시퀀스_ Phony 장애.png | 〃 · 6-7. 시퀀스: Phony 장애 | v2.4 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-0. 화면 구성.drawio.png | diagrams/관리자_스토리보드.drawio · 0. 화면 구성 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-1. 평상시 현황.drawio.png | 〃 · 1. 평상시 현황 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-2. 결품 · 담기 일시 중지.drawio.png | 〃 · 2. 결품 · 담기 일시 중지 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-3. Pode 장애.drawio.png | 〃 · 3. Pode 장애 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-4. Phody 장애.drawio.png | 〃 · 4. Phody 장애 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-5. Phony 장애.drawio.png | 〃 · 5. Phony 장애 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-6. 서비스 일시 정지 · 재시작.drawio.png | 〃 · 6. 서비스 일시 정지 · 재시작 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-7. 재고 · 이력.drawio.png | 〃 · 7. 재고 · 이력 | v1.2 |
| 2026-11-19 | 스토리보드_1st/관리자_스토리보드-8. 비상정지.drawio.png | 〃 · 8. 비상정지 | v1.2 |

- 상태 다이어그램의 "1. 순서도" 탭은 제출하지 않았습니다.
- 2026-11-18 제출본(`exports/Scenario_2nd/`)은 Confluence Scenario 페이지 첨부와 같은 이름입니다.
- 비상정지 순서도 PNG는 Confluence 첨부와 같은 이름으로 두었습니다.
- 2026-11-19 관리자 스토리보드 제출본(`exports/스토리보드_1st/`)은 draw.io 내보내기 이름 그대로이며, Confluence 첨부와 같은 이름입니다.

## draw.io 버전 라벨 고치기

각 탭 왼쪽 위의 회색 글자(`v2.4 · 2026-11-18`)가 버전 라벨입니다. 탭마다 하나씩 있으니, 버전을 올릴 때 모든 탭의 라벨을 함께 고칩니다.
draw.io에서 편집 → 찾기/바꾸기(Ctrl+F)로 `v2.4 · 2026-11-18`을 찾아 "모두 바꾸기"를 하면 한 번에 고칠 수 있습니다.
