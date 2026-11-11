# ROS 2 통신 안내 시험

ROS 2 토픽 손실을 재현하고 측정하는 시험 도구입니다. 시험 항목과 판정 기준은
[ROS2_통신_안내_시험_계획서.md](../ROS2_통신_안내_시험_계획서.md)를 따릅니다.

- 목적: 프로젝트 시작 전에 토픽 손실을 줄이는 통신 규칙을 정하고, 그 규칙에 시험 근거를 붙입니다.
- 1단계(이 문서)는 시작 직후 1회 발행(L-01)과 느린 구독자·depth(L-03)를 PC 1대에서 시험합니다.

## 준비

```bash
source /opt/ros/jazzy/setup.bash
```

`ROS_DOMAIN_ID`가 없으면 87을 씁니다. `run_stage1.sh`는 `ROS_DISTRO`가 없을 때 위 파일을 스스로 source합니다.

## 실행

```bash
tests/ros2_guide/run_stage1.sh                       # 기본: --trials 50 --seconds 20
tests/ros2_guide/run_stage1.sh --trials 2 --seconds 3 --run-id dev-check   # 소량 확인
```

옵션은 `--trials N`(L-01 변형별 횟수), `--seconds S`(L-03 조합별 시간), `--run-id ID`(기본 `날짜-시각`)입니다.
L-03은 5회 반복합니다. 전체 실행은 약 30분 걸립니다. 끝에 결과 요약을 항상 출력합니다.

종료 코드는 실행 도구가 비정상 종료했거나 시작을 거부했을 때(이미 쓰인 실행 ID 등)만 0이 아닙니다. `실패`나 `판정 불가` 판정으로는 바뀌지 않으므로 판정은 요약이나 JSON에서 확인합니다.
같은 실행 ID에 결과 파일이 이미 있으면 아무것도 실행하지 않고 멈춥니다.

## 시험 내용과 판정

| 시험 | 보여 주는 것 | 판정 |
|---|---|---|
| L-01 A | 시작 직후 바로 1회 발행(EVENT): 손실 재현 | 측정 |
| L-01 B | 구독자를 찾을 때까지(최대 5초) 기다린 뒤 1회 발행. 5초 안에 못 찾으면 발행하지 않고 그 시도를 제외 | 통과/실패 (모두 수신하면 통과) |
| L-01 C | LATCHED(transient_local)로 바로 1회 발행 | 통과/실패 |
| L-01 D | STATE(best_effort)로 바로 1회 발행 | 측정 |
| L-01 E-VOL | 구독자가 1초 늦게 뜸, volatile(설계상 받지 못함) | 측정 |
| L-01 E-TL | 구독자가 1초 늦게 뜸, transient_local | 통과/실패 |
| L-01 F | 10 Hz로 계속 발행: 첫 수신 순번과 첫 수신까지 걸린 시간 | 측정 |
| L-01 S | `wait_for_service()` 후 서비스 1회 호출 | 통과/실패 |
| L-03 규칙 | 최신 메시지만 저장하고 타이머로 처리(depth 1, best_effort). 5회 모두 라이브 구간의 묵은 정도 150 ms 이하 | 통과/실패 |
| L-03 대조 | depth 1/5/50 × best_effort/reliable, 느린 구독자(100 ms 처리)의 처리·버림·묵은 정도 | 측정 |

통신 범위: 두 실행 도구는 `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`를 명시해 시험 노드와 `ros2 doctor`에 같은 값을 넘깁니다(ROS를 source하면 기본값이 SUBNET이므로). 다른 값이 필요하면 `--discovery-range`로 바꿉니다.

## 결과

결과는 `tests/ros2_guide/results/<실행 ID>/`에 시험 ID마다 JSON 하나(`L-01-A.json`, `L-03-규칙.json` 등)와 `ros2 doctor --report` 출력(`doctor.txt`)으로 저장됩니다. 이 폴더는 커밋하지 않습니다.

JSON 필드: `id`, `result`, `run_id`, `started_at`(변형·시험 시작), `written_at`, `tool_commit`(시험 도구의 git 커밋, 모르면 `알 수 없음`), `env`(`ROS_DISTRO`, `RMW_IMPLEMENTATION`, `ROS_DOMAIN_ID`, `ROS_AUTOMATIC_DISCOVERY_RANGE`, `host`), `conditions`, `observations`, `notes`.

L-03은 원시 표본을 `L-03-대조.samples.json`, `L-03-규칙.samples.json`에 따로 남기고 결과의 `observations.samples_file`이 이 파일을 가리킵니다. 조합·반복마다 `samples`(`[발행 시각 ns, 처리 시각 ns]` 목록)와 `last_publish_ns`가 들어 있습니다.

