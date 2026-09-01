"""feed 包单元测试 — OKR 加载 / 解析 / 查询。"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from iris.feed._okr_loader import (
    KR,
    Objective,
    OKRDocument,
    OKRLoader,
    _find_latest_okr_file,
    _parse_okr_file,
)


# ═══════════════════════════════════════════════════════════════
# KR 数据类测试
# ═══════════════════════════════════════════════════════════════

class TestKR:
    """KR 数据类测试。"""

    def test_create_basic(self):
        """创建基本 KR 实例。"""
        kr = KR(kr_id="O1-KR1", title="【质量】图像验证主观项检测", short_title="【质量】")
        assert kr.kr_id == "O1-KR1"
        assert kr.title == "【质量】图像验证主观项检测"
        assert kr.owner == ""

    def test_create_with_owner(self):
        """创建带 Owner 的 KR 实例。"""
        kr = KR(kr_id="O1-KR2", title="KR标题", short_title="KR标题", owner="张三", content="完整描述")
        assert kr.owner == "张三"
        assert kr.content == "完整描述"


# ═══════════════════════════════════════════════════════════════
# Objective 数据类测试
# ═══════════════════════════════════════════════════════════════

class TestObjective:
    """Objective 数据类测试。"""

    def test_create_basic(self):
        """创建基本 Objective 实例。"""
        obj = Objective(obj_id="O1", title="检测技术升级")
        assert obj.obj_id == "O1"
        assert obj.title == "检测技术升级"
        assert obj.krs == {}

    def test_create_with_krs(self):
        """创建带 KR 的 Objective。"""
        kr = KR(kr_id="O1-KR1", title="KR1标题", short_title="KR1")
        obj = Objective(obj_id="O1", title="目标1", krs={"O1-KR1": kr})
        assert len(obj.krs) == 1
        assert obj.krs["O1-KR1"].title == "KR1标题"


# ═══════════════════════════════════════════════════════════════
# OKRDocument 测试
# ═══════════════════════════════════════════════════════════════

class TestOKRDocument:
    """OKRDocument 查询方法测试。"""

    def _make_doc(self):
        kr1 = KR(kr_id="O1-KR1", title="【质量】图像验证主观项检测", short_title="【质量】")
        kr2 = KR(kr_id="O1-KR2", title="【智能巡查】完成准确率达标", short_title="【智能巡查】")
        kr3 = KR(kr_id="O2-KR1", title="推荐体验优化", short_title="推荐体验优化")
        obj1 = Objective(obj_id="O1", title="检测技术升级", krs={"O1-KR1": kr1, "O1-KR2": kr2})
        obj2 = Objective(obj_id="O2", title="推荐体验保障", krs={"O2-KR1": kr3})
        doc = OKRDocument(objectives={"O1": obj1, "O2": obj2}, source_file="2026-Q3-OKR.md")
        return doc

    def test_get_kr_found(self):
        """get_kr 应返回匹配的 KR。"""
        doc = self._make_doc()
        kr = doc.get_kr("O1-KR1")
        assert kr is not None
        assert kr.kr_id == "O1-KR1"
        assert "图像" in kr.title

    def test_get_kr_not_found(self):
        """不存在的标签应返回 None。"""
        doc = self._make_doc()
        assert doc.get_kr("O3-KR1") is None

    def test_get_kr_empty_doc(self):
        """空文档应返回 None。"""
        doc = OKRDocument()
        assert doc.get_kr("O1-KR1") is None

    def test_resolve_tags_kr(self):
        """resolve_tags 对 KR 标签应返回 KR 标题。"""
        doc = self._make_doc()
        result = doc.resolve_tags(["O1-KR1"])
        assert result == {"O1-KR1": "【质量】图像验证主观项检测"}

    def test_resolve_tags_objective(self):
        """resolve_tags 对 Objective 标签应返回 O 标题。"""
        doc = self._make_doc()
        result = doc.resolve_tags(["O1"])
        assert result == {"O1": "检测技术升级"}

    def test_resolve_tags_multiple(self):
        """resolve_tags 支持多个标签同时解析。"""
        doc = self._make_doc()
        result = doc.resolve_tags(["O1-KR1", "O2"])
        assert len(result) == 2
        assert "图像" in result["O1-KR1"]
        assert "推荐" in result["O2"]

    def test_resolve_tags_unknown(self):
        """未知标签应被忽略（不在结果中）。"""
        doc = self._make_doc()
        result = doc.resolve_tags(["O99"])
        assert result == {}  # 未知 O 不加入结果

    def test_resolve_tags_mixed(self):
        """混合已知和未知标签应只返回已知的。"""
        doc = self._make_doc()
        result = doc.resolve_tags(["O1-KR1", "O99"])
        assert len(result) == 1
        assert "O1-KR1" in result

    def test_to_prompt_context_with_content(self):
        """有目标时格式化输出应包含 O 和 KR 信息。"""
        doc = self._make_doc()
        ctx = doc.to_prompt_context()
        assert "O1" in ctx
        assert "检测技术升级" in ctx
        assert "智能巡查" in ctx

    def test_to_prompt_context_empty(self):
        """空文档应返回默认占位文本。"""
        doc = OKRDocument()
        ctx = doc.to_prompt_context()
        assert ctx == "（无可用的 OKR 文档）"


# ═══════════════════════════════════════════════════════════════
# _find_latest_okr_file 测试
# ═══════════════════════════════════════════════════════════════

class TestFindLatestOKRFile:
    """OKR 文件查找测试（mock 文件系统）。"""

    def test_dir_not_exists(self, tmp_path):
        """01-目标管理 目录不存在时返回 None。"""
        result = _find_latest_okr_file(tmp_path)
        assert result is None

    def test_no_matching_file(self, tmp_path):
        """无匹配文件时返回 None。"""
        tm_dir = tmp_path / "01-目标管理" / "2026"
        tm_dir.mkdir(parents=True)
        # 创建非 OKR 文件
        (tm_dir / "数据部门-双周-0728.md").write_text("# 双周报", encoding="utf-8")
        (tm_dir / "数据部门-OP-0728.md").write_text("# OP", encoding="utf-8")
        result = _find_latest_okr_file(tmp_path)
        assert result is None

    def test_no_dept_keyword(self, tmp_path):
        """配置 dept_keyword 时，文件名不含该关键词应跳过。"""
        tm_dir = tmp_path / "01-目标管理" / "2026"
        tm_dir.mkdir(parents=True)
        (tm_dir / "其他部门-OKR.md").write_text("# 内容", encoding="utf-8")
        result = _find_latest_okr_file(tmp_path, dept_keyword="数据部门")
        assert result is None

    def test_no_keyword_filters_nothing(self, tmp_path):
        """未配置 dept_keyword（空=不过滤）时，任意文件名均可入选。"""
        tm_dir = tmp_path / "01-目标管理" / "2026"
        tm_dir.mkdir(parents=True)
        (tm_dir / "其他部门-OKR.md").write_text("# 内容", encoding="utf-8")
        result = _find_latest_okr_file(tmp_path)
        assert result is not None

    def test_find_latest_file(self, tmp_path):
        """应返回最新的 OKR 文件。"""
        tm_dir = tmp_path / "01-目标管理" / "2026"
        tm_dir.mkdir(parents=True)
        old_file = tm_dir / "数据部门-2026-Q2-OKR.md"
        old_file.write_text("# Q2 OKR", encoding="utf-8")
        new_file = tm_dir / "数据部门-2026-Q3-OKR.md"
        new_file.write_text("# Q3 OKR", encoding="utf-8")
        result = _find_latest_okr_file(tmp_path)
        assert result is not None
        assert "Q3" in result.name

    def test_multi_year_dirs(self, tmp_path):
        """按年份目录降序优先取最新年份。"""
        (tmp_path / "01-目标管理" / "2025").mkdir(parents=True)
        (tmp_path / "01-目标管理" / "2026").mkdir(parents=True)
        (tmp_path / "01-目标管理" / "2025" / "数据部门-OKR.md").write_text("# 2025", encoding="utf-8")
        (tmp_path / "01-目标管理" / "2026" / "数据部门-OKR.md").write_text("# 2026", encoding="utf-8")
        result = _find_latest_okr_file(tmp_path)
        assert result is not None
        assert "2026" in str(result.parent)


# ═══════════════════════════════════════════════════════════════
# _parse_okr_file 测试
# ═══════════════════════════════════════════════════════════════

class TestParseOKRFile:
    """OKR Markdown 解析测试。"""

    def test_parse_normal(self, tmp_path):
        """标准格式的 OKR 文件应正确解析。"""
        content = """## O1：检测技术升级

