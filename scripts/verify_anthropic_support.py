#!/usr/bin/env python
"""快速验证 Anthropic 多模态支持的脚本。

使用方法：
    python scripts/verify_anthropic_support.py
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def verify_anthropic_support():
    """验证 Anthropic 多模态支持是否正确集成。"""
    print("=" * 60)
    print("Anthropic 多模态支持验证")
    print("=" * 60)

    # 1. 验证导入
    print("\n[1/4] 验证模块导入...")
    try:
        from iris.llm.provider import EnvironmentConfiguredLLMProvider
        from iris.llm.service import LLMService  # noqa: F401  # 仅验证可导入性
        print("✅ 模块导入成功")
    except ImportError as e:
        print(f"❌ 模块导入失败: {e}")
        return False

    # 2. 验证方法存在
    print("\n[2/4] 验证方法存在...")
    try:
        assert hasattr(EnvironmentConfiguredLLMProvider, '_call_anthropic_multimodal')
        print("✅ _call_anthropic_multimodal 方法存在")
    except AssertionError:
        print("❌ _call_anthropic_multimodal 方法不存在")
        return False

    # 3. 验证测试文件
    print("\n[3/4] 验证测试文件...")
    test_file = project_root / "tests" / "test_anthropic_multimodal.py"
    if test_file.exists():
        print(f"✅ 测试文件存在: {test_file}")
    else:
        print(f"❌ 测试文件不存在: {test_file}")
        return False

    # 4. 验证文档
    print("\n[4/4] 验证文档...")
    doc_files = [
        project_root / "docs" / "ANTHROPIC_SETUP.md",
        project_root / "docs" / "ANTHROPIC_IMPLEMENTATION.md",
    ]
    for doc_file in doc_files:
        if doc_file.exists():
            print(f"✅ 文档存在: {doc_file.name}")
        else:
            print(f"❌ 文档不存在: {doc_file.name}")
            return False

    # 5. 运行快速测试
    print("\n[额外] 运行快速单元测试...")
    import subprocess
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_anthropic_multimodal.py", "-v", "--tb=short"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # 提取测试结果统计
        for line in result.stdout.split('\n'):
            if 'passed' in line:
                print(f"✅ {line.strip()}")
                break
    else:
        print("❌ 测试失败")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        return False

    # 总结
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)
    print("✅ Anthropic 多模态支持已成功集成！")
    print("\n下一步：")
    print("1. 在 .env 中配置 IRIS_ANTHROPIC_API_KEY")
    print("2. 在 config/llm.json 中添加 Anthropic 模型配置")
    print("3. 参考文档：docs/ANTHROPIC_SETUP.md")
    print("=" * 60)

    return True


if __name__ == "__main__":
    success = verify_anthropic_support()
    sys.exit(0 if success else 1)