`result` 값:

| 값 | 뜻 |
|---|---|
| 통과 | 규칙 변형이 기준을 모두 만족함 |
| 실패 | 규칙 변형이 기준을 만족하지 못함 |
| 미실행 | 실행하지 않음 |
| 판정 불가 | 측정 자체가 온전하지 않아 판단할 수 없음 |
| 측정 | 합격 기준이 없는 항목. 값만 기록함 |

제외된 시도(구독 노드가 죽거나, 발행 노드가 메시지를 하나도 보내지 않는 등 측정이 온전하지 않은 시도)는 분모에서 뺍니다. 규칙 변형에서 제외된 시도가 하나라도 있으면 `판정 불가`입니다. L-01의 `counted`는 집계한 시도 수, `trials`는 실행한 시도 수, `pub_sent_total`은 발행 노드들이 보고한 발행 수 합계입니다.

판정 세부 규칙:

- 공식 크기: L-01은 `--trials 50`, L-03은 `--seconds 20`과 5회 반복입니다. 이 크기가 아니면 규칙 변형(L-01 B, C, E-TL, S와 L-03 규칙)은 `판정 불가`이고, 모든 결과에 "개발 실행(…): 공식 판정 아님" 메모가 붙습니다. 측정 변형은 `측정`을 유지합니다.
- L-03 라이브 구간: 처리 시각이 발행 노드의 마지막 발행 시각 이하인 처리만 판정과 `staleness_stats`에 씁니다. 발행이 끝난 뒤의 처리(마지막 메시지가 다음 타이머까지 묵는 꼬리)는 `tail_count`, `tail_max_ms`로 따로 남깁니다. 대조 조합도 같습니다.
- L-03에서 구독자와 매칭되지 않았거나(`matched: false`), 발행 수가 예정(20초 × 50 Hz = 1,000건)에 못 미치거나, 처리한 메시지가 없는 반복·조합은 데이터가 아니므로 `ok: false`이고, 규칙은 `판정 불가`가 됩니다.
- L-03 150 ms 판정은 반올림 전 값(`max_exact`)으로 합니다. `max`는 표시용(소수 1자리)입니다.

근거로 인용할 공식 실행만 `tests/ros2_guide/evidence/<실행 ID>/`에 사본으로 남깁니다(`tests/service_bridge/evidence/`와 같은 방식). 사본에는 `.samples.json`과 `doctor.txt`도 함께 넣습니다.

## 2단계 (저녁, 라즈베리파이)

PC와 라즈베리파이 2대로 Wi-Fi 구간을 시험합니다: L-01-W(무선 시작 직후 발행), L-02(주기 메시지 손실), L-04(통합 운영 부하), G-11(장비 간 격리). 한 번에 `run_stage2.sh`로 돌립니다.

### 준비

- PC에서 라즈베리파이로 SSH 키 로그인이 되어야 합니다(`ssh -o BatchMode=yes <호스트> true`). 호스트 이름은 `PI_HOST`로 넘깁니다.
- PC와 라즈베리파이가 같은 공유기에 있어야 합니다. 라즈베리파이에는 ROS 2 Jazzy(`/opt/ros/jazzy`)가 있어야 하고, L-04 영상 동시 조합에는 `gst-launch-1.0`과 `x264enc`도 필요합니다(없으면 그 조합만 사유를 남깁니다).
- PC 방화벽에서 DDS UDP(7400~7500대 포트)와 영상 포트 5600(UDP)을 허용합니다.
- 시험 도구는 사전 점검이 `rsync`로 라즈베리파이의 `~/r2g/`에 복사합니다.

### 실행

```bash
PI_HOST=sbt-pi tests/ros2_guide/run_stage2.sh                         # 공식 크기 전체
PI_HOST=sbt-pi tests/ros2_guide/run_stage2.sh --run-id 20260612-evening --resume   # 끊긴 실행 이어서
tests/ros2_guide/run_stage2.sh --quick --run-id dev-s2                # PI_HOST=local 소량 확인
```

옵션은 `--run-id ID`(기본 `날짜-시각`), `--resume`, `--only L-01-W,L-02,L-04,G-11`(실행할 시험만 고름), `--quick`(개발 크기: L-01-W 3회, L-02 5초·1회, L-04 20초, G-11 3초)입니다. 실행 순서는 사전 점검, L-01-W, L-02, L-04, G-11, 양쪽 시험 프로세스 정리, 결과 요약입니다. 하나의 실행 ID를 네 시험이 함께 씁니다.

