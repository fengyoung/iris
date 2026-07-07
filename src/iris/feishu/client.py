"""飞书知识库客户端 — 基于 lark-cli 的 wiki + docs API 封装。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
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

    def _run(self, args: list[str], timeout: int = 60, retries: int = 3) -> dict:
        """执行 lark-cli 命令，自动追加 --as 和 --format，支持退避重试。

        Raises:
            FeishuClientError: 在非零退出码且无法解析 JSON 时，或重试耗尽后
        """
        import time as _time

        cmd = [self.LARK_CLI] + args + ["--as", self._as, "--format", "json"]
        last_error = None

        for attempt in range(retries):
            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    stdout_text, stderr_text = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()  # 回收僵尸进程
                    raise
                if proc.returncode != 0 and not stdout_text.strip():
                    stderr = (stderr_text or "")[:300]
                    raise FeishuClientError(
                        f"lark-cli 返回 {proc.returncode}: {stderr or stdout_text[:200]}")

                stdout = stdout_text.strip()
                if stdout:
                    try:
                        data = json.loads(stdout)
                    except json.JSONDecodeError:
                        return {}
                else:
                    data = {}

                # 检查 ok 标志（JSON 可能含业务错误）
                if not data.get("ok", True):
                    err = data.get("error", {})
                    msg = err.get("message", "未知错误")
                    raise FeishuClientError(f"API 错误: {msg}")

                return data
            except FeishuClientError:
                raise
            except subprocess.TimeoutExpired as e:
                last_error = e
                if attempt < retries - 1:
                    _time.sleep(1.2 ** attempt)
                    continue
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    _time.sleep(1.2 ** attempt)
                    continue
            finally:
                # 确保无孤儿进程
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.communicate()

        raise FeishuClientError(f"命令超时/失败 ({retries}次): {last_error}")

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
          - https://example.feishu.cn/docx/Wi0Td6c...
          - https://example.feishu.cn/docs/Wi0Td6c...
          - https://example.feishu.cn/wiki/Wi0Td6c...
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
        if not doc:
            doc = data.get("data", {})
        return {
            "title": doc.get("title", ""),
            "content": doc.get("content", ""),
            "create_time": doc.get("create_time", ""),
            "modify_time": doc.get("modify_time", ""),
            "owner_name": doc.get("owner_name", ""),
        }

    def search_doc_meta(self, token: str, title: str) -> Dict[str, str]:
        """通过 docs +search 搜索文档元信息（创建时间、作者）。

        docs +fetch 不返回 create_time 和 owner_name 时的 fallback。
        搜索文档标题，按 token 精确匹配，返回 {create_time, owner_name}。
        """
        if not title:
            return {}
        try:
            data = self._run([
                "docs", "+search",
                "--query", title,
            ], timeout=30)
            results = data.get("data", {}).get("results", [])
            for r in results:
                meta = r.get("result_meta", {})
                if meta.get("token") == token:
                    return {
                        "create_time": meta.get("create_time_iso", ""),
                        "owner_name": meta.get("owner_name", ""),
                    }
            return {}
        except (FeishuClientError, json.JSONDecodeError):
            return {}

    def resolve_owner_name(self, doc_url: str) -> str:
        """通过 wiki +node-get 获取文档作者姓名。

        作为 docs +fetch 拿不到 owner_name 时的 fallback。
        需要完整 wiki URL（含域名），仅支持 wiki 节点。
        """
        try:
            data = self._run([
                "wiki", "+node-get",
                "--node-token", doc_url,
            ], timeout=30)
            node = data.get("data", {})
            owner_id = node.get("owner", "")
            if not owner_id:
                return ""
            # 通过 contact +get-user 按 open_id 查询姓名
            contact = self._run([
                "contact", "+get-user",
                "--user-id", owner_id,
                "--user-id-type", "open_id",
            ], timeout=30)
            user = contact.get("data", {}).get("user", {})
            if user:
                return user.get("name", "") or user.get("localized_name", "")
            return ""
        except (FeishuClientError, json.JSONDecodeError):
            return ""

    def resolve_doc_create_time(self, token: str) -> str:
        """通过 wiki +node-get 获取文档创建时间（作为 docs +fetch 的 fallback）。

        返回 ISO 格式时间字符串，失败时返回空字符串。
        """
        try:
            data = self._run([
                "wiki", "+node-get",
                "--node-token", token,
            ], timeout=30)
            node = data.get("data", {})
            created_ts = node.get("create_time", "") or node.get("created_at", "")
            if not created_ts:
                return ""
            try:
                # node-get 返回 Unix 时间戳（秒），统一转为 UTC ISO 格式
                from datetime import timezone as _tz
                dt = datetime.fromtimestamp(int(created_ts), tz=_tz.utc)
                return dt.isoformat()
            except (ValueError, TypeError, OSError):
                return ""
        except (FeishuClientError, json.JSONDecodeError):
            return ""

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

    # ── IM 操作 ────────────────────────────────────────────

    def search_group_by_name(self, name: str) -> Optional[str]:
        """按群聊名称搜索，返回第一个匹配的 chat_id。"""
        data = self._run([
            "im", "+chat-search",
            "--query", name,
        ], timeout=30)
        chats = data.get("data", {}).get("chats", [])
        if chats:
            return chats[0].get("chat_id", "")
        return None

    def list_chats(self, *, page_size: int = 50) -> List[Dict[str, Any]]:
        """列出用户可见的群聊（供交互模式使用）。"""
        data = self._run([
            "im", "+chat-list", "--page-size", str(page_size),
        ], timeout=30)
        return data.get("data", {}).get("chats", [])

    def search_user(self, query: str) -> Optional[Dict[str, Any]]:
        """按姓名搜索用户，返回第一个匹配的用户信息。"""
        try:
            data = self._run([
                "contact", "+search-user", "--query", query,
            ], timeout=30)
            users = data.get("data", {}).get("users", [])
            return users[0] if users else None
        except (FeishuClientError, IndexError):
            return None

    def list_chat_messages(self, chat_id: str = "", *,
                            user_id: str = "",
                            time_start: str = "", time_end: str = "",
                            page_size: int = 50, page_token: str = "") -> Dict[str, Any]:
        """列出群聊/P2P 消息，支持分页。

        chat_id 和 user_id 二选一：
          - chat_id: 群聊 ID (oc_xxx)
          - user_id: 用户 open_id (ou_xxx)，用于单聊

        返回:
            {"items": [...], "page_token": str, "has_more": bool}
        """
        args = ["im", "+chat-messages-list",
                "--page-size", str(min(page_size, 50)), "--sort", "asc"]
        if chat_id:
            args += ["--chat-id", chat_id]
        elif user_id:
            args += ["--user-id", user_id]
        else:
            raise FeishuClientError("list_chat_messages 需要 chat_id 或 user_id")
        if time_start:
            args += ["--start", time_start]
        if time_end:
            args += ["--end", time_end]
        if page_token:
            args += ["--page-token", page_token]
        data = self._run(args, timeout=120)
        d = data.get("data", {})
        # +chat-messages-list 返回 messages 字段（也有用 items 的旧版本）
        raw = d.get("messages", []) or d.get("items", [])
        return {
            "items": raw,
            "page_token": d.get("page_token", ""),
            "has_more": d.get("has_more", False),
        }

    def fetch_all_messages(self, chat_id: str = "", *,
                            user_id: str = "",
                            time_start: str = "", time_end: str = "",
                            max_messages: int = 500) -> List[Dict[str, Any]]:
        """自动分页拉取群聊/P2P 消息，上限 max_messages 条。"""
        all_items: List[Dict[str, Any]] = []
        page_token = ""
        while len(all_items) < max_messages:
            result = self.list_chat_messages(
                chat_id, user_id=user_id,
                time_start=time_start, time_end=time_end,
                page_size=50, page_token=page_token)
            items = result.get("items", [])
            if not items:
                break
            all_items.extend(items)
            if not result.get("has_more"):
                break
            page_token = result.get("page_token", "")
            if not page_token:
                break
        return all_items[:max_messages]

    def batch_enrich_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量补全消息的发送者信息。

        通过 +messages-mget 批量获取消息详情（含 sender），
        将 sender 信息合并回原始消息列表。
        """
        ids = [m["message_id"] for m in messages if m.get("message_id")]
        if not ids:
            return messages

        # 按 50 条一组分批查询
        enriched = {}
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            try:
                data = self._run([
                    "im", "+messages-mget",
                    "--message-ids", ",".join(batch),
                ], timeout=60)
                items = data.get("data", {}).get("messages", []) or data.get("data", {}).get("items", [])
                for item in items:
                    mid = item.get("message_id", "")
                    if mid:
                        enriched[mid] = item
            except FeishuClientError:
                continue

        # 合并 sender 信息
        for msg in messages:
            mid = msg.get("message_id", "")
            if mid and mid in enriched:
                enriched_msg = enriched[mid]
                sender = enriched_msg.get("sender", {})
                if sender:
                    msg["sender"] = sender
                if not msg.get("msg_type"):
                    msg["msg_type"] = enriched_msg.get("msg_type", "")
        return messages
