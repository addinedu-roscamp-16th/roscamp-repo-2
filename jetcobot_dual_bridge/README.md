# jetcobot_dual_bridge (Pi)

팔 하나를 맡는 Pi에서 실행한다. pymycobot으로 시리얼(1 Mbaud)을 독점하고, `/{side}` 이름 공간에 상태 토픽, 액션, 서비스를 제공한다. 인터페이스는 [../INTERFACES.md](../INTERFACES.md) 2절에 있다.

```bash
ros2 launch jetcobot_dual_bridge bridge.launch.py side:=right
```

| launch 인자 | 기본값 | 설명 |
| --- | --- | --- |
| `side` | (필수) | `left` 또는 `right` |
| `python` | `~/venv/mycobot/bin/python` | pymycobot이 있는 인터프리터 |
| `bridge` | `sdk_drag_hybrid_bridge` | `scripts/` 안의 실행할 브리지 이름 |
| `params_file` | `config/bridge.yaml` | 파라미터 |

## 카메라 송신

```bash
ros2 launch jetcobot_dual_bridge camera.launch.py side:=left host:=<PC IP>
```

- **`host`는 픽 UI를 실행하는 PC의 IP로 바꿔야 한다**(필수, 현재 192.168.0.15). PC에서 `hostname -I`로 확인한다.
- 포트는 왼쪽 5000, 오른쪽 5001이다(`port:=`로 변경 가능).
- 장치·해상도 인자: `device`(`/dev/jetcocam0`), `width`, `height`, `fps`, `bitrate`(kbit/s)
- 파이프라인: `v4l2src → YUY2 640×480@30 → x264enc(zerolatency, ultrafast, key-int-max 30) → rtph264pay(pt 96, mtu 1200) → udpsink`

## 참고

- 브리지는 형제 모듈(`trajectory_guard`, `mode_lease` 등)을 같은 폴더에서 import한다. 그래서 파이썬 모듈이 아니라 `share/.../scripts`에 설치해 스크립트로 실행한다.
- `drag_step_deg`와 `drag_corner_trim_deg`는 PC `system.launch.py`의 `hybrid_drag_*`와 같아야 한다.
- 상속 구조: `read_joint_states` → `hardware_bridge` → `sdk_goal_bridge` → `sdk_drag_bridge` → `sdk_drag_hybrid_bridge`. 자세한 내용은 [../docs/bridge_study.md](../docs/bridge_study.md)에 있다.
- `sdk_patch/`: pymycobot timed-angles 패치와 설치 스크립트.
- 그 밖의 `trial_*`, `probe_*`, `read_drag_mapping`, `restore_sdk_mode`는 진단과 시험용이다.
