"""서비스 브리지 단위 테스트 공통 설정.

- reference/를 import 경로에 넣어 `sbt_bridge.core`를 쓸 수 있게 합니다.
- 계획서 ID 마커(plan_id)를 등록합니다.
- record fixture는 원본 저장소의 결과 기록기를 대신합니다.
  값을 메모리에만 두고 파일로 남기지 않습니다.
"""
import pathlib
import sys

import pytest

REFERENCE = pathlib.Path(__file__).resolve().parent / "reference"
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))


def pytest_configure(config):
    config.addinivalue_line("markers", "plan_id(id): 테스트 계획서의 항목 ID (예: UT-01)")


class Record:
    """테스트 하나의 관측 기록. 판정에는 쓰지 않습니다."""

    def __init__(self, plan_id):
        self.plan_id = plan_id
        self.conditions = {}
        self.observations = {}
        self.notes = []

    def condition(self, key, value):
        self.conditions[key] = value

    def observe(self, key, value):
        self.observations[key] = value

    def note(self, text):
        self.notes.append(text)


@pytest.fixture
def record(request):
    marker = request.node.get_closest_marker("plan_id")
    return Record(marker.args[0] if marker else None)
