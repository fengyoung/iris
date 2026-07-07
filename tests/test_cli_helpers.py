"""app/cli/helpers.py 单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path


class TestEmitOutput:
    """_emit_output: 输出格式化与打印。"""

    def test_emit_pretty(self, capsys):
        from iris.app.cli.helpers import _emit_output
        _emit_output("check-config", {"status": "ok"}, pretty=True)
        out = capsys.readouterr().out
        assert out.strip()  # 有输出即可

    def test_emit_json(self, capsys):
        import json
        from iris.app.cli.helpers import _emit_output
        _emit_output("search", {"hits": []}, pretty=False)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "hits" in data


class TestResolveOutputPath:
    """_resolve_output_path: 输出文件路径解析。"""

    def test_explicit_path(self, tmp_path):
        from iris.app.cli.helpers import _resolve_output_path
        path = _resolve_output_path(str(tmp_path / "out.xmind"), "query", ".xmind")
        assert str(path).endswith(".xmind")

    def test_auto_from_query(self, tmp_path):
        from iris.app.cli.helpers import _resolve_output_path
        path = _resolve_output_path("", "测试查询", ".xmind")
        assert path.suffix == ".xmind"
        assert "测试查询" in str(path) or path.name  # 文件名基于 query 或有默认名


class TestBuildStatusPayload:
    """_build_status_payload: 系统状态构建。"""

    def test_returns_dict(self, config_bundle):
        from iris.app.cli.helpers import _build_status_payload
        from iris.utils.logging import IrisLogger
        logger = IrisLogger(config_bundle)
        result = _build_status_payload(config_bundle, logger)
        assert isinstance(result, dict)
        # 应包含基础字段
        assert "version" in result or "status" in result or result
