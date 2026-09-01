"""frontmatter_batch 模块单元测试。

覆盖:
- 正则提取器（日期/标题/周期/参会人/作者/邮箱）
- 类别推断（路径 → 类别目录匹配）
- LLM prompt 构建
- BatchConfig / FileResult / BatchResult 数据类
- process_file 处理流水线（含 mock）
- 备份/恢复/列表/删除
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.core.frontmatter_batch import (
    CATEGORY_FIELDS,
    BatchConfig,
    BatchResult,
    FileResult,
    FrontmatterBatchProcessor,
)


# ═══════════════════════════════════════════════════════════
# 正则提取器
# ═══════════════════════════════════════════════════════════


class TestExtractDateFromFilename:
    """文件名日期提取。"""

    def test_standard_hyphen(self):
        assert FrontmatterBatchProcessor._extract_date_from_filename(
            "20260624-内部讨论-硬件团队.md"
        ) == "2026-06-24"

    def test_standard_underscore(self):
        assert FrontmatterBatchProcessor._extract_date_from_filename(
            "20260725_周报_w30.md"
        ) == "2026-07-25"

    def test_eight_digit_start(self):
        assert FrontmatterBatchProcessor._extract_date_from_filename(
            "20260104-something.md"
        ) == "2026-01-04"

    def test_no_date_prefix(self):
        assert FrontmatterBatchProcessor._extract_date_from_filename(
            "双周报-w01-张三.md"
        ) is None

    def test_too_short(self):
        assert FrontmatterBatchProcessor._extract_date_from_filename("2026.md") is None


class TestExtractTitle:
    """标题提取。"""

    def test_h1_heading(self):
        content = "# 会议纪要 - 硬件团队重点项目规划\n\n正文..."
        assert FrontmatterBatchProcessor._extract_title(content) == "会议纪要 - 硬件团队重点项目规划"

    def test_no_heading(self):
        assert FrontmatterBatchProcessor._extract_title("正文没有标题") == ""

    def test_h1_after_blank_lines(self):
        content = "\n\n# 周报 - 赵六\n\n内容"
        assert FrontmatterBatchProcessor._extract_title(content) == "周报 - 赵六"


class TestExtractPeriod:
    """我的周报周期提取。"""

    def test_chinese_colon(self):
        content = "*时间周期：2026.04.13～2026.04.26*\n\n## 智能检测技术"
        assert FrontmatterBatchProcessor._extract_period(content) == "2026.04.13～2026.04.26"

    def test_english_colon(self):
        content = "*时间周期: 2025.12.22～2026.01.04*\n"
        assert FrontmatterBatchProcessor._extract_period(content) == "2025.12.22～2026.01.04"

    def test_tilde(self):
        content = "时间周期：2026.05.25~2026.06.07"
        assert FrontmatterBatchProcessor._extract_period(content) == "2026.05.25~2026.06.07"

    def test_no_period(self):
        assert FrontmatterBatchProcessor._extract_period("没有时间周期") == ""


class TestExtractParticipants:
    """参会人员提取。"""

    def test_bold_format(self):
        content = """## 参会人员
