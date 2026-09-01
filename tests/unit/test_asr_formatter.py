"""asr/formatter.py 单元测试 — 热词文件、替换词典、Prompt 渲染。

覆盖 format_hotwords_file, format_replace_dict, render_asr_prompt
（standard + compact 两格式）、_render_compact 各分支。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


from iris.wiki.asr._types import AsrTerm, AsrPromptVersion
from iris.wiki.asr.formatter import (
    format_hotwords_file,
    format_replace_dict,
    render_asr_prompt,
)


def _make_term(term: str, category: str = "concept", *,
               context: str = "", mis_asr: list[str] | None = None) -> AsrTerm:
    return AsrTerm(
        term=term, category=category, context=context,
        mis_asr=mis_asr or [],
    )


def _make_version(**kwargs) -> AsrPromptVersion:
    defaults = {
        "version": "3.0.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "wiki_page_count": 10,
        "term_count": 5,
        "fingerprint": "a1b2c3d4e5f6a7b8",
    }
    defaults.update(kwargs)
    return AsrPromptVersion(**defaults)


# ── format_hotwords_file ──────────────────────────────────────────


class TestFormatHotwordsFile:
    """测试热词文件写入与去重。"""

    def test_basic_write(self, tmp_path):
        path = str(tmp_path / "hotwords.txt")
        result = format_hotwords_file(["张三", "李四"], path)
        content = Path(result).read_text(encoding="utf-8")
        assert "张三" in content
        assert "李四" in content
        assert result == path

    def test_case_insensitive_dedup(self, tmp_path):
        """大小写不敏感的重复去重。"""
        path = str(tmp_path / "hotwords.txt")
        result = format_hotwords_file(["Foo", "foo", "FOO"], path)
        content = Path(result).read_text(encoding="utf-8").strip().split("\n")
        # 只保留首次出现
        assert content == ["Foo"]

    def test_whitespace_insensitive_dedup(self, tmp_path):
        """去空格后的重复去重。"""
        path = str(tmp_path / "hotwords.txt")
        result = format_hotwords_file(["hello world", "helloworld"], path)
        content = Path(result).read_text(encoding="utf-8").strip().split("\n")
        assert content == ["hello world"]

    def test_creates_parent_dir(self, tmp_path):
        """自动创建父目录。"""
        path = str(tmp_path / "sub" / "deep" / "hotwords.txt")
        result = format_hotwords_file(["test"], path)
        assert Path(result).exists()

    def test_preserves_order(self, tmp_path):
        """保持原始顺序。"""
        path = str(tmp_path / "hotwords.txt")
        words = ["C", "A", "B", "a", "c"]
        format_hotwords_file(words, path)
        content = Path(str(path)).read_text(encoding="utf-8").strip().split("\n")
        assert content == ["C", "A", "B"]


# ── format_replace_dict ───────────────────────────────────────────


class TestFormatReplaceDict:
    """测试替换词典生成与过滤逻辑。"""

    def test_basic_mapping(self, tmp_path):
        path = str(tmp_path / "replace.json")
        terms = [_make_term("张三", "person", mis_asr=["张3"])]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert data["replace_map"]["张3"] == "张三"

    def test_dangerous_mapping_skipped(self, tmp_path):
        """单字高频词应在高危过滤中被跳过。"""
        path = str(tmp_path / "replace.json")
        terms = [_make_term("测试", "concept", mis_asr=["在"])]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert "在" not in data["replace_map"]

    def test_self_mapping_excluded(self, tmp_path):
        """mis == term 被排除。"""
        path = str(tmp_path / "replace.json")
        terms = [_make_term("张三", "person", mis_asr=["张三"])]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert "张三" not in data["replace_map"]

    def test_empty_mis_asr(self, tmp_path):
        """无误识别词的术语不产生映射。"""
        path = str(tmp_path / "replace.json")
        terms = [_make_term("张三", "person", mis_asr=[])]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert data["replace_map"] == {}

    def test_dedup_across_terms(self, tmp_path):
        """多个术语产生相同 mis 时去重。"""
        path = str(tmp_path / "replace.json")
        terms = [
            _make_term("张三", "person", mis_asr=["张3"]),
            _make_term("章三", "person", mis_asr=["张3"]),
        ]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert data["replace_map"]["张3"] == "张三"

    def test_max_mappings_cap(self, tmp_path):
        """映射数量达到上限时截断。"""
        path = str(tmp_path / "replace.json")
        terms = [
            _make_term(f"term{i}", "concept", mis_asr=[f"mis{i}"])
            for i in range(20)
        ]
        result = format_replace_dict(terms, path, max_mappings=10)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert len(data["replace_map"]) <= 10

    def test_cross_term_conflict_skipped(self, tmp_path):
        """v3.24 交叉冲突防护：误识别词 == 其他正确术语时跳过——
        音近人名场景（A 的误识别恰好是 B 的正确姓名），否则替换会误伤真实人名。"""
        path = str(tmp_path / "replace.json")
        terms = [
            _make_term("李磊", "person", mis_asr=["李雷"]),
            _make_term("李雷", "person", mis_asr=["李累"]),
        ]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        # "李雷" 是正确术语 → "李雷→李磊" 被跳过；"李累→李雷" 保留
        assert "李雷" not in data["replace_map"]
        assert data["replace_map"]["李累"] == "李雷"

    def test_cross_term_conflict_normalized_key(self, tmp_path):
        """规范化后重合也跳过（去空格/大小写）。"""
        path = str(tmp_path / "replace.json")
        terms = [
            _make_term("AI检测", "concept", mis_asr=["ai检测"]),
            _make_term("ai 检测", "concept", mis_asr=["ai检捡"]),
        ]
        result = format_replace_dict(terms, path)
        data = json.loads(Path(result).read_text(encoding="utf-8"))
        assert "ai检测" not in data["replace_map"]  # 规范化后 == "AI检测"
        assert data["replace_map"]["ai检捡"] == "ai 检测"

    def test_creates_parent_dir(self, tmp_path):
        path = str(tmp_path / "sub" / "replace.json")
        result = format_replace_dict(
            [_make_term("test", "concept", mis_asr=["tst"])], path,
        )
        assert Path(result).exists()


# ── render_asr_prompt (standard) ─────────────────────────────────


class TestRenderAsrPromptStandard:
    """测试 standard 格式的 ASR Prompt 渲染。"""

    def test_person_table(self):
        version = _make_version()
        terms = [_make_term("张三", "person", context="研发", mis_asr=["张3"])]
        result = render_asr_prompt(terms, version, output_format="standard")
        assert "## 人名词典" in result
        assert "张三" in result
        assert "张3" in result

    def test_concept_table(self):
        version = _make_version()
        terms = [_make_term("RAG", "concept", context="检索增强", mis_asr=["rag"])]
        result = render_asr_prompt(terms, version, output_format="standard")
        assert "## 术语词典" in result
        assert "RAG" in result

    def test_project_table(self):
        version = _make_version()
        terms = [_make_term("Alpha", "project")]
        result = render_asr_prompt(terms, version, output_format="standard")
        assert "## 项目名词典" in result

    def test_domain_term_table(self):
        """领域专有名词分支（未覆盖行 182-189）。"""
        version = _make_version()
        terms = [_make_term("BERT", "domain_term", context="算法")]
        result = render_asr_prompt(terms, version, output_format="standard")
        assert "## 领域专有名词" in result
        assert "BERT" in result

    def test_mis_with_dash(self):
        """无常见误识别时显示 '-'。"""
        version = _make_version()
        terms = [_make_term("张三", "person")]
        result = render_asr_prompt(terms, version, output_format="standard")
        assert "| - |" in result or "|-|" in result.replace(" ", "")

    def test_empty_terms(self):
        version = _make_version()
        result = render_asr_prompt([], version, output_format="standard")
        assert "通用校正规则" in result

    def test_version_footer(self):
        version = _make_version(version="3.2.1")
        result = render_asr_prompt([], version, output_format="standard")
        assert "v3.2.1" in result


# ── render_asr_prompt (compact) ───────────────────────────────────


class TestRenderAsrPromptCompact:
    """测试 compact 格式的 ASR Prompt 渲染（未覆盖分支）。"""

    def test_person_block(self):
        version = _make_version()
        terms = [_make_term("张三", "person", context="研发")]
        result = render_asr_prompt(terms, version, output_format="compact")
        assert "【人名】" in result
        assert "张三=person,研发" in result.replace(" ", "")

    def test_concept_block(self):
        """术语块（未覆盖行 227）。"""
        version = _make_version()
        terms = [_make_term("RAG", "concept")]
        result = render_asr_prompt(terms, version, output_format="compact")
        assert "【术语】" in result

    def test_project_block(self):
        """项目块（未覆盖行 229）。"""
        version = _make_version()
        terms = [_make_term("Alpha", "project")]
        result = render_asr_prompt(terms, version, output_format="compact")
        assert "【项目】" in result

    def test_domain_block(self):
        """领域块（未覆盖行 231）。"""
        version = _make_version()
        terms = [_make_term("BERT", "domain_term")]
        result = render_asr_prompt(terms, version, output_format="compact")
        assert "【领域】" in result

    def test_term_with_mis(self):
        """带误识别词的术语包含勿误标记。"""
        version = _make_version()
        terms = [_make_term("张三", "person", mis_asr=["张3", "章三"])]
        result = render_asr_prompt(terms, version, output_format="compact")
        assert "勿误:张3/章三" in result.replace(" ", "")

    def test_term_no_mis_no_context(self):
        """无误识别、无上下文的术语（未覆盖行 222 else 分支）。"""
        version = _make_version()
        terms = [_make_term("foo", "concept")]
        result = render_asr_prompt(terms, version, output_format="compact")
        assert "foo=concept" in result.replace(" ", "")
        assert "勿误" not in result  # 无 mis 不应有勿误标记

    def test_version_footer(self):
        version = _make_version(version="1.0.0")
        result = render_asr_prompt([], version, output_format="compact")
        assert "v1.0.0" in result
