"""飞书知识库客户端 — 基于 lark-cli 的 wiki API 封装。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class FeishuClientError(RuntimeError):
    """飞书 API 调用错误。"""


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