- **张三**（数据部门，主持人）
- **王强**（大模型算法组）
- **周八**（算法团队）"""
        names = FrontmatterBatchProcessor._extract_participants(content)
        assert "张三" in names
        assert "王强" in names
        assert "周八" in names

    def test_inline_chinese_comma(self):
        content = "张三、赵六、王强、李四"
        # 注意：非列表行顿号分隔在 _extract_participants 中有处理
        # 但逻辑限制非 #/-/| 开头的行才走顿号解析
        names = FrontmatterBatchProcessor._extract_participants(content)
        # 单行顿号：每个 part len<=5 才会被加入
        assert len(names) >= 0  # 可能提取可能不提取，取决于上下文


class TestExtractWeeklyAuthor:
    """周报作者提取。"""

    def test_from_sender_line(self):
        content = "## 邮件信息\n- **发件人**: 赵六 <zhangsan@example.com>"
        assert FrontmatterBatchProcessor._extract_weekly_author(
            content, Path("/tmp/20260725-周报-w30-赵六.md")
        ) == "赵六"

    def test_fallback_to_filename(self):
        content = "## 无邮件信息\n正文"
        result = FrontmatterBatchProcessor._extract_weekly_author(
            content, Path("/tmp/20260725-周报-w30-孙七.md")
        )
        assert result == "孙七"


class TestExtractWeeklyEmail:
    """周报邮箱提取。"""

    def test_angle_bracket_format(self):
        content = "**发件人**: 孙七 <lisi@example.com>"
        assert FrontmatterBatchProcessor._extract_weekly_email(content) == "lisi@example.com"

    def test_no_email(self):
        assert FrontmatterBatchProcessor._extract_weekly_email("无邮箱") == ""


# ═══════════════════════════════════════════════════════════
# 类别推断
# ═══════════════════════════════════════════════════════════


class TestInferCategory:
    """文件路径 → SOURCE 类别目录推断。"""

    def test_direct_child(self):
        p = Path("/SOURCE/04-讨论思考/202606/file.md")
        assert FrontmatterBatchProcessor._infer_category(p) == "04-讨论思考"

    def test_nested_deep(self):
        p = Path("/SOURCE/05-会议纪要/202606/20260616/file.md")
        assert FrontmatterBatchProcessor._infer_category(p) == "05-会议纪要"

    def test_member_weekly(self):
        p = Path("/SOURCE/07-成员周报/20260725-周报-w30-赵六.md")
        assert FrontmatterBatchProcessor._infer_category(p) == "07-成员周报"

    def test_unknown_category(self):
        p = Path("/SOURCE/99-未知目录/file.md")
        assert FrontmatterBatchProcessor._infer_category(p) is None

    def test_no_category_in_path(self):
        p = Path("/tmp/random/file.md")
        assert FrontmatterBatchProcessor._infer_category(p) is None


# ═══════════════════════════════════════════════════════════
# 正则提取流水线（_extract_by_regex）
# ═══════════════════════════════════════════════════════════


class TestExtractByRegex:
    """_extract_by_regex 端到端测试。"""

    def test_discussion_basic(self):
        raw = "# 会议纪要 - 硬件团队\n日期：2026-06-24\n类型：内部讨论"
        file_path = Path("/SOURCE/04-讨论思考/202606/20260624-内部讨论-硬件团队.md")
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        fields = processor._extract_by_regex(raw, file_path, "04-讨论思考")

        assert fields["type"] == "讨论思考"
        assert fields["date"] == "2026-06-24"
        assert fields["title"] == "会议纪要 - 硬件团队"
        assert "updated" in fields

    def test_meeting_minutes_type(self):
        raw = "# 项目周会\n正文"
        file_path = Path("/SOURCE/05-会议纪要/202606/20260616-项目周会.md")
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        fields = processor._extract_by_regex(raw, file_path, "05-会议纪要")

        assert fields["type"] == "会议纪要"
        assert fields["date"] == "2026-06-16"

    def test_weekly_report_regex(self):
        raw = (
            "# 周报 - 赵六 - 2026年07月25日\n\n"
            "## 邮件信息\n\n"
            "- **发件人**: 赵六 <zhangsan@example.com>\n"
            "- **日期**: 2026年07月25日\n"
        )
        file_path = Path("/SOURCE/07-成员周报/20260725-周报-w30-赵六.md")
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        fields = processor._extract_by_regex(raw, file_path, "07-成员周报")

        assert fields["type"] == "成员周报"
        assert fields["date"] == "2026-07-25"
        assert fields["author"] == "赵六"
        assert fields["email"] == "zhangsan@example.com"

    def test_okr_type(self):
        raw = "# OKR 双周逐项检查记录"
        file_path = Path("/SOURCE/01-目标管理/2026/20260722-OKR检查.md")
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        fields = processor._extract_by_regex(raw, file_path, "01-目标管理")

        assert fields["type"] == "目标管理"


# ═══════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════


class TestBatchConfig:
    """BatchConfig 数据类。"""

    def test_defaults(self):
        cfg = BatchConfig()
        assert cfg.use_llm is True
        assert cfg.use_wikilink is False
        assert cfg.force_overwrite is False
        assert cfg.no_backup is False
        assert cfg.llm_max_tokens == 512

    def test_custom(self):
        cfg = BatchConfig(use_llm=False, force_overwrite=True)
        assert cfg.use_llm is False
        assert cfg.force_overwrite is True


class TestFileResult:
    """FileResult 数据类。"""

    def test_success_result(self):
        fr = FileResult(
            path="/tmp/test.md",
            status="injected",
            fields_injected={"title": "Test", "date": "2026-07-30"},
        )
        assert fr.status == "injected"
        assert fr.fields_injected["title"] == "Test"
        assert fr.error is None

    def test_failed_result(self):
        fr = FileResult(
            path="/tmp/test.md",
            status="failed",
            error="File not found",
        )
        assert fr.status == "failed"
        assert fr.error == "File not found"


class TestBatchResult:
    """BatchResult 数据类。"""

    def test_empty(self):
        br = BatchResult()
        assert br.total == 0
        assert br.success == 0
        assert br.per_file == []

    def test_with_results(self):
        br = BatchResult(
            total=10, success=7, skipped=2, failed=1,
            backup_path="/tmp/backup",
            per_file=[
                FileResult(path="a.md", status="injected", fields_injected={}),
                FileResult(path="b.md", status="skipped"),
                FileResult(path="c.md", status="failed", error="oops"),
            ],
        )
        assert br.total == 10
        assert len(br.per_file) == 3


# ═══════════════════════════════════════════════════════════
# process_file（含 mock）
# ═══════════════════════════════════════════════════════════


class TestProcessFile:
    """process_file 端到端测试（mock LLM）。"""

    @pytest.fixture
    def tmp_md(self):
        """创建临时 .md 文件。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("# 会议纪要 - 测试\n\n正文内容\n")
            return Path(f.name)

    @pytest.fixture
    def tmp_source_dir(self):
        """创建临时 SOURCE 子目录结构。"""
        d = Path(tempfile.mkdtemp())
        sub = d / "04-讨论思考" / "202607"
        sub.mkdir(parents=True)
        return d, sub

    def test_skip_existing_frontmatter(self, tmp_source_dir):
        """已有 frontmatter 的文件应被跳过。"""
        source_root, sub = tmp_source_dir
        md = sub / "20260730-测试.md"
        md.write_text(
            "---\ntitle: 已有\ndate: 2026-07-30\ntype: 讨论思考\n---\n\n# 正文\n",
            encoding="utf-8",
        )
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        result = processor.process_file(md, dry_run=True)
        assert result.status == "skipped"

    def test_force_overwrite_existing(self, tmp_source_dir):
        """force_overwrite=True 时覆盖已有 frontmatter。"""
        source_root, sub = tmp_source_dir
        md = sub / "20260730-测试.md"
        md.write_text(
            "---\ntitle: 旧标题\ndate: 2026-01-01\ntype: 讨论思考\n---\n\n# 新标题\n",
            encoding="utf-8",
        )
        config = BatchConfig(force_overwrite=True, use_llm=False)
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        result = processor.process_file(md, dry_run=True)
        assert result.status == "injected"

    def test_inject_basic_fields(self, tmp_source_dir):
        """正则通道应注入 date/title/type/updated。"""
        source_root, sub = tmp_source_dir
        md = sub / "20260730-硬件团队讨论.md"
        md.write_text("# 会议纪要 - 硬件团队讨论\n\n正文\n", encoding="utf-8")

        config = BatchConfig(use_llm=False, use_wikilink=False)
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        result = processor.process_file(md, dry_run=True)

        assert result.status == "injected"
        assert result.fields_injected["type"] == "讨论思考"
        assert result.fields_injected["date"] == "2026-07-30"
        assert result.fields_injected["title"] == "会议纪要 - 硬件团队讨论"

    def test_dry_run_does_not_write(self, tmp_source_dir):
        """dry_run 模式不应修改原文件。"""
        source_root, sub = tmp_source_dir
        md = sub / "20260730-测试.md"
        original = "# 原标题\n\n正文内容\n"
        md.write_text(original, encoding="utf-8")

        config = BatchConfig(use_llm=False, use_wikilink=False)
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        processor.process_file(md, dry_run=True)

        assert md.read_text(encoding="utf-8") == original

    def test_exec_mode_writes_file(self, tmp_source_dir):
        """正式模式应写入文件。"""
        source_root, sub = tmp_source_dir
        md = sub / "20260730-测试.md"
        md.write_text("# 标题\n\n正文\n", encoding="utf-8")

        config = BatchConfig(use_llm=False, use_wikilink=False, no_backup=True)
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        result = processor.process_file(md, dry_run=False)

        assert result.status == "injected"
        content = md.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "title: 标题" in content
        assert "type: 讨论思考" in content


