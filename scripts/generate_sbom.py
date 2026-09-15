#!/usr/bin/env python3
"""从当前 Python 环境生成 SPDX 2.3 JSON SBOM。

该脚本只依赖 Python 标准库，避免发布流程再引入一个 SBOM 工具链。
它记录已安装 distribution 的名称、版本和元数据来源；构建产物应在 CI
中与 wheel 一起归档，便于后续漏洞追踪和发布审计。

iris 自身的版本例外——读 `pyproject.toml` 而非已安装元数据，原因见
`read_product_version()` 的说明。
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def read_product_version(pyproject: Path) -> str:
    """从 pyproject.toml 读取产品版本。

    不用 `importlib.metadata` 取 iris 自身版本：editable 安装的 `.dist-info`
    冻结在安装那一刻，改源码、升版本号、提交、推送都不会刷新它。于是 SBOM 会
    静默声明一个错误版本且无任何失败信号——v3.39.0 发布时实测报成 3.27.0
    （上次重装 editable 时的版本）。源码树才是产品版本的唯一真相。

    第三方依赖仍用已安装元数据：它们的 `.dist-info` 由 pip 在安装时写入，
    与实际落盘的代码一致，是可信的。
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{pyproject} 缺少 project.version 字段")
    return version


def build_sbom(pyproject: Path | None = None) -> dict:
    product_version = read_product_version(pyproject or _REPO_ROOT / "pyproject.toml")
    packages = []
    iris_ref = None
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name", "").lower(), d.version)):
        name = dist.metadata.get("Name") or "unknown"
        version = product_version if name.lower() == "iris" else dist.version
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
    try:
        sbom = build_sbom()
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        # 不静默退回已安装元数据：那会生成一份声明错误版本的 SBOM 且无失败信号。
        print(f"生成 SBOM 失败，无法确定产品版本：{exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
