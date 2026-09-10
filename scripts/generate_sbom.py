#!/usr/bin/env python3
"""从当前 Python 环境生成 SPDX 2.3 JSON SBOM。

该脚本只依赖 Python 标准库，避免发布流程再引入一个 SBOM 工具链。
它记录已安装 distribution 的名称、版本和元数据来源；构建产物应在 CI
中与 wheel 一起归档，便于后续漏洞追踪和发布审计。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path


def build_sbom() -> dict:
    packages = []
    iris_ref = None
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name", "").lower(), d.version)):
        name = dist.metadata.get("Name") or "unknown"
        version = dist.version
        ref = "SPDXRef-Package-" + "-".join(ch if ch.isalnum() else "-" for ch in f"{name}-{version}")
        packages.append({
            "SPDXID": ref,
            "name": name,
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": dist.metadata.get("License") or "NOASSERTION",
            "licenseDeclared": dist.metadata.get("License") or "NOASSERTION",
            "supplier": "NOASSERTION",
        })
        if name.lower() == "iris":
            iris_ref = ref
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "iris-python-environment",
        "documentNamespace": "https://iris.local/sbom/" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "creationInfo": {
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "creators": ["Tool: iris.generate_sbom"],
        },
        "documentDescribes": [iris_ref] if iris_ref else [],
        "packages": packages,
        "relationships": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_sbom(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