- 끊겼을 때: `--run-id <같은 ID> --resume`. 끝난 조합은 `parts/` 폴더에 남아 있어 건너뛰고, 그 다음 조합부터 이어서 실행합니다. 사전 점검은 다시 돌며, 이전 `preflight.json`은 `preflight.<시각>.json`으로 보관합니다.
- `--resume` 없이 같은 실행 ID에 결과(`L-01-W-*.json`, `L-02.json`, `L-04.json`, `G-11.json`, `parts/`)가 있으면 아무것도 실행하지 않고 멈춥니다(종료 코드 2).
- 종료 코드: 2 시작 거부(이미 쓰인 실행 ID, 잘못된 옵션), 3 사전 점검 실패, 1 실행 도구가 비정상 종료. `실패`나 `판정 불가` 판정으로는 바뀌지 않으므로 판정은 요약이나 JSON에서 확인합니다.
- 예상 시간(공식 크기): L-01-W 약 50분, L-02 약 25분, L-04 약 15분, G-11 약 10분. 사전 점검과 정리를 더해 약 1시간 40분입니다.

### 사전 점검

SSH 확인, 라즈베리파이의 `ROS_DISTRO`(jazzy), 시험 도구 복사, 이전 프로세스 정리, 양방향 3초 통신 확인, `gst-launch-1.0`/`x264enc` 확인 순서입니다. `ssh`, `pi_ros_jazzy`, `sync`, `pc_to_pi`, `pi_to_pc` 중 하나라도 통과하지 못하면 아무 시험도 돌리지 않고 종료 코드 3으로 멈춥니다. 한쪽 방향만 통신되면(예: 라즈베리파이 → PC 수신 0건) 대개 PC 방화벽이 원인입니다. `gst`가 없으면 경고만 하고 진행합니다.

### 시험 내용과 판정

| 시험 | 크기(공식) | 판정 |
|---|---|---|
| L-01-W | 변형마다 50회 (L-01-A~S, 무선) | B, C, E-TL, S는 통과/실패, 나머지는 측정 |
| L-02 | 크기 3 × 속도 2 × 신뢰성 2 조합마다 30초·3회 | 측정 |
| L-04 | 부하 3단계 × (ROS만 / 영상 동시) 조합마다 120초 | 측정 (`observations.risk`로 통합 운영 위험 표시) |
| G-11 | 방향마다 관찰 10초 (대조 + 도메인/PC_LOCALHOST/파이_LOCALHOST 격리) | 통과/실패 (대조에서 통신이 안 되면 판정 불가) |

공식 크기가 아니면 규칙 판정(L-01-W B, C, E-TL, S와 G-11)은 `판정 불가`이고 모든 결과에 "개발 실행(…): 공식 판정 아님" 메모가 붙습니다.

### PI_HOST=local (미리 돌려 보기)

`PI_HOST`의 기본값은 `local`이고, `run_l02.py`, `run_l04.py`, `run_g11.py`도 `--pi-host`를 생략하면 `local`입니다. 이 PC 한 대에서 두 장비 역할을 모두 하므로 통신 범위는 `LOCALHOST`이고 SSH와 복사는 건너뜁니다. 도구가 끝까지 도는지 확인하는 용도이며, 결과에는 "장비 1대(PI_HOST=local): 공식 판정 아님" 메모가 붙고 규칙 판정은 `판정 불가`입니다. 근거로 인용하지 않습니다.

### 결과 파일

`tests/ros2_guide/results/<실행 ID>/`에 다음이 저장됩니다(커밋하지 않음).

- `L-01-W-*.json`, `L-02.json`, `L-04.json`, `G-11.json`: 시험마다 JSON 하나(필드는 위 결과 절과 같음).
- `parts/<시험 ID>/<조합>.json`: 조합(방향)마다 끝날 때 저장하는 부분 결과. `--resume`이 이것을 보고 이어서 실행하며, 최종 JSON의 원자료이기도 합니다.
- `preflight.json`: 사전 점검 항목별 결과(`checks`), 상세(`detail`), 진행 여부(`proceed`), 사유(`reasons`).
- `doctor.txt`(PC)와 `doctor_pi.txt`(라즈베리파이)의 `ros2 doctor --report` 출력.

시계 규칙: 두 장비의 시각 차이는 계산에 쓰지 않습니다. 끊김과 간격은 한 장비 안의 단조 시계(`time.monotonic_ns()`)로만 잽니다.

근거로 인용할 공식 실행만 `tests/ros2_guide/evidence/<실행 ID>/`에 사본으로 남깁니다. 사본에는 `parts/` 폴더, `doctor.txt`, `doctor_pi.txt`, `preflight.json`을 함께 넣습니다.

## 단위 시험

```bash
python3 -m pytest tests/ros2_guide/unit -q
```
