"""飞书知识库客户端 — 基于 lark-cli 的 wiki + docs API 封装。"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class FeishuClientError(RuntimeError):
    """飞书 API 调用错误。"""


_DOC_URL_RE = re.compile(
    r"(?:https?://[^/]+/(?:docx|docs|wiki)/|^)([a-zA-Z0-9]{8,40})(?:\?|$|/#)"
)
"""匹配飞书文档 URL 或裸 token。"""


@dataclass(frozen=True)
class WikiNodeMeta:
    token: str
    title: str
    node_type: str  # "page" | "folder" | "shortcut"
    parent_token: str = ""
    has_children: bool = False


class FeishuClient:
    """基于 lark-cli 的飞书知识库操作客户端。"""

    LARK_CLI = "lark-cli"

    def __init__(self, as_user: bool = True):
        self._as = "user" if as_user else "bot"

    # ── 低层 API 调用 ──────────────────────────────────

    def _run(self, args: list[str], timeout: int = 60) -> dict:
        cmd = [self.LARK_CLI] + args + ["--as", self._as, "--format", "json"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                stderr = (result.stderr or "")[:300]
                raise FeishuClientError(f"lark-cli 返回 {result.returncode}: {stderr}")
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            if not data.get("ok", True):
                err = data.get("error", {}).get("message", "未知错误")
                raise FeishuClientError(f"API 错误: {err}")
            return data
        except json.JSONDecodeError as e:
            raise FeishuClientError(f"JSON 解析失败: {e}")
        except subprocess.TimeoutExpired:
            raise FeishuClientError(f"命令超时 ({timeout}s)")

    # ── Wiki 空间操作 ──────────────────────────────────

    def list_spaces(self) -> list[dict]:
        """列出可访问的知识空间。"""
        data = self._run(["wiki", "space", "list"])
        return data.get("data", {}).get("items", [])

    def get_space(self, space_id: str) -> dict:
        """获取知识空间详情。"""
        data = self._run(["wiki", "space", "retrieve", "--space-id", space_id])
        return data.get("data", {})

    # ── 节点操作 ───────────────────────────────────────

    def list_nodes(self, space_id: str, parent_token: str = "") -> list[WikiNodeMeta]:
        """列出知识空间下的子节点。"""
        args = ["wiki", "node", "list", "--space-id", space_id]
        if parent_token:
            args += ["--parent-token", parent_token]
        data = self._run(args)
        nodes = data.get("data", {}).get("items", [])
        return [
            WikiNodeMeta(
                token=n.get("node_token", ""),
                title=n.get("title", ""),
                node_type=n.get("obj_type", "page"),
                parent_token=n.get("parent_node_token", parent_token),
                has_children=n.get("has_child", False),
            )
            for n in nodes
        ]

    def get_node(self, token: str) -> dict:
        """获取节点详情（含内容）。"""
        data = self._run(["wiki", "node", "retrieve", "--token", token])
        return data.get("data", {})

    def create_page(self, space_id: str, parent_token: str, title: str,
                    content: str, node_type: str = "doc") -> dict:
        """在知识空间中创建文档节点。

        注意：需要先创建节点，再通过 docs API 写入内容。
        如果内容 > 10KB，使用 --file 参数。
        """
        import tempfile, os

        # Step 1: 创建空节点
        args = [
            "wiki", "node", "create",
            "--space-id", space_id,
            "--parent-token", parent_token,
            "--title", title,
            "--obj-type", node_type,
        ]
        data = self._run(args)
        node_token = data.get("data", {}).get("node", {}).get("node_token", "")

        if node_token and content.strip():
            # Step 2: 写入内容（通过临时文件传递长内容）
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                            encoding="utf-8") as tf:
                tf.write(content)
                tmp_path = tf.name
            try:
                self._run([
                    "docs", "+update",
                    "--doc", node_token,
                    "--file", str(tmp_path),
                    "--title", title,
                ], timeout=120)
            finally:
                os.unlink(tmp_path)

        return {"node_token": node_token, "title": title}

    def update_page(self, token: str, title: str, content: str) -> dict:
        """更新已有文档节点的内容。"""
        import tempfile, os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                        encoding="utf-8") as tf:
            tf.write(content)
            tmp_path = tf.name
        try:
            data = self._run([
                "docs", "+update",
                "--doc", token,
                "--file", str(tmp_path),
                "--title", title,
            ], timeout=120)
            return data.get("data", {})
        finally:
            os.unlink(tmp_path)

    def find_node_by_title(self, space_id: str, parent_token: str, title: str) -> Optional[WikiNodeMeta]:
        """在指定父节点下按标题查找子节点。"""
        nodes = self.list_nodes(space_id, parent_token)
        for node in nodes:
            if node.title == title:
                return node
        return None

    # ── 文档操作 ──────────────────────────────────────────

    @staticmethod
    def parse_doc_url(url: str) -> str:
        """从飞书文档 URL 中提取 doc token。

        支持格式：
          - https://bytedance.feishu.cn/docx/Wi0Td6c...
          - https://bytedance.feishu.cn/docs/Wi0Td6c...
          - https://bytedance.feishu.cn/wiki/Wi0Td6c...
          - 裸 token: Wi0Td6c...
        """
        m = _DOC_URL_RE.search(url.strip())
        if not m:
            raise FeishuClientError(f"无法从 URL 中提取文档 token: {url}")
        return m.group(1)

    def fetch_doc_content(self, token: str) -> Dict[str, Any]:
        """获取飞书文档内容和元信息。

        返回:
            {"title": str, "content": str(markdown), "create_time": str, "modify_time": str, "owner_name": str}
        """
        data = self._run([
            "docs", "+fetch",
            "--doc", token,
            "--doc-format", "markdown",
        ], timeout=120)
        doc = data.get("data", {}).get("document", {})
        # 若无 document 字段则尝试顶层 data
        if not doc:
            doc = data.get("data", {})
        return {
            "title": doc.get("title", ""),
            "content": doc.get("content", ""),
            "create_time": doc.get("create_time", ""),
            "modify_time": doc.get("modify_time", ""),
            "owner_name": doc.get("owner_name", ""),
        }

    def download_image(self, file_token: str, save_path: str, *, overwrite: bool = False) -> str:
        """下载飞书文档中的图片到本地。

        Args:
            file_token: 图片资源 token
            save_path: 本地保存路径
            overwrite: 是否覆盖已有文件

        Returns:
            实际保存路径
        """
        save = Path(save_path)
        if save.exists() and not overwrite:
            return str(save)

        save.parent.mkdir(parents=True, exist_ok=True)
        self._run([
            "docs", "+media-download",
            "--token", file_token,
            "--type", "media",
            "--output", str(save),
            "--overwrite",
        ], timeout=120)
        return str(save)

    def search_chat_messages(self, chat_id: str, *,
                              time_start: str = "", time_end: str = "",
                              page_size: int = 100) -> List[Dict[str, Any]]:
        """搜索群聊消息（保留供 chat-digest 使用）。"""
        args = ["im", "+search", "--chat-id", chat_id]
        if time_start:
            args += ["--time-start", time_start]
        if time_end:
            args += ["--time-end", time_end]
        args += ["--page-size", str(min(page_size, 500))]
        data = self._run(args, timeout=120)
        return data.get("data", {}).get("items", [])
