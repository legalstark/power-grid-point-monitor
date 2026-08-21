"""共享的数据结构、质量码和 HTTP 工具。

设计约束
========
1. 该模块只依赖 Python 标准库，可被两个独立可执行程序共同打包。
2. 对外 JSON 始终使用 UTF-8，中文点名与质量说明不做 ASCII 转义。
3. 时间戳使用带本地时区的 ISO 8601，显示和历史排序均精确到秒。
4. 错误响应固定为 ``{"error":{"code":...,"message":...}}``。
5. 所有成功响应显式禁止缓存，浏览器不会把历史快照当成实时数据。

质量码约定
==========
质量描述遵循 IEC 60870-5-104 的 QDS/SIQ 位布局：

* OV 0x01：遥测数值溢出；遥信点不使用该位。
* BL 0x10：测点被闭锁，不应作为实时控制依据。
* SB 0x20：当前值被替代，本项目用于明确标识人工置数。
* NT 0x40：数据不是当前值，例如上游通信中断后的旧快照。
* IV 0x80：数据无效。
* 0x00：以上异常位均未置位，表示质量正常。

边界原则
========
服务只绑定本机回环地址。跨进程传输仅交换公开字段，随机步长等内部
模拟参数不会进入接口。所有 HTTP 边界都先校验请求体大小和 JSON 类型，
再进入领域方法，避免无效输入破坏模拟线程。
"""

from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Any


STATION_ID = "A1"
DEFAULT_SIMULATOR_URL = "http://127.0.0.1:9011"

# IEC 60870-5-104 QDS/SIQ 质量描述位。
QUALITY_OV = 0x01  # Overflow，遥测溢出。
QUALITY_BL = 0x10  # Blocked，闭锁。
QUALITY_SB = 0x20  # Substituted，被人工值替代。
QUALITY_NT = 0x40  # Not topical，非当前值。
QUALITY_IV = 0x80  # Invalid，无效。


def local_timestamp() -> str:
    """返回带本机时区且精确到秒的 ISO 8601 时间。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def quality_details(code: int) -> tuple[list[str], str]:
    """把数值质量码转换为标志列表和中文说明。"""

    flags: list[str] = []
    labels: list[str] = []
    definitions = (
        (QUALITY_OV, "OV", "溢出"),
        (QUALITY_BL, "BL", "闭锁"),
        (QUALITY_SB, "SB", "人工替代"),
        (QUALITY_NT, "NT", "非当前值"),
        (QUALITY_IV, "IV", "无效"),
    )
    for mask, flag, label in definitions:
        if code & mask:
            flags.append(flag)
            labels.append(label)
    return flags, "、".join(labels) if labels else "正常"


def json_bytes(payload: Any) -> bytes:
    """使用稳定的 UTF-8 JSON 格式编码响应。"""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class JsonHandler(BaseHTTPRequestHandler):
    """为两个服务提供一致的 JSON 响应与错误格式。"""

    server_version = "PowerPointMonitor/1.0"

    def send_json(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:9010")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, code: str, message: str) -> None:
        self.send_json(status, {"error": {"code": code, "message": message}})

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1024 * 64:
            raise ValueError("请求体为空或过大")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:9010")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        # 保留简洁的标准访问日志，便于佐证运行状态。
        print(f"[{local_timestamp()}] {self.address_string()} - {fmt % args}", flush=True)
