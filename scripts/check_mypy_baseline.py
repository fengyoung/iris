#!/usr/bin/env python3
"""检查 mypy 错误数不得超过仓库基线。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


BASELINE = int((Path(__file__).with_name("mypy-baseline.txt")).read_text().strip())
proc = subprocess.run(["mypy", "src/iris", "--no-incremental"], text=True,
                      capture_output=True)
output = proc.stdout + proc.stderr
match = re.search(r"Found (\d+) errors?", output)
count = int(match.group(1)) if match else 0
print(f"mypy errors: {count} (baseline: {BASELINE})")
if count > BASELINE:
    sys.stderr.write(output)
    raise SystemExit(1)
if proc.returncode and count == 0:
    sys.stderr.write(output)
    raise SystemExit(proc.returncode)