### KR1：图像质检准确率提升

**KR Owner：** 张三

这是 KR 的完整描述内容

### KR2：智能巡查准确率达标

## O2：推荐体验优化

### KR1：首页推荐精度提升

**KR Owner：** 李四
"""
        filepath = tmp_path / "OKR.md"
        filepath.write_text(content, encoding="utf-8")
        doc = _parse_okr_file(filepath)
        assert len(doc.objectives) == 2
        assert "O1" in doc.objectives
        assert "O2" in doc.objectives
        assert doc.objectives["O1"].title == "检测技术升级"
        assert len(doc.objectives["O1"].krs) == 2
        assert doc.objectives["O1"].krs["O1-KR1"].title == "图像质检准确率提升"
        assert doc.objectives["O1"].krs["O1-KR1"].owner == "张三"
        assert doc.objectives["O2"].krs["O2-KR1"].owner == "李四"

    def test_parse_with_frontmatter(self, tmp_path):
        """含 YAML frontmatter 的文件应跳过 frontmatter。"""
        content = """---
date: 2026-07-28
type: okr
---

## O1：目标一

### KR1：关键结果一
"""
        filepath = tmp_path / "OKR.md"
        filepath.write_text(content, encoding="utf-8")
        doc = _parse_okr_file(filepath)
        assert len(doc.objectives) == 1
        assert doc.objectives["O1"].title == "目标一"

    def test_parse_empty(self, tmp_path):
        """空文件应返回空文档。"""
        filepath = tmp_path / "empty.md"
        filepath.write_text("", encoding="utf-8")
        doc = _parse_okr_file(filepath)
        assert doc.objectives == {}

    def test_parse_kr_with_short_title(self, tmp_path):
        """含【】括号的 KR 标题应正确提取 short_title。"""
        content = """## O1：目标

