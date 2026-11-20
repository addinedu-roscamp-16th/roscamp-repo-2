# `state_pick` 사본 안내

아래는 복사 전 `multi_pick` 사용법이다. `state_pick`의 교대 실행 흐름과 검증 범위는 [상위 README](../README.md)를 먼저 읽는다.

# multi_pick — 같은 초코바 여러 개 (실물, 2026-11-19 시작)

단일 물체 시스템(`../moveit_pick`, `../right_place_trial`, `~/pinky_ws/...`)은 **수정하지 않는다.**
이 폴더는 그 사본/확장이다.

- `sam_multi_real.py`: 실물 픽 UI(`moveit_pick/sam_dual_yaw_moveit.py`)를 상속해 ROI 선택과 인식만 교체.
  인식 후 물체들을 큐로 만들고, 물체마다 기존 단일 체인(팔 자동 선택 → 접근 → 자동 미세조정 →
  하강·닫기·리프트 → 케이스 놓기 → see_center 복귀)을 그대로 실행한 뒤 다음 물체로 자동 진행.
- `multi_roi_perception.py`, `multi_roi_geometry.py`, `rotation_matching.py`, `surface_geometry.py`:
  가제보 `~/pinky_ws/src/jetcobot_gazebo_with_gripper/src/jetcobot_mult_geazbo/scripts/`에서 복사.
  `multi_roi_perception.py`만 수정: 실물 모듈 함수 사용, 초코바 크기 범위(길이 50~120, 폭 15~50 mm,
  점 퍼짐 140 mm), 윗면 높이 prior(`floor_z_m + object_height_mm`, 기본 7+15 = 22 mm; 단일 인식 로그의
  초코바 윗면 Z ≈ 21~24 mm 기준).

## 실행
```bash
~/camera_code/.venv/bin/python ~/camera_code/multi_pick/sam_multi_real.py --stereo-fixed-see-center
# 계획 후 접근을 자동으로 시작하려면 --auto-execute 추가 (처음엔 빼고 E로 확인 권장)
```
1. 왼쪽 드래그로 화면 고정 → **물체마다 ROI 하나씩** 왼쪽·오른쪽 영상에 각각 그림(순서 무관, 각 최대 12). 오른쪽 클릭 = 삭제.
2. `M`: 인식(물체별 SAM → LightGlue → 좌우 전역 일대일 연결). 결과 창에 같은 색/번호로 짝 표시, 애매하면 주황.
3. 확정 물체가 큐가 되고 첫 물체 계획 시작 → `E`(또는 `--auto-execute`) → 이후는 단일 체인과 동일 → 놓기 성공 시 다음 물체 자동 시작.
4. 물체는 **처음 한 번만 인식**(사용자 결정). 다음 물체 시작 시 캡처 시각·관절만 현재값으로 갱신해 기존 "오래된 캡처" 검사를 통과시킨다. 물체가 밀리면 감지 못한다.
5. 놓기 실패 시 큐 정지(`MULTI STOPPED`), `C`로 복귀, `R`은 큐를 버리고 새 캡처.

## 아직 안 된 것 / 주의
- 인식 임계값(재투영 8 px, 에피폴라 6 px, 높이 허용 10 mm, 최소 점 8)은 실물에서 **미검증**. 옵션: `--reproj-px --epipolar-px --height-tolerance-mm --object-height-mm --floor-z-m --min-valid-points-multi`.
- 한 번에 한 팔만 작업. **양팔 동시 진행**은 다음 단계(12관절 결합 계획 필요; gate는 실행 하나만 받음).
- 다른 물체를 집는 중 이미 놓을 물체가 팔에 닿는지 등 물체 간 충돌은 MoveIt에 넣지 않았다.
