"""M14 原子写入 + M5 日志归档 + M9 常量统一 专项测试。"""

import os
import pytest
from pathlib import Path


# ── M14: _atomic_write 原子写入 ──────────────────────────────

class TestAtomicWrite:
    """验证 _atomic_write 使用 os.replace 原子写入。"""

    def test_atomic_write_creates_file(self, tmp_path):
        """原子写入成功创建文件。"""
        from iris.wiki.navigation import _atomic_write

        target = tmp_path / "test.md"
        target.write_text("original content", encoding="utf-8")

        _atomic_write(target, "new content")

        assert target.read_text(encoding="utf-8") == "new content"

    def test_atomic_write_does_not_leave_temp_file(self, tmp_path):
        """原子写入完成后不残留临时文件。"""
        from iris.wiki.navigation import _atomic_write

        target = tmp_path / "test.md"
        target.write_text("original", encoding="utf-8")

        _atomic_write(target, "updated")

        # 不应有 .tmp 文件残留
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"不应残留 .tmp 文件: {tmp_files}"

    def test_atomic_write_handles_new_file(self, tmp_path):
        """对不存在的文件也能原子写入。"""
        from iris.wiki.navigation import _atomic_write

        target = tmp_path / "new_file.md"
        _atomic_write(target, "fresh content")

        assert target.read_text(encoding="utf-8") == "fresh content"

    def test_atomic_write_unicode_content(self, tmp_path):
        """原子写入支持 Unicode 内容。"""
        from iris.wiki.navigation import _atomic_write

        target = tmp_path / "unicode.md"
        content = "图像采集3.0 项目里程碑 🎯 iPhone 全量标准化\n\n中文内容测试"

        _atomic_write(target, content)
        assert target.read_text(encoding="utf-8") == content


# ── M5: IrisLogger 原子归档 ──────────────────────────────────

class TestLoggerRotation:
    """验证日志归档使用 os.rename 原子操作。"""

    def test_rotation_uses_rename(self, tmp_path, monkeypatch):
        """归档时调用 os.rename 而非 read+write。"""
        from iris.utils.logging import IrisLogger

        log_path = tmp_path / "iris.jsonl"
        # 创建超过 10MB 的文件模拟
        log_path.write_text("x" * (11 * 1024 * 1024), encoding="utf-8")

        class FakeConfig:
            app = {
                "logging": {"log_to_file": True},
                "paths": {"log_dir": str(tmp_path)},
            }
            root = tmp_path

        rename_called = [False]

        def fake_rename(src, dst):
            rename_called[0] = True

        monkeypatch.setattr(os, "rename", fake_rename)

        logger = IrisLogger(FakeConfig())
        # 强制路径指向我们的测试文件
        logger._log_path = log_path

        # 写入一条日志触发归档检查
        logger.log("test_event", {"key": "value"})

        assert rename_called[0], "归档应使用 os.rename"


# ── M9: IMAGE_EXTENSIONS 常量统一 ────────────────────────────

class TestImageConstants:
    """验证图片扩展名常量统一且跨模块一致。"""

    def test_detector_uses_shared_constants(self):
        """complex_input/detector.py 使用 utils/constants.py。"""
        from iris.complex_input.detector import IMAGE_EXTENSIONS, MIME_MAP
        from iris.utils.constants import IMAGE_EXTENSIONS as SHARED, IMAGE_MIME_MAP

        assert IMAGE_EXTENSIONS is SHARED, "应引用同一 frozenset 对象"
        assert MIME_MAP is IMAGE_MIME_MAP, "应引用同一 dict 对象"

    def test_doc_convert_uses_shared_constants(self):
        """feishu/doc_convert.py 使用 shared constants。"""
        from iris.feishu.doc_convert import _PIC_EXTENSIONS
        from iris.utils.constants import IMAGE_EXTENSIONS_WITH_SVG

        assert _PIC_EXTENSIONS is IMAGE_EXTENSIONS_WITH_SVG

    def test_image_constants_include_common_formats(self):
        """常量包含常见图片格式。"""
        from iris.utils.constants import IMAGE_EXTENSIONS
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            assert ext in IMAGE_EXTENSIONS, f"缺少 {ext}"

    def test_svg_included_only_in_extended_set(self):
        """SVG 仅包含在扩展集中。"""
        from iris.utils.constants import IMAGE_EXTENSIONS, IMAGE_EXTENSIONS_WITH_SVG
        assert ".svg" not in IMAGE_EXTENSIONS
        assert ".svg" in IMAGE_EXTENSIONS_WITH_SVG


