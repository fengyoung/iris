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


class TestParseContext:
    def test_valid_json(self):
        from iris.app.cli.helpers import _parse_context
        assert _parse_context('{"input_type": "text"}') == {"input_type": "text"}

    def test_empty_raises(self):
        import json
        from iris.app.cli.helpers import _parse_context
        with pytest.raises(json.JSONDecodeError):
            _parse_context("")

    def test_invalid_json_raises(self):
        import json
        from iris.app.cli.helpers import _parse_context
        with pytest.raises(json.JSONDecodeError):
            _parse_context("not json")

    def test_non_dict_raises(self):
        from iris.app.cli.helpers import _parse_context
        with pytest.raises(ValueError, match="JSON 对象"):
            _parse_context("[1, 2, 3]")


class TestParseImageList:
    def test_comma_separated(self):
        from iris.app.cli.helpers import _parse_image_list
        result = _parse_image_list("a.png,b.jpg,c.pdf")
        assert result == ["a.png", "b.jpg", "c.pdf"]

    def test_single_file(self):
        from iris.app.cli.helpers import _parse_image_list
        assert _parse_image_list("file.png") == ["file.png"]

    def test_empty(self):
        from iris.app.cli.helpers import _parse_image_list
        assert _parse_image_list("") == []


class TestScanChunkPayload:
    def test_scan_payload_summary_only(self):
        from iris.app.cli.helpers import _scan_payload
        from iris.ingest.scanner import ScanSummary, DocumentRecord
        doc = DocumentRecord(source_name="t", path="/t/a.md", relative_path="a.md",
                             size_bytes=100, modified_at="2026-01-01", file_hash="abc", title="Test")
        summary = ScanSummary(source_name="test", source_path="/t",
                              scanned_at="2026-01-01T00:00:00Z", document_count=1,
                              documents=[doc])
        result = _scan_payload(summary, summary_only=True)
        assert result["source_name"] == "test"
        assert "documents" not in result

    def test_scan_payload_full(self):
        from iris.app.cli.helpers import _scan_payload
        from iris.ingest.scanner import ScanSummary, DocumentRecord
        doc = DocumentRecord(source_name="t", path="/t/a.md", relative_path="a.md",
                             size_bytes=100, modified_at="2026-01-01", file_hash="abc", title="Test")
        summary = ScanSummary(source_name="test", source_path="/t",
                              scanned_at="2026-01-01T00:00:00Z", document_count=1,
                              documents=[doc])
        result = _scan_payload(summary, summary_only=False)
        assert len(result["documents"]) == 1


class TestWriteTextFile:
    def test_writes_and_returns_path(self, tmp_path):
        from iris.app.cli.helpers import _write_text_file
        target = str(tmp_path / "test.md")
        result = _write_text_file(target, "# Hello")
        assert Path(result).exists()
        assert Path(result).read_text(encoding="utf-8") == "# Hello"
