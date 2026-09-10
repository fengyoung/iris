#!/usr/bin/env python3
"""
将本地 Markdown 文件上传到飞书云文档
"""
import os
import sys
import json
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from iris.feishu.client import FeishuClient
from iris.core.exceptions import IrisRuntimeError


def markdown_to_feishu_blocks(md_content: str) -> list:
    """
    将 Markdown 转换为飞书文档块格式
    简化版本：主要处理标题、段落、代码块、表格
    """
    blocks = []
    lines = md_content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        # 跳过 frontmatter
        if i == 0 and line.strip() == '---':
            i += 1
            while i < len(lines) and lines[i].strip() != '---':
                i += 1
            i += 1
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 标题
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            text = line.lstrip('#').strip()
            blocks.append({
                "block_type": 1,  # 标题
                "heading": {
                    "elements": [{"text_run": {"content": text}}],
                    "level": min(level, 9)
                }
            })
            i += 1
            continue

        # 代码块
        if line.strip().startswith('```'):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            blocks.append({
                "block_type": 15,  # 代码块
                "code": {
                    "elements": [{"text_run": {"content": '\n'.join(code_lines)}}]
                }
            })
            i += 1
            continue

        # 表格（简化处理）
        if '|' in line:
            table_lines = []
            while i < len(lines) and '|' in lines[i]:
                table_lines.append(lines[i])
                i += 1
            # 将表格转为文本块（飞书表格API较复杂）
            blocks.append({
                "block_type": 2,  # 文本
                "text": {
                    "elements": [{"text_run": {"content": '\n'.join(table_lines)}}]
                }
            })
            continue

        # 普通段落
        blocks.append({
            "block_type": 2,  # 文本
            "text": {
                "elements": [{"text_run": {"content": line}}]
            }
        })
        i += 1

    return blocks


def create_feishu_doc(title: str, content: str) -> str:
    """
    创建飞书文档并返回文档 URL
    """
    try:
        client = FeishuClient()

        # 1. 创建空文档
        print(f"正在创建飞书文档: {title}")
        doc_response = client._request(
            "POST",
            "https://open.feishu.cn/open-apis/docx/v1/documents",
            json={"title": title}
        )

        if not doc_response.get("data"):
            raise IrisRuntimeError(f"创建文档失败: {doc_response}")

        doc_id = doc_response["data"]["document"]["document_id"]
        doc_url = f"https://zhuanzhuan.feishu.cn/docx/{doc_id}"
        print(f"✓ 文档创建成功: {doc_url}")

        # 2. 转换内容为飞书块格式
        print("正在转换文档内容...")
        blocks = markdown_to_feishu_blocks(content)

        # 3. 批量添加内容块
        print(f"正在上传内容（共 {len(blocks)} 个块）...")

        # 飞书 API 限制每次最多 50 个块
        batch_size = 50
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i:i+batch_size]
            client._request(
                "POST",
                f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                json={
                    "children": batch,
                    "index": -1  # 追加到末尾
                }
            )
            print(f"  已上传 {min(i+batch_size, len(blocks))}/{len(blocks)} 个块")

        print("\n✓ 文档创建完成！")
        print(f"  标题: {title}")
        print(f"  URL: {doc_url}")

        return doc_url

    except Exception as e:
        raise IrisRuntimeError(f"创建飞书文档失败: {e}")


def main():
    if len(sys.argv) < 2:
        print("用法: python upload_to_feishu.py <markdown_file> [title]")
        sys.exit(1)

    file_path = sys.argv[1]
    custom_title = sys.argv[2] if len(sys.argv) > 2 else None

    # 读取文件
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在 {file_path}")
        sys.exit(1)

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取标题（从文件名或内容）
    if custom_title:
        title = custom_title
    else:
        # 尝试从第一个 # 标题提取
        for line in content.split('\n'):
            if line.startswith('# '):
                title = line.lstrip('#').strip()
                break
        else:
            # 使用文件名
            title = Path(file_path).stem

    # 创建文档
    doc_url = create_feishu_doc(title, content)

    # 输出 JSON 结果
    print("\n" + json.dumps({
        "success": True,
        "title": title,
        "url": doc_url
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
