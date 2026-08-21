"""SQLite 历史数据存储。

存储策略
========
* 每次监视端成功采集后，在一个事务内写入120条测点快照。
* ``collected_at`` 保存采集端时间，用于精确的保留窗口判断。
* ``refreshed_at`` 保留模拟器原始业务时间，用于图表和用户核对。
* 默认保留600秒，超过题目“不小于1分钟”的最低要求。
* 定时清理只删除超过保留期的数据，不影响当前实时缓存。

并发与性能
==========
* 每个操作使用独立连接，避免在线程间共享 sqlite3.Connection。
* WAL 模式允许后台批量写入与浏览器历史查询并行进行。
* ``(point_id, refreshed_at)`` 联合索引支持按点和时间读取。
* ``collected_at`` 索引支持低成本保留期清理。
* 连接在 finally 中确定性关闭，避免 Windows 锁住数据库或 WAL 文件。

数据表示
========
遥信和遥测统一以 REAL 保存；遥信使用0/1，前端按阶梯线绘制。质量码保留
整数位图，历史记录可还原当时是正常值还是人工替代值。SQLite 文件放在
运行目录 data 子目录中，程序重启后仍能读取未过期记录。
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


class HistoryStore:
    """负责建表、批量写入、历史查询和过期清理。"""

    def __init__(self, db_path: Path, retention_seconds: int = 600) -> None:
        self.db_path = Path(db_path)
        self.retention_seconds = retention_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """每次操作后确定性关闭连接，避免 Windows 下 WAL 文件被长期占用。"""

        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS point_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    point_id TEXT NOT NULL,
                    point_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    quality_code INTEGER NOT NULL,
                    refreshed_at TEXT NOT NULL,
                    collected_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_point_time
                    ON point_history(point_id, refreshed_at);
                CREATE INDEX IF NOT EXISTS idx_history_collected
                    ON point_history(collected_at);
                """
            )

    def insert_snapshots(self, points: Iterable[dict[str, Any]], collected_at: float | None = None) -> int:
        """在一个事务中批量写入，避免逐点提交造成界面抖动。"""

        stamp = time.time() if collected_at is None else collected_at
        rows = [
            (
                point["pointId"],
                point["pointType"],
                float(point["value"]),
                int(point["qualityCode"]),
                point["refreshedAt"],
                stamp,
            )
            for point in points
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO point_history
                    (point_id, point_type, value, quality_code, refreshed_at, collected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def query(self, point_id: str, minutes: int, now: float | None = None) -> list[dict[str, Any]]:
        """查询指定窗口；布尔遥信在接口层仍以0/1表达便于阶梯图绘制。"""

        current = time.time() if now is None else now
        cutoff = current - minutes * 60
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT point_id, point_type, value, quality_code, refreshed_at, collected_at
                FROM point_history
                WHERE point_id = ? AND collected_at >= ?
                ORDER BY collected_at ASC
                """,
                (point_id.upper(), cutoff),
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup(self, now: float | None = None) -> int:
        current = time.time() if now is None else now
        cutoff = current - self.retention_seconds
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM point_history WHERE collected_at < ?", (cutoff,))
            return cursor.rowcount

    def count(self, point_id: str | None = None) -> int:
        with self._connect() as connection:
            if point_id:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM point_history WHERE point_id = ?", (point_id.upper(),)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS total FROM point_history").fetchone()
        return int(row["total"])
