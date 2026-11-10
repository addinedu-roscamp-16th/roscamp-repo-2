"""계약 파일(contract.yaml)의 QoS 프로파일을 읽고 rclpy QoS로 바꾼다."""
from pathlib import Path

import yaml

CONTRACT = Path(__file__).resolve().parents[3] / "interfaces" / "fleet_interfaces" / "contract.yaml"


def load_profiles(path: Path = CONTRACT) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["qos_profiles"]


def custom(reliability: str, durability: str, depth: int) -> dict:
    return {"reliability": reliability, "durability": durability, "depth": depth}


def to_qos(p: dict):
    from rclpy.qos import (
        DurabilityPolicy,
        HistoryPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )

    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=p["depth"],
        reliability=ReliabilityPolicy[p["reliability"].upper()],
        durability=DurabilityPolicy[p["durability"].upper()],
    )