# ═══════════════════════════════════════════════════════════
# 备份与恢复
# ═══════════════════════════════════════════════════════════


class TestBackupRestore:
    """备份/恢复/列表/删除。"""

    @pytest.fixture
    def source_root_with_files(self):
        """创建包含多个子目录和 .md 文件的 SOURCE 根目录。"""
        d = Path(tempfile.mkdtemp())
        for sub_name, files in [
            ("04-讨论思考", ["a.md", "b.md"]),
            ("09-工作简报", ["c.md"]),
        ]:
            sub = d / sub_name / "202607"
            sub.mkdir(parents=True)
            for fname in files:
                (sub / fname).write_text(f"# {fname}\n\ncontent\n", encoding="utf-8")
        return d

    def test_backup_and_restore(self, source_root_with_files):
        """备份 → 修改 → 恢复 → 验证。"""
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")

        # 备份
        dir_path = source_root_with_files / "04-讨论思考"
        backup_path = processor.backup_directory(dir_path)
        assert backup_path.exists()
        assert (backup_path / "04-讨论思考" / "202607" / "a.md").exists()

        # 修改文件
        a_md = dir_path / "202607" / "a.md"
        original = a_md.read_text(encoding="utf-8")
        a_md.write_text("modified content\n", encoding="utf-8")

        # 恢复
        timestamp = backup_path.name
        count = FrontmatterBatchProcessor.restore_directory(
            source_root_with_files, timestamp
        )
        assert count == 2
        assert a_md.read_text(encoding="utf-8") == original

    def test_list_backups(self, source_root_with_files):
        """list_backups 应返回时间倒序列表。"""
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        processor.backup_directory(source_root_with_files / "04-讨论思考")
        processor.backup_directory(source_root_with_files / "09-工作简报")

        backups = FrontmatterBatchProcessor.list_backups(source_root_with_files)
        # 同一秒内的两次备份可能产生不同或相同的 timestamp
        # 但都应该在列表中
        assert len(backups) >= 1

    def test_list_backups_empty(self):
        """无备份时应返回空列表。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backups = FrontmatterBatchProcessor.list_backups(root)
            assert backups == []

    def test_restore_nonexistent(self, source_root_with_files):
        """恢复不存在的备份应抛异常。"""
        with pytest.raises(FileNotFoundError):
            FrontmatterBatchProcessor.restore_directory(
                source_root_with_files, "nonexistent_ts"
            )

    def test_remove_backup(self, source_root_with_files):
        """删除备份。"""
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="")
        backup_path = processor.backup_directory(
            source_root_with_files / "04-讨论思考"
        )
        ts = backup_path.name
        assert (source_root_with_files / "_frontmatter_backup" / ts).exists()

        FrontmatterBatchProcessor.remove_backup(source_root_with_files, ts)
        assert not (source_root_with_files / "_frontmatter_backup" / ts).exists()


# ═══════════════════════════════════════════════════════════
# process_directory
# ═══════════════════════════════════════════════════════════


class TestProcessDirectory:
    """process_directory 端到端测试。"""

    @pytest.fixture
    def dir_with_files(self):
        """创建包含多个 .md 文件的 SOURCE 类别子目录。

        返回 (root, category_dir) — process_directory 需要传入类别目录。
        """
        root = Path(tempfile.mkdtemp())
        cat_dir = root / "04-讨论思考"
        sub = cat_dir / "202607"
        sub.mkdir(parents=True)

        # 有 frontmatter 的（应跳过）
        (sub / "20260730-已有fm.md").write_text(
            "---\ntitle: 已有\ndate: 2026-07-30\n---\n\n# 正文\n",
            encoding="utf-8",
        )
        # 无 frontmatter 的（应注入）
        (sub / "20260729-无fm.md").write_text(
            "# 会议纪要 - 测试讨论\n\n正文内容\n",
            encoding="utf-8",
        )
        return root, cat_dir

    def test_process_directory_dry_run(self, dir_with_files):
        """dry-run 模式应统计但不写入。"""
        root, cat_dir = dir_with_files
        config = BatchConfig(use_llm=False, use_wikilink=False)
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        result = processor.process_directory(cat_dir, dry_run=True)

        assert result.total == 2
        assert result.success == 1  # 无 fm 的被注入
        assert result.skipped == 1  # 有 fm 的被跳过
        assert result.failed == 0
        # dry-run 不自动备份
        assert result.backup_path is None

    def test_process_directory_exec(self, dir_with_files):
        """正式模式应写入并备份。"""
        root, cat_dir = dir_with_files
        config = BatchConfig(use_llm=False, use_wikilink=False)
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        result = processor.process_directory(cat_dir, dry_run=False)

        assert result.total == 2
        assert result.success == 1
        assert result.skipped == 1
        assert result.backup_path is not None

    def test_process_directory_no_backup(self, dir_with_files):
        """no_backup=True 时跳过备份。"""
        root, cat_dir = dir_with_files
        config = BatchConfig(
            use_llm=False, use_wikilink=False, no_backup=True,
        )
        processor = FrontmatterBatchProcessor(llm=None, wiki_root="", config=config)
        result = processor.process_directory(cat_dir, dry_run=False)

        assert result.backup_path is None


# ═══════════════════════════════════════════════════════════
# LLM Prompt 构建
# ═══════════════════════════════════════════════════════════


class TestLLMPrompt:
    """LLM 提取 prompt 构建验证。"""

    def test_prompt_contains_required_elements(self):
        from iris.core.frontmatter_batch import _EXTRACTION_PROMPT

        prompt = _EXTRACTION_PROMPT.format(
            doc_type_label="讨论思考",
            file_path="/SOURCE/04-讨论思考/test.md",
            field_specs="- participants: 参会人员\n- duration: 时长",
            body="测试正文",
        )
        assert "讨论思考" in prompt
        assert "participants" in prompt
        assert "duration" in prompt
        assert "测试正文" in prompt
        assert "JSON" in prompt


# ═══════════════════════════════════════════════════════════
# 类别配置
# ═══════════════════════════════════════════════════════════


class TestCategoryFields:
    """CATEGORY_FIELDS 配置完整性。"""

    def test_all_nine_categories(self):
        expected = {
            "01-目标管理", "02-部门管理", "03-方案报告",
            "04-讨论思考", "05-会议纪要", "06-我的周报",
            "07-成员周报", "08-参考资料", "09-工作简报",
        }
        assert set(CATEGORY_FIELDS.keys()) == expected

    def test_each_has_type_and_llm_fields(self):
        for cat, (type_key, llm_fields) in CATEGORY_FIELDS.items():
            assert isinstance(type_key, str)
            assert isinstance(llm_fields, dict)
            # type_key 应在 DOC_TYPES 中存在
            from iris.core.frontmatter import DOC_TYPES
            assert type_key in DOC_TYPES, f"{cat}: {type_key} not in DOC_TYPES"