### KR1：【质量】图像验证主观项检测，支撑商品全量入仓战略
"""
        filepath = tmp_path / "OKR.md"
        filepath.write_text(content, encoding="utf-8")
        doc = _parse_okr_file(filepath)
        kr = doc.objectives["O1"].krs["O1-KR1"]
        assert kr.short_title == "【质量】"
        assert "商品" in kr.title

    def test_parse_kr_before_obj(self, tmp_path):
        """KR 出现在 Objective 之前应被忽略（日志警告）。"""
        content = """### KR1：孤立KR

## O1：目标

### KR1：有效的KR
"""
        filepath = tmp_path / "OKR.md"
        filepath.write_text(content, encoding="utf-8")
        doc = _parse_okr_file(filepath)
        # 第一个 KR1 应被忽略，第二个 KR1 应被解析为 O1-KR1
        assert "O1-KR1" in doc.objectives["O1"].krs
        assert doc.objectives["O1"].krs["O1-KR1"].title == "有效的KR"

    def test_parse_chinese_colon(self, tmp_path):
        """中英文冒号都应支持。"""
        content = """## O1: 英文冒号

### KR1: KR标题
"""
        filepath = tmp_path / "OKR.md"
        filepath.write_text(content, encoding="utf-8")
        doc = _parse_okr_file(filepath)
        assert doc.objectives["O1"].title == "英文冒号"
        assert doc.objectives["O1"].krs["O1-KR1"].title == "KR标题"


# ═══════════════════════════════════════════════════════════════
# OKRLoader 测试
# ═══════════════════════════════════════════════════════════════

class TestOKRLoader:
    """OKRLoader 主类测试（mock _find_latest_okr_file 和 _parse_okr_file）。"""

    @patch("iris.feed._okr_loader._find_latest_okr_file")
    @patch("iris.feed._okr_loader._parse_okr_file")
    def test_load_caches(self, mock_parse, mock_find, tmp_path):
        """load 结果应被缓存，第二次调用不再重新查找/解析。"""
        mock_find.return_value = tmp_path / "OKR.md"
        kr = KR(kr_id="O1-KR1", title="KR1", short_title="KR1")
        obj = Objective(obj_id="O1", title="目标1", krs={"O1-KR1": kr})
        mock_parse.return_value = OKRDocument(objectives={"O1": obj})
        loader = OKRLoader(source_root=tmp_path)
        doc1 = loader.load()
        assert doc1 is not None
        doc2 = loader.load()
        assert doc1 is doc2  # 同一对象
        mock_parse.assert_called_once()

    def test_load_source_root_none(self):
        """source_root 为 None 时 load 返回 None。"""
        loader = OKRLoader(source_root=None)  # type: ignore[arg-type]
        assert loader.load() is None

    @patch("iris.feed._okr_loader._find_latest_okr_file")
    def test_load_file_not_found(self, mock_find, tmp_path):
        """找不到 OKR 文件时返回 None。"""
        mock_find.return_value = None
        loader = OKRLoader(source_root=tmp_path)
        assert loader.load() is None

    def test_set_source_root_clears_cache(self, tmp_path):
        """set_source_root 应清空缓存。"""
        (tmp_path / "01-目标管理" / "2026").mkdir(parents=True)
        okr_file = tmp_path / "01-目标管理" / "2026" / "数据部门-2026-Q3-OKR.md"
        okr_file.write_text("## O1：目标\n\n### KR1：结果\n", encoding="utf-8")
        loader = OKRLoader(source_root=tmp_path)
        doc = loader.load()
        assert doc is not None
        # 更换 path 应清缓存
        loader.set_source_root(tmp_path / "nonexistent")
        assert loader._cached is None

    @patch("iris.feed._okr_loader._find_latest_okr_file")
    @patch("iris.feed._okr_loader._parse_okr_file")
    def test_resolve_tags_with_doc(self, mock_parse, mock_find, tmp_path):
        """resolve_tags 应委托给 OKRDocument。"""
        mock_find.return_value = tmp_path / "OKR.md"
        kr = KR(kr_id="O1-KR1", title="KR1标题", short_title="KR1")
        obj = Objective(obj_id="O1", title="目标1", krs={"O1-KR1": kr})
        mock_parse.return_value = OKRDocument(objectives={"O1": obj})
        loader = OKRLoader(source_root=tmp_path)
        result = loader.resolve_tags(["O1-KR1"])
        assert result == {"O1-KR1": "KR1标题"}

    @patch("iris.feed._okr_loader._find_latest_okr_file")
    def test_resolve_tags_no_doc(self, mock_find, tmp_path):
        """无 OKR 文档时标签应原样返回。"""
        mock_find.return_value = None
        loader = OKRLoader(source_root=tmp_path)
        result = loader.resolve_tags(["O1-KR1"])
        assert result == {"O1-KR1": "O1-KR1"}
