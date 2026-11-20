# jetcobot_dual_app (PC)

여러 상품을 스테레오로 인식하고, 왼팔과 오른팔이 번갈아 집어 바구니에 담는 UI와 worker다. 인터페이스는 [../INTERFACES.md](../INTERFACES.md) 4절에 있다.

```bash
ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left
```

| launch 인자 | 기본값 |
| --- | --- |
| `python` | `~/camera_code/.venv/bin/python` |
| `script` | `multi_pick/sam_multi_real` |
| `first_arm` | `left` |
| `args` | `--stereo-fixed-see-center` |

| 폴더 | 내용 |
| --- | --- |
| `scripts/multi_pick/` | `sam_multi_real.py`(UI·흐름), `state_machine.py`(팔별 상태와 구역 점유), `stereo_self_correct.py`(매 캡처 오른쪽 카메라 회전 보정), 다중 ROI 인식 |
| `scripts/moveit_pick/` | 단일 상품 인식과 계획 엔진(`sam_dual_yaw_moveit.py`), 접근(`move_dual_sam_yaw_real.py`), 미세조정·잡기(`move_right_sam_yaw.py`), 자동 미세조정, 쥔 물체 모델, 보정 도구, `calibration/` |
| `scripts/right_place_trial/` | PLACE(`coordinated_right_case.py`), `see_center` 복귀, 양팔 경로 진행 |
| `scripts/left_detail.py`, `left_mentos_detail.py` | 미세조정용 물체 검출 |

- 동작 흐름과 옵션의 자세한 설명은 [README_state_pick.md](README_state_pick.md)(원본 `state_pick/README.md`)에 있다.
- 테스트: `cd test && python -m pytest`. 이전 흐름을 가정한 테스트가 있어 갱신이 필요하다.
