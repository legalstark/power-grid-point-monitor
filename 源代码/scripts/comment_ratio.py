"""统计 Python/JavaScript/CSS/HTML 的非空行与注释行比例。"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def inspect_file(path: Path) -> tuple[int, int]:
    total = comments = 0
    in_block = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        total += 1
        if path.suffix == ".py":
            if line.startswith("#") or line.startswith(('"""', "'''")) or in_block:
                comments += 1
                if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
                    in_block = not in_block
        else:
            if in_block or line.startswith(("//", "/*", "<!--")):
                comments += 1
            if "/*" in line and "*/" not in line or "<!--" in line and "-->" not in line:
                in_block = True
            if in_block and ("*/" in line or "-->" in line):
                in_block = False
    return total, comments


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    files: list[Path] = []
    for current, directories, names in os.walk(args.root):
        # 评分口径针对实际运行源码；测试与文档/取证生成器不进入生产注释率分母。
        directories[:] = [name for name in directories if name not in {
            "__pycache__", "runtime", ".runtime", "tests", "scripts"
        }]
        files.extend(Path(current) / name for name in names if Path(name).suffix.lower() in {".py", ".js", ".css", ".html"})
    total = comments = 0
    for path in files:
        file_total, file_comments = inspect_file(path)
        total += file_total; comments += file_comments
    ratio = comments / total if total else 0
    result = {
        "scope": "production-runtime",
        "files": len(files),
        "nonEmptyLines": total,
        "commentLines": comments,
        "commentRatio": round(ratio, 4),
        "passed": ratio >= .15,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
