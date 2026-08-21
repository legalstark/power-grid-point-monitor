"""实时测点监视服务与 Web 静态资源入口。

系统边界
========
浏览器只连接监视端9010端口；监视端再通过本机 HTTP 连接模拟器9011端口。
这种单入口结构让分页、手动刷新、人工置数、历史查询和异常提示使用一致协议。

数据流
======
1. 后台轮询线程每1秒请求模拟器完整120点快照。
2. 成功响应先批量写入 SQLite，再原子替换内存实时缓存。
3. 页面请求从缓存分页读取，不被数据库写入延迟阻塞。
4. 历史曲线按测点直接查询 SQLite，并限制为1、5或10分钟窗口。
5. 置数请求由页面发送到监视端，转发模拟器后立即重新采集并写入历史。

断线行为
========
* 模拟器不可达时不清空缓存，用户仍可看到最后一次成功快照。
* 状态接口同时返回 ``connected=false`` 和 ``stale=true``。
* 页面使用红色连接标记明确提示“连接中断·数据陈旧”。
* 后台线程捕获错误并在下一秒继续尝试，不需要用户重启监视程序。

分页行为
========
固定默认页大小为20，120点自然形成6页。类型筛选和关键词检索先作用于
全集，再计算总数与总页数；越界页码被安全收敛到有效范围。查询不会改变
缓存顺序，返回前按类型与点号排序，保证自动刷新期间行位置稳定。

静态资源与打包
==============
开发时从源代码目录读取 ``web``；PyInstaller 模式从 ``sys._MEIPASS``
读取打包资源。所有资源均为本地 HTML/CSS/JavaScript，不访问 CDN，离线
环境可完整运行。数据库目录可通过命令行或环境变量覆盖。

并发与安全
==========
缓存与连接状态由 RLock 保护；SQLite 每操作独立连接。服务仅绑定127.0.0.1，
不对局域网开放。静态路径只允许文件名，阻止 ``..`` 路径穿越。请求超时为
2秒，故模拟器异常不会长期占用监视端请求线程。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from common import DEFAULT_SIMULATOR_URL, JsonHandler, local_timestamp
from storage import HistoryStore


def resource_path(relative: str) -> Path:
    """兼容源码运行和 PyInstaller 的临时资源目录。"""

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


class MonitorState:
    """管理实时缓存、连接状态、分页查询和持久化。"""

    def __init__(self, simulator_url: str, store: HistoryStore, poll_interval: float = 1.0) -> None:
        self.simulator_url = simulator_url.rstrip("/")
        self.store = store
        self.poll_interval = poll_interval
        self.lock = threading.RLock()
        self.cache: dict[str, dict[str, Any]] = {}
        self.connected = False
        self.last_success: str | None = None
        self.last_error: str | None = None
        self.started_at = local_timestamp()
        self._last_cleanup = 0.0

    @staticmethod
    def _request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=2.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = detail.get("error", {}).get("message", str(exc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = str(exc)
            raise ValueError(message) from exc

    def refresh(self) -> int:
        """主动采集一次；失败时保留旧值并切换为陈旧状态。"""

        try:
            response = self._request_json(f"{self.simulator_url}/api/v1/points")
            points = response.get("data", [])
            if len(points) != 120:
                raise ValueError(f"模拟器返回 {len(points)} 个测点，预期120个")
            self.store.insert_snapshots(points)
            with self.lock:
                self.cache = {point["pointId"]: point for point in points}
                self.connected = True
                self.last_success = local_timestamp()
                self.last_error = None
            now = time.time()
            if now - self._last_cleanup >= 30:
                self.store.cleanup(now)
                self._last_cleanup = now
            return len(points)
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            with self.lock:
                self.connected = False
                self.last_error = str(exc)
            raise

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "connected": self.connected,
                "stale": not self.connected,
                "lastSuccess": self.last_success,
                "lastError": self.last_error,
                "startedAt": self.started_at,
                "pointCount": len(self.cache),
                "historyRows": self.store.count(),
                "pollIntervalSeconds": self.poll_interval,
                "retentionMinutes": self.store.retention_seconds // 60,
            }

    def list_points(self, point_type: str, keyword: str, page: int, page_size: int) -> dict[str, Any]:
        """统一进行类型筛选、关键词过滤和固定大小分页。"""

        with self.lock:
            values = [dict(point) for point in self.cache.values()]
            status = self.status()
        summary = {
            "total": len(values),
            "good": sum(point["qualityCode"] == 0 for point in values),
            "manual": sum(bool(point["manualOverride"]) for point in values),
            "yx": sum(point["pointType"] == "YX" for point in values),
            "yc": sum(point["pointType"] == "YC" for point in values),
        }
        if point_type in {"YX", "YC"}:
            values = [point for point in values if point["pointType"] == point_type]
        lowered = keyword.lower().strip()
        if lowered:
            values = [
                point for point in values
                if lowered in point["pointId"].lower()
                or lowered in point.get("displayName", "").lower()
                or lowered in point["identifier"].lower()
            ]
        values.sort(key=lambda item: (item["pointType"], item["pointId"]))
        total = len(values)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), total_pages)
        start = (page - 1) * page_size
        return {
            "items": values[start:start + page_size],
            "pagination": {"page": page, "pageSize": page_size, "total": total, "totalPages": total_pages},
            "summary": summary,
            "status": status,
        }

    def manual(self, point_id: str, value: Any) -> dict[str, Any]:
        result = self._request_json(
            f"{self.simulator_url}/api/v1/points/{point_id}/manual", "PUT", {"value": value}
        )
        self.refresh()
        return result["data"]

    def clear_manual(self, point_id: str) -> dict[str, Any]:
        result = self._request_json(
            f"{self.simulator_url}/api/v1/points/{point_id}/manual", "DELETE"
        )
        self.refresh()
        return result["data"]


class MonitorHandler(JsonHandler):
    """监视端 API 和静态页面处理器。"""

    state: MonitorState

    def _serve_file(self, relative: str) -> None:
        target = resource_path(relative)
        if not target.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "静态资源不存在")
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _point_route(path: str, suffix: str) -> str | None:
        prefix = "/api/v1/points/"
        if path.startswith(prefix) and path.endswith(suffix):
            return unquote(path[len(prefix):-len(suffix)]).strip("/").upper()
        return None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in {"/", "/index.html"}:
            self._serve_file("web/index.html")
            return
        if path.startswith("/static/"):
            safe_name = Path(path[len("/static/"):]).name
            self._serve_file(f"web/{safe_name}")
            return
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "UP", **self.state.status()})
            return
        if path == "/api/v1/status":
            self.send_json(HTTPStatus.OK, self.state.status())
            return
        if path == "/api/v1/points":
            try:
                page = int(query.get("page", ["1"])[0])
                page_size = min(100, max(1, int(query.get("pageSize", ["20"])[0])))
            except ValueError:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "INVALID_PAGINATION", "分页参数必须是整数")
                return
            result = self.state.list_points(
                query.get("type", [""])[0].upper(), query.get("keyword", [""])[0], page, page_size
            )
            self.send_json(HTTPStatus.OK, result)
            return
        point_id = self._point_route(path, "/history")
        if point_id:
            try:
                minutes = int(query.get("minutes", ["1"])[0])
                if minutes not in {1, 5, 10}:
                    raise ValueError
            except ValueError:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "INVALID_WINDOW", "历史窗口只允许1、5或10分钟")
                return
            with self.state.lock:
                exists = point_id in self.state.cache
            if not exists:
                self.send_error_json(HTTPStatus.NOT_FOUND, "POINT_NOT_FOUND", f"测点 {point_id} 不存在")
                return
            self.send_json(HTTPStatus.OK, {"pointId": point_id, "minutes": minutes, "items": self.state.store.query(point_id, minutes)})
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/v1/refresh":
            self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")
            return
        try:
            count = self.state.refresh()
            self.send_json(HTTPStatus.OK, {"refreshed": count, "at": self.state.last_success})
        except Exception as exc:  # 请求边界需要把底层连接错误规范化。
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "SIMULATOR_UNAVAILABLE", str(exc))

    def do_PUT(self) -> None:  # noqa: N802
        point_id = self._point_route(urlparse(self.path).path, "/manual")
        if not point_id:
            self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")
            return
        try:
            payload = self.read_json()
            if "value" not in payload:
                raise ValueError("缺少 value 字段")
            point = self.state.manual(point_id, payload["value"])
            self.send_json(HTTPStatus.OK, {"data": point})
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "INVALID_VALUE", str(exc))
        except (URLError, OSError) as exc:
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "SIMULATOR_UNAVAILABLE", str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        point_id = self._point_route(urlparse(self.path).path, "/manual")
        if not point_id:
            self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")
            return
        try:
            point = self.state.clear_manual(point_id)
            self.send_json(HTTPStatus.OK, {"data": point})
        except ValueError as exc:
            status = HTTPStatus.NOT_FOUND if "不存在" in str(exc) else HTTPStatus.BAD_REQUEST
            self.send_error_json(status, "MANUAL_OPERATION_FAILED", str(exc))
        except (URLError, OSError) as exc:
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "SIMULATOR_UNAVAILABLE", str(exc))


def polling_loop(state: MonitorState, stop_event: threading.Event) -> None:
    """持续采集，连接失败不会终止后台线程。"""

    while not stop_event.is_set():
        try:
            state.refresh()
        except Exception as exc:
            print(f"[{local_timestamp()}] 采集失败：{exc}", flush=True)
        stop_event.wait(state.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="A1厂站测点实时监视工具")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9010)
    parser.add_argument("--simulator-url", default=DEFAULT_SIMULATOR_URL)
    parser.add_argument("--data-dir", default=os.environ.get("POWER_MONITOR_DATA_DIR", "data"))
    args = parser.parse_args()

    store = HistoryStore(Path(args.data_dir) / "monitor_history.db", retention_seconds=600)
    state = MonitorState(args.simulator_url, store, poll_interval=1.0)
    MonitorHandler.state = state
    stop_event = threading.Event()
    worker = threading.Thread(target=polling_loop, args=(state, stop_event), daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    print(f"[{local_timestamp()}] 监视工具已启动：http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
        print(f"[{local_timestamp()}] 监视工具已停止", flush=True)


if __name__ == "__main__":
    main()
