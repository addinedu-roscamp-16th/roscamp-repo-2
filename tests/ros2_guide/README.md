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
L-03은 5회 반복합니다. 전체 실행은 약 30분 걸립니다. 어느 도구든 실패하면 종료 코드가 0이 아니며, 끝에 결과 요약을 항상 출력합니다.

## 시험 내용과 판정

| 시험 | 보여 주는 것 | 판정 |
|---|---|---|
| L-01 A | 시작 직후 바로 1회 발행(EVENT): 손실 재현 | 측정 |
| L-01 B | 구독자를 찾을 때까지(최대 5초) 기다린 뒤 1회 발행 | 통과/실패 (모두 수신하면 통과) |
| L-01 C | LATCHED(transient_local)로 바로 1회 발행 | 통과/실패 |
| L-01 D | STATE(best_effort)로 바로 1회 발행 | 측정 |
| L-01 E-VOL | 구독자가 1초 늦게 뜸, volatile(설계상 받지 못함) | 측정 |
| L-01 E-TL | 구독자가 1초 늦게 뜸, transient_local | 통과/실패 |
| L-01 F | 10 Hz로 계속 발행: 첫 수신 순번과 첫 수신까지 걸린 시간 | 측정 |
| L-01 S | `wait_for_service()` 후 서비스 1회 호출 | 통과/실패 |
| L-03 규칙 | 최신 메시지만 저장하고 타이머로 처리(depth 1, best_effort). 5회 모두 묵은 정도 150 ms 이하 | 통과/실패 |
| L-03 대조 | depth 1/5/50 × best_effort/reliable, 느린 구독자(100 ms 처리)의 처리·버림·묵은 정도 | 측정 |

## 결과

결과는 `tests/ros2_guide/results/<실행 ID>/`에 시험 ID마다 JSON 하나(`L-01-A.json`, `L-03-규칙.json` 등)와 `ros2 doctor --report` 출력(`doctor.txt`)으로 저장됩니다. 이 폴더는 커밋하지 않습니다.

JSON 필드: `id`, `result`, `run_id`, `written_at`, `env`(`ROS_DISTRO`, `RMW_IMPLEMENTATION`, `ROS_DOMAIN_ID`, `host`), `conditions`, `observations`, `notes`.

`result` 값:

| 값 | 뜻 |
|---|---|
| 통과 | 규칙 변형이 기준을 모두 만족함 |
| 실패 | 규칙 변형이 기준을 만족하지 못함 |
| 미실행 | 실행하지 않음 |
| 판정 불가 | 측정 자체가 온전하지 않아 판단할 수 없음 |
| 측정 | 합격 기준이 없는 항목. 값만 기록함 |

제외된 시도(구독 노드가 죽는 등 측정이 온전하지 않은 시도)는 분모에서 뺍니다. 규칙 변형에서 제외된 시도가 하나라도 있으면 `판정 불가`입니다.

근거로 인용할 공식 실행만 `evidence/<실행 ID>/`에 사본으로 남깁니다(`tests/service_bridge/evidence/`와 같은 방식).

## 단위 시험

```bash
python3 -m pytest tests/ros2_guide/unit -q
```
