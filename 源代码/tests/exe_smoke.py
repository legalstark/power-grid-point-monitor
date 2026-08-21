"""针对已打包 EXE 的黑盒冒烟测试，不导入任何项目源码。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def request_json(url: str, method: str = "GET", body: dict | None = None) -> dict:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=encoded, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:19110")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    status = request_json(f"{args.base}/api/v1/status")
    points = request_json(f"{args.base}/api/v1/points?page=1&pageSize=20")
    with urlopen(f"{args.base}/", timeout=5) as response:
        page = response.read().decode("utf-8")
    manual = request_json(f"{args.base}/api/v1/points/YC01/manual", "PUT", {"value": 225.55})["data"]
    restored = request_json(f"{args.base}/api/v1/points/YC01/manual", "DELETE")["data"]
    checks = {
        "standaloneExecutables": status["connected"] and status["pointCount"] == 120,
        "embeddedWebInterface": "GridScope" in page and "测点实时监控" in page,
        "pagination": len(points["items"]) == 20 and points["pagination"]["totalPages"] == 6,
        "manualQuality": manual["qualityCode"] == 0x20 and manual["manualOverride"],
        "manualRelease": restored["qualityCode"] == 0 and not restored["manualOverride"],
    }
    result = {"checks": checks, "passed": all(checks.values())}
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
