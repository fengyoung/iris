"""M14 原子写入 + M5 日志归档 + M9 常量统一 专项测试。"""

import os


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
        content = "项目Beta 里程碑 🎯 手机 全量标准化\n\n中文内容测试"

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


# ── v3.8.1: query 文本路径提取 ──────────────────────────────

class TestExtractFilePaths:
    """验证 extract_file_paths_from_text 路径提取逻辑。"""

    def test_absolute_path(self, tmp_path):
        """绝对路径能正确提取。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        png = tmp_path / "test.png"
        png.write_text("fake png")
        query = f"分析这张图片 {png}"
        result = extract_file_paths_from_text(query)
        assert result == [str(png.resolve())]

    def test_nonexistent_path(self):
        """不存在的路径不返回。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        query = "分析 /nonexistent/file.png"
        result = extract_file_paths_from_text(query)
        assert result == []

    def test_no_path_in_text(self):
        """纯文本不含路径时返回空列表。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        result = extract_file_paths_from_text("今天有什么新闻")
        assert result == []

    def test_unsupported_extension(self, tmp_path):
        """不支持扩展名的文件不被提取。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        query = f"打开 {f}"
        result = extract_file_paths_from_text(query)
        assert result == []

    def test_chinese_text_surrounding_path(self, tmp_path):
        """中文文本与路径之间有空格时能提取（路径前后紧挨中文无空格时不支持）。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        png = tmp_path / "截图.png"
        png.write_text("fake")
        # 路径前后有空格 → 可提取
        query = f"帮我分析 {png} 这个截图的内容"
        result = extract_file_paths_from_text(query)
        assert result == [str(png.resolve())]
        # 路径前后无空格 → 不分词，不可提取（设计约束）
        query2 = f"帮我分析{png}这个截图的内容"
        result2 = extract_file_paths_from_text(query2)
        assert result2 == []

    def test_multiple_paths(self, tmp_path):
        """多个文件路径都能提取。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        p1 = tmp_path / "a.png"
        p2 = tmp_path / "b.jpg"
        p1.write_text("fake")
        p2.write_text("fake")
        query = f"对比 {p1} 和 {p2}"
        result = extract_file_paths_from_text(query)
        assert len(result) == 2
        assert str(p1.resolve()) in result
        assert str(p2.resolve()) in result

    def test_pdf_path(self, tmp_path):
        """PDF 路径也能被提取（非图片类型走 Stage 2 跳过）。"""
        from iris.complex_input.detector import extract_file_paths_from_text
        pdf = tmp_path / "report.pdf"
        pdf.write_text("fake pdf")
        query = f"分析报告 {pdf}"
        result = extract_file_paths_from_text(query)
        assert result == [str(pdf.resolve())]

    def test_detector_uses_query_paths(self, tmp_path):
        """detector.detect() 从 query 提取路径后正确设置 is_complex=True。"""
        from iris.complex_input.detector import InputDetector
        png = tmp_path / "photo.png"
        png.write_text("fake")
        query = f"看图 {png}"
        result = InputDetector().detect(query)
        assert result.is_complex is True
        assert str(png.resolve()) in result.file_paths


# ── v3.28.1: IrisLogger Pydantic 配置兼容 ────────────────────

class TestLoggerPydanticConfig:
    """v3.28.1 回归：Pydantic 配置（非 dict）下 log_to_file 必须生效。

    历史 bug：`isinstance(config.app, dict)` 守卫在 v3.19 Pydantic 迁移后
    恒 False，logging_cfg 恒空 → 即使配置 log_to_file: true 文件日志也
    永远关闭，结构化日志整体失效（测试全用 plain-dict FakeConfig 未暴露）。
    """

    def _make_pydantic_like_config(self, tmp_path):
        """模拟 BaseConfigModel：非 dict 但带 dict 风格 .get()。"""

        class FakeAppModel:
            """非 dict、有 .get() —— 与 Pydantic BaseConfigModel 行为一致。"""

            def __init__(self, data):
                self._data = data

            def get(self, key, default=None):
                return self._data.get(key, default)

        class FakeConfig:
            app = FakeAppModel({
                "logging": {"log_to_file": True, "log_to_console": False},
                "paths": {"log_dir": "./logs"},
            })
            root = tmp_path

        return FakeConfig()

    def test_log_to_file_enabled_with_pydantic_config(self, tmp_path):
        from iris.utils.logging import IrisLogger

        logger = IrisLogger(self._make_pydantic_like_config(tmp_path))
        assert logger._enabled is True, "Pydantic 配置下 log_to_file 必须生效（回归核心断言）"

    def test_log_writes_file_with_pydantic_config(self, tmp_path):
        from iris.utils.logging import IrisLogger

        logger = IrisLogger(self._make_pydantic_like_config(tmp_path))
        logger.log("test_event", {"key": "value"})
        assert logger.log_path.exists(), "日志文件必须真实写入"
        assert "test_event" in logger.log_path.read_text(encoding="utf-8")

    def test_log_dir_relative_path_not_mangled(self, tmp_path):
        """`./logs` 交由 pathlib 规范化，不再用 replace('./','') 破坏路径。"""
        from iris.utils.logging import IrisLogger

        logger = IrisLogger(self._make_pydantic_like_config(tmp_path))
        assert logger.log_path == tmp_path / "logs" / "iris.jsonl"
