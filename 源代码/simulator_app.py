"""厂站 A1 测点数据模拟器。

职责
====
模拟器是独立 HTTP 进程，只负责生成和维护测点实时状态。监视程序可以
随时重启而不影响模拟周期，体现“模拟和监视程序独立运行”的加分要求。

监控范围
========
* 厂站固定为 A1。
* 遥信固定为 YX00 至 YX99，共100点。
* 遥测固定为 YC01 至 YC20，共20点。
* 唯一标识使用 ``A1.<点号>``，例如 ``A1.YC01``。

周期不变量
==========
* 每1秒遍历全部非人工置数遥测点，并保证两位小数显示值实际发生变化。
* 每10秒从非人工置数遥信中随机选择一个并翻转，保证一次且仅一次变化。
* 周期由 ``time.monotonic`` 判断，不受系统时钟校准或时区变化影响。
* 对外刷新时间使用本地带时区时间，满足精确到秒的展示要求。

人工置数不变量
==============
* YX 只接受布尔值或0/1，界面显示为分/合。
* YC 只接受当前工程量定义量程内的数字，统一保留两位小数。
* 置数立即设置 SB(0x20) 并更新时间；周期线程不覆盖人工值。
* 解除置数立即恢复质量正常，下一到期周期恢复随机变化。

并发模型
========
HTTP 服务使用 ThreadingHTTPServer；模拟线程和请求线程通过同一个 RLock
保护测点字典。返回接口前构造公开副本，避免调用方在锁外修改共享状态。
可注入随机数与单调时钟，使1秒和10秒规则能够不等待真实时间完成单元测试。
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from common import JsonHandler, QUALITY_SB, STATION_ID, local_timestamp, quality_details


@dataclass(frozen=True)
class TelemetryProfile:
    """定义遥测点的工程量名称、单位、量程和随机步长。"""

    name: str
    unit: str
    minimum: float
    maximum: float
    initial: float
    step: float


# 20 个遥测点采用典型电力工程量，量程用于显示和人工置数校验。
TELEMETRY_PROFILES = (
    TelemetryProfile("A相电压", "kV", 205.0, 235.0, 220.0, 0.80),
    TelemetryProfile("B相电压", "kV", 205.0, 235.0, 220.4, 0.80),
    TelemetryProfile("C相电压", "kV", 205.0, 235.0, 219.7, 0.80),
    TelemetryProfile("A相电流", "A", 0.0, 800.0, 328.0, 9.00),
    TelemetryProfile("B相电流", "A", 0.0, 800.0, 334.0, 9.00),
    TelemetryProfile("C相电流", "A", 0.0, 800.0, 326.0, 9.00),
    TelemetryProfile("有功功率", "MW", -120.0, 320.0, 138.0, 4.00),
    TelemetryProfile("无功功率", "Mvar", -100.0, 150.0, 32.0, 3.00),
    TelemetryProfile("频率", "Hz", 49.50, 50.50, 50.00, 0.03),
    TelemetryProfile("功率因数", "", -1.0, 1.0, 0.96, 0.01),
    TelemetryProfile("母线电压", "kV", 205.0, 235.0, 221.0, 0.70),
    TelemetryProfile("主变油温", "℃", -20.0, 120.0, 52.0, 0.60),
    TelemetryProfile("绕组温度", "℃", -20.0, 150.0, 65.0, 0.80),
    TelemetryProfile("直流母线电压", "V", 180.0, 260.0, 220.0, 1.20),
    TelemetryProfile("站用电电压", "V", 200.0, 250.0, 228.0, 1.00),
    TelemetryProfile("主变分接头", "档", 1.0, 17.0, 9.0, 1.00),
    TelemetryProfile("环境温度", "℃", -30.0, 60.0, 26.0, 0.50),
    TelemetryProfile("环境湿度", "%", 0.0, 100.0, 54.0, 1.20),
    TelemetryProfile("蓄电池电压", "V", 180.0, 260.0, 226.0, 0.80),
    TelemetryProfile("通信负载", "%", 0.0, 100.0, 38.0, 2.50),
)


class PointSimulator:
    """线程安全的120点状态机，可通过时间源注入进行确定性测试。"""

    def __init__(
        self,
        *,
        yc_interval: float = 1.0,
        yx_interval: float = 10.0,
        rng: random.Random | None = None,
        monotonic=time.monotonic,
    ) -> None:
        self.yc_interval = yc_interval
        self.yx_interval = yx_interval
        self.rng = rng or random.Random()
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.points: dict[str, dict[str, Any]] = {}
        self.last_yc_update = self.monotonic()
        self.last_yx_update = self.monotonic()
        self.last_changed_yx: str | None = None
        self._create_points()

    def _create_points(self) -> None:
        """严格按题目编号创建100个YX与20个YC。"""

        now = local_timestamp()
        for index in range(100):
            point_id = f"YX{index:02d}"
            value = index % 3 == 0
            self.points[point_id] = self._base_point(
                point_id, "YX", f"开关状态{index + 1:02d}", value, "", now, 0.0, 1.0
            )
        for index, profile in enumerate(TELEMETRY_PROFILES, start=1):
            point_id = f"YC{index:02d}"
            self.points[point_id] = self._base_point(
                point_id,
                "YC",
                profile.name,
                profile.initial,
                profile.unit,
                now,
                profile.minimum,
                profile.maximum,
                profile.step,
            )

    @staticmethod
    def _base_point(
        point_id: str,
        point_type: str,
        name: str,
        value: Any,
        unit: str,
        refreshed_at: str,
        minimum: float,
        maximum: float,
        step: float = 0.0,
    ) -> dict[str, Any]:
        flags, text = quality_details(0)
        return {
            "identifier": f"{STATION_ID}.{point_id}",
            "stationId": STATION_ID,
            "pointId": point_id,
            "pointType": point_type,
            "displayName": name,
            "value": value,
            "unit": unit,
            "qualityCode": 0,
            "qualityFlags": flags,
            "qualityText": text,
            "refreshedAt": refreshed_at,
            "manualOverride": False,
            "minimum": minimum,
            "maximum": maximum,
            "step": step,
        }

    def _touch(self, point: dict[str, Any], value: Any) -> None:
        point["value"] = value
        point["refreshedAt"] = local_timestamp()
        code = QUALITY_SB if point["manualOverride"] else 0
        point["qualityCode"] = code
        point["qualityFlags"], point["qualityText"] = quality_details(code)

    def tick(self, now: float | None = None) -> dict[str, Any]:
        """执行到期的遥测与遥信更新，并返回本次更新摘要。"""

        current = self.monotonic() if now is None else now
        summary: dict[str, Any] = {"ycUpdated": False, "yxChanged": None}
        with self.lock:
            if current - self.last_yc_update >= self.yc_interval:
                for point in self.points.values():
                    if point["pointType"] != "YC" or point["manualOverride"]:
                        continue
                    delta = self.rng.uniform(-point["step"], point["step"])
                    next_value = min(point["maximum"], max(point["minimum"], float(point["value"]) + delta))
                    if point["unit"] == "档":
                        next_value = round(next_value)
                    next_value = round(next_value, 2)
                    # 随机步长可能在保留两位小数后归零；题目要求“所有遥测数据周期性变化”，
                    # 因此在这种边界情况下按最小显示分辨率向可用方向推进一次。
                    if next_value == point["value"]:
                        resolution = 1.0 if point["unit"] == "档" else 0.01
                        direction = 1 if float(point["value"]) + resolution <= point["maximum"] else -1
                        next_value = round(float(point["value"]) + direction * resolution, 2)
                    self._touch(point, next_value)
                self.last_yc_update = current
                summary["ycUpdated"] = True

            if current - self.last_yx_update >= self.yx_interval:
                candidates = [
                    point for point in self.points.values()
                    if point["pointType"] == "YX" and not point["manualOverride"]
                ]
                if candidates:
                    changed = self.rng.choice(candidates)
                    self._touch(changed, not bool(changed["value"]))
                    self.last_changed_yx = changed["pointId"]
                    summary["yxChanged"] = changed["pointId"]
                self.last_yx_update = current
        return summary

    def snapshot(self, point_id: str | None = None) -> Any:
        """返回剔除内部步长配置后的深层值副本。"""

        with self.lock:
            if point_id is not None:
                point = self.points.get(point_id.upper())
                return self._public(point) if point else None
            ordered = sorted(self.points.values(), key=lambda item: (item["pointType"], item["pointId"]))
            return [self._public(point) for point in ordered]

    @staticmethod
    def _public(point: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in point.items() if key != "step"}

    def set_manual(self, point_id: str, value: Any) -> dict[str, Any]:
        """校验类型和量程后启用人工置数。"""

        key = point_id.upper()
        with self.lock:
            point = self.points.get(key)
            if point is None:
                raise KeyError(f"测点 {point_id} 不存在")
            if point["pointType"] == "YX":
                if isinstance(value, bool):
                    normalized: Any = value
                elif value in (0, 1, "0", "1"):
                    normalized = bool(int(value))
                else:
                    raise ValueError("遥信置数只允许 0 或 1")
            else:
                if isinstance(value, bool):
                    raise ValueError("遥测置数必须是数字")
                try:
                    normalized = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("遥测置数必须是数字") from exc
                if not point["minimum"] <= normalized <= point["maximum"]:
                    raise ValueError(f"数值必须位于 {point['minimum']} 至 {point['maximum']} 之间")
                normalized = round(normalized, 2)
            point["manualOverride"] = True
            self._touch(point, normalized)
            return self._public(point)

    def clear_manual(self, point_id: str) -> dict[str, Any]:
        """解除置数；下一周期重新进入正常模拟。"""

        key = point_id.upper()
        with self.lock:
            point = self.points.get(key)
            if point is None:
                raise KeyError(f"测点 {point_id} 不存在")
            point["manualOverride"] = False
            self._touch(point, point["value"])
            return self._public(point)


class SimulatorHandler(JsonHandler):
    """数据模拟器 REST 接口。"""

    simulator: PointSimulator

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "UP", "stationId": STATION_ID})
            return
        if path == "/api/v1/points":
            data = self.simulator.snapshot()
            self.send_json(HTTPStatus.OK, {"data": data, "count": len(data)})
            return
        prefix = "/api/v1/points/"
        if path.startswith(prefix):
            point_id = unquote(path[len(prefix):]).upper()
            point = self.simulator.snapshot(point_id)
            if point is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "POINT_NOT_FOUND", f"测点 {point_id} 不存在")
            else:
                self.send_json(HTTPStatus.OK, {"data": point})
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        suffix = "/manual"
        prefix = "/api/v1/points/"
        if not (path.startswith(prefix) and path.endswith(suffix)):
            self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")
            return
        point_id = unquote(path[len(prefix):-len(suffix)]).strip("/").upper()
        try:
            payload = self.read_json()
            if "value" not in payload:
                raise ValueError("缺少 value 字段")
            point = self.simulator.set_manual(point_id, payload["value"])
            self.send_json(HTTPStatus.OK, {"data": point})
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, "POINT_NOT_FOUND", str(exc))
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "INVALID_VALUE", str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        suffix = "/manual"
        prefix = "/api/v1/points/"
        if not (path.startswith(prefix) and path.endswith(suffix)):
            self.send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在")
            return
        point_id = unquote(path[len(prefix):-len(suffix)]).strip("/").upper()
        try:
            point = self.simulator.clear_manual(point_id)
            self.send_json(HTTPStatus.OK, {"data": point})
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, "POINT_NOT_FOUND", str(exc))


def run_simulation_loop(simulator: PointSimulator, stop_event: threading.Event) -> None:
    """短周期检查到期任务，停止事件让测试和程序退出可控。"""

    while not stop_event.wait(0.1):
        simulator.tick()


def main() -> None:
    parser = argparse.ArgumentParser(description="A1厂站测点数据模拟器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9011)
    args = parser.parse_args()

    simulator = PointSimulator()
    SimulatorHandler.simulator = simulator
    stop_event = threading.Event()
    worker = threading.Thread(target=run_simulation_loop, args=(simulator, stop_event), daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.host, args.port), SimulatorHandler)
    print(f"[{local_timestamp()}] 数据模拟器已启动：http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
        print(f"[{local_timestamp()}] 数据模拟器已停止", flush=True)


if __name__ == "__main__":
    main()