# ── H8: LLM 解析工具 ─────────────────────────────────────────

class TestLlmParsing:
    """验证 strip_code_fence 和 try_parse_json 工具函数。"""

    def test_strip_code_fence_json(self):
        from iris.utils.llm_parsing import strip_code_fence

        text = '```json\n{"key": "value"}\n```'
        assert strip_code_fence(text) == '{"key": "value"}'

    def test_strip_code_fence_markdown(self):
        from iris.utils.llm_parsing import strip_code_fence

        text = '```markdown\n# Title\nContent\n```'
        assert strip_code_fence(text) == '# Title\nContent'

    def test_strip_code_fence_no_fence(self):
        from iris.utils.llm_parsing import strip_code_fence

        text = 'plain text without fence'
        assert strip_code_fence(text) == 'plain text without fence'

    def test_try_parse_json_valid(self):
        from iris.utils.llm_parsing import try_parse_json

        result = try_parse_json('```json\n{"name": "test", "value": 42}\n```')
        assert result == {"name": "test", "value": 42}

    def test_try_parse_json_truncated(self):
        from iris.utils.llm_parsing import try_parse_json

        # 截断的 JSON（末尾缺 }）
        result = try_parse_json('{"name": "test", "value": 42')
        assert result == {"name": "test", "value": 42}

    def test_try_parse_json_invalid(self):
        from iris.utils.llm_parsing import try_parse_json

        result = try_parse_json('this is not json at all')
        assert result is None

    def test_extract_json_from_text(self):
        from iris.utils.llm_parsing import extract_json_from_text

        text = '这里是一些解释文字 {"items": ["a", "b"], "count": 2} 还有一些后续内容'
        result = extract_json_from_text(text, "items")
        assert result == {"items": ["a", "b"], "count": 2}


# ── H7: Prompt 模板加载降级 ─────────────────────────────────

class TestPromptTemplateFallback:
    """验证 generator.py 的模板加载降级路径。"""

    def test_load_template_returns_none_for_missing(self):
        """模板文件不存在时返回 None。"""
        from iris.wiki.generator import WikiGenerator

        result = WikiGenerator._load_template("nonexistent/template.txt")
        assert result is None

    def test_load_template_returns_content_for_existing(self):
        """模板文件存在时返回内容。"""
        from iris.wiki.generator import WikiGenerator

        # templates/wiki/generate_generic.txt 已创建
        result = WikiGenerator._load_template("wiki/generate_generic.txt")
        assert result is not None
        assert "知识库编辑助手" in result

    def test_build_generic_prompt_uses_template(self):
        """_build_generic_prompt 优先从模板加载。"""
        # 间接验证：调用 _build_generic_prompt，结果应包含模板内容
        from iris.wiki.generator import WikiGenerator
        from iris.config.loader import ConfigBundle

        # 构造最小 generator 实例
        gen = WikiGenerator.__new__(WikiGenerator)
        prompt = gen._build_generic_prompt(
            type_name="项目", page_type="project",
            title="测试项目", query="测试",
            evidence="测试证据", related="测试关联",
            now="2026-06-29",
        )
        assert "知识库编辑助手" in prompt
        assert "测试项目" in prompt
