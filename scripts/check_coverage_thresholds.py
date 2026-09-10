#!/usr/bin/env python3
"""检查关键模块的覆盖率下限，防止总覆盖率掩盖核心路径回归。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

THRESHOLDS = {
    "src/iris/core/async_http.py": 60.0,
    "src/iris/core/write_guard.py": 85.0,
    "src/iris/core/locks.py": 80.0,
    "src/iris/config/loader.py": 80.0,
    "src/iris/feishu/doc_convert.py": 35.0,
    "src/iris/taskpanel/server.py": 70.0,
}


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("coverage.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files", {})
    failed = []
    for target, minimum in THRESHOLDS.items():
        entry = files.get(target)
        if entry is None:
            # coverage.py 在不同 cwd 下可能返回绝对路径，做后缀匹配。
            entry = next((v for k, v in files.items() if k.endswith(target)), None)
        if entry is None:
            failed.append(f"{target}: 未找到覆盖率数据")
            continue
        actual = float(entry["summary"]["percent_covered"])
        if actual < minimum:
            failed.append(f"{target}: {actual:.1f}% < {minimum:.1f}%")
        else:
            print(f"通过 {target}: {actual:.1f}%")
    if failed:
        print("关键模块覆盖率门禁失败：", file=sys.stderr)
        for item in failed:
            print(f"- {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
