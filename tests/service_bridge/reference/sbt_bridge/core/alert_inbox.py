"""알림 수신: alert_id 기준 중복 방지, ACK는 DB 커밋 뒤에만 (위반 변형 스위치 포함).

처리 순서 (receive)
1. 이미 저장된 alert_id면 ACK만 다시 보냄 (발신 측이 ACK를 못 받아 재전송한 경우) → DUPLICATE
2. 저장하고 커밋 → 커밋이 끝난 뒤 ACK → STORED. 저장이 확정되기 전에 ACK하면 발신 측이 알림을
   지워 버려, 커밋 실패 시 알림이 영영 사라집니다(UT-05-대조가 이 손실을 확인).
3. 커밋 실패 → ACK하지 않음 → COMMIT_FAILED. 발신 측이 재전송하면 다시 저장을 시도합니다.

seq: alerts 테이블의 AUTOINCREMENT 값. WS 재생(since_seq)의 기준이 되는 브리지 쪽 영속 순번입니다.
발신 측의 seq(src_seq 열)와는 다릅니다.
"""
import sqlite3
from dataclasses import dataclass
from enum import Enum


@dataclass
class AlertRecord:
    """수신한 알림 한 건 (ROS Alert 메시지의 저장용 사본). seq는 발신 측 순번입니다."""
    alert_id: str
    robot_id: str
    boot_id: str
    seq: int
    level: int
    text: str
    stamp_ns: int


class ReceiveResult(str, Enum):
    """receive 결과. probe의 alert 사건에 result로 남습니다."""
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"
    COMMIT_FAILED = "COMMIT_FAILED"


_COLS = "alert_id, robot_id, boot_id, src_seq, level, text, stamp_ns"


class AlertInbox:
    """알림 저장과 ACK.

    send_ack(alert_id): ACK 발행 함수.
    on_stored(seq, rec): 저장 후 호출(WS Hub로 실시간 전달).
    """

    def __init__(self, db, send_ack, ack_before_commit: bool = False, on_stored=None):
        self.db = db
        self.send_ack = send_ack
        self.ack_before_commit = ack_before_commit
        self.on_stored = on_stored

    def receive(self, rec: AlertRecord) -> ReceiveResult:
        with self.db.lock:
            dup = self.db.execute(
                "SELECT 1 FROM alerts WHERE alert_id = ?", (rec.alert_id,)
            ).fetchone()
            if dup:
                self.send_ack(rec.alert_id)
                return ReceiveResult.DUPLICATE
            if self.ack_before_commit:  # 위반 변형: 저장이 확정되기 전에 ACK
                self.send_ack(rec.alert_id)
            try:
                cur = self.db.execute(
                    f"INSERT INTO alerts({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rec.alert_id, rec.robot_id, rec.boot_id, rec.seq, rec.level, rec.text,
                     rec.stamp_ns))
                seq = cur.lastrowid
                self.db.commit()
            except sqlite3.Error:
                self.db.rollback()
                return ReceiveResult.COMMIT_FAILED
        if not self.ack_before_commit:
            self.send_ack(rec.alert_id)
        if self.on_stored:
            self.on_stored(seq, rec)
        return ReceiveResult.STORED

    def alerts_since(self, seq: int) -> list:
        """seq보다 큰 저장 알림을 seq 오름차순으로 (WS since_seq 재생용)."""
        rows = self.db.execute(
            f"SELECT seq, {_COLS} FROM alerts WHERE seq > ? ORDER BY seq", (seq,)).fetchall()
        return [(r[0], AlertRecord(*r[1:])) for r in rows]

    def max_seq(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM alerts").fetchone()[0]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]

    def ids(self) -> list:
        rows = self.db.execute("SELECT alert_id FROM alerts ORDER BY seq").fetchall()
        return [r[0] for r in rows]

    def seqs(self) -> list:
        return [r[0] for r in self.db.execute("SELECT seq FROM alerts ORDER BY seq").fetchall()]
