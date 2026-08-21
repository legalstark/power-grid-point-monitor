"""启动两个真实服务并执行12秒周期、接口和并发性能验收。"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def get_json(url: str) -> dict:
    started = time.perf_counter()
    with urlopen(url, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    payload["_elapsedMs"] = (time.perf_counter() - started) * 1000
    return payload


def wait_ready(url: str, timeout: float = 12) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get_json(url)
            return
        except OSError:
            time.sleep(.2)
    raise RuntimeError(f"服务未就绪：{url}")


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio) - 1))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result: dict = {"startedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": {}}
    runtime_root = ROOT / "tests" / "runtime" / "acceptance"
    runtime_root.mkdir(parents=True, exist_ok=True)
    for candidate in runtime_root.glob("monitor_history.db*"):
        candidate.unlink()
    temp_dir = str(runtime_root)
    if True:
        simulator = subprocess.Popen(
            [sys.executable, str(ROOT / "simulator_app.py"), "--port", "19011"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True,
        )
        monitor = subprocess.Popen(
            [sys.executable, str(ROOT / "monitor_app.py"), "--port", "19010", "--simulator-url",
             "http://127.0.0.1:19011", "--data-dir", temp_dir],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True,
        )
        try:
            wait_ready("http://127.0.0.1:19011/health")
            wait_ready("http://127.0.0.1:19010/health")
            time.sleep(1.5)
            first = get_json("http://127.0.0.1:19011/api/v1/points")["data"]
            yc_first = {p["pointId"]: p["value"] for p in first if p["pointType"] == "YC"}
            yx_first = {p["pointId"]: p["value"] for p in first if p["pointType"] == "YX"}
            time.sleep(10.5)
            second = get_json("http://127.0.0.1:19011/api/v1/points")["data"]
            yc_second = {p["pointId"]: p["value"] for p in second if p["pointType"] == "YC"}
            yx_second = {p["pointId"]: p["value"] for p in second if p["pointType"] == "YX"}
            result["checks"]["scope"] = {"passed": len(second) == 120, "count": len(second)}
            result["checks"]["telemetryCycle"] = {
                "passed": all(yc_first[k] != yc_second[k] for k in yc_first), "changed": sum(yc_first[k] != yc_second[k] for k in yc_first)
            }
            result["checks"]["signalCycle"] = {
                "passed": sum(yx_first[k] != yx_second[k] for k in yx_first) >= 1,
                "changed": sum(yx_first[k] != yx_second[k] for k in yx_first),
            }
            page = get_json("http://127.0.0.1:19010/api/v1/points?page=1&pageSize=20")
            result["checks"]["pagination"] = {
                "passed": len(page["items"]) == 20 and page["pagination"]["totalPages"] == 6,
                "pageSize": len(page["items"]), "totalPages": page["pagination"]["totalPages"],
            }
            urls = ["http://127.0.0.1:19010/api/v1/points?page=1&pageSize=20"] * 80
            with ThreadPoolExecutor(max_workers=12) as pool:
                latencies = [item["_elapsedMs"] for item in pool.map(get_json, urls)]
            p95 = percentile(latencies, .95)
            result["checks"]["performance"] = {
                "passed": p95 < 500, "requests": len(latencies), "p95Ms": round(p95, 2),
                "averageMs": round(statistics.mean(latencies), 2),
            }
            status = get_json("http://127.0.0.1:19010/api/v1/status")
            result["checks"]["history"] = {"passed": status["historyRows"] >= 1200, "rows": status["historyRows"]}
        finally:
            monitor.terminate(); simulator.terminate()
            monitor.wait(timeout=5); simulator.wait(timeout=5)
    result["passed"] = all(check["passed"] for check in result["checks"].values())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
