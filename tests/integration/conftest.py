"""集成测试专用 fixtures。

从 tests/conftest.py 导入共享 fixtures，并添加集成测试专用 fixtures。
"""

from pathlib import Path

import pytest

# 从根 conftest 导入共享 fixtures
# (pytest 会自动发现父目录的 conftest.py)


@pytest.fixture
def real_wiki_dir(tmp_path: Path) -> Path:
    """创建包含示例 Wiki 页面的临时目录。"""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # 创建示例领域页面
    domain_dir = wiki_dir / "01-领域"
    domain_dir.mkdir()
    (domain_dir / "领域-测试领域.md").write_text(
        "---\ntitle: 测试领域\npage_type: domain\ntags: [测试]\n---\n\n# 测试领域\n\n领域描述内容。\n",
        encoding="utf-8",
    )

    # 创建示例概念页面
    concept_dir = wiki_dir / "02-概念"
    concept_dir.mkdir()
    (concept_dir / "概念-测试概念.md").write_text(
        "---\ntitle: 测试概念\npage_type: concept\ntags: [测试]\n---\n\n# 测试概念\n\n概念描述内容。\n",
        encoding="utf-8",
    )

    # 创建示例项目页面
    project_dir = wiki_dir / "03-项目"
    project_dir.mkdir()
    (project_dir / "项目-测试项目.md").write_text(
        "---\ntitle: 测试项目\npage_type: project\ntags: [测试]\n---\n\n# 测试项目\n\n项目描述内容。\n",
        encoding="utf-8",
    )

    return wiki_dir


@pytest.fixture
def real_source_dir(tmp_path: Path) -> Path:
    """创建包含示例源文档的临时目录。"""
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # 创建子目录结构
    for subdir in ["01-目标管理", "02-部门管理", "03-方案报告", "04-讨论思考", "05-会议纪要"]:
        (source_dir / subdir).mkdir()

    # 创建示例 Markdown 文件
    (source_dir / "01-目标管理" / "2024-Q1-目标.md").write_text(
        "# Q1 目标\n\n## 测试项目\n\n项目进展正常。\n\n### 关键指标\n\n1. 完成度 80%\n",
        encoding="utf-8",
    )

    (source_dir / "04-讨论思考" / "技术方案讨论.md").write_text(
        "# 技术方案讨论\n\n讨论了微服务架构的实施方案。\n\n参会人：张三、李四\n",
        encoding="utf-8",
    )

    return source_dir
